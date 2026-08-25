"""Dataset dorado NLU: 20 frases coloquiales reales (FASE 0, sin LLM).

Estado esperado por fase:
- FASE 0: la mayoría FALLA contra el pipeline legacy (fachada delega en
  _heuristica). Es la línea base que justifica el refactor.
- FASE 2-3: deben pasar 20/20 SIN llamar a ningún LLM (ruta determinista
  con normalizadores) y con Extractor mockeado.

NOTA caso `pareja_colloquial`: el prompt maestro decía
"somos mi esposa y yo" -> 3, pero esposa + yo son 2 personas. Se codifica 2
(semántica correcta); confirmar con el autor si quería otro enunciado
(p. ej. "mi esposa, mi hijo y yo").
"""
from __future__ import annotations

import asyncio

import pytest

from app.nlu.api import interpretar


@pytest.fixture(autouse=True)
def _sin_llm(monkeypatch):
    """El dataset dorado SIEMPRE corre offline (ruta determinista)."""
    monkeypatch.setattr("app.nlu.extractor.GEMINI_API_KEY", "")

# (id, texto, campos esperados de NormalizedSlots)
CASOS = [
    ("combo_completo", "de Bogotá a San Andrés, 2 personas, 1 millón por persona 2027",
     dict(origen="Bogota", destino="San Andres", presupuesto_cop=2_000_000,
          pasajeros=2, fecha_iso="2027-01-15")),
    ("pareja_colloquial", "somos mi esposa y yo", dict(pasajeros=2)),
    ("un_palo", "un palo", dict(presupuesto_cop=1_000_000)),
    ("pa_destino", "pa san andres", dict(destino="San Andres")),
    ("reset", "olvida todo", dict(intent_hint="reset")),
    ("monto_y_ruta", "600 mil pa cartagena desde medellin",
     dict(origen="Medellin", destino="Cartagena", presupuesto_cop=600_000)),
    ("destino_con_k", "quiero ir pa santa marta con 500k",
     dict(destino="Santa Marta", presupuesto_cop=500_000)),
    ("usd_a_cop", "tengo 300 dólares", dict(presupuesto_cop=1_200_000)),
    ("por_persona_solo", "1 millón por persona", dict(presupuesto_cop=1_000_000)),
    ("principios_mes", "principios de enero 2027", dict(fecha_iso="2027-01-05")),
    ("mediados_mes", "mediados de marzo de 2027", dict(fecha_iso="2027-03-15")),
    ("fin_de_mes", "final de abril 2027", dict(fecha_iso="2027-04-25")),
    ("dia_exacto", "el 15 de diciembre de 2027", dict(fecha_iso="2027-12-15")),
    ("rango_meses", "lo más barato en los próximos 3 meses", dict(rango_meses=3)),
    ("cuatro_personas", "somos 4", dict(pasajeros=4)),
    ("adultos_y_nino", "van a ser 2 adultos y un niño", dict(pasajeros=3)),
    ("dos_millones_palabra", "dos millones", dict(presupuesto_cop=2_000_000)),
    ("separador_miles", "con 1.500.000 puedo?", dict(presupuesto_cop=1_500_000)),
    ("ruta_pa", "de cali pa barranquilla", dict(origen="Cali", destino="Barranquilla")),
    ("chitchat", "holi ¿qué tal?", dict(intent_hint="chitchat")),
]


@pytest.mark.parametrize(("_id", "texto", "esperado"), CASOS, ids=[c[0] for c in CASOS])
def test_golden(_id: str, texto: str, esperado: dict) -> None:
    slots = asyncio.run(interpretar(texto))
    reales = slots.model_dump()
    for campo, valor in esperado.items():
        assert reales[campo] == valor, (
            f"[{_id}] {campo}: esperado={valor!r} obtenido={reales[campo]!r} "
            f"(slots completos: {slots})"
        )
