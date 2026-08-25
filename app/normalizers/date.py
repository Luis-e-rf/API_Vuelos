"""DateParser: fecha coloquial -> ISO YYYY-MM-DD.

Reglas (función pura; `hoy` inyectable para tests deterministas):
- ISO explícito "2027-01-05" -> tal cual.
- "5 de enero de 2027", "15 dic 2027".
- Mes + período: principios->05, mediados->15, final(es)/últimos->25.
- Año suelto "2027" -> {año}-01-15 (convención del análisis técnico §3.5).
Sin año explícito la fecha se rueda al futuro si ya pasó este año.
Un año explícito se respeta aunque esté en el pasado: validar que sea
futuro es responsabilidad de SlotManager (FASE 3).
"""
from __future__ import annotations

import datetime
import re

from app.normalizers.text import quitar_tildes

_MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
    "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12,
    # abreviaturas
    "ene": 1, "abr": 4, "ago": 8, "sept": 9, "sep": 9, "oct": 10,
    "nov": 11, "dic": 12,
}
# alternancia más-largo-primero para que 'septiembre' no sea comido por 'sep'
_MESES_RX = "|".join(sorted(_MESES, key=len, reverse=True))
_DIA_MES_RX = re.compile(rf"\b(\d{{1,2}})\s*(?:de\s*)?({_MESES_RX})\b")
_ANYO_RX = re.compile(r"\b(20[2-9]\d)\b")
_ISO_RX = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

_PERIODO_INICIO = ("principio", "inicio", "comienzo", "arranque", "primeros")
_PERIODO_MITAD = ("mediado", "mitad")
_PERIODO_FIN = ("final", "finales", "ultimo", "ultim", "fin de")


def _periodo(t: str) -> int:
    """Día representativo del período del mes: inicio->5, mitad->15, fin->25."""
    if any(m in t for m in _PERIODO_MITAD):
        return 15
    if any(m in t for m in _PERIODO_FIN):
        return 25
    if any(m in t for m in _PERIODO_INICIO):
        return 5
    return 15  # mes sin período: mitad por convención


def rango_meses(raw: str) -> int | None:
    """'en los próximos 3 meses', '3 meses siguientes' -> 1..12, o None.

    Semanas no se aceptan (ambigüedad); eso lo pregunta SlotManager.
    """
    t = quitar_tildes(raw)
    m = (
        re.search(r"(?:proximos|siguientes)\s+(\d{1,2})\s*mes", t)
        or re.search(r"(\d{1,2})\s*mes(?:es)?\s*(?:proximos|siguientes)", t)
        or re.search(r"\ben\s+(\d{1,2})\s*mes", t)
    )
    if not m:
        return None
    return max(1, min(12, int(m.group(1))))


def parse(raw: str, hoy: datetime.date | None = None) -> str | None:
    """Texto con fecha coloquial -> ISO 'YYYY-MM-DD' o None si no hay fecha."""
    hoy = hoy or datetime.date.today()
    t = quitar_tildes(raw)

    iso = _ISO_RX.search(t)
    if iso:
        try:
            return datetime.date.fromisoformat(iso.group(1)).isoformat()
        except ValueError:
            return None

    anyo_m = _ANYO_RX.search(t)
    anyo = int(anyo_m.group(1)) if anyo_m else None

    def _armar(mes: int, dia: int) -> str | None:
        try:
            if anyo is not None:
                return datetime.date(anyo, mes, dia).isoformat()
            y = hoy.year
            if datetime.date(y, mes, dia) <= hoy:
                y += 1  # rodar al futuro
            return datetime.date(y, mes, dia).isoformat()
        except ValueError:
            return None

    m = _DIA_MES_RX.search(t)
    if m:
        return _armar(_MESES[m.group(2)], int(m.group(1)))

    for nombre in sorted(_MESES, key=len, reverse=True):
        if re.search(rf"\b{nombre}\b", t):
            return _armar(_MESES[nombre], _periodo(t))

    if anyo is not None:  # año suelto sin mes
        return f"{anyo}-01-15"

    return None
