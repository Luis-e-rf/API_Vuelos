from __future__ import annotations

from urllib.parse import quote

from app.destinos import DESTINOS


def link_google_flights(origen: str, destino: str, fecha: str) -> str:
    """Devuelve un link de búsqueda de Google Flights para ese vuelo.

    Formato entendido por Google:
      https://www.google.com/travel/flights?q=Flights from BOG to MDE on 2026-12-25

    Usa los códigos IATA de destinos.py; si alguno no tiene IATA (p. ej. una
    isla sin aeropuerto), devuelve None y el bot no mostrará link.
    """
    o = DESTINOS.get(origen)
    d = DESTINOS.get(destino)
    if not o or not d or not fecha:
        return None
    q = f"Flights from {o} to {d} on {fecha}"
    return f"https://www.google.com/travel/flights?q={quote(q)}"


def link_mensaje_vuelo(origen: str, destino: str, fecha: str) -> str:
    """El texto amigable que acompaña al link de compra."""
    link = link_google_flights(origen, destino, fecha)
    if not link:
        return ""
    return (
        f"\n\n🔗 *Compra este vuelo en Google Flights:*\n{link}\n\n"
        "Ahí eliges tu aerolínea, horarios y pagas directo. ✈️"
    )
