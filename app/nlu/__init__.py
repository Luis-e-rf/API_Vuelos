"""Capa NLU: extracción de slots con contrato Pydantic.

Arquitectura objetivo:
    texto -> Extractor (LLM JSON estricto o determinista) -> RawSlots
          -> Normalizers (app/normalizers/*) -> NormalizedSlots
"""
