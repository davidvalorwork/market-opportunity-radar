# Arquitectura final propuesta: revisión A de la entrega B

Fecha: 2026-10-03. Responsable: Codex, coordinador A.
Estado: **propuesta consolidada, no implementación ni aprobación de despliegue**.

Se revisaron los informes A/B, decisiones R-001–R-018, la
[propuesta de Claude](agent-b/architecture-proposal.md), su
[guía Telegram/Lambda](agent-b/telegram-lambda-best-practices.md), documentos de
producto, testing y laboratorio. Las fuentes primarias enlazadas se consultaron
en esta fecha mediante el lector web de Agent Reach y GitHub CLI.

## 1. Dictamen

La base de Claude es adecuada para un piloto pequeño Lambda-first, pero no la
adoptaría literalmente. Las mejoras importantes son de flujo y consistencia,
no añadir frameworks: camino HTTP antes del navegador, outbox transaccional,
coordinación por cuenta y resultados durables. No existe evidencia suficiente
para llamarla la arquitectura más eficiente en cualquier carga o plataforma.

Mantener un **monolito modular de negocio**, cuatro Lambdas por perfil de I/O y
una herramienta local. No son cinco funciones cloud: `sessions-admin` es un CLI.
Python decide; Node y Go ejecutan contratos y aplican protecciones técnicas.
Una protección contra SSRF o un lease en un worker no duplica el dominio.

El encargo actual permite revisar/proponer. No habilita cuentas, contactos,
OpenRouter pagado, AWS ni la ampliación del producto a cualquier vertical.
El radar de productos sigue siendo el alcance vigente de AGENTS/README.

## 2. Decisiones: aceptar, corregir y aplazar

| Frente | Decisión final propuesta | Razón / evidencia |
|---|---|---|
| Cifrado | Adoptar age y reutilizar el helper Go existente | Evita un segundo cifrado propio; handoff sintético ya probado |
| Estado cloud | DynamoDB para control; S3 para blobs inmutables; SSM para secretos | Un lugar autoritativo para versiones, leases, operaciones y outbox |
| Negocio | Python puro; Node/Go como adaptadores | Evita tres implementaciones de dinero, equivalencias y autorización |
| Descubrimiento | HTTP/feed/JSON-LD primero; Chromium solo si hace falta | Menos procesos y memoria; beneficio exacto pendiente de medir |
| OpenCLI | Reutilizar adaptadores útiles; no envolver toda lectura por obligación | El lab observa mayor tiempo/RAM en el fixture; no prueba cuál gana en todas las redes |
| Mensajería interna | Añadir outbox transaccional e idempotencia durable | Cierra el fallo entre guardar y encolar; no promete envío externo único |
| Cuenta | Worker adquiere lease actual, no acepta un token viejo del mensaje | FIFO y CAS no cercan al proveedor externo |
| Contratos | JSON Schema único, versionado y ejemplos dorados | Especificar tipos y errores, no solo prosa; validación en tres lenguajes |
| Testing | Adoptar niveles 0/0.5/1/2 y conservar el lab de rendimiento | Flujo barato en memoria más pruebas de adaptadores y fallos |
| IA | Opcional, desactivada por defecto, presupuesto y evaluación | No usar un modelo para seleccionar cada comando ni aprobar efectos |
| Informes | JSON + resumen Telegram primero | DuckDB/Jinja solo cuando un volumen o formato medido lo justifique |
| Infraestructura | Una región; sin NAT/VPC, panel, vectores ni Step Functions inicialmente | Evita piezas sin necesidad demostrada; revisar si cambia privacidad/conectividad |

La licencia y mantenimiento también importan: GitHub informa age BSD-3-Clause,
whatsmeow MPL-2.0 y Powertools Python MIT-0, con actividad reciente. Eso no prueba
estabilidad ni permite copiar archivos sin sus avisos. `python-telegram-bot`
actualmente figura GPL-3.0; no incorporarlo por inercia a esta distribución
Apache-2.0. Para este webhook pequeño basta un adaptador HTTP al Bot API.
Consultar licencias del **tag realmente incorporado**, conservar notices/SBOM y
revisar obligaciones de redistribución. Fuentes:
[age](https://github.com/FiloSottile/age),
[whatsmeow](https://github.com/tulir/whatsmeow),
[Powertools](https://github.com/aws-powertools/powertools-lambda-python),
[python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot).

## 3. Topología mínima elegida

```text
Telegram -> bot Python -> transacción: receipt + command + outbox
                                      |
                          relay outbox (handler de app)
                                      v
                              commands.fifo -> app Python
                                                 |
                       +-------------------------+---------------------+
                       |                         |                     |
                HTTP / feeds               browser.fifo         whatsapp.fifo
               lotes acotados                   |                     |
                  en app                  Node / Chromium       Go / whatsmeow
                       |                  Playwright/OpenCLI     pair/sync/send
                       |                         |                     |
                       +----------- resultados durables + outbox ------+
                                                 |
                                        results Standard -> app
                                                 |
                         normaliza -> compara -> estima -> alerta Telegram

DynamoDB: control + ledger + outbox + versiones + presupuesto
S3: evidencia, informes y bundles cifrados inmutables
SSM: secretos por rol; sessions-admin Go: export explícito/prepare/renew
```

**Cuatro colas de trabajo**: comandos FIFO, browser FIFO, WhatsApp FIFO y
resultados Standard; DLQ correspondientes, no incluidas en ese conteo. No añadir
una cola por cada red. La elección FIFO simplifica el piloto, no significa que
los resultados necesiten orden global ni que una cola tenga concurrencia uno.

| Unidad | Trabajo | Agrupación y límites |
|---|---|---|
| `bot`, Python | Autenticación, límites del webhook, autorización y recepción durable | Respuesta rápida después de commit; no crawling ni LLM |
| `app`, Python | Casos de uso, HTTP, resultados, relay y reparación | Comandos por usuario/agregado; lotes públicos por run/shard, no por una cuenta ficticia |
| `browser`, Node | Solo trabajos que requieren JS o adaptadores CDP | Autenticados por cuenta; públicos por dominio y shard acotado |
| `whatsapp`, Go | Protocolo pair/sync/send y persistencia de dispositivo | Grupo por cuenta; un escritor activo por cuenta y lease común |
| `sessions-admin`, Go local | Preparar, compartir y renovar estado exportado | Sin extracción oculta de Chrome ni login automático |

Todos los grupos incluyen ámbito de propietario. El límite por dominio debe
coordinarse también entre shards: crear más grupos no autoriza más solicitudes.
FIFO ordena la recepción, no las transacciones de distintos items del stream.
Las dependencias se expresan por `expected_version`/estado y una tarea hija se
publica después del commit de su predecesora; no depender de un orden global.

Para no bloquear comandos con búsquedas largas, `app` procesa páginas/lotes
pequeños, guarda cursor y presupuesto y publica una continuación durable. No
recorrer miles de URLs en un handler. La lectura HTTP usa conexiones reutilizadas,
concurrencia global y por dominio, respuestas acotadas, ETag/Last-Modified cuando
la fuente los soporte y caché por variante/identidad pública, nunca entre usuarios
privados. El preflight decide rutas por capacidades verificadas, sin LLM.

Separar un worker Python HTTP o un relay dedicado **solo si** las mediciones
muestran retraso de resultados/webhook, competencia por concurrencia, tamaño de
paquete o necesidad de IAM más estrecho. Una Lambda más entonces puede ser más
eficiente que forzar cuatro; el conteo no es un objetivo de optimización.

## 4. Consistencia y efectos externos

### Entrada y outbox

1. `bot` valida secreto Telegram, tamaño, usuario y operación permitida.
2. Una transacción persiste recibo idempotente, comando y outbox. Si falla, no
   responde como si el comando se hubiera aceptado; una repetición encuentra el
   mismo recibo, sin perder la tarea ni volver a autorizarla.
3. Un handler de `app` consume INSERT de outbox desde DynamoDB Streams, publica
   en SQS y marca publicado. Si cae entre publicación y marca, puede repetir:
   el consumidor debe ser idempotente.
4. Un índice pendiente permite reparación paginada y acotada, sin Scan global.
   No borrar outbox no publicado por TTL. Filtrar modificaciones de sus propias
   marcas para evitar un bucle del relay.
5. Workers guardan resultado/versión y su outbox antes de reconocer la tarea.
   Un reintento recupera el resultado, no vuelve a ejecutar la operación.

Es el patrón recomendado para el problema de dos escrituras por
[AWS: transactional outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html).
Streams/Lambda pueden repetir procesamiento; DynamoDB ordena por item, no por
todos los items de un agregado:
[AWS: DynamoDB event source](https://docs.aws.amazon.com/lambda/latest/dg/with-ddb.html).
El relay comparte paquete/función con `app` inicialmente, con router de eventos
y pruebas de no recursión; aislarlo si pone en riesgo el control.

### Envío: limitar duplicados, no prometer exactly-once

```text
proposed -> approved -> dispatch_committed -> provider_confirmed
                              |
                              +-> send_uncertain -> reconciliation/review
```

- La aprobación liga propietario, destinatario, cuenta, hash de contenido,
  propósito, versión y vencimiento. No sirve una aprobación genérica reutilizable.
- El worker adquiere el lease mediante condición en DynamoDB. El sobre transporta
  `sessionRef` y versión esperada, no autoridad para usar un lease vencido.
- Una transacción comprueba lease/aprobación y reclama el `operation_id` antes del
  efecto. El worker vuelve a comprobar propiedad/plazo justo antes de enviarlo.
- Esta comprobación **no es fencing del proveedor**: WhatsApp no verifica nuestro
  token DynamoDB y el lease puede vencer después del check. La defensa adicional
  es que un nuevo worker no vuelva a enviar una operación ya reclamada.
- Caída después del claim, antes o después de enviar, sin prueba durable del
  resultado: `send_uncertain`. Puede implicar un mensaje que nunca salió; aceptar
  esa pérdida temporal de disponibilidad evita reintentar a ciegas.
- Persistir ID de protocolo y metadatos de correlación antes de enviar, cuando
  el cliente lo permita. Reconciliar solo con evidencia suficiente. Falta de
  evidencia no significa fallo seguro ni autorización para repetir.
- La sesión Go contiene estado **mutable** del protocolo SQLite; se restaura en
  `/tmp`, se guarda con snapshot consistente, se cifra/sube como blob nuevo y se
  actualiza el puntero mediante CAS. Go es dueño de ese estado, no Python.
- Subir a S3 antes del commit puede dejar un blob huérfano recuperable. Fallar
  después del envío al guardar sesión/resultado sigue siendo incertidumbre; no
  convertirlo en excepción reintentable que repita el efecto.
- Un timeout/lease expirado jamás borra el ledger para permitir reenvío. Retención
  del ledger cubre replay y recuperación, no solo la ventana de dedupe de SQS.

SQS deduplica publicaciones FIFO durante una ventana de cinco minutos; eso no
demuestra un único efecto externo:
[AWS: FIFO deduplication](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/FIFO-queues-exactly-once-processing.html).
TTL se elimina de manera diferida; verificar `expires_at` con condiciones, no
esperar su borrado para liberar leases:
[AWS: TTL](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/TTL.html).

El ledger de contacto se define por propietario, destinatario, propósito y
política de campaña. No imponer "jamás contactar de nuevo" para cualquier
consulta futura ni deduplicar indiscriminadamente entre propietarios.
Cancelación impide nuevas acciones pendientes; no revierte un envío iniciado.
CAPTCHA, MFA o rechazo de plataforma producen `needs_reauth`/`blocked`, no evasión.

## 5. Seguridad y sesiones fáciles de renovar

- Export explícito del usuario -> CLI local age -> carga autenticada al prefijo
  autorizado -> puntero versionado -> preflight -> worker. `import` sigue
  `unverified` hasta una prueba autorizada de login; no confundir con conectado.
- Bundle cifrado más alias en la cola; claves, cookies, teléfonos y contenido
  privado fuera de colas/logs/LLM. Payload privado por referencia cifrada.
- Un helper Go age compartido sirve al CLI y a workers; Node lo invoca por pipes
  con buffers acotados. No implementar otra criptografía por lenguaje.
- SSM SecureString por rol/propietario y permisos KMS/S3 mínimos. age protege
  contenido pero una clave pública no autentica al uploader; exigir autorización
  de carga y de actualización del puntero, hash y versión esperada.
- Renew usa CAS y coordinación con escritor activo. No compartir perfiles Chrome
  writable ni clonar el dispositivo del bridge personal; WhatsApp necesita su
  propio dispositivo y comprobar JID contra el número declarado.
- Retirar acceso exige revocar dispositivo/sesión en la plataforma y rotar claves
  cuando corresponda. Borrar una clave no elimina copias previamente descifradas.
- El lab prueba cookies + localStorage + IndexedDB, no todos los almacenes. La
  documentación actual también incluye OPFS/credenciales WebAuthn; incorporarlas
  requiere fixtures, clasificación y permiso, no ampliar el bundle implícitamente.
  sessionStorage necesita tratamiento aparte. Fuentes:
  [Playwright auth](https://playwright.dev/docs/auth),
  [storageState](https://playwright.dev/docs/api/class-browsercontext#browser-context-storage-state),
  [especificación age](https://age-encryption.org/v1).
- URLs de la web requieren controles de DNS/redirección/IP privada/metadata,
  cuotas y tamaño. Bloquear recursos pesados solo si no rompe la extracción;
  no cargar enlaces arbitrarios obtenidos de contenido no confiable.

## 6. Organización de código y contratos

```text
src/radar/
  domain/             dinero Decimal, producto, evidencia, oportunidad, estados
  application/        búsquedas, resultados, acciones, sesiones, presupuestos
  ports/              repositorio/UoW, fuentes, colas, sesiones, reloj, LLM
  adapters/local/     SQLite + cola local; memoria para tests
  adapters/aws/       DynamoDB, S3, SQS, SSM; transacciones/outbox
  adapters/telegram/  recepción y notificaciones HTTP
  adapters/sources/   HTTP/feed; dispatch a capacidades Agent Reach/OpenCLI
  entrypoints/        CLI y handlers Lambda
contracts/            schemas versionados + ejemplos válidos/negativos
workers/browser/      Node, Playwright/OpenCLI, helper age
go/cmd/               whatsapp y sessions-admin
go/internal/          protocolo, vault, snapshot y adaptadores de control
infra/                SAM y políticas, cuando se autorice
lab/                  banco de fixtures y benchmark preservado
```

Dominio no importa SDKs/red; application solo puertos. El adaptador local
SQLite ofrece transacciones equivalentes de ledger/outbox, no pretende imitar
todos los detalles de DynamoDB. JSON Schema define sobre y payload por operación;
los ejemplos se validan con la misma versión del esquema en Python/Node/Go.

Sobre: `schema_version`, `message_id`, `operation_id`, `correlation_id`,
`causation_id`, `owner_ref`, `kind`, `deadline`, `attempt`, referencias y versión
esperada. `message_id` y `operation_id` no son intercambiables: una operación
puede producir varios eventos. Payload pequeño, inicialmente máximo propio de
32 KiB; blobs referenciados con hash. Dinero decimal como string y moneda;
fechas UTC. Rechazar versiones desconocidas y propiedades no admitidas.

Registro de capacidades separa lectura, búsqueda, publicación, respuesta y DM
por plataforma/versión/cuenta. Documentado no significa probado. La ampliación a
vertical viral/general se aplaza; extender un pequeño núcleo existente cuando
haya casos, no diseñar ahora un framework universal de oportunidades.

## 7. Recursos, costo y telemetría

La prueba a 512 MiB/1 CPU observó 2,52–2,87 s y peak 281–290 MiB para lectura
directa; OpenCLI 4,70–5,00 s y peak 320–334 MiB. Son muestras sintéticas pequeñas,
no p95, cobertura social ni costos AWS. Go/age consumió unos 4,5–5,4 MiB; eso no
mide whatsmeow. Ver [resultados completos](../testing/LOCAL_RESULTS.md).

No fijar 512/1600 MiB como óptimo cloud: Lambda asigna CPU proporcional a RAM,
mientras Docker usó siempre 1 CPU. Su ZIP admite 250 MB descomprimidos incluyendo
layers/runtime; una imagen Docker de 304 MiB no demuestra que ZIP quepa o no.
Medir artefacto completo; conservar contenedor reproducible como baseline y
evaluar ZIP solo si cabe con margen:
[AWS: cuotas](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html).

Para el piloto proponer concurrencia global pequeña y escritor por cuenta uno;
el runner local sigue secuencial por seguridad de recursos. Optimizar luego por
**costo por oportunidad válida**, no URLs/segundo. No reservar/provisionar
concurrencia pagada. Los límites ESM y reserved concurrency se coordinan;
`MaximumConcurrency` de SQS parte de 2, no configurarlo en 1 para simular lease:
[AWS: SQS scaling](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-scaling.html).

DynamoDB provisionado pequeño puede aprovechar cuota gratuita del modo/tabla
elegidos, considerando cuenta completa e índices; no asumir que on-demand ofrece
las mismas unidades gratis. Presupuesto incluye Lambda, SQS, DynamoDB, S3, ECR,
SSM/KMS, logs, red y reintentos. Las transacciones consumen operaciones de prepare
y commit. Fuentes:
[DynamoDB pricing](https://aws.amazon.com/dynamodb/pricing/),
[transacciones](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis.html).

Budgets avisa con demora; **no es un techo duro** ni garantiza USD 0. Añadir
límites de trabajos, páginas, GB-s estimados, almacenamiento, vencimiento y
reservas atómicas de presupuesto en la app. Revisión de elegibilidad/free tier y
facturación real antes de desplegar:
[AWS: Budgets](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html).

Telemetría JSON: run/operación/source IDs, estado, tiempo de cola, latencia,
bytes, caché, RAM, intentos, razón de descarte, OOM, lease conflict, outbox age,
DLQ, `send_uncertain`, oportunidades válidas y costo estimado/real diferenciado.
Logs sin secretos/PII/conversaciones; retención corta configurable y evidencia
privada con política separada. Métricas nativas primero, agregados al cierre del
run; no infraestructura de observabilidad 24/7 para un piloto.

IA opcional solo para extracción/clasificación que falle determinísticamente.
Cache key incluye contenido + modelo + prompt/esquema/versiones + idioma + ámbito
de privacidad. Temperatura cero no garantiza determinismo. Sin credenciales,
sin contenido privado implícito, sin fallback pagado, y con pruebas de calidad
y presupuesto antes de activarla. OpenRouter queda puerto, no dependencia del
flujo ni selección de modelos basada en nombres no verificados.

## 8. Tests, criterios de aceptación y orden

1. Contratos + dominio de productos + aritmética Decimal + invariantes de capas.
2. Flujo 0.5 en proceso: búsqueda -> fuente falsa -> equivalencia/costos -> alerta.
3. Local SQLite: inbox/outbox/ledger/leases y conformidad con AWS; workers Go/Node
   con clientes falsos. Moto y DynamoDB Local sirven como herramientas de prueba,
   no sustituyen IAM/red/AWS ni se presupone que todas sean open source.
4. Fallos obligatorios: commit sin enqueue; enqueue sin marca; resultado sin ACK;
   duplicado fuera de cinco minutos; replay; expiración de lease; caída antes y
   después del envío; conflicto renew; snapshot WAL; usuario/cuenta equivocados;
   outbox recursivo; cuenta bloqueada; cancelación y presupuesto agotado.
5. Runtime Docker/RIE con límites, benchmark HTTP vs navegador y métricas de
   resultados útiles. El laboratorio actual permanece fixture-only; no abrir
   sus guardas para hacer pasar una prueba real como fixture.
6. Solo con encargo separado: cuenta de prueba, fuente real y canary AWS. WhatsApp
   intermitente debe demostrar vinculación, reconexión, sync y persistencia de
   claves; no promete recepción continua como un servicio conectado 24/7.

SQS: visibilidad mínima recomendada 6×timeout + batch window cuando aplique;
batch=1 para efectos inicialmente. Activar respuestas parciales; en FIFO parar
ante el primer fallo y devolver fallidos/no procesados para preservar orden.
Errores permanentes van a diagnóstico/quarantine; incertidumbre se registra y
reconcilia, no se convierte en reintento de envío. Fuentes:
[SQS configuración](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-configure.html),
[errores parciales](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-errorhandling.html).
Reutilizar Powertools para utilidades técnicas si se incorpora; no envolver
send en un decorador que vuelva a ejecutarlo tras un timeout.

Aceptación mínima: ninguna tarea aceptada se pierde en fallos inyectados;
repeticiones convergen a un único estado de operación; ninguna incertidumbre
produce reenvío automático; sesiones cruzadas se rechazan; presupuesto limita
nuevas tareas; reporte muestra resultados/descartes y estado de cada fuente.
Estas son pruebas a construir, **no resultados ya conseguidos**.

## 9. Respuesta formal a B-Q015

- **age único: sí**, con autenticación de uploader separada y helper compartido.
- **DynamoDB único control mutable cloud: sí**. No elimina SQLite local ni el
  estado mutable SQLite de whatsmeow; los blobs S3 nuevos son inmutables.
- **Estructura: sí**, simplificada a productos y sin mover código del lab antes
  de tener contratos/tests equivalentes.
- **ARCHITECTURE y AGENTS: no cambiar alcance silenciosamente**. Esta entrega
  agrega una propuesta consolidada; el diseño vigente se migra explícitamente
  al implementar el alcance confirmado, preservando límites de autorización.

Conclusión: aprobaría esta arquitectura para implementación incremental local,
no para producción inmediata. La mejora principal es menos trabajo pesado y
menos ambigüedad de estados, no más agentes decidiendo cada paso.
