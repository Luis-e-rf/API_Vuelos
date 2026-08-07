from __future__ import annotations

import httpx

from app.config import TELEGRAM_API
from app.models import MensajeEntrada, MensajeSalida


class TelegramAdapter:
    canal: str = "telegram"

    def parse(self, update: dict) -> MensajeEntrada | None:
        message = update.get("message") or {}
        chat_id = str(message.get("chat", {}).get("id", ""))
        texto = (message.get("text") or "").strip()
        if not chat_id:
            return None
        return MensajeEntrada(
            chat_id=chat_id,
            texto=texto,
            canal=self.canal,
        )

    async def enviar(self, chat_id: str, salida: MensajeSalida) -> None:
        payload: dict = {"chat_id": chat_id, "text": salida.texto}
        if salida.opciones:
            payload["reply_markup"] = {
                "keyboard": [[o] for o in salida.opciones],
                "one_time_keyboard": True,
            }
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(f"{TELEGRAM_API}/sendMessage", json=payload)