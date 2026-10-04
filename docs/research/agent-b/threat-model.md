# Modelo de amenazas: arquitectura aprobada (F9)

Fecha: 2026-10-03. Autor: agente B (subagente B5). Estado: **análisis y propuesta**.
Cubre la topología de [ARCHITECTURE](../../ARCHITECTURE.md) y la
[revisión final A](../architecture-final-review.md) §4–§7. Nada de esto está
desplegado: "implementado" significa código con prueba en este repositorio,
casi siempre contra fakes, no un control verificado en AWS, Telegram o WhatsApp.
No es asesoría legal.

Etiquetas: `doc` = afirmación con fuente citada; `inf` = inferencia de B.
Estados de la tabla STRIDE: **implementado** (código + prueba existente),
**planificado** (control diseñado en los documentos, sin código; prueba con ID
`TM-Pnn` propuesta), **brecha** (sin control diseñado o con riesgo residual que
ningún control técnico elimina).

## 1. Alcance

| Componente | Qué es hoy |
|---|---|
| `bot` (Lambda Python, Function URL pública) | [webhook.py](../../../src/radar/adapters/telegram/webhook.py) + [consent.py](../../../src/radar/adapters/telegram/consent.py) con fakes; sin adaptadores AWS |
| `app` (Lambda Python) | Diseño; relay outbox, casos de uso, presupuesto, LLM opcional |
| `browser` worker (Node + Playwright/OpenCLI) | Diseño (tarea A4) |
| `whatsapp` worker (Go + whatsmeow) | Esqueleto con fakes ([go/README](../../../go/README.md)); sin whatsmeow |
| `sessions-admin` CLI (Go + age, PC local) | Vault age portado del lab, con pruebas sintéticas |
| DynamoDB / S3 / SSM | Diseño; ninguna tabla, bucket ni parámetro creado |
| OpenRouter | Puerto previsto; IA desactivada por defecto |
| Telegram | Única interfaz de producto del MVP |
| Colaboradores en Venezuela | Vinculan solo su WhatsApp ([B-F026/B-F027](../agent-b-sessions.md#colaboradores-en-venezuela)) |

## 2. Activos

| Activo | Dónde vive | Impacto si se pierde/filtra |
|---|---|---|
| Token del bot | SSM SecureString; solo senders salientes | Suplantar al bot ante todos los usuarios |
| Secreto del webhook (`secret_token`) | SSM; leído por la factory del `bot` | Inyectar updates falsos como cualquier usuario autorizado |
| Identidades age (`.agekey`, identidad del worker) | PC local / SSM | Descifrar bundles de sesión y blobs privados |
| Bundles de sesión (navegador) | S3 cifrado con age | Usar cuentas autenticadas del propietario |
| Claves de dispositivo WhatsApp (SQLite whatsmeow) | Snapshot cifrado en S3; `/tmp` durante la ejecución | Clonar el dispositivo vinculado del colaborador |
| Datos personales de colaboradores (teléfono, user ref, consentimiento) | DynamoDB/S3; teléfono solo en blobs age | Exposición legal (art. 28) y de confianza |
| Mensajes de terceros (chats sincronizados) | Memoria del worker; solo chats habilitados en blobs age | Exposición penal (arts. 20–22) |
| Aprobaciones, ledger de envíos y de contactos | DynamoDB | Envíos no autorizados o duplicados; pérdida de prueba |
| Consentimientos (versión + fecha) | Directorio de usuarios + historial | Procesar sin base de consentimiento |
| Presupuesto (AWS, OpenRouter) | Cuenta AWS / créditos | Factura inesperada o servicio cortado |

## 3. Fronteras de confianza y flujo de datos

```text
[Colaborador/propietario en VE] --(Telegram, red posiblemente bloqueada)--> [Telegram]
        ==TB1 Internet pública==> Function URL (auth NONE) --> bot
                                   | secreto, tamaño, JSON, dedupe, rol, consentimiento
        ==TB2 IAM==> DynamoDB: receipt + command + outbox (una transacción)
                                   |
                         app (relay -> commands.fifo -> casos de uso)
             +---------------------+------------------------+-------------------+
             |                     |                        |                   |
   ==TB3 fuentes web==>   ==TB4 tercero LLM==>    browser.fifo -> Node   whatsapp.fifo -> Go
   HTTP/feed (no confiable)  OpenRouter            ==TB3 sitios==>      ==TB5 servidores WhatsApp==>
             |                                      bundle age (S3)      SQLite age (S3), lease
             +--------- resultado + outbox (DynamoDB) / blobs privados age (S3) ---------+
                                   |
                         app -> Telegram (sendMessage, protect_content)

[PC local: sessions-admin + .agekey] ==TB6 carga autenticada + CAS==> S3/puntero DynamoDB
==TB7 jurisdicción==: datos de residentes en Venezuela almacenados en AWS fuera de Venezuela
```

- **TB1**: todo lo que llega a la Function URL es hostil hasta pasar el secreto.
  La URL es pública por diseño ([Lambda Function URL auth](https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html)).
- **TB2**: el `bot` solo escribe sus tablas y encola; no lee sesiones (`inf`,
  [buenas prácticas §3.9](telegram-lambda-best-practices.md)).
- **TB3**: contenido de anuncios y páginas = datos no confiables (SSRF, inyección).
- **TB4**: OpenRouter y sus proveedores son terceros con políticas propias
  ([OpenRouter logging](https://openrouter.ai/docs/guides/privacy/logging)).
- **TB5**: WhatsApp no verifica nuestros leases; no hay fencing del proveedor
  ([revisión A §4](../architecture-final-review.md)).
- **TB6**: una clave pública age no autentica a quien sube
  ([especificación age](https://age-encryption.org/v1)).
- **TB7**: la sentencia TSJ 1318/2011 desaconseja transferir datos a Estados sin
  protección similar ([B-F026](../agent-b-sessions.md#colaboradores-en-venezuela)).

## 4. STRIDE por componente

Pruebas Python en `tests/telegram/` y `tests/`; pruebas Go en `go/internal/**`
(subtests entre comillas).

### Bot (webhook)

| ID | STRIDE | Amenaza | Control | Prueba | Estado |
|---|---|---|---|---|---|
| TM-01 | S | Spoofing del webhook: POST forjado a la Function URL | `secret_token` comparado en tiempo constante antes de leer el cuerpo; 401 sin efectos | `test_bad_or_missing_secret_is_401_with_zero_effects`, `test_secret_checked_before_size_and_json` | implementado |
| TM-02 | T | Replay o updates duplicados (reintentos de Telegram, carreras) | Dedupe por `update_id`; receipt + command + outbox en una transacción con claves condicionales; carrera perdida sin acuse; fallo = 500 sin acuse | `test_duplicate_update_id_has_single_effect`, `test_concurrent_duplicate_lost_race_is_not_acknowledged`, `test_persistence_failure_returns_500_without_ack` | implementado (fakes) |
| TM-03 | T | Claves de dedupe expiradas por TTL antes de que Telegram deje de reintentar | TTL de idempotencia mayor que la retención de updates de la [Bot API](https://core.telegram.org/bots/api); conformidad del adaptador DynamoDB | TM-P01 | planificado |
| TM-04 | S | Usuario no autorizado o suplantación por `username` | Lista blanca por `user_id` numérico en servidor; respuesta neutra; solo chats privados | `test_unauthorized_user_gets_neutral_reply_and_only_dedupe_key`, `test_unauthorized_callback_gets_neutral_answer`, `test_group_chat_is_ignored` | implementado |
| TM-05 | S | Invitación reutilizada, expirada o filtrada en el comando | Código guardado por hash, un uso, `expires_at` exclusivo, formato acotado; el código nunca entra al comando | `test_invite_code_enrolls_collaborator_once`, `test_reused_invite_code_rejected`, `test_expired_invite_code_rejected`, `test_unknown_or_malformed_invite_code_rejected` | implementado |
| TM-06 | S | Fuerza bruta de códigos de invitación | Generador con ≥128 bits y límite de intentos por `user_id`: no existen | TM-P02 | brecha |
| TM-07 | E | Escalada colaborador→propietario por comandos | Rol leído del directorio en servidor; `OWNER_ONLY` | `test_collaborator_owner_only_command_rejected` | implementado |
| TM-08 | E | Escalada por callbacks: un colaborador toca o forja `approve:<id>` de un objeto del propietario | El webhook solo valida formato y un uso por usuario; la `app` debe comprobar en cada `callback_ref` propietario, rol y estado del objeto | TM-P03 | planificado |
| TM-09 | T | Callback forjado, sobredimensionado o con marcado | Regex ASCII 1–64; refs opacas; prefijo `consent:` reservado con coincidencia exacta; clave por usuario y ref | `test_malformed_callback_data_rejected`, `test_double_approve_has_single_effect`, `test_forged_or_oversized_consent_callback_rejected` | implementado |
| TM-10 | R | Procesar datos sin consentimiento vigente o sin prueba de cuándo se aceptó | Gate versionado: sin la versión actual solo `/start`, `/mis_datos`, `/borrar`, `/stop` y los botones; aceptar persiste versión + fecha antes de responder; nueva versión fuerza re-aceptación | `test_unconsented_user_gets_prompt_and_command_is_not_stored`, `test_accept_records_version_and_timestamp_then_commands_work`, `test_accept_persistence_failure_is_500_without_answer_and_retry_succeeds`, `test_version_bump_forces_reacceptance`, `test_double_tap_accept_has_single_effect` | implementado (fakes) |
| TM-11 | I | Token del bot en logs, URLs o excepciones | Token solo en la ruta; `repr`/errores redactados; excepciones sin encadenar; el webhook nunca recibe el token | `test_token_never_in_repr_logs_or_exceptions`, `test_send_message_posts_json_with_token_only_in_path` | implementado |
| TM-12 | I | PII en colas y logs del bot | Sin `user_id`/`chat_id` numérico en payloads (refs opacas); logs con `update_id`, comando y clase de error | `test_valid_owner_command_validates_persists_once_and_acks`, `test_numeric_user_ref_fails_schema_and_is_not_stored`, `test_transport_schemas_declare_no_private_fields` | implementado |
| TM-13 | I | Texto libre de `/buscar` y `/pedir` (≤256 caracteres) viaja en claro en command/outbox/SQS | Ninguno hoy; propuesta: `/pedir` por `private_ref` cifrado o clasificarlo como dato del propietario con retención corta | TM-P04 | brecha |
| TM-14 | D | Cuerpos enormes, JSON patológico, ráfagas | 64 KiB, JSON seguro (incluida recursión), `max_connections` explícito, fallo cerrado | `test_oversize_body_is_413`, `test_malformed_json_is_400`, `test_set_webhook_requires_explicit_options`, `test_dependency_failure_fails_closed` | implementado |
| TM-15 | E | Handler sin cableado acepta updates | 503 sin wiring, 500 si la factory falla | `test_lambda_handler_fails_closed_without_wiring`, `test_lambda_handler_wiring_error_is_500` | implementado |

### App

| ID | STRIDE | Amenaza | Control | Prueba | Estado |
|---|---|---|---|---|---|
| TM-16 | T | Inyección HTML/enlaces en mensajes Telegram desde títulos de anuncios | `escape()` en todo texto externo con `parse_mode=HTML` | `test_escape_neutralizes_link_injection_from_listing_title` | implementado |
| TM-17 | T/E | Prompt injection desde texto de anuncios hacia el LLM | IA apagada por defecto; contenido como datos; salida `json_schema` validada; el LLM no aprueba ni envía; confirmación con botones ([SECURITY](../../../SECURITY.md), [B-F040](../agent-b-sessions.md)) | TM-P05: fixtures con instrucciones embebidas no cambian acciones ni permisos | planificado |
| TM-18 | T | Aprobación reutilizada o con contenido/destinatario cambiado | Aprobación ligada a propietario, destinatario, `content_sha256`, versión y vencimiento | Go `TestSend` "content hash mismatch", "recipient mismatch", "not approved", "private text differs from approved hash" | implementado (Go, fakes) |
| TM-19 | T | Envíos externos duplicados por replay del relay o caída del worker | `approved -> dispatch_committed -> provider_confirmed / send_uncertain`; claim antes del efecto; sin reintento automático | Go `TestSend` "crash after claim before send", "crash after send before confirm", "lease lost before send", "send timeout" | implementado (Go, fakes) |
| TM-20 | T | Duplicado residual: WhatsApp no verifica nuestro lease (sin fencing) | Aceptado: `send_uncertain` + revisión humana; nunca reenviar a ciegas | — | brecha (residual aceptado) |
| TM-21 | D | Agotamiento de presupuesto, Lambda desbocada o recursión | Concurrencia reservada pequeña, límites ESM, reserva atómica de presupuesto en la app, detección de recursión de Lambda, Budgets solo como alerta ([AWS Budgets](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html)) | Go `TestSync` "more than 100 messages"; TM-P06 | planificado |
| TM-22 | I/E | Uso cruzado entre propietarios de sesiones o datos | `owner_ref` en el sobre, `recipient_scope` en `private_ref`, prefijos S3 y roles IAM por propietario/función | Go `TestSend` "scope not accepted", "blob encrypted to another recipient"; TM-P07 (acceso cruzado en conformidad AWS) | planificado |
| TM-23 | R | Repudio de una aprobación o de un envío | Ledger con `approval_ref`, actor y hora; registro de eventos solo-agregar | TM-P08 | planificado |

### Browser worker

| ID | STRIDE | Amenaza | Control | Prueba | Estado |
|---|---|---|---|---|---|
| TM-24 | I/E | SSRF en lecturas HTTP/navegador (metadata cloud, redes privadas, redirects) | Validar esquema, host, DNS, IP destino y cada redirect; bloquear credenciales en URL; no seguir enlaces de contenido no confiable ([revisión A §5](../architecture-final-review.md)) | TM-P09; hoy no hay código SSRF en el repo | planificado |
| TM-25 | T | Bundle de navegador manipulado o con orígenes ajenos | Manifiesto validado (versión, tipo, orígenes permitidos); import queda `unverified` | Go `TestTamperedManifestFailsClosed` | implementado (vault) |

### WhatsApp worker

| ID | STRIDE | Amenaza | Control | Prueba | Estado |
|---|---|---|---|---|---|
| TM-26 | I | Blob de sesión o privado robado/manipulado en S3 | Cifrado age; sha256 del cifrado comprobado antes de descifrar; scope de destinatario | Go `TestPrivateBlob`, `TestSend` "tampered blob" | implementado (Go) |
| TM-27 | S | Bundle robado **junto con** la identidad age: clon del dispositivo | age no revoca copias antiguas; única revocación real: `Logout`/`/stop` y rotar identidad; no hay procedimiento de rotación diseñado | TM-P10 | brecha |
| TM-28 | S | Dispositivo clonado / `StreamReplaced` / JID ajeno | Lease de escritor único, `StreamReplaced` = `session_conflict`, CAS de versión, `Logout` si el JID no coincide | Go `TestSync` "stream replaced", "session version conflict"; `TestSend` "stream replaced on connect"; `TestPair` "wrong jid logs out" | implementado (fakes) |
| TM-29 | I | Claves del dispositivo quedan en `/tmp` de un entorno Lambda reutilizado | Borrar el SQLite restaurado al terminar cada invocación, también si falla (`inf`: no se puede contar con que el entorno se reutilice, [buenas prácticas §4](telegram-lambda-best-practices.md), pero tampoco con que se descarte; lo que quede en `/tmp` puede verlo la siguiente invocación del mismo entorno) | TM-P11 | planificado |
| TM-30 | I | Mensajes de terceros en chats no habilitados | Filtro de chats habilitados en memoria; solo se cifra lo conservado; conteo + `private_ref` en el resultado | Go `TestSync` "keeps only enabled chats", "no enabled messages" | implementado (fakes) |

### sessions-admin CLI y datos AWS

| ID | STRIDE | Amenaza | Control | Prueba | Estado |
|---|---|---|---|---|---|
| TM-31 | I | `.age`/`.agekey`/`.env` versionados o en contexto Docker | `scripts/check_docs.py` rechaza rutas privadas en Git; `.dockerignore` por allowlist | `python scripts/check_docs.py` (rutas privadas versionadas); allowlist en `docker/telegram-test.Dockerfile.dockerignore` | implementado |
| TM-32 | I | ACL del host Windows no garantizan privacidad de `.agekey` ([SECURITY](../../../SECURITY.md)) | Ninguno técnico en el repo | — | brecha |
| TM-33 | S/T | Carga no autenticada o cambio del puntero de sesión | Carga autorizada por IAM al prefijo del propietario; CAS con versión esperada y hash | TM-P13 | planificado |
| TM-34 | I | Derecho a borrar (art. 28, `/borrar`, `/stop`) frente a registro solo-agregar | Crypto-shredding: datos personales fuera del registro general, seudónimo y clave por persona; borrar la clave ([B-D035](../agent-b-sessions.md)). Sin implementar; copias descifradas, proyecciones y logs quedan fuera | TM-P14 | brecha |
| TM-35 | E | Roles IAM/SSM con más permisos de los necesarios | Un rol por función; el `bot` no lee sesiones; Access Analyzer: solo el webhook público ([buenas prácticas §5](telegram-lambda-best-practices.md)) | TM-P15 | planificado |
| TM-36 | I | Logs de CloudWatch retenidos indefinidamente | Retención por grupo (p. ej. 14 días) ([buenas prácticas §3.7](telegram-lambda-best-practices.md)) | TM-P15 | planificado |

### OpenRouter, Telegram y colaboradores

| ID | STRIDE | Amenaza | Control | Prueba | Estado |
|---|---|---|---|---|---|
| TM-37 | I | Datos enviados al proveedor LLM retenidos o usados para entrenar | Nunca datos privados al LLM; `provider.data_collection: "deny"`, `zdr` ([B-F040](../agent-b-sessions.md)) | TM-P16 | planificado |
| TM-38 | D | Clave OpenRouter abusada o gasto sin tope | Clave en SSM, límite de crédito por clave ([OpenRouter limits](https://openrouter.ai/docs/api-reference/limits)), sin fallback pagado | TM-P16 | planificado |
| TM-39 | D | Telegram bloqueado en Venezuela: el colaborador pierde la interfaz, incluido `/stop` y `/borrar` ([B-F027](../agent-b-sessions.md#colaboradores-en-venezuela)) | Respaldo propuesto: código y avisos críticos por WhatsApp desde la cuenta piloto con aprobación; no hay canal alternativo para ejercer `/stop`/`/borrar` | TM-P12 | brecha |
| TM-40 | D | WhatsApp banea el número vinculado (cliente no oficial, uso no personal) ([WhatsApp Terms](https://www.whatsapp.com/legal/terms-of-service)) | Consentimiento lo advierte; solo envíos aprobados, sin masivos; número dedicado recomendado (`inf`). El riesgo no se elimina | `test_consent_text_fits_is_html_safe_and_covers_required_points` (aviso) | brecha (residual) |
| TM-41 | I | Exposición penal arts. 20–22 por mensajes de terceros que no consintieron | Procesar solo chats habilitados, descartar el resto en memoria, nunca revelar en logs/LLM (TM-30). Revisión de abogado venezolano `pend` | Go `TestSync` "keeps only enabled chats" | brecha |
| TM-42 | R | Consentimiento del colaborador sin respaldo legal suficiente (transferencia fuera de VE, finalidad) | Texto versionado que declara AWS fuera de Venezuela, chats, riesgo de baneo, aprobación de envíos y comandos de datos; región `pend` | `test_consent_text_fits_is_html_safe_and_covers_required_points` | implementado (texto); revisión legal `pend` |

Recuento: **20 implementado**, **13 planificado**, **9 brecha** (42 filas).
"Implementado" en TM-02, TM-10, TM-18, TM-19, TM-28 y TM-30 es contra fakes;
los adaptadores reales deben pasar las mismas pruebas.

### Principales brechas

1. **TM-41/TM-42 legal**: mensajes de terceros y transferencia internacional sin
   revisión de abogado venezolano. Bloquea vincular colaboradores reales.
2. **TM-34 borrado frente a registro solo-agregar**: crypto-shredding diseñado,
   no implementado; `/borrar` y `/stop` prometen algo que hoy no existe.
3. **TM-27 bundle + identidad robados**: sin procedimiento de rotación de identidad
   age ni de revocación masiva; `Logout` por colaborador es la única salida.
4. **TM-39 Telegram bloqueado**: el colaborador puede quedar sin forma de ejercer
   `/stop` y `/borrar`; falta un canal alternativo autorizado.
5. **TM-13 texto libre en colas**: `/pedir` puede contener datos personales y
   viaja en claro por command/outbox/SQS.

## 5. Decisiones de esta tarea

- **El propietario también pasa por el gate.** No hay razón documentada para
  eximirlo: su WhatsApp también puede vincularse y el texto cambia con la versión.
  Costo: un toque una vez por versión.
- `/start`, `/mis_datos`, `/borrar` y `/stop` funcionan sin consentimiento: los
  derechos de habeas data no pueden depender de aceptar (`inf`, art. 28). `/start`
  de un usuario sin consentimiento responde con el acuse más el texto y botones.
- La aceptación **no** es un `telegram.command`: se persiste en el directorio de
  usuarios (`users.accept_consent`, puerto de directorio de usuarios/consentimiento)
  en una transacción con las claves de dedupe. No hace falta cambiar
  `telegram.command.v1`; los callbacks `consent:*` nunca se reenvían a la app.
- Rechazar no guarda nada salvo la clave de dedupe; no revoca una aceptación
  anterior (eso es `/stop`).
- El texto describe el comportamiento previsto de `/mis_datos`, `/borrar` y
  `/stop`; no debe mostrarse a colaboradores reales hasta que existan y haya
  revisión legal.

## 6. Pruebas planificadas

| ID | Qué debe demostrar | Dueño probable |
|---|---|---|
| TM-P01 | TTL de idempotencia ≥ retención de updates; update repetido tras horas sigue sin efecto | A5 (adaptador AWS) |
| TM-P02 | Generador de invitaciones ≥128 bits; N intentos fallidos por `user_id` bloquean temporalmente | B (bot) |
| TM-P03 | Colaborador con `callback_ref` de un objeto del propietario o en estado final: rechazo sin efecto | A3 (app) |
| TM-P04 | `/pedir` no deja texto en claro en command/outbox/logs | A/B (contrato v2) |
| TM-P05 | Anuncio con instrucciones embebidas no altera acciones, herramientas ni aprobaciones | A (LLM) |
| TM-P06 | Reserva atómica de presupuesto; corrida se detiene al agotarlo; sin recursión relay | A3/A5 |
| TM-P07 | Propietario A no lee comandos, blobs ni sesiones de B (IAM + app) | A5 |
| TM-P08 | Cada aprobación/envío registra actor, hora y hash; el registro no se reescribe | A3 |
| TM-P09 | URLs a 169.254.169.254, redes privadas, `user:pass@`, redirect a privada: bloqueadas | A4 |
| TM-P10 | Rotación de identidad age: bundles viejos dejan de abrirse en workers; `Logout` revoca | B (Go) |
| TM-P11 | `/tmp` sin SQLite ni claves tras cada invocación, incluida la fallida | B (Go) |
| TM-P12 | Ejercicio de `/stop` por canal de respaldo con aprobación del propietario | B |
| TM-P13 | Carga a prefijo ajeno o con versión vieja rechazada (CAS) | B (Go) + A5 |
| TM-P14 | Tras `/borrar`, los datos del usuario no se descifran desde registro ni backups vigentes | A/B |
| TM-P15 | Access Analyzer y retención de logs verificados en plantilla SAM | A (infra) |
| TM-P16 | Peticiones LLM con `data_collection: deny`/`zdr`; clave con límite de crédito | A |

## 7. Propuesta para SECURITY.md

Texto para que el dueño de SECURITY.md (agente A) lo pegue o adapte; B no edita
ese archivo.

```markdown
## Modelo de amenazas y consentimiento

El modelo de amenazas vigente está en
`docs/research/agent-b/threat-model.md` (STRIDE por componente, con estado implementado/planificado/brecha). Revisarlo
antes de abrir acceso de red, vincular colaboradores reales o desplegar en AWS.

- Consentimiento versionado: el bot solo procesa comandos de un usuario
  (propietario incluido) que aceptó la versión vigente del texto de
  `src/radar/adapters/telegram/consent.py`; se guarda versión y fecha antes de
  responder. Una versión nueva obliga a aceptar de nuevo. `/mis_datos`,
  `/borrar` y `/stop` funcionan sin aceptar.
- Colaboradores en Venezuela: no vincular a nadie real sin revisión de abogado
  venezolano (Constitución art. 28; Ley Especial contra los Delitos
  Informáticos arts. 20–22), sin `/borrar`/`/stop` implementados con
  crypto-shredding y `Logout`, y sin canal de respaldo si Telegram está bloqueado.
- Mensajes de terceros: solo chats habilitados por el colaborador; el resto se
  descarta en memoria y nunca llega a logs, colas en claro ni LLM.
- Riesgo aceptado y declarado: WhatsApp puede banear números vinculados con un
  cliente no oficial; no hay fencing del proveedor y un envío incierto nunca se
  repite automáticamente.
```

Filas sugeridas para la tabla "Amenazas y controles":

```markdown
| Webhook falso o repetido | secret_token en tiempo constante, dedupe por update_id y transacción antes del acuse |
| Escalada colaborador→propietario | Rol del directorio en servidor; la app verifica dueño y estado de cada callback |
| Sesión/identidad robada | age + hash antes de descifrar; revocación por Logout y rotación de identidad |
| Derecho a borrar frente a registro solo-agregar | Crypto-shredding con clave por persona |
```
