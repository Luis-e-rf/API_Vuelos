"""Tests FASE 4: FlightClient sin doble conteo + validación pre-búsqueda.

- Con Google Flights (real): el precio ya viene por TODO el grupo, no se
  re-multiplica por pasajeros (bug histórico de doble conteo).
- Con simulador: precios por persona -> sí se multiplican.
- ActionExecutor: no llama a la API si falta presupuesto o el origen no
  tiene IATA.
"""
from __future__ import annotations

import pytest

from app.dialogue.executor import ActionExecutor
from app.flight_client import FlightClient, OpcionVuelo
from app.models import MensajeSalida, UserState
from app.nlu.schemas import NormalizedSlots


def _opcion(precio: int, destino: str = "San Andres") -> OpcionVuelo:
    return OpcionVuelo(destino=destino, fecha="2027-01-15", precio_cop=precio,
                       aerolinea="Wingo", duracion="1h 30m", origen="Bogota", real=True)


class TestDobleConteo:
    async def test_google_no_remultiplica(self, monkeypatch):
        client = FlightClient()
        client.real = True

        async def fake_google(origen, fecha, numero, destino=None,
                              aerolinea=None, pasajeros=1):
            return [_opcion(2_000_000)]  # Google ya cotizó 2 adultos

        monkeypatch.setattr(client, "_buscar_google", fake_google)
        res = await client.buscar("Bogota", 3_000_000, "COP", pasajeros=2)
        assert res[0].precio_cop == 2_000_000  # NO 4_000_000

    async def test_simulador_si_multiplica(self):
        client = FlightClient()
        client.real = False
        res = await client.buscar("Bogota", 50_000_000, "COP",
                                  destino="Medellin", pasajeros=3)
        # base Medellin 380k x3 = 1.14M (±15%): muy lejos del precio unitario
        assert res[0].precio_cop >= 900_000
        assert res[0].precio_cop % 1000 == 0

    async def test_rango_real_no_remultiplica(self, monkeypatch):
        client = FlightClient()
        client.real = True

        async def fake_google(origen, fecha, numero, destino=None,
                              aerolinea=None, pasajeros=1):
            return [_opcion(1_500_000)]

        monkeypatch.setattr(client, "_buscar_google", fake_google)
        res = await client.buscar_rango("Bogota", 5_000_000, 2, pasajeros=2)
        assert res[0].precio_cop == 1_500_000


class TestValidacionPreBusqueda:
    async def test_origen_sin_iata_no_llama_api(self):
        ejecutor = ActionExecutor(flight=object())
        llamadas = []

        async def _fake(*a, **k):
            llamadas.append(1)
            return MensajeSalida("x")

        ejecutor.flight = type("F", (), {"buscar": staticmethod(_fake),
                                         "buscar_rango": staticmethod(_fake)})()
        slots = NormalizedSlots(origen="Isla Malpelo", destino="Cartagena",
                                presupuesto_cop=1_000_000)
        salida = await ejecutor.buscar(slots, UserState())
        assert "Isla Malpelo" in salida.texto
        assert llamadas == []  # nunca llegó a Google Flights

    async def test_sin_presupuesto_pregunta(self):
        ejecutor = ActionExecutor(flight=object())
        slots = NormalizedSlots(origen="Bogota", destino="Cartagena")
        salida = await ejecutor.buscar(slots, UserState())
        assert "presupuesto" in salida.texto.lower()
