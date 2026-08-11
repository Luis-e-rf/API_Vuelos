from __future__ import annotations

import logging

import httpx

from app.config import (
    WA_BUSINESS_PHONE,
    WA_GRAPH_URL,
    WA_PHONE_NUMBER_ID,
    WA_TOKEN,
    WA_VERIFY_TOKEN,
)
from app.models import MensajeEntrada, MensajeSalida

log = logging.getLogger(__name__)


class WhatsAppAdapter:
    """Adaptador WhatsApp Cloud API (Meta).

    Parse (webhook entrante):
      - Mensajes de texto, imagen y ubicación del usuario.
      - Ignora statuses (entregado/leído) y mensajes del propio bot.

    Enviar (saliente):
      - Texto plano, o foto si salida.foto_url está seteada (se manda como
        media por link público HTTPS, lo cual sirve para las fotos de
        Wikimedia). Aún no se usan plantillas: asume mensajes dentro de la
        ventana de 24 horas (típico de una demo).

    Configuración (variables de entorno, ver .env.example):
      - WHATSAPP_TOKEN: token permanente de la app de Meta Developers.
      - WHATSAPP_PHONE_NUMBER_ID: ID del número de negocio que envía.
      - WHATSAPP_BUSINESS_PHONE: número verificado que recibe la demo.
      - WHATSAPP_VERIFY_TOKEN: token para verificar el webhook ante Meta.
    """

    canal: str = "whatsapp"

    def verificar(self, hub_mode: str, hub_token: str, hub_challenge: str):
        """Responder la verificación inicial del webhook que hace Meta (GET)."""
        if hub_mode == "subscribe" and hub_token == WA_VERIFY_TOKEN:
            return hub_challenge
        return None

    def parse(self, update: dict) -> MensajeEntrada | None:
        try:
            value = update["entry"][0]["changes"][0]["value"]
            msg = value.get("messages") or [{}]
            msg = msg[0]
            wa_id = msg.get("from")
            if not wa_id:
                return None
            tipo = msg.get("type")
            if tipo == "status":
                return None
            texto = ""
            ubicacion = None
            if tipo == "text":
                texto = msg.get("text", {}).get("body", "")
            elif tipo == "image":
                texto = msg.get("image", {}).get("caption", "")
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

    async def enviar(self, chat_id: str, salida: MensajeSalida) -> bool:
        if not WA_TOKEN or not WA_PHONE_NUMBER_ID:
            log.warning("WhatsApp no configurado (WA_TOKEN o WA_PHONE_NUMBER_ID vacío).")
            return False

        url = f"{WA_GRAPH_URL}/{WA_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WA_TOKEN}",
            "Content-Type": "application/json",
        }
        if salida.foto_url:
            body = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": chat_id,
                "type": "image",
                "image": {"link": salida.foto_url, "caption": salida.texto},
            }
        else:
            body = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": chat_id,
                "type": "text",
                "text": {"body": salida.texto},
            }

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(url, json=body, headers=headers)
            if resp.status_code >= 400:
                log.error(
                    "WhatsApp enviar falló %s: %s",
                    resp.status_code,
                    resp.text[:300],
                )
                return False
            return True
        except httpx.HTTPError as exc:
            log.error("WhatsApp enviar error de red: %s", exc)
            return False

    def accion_invalida(self, chat_id: str, texto: str) -> None:
        """Mensaje cuando el usuario pulsa un botón de opción no disponible.

        WhatsApp no permite mandar botones arbitrarios fuera de plantillas, así
        que las opciones de `MensajeSalida.opciones` se envían como texto. Si
        alguien teclea exactamente una opción, se procesa normal. Este hook se
        deja por si en el futuro se usan listas interactivas (type=list).
        """
        return None
