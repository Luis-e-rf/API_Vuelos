from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

import logging
import time
from collections import defaultdict

from app import config
from app.adapters.telegram import TelegramAdapter
from app.adapters.whatsapp import WhatsAppAdapter
from app.dialogue_manager import DialogueManager
from app.profile_store import ProfileStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", force=True)
log = logging.getLogger("webhook")

app = FastAPI(title="API Vuelos - Bot multiplataforma")

_store = ProfileStore()
# Pipeline NLU v2 (Extractor Gemini JSON + normalizers + SlotManager).
# Rollback a la arquitectura legacy: git revert de 2a9216d..c85abad.
_bot = DialogueManager(_store)

_telegram = TelegramAdapter()
_whatsapp = WhatsAppAdapter()

# Rate limiting simple por IP (últimos N requests por ventana)
_rate_limits: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW = 60  # segundos
_RATE_MAX = 30     # máximo requests por ventana


def _rate_limit(ip: str) -> bool:
    """Retorna True si el request está dentro del límite."""
    now = time.time()
    window = _rate_limits[ip]
    window[:] = [t for t in window if now - t < _RATE_WINDOW]
    if len(window) >= _RATE_MAX:
        return False
    window.append(now)
    return True


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
        await _bot.procesar(entrada, _telegram)
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

    # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    if not _rate_limit(client_ip):
        log.warning("Rate limit excedido para %s", client_ip)
        return {"ok": True}

    # Verificar firma HMAC-SHA256
    signature = request.headers.get("x-hub-signature-256")
    if not _whatsapp.verificar_firma(raw, signature):
        log.warning("Firma webhook inválida de %s", client_ip)
        return PlainTextResponse("Firma inválida", status_code=403)

    log.info("WA webhook POST recibido: %s", raw[:500])
    update = await request.json()
    entradas = _whatsapp.parse_todos(update)
    log.info("WA parse -> %s mensaje(s)", len(entradas))
    for entrada in entradas:
        await _bot.procesar(entrada, _whatsapp)
    return {"ok": True, "whatsapp": True}
