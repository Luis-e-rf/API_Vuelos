"""Contratos Pydantic de la capa NLU (arquitectura contract-first).

RawSlots        -> lo que el extractor devuelve TAL CUAL del texto del
                   usuario: spans crudos, sin normalizar ("1 millón por
                   persona"). El LLM nunca convierte montos ni ciudades.
NormalizedSlots -> resultado de pasar RawSlots por los normalizadores
                   deterministas (app/normalizers/*). Es el único contrato
                   que consumen SlotManager y ActionExecutor.

Desviación documentada del prompt maestro: NormalizedSlots incluye
`intent_hint` y RawSlots incluye `numero_opcion` para que el dataset dorado
pueda verificar comandos ("reset", "chitchat", "la 2") de forma stateless.
`numero_opcion` se llena siempre por vía determinista (nunca la inventa el LLM).
El hint "buy" ("lo quiero", "dame el link") reconecta el flujo de compra
con link a Google Flights que tenía el pipeline legacy.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

IntentHint = Literal["search", "reset", "chitchat", "select_option", "change", "buy"]


class RawSlots(BaseModel):
    """Spans crudos extraídos del texto. El extractor NO normaliza nada."""

    origen_raw: Optional[str] = None        # "Bogotá", "desde Medellín"
    destino_raw: Optional[str] = None       # "San Andrés", "pa cartagena"
    presupuesto_raw: Optional[str] = None   # tal cual: "1 millón por persona"
    pasajeros_raw: Optional[str] = None     # "somos mi esposa y yo"
    fecha_raw: Optional[str] = None         # "principios de enero 2027"
    rango_meses_raw: Optional[str] = None   # "en los próximos 3 meses"
    intent_hint: IntentHint = "search"
    numero_opcion: Optional[int] = Field(default=None, ge=1, le=9)  # "la 2" -> 2


class NormalizedSlots(BaseModel):
    """Slots validados y normalizados, listos para SlotManager."""

    origen: Optional[str] = None             # canónica: "Bogota" (clave de DESTINOS)
    destino: Optional[str] = None
    presupuesto_cop: Optional[int] = Field(default=None, ge=0)  # ya * pasajeros si "por persona"
    pasajeros: int = Field(default=1, ge=1, le=20)
    fecha_iso: Optional[str] = None          # YYYY-MM-DD futura
    rango_meses: Optional[int] = Field(default=None, ge=1, le=12)
    intent_hint: IntentHint = "search"
