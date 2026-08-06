from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import TELEGRAM_API, TELEGRAM_BOT_TOKEN

app = FastAPI(title="API Vuelos - Bot Telegram")

HELP_TEXT = (
    "✈️ Hola, soy tu asistente de vuelos (fase de prueba).\n\n"
    "Por ahora solo entiendo estos comandos:\n"
    "/start - empezar\n"
    "/help - esta ayuda\n\n"
    "Pronto integraré búsqueda de vuelos."
)


async def send_message(chat_id: int, text: str) -> None:
    import httpx

    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )


async def handle_update(update: dict) -> None:
    message = update.get("message")
    if not message:
        return
    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()
    if not text:
        return

    if text == "/start":
        reply = "¡Hola! 👋 Te ayudo a buscar vuelos. Escribe /help para ver qué sé hacer."
    elif text == "/help":
        reply = HELP_TEXT
    else:
        reply = f"Todavía no entiendo eso 😅. Prueba con /help."

    await send_message(chat_id, reply)


@app.get("/")
async def root():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request):
    update = await request.json()
    await handle_update(update)
    return {"ok": True}