"""Tests del Extractor NLU (FASE 2).

- Ruta determinista (offline) con casos de vocabulario coloquial.
- Ruta LLM mockeada: JSON válido, JSON inválido (reintento 1 vez) y
  ausencia de respuesta -> fallback determinista.
- Composición pura RawSlots -> NormalizedSlots (multiplicación "por
  persona", merge con base, destino==origen).
"""
from __future__ import annotations

import pytest

from app.nlu import extractor as mod_extractor
from app.nlu.composicion import componer
from app.nlu.extractor import Extractor, _parsear_json
from app.nlu.schemas import NormalizedSlots, RawSlots


@pytest.fixture(autouse=True)
def _sin_llm(monkeypatch):
    """Por defecto todos los tests corren offline; cada test de LLM
    activa la key explícitamente y mockea _gemini_json."""
    monkeypatch.setattr(mod_extractor, "GEMINI_API_KEY", "")


JSON_OK = (
    '{"origen_raw":"Bogotá","destino_raw":"San Andrés",'
    '"presupuesto_raw":"1 millón por persona","pasajeros_raw":"2 personas",'
    '"fecha_raw":"2027","rango_meses_raw":null,"intent_hint":"search"}'
)


# --- Determinista -----------------------------------------------------------


class TestDeterminista:
    async def test_combo_completo(self):
        raw = Extractor.determinista(
            "de Bogotá a San Andrés, 2 personas, 1 millón por persona 2027"
        )
        assert raw.origen_raw == "Bogota"
        assert raw.destino_raw == "San Andres"
        assert raw.presupuesto_raw and raw.pasajeros_raw and raw.fecha_raw
        assert raw.intent_hint == "search"

    async def test_roles_de_x_a_y(self):
        origen, destino = mod_extractor._roles_ciudades("600 mil pa cartagena desde medellin")
        assert (origen, destino) == ("Medellin", "Cartagena")

    async def test_ciudad_sin_marcador_es_destino(self):
        origen, destino = mod_extractor._roles_ciudades("quiero ir pa santa marta")
        assert origen is None and destino == "Santa Marta"

    @pytest.mark.parametrize(("texto", "hint"), [
        ("olvida todo", "reset"),
        ("la 2 por favor", "select_option"),
        ("no, mejor a cartagena", "change"),
        ("holi ¿qué tal?", "chitchat"),
        ("un palo", "search"),
    ])
    async def test_hints(self, texto, hint):
        assert Extractor.determinista(texto).intent_hint == hint

    async def test_vacio(self):
        raw = await Extractor().extract("   ")
        assert raw.intent_hint == "chitchat"
        assert not raw.destino_raw

    async def test_numero_opcion(self):
        assert Extractor.determinista("me voy con la tercera").numero_opcion == 3
        assert Extractor.determinista("opción 4").numero_opcion == 4


# --- LLM mockeado ------------------------------------------------------------


def _activar_llm(monkeypatch, respuestas: list[str | None]):
    """Configura key falsa + stub de _gemini_json que devuelve respuestas."""
    llamadas = []

    async def fake_gemini(prompt, timeout=8.0):
        llamadas.append(prompt)
        return respuestas[min(len(llamadas), len(respuestas)) - 1]

    monkeypatch.setattr(mod_extractor, "GEMINI_API_KEY", "fake")
    monkeypatch.setattr(mod_extractor, "_gemini_json", fake_gemini)
    return llamadas


class TestLLM:
    async def test_json_valido_una_llamada(self, monkeypatch):
        llamadas = _activar_llm(monkeypatch, [JSON_OK])
        raw = await Extractor().extract("de Bogotá a San Andrés")
        assert raw.origen_raw == "Bogotá"          # crudo, SIN normalizar
        assert raw.destino_raw == "San Andrés"
        assert len(llamadas) == 1                  # sin reintentos

    async def test_reintento_una_vez(self, monkeypatch):
        llamadas = _activar_llm(monkeypatch, ["esto no es json", JSON_OK])
        raw = await Extractor().extract("algo")
        assert raw.origen_raw == "Bogotá"
        assert len(llamadas) == 2

    async def test_fallback_tras_dos_fallos(self, monkeypatch):
        llamadas = _activar_llm(monkeypatch, [None, "tampoco {"])
        raw = await Extractor().extract("un palo")
        assert raw.presupuesto_raw == "un palo"    # ruta determinista
        assert len(llamadas) == 2                  # máximo 2 intentos

    async def test_hint_invalido_se_sanea(self, monkeypatch):
        malo = '{"intent_hint":"comprar","destino_raw":"Cartagena"}'
        _activar_llm(monkeypatch, [malo])
        raw = await Extractor().extract("lo quiero")
        assert raw.intent_hint == "search"
        assert raw.destino_raw == "Cartagena"


class TestParsearJson:
    def test_con_fences_markdown(self):
        assert _parsear_json(f"```json\n{JSON_OK}\n```").origen_raw == "Bogotá"

    def test_basura(self):
        assert _parsear_json(None) is None
        assert _parsear_json("") is None
        assert _parsear_json("sin llaves aquí") is None

    def test_campos_desconocidos_ignorados(self):
        raw = _parsear_json('{"accion":"buscar","destino_raw":"Cali","foo":1}')
        assert raw.destino_raw == "Cali"
        assert not hasattr(raw, "accion")


# --- Composición ---------------------------------------------------------------


class TestComponer:
    def test_por_persona_multiplica_pasajeros_del_turno(self):
        slots = componer(RawSlots(presupuesto_raw="1 millón por persona",
                                  pasajeros_raw="2 personas"))
        assert slots.presupuesto_cop == 2_000_000
        assert slots.pasajeros == 2

    def test_por_persona_usa_base_si_no_hay_pax_nuevo(self):
        base = NormalizedSlots(pasajeros=3)
        slots = componer(RawSlots(presupuesto_raw="500k"), base=base)
        # 500k no es "por persona": no se multiplica
        assert slots.presupuesto_cop == 500_000
        assert slots.pasajeros == 3

    def test_merge_base(self):
        base = NormalizedSlots(origen="Bogota", presupuesto_cop=1_000_000)
        slots = componer(RawSlots(destino_raw="san andres"), base=base)
        assert slots.origen == "Bogota"            # conservado
        assert slots.destino == "San Andres"       # nuevo
        assert slots.presupuesto_cop == 1_000_000  # conservado

    def test_destino_igual_origen_anula_origen(self):
        slots = componer(RawSlots(origen_raw="bogota", destino_raw="bogotá"))
        assert slots.destino == "Bogota"
        assert slots.origen is None
