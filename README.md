# API Vuelos — Bot de vuelos multiplataforma

Bot conversacional que busca vuelos baratos en Colombia, habla en lenguaje
natural (español) y responde por **Telegram** y **WhatsApp** desde el mismo
núcleo. Un solo backend, canales intercambiables.

## Cómo funciona

```
telegram/WhatsApp ──> adaptador (parse) ──> Orquestador ──> intérprete de intención
                                                    │
                                                    ▼
                              FlightClient (Google Flights o simulador)
                                    │
                                    ▼
                              Formatter + foto Wikimedia + link de compra
                                    │
                                    ▼
        adaptador (enviar: texto, foto y link) ──> usuario
```

- **Canal agnóstico:** `MensajeEntrada`/`MensajeSalida` son la única interfaz
  entre los adaptadores y el orquestador. Para agregar un canal, crea un
  adaptador con `parse(update) -> MensajeEntrada` y `enviar(chat_id, salida)`.
- **Intención:** primero intenta el LLM (`llm_router` con Gemini/Groq/DeepSeek
  en cascada, gratis) y, si no responde, cae a una heurística local. El prompt
  del LLM y la heurística están en `app/intents.py`.
- **Vuelos:** usa `fast-flights` (Google Flights, gratis). Si la red falla o el
  destino no tiene código IATA (islas del Pacífico, pueblos sin aeropuerto),
  cae a un simulador interno de precios realistas.
- **Persistencia:** perfil por chat (presupuesto, pasajeros, viajes guardados)
  en Upstash Redis vía REST; si no hay credenciales, memoria local.

## Requisitos

- Python 3.10+
- Dependencias: `pip install -r requirements.txt` (o `uv sync` si usas uv)
- `.env` a partir de `.env.example`

## Puesta en marcha

```bash
cp .env.example .env        # rellena TELEGRAM_BOT_TOKEN al menos
uv run python run.py        # o: python run.py
```

Apunta el webhook de tu bot de Telegram a:

```
https://<tu-dominio>/webhook
```

Con Render el deploy es automático desde `main` (`render.yaml`).

## Canal WhatsApp (guía paso a paso)

El adaptador (`app/adapters/whatsapp.py`) ya está listo y el webhook
(`GET`/`POST /webhook/whatsapp`) activo. Falta tu app de Meta. Pasos:

1. **Crea la app** en https://developers.facebook.com → "My Apps" → "Create
   App" → tipo **Business**. Luego agrega el producto **WhatsApp**.
2. **Consigue un número:** en WhatsApp → "API Setup" usa el número de prueba
   (gratis) o agrega tu número real. Anota el **Phone number ID**.
3. **Crea un token permanente:** en "API Setup" → "Generate token". Ese es el
   `WHATSAPP_TOKEN`. Asegúrate de que dure más de 24 h (marca "Permanent").
4. **Añade al número de prueba como usuario de test:** para que la demo pueda
   escribirle al bot. Ese número va en `WHATSAPP_BUSINESS_PHONE`.
5. **Configura en el servidor** (Render → Environment):
   ```
   WHATSAPP_TOKEN=...
   WHATSAPP_PHONE_NUMBER_ID=...
   WHATSAPP_BUSINESS_PHONE=57...   # tu celular, con código de país
   WHATSAPP_VERIFY_TOKEN=vuelos-demo-2026  # cualquier secreto que elijas
   ```
6. **Registra el webhook en Meta:** WhatsApp → "Configuration" → "Edit" →
   Callback URL: `https://<tu-dominio>/webhook/whatsapp` y Verify token:
   `vuelos-demo-2026` (el mismo de arriba). Meta hará un GET y el bot responde
   el challenge automáticamente. En "Webhook fields" marca **messages**.
7. **Prueba:** desde tu celular (el `WHATSAPP_BUSINESS_PHONE`), escribe al
   número de prueba: *"tengo 2 millones, busca a san andres"*.

Notas de la demo:
- Los mensajes salientes se envían dentro de la **ventana de 24 h** (no se usan
  plantillas). Suficiente para una demo en vivo.
- Las fotos de destinos se envían por link público HTTPS (Wikimedia).

### Compra de un vuelo

Cuando el bot muestra opciones y el usuario dice **"lo quiero"** (o "comprar",
"dame el link"), responde con un enlace directo a **Google Flights** para ese
origen → destino → fecha. No hay pago dentro del bot; la compra se hace ahí.
Si el destino no tiene IATA (ej. una isla sin aeropuerto), el bot lo dice y no
muestra link.

## Variables de entorno

| Variable | Uso |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot de Telegram (BotFather) |
| `WHATSAPP_TOKEN` | Token permanente de la app de Meta |
| `WHATSAPP_PHONE_NUMBER_ID` | Número de negocio que responde |
| `WHATSAPP_BUSINESS_PHONE` | Tu celular que recibe la demo |
| `WHATSAPP_VERIFY_TOKEN` | Secreto del webhook (mismo en Meta y acá) |
| `UPSTASH_REDIS_REST_URL/_TOKEN` | Perfil por chat (vacío = memoria local) |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | Intérprete LLM (primera opción) |
| `GROQ_API_KEY` | Intérprete LLM (fallback) |
| `DEEPSEEK_API_KEY` | Intérprete LLM (fallback) |
| `FAST_FLIGHTS_ENABLED` | `1` = precios reales de Google Flights |

## Estructura

```
app/
  main.py            # FastAPI: webhooks de Telegram y WhatsApp + /diagnostico
  orchestrator.py    # máquina de estados de la conversación (despacho por intención)
  intents.py         # intérprete: prompt LLM + heurística de respaldo
  flight_client.py   # precios Google Flights (fast-flights) + simulador
  destinos.py        # catálogo de destinos de Colombia (alias, IATA, sin-IATA)
  formatter.py       # renderiza opciones y precio legible
  fotos.py           # foto del destino desde Wikipedia (Wikimedia)
  links.py           # link directo a Google Flights para comprar
  models.py          # MensajeEntrada/Salida, Perfil (contexto por usuario)
  profile_store.py   # Upstash Redis REST o memoria local
  llm_router.py      # cascada Gemini → Groq → DeepSeek → fallback
  adapters/
    telegram.py      # canal Telegram (texto + foto + teclado)
    whatsapp.py      # canal WhatsApp Cloud API (texto + foto + verificación)
```

## Roadmap

- **Intérprete 100% LLM:** hoy la heurística de `intents.py` crece con cada
  frase nueva. El plan es que el LLM (ya es el primer intento) sea la única
  vía y la heurística quede solo como fallback de emergencia sin internet.
- **Compra real:** hoy se resuelve con el link de Google Flights. Un siguiente
  paso sería plantillas de WhatsApp para reservas fuera de la ventana de 24 h.
