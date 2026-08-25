"""Fachada del NLU hacia el resto de la app (contrato estable).

Pipeline: Extractor (Gemini JSON o determinista si no hay API key)
-> componer() con normalizadores puros -> NormalizedSlots.

La fachada NO maneja estado de diálogo: eso es trabajo de
app/dialogue_manager.py + SlotManager.
"""
from __future__ import annotations

from app.nlu.composicion import componer
from app.nlu.extractor import Extractor
from app.nlu.schemas import NormalizedSlots

_extractor = Extractor()


async def interpretar(texto: str) -> NormalizedSlots:
    """Texto libre en español coloquial -> NormalizedSlots."""
    raw = await _extractor.extract(texto)
    return componer(raw)
