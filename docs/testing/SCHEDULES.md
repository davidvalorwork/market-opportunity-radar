# Programación general local A11

Estado: **candidato local, revisión B y conexión Telegram pendientes**. Programa
intenciones de cualquier tema sobre plantillas privadas confirmadas, no sólo
productos o talleres. No implementa `/tareas`, EventBridge, bot real ni ejecutores
de fuentes/mensajería. Ningún test llama red, IA, AWS o una cuenta real.

## Frontera de confirmación y autoridad

[El modelo puro](../../src/radar/application/schedules/model.py) contiene
`Recurrence`, `TemplateBinding`, `Schedule`, `Quota` y el protocolo inyectado
`TrustedTemplateResolver`. Application sólo importa biblioteca estándar pura.
Una referencia/hash enviado por un caller no es aprobación: el resolver tiene
que cargar una confirmación independiente, inmutable y ligada al propietario.

[LocalScheduler y A6TemplateResolver](../../src/radar/adapters/local/scheduler.py)
usan el SQLite A3 existente, sin modificarlo. La compatibilidad se ensaya con el
candidato A6 congelado `5640f3b`, integrado únicamente en esta rama. No significa
revisión B del schema A6 ni integración en main.

El bridge A6 recibe dos funciones **confiables del host**, nunca campos del
usuario/modelo:

- `host_authority(owner, actor, now)` resuelve Authority vigente, capacidades,
  techo y sesiones. No reutiliza la autoridad inicial de 15 minutos.
- `confirmed_calendar(proposal)` interpreta determinísticamente la expresión
  privada de programación confirmada en un `Recurrence` exacto. Su selección de
  preset, zona, fecha inicial y política DST debe ser lo que aceptó el usuario;
  no hay parser libre/LLM ni implementación de ese handler en este corte.

`capture_confirmed` sólo registra una plantilla nueva mientras su confirmación
A6 está vigente, exige una única operación `schedule` y comprueba el owner/actor
autorizado antes de abrir el vault. Guarda binding server-owned con task/version,
confirmation hash, referencia opaca del puntero privado, calendario hasheado,
sesión/version y mínimo de unidades correspondiente al presupuesto calls de A6.
Una plantilla con varias sesiones distintas se rechaza explícitamente; no se
reduce silenciosamente a una. El bridge requiere el TaskDocumentVault real de
A6 inyectado; no añade almacenamiento plaintext ni criptografía propia.

Una vez capturada, la plantilla conserva la confirmación de intención más allá
de la expiración de la vista del router. Cada disparo verifica de nuevo fila A6,
versión, status/confirmation, vault/integridad, capacidades y sesión, así como
la autoridad fresca del host, owner activo, rol propietario y consentimiento.
Corregir/cancelar A6 invalida el binding; no reactiva el calendario con contenido
nuevo. Se debe confirmar/capturar otra plantilla y registrar otra programación.
Una repetición de captura conserva el binding original; no fabrica un permiso.

Una confirmación de programación **jamás aprueba un envío**. Cada occurrence
devuelve un `OccurrenceIntent` **local** con estado `pending_approval`; no es un
sobre B, no inventa un kind de queue y no escribe ledger, WhatsApp ni pruebas
de proveedor. Cada destinatario/mensaje necesita su propia aprobación vigente,
contenido exacto, propósito, sesión, versión, expiry y ledger del ejecutor real.

## Calendario y límites

- `daily`: hora/minuto de pared, cada día válido.
- `weekly`: días ISO Python 0=lunes…6=domingo, únicos y explícitos.
- `interval`: 60 segundos–31 días, anclado a `start_at` UTC; no deriva al cambiar
  reloj o zona. La zona configurada no modifica la aritmética UTC del intervalo.
- Zona IANA configurable, default `America/Caracas`. El adaptador usa ZoneInfo
  y falla con `timezone_unavailable` si no hay datos; no instala tzdata ni toma
  silenciosamente la zona del sistema. Tests inyectan fixtures de zonas.
- Fold ambiguo: política `earlier` o `later`, un único instante UTC. Gap
  inexistente: `skip`, sin desplazar una hora ni emitir doble disparo. Lookup
  calendar está acotado a 16 días; un resolver inusual sin fecha válida falla.
- Hora 0–23, minuto 0–59; cron, política `both` o `shift` son unsupported
  explícitos. Instantes sin zona se rechazan.

Cada tick procesa como máximo 20 schedules/50 occurrences/5 por schedule por
defecto; límites configurables respectivamente 1–100/1–100/1–10. No hace un
catch-up infinito, incluso tras meses apagado. No descarta atrasos: `backlog`,
motivos estáticos y cursor keyset quedan visibles. `tick(after=report.next_cursor)`
permite continuar una pasada acotada y atravesar una plantilla denegada sin que
impida otras. El cursor está ligado por la consulta al owner, no escanea otros
propietarios. Un caller no debe convertir backlog en un bucle sin límite.

## SQLite, reservas y recuperación

Sólo crea tablas `scheduler_policy`, `schedules` y `schedule_*`. Config temporal,
estados, IDs opacos, hashes y contadores son públicos de control; no contiene
goals, expresiones libres, texto de contacto, snapshots ni contenido del vault.
Los hashes de referencia no prueban cifrado ni autentican al productor. El fake
vault de tests sólo guarda bytes sintéticos en memoria; no es crypto real.

Cuota explícita por **owner y ventana UTC actual de reserva**, no por fecha del
disparo atrasado. Configuración de ventana 60–86400 segundos, occurrences
1–1000, unidades 1–100000. Todas las programaciones del owner comparten la misma
política persistida; otro scheduler no puede cambiarla para eludir el presupuesto.
`units_per_occurrence` 1–1000 nunca puede ser inferior al mínimo capturado del
template A6. La reserva es conservadora, sin refund automático por cancelación.

Una transacción `BEGIN IMMEDIATE` reserva count/unidades, inserta occurrence y
outbox, y avanza next_due. Binding owner/schedule/fecha UTC tiene dedupe durable,
sin ventana de cinco minutos. Autoridad se comprueba antes de reservar, antes
de encolar y antes de commit; fallos o revocación intermedia revierten todo.
Varios ticks/conexiones sobre el mismo SQLite convergen. Cada occurrence es una
transacción acotada: si falla la siguiente, las anteriores permanecen visibles.

`prepare` revalida programación/versión y autoridad antes de materializar intención
pendiente. Cancelación, pause/delete, corrección, sesión/capacidad revocada o
expiry dejan trabajo registrado como blocked con código estático, sin envío.
Si el actor solicitante fue revocado, no se le permite leer/consumir y la fila
sigue pendiente. No se ACKea contra un proveedor ni se pierde una incertidumbre.
Repetir preparación conserva el mismo occurrence; tampoco permite ejecutar un
efecto sin nueva comprobación independiente en el worker real.

Pause/delete/list son owner-bound con rol owner/consentimiento actuales. Delete
es tombstone, no borra occurrences/outbox ni se puede deshacer. Pause/resume
cambia versión e invalida trabajos antiguos en cola; resume mantiene backlog
para procesamiento acotado. No implementa Logout ni elimina datos externos.
Consulta de pendientes/status usa índice y paginación keyset por seq.

Sólo `ScheduleDenied` de allowlist y ciertos conflictos conocidos del bridge A6
se convierten en diagnósticos estáticos. Fallos de vault/SQLite, conflictos
desconocidos, bugs y failpoints propagan; no se marcan como éxito ni se pierden
filas. El ledger y control permanecen durables al reiniciar SQLite.

Presupuesto aquí es **unidades de trabajo reservadas**, no gasto USD medido.
`TickReport.cost_usd` es desconocido (`None`), jamás cero implícito. El ejecutor
real deberá reservar además costos/límites de proveedor antes de llamadas pagadas;
este módulo no llama esos proveedores ni garantiza costo de infraestructura cero.

## Uso técnico y pruebas reproducibles

No hay CLI/UI comercial nueva. Tras resolver las funciones confiables y el vault
en una factory, las APIs internas se componen así (el handler de autorización
real sigue pendiente):

```python
def register_confirmed_schedule(resolver, scheduler, *, owner, actor, task, now):
    from radar.application.schedules.model import Schedule
    binding, calendar = resolver.capture_confirmed(
        owner_ref=owner, actor_ref=actor, task_ref=task, now=now)
    return scheduler.create(Schedule(
        'schedule:' + task.split(':', 1)[1], binding, calendar,
        units_per_occurrence=binding.minimum_units))
```

Desde la raíz del worktree, sin servicios externos:

```powershell
$env:PYTHONPATH = "$PWD/src;$PWD"
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:HYPOTHESIS_STORAGE_DIRECTORY = "$PWD/.local/hypothesis"
$schedulePython = 'C:/Users/David/projects/mor-a-governance/.local/b1-review-venv/Scripts/python.exe'
& $schedulePython -m pytest -q tests/schedules
& $schedulePython -m pytest -q tests
& $schedulePython scripts/check_docs.py
git diff --check
```

Tests determinísticos cubren SQLite real, restart, replay, concurrencia entre
conexiones, rollback en reserva/outbox/advance y pérdida de autoridad intermedia;
owners separados, cuotas compartidas/ventanas UTC, backlog y cursor, cancelación
con trabajos en cola; DST con gap/fold fixtures; y A6 real propose/confirm/correct/
cancel, expiración de router vs host, vault y ausencia de plaintext en control.
No son pruebas de bot real, runtime cloud ni de cifrado del vault.

Verificación local del candidato: **60/60** en `tests/schedules` (8.28 s);
suite combinada A3/A12/A6/A11 **700 tests + 246 subtests** (50.13 s), sin skips.
`check_docs` y `git diff --check` aprobados. Es código fuente con dependencias
fijadas en el venv de revisión, no wheel instalado, Docker ni CI remota.

## Gates abiertos: A11 no completa

1. Revisión B, compatibilidad con main vigente e integración autorizada.
2. `/tareas` y callbacks Telegram reales de programación, pause/delete/list;
   autorización server-side y confirmación de calendario/presupuesto exactos.
3. Factory real de host Authority/vault cifrado durable y resolución determinística
   de expresiones privadas. Los fakes no se importan como código de producción.
4. Ejecutores reales y outbox transport versionado revisado por B; revalidación
   en cada worker, reservas monetarias y aprobación de cada efecto. La atomicidad
   local no es fencing entre SQLite distintos ni en un proveedor remoto.
5. Trigger operacional real/recuperación/retención. No hay EventBridge, wakeup
   automático, prueba de tzdata productivo, sesión real ni envío efectuado.
