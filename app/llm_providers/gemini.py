from __future__ import annotations

from typing import Optional

import httpx

from app.config import GEMINI_API_KEY
from app.llm_providers.base import ProveedorLLM

# Endpoints del Gemini (free tier de Google AI Studio)
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"


class Gemini(ProveedorLLM):
    nombre = "gemini-2.0-flash"

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
        url = f"{_GEMINI_URL}?key={GEMINI_API_KEY}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, json=body)
            r.raise_for_status()
            data = r.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError, AttributeError):
            return None