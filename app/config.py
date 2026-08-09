import os


def load_env() -> None:
    """Carga variables de entorno desde un archivo .env local (no en producción)."""
    env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


load_env()


TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# WhatsApp Cloud API (solo se usan si activas el canal WhatsApp)
WA_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WA_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
WA_BUSINESS_PHONE = os.environ.get("WHATSAPP_BUSINESS_PHONE", "")
WA_GRAPH_URL = os.environ.get("WHATSAPP_GRAPH_URL", "https://graph.facebook.com/v20.0")

# Upstash Redis (perfil por chat_id)
UPSTASH_REDIS_REST_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_REDIS_REST_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

# LLM: se recorren en orden, cada une tiene su free tier. Vacío = se usa fallback local.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Nombre exacto del modelo a usar (si vacío, se prueban los candidatos en orden).
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")