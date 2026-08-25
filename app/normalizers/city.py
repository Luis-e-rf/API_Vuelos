"""CityNormalizer: texto libre -> nombre canónico de DESTINOS.

Estrategia (sin substring sobre la frase, fuente de falsos positivos tipo
'pan' -> 'Panama' en el legacy):
1. Ventanas de tokens consecutivos contra la tabla de alias EXACTOS
   (soporta multi-palabra: "san andres", "san jose del guaviare").
2. Último recurso: distancia difusa por palabra con umbral alto (0.88)
   y longitud mínima 4, para typos puros ("cartajena" -> "Cartagena").

La tabla reutiliza la fuente única app/destinos.py (análisis §3.4).
"""
from __future__ import annotations

import difflib

from app.destinos import DESTINOS, _ALIASES  # noqa: PLC2701 - fuente única
from app.normalizers.text import quitar_tildes, tokenizar

_DIFUSO_CUTOFF = 0.88
_DIFUSO_MIN_LEN = 4


def _construir_tabla() -> dict[str, str]:
    """Clave normalizada -> nombre canónico (alias + nombres canónicos)."""
    tabla: dict[str, str] = {}
    for canon in DESTINOS:
        tabla[quitar_tildes(canon)] = canon
    for alias, canon in _ALIASES.items():
        tabla.setdefault(quitar_tildes(alias), canon)
    return tabla


_ALIAS = _construir_tabla()
_MAX_VENTANA = max(len(clave.split()) for clave in _ALIAS)


def extraer_ciudades(texto: str) -> list[tuple[int, str]]:
    """Ciudades mencionadas con su offset, en orden de aparición.

    Retorna [(offset, canonico)]. Permite componer origen/destino cuando
    hay marcadores "de X a Y" sin gramática frágil.
    """
    tokens = tokenizar(texto)
    hallados: list[tuple[int, str]] = []
    i = 0
    while i < len(tokens):
        ventana_max = min(_MAX_VENTANA, len(tokens) - i)
        for n in range(ventana_max, 0, -1):  # ventana más larga primero
            clave = " ".join(tok for tok, _ in tokens[i : i + n])
            canon = _ALIAS.get(clave)
            if canon:
                hallados.append((tokens[i][1], canon))
                i += n
                break
        else:
            i += 1
    return hallados


def normalizar(texto: str) -> str | None:
    """Primera ciudad reconocible en el texto, o None.

    Alias exacto por ventana de tokens; difusa solo como último recurso.
    """
    ciudades = extraer_ciudades(texto)
    if ciudades:
        return ciudades[0][1]
    for token, _ in tokenizar(texto):
        if len(token) < _DIFUSO_MIN_LEN:
            continue
        cercano = difflib.get_close_matches(
            token, list(_ALIAS), n=1, cutoff=_DIFUSO_CUTOFF
        )
        if cercano:
            return _ALIAS[cercano[0]]
    return None
