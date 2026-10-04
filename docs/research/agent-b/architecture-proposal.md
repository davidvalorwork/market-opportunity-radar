# Propuesta de arquitectura optimizada

Autor: Claude (frente B). Fecha: 2026-10-03. Estado: **propuesta para que el
coordinador la integre**. No es código ni infraestructura existente.

Lectura completa previa: [informe A](../agent-a-platforms.md) (A-F01–A-F27),
[informe B](../agent-b-sessions.md) (B-F001–B-F040, B-D001–B-D053),
[decisiones](../decisions.md) (R-001–R-018),
[workplan](../../testing/WORKPLAN.md) y [resultados del laboratorio](../../testing/LOCAL_RESULTS.md),
[ARCHITECTURE](../../ARCHITECTURE.md), [ROADMAP](../../ROADMAP.md),
[SESSION_MANAGEMENT](../../SESSION_MANAGEMENT.md),
[LOCAL_LAMBDA_TESTING](../../LOCAL_LAMBDA_TESTING.md), EVALUATIONS,
COST_MODEL, CONFIGURATION, SOURCES, VISION,
[lab/browser](../../../lab/browser/README.md),
[lab/sessions](../../../lab/sessions/README.md) y la
[guía Telegram + Lambda](telegram-lambda-best-practices.md).

Restricciones del usuario (2026-10-03): Lambda bajo demanda; Telegram como única
interfaz; piloto WhatsApp con colaboradores en Venezuela; modelo general de
oportunidades; IA pequeña vía OpenRouter; costo mínimo en una cuenta AWS antigua
(Always Free).

## 1. Diagnóstico: qué sobra, qué choca y qué falta

| # | Hallazgo | Dónde | Propuesta |
|---|---|---|---|
| 1 | Tres mecanismos de cifrado de sesiones: age (lab), AES-256-GCM con clave en SSM (B-F017) y age + S3 (SESSION_MANAGEMENT) | lab/sessions, informe B | **age en todas partes.** La identidad age del worker va en SSM SecureString (una identidad X25519 ocupa decenas de bytes, cabe en 4 KB). Se retira el AES propio |
| 2 | Tres lugares para leases/estado: lock + `registry.json` local, S3 `If-Match` y DynamoDB | lab, B-D009, B-D033 | **DynamoDB para todo lo mutable** (lease, puntero de versión, ledger, idempotencia, deduplicación). S3 solo blobs inmutables por versión, igual que `sessions/ALIAS/N.age` del lab |
| 3 | El CAS de S3 se presentaba como protección de envíos | B-D009 vs A-F27 / R-013 | De acuerdo con A: **ledger antes del efecto + token de fencing verificado por el propio worker + reconciliación**. Ver §5 |
| 4 | ARCHITECTURE describe monolito local con SQLite y dashboard | ARCHITECTURE | Mantener el **monolito modular hexagonal en el código**, desplegado como **varias Lambdas** con el mismo paquete. La interfaz es Telegram; SQLite queda como adaptador local de pruebas |
| 5 | Lógica de dominio en riesgo de duplicarse en Node y Go | lab/browser extrae y valida | **El dominio solo vive en Python.** Los workers Node/Go son adaptadores de I/O que devuelven campos crudos tipados por contrato |
| 6 | Contratos JSON definidos en prosa (WORKPLAN) y validados por separado en cada lenguaje | WORKPLAN, lab | **JSON Schema único en `contracts/`**, ejemplos dorados compartidos y validación en los tres lenguajes |
| 7 | `config/radar.example.json` es solo productos y sin esquema | config | Esquema JSON con verticales (B-D028) y preferencias por usuario en DynamoDB |
| 8 | Capacidades por red en tablas Markdown | informe A, SOURCES | **Registro de capacidades como datos** (`contracts/capabilities.json`) que el preflight consulta (A-D001) |
| 9 | Free tier citado como "costo cero" en varios lugares | WORKPLAN, informe B | Presupuesto explícito con Always Free + S3 cobrado + alerta Budgets (B-D048r) |
| 10 | Falta un camino de pruebas de flujo completo sin Docker | LOCAL_LAMBDA_TESTING niveles 0–5 | Añadir **nivel 0.5: todos los handlers en proceso con cola en memoria** (§7) |

## 2. Principios

1. **Un dominio, muchos adaptadores** (hexagonal): reglas en Python puro sin red,
   AWS, Telegram, navegador ni LLM.
2. **Código monolítico, despliegue por perfil de recursos:** un paquete Python,
   varios handlers; workers en el lenguaje que exige su librería (Node para
   OpenCLI/Playwright/Sparticuz, Go para whatsmeow y age).
3. **Contratos versionados en el borde:** todo lo que cruza un proceso es JSON con
   esquema, `schema_version` e idempotency key.
4. **Determinístico primero:** el LLM es un puerto opcional con caché, nunca
   decide efectos externos.
5. **Mutable en DynamoDB, inmutable en S3, secretos en SSM.**
6. **Efectos externos con ledger:** intención registrada antes, verificación de
   lease justo antes, resultado o `send_uncertain` después.
7. **Nada encendido 24/7** y nada que salga de Always Free sin alerta.

## 3. Vista de componentes

```text
                         ┌───────────────────────── Telegram ─────────────────────────┐
                         │  colaboradores / propietario  (comandos, botones, informes) │
                         └───────────────┬───────────────────────────▲────────────────┘
                                         │ webhook (secret_token)     │ Bot API
┌─────────────────────────── AWS (us-east-1) ─────────────────────────┼───────────────────┐
│                                                                      │                   │
│  bot (Py, Function URL) ──► commands.fifo ──► app (Py) ──────────────┤ notifier (en app) │
│        │ dedupe/auth/ack                       │ casos de uso        │                   │
│        ▼                                       │                     │                   │
│   DynamoDB ◄───────────────────────────────────┼──► S3 (eventos, sesiones .age,          │
│   (leases, ledger,                             │       informes)                         │
│    idempotencia, memoria)                      │                                         │
│                                   ┌────────────┴─────────────┐                           │
│                                   ▼                          ▼                           │
│                          browser.fifo                  whatsapp.fifo                     │
│                                   │                          │                           │
│                     browser worker (Node 24)      whatsapp worker (Go, al2023)           │
│                     Chromium + Playwright         whatsmeow pair/sync/send               │
│                     + OpenCLI CDP loopback        SQLite en /tmp ⇄ S3 .age               │
│                                   │                          │                           │
│                                   └──────────► results ◄─────┘                           │
│                                                  │                                       │
│                                                  ▼                                       │
│                                              app (Py): normaliza → match → estima        │
│                                                  → memoria → banda → alerta              │
│  EventBridge Scheduler ──► app (búsquedas guardadas, syncs, resúmenes, salud)            │
│  SSM Parameter Store: token bot, secreto webhook, identidades age, clave OpenRouter      │
│  OpenRouter (externo): solo desde app, vía LLMPort, con caché                            │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

Unidades de despliegue (cinco funciones, tres colas FIFO + una estándar):

| Función | Lenguaje / runtime | Memoria inicial | Disparo | Responsabilidad |
|---|---|---|---|---|
| `bot` | Python, arm64 | 256 MB | Function URL | Validar, deduplicar, autorizar, acusar, encolar comando |
| `app` | Python, arm64 | 512 MB | `commands.fifo`, `results`, Scheduler | Casos de uso, dominio, memoria, informes, notificaciones, LLM |
| `browser` | Node 24, x86 (arm64 en experimento) | 1 600 MB (medir) | `browser.fifo` | Lectura web con estado cargado; sin dominio |
| `whatsapp` | Go, `provided.al2023` arm64 | 256 MB | `whatsapp.fifo` | pair/sync/send con lease; sin dominio |
| `sessions-admin` | Go (CLI local, no Lambda) | — | Manual | prepare/renew/share de bundles age (evoluciona `lab/sessions`) |

`notifier` vive dentro de `app`: separar una función más no compensa a este
volumen. Si `app` crece demasiado (por ejemplo, DuckDB para informes), los
informes se separan en su propia función.

## 4. Módulos y puertos (código Python)

```text
src/radar/
  domain/                 # Python puro, sin I/O
    core.py               # Entity, Signal, Thesis, Evidence, Scenario, Opportunity,
                          # ProposedAction, Outcome, Money(Decimal), FxRate(fechada)
    states.py             # estados de oportunidad y de acción; transiciones válidas
    verticals/
      products.py         # normalize, match, score, allowed_actions
      viral.py            # segunda vertical (prueba de generalidad)
  application/            # casos de uso; solo dependen de domain y ports
    run_saved_search.py   evaluate_signals.py   propose_action.py
    approve_action.py     record_feedback.py    build_report.py
    pair_whatsapp.py      handle_worker_result.py  reconcile_uncertain.py
  ports/                  # typing.Protocol, sin implementación
    sources.py      SourceGateway.request_read(...)        -> job_id
    messaging.py    MessagingGateway.pair/sync/send(...)   -> job_id
    ui.py           UserInterface.send/edit/answer_callback(...)
    store.py        LeaseStore, Ledger, IdempotencyStore, MemoryRepo, SessionPointer
    blobs.py        BlobStore (inmutable por clave)
    events.py       EventLog.append(event)
    llm.py          StructuredLLM.extract(schema, text, privacy)
    clock.py fx.py secrets.py capabilities.py
  adapters/
    aws/            dynamodb.py  s3.py  sqs.py  ssm.py
    local/          sqlite.py  filesystem.py  memory_queue.py  env_secrets.py
    telegram/       bot_api.py (HTTPS directo)  webhook.py (parseo/validación)
    openrouter/     client.py (response_format json_schema, zdr, caché)
    workers/        browser_client.py  whatsapp_client.py  (publican a la cola)
  entrypoints/
    lambda_bot.py   lambda_app.py   cli.py  (modo local con adaptadores local/)
contracts/          # JSON Schema + ejemplos dorados, compartidos por los 3 lenguajes
  envelope.v1.json  browser.read.v1.json  browser.result.v1.json
  whatsapp.op.v1.json  whatsapp.result.v1.json  session.manifest.v1.json
  capabilities.json  config.v1.json  examples/
workers/browser/    # Node: evoluciona lab/browser (mismo handler, eventos reales)
go/                 # un módulo Go
  cmd/whatsapp/     # Lambda whatsmeow
  cmd/sessions/     # CLI age (desde lab/sessions)
  internal/vault/   # código age compartido por ambos
infra/sam/template.yaml
tests/              # ver §7
lab/                # se conserva como arnés de benchmark; no es producción
```

Reglas de dependencia (verificables con un test que importa módulos):
`domain` no importa nada del proyecto; `application` importa `domain` y `ports`;
`adapters` implementan `ports`; solo `entrypoints` arman adaptadores concretos.

**Ponytail:** no crear todo de golpe. Orden en §9; cada carpeta nace cuando su
primer test la necesita.

## 5. Interconexiones y contratos

### 5.1 Sobre común (todas las colas)

```json
{
  "schema_version": 1,
  "type": "browser.read | whatsapp.sync | whatsapp.send | whatsapp.pair | result | command",
  "id": "ULID; también clave de idempotencia",
  "correlation_id": "run o conversación que agrupa",
  "session_ref": "whatsapp:radar-pilot (alias, nunca secreto)",
  "lease": {"owner": "job id", "token": 42, "expires_at": "RFC3339"},
  "deadline": "RFC3339",
  "payload": {}
}
```

- `MessageGroupId` = `session_ref` en las colas FIFO: un trabajo por cuenta a la vez.
- `MessageDeduplicationId` = `id`.
- Visibilidad ≥ 6 × timeout; DLQ con alarma; `ReportBatchItemFailures` activado.
- Los resultados vuelven a `results` (estándar) con el mismo `correlation_id`.
  Errores tipados: `needs_reauth`, `unsupported`, `rate_limited`, `blocked`,
  `empty_verified`, `timeout`, `send_uncertain`, `session_conflict`.

### 5.2 Modelo DynamoDB (tabla única)

| PK | SK | Uso |
|---|---|---|
| `SESSION#<ref>` | `LEASE` | owner, `token` monotónico, `expires_at`; escritura condicional |
| `SESSION#<ref>` | `PTR` | versión activa del bundle `.age` en S3 (CAS sobre `version`) |
| `OP#<id>` | `LEDGER` | `intent → approved → dispatched → sent / uncertain / failed / reconciled` |
| `UPD#<update_id>` | `TG` | idempotencia del webhook (TTL 48 h) |
| `SIG#<hash>` | `SEEN` | deduplicación de anuncios y último precio (bi-temporal en el evento) |
| `USER#<tg_id>` | `PROFILE` | rol, consentimiento versionado, preferencias, chats habilitados |
| `CONTACT#<seudónimo>` | `LEDGER` | "nunca contactar dos veces" (B-I09) |
| `LLM#<hash>` | `CACHE` | respuesta estructurada del modelo (TTL) |

Modo provisionado dentro de Always Free (B-F039). Powertools Idempotency puede
usar esta misma tabla.

### 5.3 Efecto externo seguro (responde R-013 / A-F27)

```text
app: aprobar ──► Ledger OP# = approved (condicional: estado previo = intent)
app: encolar whatsapp.send {id, lease esperado}
worker:
  1. adquirir/renovar LEASE (condicional) → token T
  2. Ledger approved→dispatched con token T (condicional)
  3. re-leer LEASE: si token ≠ T o vencido → abortar sin enviar
  4. enviar
  5a. respuesta OK  → Ledger sent (+ id de mensaje del proveedor)
  5b. timeout/caída → Ledger queda dispatched → reconciliador lo pasa a uncertain
app: reconcile_uncertain: buscar el mensaje por chat/ID en el próximo sync;
     encontrado → sent; no encontrado → preguntar al usuario. Nunca reenvío automático.
```

Coincido con A: el `If-Match` de S3 y el token no pueden detener un envío que el
proveedor ya aceptó. El token reduce la ventana (paso 3) y el ledger hace visible
el caso incierto. El estado SQLite de whatsmeow se sube con un backup consistente
(cerrar el cliente y hacer checkpoint del WAL), nunca copiando el archivo activo
(A-F27).

### 5.4 Flujos principales

1. **Búsqueda guardada:** Scheduler → `app.run_saved_search` (preflight con
   `capabilities.json`, presupuesto) → `browser.fifo` → worker (carga
   `.age` del S3 descifrado con identidad de SSM; OpenCLI o lectura directa) →
   `results` → `app.handle_worker_result` → normalizar/match/estimar (dominio) →
   memoria → banda → alerta Telegram con botones.
2. **Vincular WhatsApp:** `/vincular` → `bot` → `commands.fifo` →
   `app.pair_whatsapp` → `whatsapp.fifo` (pair) → worker conecta y pide
   `PairPhone` → `results` (código) → `app` → Telegram con `protect_content`
   → usuario ingresa código → worker `PairSuccess` → verifica que el JID
   coincide con el número declarado (si no, `Logout` y rechazo) → sube `.age`
   v1 → `PTR`.
3. **Aprobar consulta de precio:** botón → `bot` (idempotencia `update_id`) →
   `commands.fifo` → `app.approve_action` → §5.3.
4. **Resumen diario:** Scheduler → `app.build_report` (DuckDB sobre eventos S3 o
   consultas DynamoDB) → `sendMessage` + `sendDocument`.
5. **Renovar sesión web:** CLI local `sessions renew` (captura manual) → sube
   `.age` vN+1 a S3 → CAS en `PTR` → los workers leen la versión nueva en la
   próxima invocación.

## 6. Buenas prácticas por capa

- **Dominio:** `Decimal` y tasas fechadas; tipos inmutables (`dataclass(frozen=True)`);
  transiciones de estado explícitas; ningún `datetime.now()` directo (ClockPort).
- **Aplicación:** cada caso de uso es idempotente por `id`; devuelve eventos que
  se anexan al EventLog; nada de efectos fuera de puertos.
- **Workers:** sin lógica de negocio; validan entrada con el esquema; devuelven
  campos crudos + evidencia (URL, fecha, hash) y errores tipados; deadline
  interno menor que el timeout de Lambda; limpieza en `finally` (lab ya lo hace).
- **Seguridad:** un rol IAM por función; solo `bot` es público; secretos en SSM y
  cacheados; redactar el token de Telegram y cualquier URL con credenciales;
  contenido externo es dato, nunca instrucción; datos personales seudonimizados
  con clave por persona (B-D035).
- **Costos:** arm64 donde se pueda; memoria medida, no adivinada; frecuencia
  adaptativa; logs 7–14 días; sin métricas personalizadas; alerta Budgets.
- **Observabilidad:** logs JSON con `correlation_id` en los tres lenguajes;
  EventLog en S3 como auditoría; informe de salud diario por Telegram.
- **Versionado:** `schema_version` en contratos, config y bundles; el modelo LLM
  fijado por ID; dependencias fijadas por lockfile y digest (como el lab).

## 7. Estrategia de pruebas

Extiende los niveles de LOCAL_LAMBDA_TESTING (0–5) sin repetirlos.

| Nivel | Qué prueba | Herramienta | Cuándo |
|---|---|---|---|
| 0 Dominio | Reglas, dinero, FX, matching, bandas, transiciones | pytest + Hypothesis (propiedades: suma de costos, desconocido nunca = 0, banda monótona) | Cada cambio |
| 0 Arquitectura | `domain` no importa adaptadores ni librerías de red | test que recorre imports | Cada cambio |
| 0 Contratos | Ejemplos dorados válidos e inválidos contra cada JSON Schema, **en los tres lenguajes** | `jsonschema` (Py, MIT), `ajv` (Node, MIT), `santhosh-tekuri/jsonschema` (Go, Apache-2.0) | Cada cambio |
| 0.5 Flujo en proceso | Bot → app → worker falso → resultado → alerta, con cola en memoria, SQLite y relojes falsos | pytest; adaptadores `local/` | Cada cambio |
| 1 Conformidad de adaptadores | La **misma** suite de `LeaseStore`, `Ledger`, `BlobStore` corre contra el falso local y contra DynamoDB Local / moto | DynamoDB Local (Docker, gratuito), moto (Apache-2.0) | Antes de merge |
| 1 Telegram | Updates grabados (sintéticos): secreto inválido → 401, `update_id` repetido, usuario no autorizado, callback ajeno, doble "Aprobar", 429 con `retry_after` | pytest con cliente Bot API falso | Antes de merge |
| 1 Workers | Browser: selftest y fixture del lab. WhatsApp: interfaz del cliente whatsmeow falseada (pair/sync/send, `LoggedOut`, `StreamReplaced`) | node --test, go test | Antes de merge |
| 2 Fallos | Caída después de enviar y antes del ledger; lease vencido en medio; mensaje SQS duplicado; DLQ; backup SQLite con WAL | pytest + go test con inyección de fallos | Antes de merge |
| 3 Runtime local | `sam local invoke` y RIE con límites (ya planificado y en parte probado) | SAM CLI (Apache-2.0), Docker | Al tocar handlers |
| 4 Cuenta de ensayo | Lectura real o vinculación WhatsApp con cuenta propia | Manual, autorizado | Con encargo separado |
| 5 Canary AWS | IAM, colas, costos reales, aceptación de IP | Despliegue mínimo + Budgets | Con encargo separado |
| Eval | Calidad de extracción y del LLM en español venezolano (EVALUATIONS.md) | Conjunto reservado, gates, presupuesto OpenRouter | Fase 4 |

CI (GitHub Actions ya existe para docs): niveles 0, 0.5 y 1 sin Docker pesado en
cada PR; DynamoDB Local en un job aparte; suites Docker del lab manuales o
nocturnas solo si se piden. Nunca credenciales reales en CI.

Licencia de Hypothesis: MPL-2.0 según su repositorio (el detector de GitHub
devuelve NOASSERTION); es dependencia solo de desarrollo, no se redistribuye.

## 8. Qué se elimina o se aplaza (YAGNI)

- AES propio de sesiones → age.
- Leases en S3 → DynamoDB.
- Dashboard web y Mini App → Telegram (Mini App solo si los Rich Messages no alcanzan).
- Step Functions, API Gateway, NAT, VPC, Secrets Manager, RDS, vectores, Matrix,
  Beeper Desktop → fuera del MVP.
- Durable functions → solo cuando exista seguimiento de consultas con espera larga.
- Función `notifier` y función `reports` separadas → dentro de `app` hasta que
  el tamaño o la memoria lo justifiquen.
- Pact u otros brokers de contratos → JSON Schema + ejemplos dorados basta.

## 9. Orden de implementación sugerido

Cada paso deja pruebas verdes y no requiere cuentas reales hasta el paso 7.

1. `contracts/` (sobre, browser, whatsapp, capacidades, config) + validación en
   los tres lenguajes. Adaptar `lab/browser` a `browser.read.v1`/`browser.result.v1`.
2. `domain/core.py` + `verticals/products.py` + nivel 0 (Fase 1 del ROADMAP con
   los nombres del núcleo general).
3. `ports/` + `adapters/local/` + caso de uso `run_saved_search` y
   `handle_worker_result` + nivel 0.5 con worker falso.
4. `adapters/telegram/` + `entrypoints/lambda_bot.py` + suite Telegram nivel 1.
5. `adapters/aws/` (DynamoDB, S3, SQS, SSM) + conformidad contra DynamoDB Local
   y moto + ledger/lease §5.3 con pruebas de fallos.
6. `go/` unificando `lab/sessions` (vault age) y el worker WhatsApp con cliente
   falseado; `infra/sam/template.yaml`; `sam local invoke`.
7. Con autorización: cuenta de ensayo WhatsApp (P1/P2), canary AWS con Budgets,
   primera fuente real de lectura.
8. Segunda vertical (viral) solo con fixtures, para comprobar que el núcleo es
   general sin tocar `domain/core.py`.

## 10. Preguntas abiertas para el coordinador

- ¿Acepta unificar el cifrado en age (lab) y retirar el AES de B-F017?
- ¿Acepta DynamoDB como único almacén mutable (sustituye `registry.json` local en
  la nube y los leases S3)?
- ¿Adopta `src/radar/` + `contracts/` + `go/` + `workers/browser/` como
  estructura, conservando `lab/` como arnés?
- ¿Actualiza ARCHITECTURE.md (Telegram, Lambda, DynamoDB) y AGENTS.md (alcance
  general, B-D027) cuando el usuario lo confirme?
