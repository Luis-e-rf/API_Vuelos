from __future__ import annotations

import json
import logging
import time
from typing import Optional

import httpx

from app.config import UPSTASH_REDIS_REST_TOKEN, UPSTASH_REDIS_REST_URL
from app.models import Perfil

log = logging.getLogger(__name__)


class ProfileStore:
    """Guarda/lee el perfil de un usuario. Clave = chat_id (canal-prefijado).

    Usa Upstash Redis vía su REST API (free tier). Si no hay credenciales
    configuradas, cae a un dict en memoria para que el desarrollo local y
    las pruebas funcionen sin servicios externos.
    """

    def __init__(
        self,
        url: str = UPSTASH_REDIS_REST_URL,
        token: str = UPSTASH_REDIS_REST_TOKEN,
    ) -> None:
        self.url = url
        self.token = token
        self._cache: dict[str, Perfil] = {}
        self._redis = bool(url and token)

    def _key(self, chat_id: str, canal: str) -> str:
        return f"perfil:{canal}:{chat_id}"

    async def leer(self, chat_id: str, canal: str = "unknown") -> Perfil:
        key = self._key(chat_id, canal)
        if not self._redis:
            return self._cache.get(key, Perfil(chat_id=chat_id))
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(
                    f"{self.url}/get/{key}",
                    headers={"Authorization": f"Bearer {self.token}"},
                )
                data = r.json()
            if data.get("result"):
                return Perfil.from_dict(json.loads(data["result"]))
        except Exception as exc:  # noqa: BLE001 - no romper el flujo del bot
            log.warning("Redis leer falló para %s: %s", key, exc)
        return Perfil(chat_id=chat_id)

    async def guardar(self, perfil: Perfil, canal: str = "unknown") -> None:
        key = self._key(perfil.chat_id, canal)
        if not self._redis:
            self._cache[key] = perfil
            return
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(
                    f"{self.url}/set/{key}/{json.dumps(perfil.to_dict())}",
                    headers={"Authorization": f"Bearer {self.token}"},
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("Redis guardar falló para %s: %s", key, exc)

    async def actualizar(self, update: dict, chat_id: str, canal: str) -> None:
        """Actualiza campos del perfil con valores diferentes de omisión."""
        perfil = await self.leer(chat_id, canal)
        nuevos: dict = {k: v for k, v in update if v is not None}
        for k, v in nuevos.items():
            setattr(perfil, k, v)
        await self.guardar(perfil, canal)