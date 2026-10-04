# A12 — Polling local de Telegram

Estado: incremento local aislado sobre A3 corregido `721ea5e` y main `09c1509`.
Pruebas con transporte falso, webhook B real y SQLite A3. No demuestra una
conexión con Telegram, cifrado real, SSM, AWS, cuentas, fuentes o IA. No se
declara A12 completa: el gate del bot real y revisión B siguen pendientes.

Telegram es la UI; esta CLI administra el polling local, no es otra UI comercial.
La dirección vigente es asistente general: productos, talleres y Cashea son
ejemplos/módulos, no campos obligatorios del runner. A12 no implementa el router
de pedidos, búsquedas generales, WhatsApp ni informes comerciales por sí misma.

## Configuración explícita y gate antes de I/O

```powershell
$env:PYTHONPATH="src;."
python -m radar.entrypoints.local_telegram --help
python -m pytest -q tests/local_telegram
```

La primera instrucción solo muestra ayuda. Las pruebas no leen secretos ni
contactan Telegram/SSM/LLM. Dependencias fijadas del proyecto y Python 3.11+.

Una futura configuración autorizada se invocaría con:

```text
python -m radar.entrypoints.local_telegram --factory trusted.local_config:build --state-db .local/telegram.sqlite --allowlist .local/allowlist.json --bot-ref bot:local --authorize-bot-io --max-polls 100
```

`trusted.local_config:build` es un ejemplo de firma, **no un módulo entregado**.
Sin factory y backend privado disponibles el comando falla estáticamente antes
de `getWebhookInfo`, `getUpdates` o takeover. No ejecutar este ejemplo contra
cuentas reales bajo la autorización de pruebas de esta tarea.

Token y secreto entran exclusivamente por las variables
`RADAR_TELEGRAM_TOKEN` y `RADAR_TELEGRAM_SECRET`, o nombres elegidos mediante
`--token-env`/`--secret-env`. No pasar valores en argumentos ni imprimirlos.
SSM `/market-radar/telegram_token` es una fuente prevista: su resolución requiere
un cableado autorizado aparte; A12 no añade SDK ni consulta SSM. La lista blanca
privada se carga con `PhoneAllowlist.from_file`, sin mostrar números.

La factory es configuración **confiable del operador**, nunca texto de Telegram:

```python
def build(*, state_db, phone_allowlist, secret):
    # Return radar.adapters.local.polling.PollingWiring:
    # store = SQLiteStore(state_db), with exactly one configured owner;
    # webhook = the real B Webhook(secret=secret, ...);
    # vault = a verified durable encrypted private backend;
    # owner_ref = the configured opaque owner, never inferred from an update;
    # tick = an explicitly wired local application repair/processing step.
    # Close partially created resources if construction fails.
    raise RuntimeError("real_wiring_pending")
```

No factory sintética predeterminada, importación de código de tests, alta
arbitraria de actores o worker falso conectados por omisión al bot real.
Directory/consentimiento, invitaciones y autorización son dependencias del
webhook real; la factory no debe reemplazarlas con valores recibidos del update.
La CLI verifica ruta privada, secreto y store coincidentes, propietario único,
backend privado y límites antes del primer request. Cierra SQLite al terminar.

### Backend privado requerido

`vault.preflight(owner_ref=...) -> True` debe verificar cifrado durable, acceso
owner-scoped y disponibilidad; de lo contrario debe fallar cerrado.
`vault.put(owner_ref=..., content=bytes) -> opaque_ref` confirma la persistencia
antes de retornar. `vault.get(owner_ref=..., ref=opaque_ref) -> bytes` resuelve
solo refs del propietario. Se verifica también el hash del contenido recuperado.

Es un contrato de factory confiable, no una prueba criptográfica basada en un
booleano. El backend age/verificación productiva **no está incluido**. Las pruebas
usan un fake owner-scoped en memoria, solo para bytes sintéticos: no cifrado real
ni durabilidad de ese fake ante caída total de proceso. Sí reinician el control
SQLite en disco y conservan el backend falso como proveedor independiente.
Refs opacas no significan por sí mismas que un backend esté cifrado.

Las tablas `polling_offsets`/`polling_receipts` guardan referencias de bot/owner,
update ID, hashes, estado, referencia privada y código estático. No guardan el
update completo, teléfono, destinatario numérico, texto ni método Bot API en
claro. El método con `chat_id`/texto/markup queda únicamente en el backend
privado. Los hashes no son una garantía de anonimización. La seguridad,
retención y borrado del resto del store A3 siguen siendo gates separados;
un control local limpio no transforma A3 en almacenamiento privado productivo.

## Webhook activo y takeover

El runner consulta `getWebhookInfo`; una URL activa causa
`active_webhook_requires_takeover`. No muestra ni guarda esa URL.
Solo `--take-over-bot`, junto a `--authorize-bot-io` y todos los gates locales
aprobados, permite `deleteWebhook` con `drop_pending_updates=False`.
Nunca retira el webhook por un conflicto/reintento de polling. No restaura una
URL antigua automáticamente. Al desplegar hay que restaurar `setWebhook` con
URL y secreto verificados, permisos y autorización aplicables; no prueba ese
despliegue ni presume que el webhook anterior siga siendo válido.

## Persistencia, replay e incertidumbre

`getUpdates` recibe offset durable, batch 1–100, long polling 1–30 segundos y
`allowed_updates=[message, callback_query]`. El BotApi B se reutiliza sin edits.
La CLI da al HTTP un timeout de polling + 5 segundos. Hay presupuesto finito
1–10000 polls; hasta cinco errores consecutivos por defecto, backoff 1–30 segundos
e interruptible. SIGINT/SIGTERM detienen el loop; una llamada HTTP en curso puede
esperar su timeout. No hay reintentos implícitos de envío.

1. Persistir `handling` + hash de update, sin contenido privado.
2. Pasar el update al webhook B con el secreto configurado. B conserva sus
   validaciones, permisos, consentimiento, receipt/UoW/outbox y dedupe.
3. Persistir respuesta en backend privado y luego su ref/hash + estado `ready`
   **junto al nuevo offset en una transacción SQLite**. Un 401/500, write privado
   fallido o rollback no avanza el offset.
4. Recuperar/verificar respuesta; claim durable `dispatch_committed` antes del
   request. Solo `sendMessage` y `answerCallbackQuery`, campos permitidos y
   destinatario ligado al update original, pueden salir desde el webhook.
   No se interpreta ningún `method` arbitrario del update entrante.
5. Tras confirmación válida guardar `sent`. Fallo/timeout de transporte deja
   `send_uncertain`; tras restart todo claim incompleto también es incierto.
   Nunca reenviar automáticamente una respuesta incierta.

Crash entre webhook y checkpoint puede dejar el comando/consentimiento aceptado,
pero perder la respuesta inline. Telegram redelivera con el offset sin avanzar;
se invoca otra vez el webhook, cuyo dedupe conserva la tarea original. Si B ya
no devuelve la respuesta, guardar `response_unavailable` e informar diagnóstico
**incompleto**, sin fabricar éxito/acuse ni reenviar. No existe atomicidad entre
el UoW de B y el checkpoint A12; este estado muestra explícitamente ese límite.
Un blob privado creado antes del rollback puede quedar huérfano: retención/GC y
reconciliación requieren cableado posterior, no se borran refs por adivinación.

Crash antes/después del request o fallo del write de confirmación conserva la
incertidumbre aunque el método haya salido. No hay exactly-once de Telegram,
proof/reconciliación automática ni reenvío ciego. Un reply `ready` aún no enviado
sí puede continuar después del restart desde el backend privado verificado.
Los códigos/IDs diagnósticos no contienen descripciones privadas de excepciones.

Un lease durable A3 por bot/store impide dos pollers concurrentes: el segundo
falla `poller_already_active` antes de takeover/polling. TTL explícito de 60 s por
defecto (configurable programáticamente, hasta 300 s y mayor al timeout), reloj
inyectable, token y versión crecientes; se renueva antes del polling y replies.
Checkpoints/claims/confirmaciones comprueban lease vigente en la transacción y
antes del request; perderlo produce `poller_lease_lost`, sin offset ajeno ni
confirmación obsoleta. Un claim incompleto del poller anterior queda incierto.
Cierre limpio libera solo su epoch; tras crash se espera el TTL, sin force-unlock.
El lock serializa también los hilos del runner. Esto cerca el control local,
no Telegram: un request ya iniciado puede concluir después de perder el lease,
pero no se reenvía desde otro poller. No compartir el bot con otro store/ref o
webhook activo: la identidad de una única cuenta/store y claves correctas son
responsabilidad de la factory, no se demuestra leyendo el token ni duplicando
bases. Las pruebas usan dos conexiones y reemplazo de lease en cuatro puntos.

## Evidencia y pendientes

Verificación local del candidato (2026-10-04): `tests/local_telegram` **52 passed**,
8.60 s; suite completa **588 passed + 238 subtests**, 39.20 s, sin skips;
`scripts/check_docs.py` y diff check aprobados. PYTHONPATH apunta a `src` y raíz
del worktree, con venv de revisión y dependencias fijadas. No Docker ni red.

Las pruebas cubren contacto sintético propio/no autorizado, consent gate,
revocación/grupos, offset durable/restart, replay cambiado, takeover explícito,
rollback, respuesta privada inaccesible/corrupta, crashes antes/después de enviar,
fallo al confirmar, backoff/budget, stop y CLI real `--help`. Las inspecciones
comprueban ausencia de contenido/identidades privadas en control/logs/repr.
El tick es inyectado y puede reparar/procesar la aplicación local A3: el polling
no afirma haber implementado operaciones generales o enviado sus reportes.

Pendientes antes del gate real A12: revisión B de A3/A12, factory de identidad
real y backend privado cifrado verificados, límites de retención/borrado,
application tick y canal de informes autorizados, SSM opcional y presupuesto si
se cablea IA (`catalog.build_default` de B; A12 no invoca IA). Después, y solo
con autorización separada, probar bot propio `/start` → contacto propio →
consentimiento → respuesta, incluyendo fallos reales y restauración de webhook.
Fuentes, mensajes a terceros, WhatsApp, AWS y LLM siguen requiriendo sus gates.
