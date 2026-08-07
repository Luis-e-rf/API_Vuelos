from __future__ import annotations

from typing import Optional

from app.config import DEEPSEEK_API_KEY
from app.llm_providers.base import ProveedorLLM, _post_openai

_DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


class DeepSeek(ProveedorLLM):
    nombre = "deepseek-chat"

    def configurado(self) -> bool:
        return bool(DEEPSEEK_API_KEY)

    async def generar(self, system: str, prompt: str, timeout: float = 8) -> Optional[str]:
        return await _post_openai(
            _DEEPSEEK_URL, DEEPSEEK_API_KEY, system, prompt, "deepseek-chat", timeout
        )