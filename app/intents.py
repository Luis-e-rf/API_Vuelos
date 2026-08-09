from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Optional

from app import llm_router
from app.destinos import normalizar_destino

log = logging.getLogger(__name__)

# Acciones que el bot sabe hacer. El LLM normaliza el texto libre a una de ellas.
ACCIONES = (
    "buscar",            # quiere opciones de vuelo (explícito o por contexto)
    "elegir_opcion",     # "la 3", "la segunda"
    "elegir_destino",    # "quiero ir a cartagena"
    "rango",             # "la más barata en los próximos 3 meses"
    "cambiar_presupuesto",
    "guardar_viaje",
    "ver_guardados",
    "pasajeros",         # "somos 2"
    "saludo",
    "ayuda",
    "conversacion",
)


@dataclass
class Intencion:
    accion: str = "conversacion"
    numero: Optional[int] = None       # si eligió una opción por posición
    destino: Optional[str] = None      # ciudad normalizada (canónica)
    presupuesto: Optional[int] = None  # si mencionó un monto
    pasajeros: Optional[int] = None
    rango_meses: Optional[int] = None  # para "en los próximos N meses"
    barato: bool = False               # "la más barata/económica"
    rapido: bool = False               # "la más rápida"
    opciones_recientes: int = 0        # cuántas opciones existen en el perfil

    def to_dict(self) -> dict:
        return asdict(self)


_SYSTEM_INTENT = (
    "Eres el intérprete interno de un bot de vuelos en español. Tu única tarea es "
    "traducir el mensaje libre del usuario a un objeto JSON. No escribas nada más "
    "que el JSON."
)

_PROMPT_INTENT = """Convierte el mensaje del usuario en un objeto JSON.

Posibles "accion":
- "buscar" -> quiere ver opciones de vuelos ("busca", "qué hay barato")
- "elegir_opcion" -> eligió una de las opciones numeradas que se le mostraron (ej: "la 3", "la opción 2")
- "elegir_destino" -> menciona un destino concreto ("quiero ir a cartagena")
- "rango" -> búsqueda dentro de un rango de meses, posiblemente la más barata ("lo más barato en 3 meses")
- "cambiar_presupuesto" -> quiere cambiar el presupuesto
- "guardar_viaje" / "ver_guardados" -> guardar o ver vuelos guardados
- "pasajeros" -> dice cuántas personas viajan ("somos 2")
- "saludo" / "ayuda" / "conversacion"

Formato JSON a devolver (usa valores null si no aplican):
{"accion": "...", "numero": 3, "destino": "Barranquilla", "presupuesto": 600000, "pasajeros": 2, "rango_meses": 3, "barato": true, "rapido": false}

Reglas:
- numero: índice 1-based de la opción a la que se refiere, solo si accion=elegir_opcion.
- destino: normaliza la ciudad aunque esté mal escrita ("barajilla" -> "Barranquilla").
- barato: true si pide "la más barata/económica/regalada".
- rapido: true si pide "la más rápida/corta/directa".

Mensaje del usuario: "{mensaje}"
Opciones mostradas: {recientes}
"""

_NUMERO_RE = re.compile(r"(\d+)")


class Interpretador:
    """Traduce el texto libre a un Intencion estructurado.

    Prioridad: LLM (robusto) -> heurística local (offline).
    """

    async def interpretar(
        self,
        mensaje: str,
        opciones_recientes: list[dict] | None = None,
        presupuesto_actual: Optional[int] = None,
    ) -> Intencion:
        recientes = opciones_recientes or []
        texto = mensaje.strip()

        # -------- LLM
        try:
            prompt = _PROMPT_INTENT
            prompt = prompt.replace("{mensaje}", texto)
            prompt = prompt.replace("{recientes}", json.dumps(recientes))
            respuesta, _ = await llm_router.generar(_SYSTEM_INTENT, prompt, timeout=12)
            obj = _extraer_json(respuesta)
            if obj:
                intencion = self._parse_json(obj, len(recientes))
                if intencion:
                    return intencion
        except Exception as exc:  # noqa: BLE001
            log.warning("Intérprete LLM falló: %s", exc)

        return self._heuristica(texto, recientes)

    # --- helpers ---------------------------------------------------------

    def _parse_json(self, raw: dict, n_recientes: int) -> Intencion | None:
        accion = raw.get("accion")
        if accion not in ACCIONES:
            accion = "conversacion"
        int = Intencion(
            accion=accion,
            numero=_coerce_int(raw.get("numero")),
            destino=normalizar_destino(str(raw.get("destino") or "")),
            presupuesto=_coerce_int(raw.get("presupuesto")),
            pasajeros=_coerce_int(raw.get("pasajeros")),
            rango_meses=_coerce_int(raw.get("rango_meses")),
            barato=bool(raw.get("barato")),
            rapido=bool(raw.get("rapido")),
        )
        if int.numero is not None and int.numero > n_recientes:
            int.numero = None
        return int

    def _heuristica(
        self, texto: str, recientes: list[dict]
    ) -> Intencion:
        t = texto.lower().strip()

        if t in ("/start", "hola", "buenas", "hi", "buenas tardes", "buenos días"):
            return Intencion(accion="saludo")
        if t in ("/help", "ayuda", "help", "que haces", "¿que haces?"):
            return Intencion(accion="ayuda")

        if any(w in t for w in ("guardar", "guarda", "guardados")):
            if "cambiar" in t and "guardados" in t:
                return Intencion(accion="ver_guardados")
            if "ver" in t and "guardados" in t:
                return Intencion(accion="ver_guardados")
            return Intencion(accion="guardar_viaje")

        if "cambiar" in t:
            return Intencion(accion="cambiar_presupuesto")

        # "somos 2", "para 3 personas", "viajan 4"
        m = re.search(
            r"(?:somos|hay|viajan|somos solo|solo)\s+(\d+)(?:\s*(?:personas|viajeros|adultos|pasajeros))?\b",
            t,
        )
        if not m:
            m = re.search(
                r"(?:para|viajan)\s+(\d+)\s*(?:personas|viajeros|adultos|pasajeros)\b", t
            )
        if m:
            return Intencion(accion="pasajeros", pasajeros=int(m.group(1)))

        # "la 3", "la opción 2", "la segunda"
        num = _extraer_numero_texto(t)
        if num and recientes and num <= len(recientes) and any(
            w in t for w in ("la ", "opcion", "opción", "el ", "segunda", "tercera", "primera")
            or t.strip().isdigit()
        ):
            return Intencion(accion="elegir_opcion", numero=num)

        # rango temporal: "en/los próximos N meses/semanas", "lo más barato en 3 meses"
        rango = _extraer_rango(t)
        if rango:
            barato = any(w in t for w in ("barat", "econ", "regala", "poco"))
            return Intencion(accion="rango", rango_meses=rango, barato=barato)

        # "busca/busco/buscar" explícitos -> acción buscar (aunque mencione ciudad)
        if _huele_busqueda(t):
            return Intencion(accion="buscar")

        destino = normalizar_destino(_quitar_frases(texto))
        if destino:
            return Intencion(accion="elegir_destino", destino=destino)

        return Intencion(accion="conversacion")


# --- utilidades ------------------------------------------------------------


def _extraer_json(texto: str) -> dict | None:
    if not texto:
        return None
    texto = texto.strip()
    if texto.startswith("```"):
        texto = texto.strip("`")
    if texto.startswith("json"):
        texto = texto[4:].strip()
    inicio = texto.find("{")
    fin = texto.rfind("}")
    if inicio < 0 or fin <= inicio:
        return None
    try:
        return json.loads(texto[inicio : fin + 1])
    except json.JSONDecodeError:
        return None


def _coerce_int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


_ORDINALES = {
    "primera": 1, "primero": 1, "1ra": 1, "1ª": 1,
    "segunda": 2, "segundo": 2, "2da": 2, "2ª": 2,
    "tercera": 3, "tercero": 3, "3ra": 3, "3ª": 3,
    "cuarta": 4, "4ta": 4, "quinta": 5, "5ta": 5,
}


def _extraer_numero_texto(t: str) -> int | None:
    for palabra, n in _ORDINALES.items():
        if palabra in t:
            return n
    m = re.search(r"(\d+)", t)
    return int(m.group(1)) if m else None


def _huele_busqueda(t: str) -> bool:
    return any(
        w in t for w in ("busca", "busco", "buscar", "opciones", "presupuesto", "viajar", "barato")
    )


_FRASES_PREVIAS = (
    "quiero ir a ",
    "quiero viajar a ",
    "me gustaria ir a ",
    "para ir a ",
    "vamos a ",
    "estoy buscando ",
    "es decir ",
    "hacia ",
)


def _quitar_frases(texto: str) -> str:
    """Quita muletillas iniciales para que la normalización difusa pegue mejor."""
    t = texto.lower().strip()
    for frase in _FRASES_PREVIAS:
        if t.startswith(frase):
            return t[len(frase):]
    return t


def _extraer_rango(t: str) -> int | None:
    m = re.search(r"(?:próximos|proximos|en los proximos|en los próximos|en)\s+(\d+)\s*(mes|semana)", t)
    if not m:
        return None
    n = int(m.group(1))
    if "semana" in m.group(0):
        return max(1, round(n / 4))  # "2 semanas" ~ medio mes
    return max(1, n)