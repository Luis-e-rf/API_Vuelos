from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

import logging

from app import config
from app.adapters.telegram import TelegramAdapter
from app.adapters.whatsapp import WhatsAppAdapter
from app.orchestrator import Orquestador
from app.profile_store import ProfileStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", force=True)
log = logging.getLogger("webhook")

app = FastAPI(title="API Vuelos - Bot multiplataforma")

_store = ProfileStore()
_orquestador = Orquestador(_store)

_telegram = TelegramAdapter()
_whatsapp = WhatsAppAdapter()


@app.get("/")
async def root():
    return {"status": "ok", "canales": ["telegram", "whatsapp"]}


@app.get("/diagnostico")
async def diagnostico():
    """Dice qué credenciales llegaron a la app (sin exponer sus valores)."""
    return {
        "telegram": bool(config.TELEGRAM_BOT_TOKEN),
        "whatsapp": bool(config.WA_TOKEN and config.WA_PHONE_NUMBER_ID),
        "gemini": bool(config.GEMINI_API_KEY),
        "groq": bool(config.GROQ_API_KEY),
        "deepseek": bool(config.DEEPSEEK_API_KEY),
        "upstash": bool(config.UPSTASH_REDIS_REST_URL),
    }


@app.post("/webhook")
async def webhook_telegram(request: Request):
    """Telegram enruta aquí sus mensajes."""
    update = await request.json()
    entrada = _telegram.parse(update)
    if entrada:
        await _orquestador.procesar(entrada, _telegram)
    return {"ok": True}


@app.get("/webhook/whatsapp")
async def verificar_whatsapp(request: Request):
    """Meta llama este GET para verificar que el webhook es nuestro.

    Meta envía query params con punto: hub.mode, hub.verify_token, hub.challenge.
    Se leen crudos con request.query_params y respondemos el challenge en texto plano.
    """
    q = request.query_params
    respuesta = _whatsapp.verificar(
        q.get("hub.mode", ""),
        q.get("hub.verify_token", ""),
        q.get("hub.challenge", ""),
    )
    if respuesta:
        return PlainTextResponse(respuesta)
    return PlainTextResponse("Verificación fallida", status_code=403)


@app.post("/webhook/whatsapp")
async def webhook_whatsapp(request: Request):
    """WhatsApp Cloud API enruta aquí sus mensajes."""
    raw = await request.body()
    log.info("WA webhook POST recibido: %s", raw[:500])
    update = await request.json()
    entrada = _whatsapp.parse(update)
    log.info("WA parse -> %s", entrada)
    if entrada:
        await _orquestador.procesar(entrada, _whatsapp)
    return {"ok": True, "whatsapp": True}