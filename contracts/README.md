# Contratos JSON v1

Estado: **contratos y validadores; ningún worker, cola ni adaptador Python los usa
todavía**. El módulo Go `go/internal/contract` aún tiene diferencias listadas en
el informe de B1 (pendientes de su propia tarea).

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
