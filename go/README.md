# Módulo Go `radar.local/radar`

Un solo módulo Go para el vault de sesiones age y el worker WhatsApp. Estado:
**worker probado con fakes y adaptador whatsmeow compilado y probado offline,
alineado con los contratos JSON v1 y v2** ([contratos v2](../contracts/README.md#contratos-v2-de-whatsapp)). El adaptador real (`internal/whatsapp/wameow`)
**nunca se ha conectado a WhatsApp**: emparejar, sincronizar y enviar de verdad
requieren una autorización separada y una cuenta de ensayo. No hay
DynamoDB/S3/Lambda. Ningún test usa red, cuentas o números reales.

## Estructura

| Ruta | Contenido |
|---|---|
| `internal/vault` | Vault age portado de [`lab/sessions`](../lab/sessions/README.md): keygen, prepare, renew (CAS por versión esperada), share, import, status. Mismos códigos de error constantes. `lab/sessions` queda intacto como banco preservado. `private.go`: `SealPrivate`/`OpenPrivate` cifran blobs de payload privado para destinatarios X25519 (máx. 64 KiB de texto plano) y devuelven/comprueban el sha256 del cifrado |
| `cmd/sessions` | CLI con los mismos comandos/flags que el lab, más `selftest`; JSON sin secretos |
| `internal/contract` | Sobres `envelope.v1` y `envelope.v2`, payloads `whatsapp.pair/sync/send.v1`, `whatsapp.sync/list_chats/resolve_contact.v2`, `whatsapp.result.v1/v2` y los privados `whatsapp.{pair,send,messages,chats,contact}.private.v1`. Los [JSON Schemas](../src/radar/schemas/) mandan; ver [reglas no aplicadas](#reglas-solo-de-schema) |
| `internal/whatsapp` | Puertos `Client`, `LeaseStore`, `Ledger`, `SessionStore`, `BlobStore`, `Notifier`; handlers pair/sync/send/list_chats/resolve_contact; fakes en memoria (`fake.go`) |
| `internal/whatsapp/wameow` | `Client` real sobre whatsmeow + SQLite en Go puro ([diseño](#adaptador-whatsmeow-wameow)) |
| `internal/schematest` | Solo para tests: compila `src/radar/schemas` con santhosh-tekuri/jsonschema v6.0.3 (como `contracts/validate/go`) |
| `cmd/whatsapp` | Runner local: un sobre JSON por stdin, un resultado JSON por stdout. `--fake` (cliente en memoria) o `--real` con compuerta; sin modo responde `unsupported` |

Decodificación (`contract.Decode` y los `Decode*Private`): máximo 32768 bytes,
un objeto JSON, sin `null` en ningún nivel, campos desconocidos rechazados,
`schema_version` ausente = `invalid_input` y distinto del esperado = `unsupported`
(el sobre acepta 1 y 2; un payload v2 exige 2 y uno v1 exige 1; en un sobre v1,
`page_size` y los campos de resultado v2 se rechazan). IDs
ULID/UUID, refs opacas, `session_ref`, `blob_key` sin `..`/`/` inicial/segmentos
vacíos, sha256 en minúsculas, fechas UTC con `Z` y fechas imposibles rechazadas.
`causation_id`, `session_ref`, `refs` y `since_cursor` se omiten, nunca van
vacíos. `whatsapp.result` se valida con `DecodeResult`; los kinds `browser.*` y
`telegram.*` solo se validan a nivel sobre (payload `nil`, el worker responde
`unsupported`).

El sobre no transporta lease ni datos privados. Teléfono y texto viajan en blobs
age referenciados por `private_ref` (`blob_key`, sha256 del **cifrado**,
`recipient_scope`). El worker comprueba el scope, compara el sha256 del blob con
`private_ref.sha256` **antes** de descifrar (distinto = `invalid_input`), descifra
en memoria y valida el payload privado. Los handlers no registran nada; códigos de
emparejamiento, teléfonos y texto no salen del proceso en claro.

## Protocolo de envío

Según [architecture-final-review §4](../docs/research/architecture-final-review.md):

```text
approved -> dispatch_committed -> provider_confirmed
                  |
                  +-> send_uncertain   (reconciliación/revisión; nunca reenvío automático)
```

1. Ledger en `approved` con owner, destinatario, `approval_ref` y `content_sha256`
   iguales al payload; si no, `invalid_input`.
2. Leer el blob de `private_ref`, verificar su sha256, descifrar
   `whatsapp.send.private.v1` (texto 1–4096 caracteres) y comprobar
   `sha256(texto) = content_sha256`. Cualquier fallo: `invalid_input` antes de
   lease, conexión o claim; el ledger sigue `approved`.
3. Adquirir lease de la sesión, conectar (un fallo aquí deja `approved`).
4. Claim condicional `approved -> dispatch_committed` con el token del lease.
5. Re-comprobar el lease justo antes de `Send`; perderlo da `send_uncertain`.
   No es fencing del proveedor.
6. Éxito: `provider_confirmed` con `provider_message_id`; luego snapshot + CAS de sesión.
   Error, timeout o caída tras el claim: `send_uncertain` (`status: failed`).
7. Una entrega repetida de una operación ya reclamada nunca vuelve a llamar a
   `Send` ni descifra: `provider_confirmed` devuelve el ID guardado;
   `dispatch_committed` pasa a `send_uncertain`. Un timeout jamás devuelve el
   ledger a `approved`.

Pair: `method` solo `code` (otro valor: `invalid_input` al decodificar,
`unsupported` en el handler); descifrar `whatsapp.pair.private.v1` (E.164)
antes del lease; conectar, `PairPhone`, entregar el código por `Notifier`,
`WaitPaired` dentro de 150 s; si el JID no coincide con `declared_phone`,
`Logout` e `invalid_input`. Sync: lease, conectar, esperar `OfflineSyncCompleted`
hasta el deadline (si no, `timeout`), filtrar chats habilitados, cifrar los
mensajes conservados como `whatsapp.messages.private.v1` para
`ResultRecipients`/`ResultScope`, guardar el blob con clave direccionada por
contenido y devolver solo `message_count` + `private_ref`; después snapshot y
liberar. `LoggedOut` = `needs_reauth`; `StreamReplaced` = `session_conflict`.

Sync v2 (paginado): el cliente guarda cada mensaje entrante como pendiente en el
estado de sesión **antes** del ack a WhatsApp. `since_cursor` (si viene) se pasa a
`Client.Ack` antes de conectar (cursor no emitido = `invalid_input`); `Client.Sync`
borra los pendientes de chats no habilitados y devuelve los `page_size` más
antiguos; el worker toma los que caben en 32 KiB, sella el blob y devuelve
`message_count`, `next_cursor` (cursor del último devuelto) y `has_more`. Lo
devuelto sigue pendiente hasta el siguiente `since_cursor`; lo que no cupo, también.
Sync v1: misma ruta con 100 como límite; si no cabe todo, `budget_exhausted` **y
se guarda la sesión** (los mensajes quedan pendientes para un sync v2); si cabe,
se confirma con `Ack` tras sellar.

List chats (v2): sin lease ni conexión ni snapshot; `Client.ListChats` desde el
estado de sesión, mismo reparto por 32 KiB, refs/nombres/fechas solo en
`whatsapp.chats.private.v1`; `next_cursor` = último `chat_ref`.

Resolve contact (v2): ledger `approved` para `operation_id`; descifrar
`whatsapp.contact.private.v1`; el registro debe coincidir en `owner_ref`,
`approval_ref`, `source_ref` (`RecipientRef`) y sha256 del blob cifrado
(`ContentSHA256`); lease, conectar, `Client.ResolveChat` (`IsOnWhatsApp` con un
solo número). Encontrado: `approved -> provider_confirmed` guardando el `chat_ref`
en `ProviderMessageID`, snapshot y `chat_ref` en el resultado; una reentrega lo
devuelve sin otra consulta. No encontrado: `not_on_whatsapp`, ledger intacto.
Nunca llama a `Send`.

Send: antes del lease, conexión y claim se comprueba `Client.HasChat(recipient_ref)`;
ref desconocida = `invalid_input` con el ledger en `approved` (antes daba
`send_uncertain` tras el claim sin haber enviado nada).

## Pruebas

Local (Go 1.27.1), desde `go/`:

```powershell
go vet ./...
go test -count=1 ./...
```

Conformidad: `internal/contract/golden_test.go` decodifica cada ejemplo de
[`contracts/examples`](../contracts/examples/) de `envelope.v1` y de los schemas
`whatsapp.*`; los válidos deben decodificar y su re-serialización Go debe pasar
el schema, los inválidos deben rechazarse (`wrong-schema-version` con
`unsupported`). Los tests de `internal/whatsapp` y `cmd/whatsapp` validan cada
resultado producido contra `whatsapp.result.v1` o `.v2` (según el sobre) y comprueban que ni texto, ni
chats, ni teléfonos, ni códigos aparecen en el JSON.

Docker, desde la raíz del repositorio. El contexto incluye solo fuentes Go,
`src/radar/schemas/*.json` y `contracts/examples/**/*.json`. Solo
`go mod download && go mod verify` usa red; `go vet`, `go test` y el build corren
en un paso `RUN --network=none` con `-mod=readonly`, `CGO_ENABLED=0` y
`GOTOOLCHAIN=local`. La imagen final es `scratch`, usuario 65532, con `/sessions`,
`/whatsapp` y las raíces TLS de Alpine (`/etc/ssl/certs/ca-certificates.crt`, solo
para `--real`); ~31.5 MB (antes 11.3 MB, sin whatsmeow/SQLite). El selftest necesita
un `/tmp` escribible:

```powershell
docker build -f go/Dockerfile -t market-radar/b4-go:test .
docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m --memory 256m --cpus 1 market-radar/b4-go:test
Get-Content send-envelope.json -Raw | docker run --rm -i --network none --read-only --memory 256m --cpus 1 market-radar/b4-go:test /whatsapp --fake --fake-private '{\"schema_version\":1,\"text\":\"Hola\"}'
# Compuerta: sin RADAR_WA_REAL_AUTHORIZED=yes responde invalid_input y sale con 1, sin leer stdin
Get-Content send-envelope.json -Raw | docker run --rm -i --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m market-radar/b4-go:test /whatsapp --real --session-dir /tmp
```

(En Git Bash: `MSYS_NO_PATHCONV=1` y comillas simples sin escapar.)

### Runner `--fake`

- Pair y send exigen `--fake-private` con el JSON privado sintético
  (`whatsapp.pair.private.v1` o `whatsapp.send.private.v1`). El runner lo cifra con
  una identidad age efímera, lo **siembra** en un `MemBlobs` bajo
  `private_ref.blob_key` y **reescribe `private_ref.sha256`** con el hash del
  cifrado recién generado (age no es determinista). Esa reescritura existe solo
  en el mundo falso; un worker real nunca modifica un `private_ref`.
- Send siembra además un ledger `approved` coincidente con el sobre; eso no es una
  aprobación ni prueba de entrega real.
- Resolve contact exige `--fake-private` con un `whatsapp.contact.private.v1`
  sintético y siembra una aprobación ligada a ese blob; el cliente falso encuentra
  todo número. List chats siembra dos chats sintéticos.
- Send siembra también el `recipient_ref` como chat conocido.
- Sync no necesita blob de entrada: el cliente falso devuelve un mensaje de un chat
  habilitado y otro descartado; el resultado trae solo el conteo y el `private_ref`
  del blob efímero (se pierde al terminar el proceso).

## Adaptador whatsmeow (`wameow`)

`wameow.New(ctx, dir)` abre `dir/whatsmeow.db`; `dir` debe existir (en Lambda,
`/tmp` restaurado desde el blob age; el adaptador nunca elige ubicación) y sin él
devuelve `ErrNoSessionDir`. `var _ whatsapp.Client = (*Client)(nil)` fija la
interfaz en compilación.

| Pieza | Decisión |
|---|---|
| Store | `sql.Open("sqlite", path+"?_pragma=foreign_keys(1)&_pragma=journal_mode(WAL)&_pragma=busy_timeout(10000)")` con `modernc.org/sqlite`, luego `sqlstore.NewWithDB(db, "sqlite", waLog.Noop)` + `Upgrade` (que exige foreign keys; `dbutil` acepta cualquier nombre de dialecto que empiece por `sqlite`). Sin CGO; `go list -deps` no incluye `mattn/go-sqlite3` |
| Connect | `ConnectContext` y espera `events.QR` (sin vincular: el QR se ignora y no se guarda) o `events.Connected` (sesión guardada). Fallo de conexión o `events.ConnectFailure` = `ErrTimeout` |
| PairPhone | Rechaza una sesión ya vinculada (`ErrAlreadyPaired`). `PairPhone(ctx, phone, true, PairClientChrome, "Chrome (Linux)")` justo después de `Connect`, como pide la doc (~160 s de websocket). `DeviceProps.PlatformType = CHROME`. El código solo va al `Notifier` |
| WaitPaired | Espera `events.PairSuccess` y devuelve `ID.User` (`types.JID.User`, sin servidor ni dispositivo); luego hasta 30 s por la reconexión que whatsmeow hace tras emparejar |
| Sync | Espera `events.OfflineSyncCompleted` hasta el deadline (si no: `ErrTimeout`, el worker da `timeout`). Recoge `events.Message` entrantes de texto (`conversation`/`extendedTextMessage.text`); ignora media, propios (`IsFromMe`), estados y listas de difusión. Cada mensaje se inserta en `radar_pending(seq, chat_ref, text, observed_at)` dentro del handler; con `SynchronousAck` WhatsApp recibe el ack solo después. `observed_at` = timestamp del mensaje en UTC. Desconecta **antes** de leer la tabla: lo que llegue después no se confirma y WhatsApp lo reentrega (al menos una vez; puede repetirse). Borra pendientes de chats no habilitados y devuelve los más antiguos con cursor `p<seq>` |
| Ack | `DELETE FROM radar_pending WHERE seq <= ?`; cursor mal formado o mayor que el último `seq` emitido (`sqlite_sequence`) = `ErrBadCursor`. Reenviar un cursor ya usado no borra nada más |
| List chats | `radar_chat_ref` + `radar_chat_seen(ref, last_message_at)` ordenados por ref; nombre = nombre de libreta, nombre, negocio o push name del store de contactos de whatsmeow (local, máx. 128 caracteres); grupos sin nombre |
| Resolve | `IsOnWhatsApp(ctx, []string{phone})` (nombre verificado en el whatsmeow fijado; el teléfono va con `+`); exactamente una respuesta con `IsIn` → JID PN (o el canónico) → `chatRef`; si no, `ErrNotOnWhatsApp`. Probado offline solo el mapeo de respuestas |
| Historial | `ManualHistorySyncDownload = true` (nunca se descarga; el recibo se envía igual para que el teléfono no reintente) y `RequireFullSync = false`. `events.HistorySync` no llega y se ignoraría |
| Chat refs | `wachat:c<24 hex aleatorios>` guardados en la tabla `radar_chat_ref(ref, jid)` dentro del mismo SQLite: viajan con el snapshot cifrado, son estables por chat y no contienen dígitos de teléfono. JID `@lid` se traduce a PN si el store conoce el mapeo. `Send` resuelve ref → JID; ref desconocida = `ErrUnknownChat` sin tocar la red |
| Send | `SendMessage` con `waE2E.Message{Conversation}`; devuelve `resp.ID` como `provider_message_id` |
| Errores | `LoggedOut` → `ErrLoggedOut` (`needs_reauth`); `StreamReplaced` → `ErrStreamReplaced` (`session_conflict`); `ConnectFailure` → `ErrTimeout`; `PairError`, `TemporaryBan`, `ClientOutdated` → error genérico (`internal`). El primer evento fatal gana y corta cualquier espera |
| Snapshot | `Disconnect` y `VACUUM INTO` a un archivo temporal del mismo directorio, que se transmite y se borra: copia consistente de un solo archivo aunque el WAL tenga páginas pendientes o haya goroutines escribiendo. Nunca copia el archivo vivo |
| Logs | whatsmeow recibe `waLog.Noop`; el paquete no registra nada (ni texto, ni teléfonos, ni JIDs, ni códigos) |

### Dependencias nuevas (fijadas en `go.mod`/`go.sum`)

| Módulo | Versión | Licencia |
|---|---|---|
| `go.mau.fi/whatsmeow` | `v0.0.0-20260929112325-8b41cfe6d9c4` (commit `8b41cfe`, 2026-09-29) | MPL-2.0 |
| `modernc.org/sqlite` | `v1.60.1` | BSD-3-Clause (también `modernc.org/libc`, `memory`, `mathutil`) |
| `google.golang.org/protobuf` | `v1.36.12` | BSD-3-Clause |
| `go.mau.fi/libsignal` (transitiva de whatsmeow) | `v0.2.2` | **GPL-3.0** |
| `go.mau.fi/util` | `v0.10.1` | MPL-2.0 |
| Resto transitivo: `coder/websocket` (ISC), `rs/zerolog`, `vektah/gqlparser`, `beeper/argo-go`, `elliotchance/orderedmap`, `dustin/go-humanize`, `ncruces/go-strftime`, `mattn/go-isatty`, `mattn/go-colorable` (MIT), `petermattis/goid` (Apache-2.0), `google/uuid`, `remyoudompheng/bigfft`, `filippo.io/edwards25519`, `golang.org/x/*` (BSD-3-Clause) | ver `go.mod` | permisivas |

**Copyleft además de MPL-2.0: `go.mau.fi/libsignal` es GPL-3.0** (fork de
RadicalApp/libsignal-protocol-go). El código fuente de este repo sigue siendo
Apache-2.0, pero el binario `/whatsapp` y la imagen enlazan GPL-3.0: distribuirlos
(imagen pública, ZIP de Lambda compartido) exige cumplir GPL-3.0 para el conjunto
(fuente correspondiente, licencia). Apache-2.0 es compatible en un sentido con
GPL-3.0, no al revés. Decisión del propietario pendiente; no es asesoría legal.
MPL-2.0 es copyleft por archivo: modificar archivos de whatsmeow obliga a publicar
esos archivos.

### Probado offline frente a pendiente de cuenta autorizada

Offline (`wameow_test.go`, sin red, números sintéticos): compuerta de directorio,
valores de configuración (nombre `Browser (OS)`, tipo Chrome, sin full sync,
historial manual, acks síncronos, foreign keys), mapeo evento → sentinel, que el
primer evento fatal corta `Sync`/`WaitPaired`, extracción de texto desde
`events.Message` sintéticos (incluye media, propios, estado y dispositivo AD),
refs estables sin dígitos y aceptadas por `DecodeMessagesPrivate`, resolución
inversa, `Send` con ref desconocida, deadline de `Sync`, `WaitPaired` con
`JID.User` de un AD-JID, rechazo de `PairPhone` en sesión vinculada, snapshot de
un SQLite en WAL sin checkpoint (la copia ingenua del archivo principal no tiene
las filas; el snapshot sí) y restauración de un snapshot del adaptador en un
directorio limpio sin `-wal`. `cmd/whatsapp` prueba la compuerta `--real` sin leer
stdin ni crear archivos. `Connect`, `PairPhone` contra servidor, `Send`,
`Logout` y el flujo de eventos real **no** se pueden probar sin red: whatsmeow no
ofrece un servidor falso y no se construyó uno.

Requiere cuenta de ensayo y autorización separada: emparejar por código de verdad
(formato aceptado del nombre, ventana de ~160 s, reconexión tras `PairSuccess`),
que `OfflineSyncCompleted` llegue también con cola vacía, sync real y duración,
envío real e ID devuelto, reconexión tras horas/días, regla de 14 días del
teléfono principal y límite de 4 dispositivos, `StreamReplaced` en la práctica
(dos procesos con la misma base), `LoggedOut` al desvincular desde el teléfono,
mapeo `@lid` ↔ PN con chats reales, y conexiones cortas desde IPs de AWS.

Comando para una futura corrida autorizada (no ejecutado; número y chat de ensayo
del propietario; el código aparece solo en stderr del operador):

```powershell
New-Item -ItemType Directory -Force .local\wa-session | Out-Null
$env:RADAR_WA_REAL_AUTHORIZED = "yes"
Get-Content pair-envelope.json -Raw | go run ./go/cmd/whatsapp --real --session-dir .local\wa-session --private '{\"schema_version\":1,\"declared_phone\":\"+<número de ensayo>\"}'
Remove-Item Env:RADAR_WA_REAL_AUTHORIZED
```

En `--real` el runner sigue usando lease, ledger, sesiones y blobs en memoria: la
sesión persistente es el `whatsmeow.db` vivo de `--session-dir` (dentro de
`.local/`, fuera de Git), el snapshot se calcula y se descarta, los mensajes de
sync quedan cifrados con una identidad efímera (el resultado solo trae el conteo) y
el ledger `approved` sembrado para send no es una aprobación: la autoriza solo la
invocación del operador.

## Reglas solo de schema

Reglas de los JSON Schemas que `internal/contract` **no** aplica a propósito. La
validación por schema (Python/Node/Go en `contracts/validate`) sigue cubriéndolas.

| Regla / ejemplo inválido aceptado por Go | Motivo |
|---|---|
| Payloads `browser.read`, `browser.result`, `telegram.command` dentro del sobre (`envelope.v1/payload-kind-mismatch.json`) | Este módulo no ejecuta esos kinds: valida el sobre, exige que `payload` sea objeto y responde `unsupported` |
| Claves que solo difieren en mayúsculas de un campo conocido (`{"Notify": ...}`) | `encoding/json` empareja nombres sin distinguir mayúsculas; el valor igual se valida |
| `observed_at` explícito `0001-01-01T00:00:00Z` en mensajes privados | Go lo trata como ausente y lo rechaza (más estricto) |
| Enteros escritos como `1.0` o `1e0` | El schema los acepta como `integer`; Go los rechaza (más estricto) |
| Tamaño máximo de 32768 bytes | Go mide los bytes recibidos, no la re-serialización compacta (más estricto con JSON indentado) |

## Falso frente a pendiente

| Pieza | Hoy | Pendiente |
|---|---|---|
| Cliente WhatsApp | `FakeClient`; `wameow` compilado y probado offline, nunca conectado | Prueba real con cuenta autorizada, incluidos `IsOnWhatsApp` y nombres de contacto reales |
| Lease y ledger | `MemLeases`, `MemLedger` | DynamoDB: lease condicional sobre `expires_at`; claim en transacción con la condición del lease |
| Sesiones | `MemSessions` (CAS de versión) | Snapshot cifrado con `internal/vault`, blob S3 nuevo y puntero `PTR` por CAS |
| Blobs privados | `MemBlobs`, identidad age efímera en el runner | S3 (escritura condicional), identidad del worker desde SSM, destinatarios de resultados por scope |
| Notificación | `FakeNotifier` | Telegram con `protect_content` |
| Entrada | Runner stdin/stdout | Handler Lambda/SQS |
| Sync grande | Paginación v2 con pendientes en el SQLite de sesión | Si el CAS del snapshot falla tras un sync, los pendientes nuevos de esa corrida solo viven en el `/tmp` de la Lambda (el blob sellado sí queda referenciado) |
| Ritmo de resolve/send | Una consulta por operación aprobada | Presupuesto por pasada y espera anti-ráfaga en la app (B-I09) |
