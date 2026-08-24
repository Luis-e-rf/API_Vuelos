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
        async with httpx.AsyncClient(timeout=15) as client:
            if salida.foto_url:
                payload = {
                    "chat_id": chat_id,
                    "photo": salida.foto_url,
                    "caption": salida.texto,
                    "parse_mode": "Markdown",
                }
                if salida.opciones:
                    payload["reply_markup"] = self._keyboard(salida.opciones)
                await client.post(f"{TELEGRAM_API}/sendPhoto", json=payload)
                return
            payload: dict = {
                "chat_id": chat_id,
                "text": salida.texto,
                "parse_mode": "Markdown",
            }
            if salida.opciones:
                payload["reply_markup"] = self._keyboard(salida.opciones)
            await client.post(f"{TELEGRAM_API}/sendMessage", json=payload)

    def _keyboard(self, opciones: list[str]) -> dict:
        return {
            "keyboard": [[o] for o in opciones],
            "one_time_keyboard": True,
        }
