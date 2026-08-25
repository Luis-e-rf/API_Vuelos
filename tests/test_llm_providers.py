"""Tests de proveedores LLM (mockeados, sin red).

Groq: verificación de candidatos gpt-oss (llama-3.3-70b pasó a Enterprise
y da 404 en cuentas free) y degradación silenciosa a None.
"""
from __future__ import annotations

import pytest

from app.llm_providers import groq as mod_groq
from app.llm_providers.groq import Groq


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(mod_groq, "GROQ_API_KEY", "fake")


async def test_primer_modelo_404_usa_segundo(monkeypatch):
    probados = []

    async def fake_post(url, key, system, prompt, modelo, historial=None, timeout=8):
        probados.append(modelo)
        if modelo == "openai/gpt-oss-20b":
            raise RuntimeError("404 model retired")
        return "respuesta del 120b"

    monkeypatch.setattr(mod_groq, "_post_openai", fake_post)
    salida = await Groq().generar("sys", "hola")
    assert salida == "respuesta del 120b"
    assert probados == ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]


async def test_todos_fallan_devuelve_none(monkeypatch):
    async def fake_post(*a, **k):
        raise RuntimeError("429 quota")

    monkeypatch.setattr(mod_groq, "_post_openai", fake_post)
    assert await Groq().generar("sys", "hola") is None


def test_configurado_depende_de_key(monkeypatch):
    monkeypatch.setattr(mod_groq, "GROQ_API_KEY", "")
    assert Groq().configurado() is False
