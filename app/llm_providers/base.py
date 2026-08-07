from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import httpx


class ProveedorLLM(ABC):
    """Interfaz única que todo proveedor (Gemini, Groq, DeepSeek, ...) implementa.

    El llm_router solo conoce estos 3 métodos. Así cambiar de modelo de turno
    (la promo que esté gratis) es crear otro archivo en esta carpeta.
    """

    nombre: str

    @abstractmethod
    def configurado(self) -> bool:
        """True si el proveedor tiene credenciales configuradas."""

    @abstractmethod
    async def generar(self, system: str, prompt: str) -> Optional[str]:
        """Devuelve el texto del modelo o None si no hay respuesta útil."""


async def _post_openai(
    url: str, api_key: str, system: str, prompt: str, model: str, timeout: float = 8
) -> Optional[str]:
    """Helper para proveedores con API compatible OpenAI (Groq, DeepSeek, ...)."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 500,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=body, headers=headers)
        r.raise_for_status()
        data = r.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError):
        return None