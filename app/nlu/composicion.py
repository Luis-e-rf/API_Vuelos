"""Composición pura RawSlots -> NormalizedSlots.

Aplica los normalizadores deterministas a los spans crudos y fusiona con
los slots ya confirmados del usuario (`base`). No tiene estado de diálogo:
la política de validación/preguntas vive en app/dialogue/slot_manager.py.
"""
from __future__ import annotations

from app.normalizers import city, date, money, passengers
from app.nlu.schemas import NormalizedSlots, RawSlots


def componer(raw: RawSlots, base: NormalizedSlots | None = None) -> NormalizedSlots:
    """RawSlots (+ estado previo) -> NormalizedSlots fusionados.

    Reglas:
    - Un valor nuevo pisa el base; un slot ausente conserva el base.
    - "por persona" multiplica por pasajeros del MISMO turno (o base).
    - destino == origen anula el origen (se preguntará después).
    """
    b = base or NormalizedSlots()

    monto = money.parse(raw.presupuesto_raw) if raw.presupuesto_raw else money.Monto(None)
    pax_nuevo = (
        passengers.parse(raw.pasajeros_raw) if raw.pasajeros_raw else None
    )
    pasajeros = pax_nuevo if pax_nuevo else b.pasajeros

    presupuesto = monto.valor_cop
    if presupuesto is None:
        presupuesto = b.presupuesto_cop  # turno sin dinero -> conserva base
    elif monto.por_persona:
        presupuesto *= max(1, pasajeros)

    fecha = date.parse(raw.fecha_raw) if raw.fecha_raw else None
    rango = date.rango_meses(raw.rango_meses_raw) if raw.rango_meses_raw else None

    origen = city.normalizar(raw.origen_raw) if raw.origen_raw else b.origen
    destino = city.normalizar(raw.destino_raw) if raw.destino_raw else b.destino
    if origen and destino and origen == destino:
        origen = None  # "de Bogotá a Bogota": el origen real se pregunta luego

    return NormalizedSlots(
        origen=origen,
        destino=destino,
        presupuesto_cop=presupuesto,
        pasajeros=max(1, pasajeros),
        fecha_iso=fecha or b.fecha_iso,
        rango_meses=rango or b.rango_meses,
        intent_hint=raw.intent_hint,
    )
