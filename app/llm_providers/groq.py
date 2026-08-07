from __future__ import annotations

from typing import Optional

from app.config import GROQ_API_KEY
from app.llm_providers.base import ProveedorLLM, _post_openai

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class Groq(ProveedorLLM):
    nombre = "groq/llama-3.3-70b"

    def configurado(self) -> bool:
        return bool(GROQ_API_KEY)

    async def generar(self, system: str, prompt: str, timeout: float = 8) -> Optional[str]:
        return await _post_openai(
            _GROQ_URL, GROQ_API_KEY, system, prompt, "llama-3.3-70b-versatile", timeout
        )