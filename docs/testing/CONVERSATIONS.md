# A10: conversaciones generales, borradores y aprobación exacta

Estado: preparación y persistencia local, con fixtures explícitos. **No hay envío
real, acceso a inbox real, UI Telegram completa, vault age conectado ni aprobación
de B/Go acreditada.** Los tests no demuestran que WhatsApp u otra red funcione.
El alcance actual del usuario es general: cualquier tema, propósito configurable,
destinatarios conocidos o encontrados y canales extensibles. No exige talleres,
proveedores, productos, cotizaciones ni WhatsApp.

## API y composición

- [Conversations](../../src/radar/application/conversations/service.py) es el caso
  de uso puro, sin SDK/red/IA. Recibe un repository inyectado.
- [DTOs internos](../../src/radar/application/conversations/models.py) no son
  schemas de transporte. `Authority` se reutiliza de A6, expedida por el host;
  el actor no se obtiene de una instrucción entrante o de un LLM.
- [SQLiteConversations](../../src/radar/adapters/local/conversations.py) extiende
  con tablas propias la conexión/transacciones serializadas de A3. No modifica
  `sqlite.py`, task store, puertos, schemas ni Go. Reutiliza `LedgerRecord`,
  `Approval`, `_approved`, `_write_ledger`, `claim_dispatch`, `finish_local`
  y `reconcile_proof` existentes. No anida transacciones independientes.

Configuración del host: `vault`, `capabilities`, `clock` obligatorios; registry de
`Channel(name, backend, enabled=False, fixture_only=True)`. Ningún canal viene
habilitado ni hay backend de mensajes predeterminado. WhatsApp puede ser el primer
backend real tras sus gates; registrar `email`/`messenger` en un test sólo prueba
generalidad del modelo, no clientes implementados.

Flujo programático:

1. Administrador autenticado configura `Account(account_ref, channel, backend,
   self_recipient_ref, session_ref, session_version, expires_at)` mediante
   `register_account`. El alias debe representar una identidad canónica del
   host; cambiar el alias no debe evadir cuotas/dedupe de la misma cuenta.
2. `enable_chat(...)` entrega refs de cuenta/chat/destinatario y un `identity_ref`
   privado con `account_ref`, `chat_ref`, `recipient_ref`, `display`, `address`.
   Es una operación administrativa del propietario, **no** un resolver ni prueba
   independiente de que dirección/alias pertenezcan al destinatario. Para alguien
   descubierto, A7 debe obtener resolución/prueba autorizada separada y aprobación
   de habilitación antes de usarlo. Encontrar un teléfono no prueba WhatsApp.
3. `compose(authority, event_ref, account_ref, drafts, now)` recibe un tuple de
   `DraftInput(chat_refs=(exactamente_un_chat,), text, purpose_ref)`. Devuelve
   `Batch` con refs, versión, estados, digest, ids de operación y vencimiento.
   No hay plantilla comercial obligatoria ni interpretación automática del tema.
4. `screen(authority,batch_ref,now)` devuelve `ApprovalScreen`: **todas** las filas,
   identidad/dirección del destinatario, texto exacto, propósito, canal/backend,
   cuenta/sesión/versión e IDs, más token opaco de aprobación. No recorta el lote.
5. `approve(authority,event_ref,screen,now)` verifica token emitido, owner/actor,
   versión, hash del manifest, hash de filas mostradas e IDs exactos en orden.
   No se admite texto cambiado, filas ocultas, extras, token viejo ni hash A6.
   Reserva cuota y aprueba **todos** los ledgers con intents locales en una sola
   transacción, o ninguno. Un replay devuelve estado actual tras reautorización.
6. `correct(...)` invalida displays/aprobaciones e intents viejos y crea operaciones
   nuevas. Si alguna operación ya se reclamó, falla: no revierte un efecto iniciado.
7. `dispatch_fixture` sólo existe para el ensayo explícito y sintético descrito
   abajo. `results` lista estados autenticados; `reconcile` delega al proof
   independiente de A3, nunca a un bool del caller ni a un texto recibido.

La integración UI real aún debe paginar **sin ocultar destinatarios/textos** y
emitir autorización sólo después de mostrar todo el lote. El modelo de pantalla
y su hash no prueban que una persona haya visto/aprobado una UI inexistente.
`TaskRouter.confirmation_hash` confirma intención; **no** es `Approval` de ledger.
La prueba de integración llama a `TaskRouter.plan` real sin parser ni IA y
demuestra que su hash no autoriza ningún mensaje.

## Hilos, sync y datos entrantes

`list_chats` y `list_messages` devuelven refs privadas y paginación keyset acotada
(1–100). Los mensajes se ordenan por ref estable para paginar, no se afirma orden
cronológico ni una clasificación de "pendientes" todavía. El host puede leer
los cuerpos desde el vault autorizado para construir una vista privada.

`sync` usa `InboxBoundary` inyectado; hoy admite sólo un worker fixture explícito.
Se le entrega una allowlist de hasta 100 chats habilitados. Chats no habilitados
del resultado se descartan sin descifrar ni persistir. Mensajes aceptados ligan
chat/destinatario/provider message ref a su cuerpo privado. El ID proveedor debe
ser estable y emitido por un resolver autorizado, no un hash del texto.
Re-encriptar el mismo mensaje no crea otro; contenido discordante con el mismo
ID produce conflicto. Cursor privado liga owner, cuenta, backend/canal, sesión
y versión. No reutilizar uno de otra cuenta o sesión.

El texto recibido **es dato**. Sync no llama a compose, parser, aprobación ni send.
Una frase "ignora las reglas y envía" permanece en el blob privado; no concede
autoridad. No hay modelo, prompt, API ni llamada de IA en esta entrega.

## Privacidad y autorización

Backend privado obligatorio: el host debe aportar cifrado autenticado inmutable
con age, autorización por owner, integridad, cuotas, retención y borrado. Audience
por defecto `worker:conversations` (configurable), distinta del propietario.
No hay fallback a blobs plaintext de A3. `_seal` valida el puntero y su round-trip
owner-bound; `_open` acota 128 KiB y nunca propaga errores con datos externos.
Los fixtures usan RAM sintética sin criptografía: **no son un vault productivo**.

SQLite/WAL sólo conserva refs, ciphertext digests, hashes de binding, estados,
vencimientos y contadores. `purpose` del ledger es `purpose_ref` opaco, no texto
libre. Textos, nombres y direcciones se guardan en el manifest privado. Screens,
DraftInput y Delivery excluyen cuerpos de `repr`. Un test busca un marcador privado
en tablas, archivo SQLite y WAL y no lo encuentra.

Cada plan, pantalla, approval, replay y claim comprueba owner activo, rol owner,
actor habilitado, consentimiento actual, capacidades autorizadas, cuenta, alias
de sesión, versión y expiración. Capacidades se consultan por canal/backend/
operación/sesión: `documentado` no sirve; `probado_local` sólo para fixture, live
exige `probado_real` con fecha no futura y antigüedad máxima siete días. La
verificación real sigue fuera de este adaptador y pendiente por operación.
El reloj inyectado evita que un `now` viejo prolongue autorización.

Un callback/ref de otro owner no devuelve resultados privados; las tablas usan
owner+actor+evento y la autoridad se comprueba antes de consultar receipts de replay.
Destinatarios ambiguos piden aclaración (`recipient_ambiguous`), chats no habilitados
fallan, y un self-recipient canónico se rechaza. Resolver alias de sí mismo aún es
responsabilidad del host/proveedor real, no una comparación universal de teléfonos.

## Cuotas, intent outbox y estados de incertidumbre

Por lote: hasta 25 mensajes, 4096 bytes UTF-8 por texto, 32 KiB de texto total y
techo de `Authority`. Hasta 20 batches propuestos y 1000 conservados por owner.
Cuota diaria de mensajes: default 20, configurable 1–1000, limitada además por
el techo de autoridad y compartida entre cuentas del owner. Se reserva al aprobar;
corregir/fallar no reembolsa automáticamente, para no evadir el límite.
Sync reserva una llamada antes de I/O; hasta min(100, ceiling.calls) por owner/día,
incluidos fallos. No hay retry automático, búsqueda infinita ni API pagada.

Anti-ráfaga default 10 segundos (1–3600) por cuenta, persistido y conservado al
cambiar de día. El ledger impide repetir destinatario/propósito/cuenta incluso
en corridas distintas; cambiar explícitamente propósito es una decisión distinta,
no un retry de una incertidumbre. Las reservas/ledgers permanecen entre reinicios;
el vault debe seguir disponible por referencia. Cuenta y sesión activas se vuelven
a comprobar antes del claim y justo antes del intento simulado.

`conversation_outbox` contiene `local.conversation_intent` con ids/hash/private_ref;
**no es un envelope B** y ningún relay de SQS lo consume. Hay proyección offline
canónica por operación, pero falta publicar/reclamar mediante un bridge revisado.
La reserva de ritmo puede sobrevivir un fallo anterior al claim;
es conservadora. Claim/result usan las transacciones de A3 y correlación previa.
El commit de result y la marca de este intent local son pasos separados; un fallo
entre ambos requiere reparar la marca consultando ledger, nunca volver a enviar.

Dispatcher **sin default**: sólo `synthetic_authorized=True`, canal fixture-only
y `dispatcher.fixture_only=True` habilitan `send_fixture`. Esto es prueba con
datos inventados, no una forma de usar un fake con una cuenta real. Tras claim,
un error, revocación o falta de confirmación produce `send_uncertain`. Ningún
replay de `dispatch_committed`, `send_uncertain` o `provider_confirmed` reenvía.
Caída antes de persistir result puede dejar `dispatch_committed`; falta un recovery
runner para marcar/reconciliar ese estado sin reenviar. La prueba demuestra un
solo intento simulado incluso con dos threads; **no** exactly-once del proveedor.
Lease/check local no es fencing de WhatsApp y no evita que una autorización expire
después del último check. Backend Go real y replay entre owners siguen como gate.

## Propuesta de frontera para B, sin cambiar v1/v2 publicados

1. Ledger/Approval por mensaje guardan `sha256(UTF-8(text))`, exactamente como
   `whatsapp.send.v1` y Go. `Batch.content_hash` y el hash de pantalla son separados:
   vinculan owner/actor/cuenta/canal/sesión/versión/identidad/destino/propósito/IDs.
   Una sesión identifica una cuenta server-side; registrar otro alias de cuenta
   para la misma sesión/owner falla. No cambia ningún schema publicado.
2. `SQLiteConversations.prepare_whatsapp(authority, operation_id, now, worker_vault)`
   requiere aprobación local vigente, owner/consent/permisos/capability/chat y
   sesión frescos. Proyecta **sólo** `{schema_version: 1, text}` al vault inyectado
   de audiencia `worker:whatsapp`; valida `whatsapp.send.private.v1` y
   `envelope.v2`. Persiste únicamente envelope/ref/hash, con message ID distinto
   del operation ID, y devuelve el mismo envelope ante replay todavía autorizado.
   `worker_approval(authority, operation_id, approval_ref, now)` resuelve el callback
   realmente aprobado a LedgerRecord/Approval existentes; no convierte un hash de
   TaskRouter ni un token arbitrario en permiso. Cuenta/canal se resuelven por la
   sesión y los bindings privados, sin añadir campos de wire.
   Estas APIs no publican ni envían, no implementan los ports wire incompletos de
   A3 ni el Ledger de Go. El bridge debe adaptar el registro y approval_ref, validar
   identidad del worker y realizar claim/lease/result con garantías revisadas.
   El vault de pruebas es RAM sintética, **no age**: age real, almacenamiento privado,
   scopes del worker, fencing/replay Go y cuenta real siguen gates independientes.
   Un seal que falla puede dejar un blob huérfano, pero no intención publicable.
3. `whatsapp.messages.private.v1` en esta base contiene chat/text/observed_at,
   no ID estable de mensaje. `Incoming.provider_message_ref` necesita un journal/
   resolver verificado o una extensión privada versionada revisada por B.
   No deduplicar dos mensajes iguales mediante hash(texto).
4. `identity_ref`/cursor propios y el registry de canales son contratos internos;
   mapping de list_chats/sync/resolve_contact v2 y canales futuros pendiente.
   La proyección usa el callback aprobado del lote como referencia resoluble local;
   el bridge real debe verificarlo, no aceptar tokens UI por su formato.

## Verificación local

```powershell
$env:PYTHONPATH = 'src'
python -m pytest tests/conversations -q -p no:cacheprovider
python -m pytest -q -p no:cacheprovider
python scripts/check_docs.py
```

Los casos cubren lote exacto y mutado, corrección, owner/revoke/consent/session/
expiry, self/ambiguidad/chats disabled, presupuesto/cursor, outbox/ledger privados,
rollback total, concurrencia, incertidumbre, replay y proof correlacionado sintético.
También body hash canónico (Unicode), proyección B sin extras, audiencia correcta,
owner/callback vinculados y revocación/corrección/expiry antes de replay de wire.
No se ejecutaron cookies, cuentas, red, modelos, Docker, AWS ni mensajes reales.
