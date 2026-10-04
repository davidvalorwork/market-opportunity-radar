# Puente WhatsApp LOCAL (A29)

Candidato local general, no worker AWS ni prueba de cuenta real. Telegram sigue
siendo la única UI. No se ejecutó pair, sync o send real en esta entrega. Root
integra las rutas y confirma por separado cuenta, capacidades y autorización.

## Frontera operativa

`radar.adapters.local.whatsapp_bridge.create_bridge` recibe `store`,
`conversations_repository` (misma SQLite), `vault_view` de A14 para
`worker:whatsapp-local`, `authority_provider`, `clock`, `helper_path`,
`helper_sha256`, `session_root` privado, `authorize_live=False` y
`pair_authorizer=None`, `timeout_seconds=120` (máximo 150) y `daily_calls=100`.
Sin autorización explícita no ejecuta operaciones.
`authority_provider(owner_ref=..., actor_ref=...)` obtiene Authority A6 vigente;
no se usa la autoridad de una entrada externa como fuente de permisos.

Host registra Account A10 y sesión/version actuales, y llama
`bind_account(authority=..., account=...)`. Esto no demuestra login. Pair acepta
`authority`, `account_ref`, `event_ref`, `phone_ref` age que contiene exactamente
`declared_phone`, y `notifier`. `pair_authorizer` comprueba confirmación humana
vigente para esa referencia/cuenta/evento. Notifier recibe owner/actor/event y
code sólo en RAM y debe confirmar entrega privada; nunca se imprime el código.
Pair devuelve BridgeResult con `state`, `private_ref`, `replayed`; el documento
privado contiene `paired: true`. La sesión conserva su versión; una rotación
requiere nueva configuración explícita, no reescribe approvals.

`list_chats(authority, account_ref, event_ref, after_ref='', limit=20)` devuelve
referencia privada a `chats`, `has_more`; cada chat tiene `chat_ref`, `is_self`
bool, display privado y fecha opcional. Self se compara contra el JID observado
del dispositivo, nunca nombre/contacto parecido. Self se ancla primero aunque
quede fuera de la página ordenada original; cuenta dentro del límite, no se
duplica, y cualquier fila desplazada activa `has_more`. Esta vista acotada no
promete cursor exhaustivo: el ancla self puede repetirse al avanzar `after_ref`.
Sólo metadatos: filtro vacío
antes de Connect. El puente no elimina la prohibición self-contact de A10; la
prueba a uno mismo necesita el gate separado del host.

Captura opt-in de `IsFromMe`: sólo eventos del chat propio que coincide con
Store.ID observado (o mapping LID→PN confiable del SDK), y sólo si ese chat está
explícitamente habilitado. Send propio instala esa allowlist antes de Connect;
Sync usa su selección explícita. No captura enviados a otros chats ni cambia
extract del Worker legado. Cada eco conserva ID real y dedupe. Nunca se fabrica
una fila incoming desde SendResponse/ACK. Si el proveedor no emite eco o éste
llega después de desconectar, `/leer` sólo ofrece eventos realmente capturados;
no garantiza recuperar historial ni confirmar lectura por el envío. Eso requiere
evidencia real separada del piloto, no un fixture.

`sync(authority, account_ref, event_ref, enabled_chat_refs=tuple, limit=20,
ack_ref=None)` instala la allowlist confiable antes de Connect. Resultado privado:
owner/account/session/version, `messages`, `has_more`, `cursor` (último cursor
capturado, vacío si no hay mensajes). `ack_ref` es el private_ref de un sync
completed anterior, nunca un cursor arbitrario. Se admite sólo si está registrado
en la SQLite de esa cuenta/version. Antes de admitirlo ya se proyectaron mensajes
en `conversation_messages` de A10 con vault de Conversations. La identidad opaca
provider_ref deriva de owner/account/chat y **ID real** de WhatsApp, no texto,
fecha ni secuencia. El ID original viaja sólo privado. Replay guarda el pointer
original y rechaza mismo ID con cuerpo cambiado. Una redelivery después de ACK
también converge en el journal del cliente.

`send(authority, operation_id, approval_ref)` usa operación/approval A10 exactas:
texto UTF-8/SHA, identidad habilitada, propósito, owner/actor, cuenta, sesión y
versión. Nunca inventa Approval ni usa `go/cmd/whatsapp --real`/Worker.send.
Antes del efecto revalida consentimiento, rol owner, stop/cancel, vencimiento,
binding y lease. Resultado confirmado exige observación del helper; el ID real
queda age privado, mientras ledger conserva correlación protocolo preasignada.
Reconcile independiente del journal tras respuesta perdida queda pendiente;
no existe resend ni bool caller como proof.

## Persistencia, límites y privacidad

Helper nuevo `radar.local/radar/localbridge` reutiliza SAME wameow.Client por
require/replace al módulo Go existente, sin cambiar schemas públicos. El parche
A-authored añade journal privado de e.Info.ID scoped cuenta/chat y filtro previo
al INSERT. Modo legado B no configurado se conserva y no se presenta como seguro
para piloto. No se usa su replay inseguro de Worker. Protocolo JSONL privado por
stdio; phone/text/code nunca argv/logs. Errores IPC son códigos estáticos.

Control SQLite guarda refs/hashes/contadores, no teléfonos/textos. A14 age real
es interoperable **vía host decrypt en RAM + pipe**, no directamente con el blob
wire B: su SHA es ciphertext, framing y audience son distintos. No se afirma
equivalencia con el contrato publicado. La preparación puede dejar ciphertext
huérfano; no hay transacción física vault/SQLite. Proyección+checkpoint de
metadatos sí son una transacción antes de ACK.

`whatsmeow.db` conserva credenciales y mensajes seleccionados pendientes en
formato propio del protocolo; `bridge.db` journal privado conserva ID confirmado.
Estos archivos NO se llaman cifrados age: se exigen directorios privados/ACL,
fuera de Git. No se borran bases anteriores del operador. Session/account están
ligados de forma duradera y owner-bound. Sólo hosts cooperantes con mismo
session_root/control SQLite tienen exclusión OS + lease TTL; no protege aliases,
archivos distintos ni fence del proveedor. El lock OS sigue hasta que termina
el helper aunque expire lease. Deadline máximo 150 s, frames 256 KiB, chats/página
1–100, texto 4096 bytes y página 64 KiB. Llamadas/día UTC por owner se reservan
antes de I/O hasta min(configuración, authority.ceiling.calls); son cuotas
locales, no presupuesto USD unificado. Ningún retry de I/O incierto.

## Evidencia offline

```powershell
$env:PYTHONPATH="$PWD/src"
$env:PYTHONDONTWRITEBYTECODE="1"
python -m pytest -q tests/whatsapp_bridge
$env:GOPROXY="off"
$env:GOSUMDB="off"
$env:GOWORK="off"
cd helpers/whatsapp-local
go test ./...
go vet ./...
```

Pruebas: SQLite real multi-conexión/restart, crypto age real con keys SYNTHETIC,
provider/authority fixtures explícitos, aprobación exacta, cross-owner, revocación
tras claim, cancel/stop/expiry/session, fallo antes/después send sin resend,
cursor forjado, ACK sin autoridad, lease perdido, exclusión concurrente,
WAL/DB sin marcador privado de contenido y budgets. Go prueba SAME helper
journal/fake Client y captura real de events.Message sin red. Diagnóstico rojo
previo: strict provider ID rompía cuatro tests del Worker legado; se limitó al
modo de captura explícito. Claim anidado fallaba ocho tests antes de IPC; se
separó preparación durable/claim A3 sin duplicar reglas.

Pendientes antes de piloto: revisión independiente, integración Telegram del
host, permiso de cuenta propia, real pair/identidad, capacidades probadas por
operación, lectura selected-chat y smoke de envío exacto autorizado. Esto no
autoriza contactos terceros, lectura general de toda la cuenta, IA, AWS ni uso
productivo del antiguo runner. Investigación/contextual A18 consume la proyección
privada A10; datos entrantes siguen siendo datos, no instrucciones.
