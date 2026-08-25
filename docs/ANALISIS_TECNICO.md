# Análisis Técnico — API Vuelos Bot

> Fecha: 2026-08-24
> Autor: Muse Spark (análisis de repo completo)
> Objetivo: explicar cómo funciona hoy, por qué la capa LLM+heurística no entiende contexto, y proponer una arquitectura corregida.

---

## 1. Cómo funciona HOY (arquitectura real)

### 1.1 Flujo end-to-end

```
[Telegram/WhatsApp] 
  -> app/main.py (FastAPI /webhook, /webhook/whatsapp) 
  -> adapter.parse() -> MensajeEntrada(chat_id, texto, canal)  [app/models.py:8]
  -> Orquestador.procesar() [app/orchestrator.py:49]
     -> ProfileStore.leer() -> Perfil  [app/profile_store.py:37]
     -> _verificar_sesion() (48h) [app/orchestrator.py:475]
     -> early-reset regex (olvida todo) [app/orchestrator.py:63]
     -> esperando == "presupuesto" ? [app/orchestrator.py:85]
     -> Interpretador.interpretar() [app/intents.py:142]
        -> llm_router.generar() cascada Gemini->Groq->DeepSeek [app/llm_router.py:23]
        -> _parse_respuesta_llm() + validaciones ad-hoc [app/intents.py:174]
        -> fallback _heuristica() si LLM falla [app/intents.py:196]
     -> _dispatch() por Intencion.accion [app/orchestrator.py:138]
        -> _respuesta_buscar / _respuesta_destino / _respuesta_rango -> FlightClient [app/flight_client.py:128]
        -> _mostrar() -> formatter + fotos.py + links.py [app/formatter.py:11, app/fotos.py:60, app/links.py:8]
        -> _respuesta_conversacion() -> llm_router.generar() de nuevo [app/orchestrator.py:428]
     -> guarda historial (20 turnos) + ultima_conexion -> ProfileStore.guardar() [app/orchestrator.py:127]
  -> adapter.enviar() -> WhatsApp Graph API / Telegram API
```

### 1.2 Mapa de módulos comentado

| Archivo | Responsabilidad supuesta | Comentario / deuda |
|---|---|---|
| `app/main.py:88` | Webhook WhatsApp, HMAC, rate-limit | Correcto, pero mezcla infra (rate-limit dict en memoria) con lógica. No valida `json()` si firma falla — Lee body 2 veces en logs. |
| `app/adapters/whatsapp.py:62` | Parse WA `entry[0].changes[0].value.messages[0]` | Frágil: asume 1 mensaje; ignora `statuses`, no soporta múltiples mensajes en un POST. |
| `app/models.py:27` | `Perfil` = Dios-objeto: origen, destino, presupuesto, moneda, historial, opciones_recientes, viajes_guardados, esperando, ultimo_destino_sugerido, aerolinea, pasajeros, ultima_conexion | **Anti-patrón**: un solo dataclass persiste todo indefinidamente. Mezcla estado conversacional (historial) con estado de dominio (presupuesto) y UI (opciones_recientes). Por eso `olvida todo` tiene que resetear 11 campos a mano y se olvida uno → bug. |
| `app/profile_store.py:37` | Upstash Redis REST `GET/SET /perfil:{canal}:{chat_id}` | Sin transacción, sin TTL, sin migración de esquema. `from_dict` filtra keys desconocidas → silencioso. Si Upstash cae, fallback a memoria pero el orquestador no lo sabe. |
| `app/intents.py:62` | Prompt `_PROMPT_INTENT` + `Interpretador` | **Núcleo roto** (ver §2). Un solo prompt hace: NLU + slot filling + normalización de dinero + destinos + routing de 13 acciones. Sin JSON-mode, sin schema, sin ejemplos balanceados. |
| `app/destinos.py:12` | Catálogo `DESTINOS` IATA + `_ALIASES` + `normalizar_destino()` | Bien como fuente única, pero `normalizar_destino` hace substring matching (`if token in t`) → "pan" matchea "panal". `difflib` con cutoff 0.86 es caro y no determinista. |
| `app/llm_router.py:23` | Cascada Gemini→Groq→DeepSeek | Timeout 8s por proveedor, pero `intents.py:173` usa 12s + historial 10 turnos → latencia. Modelos Groq 404 (`llama-3.3-70b-versatile` ya no existe en 2026), Gemini fallback de 6 modelos obsoleto. |
| `app/llm_providers/gemini.py:18` | Candidatos `gemini-flash-lite-latest`... | Genera `contents` con `system+prompt` concatenados en un solo `user` → pierde rol system. No usa `response_mime_type: application/json`. |
| `app/orchestrator.py:138` | `_dispatch()` switch gigante por `accion` | 13 branches con lógica duplicada (`if intent.origen: p.origen=...` repetido 4 veces). Retorna tuplas `(MensajeSalida, False)` vs `MensajeSalida` inconsistente. Maneja `historial` manual (append + slice 20). |
| `app/flight_client.py:128` | `buscar()` live Google Flights o simulador | Simula precios con `random` por hora → no reproducible por usuario. No valida presupuesto antes de buscar. Multiplica precio al final `_por_pasajeros` sin distinguir ida/vuelta. |
| `app/formatter.py:11` | Render texto numerado | Mezcla COP y USD con tasa hardcodeada 4000 (duplicada en orchestrator y formatter). |
| `app/fotos.py:60` | Wikipedia ES summary | OK como nice-to-have, pero bloquea respuesta si tarda 6s. |

### 1.3 Estado y memoria hoy

* **Permanente**: `Perfil` en Redis sin expiración. Cualquier slot que el LLM extrae se escribe directo a `p.*` y nunca se limpia salvo `olvida todo`.
* **Efímero**: `historial: list[dict]` últimos 20 turnos, truncado a 48h por `_verificar_sesion`. Pero `historial` se pasa al LLM siempre, y el prompt lo incluye como texto plano `HISTORIAL: {historial}` → si el historial contiene presupuesto viejo, el LLM lo re-prioriza y contamina la nueva intención (bug visto: `olvida todo` + LLM conversacional devuelve `presupuesto de unos 1.000 COP`).
* **Máquina de estados implícita**: `p.esperando = "presupuesto"` es el único estado. No hay estados `esperando_destino`, `esperando_fecha`, ni validación de completitud.

---

## 2. Por qué la capa LLM+heurística está rota (diagnóstico)

No es un bug de `1 millón → 1000`. Es **diseño**:

### 2.1 Un solo LLM hace todo (NLU + normalización + routing)

`_PROMPT_INTENT` (`app/intents.py:69`) le pide al LLM en un solo JSON: `accion, destino, origen, presupuesto, moneda, pasajeros, rango_meses, barato, campo_actualizado`. El LLM debe a la vez:
* entender español coloquial ("de Bogotá a San Andrés, somos 2, pal millón por cabeza pa'l 2027 lo más barato")
* normalizar "1 millón" → `1000000` y "Bogotá" → `Bogota`
* elegir entre 13 acciones
* tolerar historial contaminado

Sin `response_format=json_object`, sin Pydantic, sin function-calling, el LLM devuelve texto libre que `_extraer_json` intenta rescatar con `find("{")/rfind("}")` → frágil. Cuando falla, el código ni siquiera sabe si falló por presupuesto, por destino o por acción.

Evidencia: `presupuesto < 10000` (`app/intents.py:182`) y `destino==origen` (`app/intents.py:179`) son parches post-hoc porque el LLM inventa `1000` para "1 millón". El `log.warning` confirma que el fallback se activa, pero luego `_heuristica` tampoco extrae presupuesto → se pierde todo.

### 2.2 Heurística compite contra LLM, no lo complementa

Arquitectura actual: `try LLM → if fail → heurística` (`app/intents.py:142-206`). La heurística es un conjunto de regex españoles (`_extraer_pasajeros`, `_extraer_fecha`, `_extraer_origen_destino`, `_quiere_comprar`, `_huele_busqueda`) que intenta replicar lo que el LLM ya debería hacer, pero con reglas gramaticales frágiles.

Problemas:
* Regex `r"(?:de|desde)\s+(.+?)\s+(?:a|hasta)\s+(.+?)(?:\s*,|\s*$)"` (`intents.py:571`) captura "de Bogotá a San Andrés, 2 personas" como `origen="bogotá", destino="san andrés, 2 personas"` → luego `normalizar_destino` limpia, pero si hay ruido ("de Bogotá a San Andrés pal 2027") no matchea el `,|$`.
* `_extraer_pasajeros` solo matchea `somos|hay|viajan|para ... personas` → "2 personas" solo sin verbo no lo detecta si no pasó por LLM.
* `_PASAJEROS_RE` usa `([a-záéíóú]+|\d+)` y luego `_valor_numero` → "dos" ok, pero "2p" no.
* `_quiere_comprar` requiere que NO haya palabras "busca/barato/destino" → si el usuario dice "quiero comprar barato" se ignora.

Resultado: ni LLM ni heurística son autoritativas; se corrigen mutuamente con `if LLM sin origen → heurística lo inyecta` (`intents.py:185`) → carrera de parches.

### 2.3 Gramática española no es el camino

La idea inicial ("que entienda a cualquier persona, mayor de 60, sin formularios") es correcta, pero implementarla con regex de español es imposible: hay infinitas variaciones ("pa dos", "somos mi esposa y yo", "un palo", "un melón", "quiero irme pal San Andrés"). Cada nueva regla introduce falsos positivos. El bot acaba iterando entre errores: se arregla "de X a Y" y se rompe "para 2 personas".

**Principio**: normalización determinista sí (COP, IATA, fechas ISO), pero **no** gramática para intención.

### 2.4 Perfil contamina el contexto

`perfil.to_dict()` se inyecta al prompt (`app/intents.py:160`), pero el prompt solo muestra 5 campos. El LLM ve `Origen: Bogota, Destino: San Andres, Presupuesto: 1000 COP` heredado de un turno anterior y cree que es la intención actual. No hay distinción entre "lo que el usuario dijo ahora" vs "lo que ya sabemos". Por eso tras `olvida todo` el LLM conversacional aún habla de `1.000 COP` — el `historial` se limpia, pero `perfil` se guarda antes de llamar al LLM y el `contexto` en `_respuesta_conversacion` (`app/orchestrator.py:429`) lo reinyecta.

### 2.5 Multi-intención mal modelada

`ResultadoInterpretacion.intenciones: list[Intencion]` (`intents.py:129`) permite 2 intenciones, pero el orquestador las ejecuta secuencialmente con `for intent in resultado.intenciones: await _dispatch()` (`orchestrator.py:121`) y cada dispatch puede sobreescribir `p.destino/p.presupuesto` y guardar. Si el LLM devuelve `[{buscar destino=Cartagena}, {pasajeros=2}]`, el segundo dispatch ya ve el perfil mutado del primero → orden-dependiente.

Además, nunca se usa multi-intención en la práctica: los ejemplos del prompt solo muestran 1 intent, y `_heuristica` siempre retorna 1.

### 2.6 Sin validación de slots antes de llamar a la API

`FlightClient.buscar(origen, presupuesto_cop, ...)` se llama con `origen or "Bogota"` y `cop=_a_cop(presupuesto)` aunque `presupuesto is None` ya se chequeó, pero no se valida si `origen` es IATA conocido, si `presupuesto` es razonable (>50k COP), si `fecha` es futuro. El simulador filtra `d != origen and _PRECIOS_COP[d] <= presupuesto` pero Google Flights no — pide 6 destinos a ciegas y si falla, loguea warning y cae a simulador sin decirle al usuario por qué.

### 2.7 Fallos en cascada silenciosos

* LLM falla → heurística falla → `mensaje_clarificacion` genérico ("¿Puedes reformular?") → usuario reformula con más contexto → historial crece → próximo LLM ve historial más largo → timeout.
* `llm_router.generar` con `timeout=12` en intents pero 8 en conversacional → inconsistente.
* Groq 404 y Gemini 429 se loguean como `warning` pero el bot no degrada a modo "solo heurística determinista" — sigue intentando LLM en cada turno.

---

## 3. Nueva arquitectura propuesta

### 3.1 Principios

1. **Separar NLU de ejecución**. El LLM solo extrae slots; nunca decide `accion` ni escribe perfil.
2. **LLM en modo JSON estricto + validación Pydantic**. Si el JSON no valida, se reintenta una vez con mensaje de error; si vuelve a fallar, se usa parser determinista (no regex gramatical, solo extractores de entidades).
3. **Normalizadores deterministas post-LLM**: dinero → COP, ciudad → canónica, fecha → ISO, pasajeros → int. El LLM devuelve spans crudos ("1 millón por persona"), el normalizador convierte.
4. **State machine explícita**: `SlotManager` con slots faltantes y preguntas de clarificación, no `esperando="presupuesto"` suelto.
5. **Perfil = solo preferencias confirmadas**, historial = solo contexto para NLG, no para NLU.
6. **Un proveedor LLM + JSON mode**, no cascada infinita.

### 3.2 Diagrama propuesto

```
WhatsApp/Telegram
  -> Adapter (parse único, soporta múltiples messages en entry)
  -> Gateway (HMAC, rate-limit por chat_id, deduplication por message_id)
  -> DialogueManager
     -> NLU (LLM JSON + DeterministicNormalizers)
        input: texto + slots_confirmados + ultimo_turno (no historial completo)
        output: RawSlots {origen_raw, destino_raw, presupuesto_raw, pasajeros_raw, fecha_raw, rango_raw, intent_hint}
     -> SlotManager (valida y normaliza)
        - normaliza destino/origen via destinos.py pero con tokenizador (no substring)
        - normaliza presupuesto via MoneyParser (soporta "palo", "melon", "k", "millo")
        - valida completitud: ¿falta origen? ¿presupuesto? ¿destino vs rango?
        - decide próxima acción: ASK_SLOT | SEARCH | SHOW_OPTIONS | RESET | CHITCHAT
     -> ActionExecutor
        - SEARCH -> FlightClient (valida antes, con presupuesto COP y origen IATA)
        - ASK_SLOT -> NLG (pregunta específica, no genérica)
        - CHITCHAT -> NLG (llm_router solo para charla, sin slots)
     -> NLG (templates + LLM opcional para tono cálido, no para datos)
  -> Adapter.enviar()
  -> ProfileStore (solo slots confirmados + historial resumido 5 turnos)
```

### 3.3 Contratos (esqueleto)

```python
# app/nlu/schemas.py
class RawSlots(BaseModel):
    origen_raw: Optional[str] = None      # "Bogotá", "desde Medellín"
    destino_raw: Optional[str] = None     # "San Andrés", "a cartagena"
    presupuesto_raw: Optional[str] = None # "1 millón por persona", "600k"
    pasajeros_raw: Optional[str] = None   # "2 personas", "somos mi esposa y yo"
    fecha_raw: Optional[str] = None       # "principios de enero 2027", "en 3 meses"
    rango_meses_raw: Optional[str] = None
    intent_hint: Literal["search","change","reset","chitchat","select_option"] = "search"
    # El LLM NO devuelve accion con 13 valores, solo hint.

class NormalizedSlots(BaseModel):
    origen: Optional[str] = None          # canónica "Bogota"
    destino: Optional[str] = None
    presupuesto_cop: Optional[int] = None # ya multiplicado si "por persona" + auditado >50000
    pasajeros: int = 1
    fecha_iso: Optional[str] = None
    rango_meses: Optional[int] = None
```

**Prompt NLU** (JSON mode, ~150 tokens, no historial completo):
```
Eres extractor de slots de vuelos. Devuelve SOLO JSON válido.
Extrae tal cual aparece, sin normalizar.
Texto: "de Bogotá a San Andrés, 2 personas, 1 millón por persona y que sea en 2027 lo más económico"
→ {"origen_raw":"Bogotá","destino_raw":"San Andrés","presupuesto_raw":"1 millón por persona","pasajeros_raw":"2 personas","fecha_raw":"2027","rango_meses_raw":null,"intent_hint":"search"}
Reglas: no inventes, si no hay dato deja null.
```

**Normalizadores** (deterministas, testeables):
* `MoneyParser.parse("1 millón por persona", pasajeros=2) -> (1000000, moneda=COP, es_por_persona=True)` con regex de números + palabras + "k/mil/millón/palo" y regla `if "por persona" in raw.lower(): presupuesto_total = unit * pasajeros` explícita en SlotManager, no en LLM.
* `CityNormalizer.normalize("san andres") -> "San Andres"` via tokenización + alias exacto + difflib solo como último recurso, sin substring.
* `DateParser.parse("2027") -> "2027-01-15"` con año futuro default y validación `date >= today`.

### 3.4 Qué eliminar / qué conservar

| Mantener | Reescribir | Eliminar |
|---|---|---|
| `destinos.py` DESTINOS/IATA como fuente única | `intents.py` completo → `nlu/` + `slot_manager.py` | Regex gramatical `_heuristica`, `_quiere_comprar`, `_huele_busqueda`, `_extraer_origen_destino` frágil |
| `flight_client.py` Google Flights + simulador (extraer validación) | `orchestrator.py` → `dialogue_manager.py` con state machine | `Perfil` Dios-objeto → `UserState {slots, history_summary, pending_question}` |
| `adapters/` contrato, pero fix multi-message | `profile_store.py` → con TTL + versionado | Cascada 3 LLMs sin JSON mode |
| `formatter.py` / `links.py` / `fotos.py` | `llm_router` → un solo provider con `response_format=json_object` | `ACCIONES` de 13 valores |
| `config.py` | Prompt conversacional (separar de NLU) | Historial inyectado crudo al prompt de intención |

### 3.5 Ejemplo de flujo corregido

Usuario: `Hola busco de Bogotá a San Andrés, 2 personas, 1 millón por persona y que sea en 2027 lo más económico`

1. NLU LLM → `RawSlots(origen_raw="Bogotá", destino_raw="San Andrés", presupuesto_raw="1 millón por persona", pasajeros_raw="2 personas", fecha_raw="2027")`
2. SlotManager:
   * CityNormalizer → `origen="Bogota", destino="San Andres"`
   * MoneyParser → `presupuesto_cop = 1_000_000 * 2 = 2_000_000` (porque detecta "por persona")
   * DateParser → `fecha_iso="2027-01-15"` (mitad de enero por defecto)
   * Validación: todos los slots críticos presentes → acción `SEARCH`
3. ActionExecutor → `FlightClient.buscar(origen="Bogota", presupuesto_cop=2000000, destino="San Andres", fecha="2027-01-15", pasajeros=2)`
4. NLG → template cálido con opciones reales, no LLM inventando precios.

Si usuario dice `olvida todo`:
* NLU `intent_hint=reset` → SlotManager `RESET` → `UserState.clear()` + `history.clear()` antes de cualquier otra cosa, sin pasar por LLM.

Si usuario dice `somos 3` en turno siguiente:
* NLU `pasajeros_raw="somos 3"` → SlotManager actualiza solo `pasajeros=3`, recalcula `presupuesto_cop` si era "por persona", pregunta `¿Busco de nuevo con 3 personas?`

### 3.6 Testing y observabilidad

* **Golden dataset**: 50 frases reales (incluyendo typos, audios transcritos, "pa san andres", "un palo", "mi esposa y yo") con slots esperados. CI corre `pytest tests/test_nlu.py` que valida normalizadores sin LLM.
* **Logs estructurados**: `log.info("nlu_raw=%s normalized=%s action=%s", raw, norm, action)` en vez de 3 logs separados.
* **Feature flag**: `NLU_MODE=llm|deterministic` para poder operar sin LLM si proveedores caen.

---

## 4. Plan de corrección por fases (sin reescribir todo a ciegas)

**Fase 0 — Congelar y testear lo actual (1 día)**
* Crear `tests/test_intents_golden.py` con 20 casos del bug real (incluido "1 millón por persona", "de X a Y"). Ver que hoy fallan. Commit como línea base.

**Fase 1 — Aislar normalizadores (2 días)**
* Crear `app/normalizers/money.py`, `city.py`, `date.py`, `passengers.py` con funciones puras y tests. Migrar `destinos.py:199 normalizar_destino` a `CityNormalizer` sin substring.
* Cambiar `intents.py` para que LLM devuelva `*_raw` y normalizadores conviertan después (no LLM normalizando). Eliminar validación `presupuesto<10000` ad-hoc.

**Fase 2 — NLU JSON estricto (2 días)**
* Nuevo `app/nlu/extractor.py` con un solo provider (Gemini `gemini-flash-lite-latest` con `generationConfig.response_mime_type="application/json"`), prompt corto, Pydantic, reintento 1 vez.
* Eliminar cascada Groq/DeepSeek o dejar solo como fallback si `json` falla.
* Borrar `ACCIONES` de 13; usar `intent_hint` de 5.

**Fase 3 — DialogueManager + UserState (3 días)**
* Reemplazar `Perfil` por `UserState(slots: NormalizedSlots, history_summary: list, pending_question: Optional[str], last_search: Optional[dict])` con `version` para migración Redis.
* `SlotManager` decide `ASK_SLOT` con pregunta específica: "¿Desde qué ciudad sales?" no "¿Puedes reformular?".
* `olvida todo` como comando determinista antes de NLU (mantener early-reset pero con `store.delete(key)` no mutación parcial).

**Fase 4 — FlightClient validado + NLG separado (2 días)**
* Validar `origen IATA, presupuesto>50000, fecha>=hoy` antes de `buscar`. Si falta slot, no llamar a Google Flights.
* NLG conversacional separado: LLM solo para tono, con `Perfil` resumido, no para datos.

**Fase 5 — Deploy gradual**
* Feature flag `NEW_NLU=1` en Render, comparar logs viejo vs nuevo con mismo `chat_id` de prueba. Rollback inmediato si golden falla.

Estimado total: 8-10 días con tests, vs seguir parcheando regex que ya lleva 6 commits (`bd3ef4c`, `ec4647e`, `768436b`...) sin cerrar el bug.

### 4.1 Registro de ejecución real (2026-08-24, refactor completado)

Estado: **FASES 0-5 ejecutadas y verificadas**. Suite: `pytest` 106 tests en verde. Cobertura `app/normalizers/`: 98%.

| Fase | Commit | Entregado |
|---|---|---|
| 0 | `db22a4f` | Golden dataset de 20 frases coloquiales (`tests/test_nlu_golden.py`) + contratos Pydantic `RawSlots`/`NormalizedSlots` (`app/nlu/schemas.py`). Línea base contra el legacy: 11/20 fallaban. |
| 1 | `2a9216d` | `app/normalizers/{money,city,date,passengers,text}.py`: funciones puras. MoneyParser (k/mil/lucas/millón/palo/melón, separador de miles, USD/EUR→COP, marca "por persona"), CityNormalizer (ventanas de tokens + alias exacto, difflib 0.88 solo último recurso, sin substring), DateParser (períodos 05/15/25, año suelto→01-15, rolloff futuro), pasajeros (familiar+"y yo", cantidad+sustantivo, pareja). |
| 2 | `4fa1dd4` | `app/nlu/extractor.py`: Gemini `gemini-flash-lite-latest` con `response_mime_type=application/json`, prompt <250 tokens, 2 few-shots, reintento único, sin cascada. Fallback determinista offline. `composicion.py`: fusión de slots + multiplicación "por persona" por pasajeros del turno. Golden 20/20 sin LLM. |
| 3 | `c85abad` | `app/dialogue_manager.py` + `app/dialogue/{slot_manager,executor}.py`: early-reset determinista con `store.borrar()`, invariantes (presupuesto>50k, destino≠origen, fecha futura), `ASK_SLOT` con pregunta específica, `UserState` v2 (Pydantic) con `history_summary` máx 5 solo para NLG. `ProfileStore` v2: clave `estado:*` con TTL 30d, DELETE real, JSON en cuerpo del POST (fix del bug de encoding). |
| 4 | `b4dae58` | Groq migrado a `openai/gpt-oss-20b`→`gpt-oss-120b` (llama-3.3-70b-versatile pasó a Enterprise: 404 en free). Fix doble conteo: Google Flights ya cotiza por el grupo, el multiplicador queda solo para el simulador. Guardas en ActionExecutor: sin presupuesto u origen sin IATA no se llama a la API. |
| 5 | (este) | Legacy eliminado (`intents.py`, `orchestrator.py`, flag `NEW_NLU`): criterio `grep -r "_heuristica\|_quiere_comprar\|_huele_busqueda" app/` = 0. Rollback = `git revert 2a9216d..c85abad`. WhatsApp `parse_todos` procesa múltiples messages por POST; Telegram soporta `edited_message`. `pydantic` explícito en pyproject. README y este documento actualizados. |

Decisiones de diseño tomadas durante la ejecución (desviaciones documentadas):

1. `NormalizedSlots.intent_hint` y `RawSlots.numero_opcion` se añadieron al contrato para que el dataset dorado observe reset/chitchat/"la 2" de forma stateless; `numero_opcion` se llena SIEMPRE por vía determinista.
2. `"somos mi esposa y yo"` = 2 personas (el prompt original decía 3; confirmado con el autor: 2 es lo correcto).
3. El fallback determinista llena los `*_raw` con el texto completo (los normalizers son idempotentes); el LLM sí extrae spans precisos.
4. El flag `NEW_NLU` vivió en fases 3-4 y se retiró en la 5: mantener el Orquestador legacy como "rollback" era una trampa (crash conocido: `_dispatch` retornaba tuplas). Rollback real: `git revert`.
5. `Perfil` v1 y sus métodos de store permanecen como esquema deprecado para no romper datos viejos en Redis; la clave nueva `estado:*` convive sin colisión.
6. Pendiente fuera de alcance: compra real dentro del chat (hoy link a Google Flights), plantillas WhatsApp fuera de ventana 24h, tasas de cambio vía API (siguen fijas 4000/4400), TTL para claves v1 huérfanas.

---

## 5. ¿Es un proyecto muy complejo sin arquitectura principal? ¿Hay que guiarlo más?

**Sí. Es exactamente el caso de "sin contrato inicial, la complejidad se vuelve exponencial".**

Por qué:

* **LLM como pegamento**: al no definir al inicio "qué es una intención" vs "qué es un slot" vs "qué es un estado", el LLM se usó para tapar cada hueco de diseño. Cada parche ("si LLM devuelve 1000, fallback a heurística") crea otro hueco ("heurística no extrae presupuesto"). Es el loop que ves: el LLM arregla un error creando otro, porque no hay capa de validación que lo contenga.

* **Sin arquitectura, no hay invariantes**: no hay forma de decir "esto nunca debe pasar" (ej: `presupuesto` nunca < 50k, `destino` nunca == `origen`). Sin invariantes, los bugs no se detectan, solo se observan en producción por el usuario.

* **Integración guiada es obligatoria ahora**: no basta con "prompt mejor". Hace falta:
  * Contrato Pydantic primero (qué datos el bot necesita para buscar un vuelo)
  * Luego NLU que llene ese contrato
  * Luego validación que rechace contratos inválidos
  * Luego ejecución

  Ese orden es integración guiada. Si se deja al LLM "entender múltiples contextos" sin slots definidos, el LLM inventará contextos.

**Recomendación honesta**:

> Si sigues parcheando `intents.py` con heurísticas de español, en 2 semanas tendrás 800 líneas de regex y el mismo bug con otra frase ("dos palos" en vez de "1 millón"). La salida no es más gramática, es menos gramática y más contratos.

* Haz la Fase 1 (normalizadores) esta semana — es reversible y no toca LLM.
* Si el golden dataset de Fase 0 muestra que >30% de frases fallan, no despliegues más parches sobre `orchestrator.py`; pasa a Fase 2.

No es un proyecto inviable, pero **sí requiere parar de iterar en el prompt y empezar a iterar en el contrato**. El LLM es bueno extrayendo "Bogotá" y "1 millón por persona" si le pides solo eso; es malo decidiendo si debe ser `buscar` o `actualizar_perfil` o `conversacion` mientras normaliza todo a la vez.

---

## 6. Checklist para el próximo PR

- [ ] `app/nlu/schemas.py` con Pydantic + `response_format=json_object`
- [ ] `app/normalizers/money.py` con soporte "millón/palo/k/millo/por persona" y test `test_money.py`
- [ ] `app/nlu/extractor.py` con un provider, sin cascada
- [ ] `app/dialogue/slot_manager.py` con `ASK_SLOT` específico
- [ ] `app/models.py` con `UserState` versionado y `store.delete` en reset
- [ ] `tests/golden.jsonl` con 50 frases y `pytest` en CI
- [ ] Logs `nlu_raw/normalized/action` en una línea

---

*Documento generado a partir de lectura completa de `app/*.py` y `git log`. Para implementar, empezar por `docs/ANALISIS_TECNICO.md:3.5` ejemplo y `4. Fase 1`.*
