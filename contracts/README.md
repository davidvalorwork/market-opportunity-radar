# Contratos JSON (v1 y v2)

Estado: **contratos y validadores en uso por el webhook de Telegram
(`src/radar/adapters/telegram`) y por el módulo Go (`go/internal/contract`,
alineado en B3b con pruebas de ejemplos dorados y de conformidad de
resultados)**. Ninguna cola ni adaptador AWS real los usa todavía. Las reglas
que Go no aplica a propósito están en [go/README.md](../go/README.md).

Estos JSON Schema (draft 2020-12) son el contrato compartido entre el agente A
(Codex) y el agente B (Claude): Python decide, Node y Go ejecutan, y los tres
validan **los mismos archivos de esquema y los mismos ejemplos dorados**. La
especificación sale de la [revisión final de arquitectura](../docs/research/architecture-final-review.md)
§4–§6; el manifiesto de sesión, de [SESSION_MANAGEMENT](../docs/SESSION_MANAGEMENT.md).

## Dónde vive cada cosa

| Ruta | Contenido |
|---|---|
| [`src/radar/schemas/`](../src/radar/schemas/) | **Única copia de los schemas**; se publica dentro del wheel `radar` |
| `contracts/examples/{valid,invalid}/<schema>/` | Ejemplos dorados: ≥2 válidos y ≥3 negativos por schema |
| `contracts/capabilities.json` | Registro de capacidades (instancia de `capabilities.v1`) |
| `contracts/validate/node/`, `contracts/validate/go/` | Validadores Node (Ajv) y Go (santhosh-tekuri/jsonschema v6) |
| `src/radar/contracts.py`, `tests/test_contracts.py` | Validador Python y su suite |

Los schemas se movieron a `src/radar/schemas/` para que el paquete Python
instalado los cargue con `importlib.resources` sin depender del checkout; Node y
Go leen esos mismos archivos por ruta relativa. Así hay una sola fuente y ninguna
copia mantenida a mano. El build Docker construye el wheel, lo instala en un venv
limpio y corre la suite desde un directorio sin `src/` ni `pyproject.toml`.

| Schema | Uso |
|---|---|
| `common.v1` | `$defs`: IDs, refs opacas, `session_ref`, `blob_key`, `private_ref`, sha256, fechas UTC, errores tipados, `status`, dinero, evidencia |
| `envelope.v1` | Sobre de todas las colas; `kind` selecciona el schema de `payload` |
| `browser.read.v1` / `browser.result.v1` | Lectura sin efectos del worker navegador y su resultado |
| `whatsapp.pair/sync/send/result.v1` | Transporte: vincular, sincronizar chats habilitados, envío aprobado, resultado |
| `whatsapp.pair.private.v1`, `whatsapp.send.private.v1`, `whatsapp.messages.private.v1` | **Payloads privados** (ver abajo) |
| `telegram.command.v1` | Comando ya autenticado por el bot |
| `session.manifest.v1` | Metadatos del bundle cifrado (nunca cookies ni claves) |
| `capabilities.v1` | Registro plataforma → backend → operación → estado, con fuente y fecha |

Todos los valores de ejemplo son sintéticos (`+10000000000`, `radar-pilot`,
`example.com`). `capabilities.json` resume `docs/research/agent-a-platforms.md`:
nada figura como `probado_real`; documentado o inspeccionado no significa probado.

## Datos privados: transporte por referencia cifrada

El sobre y sus payloads de transporte van a colas, DynamoDB, logs y reintentos;
por eso **no contienen teléfonos, texto de mensajes ni contenido privado**. Esos
datos viajan en un blob cifrado con age (el vault Go existente: hoy
`lab/sessions`, `go/internal/vault` en `main`) y el transporte lleva solo
`private_ref = {blob_key, sha256, recipient_scope}`:

| Transporte (en claro) | Privado (cifrado, schema `*.private.v1`) |
|---|---|
| `whatsapp.pair`: `notify`, `method`, `private_ref` | `whatsapp.pair.private`: `declared_phone` (E.164) |
| `whatsapp.send`: `recipient_ref`, `approval_ref`, `content_sha256`, `private_ref` | `whatsapp.send.private`: `text` (≤4096) |
| `whatsapp.result`: `message_count`, `private_ref` | `whatsapp.messages.private`: `messages[{chat_ref, text, observed_at}]` |

- `private_ref.sha256` es el hash del blob **cifrado** (integridad); `content_sha256`
  es el hash del **texto en claro** que liga la aprobación. Tras descifrar, el
  worker valida el payload privado y comprueba `sha256(text) = content_sha256`.
- `recipient_scope` nombra el conjunto de destinatarios age autorizado a
  descifrar (ej. `worker:whatsapp`); nunca contiene una clave.
- Los payloads privados se validan solo después de descifrar, en memoria. Nunca se
  registran en logs/telemetría, nunca se ponen en SQS/DynamoDB en claro y nunca se
  envían a un LLM.
- Los schemas de transporte cierran sus objetos (`additionalProperties: false`):
  ejemplos negativos prueban que `text`, `declared_phone` o `messages` en el
  sobre se rechazan. Las refs opacas exigen al menos una letra en el valor, así
  `tgchat:10000000000` (teléfono o ID numérico crudo) no pasa.

## Reglas

- **Versionado:** un cambio incompatible crea un archivo `vN` nuevo; nunca se
  edita un v1 publicado de forma incompatible. Cambiar un contrato es un PR propio
  revisado por A y B. `schema_version` es `const`: un consumidor responde
  `invalid_input` si falta y `unsupported` si es otra versión.
- **Cerrado:** `additionalProperties: false` en cada objeto con forma fija. Los
  mapas acotados (`args`, `fields`, `versions`, registro) limitan nombres,
  tamaño y tipo de sus valores.
- **32 KiB:** el JSON serializado compacto (UTF-8, sin espacios) de cualquier
  instancia (sobre y también payloads privados) no supera 32768 bytes. JSON
  Schema no puede expresarlo; lo comprueba cada validador. Más datos = más blobs
  o páginas, no un payload mayor.
- **Dinero:** `{"amount": "60.00", "currency": "USD"}`; importe decimal como
  string, nunca float; moneda ISO 4217 de tres letras.
- **Fechas:** RFC 3339 en UTC con `Z` (`2026-10-03T12:00:00Z`). Los tres
  validadores comprueban también `format` (`date`, `date-time`): fechas
  imposibles como 30 de febrero se rechazan.
- **Sobre:** `causation_id` se omite en el primer mensaje (no `null` ni vacío).
  `whatsapp.pair` exige `session_ref` y `expected_version` 0; `sync`/`send`
  exigen `expected_version` ≥ 1. `whatsapp.result` usa solo `succeeded`/`failed`
  (envío sin prueba durable = `failed` + `send_uncertain`); `partial` solo
  existe para lecturas. `method` de vinculación: solo `code` por ahora.
- **Sin lease en el mensaje:** el worker adquiere el lease por condición en el
  almacén de control; un token en el sobre no daría autoridad (ejemplo negativo
  `envelope.v1/lease-field.json`).
- **Los `$id` usan el dominio reservado `.invalid`:** nunca se resuelven por red;
  los validadores precargan los archivos locales.
- **Límites del schema:** no sustituye controles en runtime (DNS, redirects, IP
  privada, autorización, hash del texto, ledger de envíos, `pairing` presente en
  resultados de pair). Ver [SECURITY](../SECURITY.md).

## Contratos v2 de WhatsApp

Estado: **schemas, ejemplos dorados y worker Go (fakes + adaptador whatsmeow
probado offline) implementados; el webhook de Telegram ya emite `envelope.v2`.
Nunca probado contra WhatsApp real.** Los archivos v1 publicados no cambian: v2
son archivos nuevos (regla de versionado de arriba).

| Schema nuevo | Uso |
|---|---|
| `envelope.v2` | Igual que `envelope.v1` (sin lease, sin datos personales, 32 KiB, IDs, `deadline` UTC, `causation_id`, `expected_version`) con `schema_version: 2` y dos kinds más: `whatsapp.list_chats`, `whatsapp.resolve_contact` (ambos exigen `session_ref` y `expected_version` ≥ 1). `sync`, `result`, `list_chats` y `resolve_contact` llevan payload v2; `pair`, `send`, `browser.*` y `telegram.command` reutilizan su payload v1 |
| `whatsapp.sync.v2` | `enabled_chat_refs` (1–50), `page_size` (1–100, obligatorio), `since_cursor` opcional |
| `whatsapp.list_chats.v2` | `page_size` (1–100), `since_cursor` opcional |
| `whatsapp.resolve_contact.v2` | Solo `private_ref` → `whatsapp.contact.private.v1` |
| `whatsapp.result.v2` | v1 + `next_cursor`, `has_more` (ausente = `false`), `chat_count` (+ `private_ref` → `whatsapp.chats.private.v1`), `chat_ref` (resolve_contact) y el código de error `not_on_whatsapp` (enum local; `common.v1` no cambia). `message_count` ≥ 1 y `chat_count` no van juntos |
| `whatsapp.chats.private.v1` | **Privado**: `chats[{chat_ref, display_name? (1–128), last_message_at?}]` (1–100). Los nombres visibles son datos personales; `last_message_at` falta en un chat resuelto que aún no escribió |
| `whatsapp.contact.private.v1` | **Privado**: `phone` (E.164), `source_ref` (ref opaca del anuncio publicado que expuso el número), `approval_ref` |

Los payloads privados nuevos usan `schema_version: 1` (son v1 de su propio
schema); los transportes v2 usan `schema_version: 2`. Ningún transporte v2
declara `text`, `messages`, `phone`, `declared_phone`, `display_name` ni `chats`
(lo comprueba `tests/test_contracts.py`).

### Migración: aceptar v1 y v2

Durante la migración **todo consumidor acepta `envelope.v1` y `envelope.v2`** y
elige el schema por `schema_version` (`validate("envelope.v1" | "envelope.v2", …)`);
`schema_version` ausente sigue siendo `invalid_input` y cualquier otro valor
`unsupported`. El worker Go decodifica ambos y responde con `whatsapp.result` de
la misma versión que el sobre (v1 → `whatsapp.result.v1`, v2 → `whatsapp.result.v2`).
Un `whatsapp.sync` v1 sigue funcionando sin paginar: si no cabe en un resultado
responde `budget_exhausted`, pero ahora **guarda la sesión** y los mensajes quedan
pendientes para un `sync` v2 (antes se perdían tras el ack de WhatsApp).

Productores que deben cambiar: la app (agente A) al leer el outbox de Telegram
(ya `envelope.v2`) y al encolar trabajos WhatsApp nuevos (`sync` con
`page_size`, `list_chats`, `resolve_contact`), y su lector de resultados
(`whatsapp.result.v2`: `next_cursor`, `has_more`, `chat_count`, `chat_ref`,
`not_on_whatsapp`).

### Paginación de sync (cursor de salida)

1. Primera página: `since_cursor` ausente. El worker conecta, espera
   `OfflineSyncCompleted`, guarda todo mensaje entrante en el estado de sesión
   cifrado (tabla `radar_pending` del SQLite whatsmeow) **antes** del ack a
   WhatsApp, descarta los de chats no habilitados y devuelve los más antiguos,
   hasta `page_size` o 32 KiB de `whatsapp.messages.private.v1`.
2. El resultado trae `message_count`, `private_ref`, `next_cursor` y `has_more`.
   Los mensajes devueltos **siguen pendientes**: los borra el siguiente `sync`
   cuyo `since_cursor` es ese `next_cursor` (prueba de que el consumidor los
   guardó). Reentregar el mismo sobre devuelve la misma página (al menos una vez).
3. Repetir con `since_cursor = next_cursor` y `expected_version = session_version`
   mientras `has_more`. Una página vacía devuelve `next_cursor = since_cursor`.
4. Un cursor que la sesión nunca emitió es `invalid_input` antes de conectar.

### Descubrir chats (`list_chats`)

Lista por páginas los chats que la sesión conoce: los que escribieron al número
(también los no habilitados: de ellos queda ref + fecha; su texto, si llegó
durante un send/resolve, existe solo dentro del snapshot cifrado hasta que el
siguiente `sync` lo borra) y los
resueltos con `resolve_contact`. Solo lee el estado de sesión: sin lease, sin
conexión, sin snapshot. El transporte lleva `chat_count`, `private_ref`,
`next_cursor` (último `chat_ref` de la página) y `has_more`; refs, nombres
(libreta/negocio/push name del store local de whatsmeow, nunca de la red) y fechas
viajan solo cifrados. El propietario elige en Telegram qué refs habilitar.

### Primer contacto (`resolve_contact`) y restricciones de ToS

Los términos de WhatsApp prohíben mensajería masiva y automática y el uso no
personal sin autorización ([B-F018](../docs/research/agent-b-sessions.md));
el cliente no oficial arriesga el bloqueo del número (TM-40 del
[modelo de amenazas](../docs/research/agent-b/threat-model.md)). Por eso:

- **Solo números publicados por el vendedor para esa venta.** `source_ref`
  identifica el anuncio que expuso el número; nunca números comprados, adivinados,
  de listas, de otros anuncios ni de grupos.
- **Una aprobación humana por contacto.** El worker exige un registro `approved`
  en el ledger para `operation_id`, ligado a `owner_ref`, `approval_ref`,
  `source_ref` y al sha256 del blob cifrado del contacto (el teléfono nunca se
  hashea ni se guarda fuera del blob). Tras el éxito pasa a `provider_confirmed`
  con el `chat_ref`; una reentrega devuelve ese ref sin consultar otra vez.
  `not_on_whatsapp` deja la aprobación como estaba y no crea nada.
- **Un número por operación, sin enumeración masiva.** El contrato no admite
  listas de teléfonos; `IsOnWhatsApp` se llama con exactamente uno.
- **Nunca envía mensajes.** Obtener el `chat_ref` no es contactar: el primer
  mensaje es un `whatsapp.send` aparte, con su propia aprobación, sujeto a las
  salvaguardas B-I09 ([sesiones B](../docs/research/agent-b-sessions.md):
  presupuesto por pasada, espera anti-ráfaga, nunca contactar dos veces a la misma
  persona, control humano) y a `send_uncertain`.
- **Límites de ritmo y presupuesto** (pendiente en la app, no en el schema): pocas
  resoluciones por día y por cuenta, espaciadas, contadas en el presupuesto de la
  pasada; un `rate_limited` o `blocked` detiene la cola.

`whatsapp.send` con un `recipient_ref` que la sesión no conoce responde ahora
`invalid_input` **antes** del lease y del claim: el ledger sigue `approved` y no
se registra `send_uncertain` (antes se detectaba después del claim).

## Ejecutar los validadores

Desde la raíz del repositorio (Windows; en Linux/macOS usar `.venv/bin/python`):

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install --no-deps -r requirements.lock
.venv/Scripts/python -m pip install --no-deps -e .
.venv/Scripts/python -m pytest -q tests

cd contracts/validate/node; npm ci --ignore-scripts; node validate.mjs; cd ../../..
cd contracts/validate/go; go test ./...; cd ../../..

docker build -f contracts/Dockerfile -t market-radar/b1-contracts:test .
```

Python expone `radar.contracts.validate(schema_name, instance)` (por ejemplo
`validate("envelope.v1", sobre)`, lanza `jsonschema.ValidationError`),
`load_schema(name)`, `schema_names()` y `registry()`. `tests/test_contracts.py`
se omite si faltan las dependencias de test. El build Docker ejecuta las tres
suites sin red y solo termina si todas pasan; su imagen final contiene únicamente
marcadores.
