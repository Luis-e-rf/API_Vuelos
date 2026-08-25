"""SlotManager: normaliza, valida invariantes y decide la próxima acción.

Invariantes (análisis técnico §2.6):
- presupuesto_cop > 50_000 (si vino y es muy bajo se pregunta, no se adivina)
- destino != origen (composición ya anula el origen duplicado)
- fecha >= hoy (fecha explícita pasada se rechaza)

La decisión NO muta estado: retorna una `Decision` inmutable que el
DialogueManager aplica. El historial NUNCA entra al NLU.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Literal

from app.nlu.composicion import componer
from app.nlu.schemas import NormalizedSlots, RawSlots

Accion = Literal[
    "search", "search_rango", "ask_slot", "chitchat", "select_option", "reset"
]

MIN_PRESUPUESTO_COP = 50_000

PREGUNTAS: dict[str, str] = {
    "destino": "¿Hacia qué ciudad o destino quieres ir? ✈️",
    "presupuesto": "¿Con cuánto presupuesto cuentas? (ej: 500 mil o un palo)",
    "origen": "¿Desde qué ciudad sales?",
    "fecha": "¿Para qué fecha quieres volar? (ej: principios de marzo de 2027)",
}


@dataclass(frozen=True)
class Decision:
    """Resultado de evaluar un turno. Inmutable a propósito."""

    accion: Accion
    slots: NormalizedSlots
    slot_faltante: str | None = None
    numero_opcion: int | None = None
    motivo: str = field(default="")


def _sin_huecos(slots: NormalizedSlots) -> bool:
    return all(
        v is None for v in (
            slots.origen, slots.destino, slots.presupuesto_cop,
            slots.fecha_iso, slots.rango_meses,
        )
    ) and slots.pasajeros == 1


class SlotManager:

    def decidir(self, estado_slots: NormalizedSlots, raw: RawSlots,
                hoy: datetime.date | None = None) -> Decision:
        """Evalúa el turno: fusiona slots y decide la siguiente acción."""
        hoy = hoy or datetime.date.today()
        merged = componer(raw, estado_slots)
        hint = raw.intent_hint

        if hint == "reset":
            return Decision("reset", merged)

        if hint == "select_option" and raw.numero_opcion:
            return Decision("select_option", merged, numero_opcion=raw.numero_opcion)

        if hint == "chitchat" and merged.model_dump(exclude={"intent_hint"}) == \
                estado_slots.model_dump(exclude={"intent_hint"}):
            return Decision("chitchat", merged)

        # --- invariantes -------------------------------------------------
        if merged.presupuesto_cop is not None and merged.presupuesto_cop <= MIN_PRESUPUESTO_COP:
            return Decision(
                "ask_slot", merged, slot_faltante="presupuesto",
                motivo=f"presupuesto {merged.presupuesto_cop} <= {MIN_PRESUPUESTO_COP}",
            )
        if merged.fecha_iso and datetime.date.fromisoformat(merged.fecha_iso) <= hoy:
            return Decision("ask_slot", merged, slot_faltante="fecha",
                            motivo=f"fecha {merged.fecha_iso} en el pasado")

        # --- completitud para buscar --------------------------------------
        if merged.destino is None and not merged.rango_meses:
            return Decision("ask_slot", merged, slot_faltante="destino")
        if merged.presupuesto_cop is None:
            return Decision("ask_slot", merged, slot_faltante="presupuesto")
        if merged.origen is None:
            return Decision("ask_slot", merged, slot_faltante="origen")

        if merged.rango_meses:
            return Decision("search_rango", merged)
        return Decision("search", merged)
