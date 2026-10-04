# Conformidad local del control plane (primer corte A5)

Esta batería ejecuta los puertos reales de `SQLiteStore`, no un mock, una
implementación DynamoDB ni un emulador de AWS. Es preparación reutilizable de F7;
no completa A5/F7 ni habilita despliegues, cuentas o efectos externos.

## Contratos cubiertos

- `LeaseStore`: exclusión incluso entre intentos del mismo worker, aislamiento
  por owner, epoch monotónico tras liberar/reabrir, expiración exacta, renovación
  con versión nueva y rechazo de snapshots/tokens/workers obsoletos.
- `Outbox`: páginas acotadas por owner, continuación keyset cuando se publica
  una página anterior, longitud y contenido completo de cada página,
  publicación CAS por owner/mensaje/versión y persistencia
  del mensaje cuando hay aceptación externa pero todavía no marca local.
- `UnitOfWork.accept_command`: receipts principal y aliases, command y outbox
  atómicos; rollback tras cada etapa y antes de commit; replay con IDs originales;
  documento original íntegro tras aceptación, reinicio y cada replay; rechazo de
  hashes/ref bindings incompatibles sin dejar aliases nuevos.

Los comandos Telegram canónicos son sintéticos y generales (`pedir`), sin
productos, precios, localidad, ofertas ni cuentas de redes obligatorios. El hash
semántico de `Receipt` lo suministra el boundary confiable; no se interpreta
contenido entrante ni se llama a un modelo. No se envía ningún mensaje.

## Ejecución y extensión

```powershell
$env:PYTHONPATH = 'src'
python -m pytest -q -p no:cacheprovider tests/conformance
```

El corte inicial `56f9773` tenía 20 casos verdes (suite 571 + 238 subtests), pero
la revisión independiente encontró dos brechas del oráculo: inspeccionar solo
`entries[0]` no detectaba páginas sobre el límite; comprobar únicamente ausencia
tras rollback no detectaba pérdida del command después de una aceptación válida.
No fueron bugs del adaptador productivo. El follow-up exige longitud y tupla
completa en las tres páginas, y lectura positiva del documento original tras
aceptación, reinicio y todos los replays. Dos regresiones adversariales comprueban
que estas aserciones rechazan una página excesiva conservando su cursor y una
proyección de command ausente tras reinicio; esta última muta el hook de lectura,
no borra filas ni modifica código productivo. Son pruebas del oráculo, no otro
backend productivo ni un emulador AWS.

Follow-up verificado localmente el 4 de octubre de 2026: 22 casos propios verdes,
suite completa 573 tests + 238 subtests verdes (38,26 s en Windows, venv a15),
`scripts/check_docs.py` y `git diff --check` verdes. Los resultados corresponden
únicamente al backend SQLite registrado y a los dos tests adversariales del
oráculo; no aportan evidencia AWS.

`tests/conformance/conftest.py` registra **solo** `sqlite_factory`. La fixture
`backend_factory` parametriza los mismos tests sin SQL en las aserciones. Un
backend futuro debe aportar los puertos `uow`, `outbox`, `leases` y los hooks de
laboratorio `restart`, `fail_at`, `read_command`, `close` del DTO `ControlBackend`.
Debe preparar los dos owners/actores sintéticos con consentimiento vigente.
`read_command` sirve únicamente para observar ausencia/durabilidad del comando;
no se propone como nuevo puerto productivo.

Los checkpoints de fault injection son `after_receipt`, `after_command`,
`after_outbox` y `before_commit`; son hooks de conformidad, no una promesa de
transacciones idénticas entre SQLite y DynamoDB. Una futura factory debe traducir
estos escenarios a fallos equivalentes de su backend real. No hay factory
DynamoDB vacía, tests saltados presentados como éxito, ni dependencias AWS nuevas.

El escenario aceptación-antes-de-marca conserva una observación sintética y
reinicia el control store: prueba que la entrada sigue pendiente con los mismos
IDs. No prueba entrega de SQS ni exactly-once; una publicación puede repetirse y
el consumidor debe aplicar su propia idempotencia.

## Límites pendientes

SQLite no demuestra semántica de DynamoDB, paginación/indexación AWS, Streams,
SQS, TTL, S3, SSM, IAM, costos ni disponibilidad. No se ejecutaron SDKs, moto,
DynamoDB Local, Docker o red. Falta registrar y ejecutar un backend AWS local
real, con fallos equivalentes y las mismas aserciones, antes de declarar F7.
Este corte no cubre todos los métodos de `UnitOfWork`, aprobación/ledger/envíos,
ni elimina los guards parciales existentes. Tampoco demuestra fencing de efectos
externos después de perder un lease.
