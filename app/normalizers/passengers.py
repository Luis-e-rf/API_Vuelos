"""Conteo determinista de viajeros a partir de lenguaje coloquial.

Estrategia por vocabulario (sin gramática), en orden de prioridad:
1. Cantidad + sustantivo de persona: "2 adultos y un niño" -> 3
2. Pareja explícita: "somos pareja", "matrimonio" -> 2
3. Familiar(es) + "y yo": "somos mi esposa y yo" -> 2
4. Número desnudo tras verbo de grupo: "somos 4", "vamos 3" -> N

Retorna None si no hay evidencia (el SlotManager pregunta, no adivina).
"""
from __future__ import annotations

from app.normalizers.text import tokenizar

_PERSONAS = {
    "persona", "personas", "adulto", "adultos", "adulta", "adultas",
    "nino", "ninos", "nina", "ninas", "bebe", "bebes", "pax",
    "viajero", "viajeros", "pasajero", "pasajeros",
}
_PAREJA = {"pareja", "matrimonio"}
_FAMILIARES = {
    "esposa", "mujer", "esposo", "marido", "novio", "novia",
    "hijo", "hija", "hijos", "hijas", "hermano", "hermana",
    "mama", "papa", "bebe",
}
_NUM_PALABRA = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
    "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14,
    "quince": 15, "veinte": 20,
}
_VERBOS_GRUPO = {"somos", "seremos", "vamos", "van", "iremos", "sere"}
_MAX = 20


def _valor_numerico(tok: str) -> int | None:
    if tok.isdigit():
        return int(tok)
    return _NUM_PALABRA.get(tok)


def parse(raw: str) -> int | None:
    """Texto con mención de viajeros -> cantidad (1..20) o None."""
    tokens = [w for w, _ in tokenizar(raw)]
    total = 0
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        n = _valor_numerico(tok)
        siguiente = tokens[i + 1] if i + 1 < len(tokens) else ""
        if n is not None and siguiente in _PERSONAS:
            total += n
            i += 2
            continue
        if tok in _PAREJA:
            total += 2
            i += 1
            continue
        if tok in _FAMILIARES:
            total += 1
            i += 1
            continue
        # número desnudo solo si lo precede un verbo de grupo ("somos 4")
        if (
            n is not None and 1 <= n <= _MAX and i > 0
            and tokens[i - 1] in _VERBOS_GRUPO
        ):
            total += n
        i += 1

    if total == 0:
        return None
    # "mi esposa Y YO": el 'yo' suma uno cuando ya hay alguien contado
    if any(tokens[j] == "y" and tokens[j + 1] == "yo"
           for j in range(len(tokens) - 1)):
        total += 1
    if not 1 <= total <= _MAX:
        return None
    return total
