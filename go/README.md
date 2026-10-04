# Módulo Go `radar.local/radar`

Un solo módulo Go para el vault de sesiones age y el worker WhatsApp. Estado:
**esqueleto probado con fakes**. No hay conexión WhatsApp real, ni dependencia
whatsmeow, ni DynamoDB/S3/Lambda. Ningún test usa red, cuentas o números reales.

## Estructura

| Ruta | Contenido |
|---|---|
| `internal/vault` | Vault age portado de [`lab/sessions`](../lab/sessions/README.md): keygen, prepare, renew (CAS por versión esperada), share, import, status. Mismos códigos de error constantes. `lab/sessions` queda intacto como banco preservado |
| `cmd/sessions` | CLI con los mismos comandos/flags que el lab, más `selftest`; JSON sin secretos |
| `internal/contract` | Sobre de trabajo, payloads `whatsapp.pair/sync/send`, resultado y códigos de error. Decodificación estricta (campos desconocidos rechazados), `schema_version` 1, máximo 32768 bytes, fechas UTC |
| `internal/whatsapp` | Puertos `Client`, `LeaseStore`, `Ledger`, `SessionStore`, `Notifier`; handlers pair/sync/send; fakes en memoria (`fake.go`) |
| `cmd/whatsapp` | Runner local: un sobre JSON por stdin, un resultado JSON por stdout. Exige `--fake`; sin él responde `unsupported` |

El sobre no transporta lease: el worker lo adquiere. Los handlers no registran
nada; códigos de emparejamiento, teléfonos y texto de chats no habilitados no
salen del proceso (los mensajes de chats fuera de `enabled_chat_refs` se descartan
en memoria).

## Protocolo de envío

Según [architecture-final-review §4](../docs/research/architecture-final-review.md):

```text
approved -> dispatch_committed -> provider_confirmed
                  |
                  +-> send_uncertain   (reconciliación/revisión; nunca reenvío automático)
```

1. Ledger en `approved` con owner, destinatario, `approval_ref` y `content_sha256`
   iguales al payload; si no, `invalid_input`. El hash también se comprueba contra `text`.
2. Adquirir lease de la sesión, conectar (un fallo aquí deja `approved`).
3. Claim condicional `approved -> dispatch_committed` con el token del lease.
4. Re-comprobar el lease justo antes de `Send`; perderlo da `send_uncertain`.
   No es fencing del proveedor.
5. Éxito: `provider_confirmed` con `provider_message_id`; luego snapshot + CAS de sesión.
   Error, timeout o caída tras el claim: `send_uncertain`.
6. Una entrega repetida de una operación ya reclamada nunca vuelve a llamar a
   `Send`: `provider_confirmed` devuelve el ID guardado; `dispatch_committed`
   pasa a `send_uncertain`. Un timeout jamás devuelve el ledger a `approved`.

Pair: `method` solo `code` (`qr` = `unsupported`); conectar, `PairPhone`, entregar
el código por `Notifier`, `WaitPaired` dentro de 150 s; si el JID no coincide con
`declared_phone`, `Logout` e `invalid_input`. Sync: lease, conectar, esperar
`OfflineSyncCompleted` hasta el deadline (si no, `timeout`), filtrar, snapshot,
liberar. `LoggedOut` = `needs_reauth`; `StreamReplaced` = `session_conflict`.

## Pruebas

Local (Go 1.27.1), desde `go/`:

```powershell
go vet ./...
go test ./...
```

Docker, desde la raíz del repositorio. El build ejecuta `go vet` y `go test` con
`-mod=readonly`; la imagen final es `scratch`, usuario 65532, con `/sessions` y
`/whatsapp`. El selftest necesita un `/tmp` escribible:

```powershell
docker build -f go/Dockerfile -t market-radar/b3-go:test .
docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m --memory 256m --cpus 1 market-radar/b3-go:test
Get-Content envelope.json -Raw | docker run --rm -i --network none --read-only --memory 256m --cpus 1 market-radar/b3-go:test /whatsapp --fake
```

Con `--fake`, el runner siembra un ledger `approved` coincidente con el sobre de
envío y un cliente falso; eso no es una aprobación ni prueba de entrega real.

## Falso frente a pendiente

| Pieza | Hoy | Pendiente |
|---|---|---|
| Cliente WhatsApp | `FakeClient` | Adaptador whatsmeow (`PairPhone`, eventos `OfflineSyncCompleted`/`LoggedOut`/`StreamReplaced`, snapshot SQLite con checkpoint WAL) |
| Lease y ledger | `MemLeases`, `MemLedger` | DynamoDB: lease condicional sobre `expires_at`; claim en transacción con la condición del lease |
| Sesiones | `MemSessions` (CAS de versión) | Snapshot cifrado con `internal/vault`, blob S3 nuevo y puntero `PTR` por CAS |
| Notificación | `FakeNotifier` | Telegram con `protect_content` |
| Entrada | Runner stdin/stdout | Handler Lambda/SQS |
| Contratos | Structs Go | Reconciliar con los JSON Schemas de `contracts/` |
