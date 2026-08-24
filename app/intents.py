from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Optional

from app import llm_router
from app.destinos import destinos_en_texto, normalizar_destino, quitar_origen

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
    "comprar",           # "lo quiero", "dame el link para comprarlo"
    "saludo",
    "ayuda",
    "conversacion",
)


@dataclass
class Intencion:
    accion: str = "conversacion"
    numero: Optional[int] = None              # si eligió una opción por posición
    destino: Optional[str] = None           # ciudad normalizada (canónica)
    presupuesto: Optional[int] = None       # si mencionó un monto
    pasajeros: Optional[int] = None         # cuántos viajan
    rango_meses: Optional[int] = None       # "en los próximos N meses"
    fecha: Optional[str] = None             # fecha ISO detectada ("principios de enero 2027")
    aerolinea: Optional[str] = None         # si pidió una aerolínea concreta ("con wingo")
    barato: bool = False                    # "más barata/económica"
    rapido: bool = False                    # "más rápida"
    opciones_recientes: int = 0             # cuántas opciones existen en el perfil

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
- "comprar" -> quiere comprar/reservar el vuelo que se le mostró ("lo quiero", "dame el link", "quiero comprar este vuelo")
- "saludo" / "ayuda" / "conversacion"

Formato JSON a devolver (usa valores null si no aplican):
{"accion": "...", "numero": 3, "destino": "Barranquilla", "presupuesto": 600000, "pasajeros": 2, "rango_meses": 3, "fecha": "2027-01-05", "aerolinea": "Wingo", "barato": true, "rapido": false}

Reglas:
- numero: índice 1-based de la opción a la que se refiere, solo si accion=elegir_opcion.
- destino: normaliza la ciudad aunque esté mal escrita ("barajilla" -> "Barranquilla").
- pasajeros: cuántas personas viajan aunque esté en medio de otra frase ("para 4 personas" -> 4).
- fecha: si el usuario menciona un mes/periodo ("a principios de enero de 2027") usa ISO: principios -> día 05, mediados -> 15, fines -> 25; "enero 2027" -> 2027-01-15; "finales de año" -> 2026-12-25.
- aerolinea: nombre de la aerolínea si la pide ("con wingo" -> "Wingo", "por avianca" -> "Avianca").
- barato: true si pide "la más barata/económica/regalada".
- rapido: true si pide "la más rápida/corta/directa".

Mensaje del usuario: "{mensaje}"
Opciones mostradas: {recientes}
"""

_ORDINALES = {
    "primera": 1, "primero": 1, "1ra": 1, "1ª": 1,
    "segunda": 2, "segundo": 2, "2da": 2, "2ª": 2,
    "tercera": 3, "tercero": 3, "3ra": 3, "3ª": 3,
    "cuarta": 4, "4ta": 4, "quinta": 5, "5ta": 5,
}

class Interpretador:
    """Traduce el texto libre a un Intencion estructurado.

    Prioridad: LLM (robusto) -> heurística local (offline).
    """

    async def interpretar(
        self,
        mensaje: str,
        opciones_recientes: list[dict] | None = None,
        presupuesto_actual: Optional[int] = None,
        historial: Optional[list[dict]] = None,
    ) -> Intencion:
        recientes = opciones_recientes or []
        texto = mensaje.strip()

        # -------- LLM
        try:
            prompt = _PROMPT_INTENT
            prompt = prompt.replace("{mensaje}", texto)
            prompt = prompt.replace("{recientes}", json.dumps(recientes))
            respuesta, _ = await llm_router.generar(
                _SYSTEM_INTENT, prompt, historial=historial, timeout=12,
            )
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
            fecha=_coerce_fecha(raw.get("fecha")),
            aerolinea=_coerce_aerolinea(raw.get("aerolinea")),
            barato=bool(raw.get("barato")),
            rapido=bool(raw.get("rapido")),
        )
        if int.numero is not None and int.numero > n_recientes:
            int.numero = None
        return int

    def _heuristica(
        self, texto: str, recientes: list[dict]
    ) -> Intencion:
        """Recoge TODO lo que menciona el usuario (destino, fecha, pasajeros,
        rango...) en una sola pasada y luego decide la acción. Así una frase
        como "buscar vuelo a san andres para 4 personas en enero" no pierde
        ninguna parte por el camino."""
        t = texto.lower().strip()

        if t in ("/start", "hola", "buenas", "hi", "buenas tardes", "buenos días"):
            return Intencion(accion="saludo")
        if t in ("/help", "ayuda", "help", "que haces", "¿que haces?"):
            return Intencion(accion="ayuda")

        # acciones aisladas de memoria
        if "guardados" in t and "ver" in t:
            return Intencion(accion="ver_guardados")
        if any(w in t for w in ("guardar", "guarda")):
            return Intencion(accion="guardar_viaje")
        if "cambiar" in t:
            return Intencion(accion="cambiar_presupuesto")
        if _quiere_comprar(t):
            return Intencion(accion="comprar")

        # ---- recoger TODOS los campos que aparezcan ----
        pasajeros = _extraer_pasajeros(t)
        fecha = _extraer_fecha(t)
        rango = _extraer_rango(t)
        num_opcion = _extraer_opcion(t, len(recientes))
        barato = any(w in t for w in ("barat", "econ", "regala", "poco"))
        rapido = any(w in t for w in ("rapid", "direct", "corto"))
        aerolinea = _extraer_aerolinea(t)
        sin_origen = quitar_origen(texto)
        destino = _destino_con_negacion(sin_origen)
        if not destino:
            destino = normalizar_destino(sin_origen)
        huella = _huele_busqueda(t) or destino or fecha or rango

        # ---- decidir la acción central ----
        if rango and not fecha:
            # "lo más barato en 3 meses" -> rango, portando destino si lo dió
            return Intencion(
                accion="rango", rango_meses=rango, destino=destino,
                pasajeros=pasajeros, barato=barato, aerolinea=aerolinea,
            )
        if destino or huella:
            if destino:
                return Intencion(
                    accion="elegir_destino", destino=destino, fecha=fecha,
                    pasajeros=pasajeros, barato=barato, aerolinea=aerolinea,
                )
            if fecha:
                return Intencion(
                    accion="buscar", fecha=fecha, pasajeros=pasajeros, barato=barato,
                    aerolinea=aerolinea,
                )
            return Intencion(accion="buscar", pasajeros=pasajeros, barato=barato, aerolinea=aerolinea)

        if num_opcion:
            return Intencion(accion="elegir_opcion", numero=num_opcion)

        if pasajeros:
            return Intencion(accion="pasajeros", pasajeros=pasajeros)

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


def _coerce_fecha(v) -> Optional[str]:
    """Acepta solo fechas ISO válidas (YYYY-MM-DD)."""
    if not isinstance(v, str):
        return None
    try:
        datetime.datetime.strptime(v, "%Y-%m-%d")
        return v
    except (ValueError, TypeError):
        return None


_AEROLINEAS_CANON = {
    "avianca": "Avianca",
    "latam": "LATAM",
    "wingo": "Wingo",
    "viva": "Viva Colombia",
    "cop": "Copa Airlines",
    "satena": "Satena",
    "easyfly": "EasyFly",
    "easy fly": "EasyFly",
    "clic": "Clic",
    "pacifico": "Pacifico",
}


def _coerce_aerolinea(v) -> Optional[str]:
    if not isinstance(v, str):
        return None
    v = v.strip().lower()
    for clave, canon in _AEROLINEAS_CANON.items():
        if clave in v:
            return canon
    return None


_AEROLINEAS_RE = re.compile(
    r"(?:con|por|en|que uses|usando|vuelo con|volar)\s*([a-záéíóúñ\s]+?)(?:\b|$)",
    re.I,
)


def _extraer_aerolinea(t: str) -> Optional[str]:
    """Detecta si el usuario pide una aerolínea concreta ("con wingo")."""
    for m in _AEROLINEAS_RE.finditer(t):
        candidata = m.group(1).strip().lower()
        for clave, canon in _AEROLINEAS_CANON.items():
            if clave in candidata:
                return canon
    return None


_PASAJEROS_RE = [
    re.compile(r"(?:somos|hay|viajan|somos solo|solo)\s+([a-záéíóú]+|\d+)(?:\s*(?:personas|viajeros|adultos|pasajeros))?\b"),
    re.compile(r"(?:para|viajan)\s+([a-záéíóú]+|\d+)\s*(?:personas|viajeros|adultos|pasajeros)\b"),
]

_NUMEROS_PALABRA = {
    "uno": 1, "una": 1, "un": 1, "solo": 1, "sola": 1,
    "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
    "doce": 12,
}


def _extraer_pasajeros(t: str) -> int | None:
    for rx in _PASAJEROS_RE:
        m = rx.search(t)
        if m:
            n = _valor_numero(m.group(1))
            if n and 1 <= n <= 20:
                return n
    return None


def _valor_numero(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    return _NUMEROS_PALABRA.get(s)


_MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


def _extraer_fecha(t: str) -> str | None:
    """Traduce referencias temporales a una fecha ISO (día típico del periodo).
    ("a principios de enero de 2027" -> "2027-01-05")"""
    t = t.lower()
    anyo = None
    for y in range(2025, 2032):
        if f" {y}" in t or t.endswith(f" {y}") or re.search(rf"\b{y}\b", t):
            anyo = y
            break

    periodo = None
    if any(w in t for w in ("principio", "inicio", "comienzo", "arranque", "a inicios")):
        periodo = 5
    elif any(w in t for w in ("mediad", "mitad", "media de")):
        periodo = 15
    elif any(w in t for w in ("fin", "final", "últim", "ultim", "cierre de")):
        periodo = 25

    # día explícito: "el 10 de enero de 2027"
    m = re.search(r"\b(\d{1,2})\s+de\s+(\w+)", t)
    if m and m.group(2) in _MESES:
        dia = min(28, int(m.group(1)))
        return _armar(anyo, _MESES[m.group(2)], dia)

    # "finales de año" / "fin de año" -> diciembre
    if any(w in t for w in ("de año", "del año", "de anio", "del anio")):
        if periodo and periodo == 25:
            return _armar(anyo, 12, 25)
        if any(w in t for w in ("principio", "inicio", "comienzo")):
            return _armar(anyo, 1, 5)
        if any(w in t for w in ("mediad", "mitad")):
            return _armar(anyo, 6, 15)

    # mes + (año)
    for nombre, n in _MESES.items():
        if nombre in t or (len(nombre) > 4 and nombre[:4] in t):
            dia = periodo or 15
            return _armar(anyo, n, dia)

    return None


def _armar(anyo: int | None, mes: int, dia: int) -> str | None:
    import datetime as _dt
    hoy = _dt.datetime.now().date()
    a = anyo or hoy.year
    if anyo is None and (mes, dia) <= (hoy.month, hoy.day):
        a = hoy.year + 1  # "enero" sin año -> el próximo enero
    if a < hoy.year:
        a = hoy.year
    if a > 2031:
        a = 2031
    try:
        return _dt.date(a, mes, dia).isoformat()
    except ValueError:
        return None


def _extraer_opcion(t: str, n_recientes: int) -> int | None:
    """'la 2', 'la opción 3', 'segunda' -> índice si hay opciones mostradas."""
    for palabra, n in _ORDINALES.items():
        if palabra in t:
            return n if n <= n_recientes else None
    m = re.search(r"(?:la\s+|la\s+opci[oó]n\s*|el\s+|n[uú]mero\s*)(\d+)", t)
    if m:
        n = int(m.group(1))
        return n if n <= n_recientes else None
    return None


_NEGACION_MARCADORES = ("no ", "ya no", "no quiero", "mejor no", "si no", "sino", "en vez de", "en lugar de", "cambiando", "cambio de")


def _destino_con_negacion(texto: str) -> str | None:
    """Si el usuario descarta un destino y propone otro, usa el último
    ("mejor ya no gorgona si no que quiero buscar para medellin" -> Medellin)."""
    t = texto.lower()
    destinos = destinos_en_texto(texto)
    if not destinos:
        return None
    # no hay corrección -> el primero mencionado
    if not any(m in t for m in _NEGACION_MARCADORES):
        return destinos[0]
    # hay corrección -> tomar el destino que aparece después del 'no',
    # o el último mencionado si no está claro
    pos_no = -1
    for m in _NEGACION_MARCADORES:
        idx = t.find(m)
        if idx >= 0:
            pos_no = max(pos_no, idx)
    for destino in reversed(destinos):
        idx = t.find(destino.lower())
        if idx > pos_no:
            return destino
    return destinos[-1]


def _quiere_comprar(t: str) -> bool:
    """'lo quiero', 'comprar', 'reservar', 'dame el link' -> comprar."""
    return any(
        w in t for w in (
            "lo quiero", "quiero comprar", "comprar este", "compralo", "cómpramelo",
            "reservar", "dame el link", "dame el enlace", "link de compra",
            "quiero el vuelo", "como compro", "cómo compro", "comprar el vuelo",
            "adquirir", "comprar",
        )
    ) and not any(w in t for w in ("busca", "busco", "barato", "destino", "vuelo a"))


def _huele_busqueda(t: str) -> bool:
    return any(
        w in t for w in ("busca", "busco", "buscar", "opciones", "presupuesto", "viajar", "barato")
    )


def _extraer_rango(t: str) -> int | None:
    m = re.search(r"(?:próximos|proximos|en los proximos|en los próximos|en)\s+(\d+)\s*(mes|semana)", t)
    if not m:
        return None
    n = int(m.group(1))
    if "semana" in m.group(0):
        return max(1, round(n / 4))  # "2 semanas" ~ medio mes
    return max(1, n)