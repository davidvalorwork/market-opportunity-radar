# Router general de tareas A6

Estado: **implementado y probado localmente en rama aislada; revisión B del
schema y cableado de Telegram pendientes**. No ejecuta búsquedas, lecturas,
mensajes, follows ni programaciones reales. No llama IA, cuentas o cloud en estas
pruebas. Los casos originales usan parser falso; A22 añade el cliente B real con
HTTP/secretos/caché sintéticos, sin consultar IA real.

La dirección general vigente autoriza pedidos de cualquier tema; productos son
un caso opcional. No se requieren vehículo, zona, pago, Cashea, taller ni PDF.
Telegram sigue siendo la interfaz prevista, pero este corte es una API interna,
no un comando comercial nuevo ni un webhook conectado.

## Fronteras y flujo

- [Application](../../src/radar/application/tasks/) importa sólo dominio, puertos,
  módulos propios y biblioteca estándar pura. No SQLite, red, SDK o validador
  JSON dentro de application: la frontera inyecta `contracts.validate`.
- [Registro](../../src/radar/application/tasks/registry.py) define `search`,
  `read`, `extract`, `inform`, `compose`, `contact`, `follow`, `schedule`.
  Cada paso referencia dependencias anteriores; el host puede añadir operaciones
  explícitas con revisión propia. Registro no equivale a backend disponible.
- [Schema candidato](../../src/radar/schemas/llm.task_request.v1.json) cierra
  campos/objetos y limita pasos, referencias, strings, confianza y presupuesto.
  Validadores canónicos aplican 32 KiB. Tipo desconocido se rechaza por registro;
  datos faltantes o confianza inferior al umbral producen `needs_clarification`.
- El host entrega `Authority` inmutable con owner/actor, capacidades, sesiones,
  vencimiento y techo Decimal. El documento del modelo no puede añadir permisos.
  El adaptador comprueba owner activo, actor propietario, consentimiento vigente,
  capacidades habilitadas, versiones de sesión y presupuesto dentro de la
  transacción, también al leer/reentregar propuestas activas.
- `propose` crea una propuesta inmutable de 15 minutos como máximo, acotada por
  la autoridad. `Confirmar`, `Corregir` y `Cancelar` llevan callbacks aleatorios
  opacos, ligados a owner, actor, tarea y versión. El primer callback consume
  todos los de esa versión; CAS y recibo transaccional impiden dobles transiciones.
  Reentrega del mismo evento devuelve el estado actual, no una confirmación vieja.
- Corregir invalida `confirmation_hash`, exige contenido nuevo y versión esperada;
  genera botones nuevos. Cancelar sigue permitido si una fuente fue revocada,
  pero nunca sin owner/consentimiento vigentes. Vencimiento elimina confirmación y
  botones de la vista. Datos de entrada y vistas no mutan el snapshot.

`confirmation_hash` **confirma intención, no aprueba un envío**. El campo
`effect_approval_required` lista pasos que todavía necesitan aprobación específica
por destinatario, mensaje, propósito, sesión, versión y expiración. A6 no escribe
ledger, outbox, queue ni pruebas de proveedor. No se debe usar esa confirmación
como un `radar.ports.Approval`. Cada efecto requiere su propio flujo autorizado;
una tarea programada tampoco hereda autorización de contacto para futuras pasadas.

## Privacidad y persistencia

[SQLiteTaskStore](../../src/radar/adapters/local/tasks.py) usa únicamente tablas
`task_router_*` y transacciones del `SQLiteStore` A3 existente, sin cambiarlo.
Snapshots y resultados del parser **son privados por defecto**, aunque el modelo
declare `privacy_scope: public`. Campos libres podrían contener nombres o
teléfonos: un schema o detector de PII no prueba clasificación pública.

El constructor exige un `TaskDocumentVault` inyectado con `seal/open`: producción
debe aportar almacenamiento cifrado autenticado e inmutable, referencias ligadas
al owner y descifrado restringido. No existe fallback plaintext ni implementación
criptográfica nueva en A6. SQLite contiene sólo estados, contadores, referencias
opacas, hashes y punteros `private_ref` + `document_hash`; recibos de callback
guardan sólo `task_ref`. Nunca guarda el snapshot/result descifrado o texto fuente.
Contenido se abre sólo en memoria tras comprobar actor y consentimiento.

Referencias privadas de contexto requieren schema válido, `worker:task-router`,
blob existente bajo ese owner y hash coincidente. Este hash comprueba integridad
referenciada, **no autenticidad del productor ni cifrado real**. Esas propiedades
pertenecen al backend confiable. A6 no descifra el contexto para el parser ni
envía blobs privados al modelo. El backend debe entregar claves/nombres de blob
opacos, sin datos personales en sus metadatos.

El fake `FakePrivateVault` sólo vive en tests y conserva bytes en memoria; sus
punteros/digests son sintéticos, no ciphertext verificado. La prueba de ausencia
de plaintext en SQLite/WAL/log/repr verifica esta frontera, no criptografía age.
Persistencia de propuestas se prueba reabriendo SQLite con el mismo fake; la
durabilidad del vault real queda pendiente. Un rollback puede dejar un blob
cifrado huérfano: no autoriza una tarea ni justifica borrar objetos externos.

Los eventos que entrega la frontera son referencias técnicas opacas, no texto
del usuario. `Proposal.snapshot` y `StructuredRequest.public_input` no aparecen
en `repr`. `.document` devuelve una copia para uso autorizado en memoria; no debe
serializarse en telemetría, colas o una respuesta pública sin su propia frontera.

## Parser opcional, cuotas y programación

`interpret` está deshabilitado salvo opt-in explícito + `StructuredLLM` + reloj
inyectados. Usa [prompt versionado](../../src/radar/application/tasks/prompt.py)
`task-request-v1`, schema `llm.task_request.v1`, modelo seleccionado por el host,
2048 tokens de salida y techo Decimal explícito. Entrada pública explícitamente
clasificada se limita a 4096 bytes; referencias se congelan antes de llamar al
puerto y no se permite sustituirlas. Texto no clasificado debe permanecer en un
blob privado, no pasarse a `public_text`. El webhook aún no implementa esa frontera.

Se reserva una llamada y costo máximo **antes** del parser, con recibo durable.
Salida exige schema, operación/capacidad, modelo, contador de tokens y costo
conocido dentro del techo. Error, crash o costo desconocido conservan la reserva
y quedan inciertos; no se reintenta automáticamente con el mismo evento. Un
resultado validado se cifra/cachea, evitando repetir la interpretación. El reloj
se vuelve a consultar tras la llamada para rechazar autoridad vencida; el SDK
inyectado deberá aplicar además sus propios timeouts (A6 no cancela un SDK bloqueado).

Confirmar reserva presupuesto de la propuesta. La cuota actual es acumulativa
por owner y conservadora: no hay ventana diaria, refund, reconciliación de gasto
real ni liberación por cancelación/corrección. El máximo reservado no representa
gasto medido; costos no conocidos nunca se presentan como cero. Corregir y volver
a confirmar reserva de nuevo. No afirmar costo USD 0 de infraestructura ni IA.

`schedule` preserva la expresión como intención y fija la zona del host (Caracas
por defecto, configurable mediante allowlist). No analiza cron, calcula próximas
fechas ni crea un timer real. Su backend futuro debe validar calendario/DST y
planificar sólo operaciones conocidas con presupuestos y permisos por pasada.

## A22: compatibilidad local del parser con el cliente B

La revisión A21 encontró dos fallos determinísticos: el puerto recibía
`llm.task_request.v1` más versión `1` y B buscaba el inexistente
`llm.task_request.v1.v1`; además su inliner sólo conoce definiciones de
`common.v1` y rechazaba los `$ref` locales de este candidato.
Las dos regresiones originales fallaron antes del cambio y pasaron después.

El [prompt](../../src/radar/application/tasks/prompt.py) separa ahora
`SCHEMA_NAME = llm.task_request`, `SCHEMA_VERSION = 1` para `StructuredRequest`
y `CONTRACT_NAME = llm.task_request.v1` para validación local. El nuevo schema
candidato tiene las definiciones locales inline; mantiene refs comunes,
privacidad condicionada, campos opcionales, objetos cerrados, patrones y límites.
No modifica contratos publicados, el cliente B ni su catálogo predeterminado.

[Las regresiones](../../tests/tasks/test_router.py) usan `OpenRouterLLM` real,
`Prompt` inyectado y HTTP falso con clave sintética. Comprueban interpretación,
replay sin otra llamada, expansión sin refs pendientes, todos los golden válidos
y negativos, costo Decimal conocido, campos extra rechazados, ZDR/personal,
referencias privadas ligadas al owner y ausencia de efectos. Constructor B
apagado y catálogo B sin este prompt rechazan antes de secretos/caché/HTTP.
JSON inválido, usage ausente y timeout conservan la reserva e incertidumbre,
sin nueva propuesta ni reintento del modelo.

Esto verifica compatibilidad **local**, no aceptación de un endpoint real.
El candidato conserva opcionales y condicionales JSON Schema; generar
`response_format.json_schema.strict = true` con FakeHTTP no demuestra soporte
de esos keywords por un proveedor. Registro B, modelo/endpoints con salida
estructurada/ZDR, política personal, precios vigentes y evaluación semántica
siguen sujetos a revisión y autorización. No hay factory de producto activado.

Verificación A22 sobre base A6 `5640f3b`, sin Docker/red: **70/70** área;
área + contratos **78 tests + 228 subtests**; suite completa **606 tests +
246 subtests**, sin skips/fallos. `check_docs.py` y `git diff --check` aprobados.
La guía de diagnóstico exigió las regresiones rojas antes del arreglo;
no se modificaron fakes o guardas B para convertir los fallos en verde.

## Verificación observada y reproducción

En este corte se observó, sin Docker ni servicios externos:

- Python área A6: **52/52**.
- Python suite completa: **588 tests + 246 subtests**, cero skips/fallos.
- Node/AJV: **229/229 ejemplos, 23 schemas**.
- Go validador compartido: `go test ./...` y `go vet ./...` verdes, `GOPROXY=off`.

Cobertura incluye tema libre, pasos dependientes, tipos desconocidos, falta de
datos/confianza, presupuestos excesivos, inyección, owner/actor/consentimiento,
sesión/capacidad revocada, vencimiento, replay/CAS, dos conexiones concurrentes,
rollback, corrección/cancelación, snapshot/contexto inmutables, zonas configurables,
parser incierto, sustitución de referencias, vault owner-bound/integridad y marker
sintético de nombre/teléfono ausente de SQLite/WAL/logs/repr. No hay omisiones
para convertir fallos en resultados verdes.

Comandos desde la raíz del worktree (PowerShell, venv ya existente):

```powershell
$env:PYTHONPATH = "$PWD/src;$PWD"
$env:HYPOTHESIS_STORAGE_DIRECTORY = "$PWD/.local/hypothesis"
$taskPython = 'C:/Users/David/projects/mor-a-governance/.local/b1-review-venv/Scripts/python.exe'
& $taskPython -m pytest -q tests/tasks
& $taskPython -m pytest -q tests
& $taskPython scripts/check_docs.py
git diff --check
```

Desde `contracts/validate/node`: `npm ci --ignore-scripts --offline`, luego
`node validate.mjs`. Desde `contracts/validate/go`: fijar `GOPROXY=off`, ejecutar
`go test ./...` y `go vet ./...`. No locks, contratos publicados, archivos B,
workers, lab ni CI se modificaron para hacer pasar esta entrega.

## Gates pendientes, no funcionalidad afirmada

1. **B: revisión obligatoria del schema nuevo y ejemplos** antes de integrar;
   registro en documentación/catalog de schemas/prompts y revisión de compatibilidad
   con `json_schema strict` del proveedor (opcionales no implican soporte real).
2. **B: webhook Telegram** de texto libre y callbacks autenticados. Debe resolver
   owner/actor/consentimiento, clasificar/cifrar entradas privadas, entregar eventos
   opacos y construir Authority server-side. No importar parser/vault de tests.
3. **Backend real de documentos cifrados** owner-bound, durabilidad y pruebas age/
   tamper/rotación; A6 sólo define el puerto y exige inyección, no lo implementa.
4. **Ejecutores y programación** para cada primitiva. Backend soportado, acceso,
   política de fuente/canal, aprobaciones por efecto, límites de ritmo, ledger y
   pruebas de proveedor se comprueban de nuevo; registro o modelo no los concede.
5. Revisión cruzada B de A6 y A3 corregido, integración serial y regresión sobre
   main actual por el coordinador. Este documento no declara dichas revisiones
   o integración realizadas.
