# Arquitectura de implementación

Actualizado: 2026-10-03, tarea A0. Dirección: monolito modular hexagonal,
**cuatro Lambdas por perfil de I/O y un CLI local**, con implementación local
primero. La línea base contiene documentación y el laboratorio experimental;
ningún componente cloud ni conector comercial está desplegado.
Ver [revisión final A](research/architecture-final-review.md),
[decisiones y autoridad](research/decisions.md),
[plan A/B](research/agent-b/implementation-plan.md) y [tablero](work/BOARD.md).

## Componentes y responsabilidades

| Unidad prevista | Tecnología | Responsabilidad |
|---|---|---|
| `bot` Lambda | Python | Webhook Telegram: secreto, tamaño, usuario/roles y recepción durable; no crawling ni LLM |
| `app` Lambda | Python | Casos de uso, lotes HTTP permitidos, resultados, outbox, reparación, comparación/costos e informes |
| `browser` Lambda | Node + Playwright/OpenCLI | Lecturas que requieren JavaScript/CDP, datos crudos y evidencia por contrato |
| `whatsapp` Lambda | Go + whatsmeow | Pair/sync/send, protección técnica de operaciones y snapshot de protocolo |
| `sessions-admin` local | Go + age | Preparar/renovar estado exportado explícitamente; no extraer cookies ni autenticar automáticamente |

Telegram es la **única interfaz de producto del MVP**. El CLI local administra
sesiones y pruebas; no es segunda UI comercial. Dashboard/Mini App y otras
verticales quedan fuera. Productos es la primera vertical; nombres como Entity,
Signal y Opportunity se introducen por casos concretos, no como framework universal.

## Flujo durable previsto

```text
Telegram -> bot -> transacción: receipt + command + outbox
                                    |
                          app: relay idempotente
                                    v
                            commands.fifo -> app
                                              |
                      +-----------------------+---------------------+
                      |                       |                     |
                 HTTP/feed               browser.fifo         whatsapp.fifo
              lotes permitidos                |                     |
                  en app                    Node                    Go
                      +--------- resultado durable + outbox --------+
                                              |
                                    results Standard -> app
                                              |
                           normalizar/comparar/estimar -> Telegram
```

Baseline de **cuatro colas de trabajo** más sus DLQ. R1 es un experimento:
comparar ejecución de comandos directamente desde Streams para retirar un salto
y `commands.fifo`. No está implementado ni decidido por un diagrama. Las pruebas
0.5/1 deben demostrar replay, orden por agregado, fallos por shard y reparación;
conservar FIFO si la alternativa complica consistencia. No confiar en orden global
de items del stream ni en `message_id` como único ID de una operación.

Los grupos incluyen propietario: comandos por usuario/agregado, navegador
autenticado por cuenta, público por dominio/shard acotado y WhatsApp por cuenta.
Cuotas compartidas entre shards; crear grupos no multiplica presupuesto. Un
escritor por cuenta con lease adquirido por el worker, no heredado del mensaje.

## Estado y consistencia

- DynamoDB es la autoridad cloud de control: versiones, recibos, ledger,
  outbox, leases, presupuesto y referencias. S3 guarda blobs inmutables de
  evidencia, informes y sesiones cifradas; SSM contiene secretos con mínimo privilegio.
- SQLite ofrece transacciones locales; la base de protocolo whatsmeow sigue
  siendo mutable y propiedad de Go. No se interpreta "DynamoDB único" como
  eliminar ese estado o mover la lógica de protocolo a Python.
- Receipt + command + outbox se guardan atómicamente antes del acuse. El relay
  publica y marca; si cae puede repetir, por lo que el consumidor es idempotente.
  Filtrar eventos de su propia marca y reparar pendientes de forma paginada, sin
  Scan global ni TTL que elimine trabajos no publicados.
- El worker guarda resultado/versión/outbox antes de reconocer la tarea. Un
  replay recupera el resultado; no vuelve a ejecutar un efecto confirmado/incierto.
- Blob nuevo primero, luego CAS del puntero. Una caída puede dejar un huérfano;
  snapshot SQLite debe ser consistente respecto de WAL. No compartir perfil writable.

Contratos JSON Schema versionados y ejemplos dorados comunes a Python/Node/Go.
Dinero Decimal como string + moneda; timestamps UTC; desconocido no es cero.
IDs separados de mensaje, operación, correlación y causación; referencias cifradas
en lugar de contenido privado en colas. Rechazar esquemas desconocidos y campos
no admitidos. La configuración de ejemplo sigue documental y desactivada.

## Efectos externos: diseñados, no habilitados

```text
proposed -> approved -> dispatch_committed -> provider_confirmed
                              |
                              +-> send_uncertain -> reconciliation/review
```

La aprobación liga propietario, cuenta, destinatario, propósito, hash, versión
y vencimiento. Claim transaccional antes del efecto; lease comprobado justo antes
del envío. **No es fencing del proveedor ni garantía exactly-once**. Si falta
confirmación durable, no repetir automáticamente ni borrar ledger al expirar un
lease. Dedupe FIFO de cinco minutos no sustituye historial durable. Cancelar
impide acciones pendientes, no revierte una ya iniciada.

Esta tarea construye diseños/fakes locales; [SECURITY](../SECURITY.md) mantiene
bloqueados mensajes reales, compras, pagos, reservas, publicaciones, follows y
grupos sin autorización separada. WhatsApp necesita dispositivo propio, JID que
coincida con número declarado y prueba real posterior; no clonar el bridge personal.

## Capas previstas

```text
src/radar/domain/             reglas puras, dinero, productos, estados
src/radar/application/        casos de uso y coordinación por puertos
src/radar/ports/              UoW, outbox, ledger, leases, UI, fuentes, reloj
src/radar/adapters/local/     SQLite + fakes/colas de prueba
src/radar/adapters/aws/       DynamoDB, S3, SQS y SSM
src/radar/adapters/telegram/  transporte Bot API
src/radar/adapters/sources/   HTTP/feed y capacidades verificadas
src/radar/entrypoints/        handlers y herramientas técnicas
contracts/                   esquemas comunes y casos válidos/negativos
workers/browser/             Node I/O; helper age por pipes
go/                          protocolo WhatsApp y vault age compartido
infra/sam/                   infraestructura futura, no provisionada
lab/                         fixtures y benchmarks actuales preservados
```

Dominio no importa SDKs/red/adaptadores; aplicación usa dominio y puertos.
Workers no recalculan reglas comerciales. El laboratorio queda intacto como
baseline fixture-only; no abrir sus guardas para declarar una prueba real como fixture.

## Eficiencia, seguridad y costo

HTTP/feed/JSON-LD primero **solo con permiso y capacidad verificados**; Chromium
cuando sea necesario. Pools acotados, conexiones reutilizadas, caché incremental,
ETag/Last-Modified si existen, dedupe antes de lecturas caras. Cursor/continuación
durable por lote, no miles de URLs por handler. Backoff con jitter y circuit breaker;
SSRF, DNS y cada redirect validados; rechazos/MFA producen blocked/needs_reauth.

age protege contenido, no autentica al uploader ni revoca copias antiguas.
Carga autenticada, hash/CAS, claves fuera de Git/logs y permisos por propietario;
import sigue unverified hasta prueba autorizada. Respaldo local en PC permitido
según respuesta reportada B-Q006, no implementado; sin proxies ni evasión.

R2: concurrencia reservada limita ejecuciones simultáneas, **no el acumulado de
invocaciones ni una factura universal**. Presupuesto se reserva atómicamente
por alcance común, no se reinicia por worker/shard; separar trabajos, requests,
tokens y tiempo. Incluir almacenamiento, red, logs y reintentos; límites proveedor
no equivalen a costo USD 0. Free tier/cuenta/canary deben verificarse antes de AWS.
Los límites Docker no reproducen asignación CPU ni facturación Lambda.

IA desactivada por defecto, opcional y evaluada contra baseline determinístico.
Sin fallback pagado ni datos privados implícitos; caché incluye contenido, modelo,
prompt/esquema/versiones, idioma y ámbito. No decide cada acción ni aprueba envíos.
Telemetría JSON sin secretos/PII: cobertura, latencia/cola, RAM, outbox pendiente,
errores, descartes, coste estimado/real diferenciado y oportunidades válidas.

## Verificación incremental

Contratos/dominio e invariantes (nivel 0), flujo falso completo en proceso (0.5),
conformidad local/AWS (1), fallos/replay/incertidumbre (2), runtime Docker/RIE,
y solo tras autorización cuentas reales/canary. Aceptación: ninguna tarea aceptada
perdida en fallos inyectados, ninguna incertidumbre reenviada, aislamiento de
cuenta y presupuesto, informe de cobertura. Son criterios por construir,
no resultados ya alcanzados. Fases y gates en [ROADMAP](ROADMAP.md).
