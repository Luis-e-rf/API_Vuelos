"""Extractor NLU: texto libre -> RawSlots.

Un solo proveedor LLM (Gemini gemini-flash-lite-latest) en modo JSON
estricto (response_mime_type="application/json"), prompt <250 tokens con
máximo 2 ejemplos, reintento único si el JSON no parsea. SIN cascada: si
el LLM no está configurado o falla, se usa el extractor determinista
(vocabulario + app/normalizers), nunca un segundo proveedor.

Nota sobre el fallback determinista: llena los *_raw con el propio texto
del usuario en lugar de spans recortados. Es seguro porque los
normalizadores son idempotentes sobre ese texto (money/date/passengers
buscan dentro del string); el LLM sí devuelve spans precisos.
"""
from __future__ import annotations

import json
import logging
import re
from string import Template

import httpx

from app.config import GEMINI_API_KEY, GEMINI_MODEL
from app.normalizers import city, date, money, passengers
from app.normalizers.text import quitar_tildes, tokenizar
from app.nlu.schemas import IntentHint, RawSlots

log = logging.getLogger(__name__)

_MODELO_DEFAULT = "gemini-flash-lite-latest"
_BASE_GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_SYSTEM = (
    "Eres un extractor de slots. Devuelve SOLO JSON válido. "
    "No inventes datos; si algo no aparece usa null."
)

_PROMPT = Template("""Eres extractor de slots de un bot de vuelos. Responde SOLO JSON.
Extrae tal cual aparece en el texto, sin normalizar ni convertir nada.
Campos: origen_raw, destino_raw, presupuesto_raw, pasajeros_raw, fecha_raw, rango_meses_raw, intent_hint.
intent_hint: "search" | "reset" | "chitchat" | "select_option" | "change".
Ejemplo 1:
Texto: "de Bogotá a San Andrés, 2 personas, 1 millón por persona y que sea en 2027"
JSON: {"origen_raw":"Bogotá","destino_raw":"San Andrés","presupuesto_raw":"1 millón por persona","pasajeros_raw":"2 personas","fecha_raw":"2027","rango_meses_raw":null,"intent_hint":"search"}
Ejemplo 2:
Texto: "olvídale pues, borra todo eso"
JSON: {"origen_raw":null,"destino_raw":null,"presupuesto_raw":null,"pasajeros_raw":null,"fecha_raw":null,"rango_meses_raw":null,"intent_hint":"reset"}
Texto: "$texto"
JSON:""")

# Vocabulario determinista (única fuente compartida con DialogueManager).
VOCAB_RESET = (
    "olvida todo", "olvidar todo", "empezar de cero", "reset",
    "cancela todo", "cancelar todo", "borra todo",
)
_VOCAB_CAMBIO = ("no, mejor", "no mejor", "cambie", "cambié", "en vez de",
                 "en lugar de", "mejor a ", "mejor pa ")
_MARCADORES_ORIGEN = {"de", "desde", "del"}
_MARCADORES_DESTINO = {"a", "hasta", "pa", "para", "al", "hacia"}
_ORDINALES = {
    "primera": 1, "primero": 1, "segunda": 2, "segundo": 2,
    "tercera": 3, "tercero": 3, "cuarta": 4, "cuarto": 4, "quinta": 5,
}
_OPCION_RX = re.compile(r"\b(?:la|el|opcion)\s*(\d)\b")


def _numero_opcion(texto: str) -> int | None:
    """'la 2', 'opción 3', 'segunda' -> 1..9, o None."""
    t = quitar_tildes(texto)
    m = _OPCION_RX.search(t)
    if m:
        return int(m.group(1))
    for palabra, n in _ORDINALES.items():
        if re.search(rf"\b{palabra}\b", t):
            return n
    return None


def _roles_ciudades(texto: str) -> tuple[str | None, str | None]:
    """Asigna rol origen/destino según el marcador previo a cada ciudad.

    'de X a Y' -> (X, Y). Ciudad sin marcador se trata como destino.
    """
    tokens = tokenizar(texto)
    indice_por_offset = {off: i for i, (_tok, off) in enumerate(tokens)}
    origen: str | None = None
    destino: str | None = None
    for off, canon in city.extraer_ciudades(texto):
        i = indice_por_offset.get(off)
        previo = tokens[i - 1][0] if i else ""
        if previo in _MARCADORES_ORIGEN and origen is None:
            origen = canon
        elif destino is None:
            destino = canon
    return origen, destino


def _hint_determinista(t: str, hay_datos: bool) -> IntentHint:
    if any(v in t for v in VOCAB_RESET):
        return "reset"
    if _numero_opcion(t):
        return "select_option"
    if any(v in t for v in _VOCAB_CAMBIO):
        return "change"
    if not hay_datos:
        return "chitchat"
    return "search"


class Extractor:
    """Fachada única de extracción. `extract()` nunca lanza excepciones."""

    def configurado(self) -> bool:
        """True solo si hay GEMINI_API_KEY (un proveedor, sin cascada)."""
        return bool(GEMINI_API_KEY)

    async def extract(self, texto: str) -> RawSlots:
        t = texto.strip()
        if not t:
            return RawSlots(intent_hint="chitchat")
        if not self.configurado():
            return self.determinista(t)
        raw = await self._extraer_llm(t)
        if raw is None:
            log.warning("Extractor: LLM sin respuesta útil -> modo determinista")
            return self.determinista(t)
        # el número de opción siempre por vía determinista
        raw.numero_opcion = raw.numero_opcion or _numero_opcion(t)
        return raw

    # --- LLM (JSON estricto) --------------------------------------------

    async def _extraer_llm(self, texto: str) -> RawSlots | None:
        prompt_base = _PROMPT.safe_substitute(texto=texto)
        for intento, prompt in enumerate(
            (prompt_base, prompt_base + "\nResponde únicamente el JSON."), start=1
        ):
            respuesta = await _gemini_json(prompt)
            raw = _parsear_json(respuesta)
            if raw is not None:
                return raw
            log.warning("Extractor: JSON inválido (intento %s/2)", intento)
        return None

    # --- Determinista (offline) ------------------------------------------

    @staticmethod
    def determinista(texto: str) -> RawSlots:
        """Extracción offline: vocabulario + normalizadores puros."""
        t_norm = quitar_tildes(texto.lower())
        origen, destino = _roles_ciudades(texto)
        monto = money.parse(texto)
        pax = passengers.parse(texto)
        fecha = date.parse(texto)
        rango = date.rango_meses(texto)
        numero = _numero_opcion(texto)

        hay_datos = bool(origen or destino or monto.valor_cop or monto.por_persona
                         or pax or fecha or rango or numero)
        return RawSlots(
            origen_raw=origen,          # ya canónico; CityNormalizer es idempotente
            destino_raw=destino,
            presupuesto_raw=texto if (monto.valor_cop or monto.por_persona) else None,
            pasajeros_raw=texto if pax else None,
            fecha_raw=texto if fecha else None,
            rango_meses_raw=texto if rango else None,
            intent_hint=_hint_determinista(t_norm, hay_datos),
            numero_opcion=numero,
        )


# --- Gemini JSON mode -----------------------------------------------------


async def _gemini_json(prompt: str, timeout: float = 8.0) -> str | None:
    """Una sola llamada a Gemini con response_mime_type application/json.

    Sin cascada ni lista de candidatos: 404/429 -> warning y None.
    """
    modelo = GEMINI_MODEL or _MODELO_DEFAULT
    url = f"{_BASE_GEMINI.format(model=modelo)}?key={GEMINI_API_KEY}"
    body = {
        "systemInstruction": {"parts": [{"text": _SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 500,
            "response_mime_type": "application/json",
        },
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, json=body)
        if r.status_code != 200:
            log.warning("Gemini %s HTTP %s: %s", modelo, r.status_code, r.text[:120])
            return None
        data = r.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
        log.warning("Gemini %s falló: %s", modelo, exc)
        return None


def _parsear_json(respuesta: str | None) -> RawSlots | None:
    """json.loads + validación Pydantic. Unknown keys y hints raros se filtran."""
    if not respuesta:
        return None
    limpio = respuesta.strip().strip("`")
    if limpio.startswith("json"):
        limpio = limpio[4:].strip()
    inicio, fin = limpio.find("{"), limpio.rfind("}")
    if inicio < 0 or fin <= inicio:
        return None
    try:
        datos = json.loads(limpio[inicio : fin + 1])
    except json.JSONDecodeError:
        return None
    # sanear el hint ANTES de validar: un hint raro del LLM no debe
    # costarnos el resto de slots válidos del mismo JSON
    if not isinstance(datos.get("intent_hint"), str) or datos["intent_hint"] not in (
        "search", "reset", "chitchat", "select_option", "change",
    ):
        datos["intent_hint"] = "search"
    try:
        raw = RawSlots.model_validate(datos)
    except ValueError:
        return None
    return raw
