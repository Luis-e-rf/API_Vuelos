from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.config import AMADEUS_API_KEY, AMADEUS_API_SECRET

log = logging.getLogger(__name__)

# Códigos IATA de los aeropuertos que conocemos (Colombia + algunos intl)
_AEROPUERTOS = {
    "Bogota": "BOG",
    "Medellin": "MDE",
    "Cali": "CLO",
    "Barranquilla": "BAQ",
    "Cartagena": "CTG",
    "Santa Marta": "SMR",
    "Villa de Leyva": "BOG",
    "Leticia": "LET",
    "Miami": "MIA",
    "Madrid": "MAD",
    "Lima": "LIM",
    "Quito": "UIO",
    "Panama": "PTY",
    "Cancun": "CUN",
}

# Precios aproximados de tiquete ida y vuelta en COP para un adulto.
# Base usada por el motor simulado hasta conectar Amadeus.
_PRECIOS_COP = {
    "Bogota": 0,
    "Medellin": 380_000,
    "Cali": 420_000,
    "Barranquilla": 520_000,
    "Cartagena": 560_000,
    "Santa Marta": 600_000,
    "Villa de Leyva": 250_000,
    "Leticia": 950_000,
    "Miami": 1_600_000,
    "Madrid": 2_900_000,
    "Quito": 1_100_000,
    "Panama": 1_300_000,
    "Cancun": 1_700_000,
}

_AEROLINEAS_SIM = ["Avianca", "LATAM", "Wingo", "Viva Colombia", "Copa Airlines"]


@dataclass
class OpcionVuelo:
    """Una oferta de vuelo lista para mostrarle al usuario."""

    destino: str
    fecha: str  # ISO YYYY-MM-DD
    precio_cop: int
    aerolinea: str
    duracion: str
    origen: str

    def cabecera(self) -> str:
        return (
            f"✈️ *{self.origen} → {self.destino}* · {self.fecha}\n"
            f"   💰 *{self.precio_cop:,.0f}* COP · {self.aerolinea} · {self.duracion}"
        )


class FlightClient:
    """Busca vuelos reales (Amadeus) si hay credenciales; si no, simula.

    Así el bot funciona desde ya sin tarjeta ni cuentas, y cuando el usuario
    cree sus credenciales Amadeus (gratis en test) pasa a datos reales.
    """

    def __init__(self) -> None:
        self.amadeus = bool(AMADEUS_API_KEY and AMADEUS_API_SECRET)

    async def buscar(
        self,
        origen: str,
        presupuesto_cop: int,
        moneda: Optional[str] = None,
        fecha: Optional[str] = None,
        numero: int = 3,
    ) -> list[OpcionVuelo]:
        fechas = _construir_fechas(fecha)
        if self.amadeus:
            reales = await self._buscar_amadeus(origen, numero)
            if reales:
                return reales
            log.warning("Amadeus sin respuesta -> uso el motor simulado")
        return self._simular(origen, presupuesto_cop, fechas, numero)

    # --- implementación --------------------------------------------------

    async def _buscar_amadeus(self, origen: str, numero: int) -> list[OpcionVuelo]:
        """Busca ofertas con Amadeus v2 flight-offers (entorno de prueba, gratis)."""
        origen_iata = _AEROPUERTOS.get(origen)
        if not origen_iata:
            log.warning("Amadeus: origen %r sin IATA", origen)
            return []

        async with httpx.AsyncClient(timeout=10) as client:
            try:
                r = await client.post(
                    "https://test.api.amadeus.com/v1/security/oauth2/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": AMADEUS_API_KEY,
                        "client_secret": AMADEUS_API_SECRET,
                    },
                )
                r.raise_for_status()
                token = r.json()["access_token"]
            except Exception as exc:  # noqa: BLE001
                log.warning("Amadeus token falló: %s", exc)
                return []

            destinos = [c for c in _AEROPUERTOS if c != origen][:5]
            salida: list[OpcionVuelo] = []
            for d in destinos:
                try:
                    r = await client.get(
                        "https://test.api.amadeus.com/v2/shopping/flight-offers",
                        params={
                            "originLocationCode": origen_iata,
                            "destinationLocationCode": _AEROPUERTOS[d],
                            "departureDate": fecha_siguiente_sabado(),
                            "adults": 1,
                            "currencyCode": "COP",
                            "max": 2,
                            "oneWay": "false",
                        },
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    r.raise_for_status()
                    for oferta in r.json().get("data", []):
                        precio = float(oferta["price"]["total"])
                        if precio > 0 and len(salida) < numero:
                            salida.append(
                                OpcionVuelo(
                                    destino=d,
                                    fecha=fecha_siguiente_sabado(),
                                    precio_cop=round(precio),
                                    aerolinea=_aerolinea(oferta),
                                    duracion=_duracion(oferta),
                                    origen=origen,
                                )
                            )
                except Exception as exc:  # noqa: BLE001
                    log.warning("Amadeus %r falló: %s", d, exc)
                    continue
                if len(salida) >= numero:
                    break
        return salida

    def _simular(
        self, origen: str, presupuesto_cop: int, fechas: list[str], numero: int
    ) -> list[OpcionVuelo]:
        """Genera opciones realistas dentro del presupuesto (motor de demo).

        Es determinístico por fecha, para que no cambien entre llamadas.
        """
        origen = origen or "Bogota"
        rnd = random.Random(f"{origen}|{fechas[0]}")
        destinos = sorted(
            (d for d in _PRECIOS_COP if d != origen and _PRECIOS_COP[d] <= presupuesto_cop),
            key=lambda d: _PRECIOS_COP[d],
        )[:numero]
        opciones: list[OpcionVuelo] = []
        for i, d in enumerate(destinos):
            base = _PRECIOS_COP[d]
            precio = int(base * rnd.uniform(0.9, 1.15) / 1000) * 1000
            opciones.append(
                OpcionVuelo(
                    destino=d,
                    fecha=fechas[i % len(fechas)],
                    precio_cop=precio,
                    aerolinea=rnd.choice(_AEROLINEAS_SIM),
                    duracion=rnd.choice(["1h 20m", "1h 50m", "2h 05m", "1h 40m"]),
                    origen=origen,
                )
            )
        return opciones


# --- helpers ---------------------------------------------------------


def fecha_siguiente_sabado() -> str:
    hoy = datetime.now()
    dias = (5 - hoy.weekday()) % 7
    if dias == 0:
        dias = 7
    return (hoy + timedelta(days=dias)).strftime("%Y-%m-%d")


def _construir_fechas(fecha: Optional[str]) -> list[str]:
    base = datetime.now()
    if fecha and fecha.strip().lower() in ("sabado", "sábado"):
        return [fecha_siguiente_sabado()]
    return [(base + timedelta(days=d)).strftime("%Y-%m-%d") for d in (1, 2, 3)]


def _duracion(oferta: dict) -> str:
    try:
        seg = int(oferta["itineraries"][0]["duration"][2:-1])  # "PT1H20M"
        h, m = divmod(seg, 60)
        return f"{h}h {m:02d}m"
    except Exception:  # noqa: BLE001
        return "—"


def _aerolinea(oferta: dict) -> str:
    try:
        return oferta["itineraries"][0]["segments"][0]["carrierCode"]
    except Exception:  # noqa: BLE001
        return "Aerolínea"