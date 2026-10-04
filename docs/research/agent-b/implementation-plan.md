# Plan de implementación de inicio a fin y trabajo en paralelo A/B

Autor: Claude (frente B). Fecha: 2026-10-03. Estado: **propuesta**; nada de esto
está implementado. Base: [arquitectura final de A](../architecture-final-review.md)
y [propuesta B](architecture-proposal.md). El coordinador decide qué integrar.

## 1. Revisión de la arquitectura final de A

**Dictamen B:** acepto la arquitectura final de A como base de implementación.
Corrige errores reales de mi propuesta:

- El lease no viaja en el mensaje: el worker lo adquiere al empezar.
- Outbox transaccional entre guardar y encolar.
- HTTP antes que Chromium.
- Ledger de contacto acotado por propietario, destinatario y propósito.
- Informes JSON + Telegram antes que DuckDB/Jinja.

Retiro de mi propuesta lo que contradice esos puntos (B-D061).

Cuatro refinamientos, todos verificados el 2026-10-03:

| # | Refinamiento | Evidencia | Efecto |
|---|---|---|---|
| R1 | El consumidor de DynamoDB Streams en `app` puede **procesar los comandos directamente** y publicar en SQS solo las tareas de workers. Se elimina `commands.fifo` y un salto | Lambda no paga `GetRecords` de Streams cuando lo invoca un trigger; Streams ordena por ítem y, con `ParallelizationFactor`, agrupa por clave de partición dentro del shard ([DynamoDB pricing](https://aws.amazon.com/dynamodb/pricing/), [Lambda con DynamoDB](https://docs.aws.amazon.com/lambda/latest/dg/with-ddb.html)) | 3 colas en vez de 4; menos código. Decidir con la prueba 0.5/1; si el reintento por shard complica, volver a `commands.fifo` |
| R2 | **Techos duros gratuitos** además de Budgets: concurrencia reservada por función, límite mensual en la clave de OpenRouter y reservas de presupuesto en la app (propuesta A) | Concurrencia reservada "no genera cargos adicionales" y fija máximo y mínimo ([Lambda concurrency](https://docs.aws.amazon.com/lambda/latest/dg/configuration-concurrency.html)); las claves OpenRouter aceptan `limit` con `limit_reset: monthly` ([API keys](https://openrouter.ai/docs/api-reference/api-keys/create-api-key)) | Budgets sigue como alerta tardía; los techos impiden el gasto descontrolado |
| R3 | **Alcance:** el usuario pidió en chat un modelo general. Implementar solo productos, pero con los nombres del núcleo (`Entity`, `Signal`, `Opportunity`…) desde el primer commit | Mensaje del usuario 2026-10-03; A pide no cambiar alcance en silencio | Costo cero hoy; registrar como decisión del usuario pendiente de formalizar en AGENTS/VISION |
| R4 | Las lecturas HTTP de redes sociales también pasan por el registro de capacidades: "HTTP primero" no significa "HTTP permitido" | Términos por plataforma (informe A); SECURITY.md | El preflight elige la ruta según capacidad y permiso, no según velocidad |

## 2. Metodología para que A y B trabajen a la vez sin chocar

Fuentes consultadas el 2026-10-03:
- Claude Code permite sesiones paralelas con git worktrees: cada worktree es un
  checkout propio en su rama y requiere al menos un commit
  ([Claude Code](https://code.claude.com/docs/en/common-workflows#run-parallel-sessions-with-worktrees)).
- Ramas cortas: un desarrollador por rama y "no más de un par de días"
  ([trunkbaseddevelopment.com](https://trunkbaseddevelopment.com/short-lived-feature-branches/)).
- DORA recomienda tres o menos ramas activas y lotes pequeños integrados con
  frecuencia ([DORA](https://dora.dev/capabilities/trunk-based-development/)).
- Agentes paralelos: "un agente, un worktree, una rama, un dueño de tarea, una
  entrega revisable", y tratar los archivos calientes como recursos agendados
  ([guía](https://codingagentguide.com/posts/worktree-isolation-rules-for-parallel-coding-agents/)).
- Contract-first/schema-first para equipos paralelos
  ([ejemplo](https://www.tombee.io/blog/schema-first-development)).
- CODEOWNERS pide revisión automática al dueño de los archivos tocados
  ([GitHub](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)).
- Merge queue solo existe en repos públicos de organizaciones o en Enterprise
  ([GitHub](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue)).
  Este repo personal probablemente no la tiene (`pend`), así que el merge es
  serial y manual.

### Método propuesto: trunk-based + contratos primero + dueños por puerto

1. **Base común:** todo arranca desde un commit de `main`. Hoy hay mucho trabajo
   sin commitear (lab, docs, investigación). Los worktrees no lo verían.
   **Paso 0:** commitear la línea base en una rama `codex/`, verificarla y
   fusionarla a `main`, con autorización del usuario (AGENTS.md).
2. **Aislamiento:** un agente = un worktree = una rama = una tarea.
   - Ramas `codex/a-<tarea>` (Codex) y `codex/b-<tarea>` (Claude), respetando el
     prefijo de AGENTS.md.
   - Worktrees fuera del checkout principal (por ejemplo `../mor-a-<tarea>`). El
     checkout principal no se edita mientras haya worktrees activos.
3. **Contratos primero:** antes de dividir el trabajo se fusiona un PR pequeño
   con `contracts/*.v1.json`, ejemplos dorados y `src/radar/ports/` (puertos =
   contratos internos). Desde ahí cada uno trabaja contra esquemas y fakes, no
   contra el código del otro. Cambiar un contrato = PR propio, con versión nueva
   y revisión de ambos.
4. **Separación por puertos (hexagonal):** cada frente es dueño de módulos
   completos detrás de un puerto. Nadie edita los internos del otro.
5. **Archivos calientes con un solo dueño** (tabla §3). Si una tarea necesita uno
   ajeno, primero se pide un PR mínimo al dueño.
6. **Tablero con reclamos:** `docs/work/BOARD.md` (o GitHub Issues). Cada tarea
   lleva ID, dueño, rama, archivos reclamados, dependencias y estado. Se reclama
   antes de empezar y se libera al fusionar. Recursos compartidos que también se
   reclaman: Docker pesado (benchmarks secuenciales), cuentas de ensayo y AWS.
7. **Ramas cortas:** como máximo 1–2 días y como máximo tres ramas activas. Si
   crece, se parte en PRs más chicos.
8. **Flags:** el trabajo incompleto entra a `main` detrás de
   `enabled: false` (el patrón ya existe en `config/radar.example.json`).
   Así se integra seguido sin activar nada.
9. **Integración serial:** rebase sobre `main` → suite completa local
   (`pytest`, `go test`, `node --test`, `check_docs`) → CI verde → revisión del
   otro agente o CODEOWNERS → merge por el coordinador con autorización del
   usuario. Un merge a la vez.
10. **Entrega:** cada PR declara instrucciones seguidas (AGENTS.md), contratos y
    versión tocados, pruebas ejecutadas con salida, archivos reclamados y qué
    queda `pend`.
11. **Cierre:** al fusionar se decide mantener, borrar o archivar el worktree y
    la rama; no quedan worktrees huérfanos.
12. **Sincronía por documentos:** al empezar cada bloque, cada agente lee
    BOARD.md y `decisions.md` (como ya hacen con los informes).

## 3. Mapa de propiedad propuesto

| Área | Dueño | Revisor | Notas |
|---|---|---|---|
| `contracts/` | B (autor de v1) | A | Archivo caliente; cambios solo por PR de contrato |
| `src/radar/ports/` | A | B | Archivo caliente; nace en el PR de contratos |
| `src/radar/domain/`, `application/` | A | B | Núcleo y vertical productos |
| `src/radar/adapters/local/`, `adapters/aws/` | A | B | Outbox, ledger, leases, conformidad |
| `src/radar/adapters/sources/` (HTTP) | A | B | SSRF, cuotas |
| `workers/browser/` (desde `lab/browser`) | A | B | A construyó el lab |
| `src/radar/adapters/telegram/`, `entrypoints/lambda_bot.py` | B | A | Guía Telegram + Lambda |
| `go/` (vault desde `lab/sessions` + worker WhatsApp) | B | A | B investigó whatsmeow; reutiliza el helper age de A |
| `src/radar/adapters/openrouter/` | B | A | Después de la línea base determinística |
| `infra/sam/` | A | B (seguridad/IAM) | Archivo caliente |
| `.github/workflows/`, lockfiles (`pyproject`, `go.mod`, `package-lock`) | Dueño del área; workflows A | El otro | Archivos calientes |
| README, ROADMAP, AGENTS, decisions, ARCHITECTURE | A (coordinador) | B | B propone en su informe |
| `docs/research/agent-b*` | B | — | Igual que hoy |

## 4. Checklists de inicio a fin

Leyenda: **[U]** requiere decisión o autorización del usuario. A/B indica el
dueño. Cada fase termina con su "Hecho cuando".

### F0 — Gobierno y línea base
- [ ] [U] Confirmar decisiones de arquitectura: age, DynamoDB, outbox,
      estructura, R1–R4 (A integra en `decisions.md`).
- [ ] [U] Formalizar el alcance: productos ahora, núcleo con nombres generales
      (B-D027/R3); A actualiza AGENTS/VISION si el usuario confirma.
- [ ] [U] Responder B-Q006 (respaldo local si AWS rechaza sesiones).
- [ ] [U] Autorizar commit de la línea base y merge a `main` (A).
- [ ] Crear `docs/work/BOARD.md` y `CODEOWNERS` según §3 (A).
- [ ] CI: ampliar `.github/workflows` con pytest/go/node en PR (A).
- Hecho cuando: `main` contiene la línea base, CI verde y tablero creado.

### F1 — Contratos y puertos (bloqueante, una rama)
- [ ] Sobre v1 con `message_id`, `operation_id`, `correlation_id`,
      `causation_id`, `owner_ref`, `kind`, `deadline`, `attempt` y versión
      esperada (B).
- [ ] Esquemas: `browser.read`/`result`, `whatsapp.pair`/`sync`/`send`/`result`,
      `telegram.command`, `session.manifest`, `capabilities`, `config` (B).
- [ ] Ejemplos válidos y negativos; validación en Python, Node y Go (B).
- [ ] `ports/` como `Protocol`: repositorio/UoW, outbox, ledger, lease,
      blobs, cola, UI, LLM, reloj, secretos, capacidades (A).
- Hecho cuando: los tres lenguajes validan los mismos ejemplos y el PR se fusiona.

### F2 — Dominio productos (A) ‖ F4 Telegram (B) ‖ F5 Go (B) — en paralelo

**F2 Dominio (A)**
- [ ] `Money` Decimal, `FxRate` fechada, núcleo general, vertical productos.
- [ ] Estados de oportunidad y de acción con transiciones válidas.
- [ ] Pruebas nivel 0 con Hypothesis (desconocido ≠ 0, sumas, bandas monótonas).
- [ ] Test de arquitectura: `domain` no importa adaptadores.

**F4 Telegram (B)**
- [ ] Adaptador Bot API por HTTPS: `sendMessage`, `editMessageText`,
      `answerCallbackQuery`, `sendDocument`, `setWebhook`, `setMyCommands`;
      token redactado en logs.
- [ ] Webhook: secreto, tamaño, `update_id` idempotente, lista blanca y roles,
      `callback_data` opaco, acuse en la respuesta HTTP.
- [ ] Comandos MVP y deep link de invitación de un solo uso.
- [ ] Suite nivel 1 con updates sintéticos (401, duplicado, usuario ajeno,
      callback ajeno, doble aprobación, 429).

**F5 Go (B)**
- [ ] Módulo `go/` con `internal/vault` desde `lab/sessions`, conservando sus
      pruebas y el handoff.
- [ ] `cmd/whatsapp`: interfaz del cliente whatsmeow falseada; pair (código),
      sync (`OfflineSyncCompleted`), send con claim en ledger; errores
      `LoggedOut`/`StreamReplaced`.
- [ ] Snapshot SQLite consistente (cierre/checkpoint WAL) y subida como blob
      versionado.
- [ ] Validación de contratos en Go.

- Hecho cuando: cada rama pasa su suite y se fusiona por separado.

### F3 — Flujo local completo (A, con integración de B)
- [ ] `adapters/local/`: SQLite con transacción recibo + comando + outbox,
      ledger, leases, cola en memoria.
- [ ] Casos de uso `run_saved_search`, `handle_worker_result`, `approve_action`,
      `reconcile_uncertain`.
- [ ] Nivel 0.5: Telegram falso → bot → app → worker falso → resultado → alerta.
- [ ] Fallos obligatorios de A §8.4 (commit sin enqueue, resultado sin ACK,
      replay, lease vencido, caída antes y después del envío, presupuesto
      agotado…).
- Hecho cuando: ninguna tarea aceptada se pierde y ninguna incertidumbre se reenvía.

### F6 — Worker navegador (A)
- [ ] `workers/browser/` desde `lab/browser`, con entrada y salida por contrato.
- [ ] Ruta HTTP primero en `app`; Chromium solo si la capacidad lo exige.
- [ ] Carga de bundle age vía el helper Go (pipes acotados), sin cripto en Node.
- [ ] Benchmark HTTP vs navegador con el runner del lab.

### F7 — Adaptadores AWS (A; B revisa)
- [ ] DynamoDB (tabla única, provisionada), S3 (blobs inmutables), SQS, SSM.
- [ ] Outbox con Streams (probar R1) y reparación paginada sin Scan.
- [ ] Suites de conformidad: la misma batería contra SQLite y contra DynamoDB
      Local/moto.
- [ ] Powertools solo para utilidades técnicas; nunca envolver `send` en un
      decorador que lo reintente.

### F8 — Infraestructura y techos (A; B revisa seguridad)
- [ ] `infra/sam/template.yaml`: 4 funciones, colas + DLQ, tabla, bucket, roles
      IAM por función, Function URL solo para `bot`.
- [ ] Permisos `lambda:InvokeFunctionUrl` + `lambda:InvokeFunction`.
- [ ] Concurrencia reservada por función (R2), `MaximumConcurrency` ≥ 2 en las
      colas, visibilidad ≥ 6 × timeout, retención de logs.
- [ ] `sam build` + `sam local invoke` con eventos sintéticos.

### F9 — Seguridad y privacidad (B; A revisa)
- [ ] Actualizar el modelo de amenazas de SECURITY.md: webhook, SSRF, tokens,
      sesiones age, datos personales, Venezuela (B-F026).
- [ ] Consentimiento versionado, `/mis_datos`, `/borrar`, `/stop` con `Logout`.
- [ ] Seudonimización con clave por persona; chats no habilitados descartados en
      memoria.
- [ ] [U] Revisión por abogado venezolano antes de vincular colaboradores.

### F10 — Pre-despliegue y canary
- [ ] [U] Confirmar el programa del free tier en Billing.
- [ ] [U] Alerta de Budgets creada.
- [ ] Checklist de 10 pasos de la [guía](telegram-lambda-best-practices.md) §5.
- [ ] [U] Autorizar el canary AWS con presupuesto explícito (bot de pruebas,
      sin cuentas reales).
- Hecho cuando: canary con costos reales medidos e IAM Access Analyzer limpio.

### F11 — Piloto WhatsApp
- [ ] [U] Número dedicado del radar y autorización de emparejamiento.
- [ ] P1: emparejar por código vía Telegram; JID verificado; sesión cifrada.
- [ ] P2: solo recepción durante 14 días; medir `LoggedOut`, duración y costo.
- [ ] [U] P3: autorización separada para enviar; salvaguardas B-I09; primero
      cuentas propias.
- [ ] [U] P4: colaboradores tras la revisión legal (F9).

### F12 — Primera fuente real y evaluación (A)
- [ ] [U] Fuente y cuenta de ensayo autorizadas; captura manual con
      `sessions-admin`.
- [ ] Prueba de lectura inocua desde AWS (aceptación de IP, R-014).
- [ ] Dataset de evaluación con fixtures revisados (EVALUATIONS.md).

### F13 — IA opcional (B)
- [ ] Puerto LLM + adaptador OpenRouter: `json_schema`, `zdr`,
      `data_collection: deny`, caché por contenido + modelo + esquema + privacidad.
- [ ] [U] Clave con límite mensual; evaluar 2–3 modelos en español venezolano.
- [ ] Activación detrás de flag, solo si mejora frente a la línea base.

### F14 — Memoria, informes y operación (A/B)
- [ ] Feedback de botones → calibración de bandas (A).
- [ ] Resumen diario JSON + Telegram con cobertura y salud (B).
- [ ] Runbooks: `needs_reauth`, token filtrado, DLQ, `send_uncertain`, renovar
      sesión, rotar claves (B).
- [ ] Cadencia de actualización de dependencias y de la guía (§6 de la guía).

### F15 — Segunda vertical (después de F12 y con alcance confirmado)
- [ ] [U] Confirmación de alcance general.
- [ ] Vertical viral con fixtures, sin tocar `domain/core.py`.

## 5. Primera división en paralelo

```text
F0 (A, usuario) ──► F1 contratos+puertos (B autor, A puertos) ──┬─► F2 dominio (A)
                                                                ├─► F4 Telegram (B)
                                                                └─► F5 Go (B)
F2 + F4 + F5 ──► F3 flujo local (A integra) ──► F6/F7 (A) ‖ F9 (B) ──► F8 ──► F10 ──► F11/F12
```

B tiene dos ramas posibles a la vez (F4 y F5). Para respetar "tres ramas
activas o menos", B las hace en secuencia: primero F4, que desbloquea la
interfaz, luego F5.

## 6. Definición de hecho (cada PR)

- Pruebas del área en verde, más `check_docs.py`; salida pegada en el PR.
- Contratos y versiones respetados; ningún secreto en código, logs ni fixtures.
- Sin funcionalidad activa sin flag; nada que contacte cuentas reales.
- Documentación del área actualizada; los `pend` declarados.
- Revisado por el otro agente (o CODEOWNERS) y fusionado por el coordinador con
  autorización del usuario.
