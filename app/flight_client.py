from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from app.config import FAST_FLIGHTS_ENABLED
from app.destinos import DESTINOS

log = logging.getLogger(__name__)

try:  # dependencia opcional: si no está instalada, usamos solo el simulador
    from fast_flights import FlightQuery, Passengers, create_query, get_flights

    _FAST_FLIGHTS_OK = True
except Exception:  # noqa: BLE001
    _FAST_FLIGHTS_OK = False

# Códigos IATA de los aeropuertos que conocemos (Colombia + algunos intl).
# Fuente única en app/destinos.py; aquí solo el alias para compatibilidad.
_AEROPUERTOS = DESTINOS

# Precios aproximados de tiquete (COP) para el motor simulado de emergencia.
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
    real: bool = False  # True si vino de Google Flights (fast-flights)

    def cabecera(self) -> str:
        return (
            f"✈️ *{self.origen} → {self.destino}* · {self.fecha}\n"
            f"   💰 *{self.precio_cop:,.0f}* COP · {self.aerolinea} · {self.duracion}"
        )


class FlightClient:
    """Busca vuelos en vivo (Google Flights vía fast-flights) si es posible;
    si falla por red/cuota, cae a un motor simulado determinístico.

    Así el bot funciona desde ya gratis y da precios reales normalmente.
    """

    def __init__(self) -> None:
        self.real = _FAST_FLIGHTS_OK and FAST_FLIGHTS_ENABLED

    async def buscar(
        self,
        origen: str,
        presupuesto_cop: int,
        moneda: Optional[str] = None,
        fecha: Optional[str] = None,
        numero: int = 3,
        destino: Optional[str] = None,
        pasajeros: int = 1,
    ) -> list[OpcionVuelo]:
        if self.real:
            reales = await self._buscar_google(
                origen, fecha or fecha_default(), numero, destino=destino
            )
            if reales:
                return [_por_pasajeros(o, pasajeros) for o in reales]
            log.warning("Google Flights sin respuesta -> crea motor simulado")
        opciones = self._simular(origen, presupuesto_cop, fecha, numero, destino)
        return [_por_pasajeros(o, pasajeros) for o in opciones]

    async def buscar_rango(
        self,
        origen: str,
        presupuesto_cop: int,
        meses: int,
        moneda: Optional[str] = None,
        destino: Optional[str] = None,
        numero: int = 3,
        pasajeros: int = 1,
    ) -> list[OpcionVuelo]:
        """Busca la opción más barata dentro de los próximos `meses` meses.

        Prueba un fin de semana por mes (sábado) y devuelve las más baratas.
        """
        hoy = datetime.now()
        fechas = []
        for m in range(min(meses, 5)):  # máx 5 consultas para no abusar de Google
            fechas.append(_sabado(hoy + timedelta(days=30 * m)))
        mejores: list[OpcionVuelo] = []
        for f in fechas:
            opciones = await self._buscar_google(origen, f, numero, destino=destino)
            for o in opciones:
                mejores.append(o)
        mejores.sort(key=lambda o: o.precio_cop)
        return [_por_pasajeros(o, pasajeros) for o in mejores[:numero]]

    # --- Google Flights (vivo) ----------------------------------------------

    async def _buscar_google(
        self, origen: str, fecha: str, numero: int, destino: Optional[str] = None
    ) -> list[OpcionVuelo]:
        """Pide precios reales a Google Flights por origen->varios destinos
        (o solo el destino pedido)."""
        origen_iata = _AEROPUERTOS.get(origen)
        if not origen_iata:
            log.warning("Google: origen %r sin IATA", origen)
            return []

        if destino:
            candidatos = [destino] if destino in _AEROPUERTOS else []
        else:
            candidatos = [
                c for c in _AEROPUERTOS
                if c and c != origen and _AEROPUERTOS[c] != origen_iata
            ][:6]
        salida: list[OpcionVuelo] = []

        destinos = candidatos
        async def _uno(dest: str) -> None:
            try:
                resultado = await asyncio.to_thread(
                    get_flights,
                    create_query(
                        flights=[
                            FlightQuery(
                                date=fecha,
                                from_airport=origen_iata,
                                to_airport=_AEROPUERTOS[dest],
                            )
                        ],
                        seat="economy",
                        trip="one-way",
                        passengers=Passengers(adults=1, children=0),
                        language="es",
                        currency="COP",
                    ),
                )
                mejor = None
                for f in resultado:  # cada f : Flights (precio en COP)
                    precio = int(f.price)
                    if mejor is None or precio < mejor.price:
                        mejor = f
                if mejor is None:
                    return
                salida.append(
                    OpcionVuelo(
                        destino=dest,
                        fecha=fecha,
                        precio_cop=mejor.price,
                        aerolinea=", ".join(mejor.airlines) or "Aerolínea",
                        duracion=_duracion_de(mejor),
                        origen=origen,
                        real=True,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Google Flights %r falló: %s", dest, exc)

        await asyncio.gather(*[_uno(d) for d in destinos])
        salida.sort(key=lambda o: o.precio_cop)
        return salida[:numero]

    # --- Simulador de emergencia -------------------------------------------

    def _simular(
        self, origen: str, presupuesto_cop: int, fecha: Optional[str], numero: int,
        destino: Optional[str] = None,
    ) -> list[OpcionVuelo]:
        """Opciones realistas dentro del presupuesto (solo offline/degradado)."""
        origen = origen or "Bogota"
        fecha = fecha or fecha_default()
        rnd = random.Random(f"{origen}|{fecha}")
        pool = [destino] if destino else _PRECIOS_COP.keys()
        destinos = sorted(
            (d for d in pool if d != origen and _PRECIOS_COP[d] <= presupuesto_cop),
            key=lambda d: _PRECIOS_COP[d],
        )[:numero]
        opciones: list[OpcionVuelo] = []
        for i, d in enumerate(destinos):
            base = _PRECIOS_COP[d]
            precio = int(base * rnd.uniform(0.9, 1.15) / 1000) * 1000
            opciones.append(
                OpcionVuelo(
                    destino=d,
                    fecha=_proxima_fecha(fecha, i),
                    precio_cop=precio,
                    aerolinea=rnd.choice(_AEROLINEAS_SIM),
                    duracion=rnd.choice(["1h 20m", "1h 50m", "2h 05m", "1h 40m"]),
                    origen=origen,
                )
            )
        return opciones


# --- helpers ---------------------------------------------------------


def fecha_default() -> str:
    """Fecha de salida sugerida: el próximo sábado (short trip)."""
    return _sabado(datetime.now())


def _sabado(base: datetime) -> str:
    dias = (5 - base.weekday()) % 7
    dias = dias or 7
    return (base + timedelta(days=dias)).strftime("%Y-%m-%d")


def _por_pasajeros(o: OpcionVuelo, pasajeros: int) -> OpcionVuelo:
    if pasajeros and pasajeros > 1:
        o.precio_cop = int(o.precio_cop * pasajeros / 1000) * 1000
    return o


def _proxima_fecha(base: str, offset: int) -> str:
    return (
        datetime.strptime(base, "%Y-%m-%d") + timedelta(days=offset)
    ).strftime("%Y-%m-%d")


def _duracion_de(mejor) -> str:
    try:
        # SingleFlight.duration en minutos
        seg = mejor.flights[0].duration
        h, m = divmod(seg, 60)
        return f"{h}h {m:02d}m"
    except Exception:  # noqa: BLE001
        return "—"