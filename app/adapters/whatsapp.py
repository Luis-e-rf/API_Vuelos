from __future__ import annotations

import hashlib
import hmac
import logging

import httpx

from app.config import (
    WA_APP_SECRET,
    WA_GRAPH_URL,
    WA_PHONE_NUMBER_ID,
    WA_TOKEN,
    WA_VERIFY_TOKEN,
)
from app.models import MensajeEntrada, MensajeSalida

log = logging.getLogger(__name__)

_MAX_TEXT_LEN = 4096


class WhatsAppAdapter:
    """Adaptador WhatsApp Cloud API (Meta).

    Parse (webhook entrante):
      - Verifica firma HMAC-SHA256 (x-hub-signature-256).
      - Mensajes de texto, imagen y ubicación del usuario.
      - Ignora statuses (entregado/leído) y mensajes del propio bot.

    Enviar (saliente):
      - Texto plano, o foto si salida.foto_url está seteada (se manda como
        media por link público HTTPS, lo cual sirve para las fotos de
        Wikimedia). Aún no se usan plantillas: asume mensajes dentro de la
        ventana de 24 horas (típico de una demo).
      - Respeta límite de 4096 caracteres de WhatsApp.
    """

    canal: str = "whatsapp"

    def verificar(self, hub_mode: str, hub_token: str, hub_challenge: str):
        """Responder la verificación inicial del webhook que hace Meta (GET)."""
        if hub_mode == "subscribe" and hub_token == WA_VERIFY_TOKEN:
            return hub_challenge
        return None

    def verificar_firma(self, body: bytes, signature: str | None) -> bool:
        """Verifica la firma HMAC-SHA256 del webhook de WhatsApp.

        Meta firma cada POST con x-hub-signature-256 = "sha256=<hex>".
        Si WA_APP_SECRET no está configurado, acepta todo (backward compat).
        """
        if not WA_APP_SECRET:
            return True
        if not signature:
            return False
        expected = "sha256=" + hmac.new(
            WA_APP_SECRET.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

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

    async def enviar(self, chat_id: str, salida: MensajeSalida) -> None:
        if not WA_TOKEN or not WA_PHONE_NUMBER_ID:
            log.warning("WhatsApp no configurado (WA_TOKEN o WA_PHONE_NUMBER_ID vacío).")
            return

        url = f"{WA_GRAPH_URL}/{WA_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WA_TOKEN}",
            "Content-Type": "application/json",
        }

        texto = salida.texto[:_MAX_TEXT_LEN]

        if salida.foto_url:
            body = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": chat_id,
                "type": "image",
                "image": {"link": salida.foto_url, "caption": texto},
            }
        else:
            body = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": chat_id,
                "type": "text",
                "text": {"body": texto},
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
        except httpx.HTTPError as exc:
            log.error("WhatsApp enviar error de red: %s", exc)
