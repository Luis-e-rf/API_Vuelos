"""Tests de adapters: multi-message en webhooks y firma HMAC de Meta.

FASE 5: un POST de WhatsApp puede traer varios messages; todos deben
procesarse. Telegram soporta message y edited_message.
"""
from __future__ import annotations

import hashlib
import hmac

from app.adapters.telegram import TelegramAdapter
from app.adapters.whatsapp import WhatsAppAdapter


def _wa_update(*mensajes_texto: str) -> dict:
    return {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [
                        {"from": "573001112233", "type": "text",
                         "text": {"body": t}}
                        for t in mensajes_texto
                    ]
                }
            }]
        }]
    }


class TestWhatsApp:
    def test_multiples_mensajes_en_un_post(self):
        entradas = WhatsAppAdapter().parse_todos(_wa_update("hola", "pa san andres"))
        assert [e.texto for e in entradas] == ["hola", "pa san andres"]
        assert all(e.canal == "whatsapp" for e in entradas)

    def test_solo_statuses_devuelve_vacio(self):
        update = {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.X"}]}}]}]}
        assert WhatsAppAdapter().parse_todos(update) == []

    def test_malformado_no_explota(self):
        assert WhatsAppAdapter().parse_todos({}) == []
        assert WhatsAppAdapter().parse_todos({"entry": []}) == []

    def test_parse_retorna_primero_compat(self):
        assert WhatsAppAdapter().parse(_wa_update("uno", "dos")).texto == "uno"

    def test_firma_hmac_valida_e_invalida(self, monkeypatch):
        secreto = "mi-secreto"
        monkeypatch.setattr("app.adapters.whatsapp.WA_APP_SECRET", secreto)
        adapter = WhatsAppAdapter()
        body = b'{"payload": true}'
        firma = "sha256=" + hmac.new(secreto.encode(), body, hashlib.sha256).hexdigest()
        assert adapter.verificar_firma(body, firma)
        assert not adapter.verificar_firma(body, "sha256=" + "0" * 64)
        assert not adapter.verificar_firma(body, None)

    def test_sin_secreto_acepta_compat(self, monkeypatch):
        monkeypatch.setattr("app.adapters.whatsapp.WA_APP_SECRET", "")
        assert WhatsAppAdapter().verificar_firma(b"x", None)


class TestTelegram:
    def test_message_y_edited_message(self):
        adapter = TelegramAdapter()
        update = {"message": {"chat": {"id": 42}, "text": "hola"}}
        assert adapter.parse_todos(update)[0].texto == "hola"
        editado = {"edited_message": {"chat": {"id": 42}, "text": "hola editado"}}
        assert adapter.parse_todos(editado)[0].texto == "hola editado"

    def test_vacio(self):
        assert TelegramAdapter().parse_todos({}) == []
