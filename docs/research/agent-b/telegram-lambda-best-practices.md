# Buenas prácticas: bot de Telegram sobre AWS Lambda

Autor: Claude (frente B). Consulta: 2026-10-03. Estado: guía de investigación,
**no hay código, infraestructura ni bot creados**. Complementa
[pruebas locales de Lambda](../../LOCAL_LAMBDA_TESTING.md) (SAM, RIE, memoria) y
[gestión de sesiones](../../SESSION_MANAGEMENT.md); no repite su contenido.
Contexto: [informe B](../agent-b-sessions.md), decisiones B-D016, B-D036 y B-D038–B-D043.

Etiquetas: `doc` = documentado en la fuente citada; `inf` = recomendación propia
derivada; `pend` = no verificado.

## 1. Arquitectura de referencia

```text
Telegram ──HTTPS POST──► Lambda Function URL (bot-webhook, Python, arm64)
                           1. valida X-Telegram-Bot-Api-Secret-Token
                           2. descarta update_id ya visto (DynamoDB, TTL)
                           3. autoriza user_id (lista blanca + rol)
                           4. acuse inmediato en la respuesta HTTP (método Bot API)
                           5. encola trabajo pesado
                                  │
                                  ▼
                     SQS FIFO (MessageGroupId = cuenta/usuario)
                                  │
              ┌───────────────────┼────────────────────┐
              ▼                   ▼                    ▼
       wa-worker (Go)      reader (Node/Chromium)   reports (Python + DuckDB)
              │                   │                    │
              └──── DynamoDB (memoria operativa, leases, idempotencia)
                    S3 (eventos, sesiones cifradas, informes)
                    SSM Parameter Store (token, secreto webhook, claves)
                                  │
                                  ▼
                 Bot API: sendMessage / editMessageText / sendDocument
EventBridge Scheduler ──► sync WhatsApp, resúmenes, chequeos de salud
```

## 2. Telegram

### 2.1 Webhook

- `setWebhook` con `secret_token` (1–256 caracteres `A-Z a-z 0-9 _ -`). Telegram
  lo envía en `X-Telegram-Bot-Api-Secret-Token`. Rechazar con 401 todo lo que no
  coincida, comparando en tiempo constante. `doc` / `inf`
- `allowed_updates` explícito: `["message", "callback_query"]` y lo que haga falta
  después. El cambio no afecta updates creados antes de la llamada. `doc`
- `max_connections` (1–100, por defecto 40): bajarlo limita la concurrencia de la
  Lambda del webhook. Empezar con 10 en el piloto. `doc` / `inf`
- `drop_pending_updates=true` solo al redeplegar a propósito; si no, se pierden
  mensajes reales. `doc` / `inf`
- Si el webhook devuelve un código distinto de 2XX, Telegram reintenta "un número
  razonable de veces" y luego desiste. Los updates se guardan hasta 24 h. Por eso
  deduplicar por `update_id` y devolver 200 cuando el error es del lado propio y
  el trabajo ya quedó encolado. `doc` / `inf`
- Requisitos del webhook: IPv4 (IPv6 no soportado), TLS 1.2 o superior, puertos
  443/80/88/8443. Telegram envía desde `149.154.160.0/20` y `91.108.4.0/22`. Una
  Function URL cumple TLS y puerto. El filtrado por IP es opcional: el secreto ya
  autentica. `doc`
- Mientras haya webhook, `getUpdates` no funciona. Para desarrollo local, usar
  otro bot de pruebas con `getUpdates`, nunca el de producción. `doc` / `inf`
- Revisar `getWebhookInfo` (pendientes y último error) en el chequeo de salud. `inf`

### 2.2 Respuesta rápida

- Con webhook, la respuesta HTTP puede contener un método de la Bot API
  (`{"method":"sendMessage", ...}`): acuse sin segunda petición. Telegram no
  devuelve el resultado de ese método, así que solo sirve para acuses sin
  seguimiento. `doc` / `inf`
- Todo lo que tarde (WhatsApp, navegador, informes) va a SQS y responde después
  con `sendMessage` o `editMessageText`. `inf`
- `answerCallbackQuery` siempre, aunque sea vacío: si no, el botón queda
  "cargando" en el cliente. `doc` / `inf`

### 2.3 Seguridad

- **El token va en la URL** (`https://api.telegram.org/bot<token>/MÉTODO`).
  Nunca registrar URLs completas de peticiones ni excepciones del cliente HTTP sin
  redactar. Guardar el token en SSM SecureString. `doc` / `inf`
- Si el token se filtra: `/token` en BotFather genera uno nuevo e invalida el
  anterior. Volver a ejecutar `setWebhook` con un secreto nuevo. `doc` / `inf`
- Autorizar por `user_id` numérico, nunca por `username`. Responder de forma
  neutra a desconocidos y no revelar si el bot tiene datos. `inf`
- `callback_data` (1–64 bytes) solo lleva un ID opaco y corto. Verificar en cada
  toque que ese usuario puede actuar sobre ese objeto y que el objeto sigue en el
  estado esperado. `doc` / `inf`
- Invitaciones de colaboradores con deep link `t.me/<bot>?start=<código>`
  (parámetro de hasta 64 caracteres `A-Z a-z 0-9 _ -`). El código es de un solo
  uso, caduca y se guarda con hash. Al usarlo, se agrega el `user_id` a la lista
  blanca. `doc` / `inf`
- Mensajes con códigos de emparejamiento: `protect_content=true`. `doc`
- Mini Apps: validar `initData` en el servidor y rechazar `auth_date` viejo;
  nunca confiar en `initDataUnsafe`. `doc` / `inf`
- Texto de usuarios y de fuentes = datos. Si un LLM interpreta texto libre, su
  salida se confirma con botones antes de tener efectos (B-D040). `inf`

### 2.4 Límites y entrega

- Unos 1 mensaje/s por chat, 20 mensajes/min por grupo y ~30 mensajes/s en
  difusión; al pasarse llega 429 con `retry_after`. Respetarlo con espera, no con
  reintentos inmediatos. `doc`
- Mensajes de texto: 1–4096 caracteres. Informes largos van como documento
  (`sendDocument`, hasta 50 MB por subida). `doc`
- En `parse_mode=HTML`, escapar `<`, `>` y `&` de todo texto externo: un título de
  anuncio puede romper el formato o inyectar enlaces. `inf`
- Rich Messages (Bot API 10.3, 2026-08-24) con tablas y botones: evaluar para
  tarjetas y resúmenes antes de construir una Mini App. `doc` / `pend` formato

### 2.5 UX

- `setMyCommands` con el menú por rol; comandos cortos en español. `doc` / `inf`
- Aprobaciones: editar el mismo mensaje con el estado final ("Aprobado por X a
  las HH:MM") para que no se apruebe dos veces. `inf`
- Botones de feedback en cada alerta; una acción por toque, con confirmación
  solo cuando hay efecto externo. `inf`
- Privacidad: el bot opera en chats privados; si algún día entra en grupos, el
  "privacy mode" limita qué mensajes recibe. `doc`

## 3. AWS Lambda

### 3.1 Funciones y runtime

- Separar funciones por perfil de recursos: webhook (128–256 MB), WhatsApp (Go,
  `provided.al2023`), lector con Chromium (≥1600 MB según Sparticuz) e informes.
  Una función grande con todo encarece cada invocación. `inf`
- arm64 (Graviton): precio por GB-s de $0.0000133334 frente a $0.0000166667 en
  x86. Usar arm64 donde las dependencias lo permitan; Chromium arm64 es un
  experimento aparte (LOCAL_LAMBDA_TESTING §1). `doc` / `inf`
- Go solo en `provided.al2023`/`provided.al2`; `go1.x` está obsoleto. `doc`
- Runtime Python: el más reciente soportado; revisar la lista de runtimes y sus
  fechas de deprecación cada trimestre. `inf`
- SnapStart para Python: aplicabilidad y costo `pend`; el webhook es pequeño y
  probablemente no lo necesita. `pend`

### 3.2 Function URL

- `AuthType NONE` para el webhook: Telegram no firma con IAM. La política basada
  en recursos debe permitir el acceso público de forma explícita. `doc`
- Desde octubre de 2025, las Function URL nuevas requieren los permisos
  `lambda:InvokeFunctionUrl` y `lambda:InvokeFunction`. Las plantillas viejas de
  internet pueden fallar por esto. `doc`
- IAM Access Analyzer (sin costo) detecta funciones con acceso público o entre
  cuentas: confirmar que solo el webhook es público. `doc`
- Las demás funciones no tienen URL: se invocan solo por SQS o EventBridge. `inf`

### 3.3 SQS y concurrencia

- Visibility timeout de la cola ≥ 6 × timeout de la función, más
  `MaximumBatchingWindowInSeconds`. Lambda rechaza una función con timeout mayor
  que la visibilidad. `doc`
- Activar respuestas parciales de lote (`ReportBatchItemFailures`) para no
  reprocesar mensajes que ya terminaron bien. `doc`
- FIFO con `MessageGroupId` por cuenta: orden y un lote a la vez por grupo; la
  concurrencia queda limitada por el número de grupos. `doc` (B-F017)
- `Maximum concurrency` en el event source mapping para topar el gasto. `doc`
- Cola de mensajes fallidos (DLQ) con alarma; un mensaje en DLQ es un fallo
  visible, no un éxito silencioso. `inf`
- Detección de bucles recursivos activa (es el comportamiento por defecto): una
  Lambda que escribe en la cola que la dispara se detiene sola. No desactivarla. `doc`

### 3.4 Idempotencia y efectos externos

- Powertools for AWS Lambda (Python, MIT-0, v3.35.0 del 2026-09-15): la utilidad
  de idempotencia usa DynamoDB, deriva la clave de un hash del payload o de
  campos elegidos y protege contra peticiones concurrentes, timeouts y payloads
  alterados. Usarla en el webhook (clave = `update_id`) y en las aprobaciones. `doc`
- La idempotencia evita repetir trabajo propio, pero no deshace un mensaje ya
  aceptado por WhatsApp o Telegram. Si un envío queda incierto: `send_uncertain`
  y reconciliación, nunca reenvío automático (A-F16). `inf`
- Powertools también ofrece procesamiento de lotes SQS con fallos parciales,
  logger JSON, métricas, trazas y parámetros con caché: un solo paquete para todo
  lo transversal. `doc` / `inf`

### 3.5 Flujos largos con espera humana

- Lambda durable functions: ejecuciones de hasta un año con checkpoints; las
  esperas suspenden la función **sin cobrar duración**. Se cobran las operaciones
  durables, los datos escritos y la retención (1–90 días, 14 por defecto). SDK
  para Python, JavaScript/TypeScript y Java. `doc`
- Encaje `inf`: el seguimiento tras silencio de una consulta de precio (B-I13)
  y la espera de respuesta de un vendedor. **No** sirve para emparejar WhatsApp,
  porque ese paso necesita el websocket abierto y no se puede suspender.
- Para aprobaciones simples basta el estado en DynamoDB y el callback de
  Telegram. Adoptar durable functions solo cuando haya flujos de varios pasos con
  esperas largas. `inf`

### 3.6 Secretos y configuración

- Token del bot, secreto del webhook y claves de cifrado en SSM SecureString
  estándar (sin cargo, hasta 4 KB). `doc` (B-F017)
- AWS Parameters and Secrets Lambda Extension: cachea parámetros y reduce
  llamadas y latencia, sin SDK. Alternativa: caché de Powertools. `doc`
- Nada secreto en variables de entorno en claro, eventos de prueba, imágenes ni
  logs. `.dockerignore` además de `.gitignore` (LOCAL_LAMBDA_TESTING §3). `inf`

### 3.7 Observabilidad

- Logs JSON con nivel configurable y sin contenido de usuario: IDs, estados,
  tiempos y clases de error (SECURITY.md). `inf`
- CloudWatch Logs guarda los logs **indefinidamente** por defecto: fijar
  retención por grupo (por ejemplo, 14 días) para evitar costo y retención de
  datos innecesaria. `doc` / `inf`
- Métricas mínimas: updates recibidos y rechazados, latencia del webhook,
  mensajes en DLQ, 429 de Telegram, `needs_reauth`, costo estimado por corrida. `inf`

### 3.8 Costo y salvaguardas

- AWS Budgets: el monitoreo y las notificaciones son gratis; los dos primeros
  presupuestos con acciones también. Crear un presupuesto mensual bajo con alerta
  por correo antes del primer despliegue. `doc` / `inf`
- Free tier de Lambda: 1 M solicitudes y 400 000 GB-s al mes; DynamoDB 25 GB
  (capacidad provisionada); SQS 1 M solicitudes; EventBridge Scheduler 14 M;
  KMS 20 000 solicitudes. `doc`
- **Free plan de cuentas nuevas (desde julio de 2025):** la cuenta se suspende a
  los 6 meses o al agotar créditos, y se borra 90 días después si no se pasa a
  Paid plan. Para producción: Paid plan, que conserva las ofertas Always Free. `doc`
  (informe B, B-F038)
- Cuentas creadas antes del 2025-07-15: free tier antiguo, sin cierre automático;
  las ofertas de 12 meses vencen al año y las Always Free siguen. Todo exceso se
  cobra: alerta de Budgets obligatoria. `doc`
- Topes: `max_connections` del webhook, concurrencia reservada o máxima por
  función, frecuencia del scheduler y presupuesto por fuente. `inf`

### 3.9 Despliegue e IAM

- Un rol IAM por función con permisos mínimos: el webhook puede encolar y
  leer/escribir sus tablas de idempotencia, no leer sesiones de WhatsApp. `inf`
- Infraestructura como código con SAM, coherente con LOCAL_LAMBDA_TESTING. Fijar
  versiones, lockfiles y digests de imagen. `inf`
- Entornos separados: bot de pruebas y bot de producción con tokens distintos,
  y colas y tablas con prefijo por entorno. `inf`

## 4. Antipatrones

- Hacer polling con `getUpdates` desde Lambda programada: añade latencia, gasta
  invocaciones y choca con el webhook.
- Procesar todo dentro del webhook hasta que Telegram corte y reintente:
  duplica efectos.
- Registrar el objeto de petición HTTP completo: filtra el token.
- Autorizar por `username` o por el texto de un mensaje.
- Guardar estado entre invocaciones en memoria o en `/tmp`: el entorno puede no
  reutilizarse.
- Publicar informes o QR en URLs públicas de S3.
- Reintentar envíos inciertos por un reintento de cola.

## 5. Checklist antes del primer despliegue

1. Bot de pruebas creado; token en SSM; nunca en Git.
2. Presupuesto AWS con alerta configurado.
3. Retención de logs fijada en cada grupo.
4. Webhook con `secret_token`, `allowed_updates` y `max_connections` explícitos.
5. Prueba negativa: petición sin secreto → 401 sin efectos.
6. Prueba de duplicado: mismo `update_id` dos veces → un efecto.
7. Prueba de usuario no autorizado → respuesta neutra.
8. DLQ con alarma; visibilidad SQS ≥ 6 × timeout.
9. IAM Access Analyzer: solo el webhook es público.
10. Revisión de logs de la prueba: sin token, sin contenido de usuario.

## 6. Cómo mantenerse actualizado

| Fuente | Qué vigilar | Cadencia |
|---|---|---|
| [Bot API "Recent changes"](https://core.telegram.org/bots/api#recent-changes) y canal @BotNews | Métodos nuevos, cambios de límites y de formato | Mensual |
| [AWS Lambda: historial de documentación](https://docs.aws.amazon.com/lambda/latest/dg/lambda-releases.html) y [precios](https://aws.amazon.com/lambda/pricing/) | Runtimes deprecados, funciones nuevas, cambios de permisos (como el de Function URL de 2025) | Mensual |
| [Powertools Python releases](https://github.com/aws-powertools/powertools-lambda-python/releases) | Cambios de API en idempotencia y lotes | Con cada actualización de dependencias |
| [whatsmeow](https://github.com/tulir/whatsmeow) | Cambios de protocolo que rompan conexión | Semanal mientras haya piloto WhatsApp |
| Esta guía | Fecha de consulta en el encabezado; reverificar cada `pend` | Trimestral |

Regla: fijar versiones, actualizar a propósito con pruebas y anotar la fecha de
consulta de cada afirmación. Una práctica sin fuente fechada se reverifica antes
de usarla.

## Fuentes (consultadas 2026-10-03)

- [Telegram Bot API](https://core.telegram.org/bots/api): `setWebhook`, retención
  24 h, respuesta con método, `callback_data`, límites de archivo, Rich Messages,
  URL con token.
- [Telegram webhooks](https://core.telegram.org/bots/webhooks): IPs, puertos, TLS.
- [Telegram Bot FAQ](https://core.telegram.org/bots/faq): límites de envío y 429.
- [Telegram bot features](https://core.telegram.org/bots/features): deep linking,
  privacy mode, regeneración de token.
- [Telegram Mini Apps](https://core.telegram.org/bots/webapps): validación de `initData`.
- [Lambda pricing](https://aws.amazon.com/lambda/pricing/): arm64, x86, durable functions.
- [Lambda durable functions](https://docs.aws.amazon.com/lambda/latest/dg/durable-functions.html).
- [Lambda SQS configuración](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-configure.html).
- [Lambda SQS escalado](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-scaling.html).
- [Lambda recursive loop detection](https://docs.aws.amazon.com/lambda/latest/dg/invocation-recursion.html).
- [Lambda Function URL auth](https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html).
- [Lambda Go](https://docs.aws.amazon.com/lambda/latest/dg/lambda-golang.html).
- [Powertools idempotency](https://docs.powertools.aws.dev/lambda/python/latest/utilities/idempotency/).
- [Parameters and Secrets Lambda Extension](https://docs.aws.amazon.com/systems-manager/latest/userguide/ps-integration-lambda-extensions.html).
- [CloudWatch Logs retención](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/Working-with-log-groups-and-streams.html).
- [AWS Budgets pricing](https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/).
- [DynamoDB pricing](https://aws.amazon.com/dynamodb/pricing/).
