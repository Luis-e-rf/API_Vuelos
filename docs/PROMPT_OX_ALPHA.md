# Prompt para escalar API_Vuelos con modelos gratuitos (ox alpha / opencode sin /goal)

Copia y pega este prompt tal cual en ox alpha (o cualquier LLM gratuito). Está diseñado para que una IA lo ejecute en 3-5 horas por fases, sin intervención.

---

## PROMPT MAESTRO (copiar desde aquí)

```
Eres un Tech Lead + Senior Python Engineer. Tu misión es REFACTORIZAR el repo API_Vuelos para que el bot de vuelos entienda español coloquial y llame correctamente a la API de vuelos, usando SOLO modelos gratuitos.

## CONTEXTO OBLIGATORIO
Lee primero estos archivos en orden:
1. docs/ANALISIS_TECNICO.md (diagnóstico completo de la arquitectura rota)
2. app/orchestrator.py (orquestador actual, líneas 49-137 flujo principal, 138-247 _dispatch)
3. app/intents.py (intérprete LLM+heurística roto, líneas 62-125 prompt, 142-206 interpretar, 283-352 heurística)
4. app/models.py (Perfil Dios-objeto)
5. app/destinos.py (catálogo IATA + alias substring)
6. app/flight_client.py (buscar con Google Flights / simulador)
7. app/llm_router.py + app/llm_providers/gemini.py (cascada rota, Groq 404)
8. app/profile_store.py (Upstash sin TTL)

No inventes APIs. Verifica cada archivo con Read antes de editar.

## DIAGNÓSTICO QUE YA CONOCES (no lo re-descubras)
- El LLM hace NLU+normalización+routing 13 acciones en un solo prompt sin JSON mode -> devuelve 1000 para "1 millón" y destino==origen para "de Bogotá a San Andrés".
- Heurística regex española compite contra LLM (r"(?:de|desde)\s+(.+?)\s+(?:a|hasta)") y contamina. Gramática no escala.
- Perfil (models.py:27) mezcla estado de dominio+historial+UI y se reinyecta al prompt, causando eco "1.000 COP" tras "olvida todo".
- Sin validación de slots antes de FlightClient.buscar(). Multi-intención muta perfil en loop.
- Groq llama a llama-3.3-70b-versatile (404 en 2026), Gemini no usa response_mime_type=json.

## OBJETIVO
Nueva arquitectura CONTRACT-FIRST en 5 fases. No parches regex. Entregar bot que entienda cualquier forma coloquial ("pa san andres", "un palo", "somos mi esposa y yo", "de Bogotá a San Andrés 1 millón por persona 2027") y busque vuelos con presupuesto COP correcto.

## ARQUITECTURA OBJETIVO (implementa esto)
```
Adapter -> DialogueManager -> NLU (LLM JSON estricto -> RawSlots) -> Normalizers -> SlotManager -> ActionExecutor -> NLG
```

Contratos Pydantic:
class RawSlots(BaseModel):
  origen_raw: Optional[str] = None
  destino_raw: Optional[str] = None
  presupuesto_raw: Optional[str] = None  # tal cual: "1 millón por persona"
  pasajeros_raw: Optional[str] = None
  fecha_raw: Optional[str] = None
  rango_meses_raw: Optional[str] = None
  intent_hint: Literal["search","reset","chitchat","select_option","change"] = "search"

class NormalizedSlots(BaseModel):
  origen: Optional[str] = None  # canónica "Bogota"
  destino: Optional[str] = None
  presupuesto_cop: Optional[int] = None  # ya con *pasajeros si "por persona" y validado >50000
  pasajeros: int = 1
  fecha_iso: Optional[str] = None
  rango_meses: Optional[int] = None
```

## RESTRICCIONES PARA MODELOS GRATUITOS
- Un solo proveedor LLM: Gemini gemini-flash-lite-latest con generationConfig.response_mime_type="application/json" y response MAX 500 tokens. No cascada. Si GEMINI_API_KEY vacío, usa determinista (no falles).
- Prompts <250 tokens. Ejemplos few-shot máx 2.
- No regex gramatical. Normalizadores deterministas puros y testeables (CityNormalizer sin substring `if token in t`, MoneyParser con "k/mil/millón/palo/millon/millo", DateParser).
- Rate limit y HMAC ya existen en main.py:88, no romper.
- Python 3.12, FastAPI, httpx, Pydantic (añadir si falta). No añadir deps pesadas.

## PLAN SECUENCIAL (ejecuta fase por fase, verifica con tests antes de avanzar)

FASE 0 - GOLDEN DATASET (30 min, sin LLM):
Crea tests/test_nlu_golden.py con 20 casos que HOY fallan:
- "de Bogotá a San Andrés, 2 personas, 1 millón por persona 2027" -> origen Bogota, destino San Andres, presupuesto 2000000 (1M *2), pasajeros 2
- "somos mi esposa y yo" -> 3
- "un palo" -> 1000000
- "pa san andres" -> San Andres
- "olvida todo" -> reset
Ejecuta pytest, confirma que fallan con código actual. Commit.

FASE 1 - NORMALIZERS (60 min):
Crea app/normalizers/money.py, city.py, date.py, passengers.py con funciones puras + tests/test_normalizers.py. CityNormalizer: tokeniza, luego alias exacto, luego difflib cutoff 0.88 solo último recurso. MoneyParser: detecta "por persona" y retorna (unit, es_por_persona). No tocar intents.py aún. pytest debe pasar.

FASE 2 - NLU JSON ESTRICTO (60 min):
Crea app/nlu/schemas.py (Pydantic RawSlots) y app/nlu/extractor.py con un solo método async extract(texto)->RawSlots usando Gemini JSON mode. Prompt: "Eres extractor slots vuelos. Devuelve SOLO JSON. Extrae tal cual sin normalizar." Reintento 1 vez si json.loads falla. Añade fallback determinista que retorna RawSlots vacíos si LLM no configurado. Crea tests/test_nlu_extractor.py con mock. Borra dependencia de ACCIONES 13.

FASE 3 - DIALOGUE MANAGER + USERSTATE (90 min):
Reemplaza orchestrator.py por dialogue_manager.py. Crea app/models.py: UserState(slots:NormalizedSlots, history_summary:list[dict] max5, pending_question:Optional[str], version:int=2). SlotManager valida invariantes (presupuesto>50000, destino!=origen, fecha>=hoy) y decide next_action: ASK_SLOT (pregunta específica "¿Desde qué ciudad sales?") / SEARCH / RESET (store.delete key) / CHITCHAT. Historial solo para NLG, no para NLU. early-reset "olvida todo" determinista ANTES de NLU con delete. Mantén compatibilidad con ProfileStore (from_dict filtra keys).

FASE 4 - FLIGHTCLIENT VALIDADO + NLG (45 min):
Valida en ActionExecutor antes de buscar: origen IATA existe, presupuesto_cop not None, fecha futura. Si falta slot, ASK_SLOT, no llames a Google Flights. Separa NLG: llm_router solo para chitchat con system persona corta, sin slots.

FASE 5 - INTEGRACIÓN (30 min):
Reconecta main.py a DialogueManager, feature flag NEW_NLU=1. Actualiza render.yaml si añades pydantic. Ejecuta pytest completo + prueba manual con golden dataset.

## CRITERIOS DE ACEPTACIÓN
- pytest tests/test_nlu_golden.py 20/20 pasan sin LLM (usando normalizers) y con LLM mock.
- "de Bogotá a San Andrés, 2 personas, 1 millón por persona" -> presupuesto_cop 2000000, no 1000.
- "olvida todo" -> siguiente mensaje no muestra presupuesto viejo.
- Cobertura normalizers >90%, sin regex heurística restante en intents.py.
- Latencia NLU <2s, prompt <300 chars historial.

## REGLAS DE EJECUCIÓN
1. Lee archivo -> Edita mínimo -> Testea -> Commit atómico por fase.
2. Comenta código con docstrings, no con regex.
3. Si un proveedor LLM da 404/429, log warning y usa determinista, no cascada infinita.
4. No rompas adapters (Telegram/WhatsApp parse debe soportar múltiples messages en entry).
5. Al final actualiza docs/ANALISIS_TECNICO.md §4 con lo realizado.

Empieza por FASE 0 ahora. Muestra git diff y pytest output por fase.
```

## FIN PROMPT MAESTRO

---

## Cómo usarlo en opencode sin /goal (secuencialidad ox alpha)

Como opencode aquí no tiene `/goal`, ejecuta el prompt maestro en 3 llamadas secuenciales gratuitas:

**Llamada 1 (ox alpha gratuito - Gemini Flash Lite):**
Pega solo FASE 0 + FASE 1 del prompt. Pide: "Ejecuta solo Fase 0 y 1, entrega tests/test_nlu_golden.py y app/normalizers/* con pytest output."

**Llamada 2 (ox alpha gratuito - Groq o DeepSeek si Gemini cae):**
Pega FASE 2 + FASE 3. Input: "Continúa sobre el commit anterior. Implementa NLU JSON y DialogueManager. No reescribas normalizers."

**Llamada 3 (ox alpha):**
Pega FASE 4 + FASE 5. Input: "Finaliza integración, valida criterios de aceptación y actualiza docs."

Ventaja secuencial: cada modelo gratuito recibe contexto acotado (<4k tokens) y entrega artefactos testeables. Si un modelo falla, el siguiente retoma desde git.

## Tips para modelos gratuitos

- Añade al inicio de cada llamada: `Responde con max 500 tokens, sin markdown excesivo, prioriza código ejecutable.`
- Si ox alpha limita a 8k contexto, omite la sección DIAGNÓSTICO y referencia solo `docs/ANALISIS_TECNICO.md`.
- Usa `uv run pytest -q` para verificar sin instalar global.

## Checklist para validar que la IA lo ejecutó

- [ ] `git log --oneline` muestra 5 commits fase por fase
- [ ] `uv run pytest` 20/20 golden pasan
- [ ] `grep -r "_heuristica\|_quiere_comprar\|_huele_busqueda" app/` = 0 resultados
- [ ] `curl /diagnostico` sigue OK y `/webhook/whatsapp` responde 200 con HMAC
- [ ] Prueba manual WhatsApp: "de Bogotá a San Andrés 2 personas 1 millón por persona 2027" -> muestra San Andrés ~2M COP

## Prompt corto alternativo (si ox alpha tiene límite de tokens)

```
Lee docs/ANALISIS_TECNICO.md y app/intents.py. Refactoriza a NLU JSON estricto + Normalizers deterministas + SlotManager con Pydantic. Entrega Fase 0-1 en este turno: golden dataset 20 casos + app/normalizers/* testeados. No uses regex gramatical, un solo LLM Gemini JSON mode, valida presupuesto>50k.
```
