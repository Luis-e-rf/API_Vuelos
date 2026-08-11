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
    "San Andres": 640_000,
    "Villa de Leyva": 250_000,
    "Leticia": 680_000,
    "Miami": 1_600_000,
    "Madrid": 2_900_000,
    "Quito": 1_100_000,
    "Panama": 1_300_000,
    "Cancun": 1_700_000,
    # Caribe colombiano
    "Riohacha": 540_000,
    "Valledupar": 500_000,
    "Monteria": 470_000,
    "Sincelejo": 490_000,
    "Providencia": 690_000,
    # Pacifico e islas
    "Tumaco": 720_000,
    "Bahia Solano": 780_000,
    "Nuqui": 800_000,
    "Jurado": 760_000,
    "Guapi": 700_000,
    "Isla Gorgona": 810_000,
    "Isla Malpelo": 850_000,
    "Quibdo": 660_000,
    # Amazonia y Orinoquia
    "Mitu": 930_000,
    "Puerto Carreno": 1_000_000,
    "San Jose del Guaviare": 750_000,
    "Puerto Inirida": 880_000,
    "La Pedrera": 1_050_000,
    "La Macarena": 760_000,
    "Miraflores": 920_000,
    "Florencia": 720_000,
    "Villa Garzon": 740_000,
    "Orocue": 960_000,
    "San Vicente del Caguan": 700_000,
    "Yopal": 570_000,
    "Tame": 740_000,
    "Arauca": 680_000,
    "Saravena": 700_000,
    "Puerto Asis": 860_000,
    "Condoto": 720_000,
    # Andes e interior
    "Bucaramanga": 390_000,
    "Pereira": 400_000,
    "Armenia": 410_000,
    "Manizales": 420_000,
    "Cucuta": 460_000,
    "Ibague": 430_000,
    "Neiva": 440_000,
    "Pasto": 580_000,
    "Ipiales": 640_000,
    "Popayan": 520_000,
    "Villavicencio": 330_000,
    "Barrancabermeja": 450_000,
    "Apartado": 620_000,
    "El Bagre": 650_000,
}

_AEROLINEAS_SIM = [
    "Avianca", "LATAM", "Wingo", "Viva Colombia", "Copa Airlines",
    "Satena", "EasyFly", "Clic", "Pacifico", "SEARCA",
]


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
        aerolinea: Optional[str] = None,
    ) -> list[OpcionVuelo]:
        if self.real:
            reales = await self._buscar_google(
                origen, fecha or fecha_default(), numero, destino=destino, aerolinea=aerolinea
            )
            if reales:
                return [_por_pasajeros(o, pasajeros) for o in reales]
            log.warning("Google Flights sin respuesta -> crea motor simulado")
        opciones = self._simular(origen, presupuesto_cop, fecha, numero, destino, aerolinea=aerolinea)
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
        aerolinea: Optional[str] = None,
    ) -> list[OpcionVuelo]:
        """Busca la opción más barata dentro de los próximos `meses` meses.

        Prueba un fin de semana por mes (sábado) y devuelve las más baratas.
        """
        hoy = datetime.now()
        fechas = []
        for m in range(min(meses, 5)):  # máx 5 consultas para no abusar de Google
            fechas.append(_sabado(hoy + timedelta(days=30 * m)))
        mejores: list[OpcionVuelo] = []
        if self.real:
            for f in fechas:
                opciones = await self._buscar_google(origen, f, numero, destino=destino, aerolinea=aerolinea)
                for o in opciones:
                    mejores.append(o)
        else:
            for f in fechas:
                mejores.extend(self._simular(origen, presupuesto_cop, f, 1, destino, aerolinea=aerolinea))
        mejores.sort(key=lambda o: o.precio_cop)
        if aerolinea:
            matching = [o for o in mejores if _coincide_aerolinea(o.aerolinea, aerolinea)]
            if matching:
                mejores = matching
        return [_por_pasajeros(o, pasajeros) for o in mejores[:numero]]

    # --- Google Flights (vivo) ----------------------------------------------

    async def _buscar_google(
        self, origen: str, fecha: str, numero: int, destino: Optional[str] = None,
        aerolinea: Optional[str] = None,
    ) -> list[OpcionVuelo]:
        """Pide precios reales a Google Flights por origen->varios destinos
        (o solo el destino pedido)."""
        origen_iata = _AEROPUERTOS.get(origen)
        if not origen_iata:
            log.warning("Google: origen %r sin IATA", origen)
            return []

        if destino:
            candidatos = [destino] if destino in _AEROPUERTOS and _AEROPUERTOS[destino] else []
        else:
            candidatos = [
                c for c in _AEROPUERTOS
                if c and c != origen and _AEROPUERTOS[c] and _AEROPUERTOS[c] != origen_iata
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
        if aerolinea:
            matching = [o for o in salida if _coincide_aerolinea(o.aerolinea, aerolinea)]
            if matching:
                salida = matching
        return salida[:numero]

    # --- Simulador de emergencia -------------------------------------------

    def _simular(
        self, origen: str, presupuesto_cop: int, fecha: Optional[str], numero: int,
        destino: Optional[str] = None, aerolinea: Optional[str] = None,
    ) -> list[OpcionVuelo]:
        """Opciones realistas dentro del presupuesto (solo offline/degradado)."""
        origen = origen or "Bogota"
        fecha = fecha or fecha_default()
        rnd = random.Random(f"{origen}|{fecha}")
        pool = [destino] if destino else _PRECIOS_COP.keys()
        destinos = sorted(
            (d for d in pool if d != origen and (
                destino or _PRECIOS_COP[d] <= presupuesto_cop
            )),
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
                    aerolinea=aerolinea or rnd.choice(_AEROLINEAS_SIM),
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


def _coincide_aerolinea(real: str, pedida: Optional[str]) -> bool:
    """'Avianca' == 'avianca' o 'LATAM Airlines' == 'latam'."""
    if not pedida:
        return True
    return pedida.lower() in real.lower() or real.lower() in pedida.lower()


def _duracion_de(mejor) -> str:
    try:
        # SingleFlight.duration en minutos
        seg = mejor.flights[0].duration
        h, m = divmod(seg, 60)
        return f"{h}h {m:02d}m"
    except Exception:  # noqa: BLE001
        return "—"