from fastapi import FastAPI, Request

from app.adapters.telegram import TelegramAdapter
from app.adapters.whatsapp import WhatsAppAdapter
from app.orchestrator import Orquestador
from app.profile_store import ProfileStore

app = FastAPI(title="API Vuelos - Bot multiplataforma")

_store = ProfileStore()
_orquestador = Orquestador(_store)

_telegram = TelegramAdapter()
_whatsapp = WhatsAppAdapter()


@app.get("/")
async def root():
    return {"status": "ok", "canales": ["telegram", "whatsapp"]}


@app.post("/webhook")
async def webhook_telegram(request: Request):
    """Telegram enruta aquí sus mensajes."""
    update = await request.json()
    entrada = _telegram.parse(update)
    if entrada:
        await _orquestador.procesar(entrada, _telegram)
    return {"ok": True}


@app.post("/webhook/whatsapp")
async def webhook_whatsapp(request: Request):
    """WhatsApp Cloud API enruta aquí sus mensajes (cuando se active el canal)."""
    update = await request.json()
    entrada = _whatsapp.parse(update)
    if entrada:
        await _orquestador.procesar(entrada, _whatsapp)
    return {"ok": True, "whatsapp": True}