"""MoneyParser: monto coloquial -> COP entero (unidad, sin pasajeros).

Vocabulario soportado (puro, sin gramática):
- Cifras con separador de miles: "1.500.000", "1,500,000"
- Sufijos: k / mil / lucas (x1000), M / millón(es) / millon / millo /
  palo(s) / melón(es) (x1.000.000)
- Palabras-numéricas como cantidad: "un palo", "dos millones"
- Decimales antes del sufijo: "1,5 millones" -> 1_500_000
- Moneda extranjera: "dólares/dólar/usd/verdes" -> USD, "euros" -> EUR
- Marca de unidad por persona: "por persona/cabeza", "cada uno/quien"

Reglas de ambigüedad documentadas:
- Cifra desnuda <10.000 en COP es ambigua ("300" ¿pesos?) -> None.
  Con moneda extranjera sí se acepta ("300 dólares").
- El multiplicador por pasajeros NO se aplica aquí (lo hace SlotManager,
  FASE 3); este parser retorna la unidad y la marca `por_persona`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.normalizers.text import quitar_tildes

Moneda = Literal["COP", "USD", "EUR"]

_TASAS_COP = {"USD": 4000, "EUR": 4400}
_POR_PERSONA = ("por persona", "por cabeza", "cada uno", "cada quien")
_MONEDA_USD = re.compile(r"\b(usd|dolares|dolar|verdes?)\b")
_MONEDA_EUR = re.compile(r"\b(euros?)\b")

_NUM_PALABRA = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "veinte": 20,
}
_MULTIPLOS = {
    # orden de alternancia: más largos primero (lo construye el regex)
    "millones": 1_000_000, "millon": 1_000_000, "millo": 1_000_000,
    "palos": 1_000_000, "palo": 1_000_000,
    "melones": 1_000_000, "melon": 1_000_000,
    "lucas": 1_000, "mil": 1_000, "k": 1_000, "m": 1_000_000,
}
_MULT_RX = "|".join(_MULTIPLOS)
_PAL_RX = "|".join(_NUM_PALABRA)

# cifra o palabra-numérica seguida (opcionalmente) de un sufijo multiplicador
_CON_MULT = re.compile(rf"(?<!\w)(\$?\d[\d.,]*|{ _PAL_RX })\s*({_MULT_RX})(?!\w)")
# cifra desnuda (sin sufijo)
_SOLO_DIGITOS = re.compile(r"(?<!\w)\$?\d[\d.,]*(?!\w)")


@dataclass(frozen=True)
class Monto:
    """Resultado del parseo. `valor_cop` ya convertido a pesos."""

    valor_cop: int | None
    moneda: Moneda = "COP"
    por_persona: bool = False


def _numero(crudo: str) -> float | None:
    """Convierte la cifra cruda ('1.500.000', '1,5', 'dos') a float."""
    s = crudo.lstrip("$")
    if s in _NUM_PALABRA:
        return float(_NUM_PALABRA[s])
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", s):  # separadores de miles
        return float(re.sub(r"[.,]", "", s))
    if re.fullmatch(r"\d+[.,]\d+", s):  # decimal: '1,5' o '1.5'
        return float(s.replace(",", "."))
    if s.isdigit():
        return float(s)
    return None


def parse(raw: str) -> Monto:
    """Texto con monto coloquial -> Monto(valor_cop, moneda, por_persona)."""
    t = quitar_tildes(raw)

    moneda: Moneda = "COP"
    if _MONEDA_USD.search(t):
        moneda = "USD"
    elif _MONEDA_EUR.search(t):
        moneda = "EUR"

    por_persona = any(marca in t for marca in _POR_PERSONA)

    valor: float | None = None
    m = _CON_MULT.search(t)
    if m:
        base = _numero(m.group(1))
        if base is not None:
            valor = base * _MULTIPLOS[m.group(2)]

    if valor is None:
        m = _SOLO_DIGITOS.search(t)
        if m:
            n = _numero(m.group(0))
            # cifra desnuda: aceptar solo si no es ambigua
            if n is not None and (moneda != "COP" or n >= 10_000):
                valor = n

    if valor is None:
        return Monto(None, moneda, por_persona)
    if moneda != "COP":
        return Monto(round(valor * _TASAS_COP[moneda]), moneda, por_persona)
    return Monto(round(valor), moneda, por_persona)
