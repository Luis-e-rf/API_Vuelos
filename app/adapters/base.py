from __future__ import annotations

from typing import Protocol

from app.models import MensajeEntrada, MensajeSalida


class CanalAdapter(Protocol):
    """Contrato que todo canal (Telegram, WhatsApp, ...) debe cumplir.

    Un adapter traduce el payload crudo del canal a MensajeEntrada y el
    MensajeSalida de vuelta al formato del canal. La lógica de negocio nunca
    toca el payload crudo. `parse_todos` soporta webhooks con varios
    mensajes en un solo POST (WhatsApp los agrupa).
    """

    canal: str

    def parse(self, update: dict) -> MensajeEntrada | None: ...

    def parse_todos(self, update: dict) -> list[MensajeEntrada]: ...

    async def enviar(self, chat_id: str, salida: MensajeSalida) -> None: ...
