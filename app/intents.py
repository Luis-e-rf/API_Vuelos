from __future__ import annotations

import json
import logging
import re
from string import Template
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
    "actualizar_perfil", # "no, mejor a Cartagena" (cambio de parecer)
    "olvidar_todo",      # "olvida todo", "empezar de cero"
    "saludo",
    "ayuda",
    "conversacion",
)


@dataclass
class Intencion:
    """Una intención extraída del mensaje del usuario.

    Soporta:
      - Acción principal (buscar, elegir_destino, etc.)
      - Múltiples intenciones (el LLM retorna una lista)
      - Cambio de parecer (actualizar_perfil con campo_actualizado)
    """
    accion: str = "conversacion"
    numero: Optional[int] = None              # si eligió una opción por posición
    destino: Optional[str] = None           # ciudad normalizada (canónica)
    origen: Optional[str] = None            # ciudad de origen (si menciona "de X a Y")
    presupuesto: Optional[int] = None       # si mencionó un monto
    pasajeros: Optional[int] = None         # cuántos viajan
    rango_meses: Optional[int] = None       # "en los próximos N meses"
    fecha: Optional[str] = None             # fecha ISO detectada ("principios de enero 2027")
    aerolinea: Optional[str] = None         # si pidió una aerolínea concreta ("con wingo")
    barato: bool = False                    # "más barata/económica"
    rapido: bool = False                    # "más rápida"
    opciones_recientes: int = 0             # cuántas opciones existen en el perfil
    campo_actualizado: Optional[str] = None # para actualizar_perfil: "destino", "presupuesto", etc.
    moneda: Optional[str] = None            # USD, COP, EUR

    def to_dict(self) -> dict:
        return asdict(self)


_SYSTEM_INTENT = (
    "Eres el intérprete interno de un bot de vuelos en español. "
    "Tu única tarea es analizar el mensaje del usuario considerando el historial "
    "y el perfil actual, y devolver un JSON con la(s) intención(es). "
    "No escribas nada más que el JSON."
)

_PROMPT_INTENT = """Eres el intérprete de un bot de vuelos. Analiza el mensaje y devuelve JSON.

PERFIL:
- Origen: {origen}
- Destino: {destino}
- Presupuesto: {presupuesto} {moneda}
- Pasajeros: {pasajeros}

HISTORIAL:
{historial}

OPCIONES ANTERIORES:
{recientes}

MENSAJE: "{mensaje}"

REGLAS:

1. "de X a Y" = origen es X, destino es Y. Ejemplo:
   "de Bogotá a San Andrés" → origen="Bogota", destino="San Andres", accion="buscar"

2. PRESUPUESTO:
   - "1 millón" = 1000000
   - "1 millón por persona" = 1000000 (el orquestador multiplica × pasajeros)
   - "300 dólares" = 300 USD
   - "600 mil" = 600000

3. PASAJEROS: "2 personas" = 2, "somos 2" = 2

4. RANGO: "lo más barato en 3 meses" o "sin importar el mes" → rango_meses=3

5. CAMBIO: "no, mejor a X" → accion="actualizar_perfil"

6. Si menciona todo junto (origen+destino+presupuesto+pasajeros), usa "buscar"

EJEMPLOS:
"de Bogotá a Cartagena, 1 millón, 2 personas" → {{"accion":"buscar","origen":"Bogota","destino":"Cartagena","presupuesto":1000000,"moneda":"COP","pasajeros":2}}
"de Medellín a San Andrés, 500 mil, 1 persona" → {{"accion":"buscar","origen":"Medellin","destino":"San Andres","presupuesto":500000,"moneda":"COP","pasajeros":1}}
"busca vuelos a Cartagena" → {{"accion":"buscar","destino":"Cartagena"}}
"para 2 personas" → {{"accion":"pasajeros","pasajeros":2}}

RESPUESTA JSON:
{{
  "intents": [{{
    "accion": "buscar|elegir_opcion|elegir_destino|rango|cambiar_presupuesto|pasajeros|comprar|actualizar_perfil|olvidar_todo|saludo|ayuda|conversacion",
    "destino": "Ciudad",
    "origen": "Ciudad",
    "presupuesto": 1000000,
    "moneda": "COP",
    "pasajeros": 2,
    "rango_meses": 3,
    "barato": true,
    "campo_actualizado": "destino|presupuesto|pasajeros|null"
  }}],
  "mensaje_clarificacion": null
}}
"""


@dataclass
class ResultadoInterpretacion:
    """Resultado del intérprete: puede retornar múltiples intenciones."""
    intenciones: list[Intencion]
    mensaje_clarificacion: Optional[str] = None


class Interpretador:
    """Traduce el texto libre a una o más Intenciones estructuradas.

    Prioridad: LLM (robusto) -> heurística local (offline).
    Soporta múltiples intenciones por mensaje y detección de cambio de parecer.
    """

    async def interpretar(
        self,
        mensaje: str,
        opciones_recientes: list[dict] | None = None,
        presupuesto_actual: Optional[int] = None,
        historial: Optional[list[dict]] = None,
        perfil_actual: Optional[dict] = None,
    ) -> ResultadoInterpretacion:
        recientes = opciones_recientes or []
        texto = mensaje.strip()

        # Datos del perfil para el prompt
        p = perfil_actual or {}
        presupuesto_str = f"{p.get('presupuesto', 'null')} {p.get('moneda', '')}" if p.get('presupuesto') else "no definido"
        historial_str = _formatear_historial(historial) if historial else "(nueva conversación)"

        # -------- LLM
        try:
            template = Template(_PROMPT_INTENT)
            prompt = template.safe_substitute(
                mensaje=texto,
                recientes=json.dumps(recientes),
                presupuesto=presupuesto_str,
                moneda=p.get('moneda', '') or '',
                origen=p.get('origen', 'desconocido') or 'desconocido',
                destino=p.get('destino', 'ninguno') or 'ninguno',
                pasajeros=str(p.get('pasajeros', 1)),
                historial=historial_str,
            )

            respuesta, _ = await llm_router.generar(
                _SYSTEM_INTENT, prompt, historial=historial, timeout=12,
            )
            resultado = _parse_respuesta_llm(respuesta, len(recientes))
            if resultado and resultado.intenciones:
                return resultado
        except Exception as exc:  # noqa: BLE001
            log.warning("Intérprete LLM falló: %s", exc)

        # Fallback: heurística local
        intencion = _heuristica(texto, recientes)
        if intencion.accion == "conversacion":
            # Si la heurística tampoco entendió, informar al usuario
            return ResultadoInterpretacion(
                intenciones=[intencion],
                mensaje_clarificacion=(
                    "Disculpa, tuve un problema técnico. ¿Puedes reformular tu mensaje? "
                    "Por ejemplo: 'busca vuelos a Cartagena' o 'ayuda'."
                ),
            )
        return ResultadoInterpretacion(intenciones=[intencion])


def _formatear_historial(historial: list[dict]) -> str:
    """Formatea el historial para el prompt del LLM."""
    lineas = []
    for msg in historial[-6:]:  # últimos 6 turnos
        rol = "Usuario" if msg["role"] == "user" else "Bot"
        lineas.append(f"- {rol}: {msg['content'][:300]}")
    return "\n".join(lineas) if lineas else "(nueva conversación)"


def _parse_respuesta_llm(respuesta: str | None, n_recientes: int) -> ResultadoInterpretacion | None:
    """Parsea la respuesta JSON del LLM en un ResultadoInterpretacion."""
    if not respuesta:
        return None

    obj = _extraer_json(respuesta)
    if not obj:
        return None

    intents_raw = obj.get("intents", [])
    if not intents_raw:
        # Formato legacy: objeto directo
        intents_raw = [obj]

    intenciones = []
    for raw in intents_raw[:2]:  # máximo 2 intenciones
        intent = _parse_intencion(raw, n_recientes)
        if intent:
            intenciones.append(intent)

    if not intenciones:
        return None

    return ResultadoInterpretacion(
        intenciones=intenciones,
        mensaje_clarificacion=obj.get("mensaje_clarificacion"),
    )


def _parse_intencion(raw: dict, n_recientes: int) -> Intencion | None:
    """Convierte un dict JSON en una Intencion."""
    accion = raw.get("accion", "conversacion")
    if accion not in ACCIONES:
        accion = "conversacion"

    intent = Intencion(
        accion=accion,
        numero=_coerce_int(raw.get("numero")),
        destino=normalizar_destino(str(raw.get("destino") or "")),
        origen=normalizar_destino(str(raw.get("origen") or "")),
        presupuesto=_coerce_int(raw.get("presupuesto")),
        pasajeros=_coerce_int(raw.get("pasajeros")),
        rango_meses=_coerce_int(raw.get("rango_meses")),
        fecha=_coerce_fecha(raw.get("fecha")),
        aerolinea=_coerce_aerolinea(raw.get("aerolinea")),
        barato=bool(raw.get("barato")),
        rapido=bool(raw.get("rapido")),
        campo_actualizado=raw.get("campo_actualizado"),
        moneda=raw.get("moneda"),
    )

    if intent.numero is not None and intent.numero > n_recientes:
        intent.numero = None

    # Corregir si el LLM puso la ciudad de origen como destino
    # (ej: "de Bogotá a San Andrés" → LLM pone destino="Bogota")
    if intent.destino and intent.origen and intent.destino == intent.origen:
        intent.origen = None  # dejar que el orquestador use el origen del perfil

    return intent


# --- Heurística local (fallback cuando no hay LLM) ------------------------


def _heuristica(texto: str, recientes: list[dict]) -> Intencion:
    """Fallback offline: regex y keyword matching."""
    t = texto.lower().strip()

    if t in ("/start", "hola", "buenas", "hi", "buenas tardes", "buenos días"):
        return Intencion(accion="saludo")
    if t in ("/help", "ayuda", "help", "que haces", "¿que haces?"):
        return Intencion(accion="ayuda")
    if any(w in t for w in ("olvida todo", "olvidar todo", "empezar de cero", "reset")):
        return Intencion(accion="olvidar_todo")
    if "guardados" in t and "ver" in t:
        return Intencion(accion="ver_guardados")
    if any(w in t for w in ("guardar", "guarda")):
        return Intencion(accion="guardar_viaje")
    if _quiere_comprar(t):
        return Intencion(accion="comprar")

    # Detectar cambio de parecer
    if any(m in t for m in ("no, mejor", "no mejor", "cambié", "cambie", "en vez de", "en lugar de")):
        destino = normalizar_destino(quitar_origen(texto))
        if destino:
            return Intencion(accion="actualizar_perfil", destino=destino, campo_actualizado="destino")

    pasajeros = _extraer_pasajeros(t)
    fecha = _extraer_fecha(t)
    rango = _extraer_rango(t)
    num_opcion = _extraer_opcion(t, len(recientes))
    barato = any(w in t for w in ("barat", "econ", "regala", "poco"))
    rapido = any(w in t for w in ("rapid", "direct", "corto"))
    aerolinea = _extraer_aerolinea(t)

    # Detectar patrón "de X a Y" (origen -> destino)
    origen, destino = _extraer_origen_destino(texto)
    if origen and destino:
        return Intencion(
            accion="buscar", origen=origen, destino=destino, fecha=fecha,
            pasajeros=pasajeros, barato=barato, aerolinea=aerolinea,
        )

    sin_origen = quitar_origen(texto)
    destino = _destino_con_negacion(sin_origen)
    if not destino:
        destino = normalizar_destino(sin_origen)
    huella = _huele_busqueda(t) or destino or fecha or rango

    if rango and not fecha:
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


# --- utilidades -----------------------------------------------------------


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
        from datetime import datetime as _dt
        _dt.strptime(v, "%Y-%m-%d")
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

    m = re.search(r"\b(\d{1,2})\s+de\s+(\w+)", t)
    if m and m.group(2) in _MESES:
        dia = min(28, int(m.group(1)))
        return _armar(anyo, _MESES[m.group(2)], dia)

    if any(w in t for w in ("de año", "del año", "de anio", "del anio")):
        if periodo and periodo == 25:
            return _armar(anyo, 12, 25)
        if any(w in t for w in ("principio", "inicio", "comienzo")):
            return _armar(anyo, 1, 5)
        if any(w in t for w in ("mediad", "mitad")):
            return _armar(anyo, 6, 15)

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
        a = hoy.year + 1
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
    _ORDINALES = {
        "primera": 1, "primero": 1, "1ra": 1, "1ª": 1,
        "segunda": 2, "segundo": 2, "2da": 2, "2ª": 2,
        "tercera": 3, "tercero": 3, "3ra": 3, "3ª": 3,
        "cuarta": 4, "4ta": 4, "quinta": 5, "5ta": 5,
    }
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
    """Si el usuario descarta un destino y propone otro, usa el último."""
    t = texto.lower()
    destinos = destinos_en_texto(texto)
    if not destinos:
        return None
    if not any(m in t for m in _NEGACION_MARCADORES):
        return destinos[0]
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


def _extraer_origen_destino(texto: str) -> tuple[str | None, str | None]:
    """Detecta patrón 'de X a Y' y retorna (origen, destino)."""
    t = texto.lower()
    # Patrones: "de bogota a san andres", "desde medellin hasta cartagena"
    patron = re.search(r"(?:de|desde)\s+(.+?)\s+(?:a|hasta)\s+(.+?)(?:\s*,|\s*$)", t)
    if patron:
        origen = normalizar_destino(patron.group(1))
        destino = normalizar_destino(patron.group(2))
        if origen and destino and origen != destino:
            return origen, destino
    return None, None


def _quiere_comprar(t: str) -> bool:
    """'lo quiero', 'comprar este', 'reservar', 'dame el link' -> comprar."""
    return any(
        w in t for w in (
            "lo quiero", "quiero comprar", "comprar este", "compralo", "cómpramelo",
            "reservar", "dame el link", "dame el enlace", "link de compra",
            "quiero el vuelo", "como compro", "cómo compro", "comprar el vuelo",
            "adquirir",
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
        return max(1, round(n / 4))
    return max(1, n)
