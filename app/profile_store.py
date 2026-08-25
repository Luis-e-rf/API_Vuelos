from __future__ import annotations

import json
import logging
import time
from typing import Optional

import httpx

from app.config import UPSTASH_REDIS_REST_TOKEN, UPSTASH_REDIS_REST_URL
from app.models import Perfil, UserState

log = logging.getLogger(__name__)

_TTL_ESTADO_SEG = 60 * 60 * 24 * 30  # estado v2 expira en 30 días


class ProfileStore:
    """Guarda/lee el perfil de un usuario. Clave = chat_id (canal-prefijada).

    Usa Upstash Redis vía su REST API (free tier). Si no hay credenciales
    configuradas, cae a un dict en memoria para que el desarrollo local y
    las pruebas funcionen sin servicios externos.

    Dos generaciones de esquema:
    - v1 `perfil:*`   -> dataclass Perfil (legacy, rollback del flag).
    - v2 `estado:*`   -> UserState (Pydantic) con TTL y borrado real.
    El valor de v2 viaja en el CUERPO del POST (no en la URL), lo que
    elimina el bug de JSON sin URL-encodear de v1.
    """

    def __init__(
        self,
        url: str = UPSTASH_REDIS_REST_URL,
        token: str = UPSTASH_REDIS_REST_TOKEN,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url
        self.token = token
        self._transport = transport  # inyectable para tests (MockTransport)
        self._cache: dict[str, Perfil | UserState] = {}
        self._redis = bool(url and token)

    def _cliente(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=5, transport=self._transport)

    def _key(self, chat_id: str, canal: str) -> str:
        return f"perfil:{canal}:{chat_id}"

    @staticmethod
    def _key_estado(chat_id: str, canal: str) -> str:
        return f"estado:{canal}:{chat_id}"

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
        """Actualiza campos del perfil con valores diferentes de omisión.

        Nota: Este método tiene un race condition (read-then-write).
        Preferir usar leer() -> modificar -> guardar() en el orquestador.
        """
        perfil = await self.leer(chat_id, canal)
        nuevos: dict = {k: v for k, v in update.items() if v is not None}
        for k, v in nuevos.items():
            setattr(perfil, k, v)
        await self.guardar(perfil, canal)

    # --- esquema v2 (UserState) ------------------------------------------

    async def leer_estado(self, chat_id: str, canal: str = "unknown") -> UserState:
        key = self._key_estado(chat_id, canal)
        if not self._redis:
            return self._cache.get(key) or UserState()
        try:
            async with self._cliente() as client:
                r = await client.get(
                    f"{self.url}/get/{key}",
                    headers={"Authorization": f"Bearer {self.token}"},
                )
                data = r.json()
            if "error" in data:
                log.error("Redis leer_estado rechazado para %s: %s", key, data["error"])
                return UserState()
            if data.get("result"):
                return UserState.from_dict(json.loads(data["result"]))
        except Exception as exc:  # noqa: BLE001 - no romper el flujo del bot
            log.warning("Redis leer_estado falló para %s: %s", key, exc)
        return UserState()

    async def guardar_estado(self, estado: UserState, chat_id: str, canal: str = "unknown") -> None:
        """Persiste el UserState v2 con TTL de 30 días.

        Sintaxis Upstash verificada contra su doc oficial: el body del POST
        se agrega como ÚLTIMO parámetro del comando, así que los modifiers
        (EX) van como QUERY PARAMS: POST /set/{key}?EX={ttl} + body=valor.
        Escribir /set/{key}/ex/{ttl} con body produce
        `SET key ex ttl <json>` -> ERR syntax error (fallo silencioso).
        """
        key = self._key_estado(chat_id, canal)
        if not self._redis:
            self._cache[key] = estado
            return
        try:
            async with self._cliente() as client:
                r = await client.post(
                    f"{self.url}/set/{key}",
                    params={"EX": _TTL_ESTADO_SEG},
                    content=json.dumps(estado.to_dict()),
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "text/plain",
                    },
                )
            data = r.json()
            if "error" in data:
                log.error("Redis guardar_estado rechazado para %s: %s", key, data["error"])
            elif data.get("result") != "OK":
                log.error("Redis guardar_estado respuesta inesperada para %s: %s", key, data)
        except Exception as exc:  # noqa: BLE001
            log.error("Redis guardar_estado falló para %s: %s", key, exc)

    async def borrar(self, chat_id: str, canal: str = "unknown") -> bool:
        """Elimina la clave del estado (reset real, no mutación parcial).

        El comando Redis es DEL (no DELETE): /del/{key}.
        """
        key = self._key_estado(chat_id, canal)
        self._cache.pop(key, None)
        if not self._redis:
            return True
        try:
            async with self._cliente() as client:
                r = await client.get(
                    f"{self.url}/del/{key}",
                    headers={"Authorization": f"Bearer {self.token}"},
                )
            data = r.json()
            if "error" in data:
                log.error("Redis borrar rechazado para %s: %s", key, data["error"])
                return False
            return bool(data.get("result"))
        except Exception as exc:  # noqa: BLE001
            log.error("Redis borrar falló para %s: %s", key, exc)
            return False