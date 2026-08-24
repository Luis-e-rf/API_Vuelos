from __future__ import annotations

import logging
from typing import Optional

from app.llm_providers import DeepSeek, Gemini, Groq, ProveedorLLM

log = logging.getLogger(__name__)

# Orden preferido. Cada proveedor se intenta solo si está configurado y va con
# timeout corto; si falla/timeout/agota cuota pasa automáticamente al siguiente.
PROVEEDORES: list[ProveedorLLM] = [
    Gemini(),
    Groq(),
    DeepSeek(),
]


def proveedores_actuales() -> list[ProveedorLLM]:
    return PROVEEDORES


async def generar(
    system: str,
    prompt: str,
    historial: Optional[list[dict]] = None,
    proveedores: Optional[list[ProveedorLLM]] = None,
    timeout: float = 8,
) -> tuple[Optional[str], Optional[str]]:
    """Llama los proveedores en orden y devuelve (texto, nombre_proveedor).

    Devuelve (None, None) si ninguno responde (p.ej. sin keys configuradas),
    para que el llamador use su fallback local.
    """
    lista = proveedores if proveedores is not None else PROVEEDORES
    for provider in lista:
        if not provider.configurado():
            continue
        try:
            respuesta = await provider.generar(system, prompt, historial=historial, timeout=timeout)
            if respuesta:
                return respuesta, provider.nombre
        except Exception as exc:  # noqa: BLE001 - cualquier fallo pasa al siguiente
            log.warning("LLM %s falló: %s", provider.nombre, exc)
    return None, None