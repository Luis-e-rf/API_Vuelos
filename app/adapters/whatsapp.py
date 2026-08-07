from __future__ import annotations

import httpx

from app.config import (
    WA_BUSINESS_PHONE,
    WA_GRAPH_URL,
    WA_PHONE_NUMBER_ID,
    WA_TOKEN,
)
from app.models import MensajeEntrada, MensajeSalida


class WhatsAppAdapter:
    """Adaptador WhatsApp Cloud API (Meta).

    Esqueleto funcional: ya sabe parsear el webhook y enviar texto. La parte
    pendiente (cuando se active el canal) es: verificación de negocio/Meta,
    número, plantillas para mensajes outbound y el flujo 24h.
    """

    canal: str = "whatsapp"

    def parse(self, update: dict) -> MensajeEntrada | None:
        try:
            value = update["entry"][0]["changes"][0]["value"]
            msg = value.get("messages") or [{}]
            msg = msg[0]
            wa_id = msg.get("from")
            tipo = msg.get("type")
            if not wa_id:
                return None
            texto = ""
            ubicacion = None
            if tipo == "text":
                texto = msg.get("text", {}).get("body", "")
            elif tipo == "location":
                loc = msg.get("location", {})
                ubicacion = (loc.get("latitude"), loc.get("longitude"))
            return MensajeEntrada(
                chat_id=wa_id,
                texto=texto,
                canal=self.canal,
                ubicacion=ubicacion,
            )
        except (KeyError, IndexError, TypeError):
            return None

    async def enviar(self, chat_id: str, salida: MensajeSalida) -> None:
        if not WA_TOKEN:
            import logging

            logging.getLogger(__name__).warning("WhatsApp no configurado (WA_TOKEN vacío).")
            return

        url = f"https://graph.facebook.com/v20.0/{WA_PHONE_NUMBER_ID}/messages"
        body = {
            "messaging_product": "whatsapp",
            "to": chat_id,
            "type": "text",
            "text": {"body": salida.texto},
        }
        headers = {
            "Authorization": f"Bearer {WA_TOKEN}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(url, json=body, headers=headers)