"""Tests del DialogueManager + SlotManager (FASE 3).

Todo offline: Extractor determinista (sin GEMINI_API_KEY), chitchat sin
proveedores, FlightClient fake y fotos mockeadas. Verifica:
- early-reset ANTES del NLU con borrado real de la clave
- búsqueda con slots normalizados correctos (combo coloquial)
- ASK_SLOT específico y resolución en el turno siguiente
- invariantes: presupuesto <= 50k, fecha pasada
- select_option sobre opciones_recientes
- persistencia UserState v2 (memoria) con historial recortado
"""
from __future__ import annotations

import pytest

from app.dialogue_manager import DialogueManager
from app.flight_client import OpcionVuelo
from app.models import MensajeEntrada, MensajeSalida, UserState
from app.profile_store import ProfileStore


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Sin LLM y sin red en toda la suite."""
    monkeypatch.setattr("app.nlu.extractor.GEMINI_API_KEY", "")

    async def _sin_foto(destino):
        return None

    monkeypatch.setattr("app.dialogue.executor.foto_destino", _sin_foto)


class FakeSender:
    def __init__(self):
        self.enviados: list[MensajeSalida] = []

    async def enviar(self, chat_id: str, salida: MensajeSalida) -> None:
        self.enviados.append(salida)


class FakeFlight:
    def __init__(self, opciones=None):
        self.llamadas = []
        self.opciones = opciones or [
            OpcionVuelo(destino="San Andres", fecha="2027-01-15",
                        precio_cop=1_950_000, aerolinea="Wingo",
                        duracion="1h 30m", origen="Bogota"),
            OpcionVuelo(destino="San Andres", fecha="2027-01-16",
                        precio_cop=2_100_000, aerolinea="Avianca",
                        duracion="1h 25m", origen="Bogota"),
        ]

    async def buscar(self, origen, presupuesto_cop, moneda=None, fecha=None,
                     numero=3, destino=None, pasajeros=1, aerolinea=None):
        self.llamadas.append(dict(origen=origen, presupuesto_cop=presupuesto_cop,
                                  fecha=fecha, destino=destino, pasajeros=pasajeros))
        return self.opciones

    async def buscar_rango(self, origen, presupuesto_cop, meses, moneda=None,
                           destino=None, numero=3, pasajeros=1, aerolinea=None):
        self.llamadas.append(dict(origen=origen, meses=meses,
                                  presupuesto_cop=presupuesto_cop, destino=destino))
        return self.opciones[:1]


def _nuevo():
    store = ProfileStore()  # sin credenciales -> memoria local
    flight = FakeFlight()
    manager = DialogueManager(store=store, flight=flight)
    return store, flight, manager


async def _turno(manager, texto, chat_id="test"):
    sender = FakeSender()
    await manager.procesar(
        MensajeEntrada(chat_id=chat_id, texto=texto, canal="whatsapp"), sender
    )
    assert sender.enviados, f"sin respuesta para {texto!r}"
    return sender.enviados[-1]


# --- reset ------------------------------------------------------------------


class TestReset:
    async def test_early_reset_antes_del_nlu(self, monkeypatch):
        store, _, manager = _nuevo()
        borradas = []

        async def _spy_borrar(chat_id, canal="unknown"):
            borradas.append((chat_id, canal))
            return True

        monkeypatch.setattr(store, "borrar", _spy_borrar)

        async def _explotar(texto):
            raise AssertionError("el NLU no debe ejecutarse en un reset")

        monkeypatch.setattr(manager.nlu, "extract", _explotar)

        salida = await _turno(manager, "olvida todo")
        assert "cero" in salida.texto.lower()
        assert borradas == [("test", "whatsapp")]

    async def test_reset_via_llm_hint(self, monkeypatch):
        store, _, manager = _nuevo()

        async def _raw_reset(texto):
            from app.nlu.schemas import RawSlots
            return RawSlots(intent_hint="reset")

        monkeypatch.setattr(manager.nlu, "extract", _raw_reset)
        salida = await _turno(manager, "borra esta conversación por favor")
        assert "cero" in salida.texto.lower()


# --- flujo completo -----------------------------------------------------------


class TestBusqueda:
    async def test_combo_colloquial_slots_correctos(self):
        _, flight, manager = _nuevo()
        salida = await _turno(
            manager, "de Bogotá a San Andrés, 2 personas, 1 millón por persona 2027"
        )
        llamada = flight.llamadas[0]
        assert llamada["origen"] == "Bogota"
        assert llamada["destino"] == "San Andres"
        assert llamada["presupuesto_cop"] == 2_000_000   # no 1000 ni 1M
        assert llamada["pasajeros"] == 2
        assert llamada["fecha"] == "2027-01-15"
        assert "San Andres" in salida.texto

    async def test_pregunta_presupuesto_y_resuelve_siguiente_turno(self):
        _, flight, manager = _nuevo()
        salida = await _turno(manager, "pa cartagena desde medellin")
        assert "presupuesto" in salida.texto.lower()
        salida2 = await _turno(manager, "un palo")
        assert flight.llamadas[0]["destino"] == "Cartagena"
        assert flight.llamadas[0]["origen"] == "Medellin"
        assert flight.llamadas[0]["presupuesto_cop"] == 1_000_000
        assert salida2.texto

    async def test_pregunta_origen_primera_vez(self):
        _, flight, manager = _nuevo()
        salida = await _turno(manager, "pa san andres con un palo")
        assert "ciudad" in salida.texto.lower()      # ¿Desde qué ciudad sales?
        assert flight.llamadas == []                 # no llamó a Google Flights


# --- invariantes ----------------------------------------------------------------


class TestInvariantes:
    async def test_presupuesto_bajo_rechazado(self):
        _, flight, manager = _nuevo()
        salida = await _turno(manager, "pa medellin desde bogota con 20 mil")
        assert "presupuesto" in salida.texto.lower()
        assert flight.llamadas == []

    async def test_fecha_pasada_rechazada(self):
        _, flight, manager = _nuevo()
        await _turno(manager, "desde bogota")  # pregunta destino; siembra origen
        salida = await _turno(manager, "a cartagena con un palo el 5 de enero de 2020")
        assert any(p in salida.texto.lower() for p in ("fecha", "cuando", "cuándo"))
        assert flight.llamadas == []


# --- select_option y rango --------------------------------------------------------


class TestOtrosFlujos:
    async def test_select_option_rebusca(self):
        _, flight, manager = _nuevo()
        await _turno(manager, "desde medellin")           # siembra origen
        await _turno(manager, "a san andres con un palo")  # search -> 2 opciones
        salida = await _turno(manager, "la 2")
        assert len(flight.llamadas) >= 2
        assert "opciones" in salida.texto.lower() or "✈️" in salida.texto

    async def test_rango_usa_buscar_rango(self):
        _, flight, manager = _nuevo()
        await _turno(manager, "desde cali")
        await _turno(manager, "lo más barato en los próximos 3 meses con un palo")
        assert flight.llamadas[0]["meses"] == 3
        assert flight.llamadas[0]["presupuesto_cop"] == 1_000_000


# --- compra (link Google Flights) ---------------------------------------------------


class TestCompra:
    async def test_lo_quiero_da_link_tras_busqueda(self):
        _, flight, manager = _nuevo()
        await _turno(manager, "desde bogota")
        await _turno(manager, "a san andres con un palo")
        salida = await _turno(manager, "lo quiero")
        assert "google.com/travel/flights" in salida.texto
        assert "San Andres" in salida.texto
        assert "$1.950.000" in salida.texto  # precio de la primera opción

    async def test_dame_el_link_de_la_2(self):
        _, flight, manager = _nuevo()
        await _turno(manager, "desde bogota")
        await _turno(manager, "a san andres con un palo")
        salida = await _turno(manager, "dame el link de la 2")
        assert "2027-01-16" in salida.texto  # fecha de la opción 2 (Avianca)

    async def test_comprar_sin_opciones_invita_a_buscar(self):
        _, _, manager = _nuevo()
        salida = await _turno(manager, "dame el link para comprar")
        assert "busque" in salida.texto.lower() or "búsqueda" in salida.texto.lower()

    async def test_comprar_no_llama_a_vuelos(self):
        _, flight, manager = _nuevo()
        await _turno(manager, "desde bogota")
        await _turno(manager, "a san andres con un palo")
        antes = len(flight.llamadas)
        await _turno(manager, "lo quiero")
        assert len(flight.llamadas) == antes  # el link no re-consulta Google


# --- persistencia v2 -----------------------------------------------------------------


class TestPersistencia:
    async def test_estado_v2_guardado_con_historial_recortado(self):
        store, _, manager = _nuevo()
        for frase in (
            "desde bogota", "a san andres con un palo",
            "cambia la fecha a principios de marzo 2027", "holi",
            "otra cosa", "y otra más", "séptima línea",
        ):
            await _turno(manager, frase)
        estado = await store.leer_estado("test", "whatsapp")
        assert isinstance(estado, UserState)
        assert estado.version == 2
        assert len(estado.history_summary) <= UserState.MAX_HISTORIAL
