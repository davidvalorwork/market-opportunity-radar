# Contratos JSON v1

Estado: **contratos y validadores; ningún worker, cola ni adaptador los usa todavía**.

Estos JSON Schema (draft 2020-12) son el contrato compartido entre el agente A
(Codex) y el agente B (Claude): Python decide, Node y Go ejecutan, y los tres
validan **los mismos archivos de esquema y los mismos ejemplos dorados**. La
especificación sale de la [revisión final de arquitectura](../docs/research/architecture-final-review.md)
§4 y §6; el manifiesto de sesión, de [SESSION_MANAGEMENT](../docs/SESSION_MANAGEMENT.md).

| Archivo | Contenido |
|---|---|
| `common.v1.json` | `$defs` compartidas: IDs, refs opacas, `session_ref`, sha256, fechas UTC, errores tipados, `status`, dinero, evidencia |
| `envelope.v1.json` | Sobre de todas las colas; `kind` selecciona el schema de `payload` |
| `browser.read.v1.json` / `browser.result.v1.json` | Lectura sin efectos del worker navegador y su resultado |
| `whatsapp.pair/sync/send/result.v1.json` | Vincular, sincronizar chats habilitados, envío aprobado y resultado |
| `telegram.command.v1.json` | Comando ya autenticado por el bot |
| `session.manifest.v1.json` | Metadatos del bundle cifrado (nunca cookies ni claves) |
| `capabilities.v1.json` + `capabilities.json` | Registro plataforma → backend → operación → estado, con fuente y fecha |
| `examples/{valid,invalid}/<schema>/` | Ejemplos dorados: ≥2 válidos y ≥3 negativos por schema |

Todos los valores de ejemplo son sintéticos (`+10000000000`, `radar-pilot`,
`example.com`). `capabilities.json` resume `docs/research/agent-a-platforms.md`:
nada figura como `probado_real`; documentado o inspeccionado no significa probado.

## Reglas

- **Versionado:** un cambio incompatible crea un archivo `vN` nuevo; nunca se
  edita un v1 publicado de forma incompatible. Cambiar un contrato es un PR propio
  revisado por A y B. `schema_version` es `const`: versiones desconocidas se rechazan.
- **Cerrado:** `additionalProperties: false` en cada objeto con forma fija. Los
  mapas acotados (`args`, `fields`, `versions`, registro) limitan nombres,
  tamaño y tipo de sus valores.
- **32 KiB:** el JSON serializado compacto (UTF-8, sin espacios) de cualquier
  instancia, en particular el sobre, no supera 32768 bytes. JSON Schema no puede
  expresarlo; lo comprueba cada validador. Datos mayores van como blob con
  `refs[].sha256`.
- **Dinero:** `{"amount": "60.00", "currency": "USD"}`; importe decimal como
  string, nunca float; moneda ISO 4217 de tres letras.
- **Fechas:** RFC 3339 en UTC con `Z` (`2026-10-03T12:00:00Z`). Los tres
  validadores comprueban también `format` (`date`, `date-time`): fechas
  imposibles como 30 de febrero se rechazan.
- **Sin lease en el mensaje:** el worker adquiere el lease por condición en el
  almacén de control; un token en el sobre no daría autoridad (ejemplo negativo
  `envelope.v1/lease-field.json`).
- **Sin secretos:** ni cookies, storageState, tokens ni códigos de vinculación.
  `whatsapp.result` solo informa `code_delivered`. `declared_phone` en
  `whatsapp.pair` está marcado SENSIBLE: no registrarlo; moverlo a referencia
  cifrada queda `pend` (ver [SECURITY](../SECURITY.md)).
- **Los `$id` usan el dominio reservado `.invalid`:** nunca se resuelven por red;
  los validadores precargan los archivos locales.
- **Límites del schema:** no sustituye controles en runtime (DNS, redirects, IP
  privada, autorización, `content_sha256 = sha256(text)`, ledger de envíos).

## Ejecutar los validadores

Desde la raíz del repositorio:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install --no-deps -r requirements.lock   # Linux/macOS: .venv/bin/python
.venv/Scripts/python -m pip install --no-deps -e .
.venv/Scripts/python -m pytest -q tests

cd contracts/validate/node; npm ci --ignore-scripts; node validate.mjs; cd ../../..
cd contracts/validate/go; go test ./...; cd ../../..

docker build -f contracts/Dockerfile -t market-radar/b1-contracts:test .
```

Python expone `radar.contracts.validate(schema_name, instance)` (por ejemplo
`validate("envelope.v1", sobre)`, lanza `jsonschema.ValidationError`) y
`load_schema(name)`. `tests/test_contracts.py` se omite si faltan las
dependencias de test. El build Docker ejecuta las tres suites (pruebas sin red)
y solo termina si todas pasan; su imagen final contiene únicamente marcadores.
