"""Fachada del NLU hacia el resto de la app (contrato estable entre fases).

FASE 0 - TRANSITORIO (patrón Strangler Fig): delega en la heurística legacy
de app/intents.py para que el dataset dorado corra contra código real y
demuestre los fallos actuales. Las fases 2-3 reemplazan este cuerpo por
Extractor LLM JSON + normalizadores deterministas, SIN cambiar la firma de
`interpretar()`.
"""
from __future__ import annotations

from app.intents import _heuristica
from app.nlu.schemas import IntentHint, NormalizedSlots

# Tasas de conversión a COP (misma convención que el legacy; se centralizan
# en FASE 4 junto con formatter.py).
_TASAS_COP = {"USD": 4000, "EUR": 4400}

_HINT_POR_ACCION: dict[str, IntentHint] = {
    "olvidar_todo": "reset",
    "saludo": "chitchat",
    "ayuda": "chitchat",
    "conversacion": "chitchat",
    "actualizar_perfil": "change",
    "elegir_opcion": "select_option",
}


def _a_cop(monto: int, moneda: str | None) -> int:
    """Convierte USD/EUR a COP con las tasas fijas del legacy."""
    tasa = _TASAS_COP.get((moneda or "").upper())
    return round(monto * tasa) if tasa else monto


async def interpretar(texto: str) -> NormalizedSlots:
    """Texto libre en español coloquial -> NormalizedSlots.

    Async desde hoy para no romper llamadores cuando llegue el Extractor LLM.
    """
    intent = _heuristica(texto.strip(), recientes=[])
    return NormalizedSlots(
        origen=intent.origen,
        destino=intent.destino,
        presupuesto_cop=(
            _a_cop(intent.presupuesto, intent.moneda) if intent.presupuesto else None
        ),
        pasajeros=max(1, intent.pasajeros or 1),
        fecha_iso=intent.fecha,
        rango_meses=intent.rango_meses,
        intent_hint=_HINT_POR_ACCION.get(intent.accion, "search"),
    )
