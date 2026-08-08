from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.config import GEMINI_API_KEY
from app.llm_providers.base import ProveedorLLM

log = logging.getLogger(__name__)

_BASE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# En el free tier el nombre exacto puede variar (2.0-flash / 2.5-flash / lite).
# Se prueban en orden hasta encontrar uno que responda.
_MODELOS_CANDIDATOS = [
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
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
            for modelo in _MODELOS_CANDIDATOS:
                url = f"{_BASE.format(model=modelo)}?key={GEMINI_API_KEY}"
                try:
                    r = await client.post(url, json=body)
                    if r.status_code != 200:
                        log.warning("Gemini %s HTTP %s: %s", modelo, r.status_code, r.text[:200])
                        # modelo inexistente o cuota -> probar siguiente
                        if r.status_code in (404, 400, 429):
                            continue
                        r.raise_for_status()
                    data = r.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
                except (httpx.HTTPStatusError, KeyError, IndexError) as exc:
                    last_error = exc
                    continue
        log.warning("Gemini: todos los modelos fallaron. %s", last_error)
        return None