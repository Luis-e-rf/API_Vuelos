from __future__ import annotations

import logging
from typing import Optional

import httpx

log = logging.getLogger(__name__)

# Título de artículo de Wikipedia de cada destino (base para buscar la foto).
# El nombre canónico del destino en destinos.py -> título útil en es.wikipedia.
_TITULOS: dict[str, str] = {
    "Bogota": "Bogotá",
    "Medellin": "Medellín",
    "Cali": "Cali",
    "Barranquilla": "Barranquilla",
    "Cartagena": "Cartagena (Colombia)",
    "Santa Marta": "Santa Marta",
    "San Andres": "San Andrés y Providencia",
    "Villa de Leyva": "Villa de Leyva",
    "Leticia": "Leticia",
    "Miami": "Miami",
    "Madrid": "Madrid",
    "Lima": "Lima",
    "Quito": "Quito",
    "Panama": "Panamá",
    "Cancun": "Cancún",
    "Riohacha": "Riohacha",
    "Valledupar": "Valledupar",
    "Monteria": "Montería",
    "Sincelejo": "Sincelejo",
    "Providencia": "Providencia (Colombia)",
    "Tumaco": "Tumaco",
    "Bahia Solano": "Bahía Solano",
    "Nuqui": "Nuquí",
    "Jurado": "Juradó",
    "Guapi": "Guapi",
    "Isla Gorgona": "Isla de Gorgona",
    "Isla Malpelo": "Isla de Malpelo",
    "Quibdo": "Quibdó",
    "Mitu": "Mitú",
    "Leticia": "Leticia",
    "La Macarena": "La Macarena",
    "Yopal": "Yopal",
    "Bucaramanga": "Bucaramanga",
    "Pereira": "Pereira",
    "Manizales": "Manizales",
    "Cucuta": "Cúcuta",
    "Neiva": "Neiva",
    "Pasto": "Pasto",
    "Popayan": "Popayán",
    "Villavicencio": "Villavicencio",
}

_WIKIPEDIA_ES: dict[str, str | None] = {}
_NONE_CACHE_MAX = 100  # máx entradas None en cache
_none_count = 0


async def foto_destino(destino: str) -> Optional[str]:
    """Devuelve una URL de imagen (Wikimedia Commons open license) del destino.

    Usa la REST API resumida de Wikipedia (sin clave). Cachea por proceso.
    Devuelve None si no hay nada (el bot seguirá sin foto).
    """
    clave = destino.title()
    if clave in _WIKIPEDIA_ES:
        return _WIKIPEDIA_ES[clave]

    titulo = _TITULOS.get(destino) or destino.title()
    titulo = titulo.replace(" ", "%20")
    url = f"https://es.wikipedia.org/api/rest_v1/page/summary/{titulo}"
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.get(url, headers={"User-Agent": "API_Vuelos/0.1 (bot personal)"})
            r.raise_for_status()
            data = r.json()
        # propuesta: a veces da "thumbnail" resumido (solo la thumbnail, ya sirve)
        thumb = data.get("thumbnail") or {}
        imagen = thumb.get("source")
        if not imagen:
            # fallback: original si hay
            original = data.get("originalimage") or {}
            imagen = original.get("source")
        _WIKIPEDIA_ES[clave] = imagen
        return imagen
    except Exception as exc:  # noqa: BLE001
        log.warning("Foto de Wikipedia para %r falló: %s", destino, exc)
        # No cachear None permanentemente - permitir reintentos
        return None