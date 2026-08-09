from __future__ import annotations

import difflib
import re
from typing import Optional

# Fuente única de destinos soportados y sus aeropuertos (IATA).
# Rodrigo: agregar destinos aquí los hace entendibles por el bot en todo el
# pipeline (intérprete → motor de vuelos → mensaje).
DESTINOS: dict[str, str] = {
    "Bogota": "BOG",
    "Medellin": "MDE",
    "Cali": "CLO",
    "Barranquilla": "BAQ",
    "Cartagena": "CTG",
    "Santa Marta": "SMR",
    "San Andres": "ADZ",
    "Villa de Leyva": "BOG",
    "Leticia": "LET",
    "Miami": "MIA",
    "Madrid": "MAD",
    "Lima": "LIM",
    "Quito": "UIO",
    "Panama": "PTY",
    "Cancun": "CUN",
}

# Alias en minúsculas que el intérprete entiende aunque el usuario escriba
# mal o con apodos (¡"barajilla" -> Barranquilla!).
_ALIASES: dict[str, str] = {
    "bogota": "Bogota",
    "bogotá": "Bogota",
    "bta": "Bogota",
    "medellin": "Medellin",
    "medellín": "Medellin",
    "mde": "Medellin",
    "cali": "Cali",
    "barranquilla": "Barranquilla",
    "baranquilla": "Barranquilla",
    "baranq": "Barranquilla",
    "barajilla": "Barranquilla",
    "baranji": "Barranquilla",
    "baq": "Barranquilla",
    "cartagena": "Cartagena",
    "cartajena": "Cartagena",
    "ctg": "Cartagena",
    "santa marta": "Santa Marta",
    "santamarta": "Santa Marta",
    "smr": "Santa Marta",
    "san andres": "San Andres",
    "san andrés": "San Andres",
    "sanandres": "San Andres",
    "san mindres": "San Andres",
    "adi": "San Andres",
    "adz": "San Andres",
    "villa de leyva": "Villa de Leyva",
    "villa de leiva": "Villa de Leyva",
    "leticia": "Leticia",
    "let": "Leticia",
    "miami": "Miami",
    "mia": "Miami",
    "madrid": "Madrid",
    "mad": "Madrid",
    "lima": "Lima",
    "lim": "Lima",
    "quito": "Quito",
    "uio": "Quito",
    "panama": "Panama",
    "panamá": "Panama",
    "pty": "Panama",
    "cancun": "Cancun",
    "cancún": "Cancun",
    "cun": "Cancun",
}

_NOMBRES = list(DESTINOS.keys())


def normalizar_destino(texto: str) -> Optional[str]:
    """Devuelve el nombre canónico de un destino dado un texto libre del usuario.

    Orden de ataque:
      1. Alias exacto encontrado como substring (tolera 'vamos a baranquilla').
      2. Diferencias difusas contra la lista completa (tolera typo puro).
    3. Ninguno -> None.
    """
    t = texto.strip().lower()
    # 1) alias directo, permitiendo que aparezca embebido
    for token, canon in _ALIASES.items():
        if token in t:
            return canon
    # 2) coincidencia difusa palabra a palabra
    palabras = [p for p in t.replace(",", " ").split() if len(p) >= 3]
    for pal in palabras:
        match = difflib.get_close_matches(pal, _NOMBRES, n=1, cutoff=0.86)
        if match:
            return match[0]
    # 3) el texto entero contra la lista (p. ej. "barránquilla" sin espacios)
    entero = difflib.get_close_matches(t, _NOMBRES, n=1, cutoff=0.9)
    if entero:
        return entero[0]
    return None


_MARCADORES_ORIGEN = (
    " desde ",
    "salgo de ",
    "me voy de ",
    "parto de ",
    "saliendo de ",
    "salida desde ",
)


def quitar_origen(texto: str) -> str:
    """Quita del texto la ciudad que sigue a un marcador de origen
    ("desde bogota" -> "") para que el destino se detecte sin confusión."""
    t = texto.lower()
    for alias, _c in _ALIASES.items():
        for marker in _MARCADORES_ORIGEN:
            pat = marker + alias
            if pat in t:
                t = t.replace(pat, " ", 1)
                break
    return t.strip()