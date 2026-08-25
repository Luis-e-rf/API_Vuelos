"""Utilidades de texto compartidas por los normalizadores.

`quitar_tildes` preserva la longitud del string (solo elimina marcas
diacríticas combinantes), por lo que los offsets de `tokenizar` son
válidos sobre el texto original.
"""
from __future__ import annotations

import re
import unicodedata

_TOKEN_RX = re.compile(r"[a-z0-9]+")


def quitar_tildes(texto: str) -> str:
    """Minúsculas sin diacríticos: 'Bogotá' -> 'bogota', 'niño' -> 'nino'.

    Determinista y estable para comparar contra claves canónicas.
    """
    nfkd = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def tokenizar(texto: str) -> list[tuple[str, int]]:
    """Tokens normalizados con su offset en el texto original.

    Retorna [(token, offset)]. Los offsets apuntan al carácter inicial
    de cada token dentro del string ORIGINAL (útil para extraer spans).
    """
    base = quitar_tildes(texto)
    return [(m.group(0), m.start()) for m in _TOKEN_RX.finditer(base)]
