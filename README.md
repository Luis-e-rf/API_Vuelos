# API Vuelos — Bot de vuelos multiplataforma

Bot conversacional que busca vuelos baratos en Colombia, habla en lenguaje
natural (español) y responde por **Telegram** y **WhatsApp** desde el mismo
núcleo. Un solo backend, canales intercambiables.

## Cómo funciona

```
telegram/WhatsApp ──> adapter (parse_todos) ──> DialogueManager
                                                     │
                              NLU: Extractor (Gemini JSON mode o determinista) -> RawSlots
                                                     │
                              Normalizers (dinero/ciudad/fecha/pasajeros) -> NormalizedSlots
                                                     │
                              SlotManager: invariantes + ASK_SLOT | SEARCH | RESET | CHITCHAT
                                                     │
                              ActionExecutor -> FlightClient (Google Flights o simulador)
                                                     │
                              Formatter + foto Wikimedia + link de compra ──> usuario
```

- **Canal agnóstico:** `MensajeEntrada`/`MensajeSalida` son la única interfaz
  entre los adaptadores y el `DialogueManager`. Para agregar un canal, crea un
  adaptador con `parse_todos(update) -> list[MensajeEntrada]` y `enviar(chat_id, salida)`.
- **NLU por contrato:** el LLM (Gemini `gemini-flash-lite-latest` en modo JSON
  estricto) solo extrae spans crudos (`RawSlots`: "1 millón por persona").
  Nunca normaliza ni decide acciones. Sin API key, un extractor determinista
  offline hace el mismo trabajo (vocabulario coloquial: "un palo", "pa san
  andres", "somos mi esposa y yo").
- **Normalizers puros:** `app/normalizers/` convierte dinero a COP (k/mil/
  lucas/millón/palo/melón, USD/EUR), ciudades al catálogo IATA (sin substring
  matching), fechas a ISO y cuenta viajeros. 100% testeables, sin LLM.
- **SlotManager:** valida invariantes (presupuesto > 50k COP, destino ≠
  origen, fecha futura) y pregunta lo que falta con preguntas específicas
  ("¿Desde qué ciudad sales?"). No llama a Google Flights con slots incompletos.
- **Estado:** `UserState` v2 (slots confirmados + resumen de historial de 5
  turnos solo para tono) en Upstash Redis con TTL de 30 días; sin credenciales,
  memoria local.
- **Vuelos:** usa `fast-flights` (Google Flights, gratis). Si la red falla o el
  destino no tiene código IATA, cae a un simulador interno de precios realistas.

## Requisitos

- Python 3.12+
- Dependencias: `uv sync` (o `pip install .`)
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
4. **Registra tu número de negocio:** en "Paso 3: Verificación del negocio"
   del wizard de WhatsApp, registra y verifica tu número por SMS/llamada.
5. **Vincula la App con la WABA:** ejecuta en Graph API Explorer:
   ```
   POST /{WABA_ID}/subscribed_apps
   ```
   Con permisos: `whatsapp_business_management`, `whatsapp_business_messaging`.
6. **Configura en el servidor** (Render → Environment):
   ```
   WHATSAPP_TOKEN=...
   WHATSAPP_PHONE_NUMBER_ID=...
   WHATSAPP_BUSINESS_PHONE=57...   # tu celular, con código de país
   WHATSAPP_VERIFY_TOKEN=vuelos-demo-2026  # cualquier secreto que elijas
   ```
7. **Registra el webhook en Meta:** WhatsApp → "Configuration" → "Edit" →
   Callback URL: `https://<tu-dominio>/webhook/whatsapp` y Verify token:
   `vuelos-demo-2026` (el mismo de arriba). Meta hará un GET y el bot responde
   el challenge automáticamente. En "Webhook fields" marca **messages**.
8. **Prueba:** desde tu celular, escribe al número de Business:
   *"tengo 2 millones, busca a san andres"*.

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
  main.py               # FastAPI: webhooks de Telegram y WhatsApp + /diagnostico
  config.py             # Carga de variables de entorno
  dialogue_manager.py   # flujo por turno: reset temprano -> NLU -> SlotManager -> Executor
  dialogue/
    slot_manager.py     # invariantes (presupuesto>50k, fecha futura) y next_action
    executor.py         # búsqueda validada + chitchat (LLM solo para tono)
  nlu/
    schemas.py          # contratos Pydantic RawSlots / NormalizedSlots
    extractor.py        # Gemini JSON mode (1 proveedor) + fallback determinista
    composicion.py      # RawSlots + estado -> NormalizedSlots (puro)
    api.py              # fachada interpretar(texto) -> NormalizedSlots
  normalizers/
    money.py            # "un palo", "600 mil", "500k", "300 dólares" -> COP
    city.py             # alias exacto por tokens + difflib último recurso
    date.py             # "principios de enero 2027" -> ISO; rango de meses
    passengers.py       # "somos mi esposa y yo" -> 2
    text.py             # utilidades (tildes, tokenización con offsets)
  flight_client.py      # precios Google Flights (fast-flights) + simulador
  destinos.py           # catálogo de destinos de Colombia (alias, IATA, sin-IATA)
  formatter.py          # renderiza opciones y precio legible
  fotos.py              # foto del destino desde Wikipedia (Wikimedia)
  links.py              # link directo a Google Flights para comprar
  models.py             # MensajeEntrada/Salida, UserState v2, Sender
  profile_store.py      # Upstash Redis REST (TTL + DELETE) o memoria local
  llm_router.py         # chitchat: Gemini -> Groq (gpt-oss) -> DeepSeek -> plantillas
  llm_providers/        # implementaciones de cada proveedor LLM
    base.py             # interfaz ProveedorLLM + helper OpenAI-compatible
    gemini.py           # Google Gemini (free tier)
    groq.py             # Groq openai/gpt-oss-20b|120b (free tier Developer)
    deepseek.py         # DeepSeek (económico)
  adapters/
    base.py             # Protocol CanalAdapter (parse_todos multi-message)
    telegram.py         # canal Telegram (texto + foto + teclado)
    whatsapp.py         # canal WhatsApp Cloud API (texto + foto + verificación)
tests/                  # pytest: golden dataset, normalizers, extractor, diálogo, adapters
```

## Roadmap

- **Compra real:** hoy se resuelve con el link de Google Flights. Un siguiente
  paso sería plantillas de WhatsApp para reservas fuera de la ventana de 24 h.
