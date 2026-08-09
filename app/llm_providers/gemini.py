from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.config import GEMINI_API_KEY, GEMINI_MODEL
from app.llm_providers.base import ProveedorLLM

log = logging.getLogger(__name__)

_BASE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Modelo gratuito verificado (2026): gemini-flash-lite-latest.
# Los alias 2.5 ya no funcionan para cuentas nuevas (404) y los 2.0 no tienen free tier (429).
_MODELOS_CANDIDATOS = [
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-2.0-flash-lite-001",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]


class Gemini(ProveedorLLM):
    nombre = "gemini-flash"

    def configurado(self) -> bool:
        return bool(GEMINI_API_KEY)

    async def generar(self, system: str, prompt: str, timeout: float = 8) -> Optional[str]:
        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"{system}\n\n{prompt}"}],
                }
            ],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 500},
        }
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=timeout) as client:
            candidatos = [GEMINI_MODEL] if GEMINI_MODEL else _MODELOS_CANDIDATOS
            for modelo in candidatos:
                url = f"{_BASE.format(model=modelo)}?key={GEMINI_API_KEY}"
                try:
                    r = await client.post(url, json=body)
                    if r.status_code == 200:
                        data = r.json()
                        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    if r.status_code == 429:
                        # Sin free tier para este modelo: probar otro, no desistir.
                        log.warning("Gemini %s sin free tier (429) -> siguiente", modelo)
                        continue
                    if r.status_code == 404 and "no longer available" in r.text.lower():
                        log.warning("Gemini %s no disponible para cuentas nuevas -> siguiente", modelo)
                        continue
                    log.warning("Gemini %s HTTP %s: %s", modelo, r.status_code, r.text[:120])
                except (httpx.HTTPStatusError, KeyError, IndexError) as exc:
                    last_error = exc
                    continue
        log.warning("Gemini: ninguno de los %s modelo(s) respondió. %s", len(candidatos), last_error)
        return None