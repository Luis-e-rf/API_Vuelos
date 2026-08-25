"""Normalizadores deterministas para español coloquial colombiano.

Cada módulo es una función pura y testeable: nada de gramática, solo
vocabulario y reglas explícitas. El LLM (app/nlu/extractor.py, FASE 2)
extrae spans crudos; estos módulos los convierten a valores canónicos.
"""
