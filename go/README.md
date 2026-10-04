# Módulo Go `radar.local/radar`

Un solo módulo Go para el vault de sesiones age y el worker WhatsApp. Estado:
**esqueleto probado con fakes, alineado con los contratos JSON v1**. No hay
conexión WhatsApp real, ni dependencia whatsmeow, ni DynamoDB/S3/Lambda. Ningún
test usa red, cuentas o números reales.

## Estructura

| Ruta | Contenido |
|---|---|
| `internal/vault` | Vault age portado de [`lab/sessions`](../lab/sessions/README.md): keygen, prepare, renew (CAS por versión esperada), share, import, status. Mismos códigos de error constantes. `lab/sessions` queda intacto como banco preservado. `private.go`: `SealPrivate`/`OpenPrivate` cifran blobs de payload privado para destinatarios X25519 (máx. 64 KiB de texto plano) y devuelven/comprueban el sha256 del cifrado |
| `cmd/sessions` | CLI con los mismos comandos/flags que el lab, más `selftest`; JSON sin secretos |
| `internal/contract` | Sobre `envelope.v1`, payloads `whatsapp.pair/sync/send.v1`, `whatsapp.result.v1` y los privados `whatsapp.{pair,send,messages}.private.v1`. Los [JSON Schemas](../src/radar/schemas/) mandan; ver [reglas no aplicadas](#reglas-solo-de-schema) |
| `internal/whatsapp` | Puertos `Client`, `LeaseStore`, `Ledger`, `SessionStore`, `BlobStore`, `Notifier`; handlers pair/sync/send; fakes en memoria (`fake.go`) |
| `internal/schematest` | Solo para tests: compila `src/radar/schemas` con santhosh-tekuri/jsonschema v6.0.3 (como `contracts/validate/go`) |
| `cmd/whatsapp` | Runner local: un sobre JSON por stdin, un resultado JSON por stdout. Exige `--fake`; sin él responde `unsupported` |

Decodificación (`contract.Decode` y los `Decode*Private`): máximo 32768 bytes,
un objeto JSON, sin `null` en ningún nivel, campos desconocidos rechazados,
`schema_version` ausente = `invalid_input` y distinto de 1 = `unsupported`. IDs
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
liberar. Más de 100 mensajes o más de 32 KiB: `budget_exhausted` sin avanzar la
sesión (el contrato v1 no tiene paginación). `LoggedOut` = `needs_reauth`;
`StreamReplaced` = `session_conflict`.

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
resultado producido contra `whatsapp.result.v1` y comprueban que ni texto, ni
chats, ni teléfonos, ni códigos aparecen en el JSON.

Docker, desde la raíz del repositorio. El contexto incluye solo fuentes Go,
`src/radar/schemas/*.json` y `contracts/examples/**/*.json`; el build ejecuta
`go vet` y `go test` con `-mod=readonly`; la imagen final es `scratch`, usuario
65532, con `/sessions` y `/whatsapp`. El selftest necesita un `/tmp` escribible:

```powershell
docker build -f go/Dockerfile -t market-radar/b3-go:test .
docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m --memory 256m --cpus 1 market-radar/b3-go:test
Get-Content send-envelope.json -Raw | docker run --rm -i --network none --read-only --memory 256m --cpus 1 market-radar/b3-go:test /whatsapp --fake --fake-private '{\"schema_version\":1,\"text\":\"Hola\"}'
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
- Sync no necesita blob de entrada: el cliente falso devuelve un mensaje de un chat
  habilitado y otro descartado; el resultado trae solo el conteo y el `private_ref`
  del blob efímero (se pierde al terminar el proceso).

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
| Cliente WhatsApp | `FakeClient` | Adaptador whatsmeow (`PairPhone`, eventos `OfflineSyncCompleted`/`LoggedOut`/`StreamReplaced`, snapshot SQLite con checkpoint WAL) |
| Lease y ledger | `MemLeases`, `MemLedger` | DynamoDB: lease condicional sobre `expires_at`; claim en transacción con la condición del lease |
| Sesiones | `MemSessions` (CAS de versión) | Snapshot cifrado con `internal/vault`, blob S3 nuevo y puntero `PTR` por CAS |
| Blobs privados | `MemBlobs`, identidad age efímera en el runner | S3 (escritura condicional), identidad del worker desde SSM, destinatarios de resultados por scope |
| Notificación | `FakeNotifier` | Telegram con `protect_content` |
| Entrada | Runner stdin/stdout | Handler Lambda/SQS |
| Sync grande | `budget_exhausted` sobre 100 mensajes / 32 KiB | Paginación o cursor de salida en un contrato v2 |
