from __future__ import annotations

import logging
from typing import Optional

from app.config import GROQ_API_KEY
from app.llm_providers.base import ProveedorLLM, _post_openai

log = logging.getLogger(__name__)

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Modelos del plan Developer (2026, verificado en dashboard):
# llama-3.3-70b-versatile pasó a Enterprise (404 para cuentas free).
# gpt-oss sí está en free tier (250K TPM / 1K RPM). 20b primero: más rápido
# y barato, suficiente para chitchat; 120b de respaldo si 20b desaparece.
_MODELOS = ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]


class Groq(ProveedorLLM):
    nombre = "groq/gpt-oss"

    def configurado(self) -> bool:
        return bool(GROQ_API_KEY)

    async def generar(
        self, system: str, prompt: str, historial: Optional[list[dict]] = None, timeout: float = 8
    ) -> Optional[str]:
        for modelo in _MODELOS:
            try:
                respuesta = await _post_openai(
                    _GROQ_URL, GROQ_API_KEY, system, prompt, modelo,
                    historial=historial, timeout=timeout,
                )
            except Exception as exc:  # noqa: BLE001 - fallo de un candidato no mata el resto
                # 404 (modelo retirado) / 429 (cuota) / red: siguiente candidato
                log.warning("Groq %s no disponible: %s -> siguiente", modelo, exc)
                continue
            if respuesta:
                return respuesta
        return None
