from __future__ import annotations

import difflib
import re
from typing import Optional

# Fuente única de destinos soportados y sus aeropuertos (IATA).
# Rodrigo: agregar destinos aquí los hace entendibles por el bot en todo el
# pipeline (intérprete → motor de vuelos → mensaje).
# IATA vacío ("") = no tiene aeropuerto comercial (p. ej. isla Malpelo):
# Google Flights no lo busca, pero el motor simulado de emergencia sí lo ofrece.
DESTINOS: dict[str, str] = {
    # --- Principales / internacionales ---
    "Bogota": "BOG",
    "Medellin": "MDE",
    "Cali": "CLO",
    "Barranquilla": "BAQ",
    "Cartagena": "CTG",
    "Santa Marta": "SMR",
    "San Andres": "ADZ",
    "Villa de Leyva": "BOG",
    "Miami": "MIA",
    "Madrid": "MAD",
    "Lima": "LIM",
    "Quito": "UIO",
    "Panama": "PTY",
    "Cancun": "CUN",
    # --- Caribe colombiano ---
    "Riohacha": "RCH",
    "Valledupar": "VUP",
    "Monteria": "MTR",
    "Sincelejo": "CZU",
    "Providencia": "PVA",
    # --- Pacifico colombiano e islas ---
    "Tumaco": "TCO",
    "Bahia Solano": "BSC",
    "Nuqui": "NQU",
    "Jurado": "JUO",
    "Guapi": "GPI",
    "Isla Gorgona": "GPI",
    "Isla Malpelo": "",
    "Quibdo": "UIB",
    # --- Amazonia y Orinoquia ---
    "Leticia": "LET",
    "Mitu": "MVP",
    "Puerto Carreno": "PCR",
    "San Jose del Guaviare": "SJE",
    "Puerto Inirida": "PDA",
    "La Pedrera": "LPD",
    "La Macarena": "LMC",
    "Miraflores": "MFS",
    "Florencia": "FLA",
    "Villa Garzon": "VGZ",
    "Orocue": "ORC",
    "San Vicente del Caguan": "SVI",
    "Yopal": "EYP",
    "Tame": "TME",
    "Arauca": "AUC",
    "Saravena": "RVE",
    "Puerto Asis": "PUU",
    "Condoto": "COG",
    # --- Andes e interior ---
    "Bucaramanga": "BGA",
    "Pereira": "PEI",
    "Armenia": "AXM",
    "Manizales": "MZL",
    "Cucuta": "CUC",
    "Ibague": "IBE",
    "Neiva": "NVA",
    "Pasto": "PSO",
    "Ipiales": "IPI",
    "Popayan": "PPN",
    "Villavicencio": "VVC",
    "Barrancabermeja": "EJA",
    "Apartado": "APO",
    "El Bagre": "EBG",
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
    "riohacha": "Riohacha",
    "valledupar": "Valledupar",
    "monteria": "Monteria",
    "montería": "Monteria",
    "sincelejo": "Sincelejo",
    "providencia": "Providencia",
    "tumaco": "Tumaco",
    "bahia solano": "Bahia Solano",
    "bahía solano": "Bahia Solano",
    "bahia-solano": "Bahia Solano",
    "nuqui": "Nuqui",
    "nuquí": "Nuqui",
    "jurado": "Jurado",
    "juradó": "Jurado",
    "guapi": "Guapi",
    "isla gorgona": "Isla Gorgona",
    "gorgona": "Isla Gorgona",
    "isla malpelo": "Isla Malpelo",
    "malpelo": "Isla Malpelo",
    "quibdo": "Quibdo",
    "quibdó": "Quibdo",
    "mitu": "Mitu",
    "mitú": "Mitu",
    "puerto carreño": "Puerto Carreno",
    "puerto carreno": "Puerto Carreno",
    "san jose del guaviare": "San Jose del Guaviare",
    "san josé del guaviare": "San Jose del Guaviare",
    "guaviare": "San Jose del Guaviare",
    "puerto inirida": "Puerto Inirida",
    "puerto inírida": "Puerto Inirida",
    "inirida": "Puerto Inirida",
    "la pedrera": "La Pedrera",
    "la macarena": "La Macarena",
    "macarena": "La Macarena",
    "miraflores": "Miraflores",
    "florencia": "Florencia",
    "villa garzon": "Villa Garzon",
    "villa garzón": "Villa Garzon",
    "orocue": "Orocue",
    "orocu": "Orocue",
    "san vicente del caguan": "San Vicente del Caguan",
    "san vicente del caguán": "San Vicente del Caguan",
    "caguan": "San Vicente del Caguan",
    "yopal": "Yopal",
    "tame": "Tame",
    "arauca": "Arauca",
    "saravena": "Saravena",
    "puerto asis": "Puerto Asis",
    "puerto asís": "Puerto Asis",
    "asis": "Puerto Asis",
    "condoto": "Condoto",
    "bucaramanga": "Bucaramanga",
    "pereira": "Pereira",
    "armenia": "Armenia",
    "manizales": "Manizales",
    "cucuta": "Cucuta",
    "cúcuta": "Cucuta",
    "ibague": "Ibague",
    "ibagué": "Ibague",
    "neiva": "Neiva",
    "pasto": "Pasto",
    "ipiales": "Ipiales",
    "popayan": "Popayan",
    "popayán": "Popayan",
    "villavicencio": "Villavicencio",
    "barrancabermeja": "Barrancabermeja",
    "apartado": "Apartado",
    "apartadó": "Apartado",
    "el bagre": "El Bagre",
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


def destinos_en_texto(texto: str) -> list[str]:
    """Devuelve los destinos mencionados en orden de aparición, sin duplicar.
    Útil para manejar correcciones ("mejor ya no gorgona si no medellin")."""
    t = texto.lower()
    encontrados: list[tuple[int, str]] = []
    for alias, canon in _ALIASES.items():
        pos = 0
        while True:
            idx = t.find(alias, pos)
            if idx < 0:
                break
            encontrados.append((idx, canon))
            pos = idx + 1
    # dedupe por canon, conservando la primera posición de cada uno
    vistos: dict[str, int] = {}
    for idx, canon in encontrados:
        if canon not in vistos or idx < vistos[canon]:
            vistos[canon] = idx
    return [c for c, _ in sorted(vistos.items(), key=lambda kv: kv[1])]