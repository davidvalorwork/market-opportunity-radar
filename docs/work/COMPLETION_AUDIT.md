# Auditoría de cierre del tablero

Fecha: 2026-10-04. Base principal observada `09c1509`; tablero compartido de main
es autoridad de reclamos, no el snapshot de esta rama. Objetivo íntegro:
**terminar las tareas y comprobaciones del board**. No se declara conseguido.

Fuentes: [BOARD](BOARD.md), [CODEX_TASKS](CODEX_TASKS.md),
[plan F0–F15](../research/agent-b/implementation-plan.md),
[arquitectura](../ARCHITECTURE.md), [evaluaciones](../EVALUATIONS.md).
El usuario confirmó alcance general: la dirección A0g `e6d6279` (incluye `d38416b`) sustituye
restricciones temáticas anteriores. Productos/talleres son ejemplos opcionales.
No modificar contratos de B ni considerar cookies como permiso universal.

## Evidencia nueva independiente

- A3 `721ea5e` combinado sin conflictos con main `09c1509`:
  árbol `2becf75262ec00b0c05849e3ec51d4eea752e9a1`.
  **35/35** regresiones de revisión (7.61 s), luego **536 tests + 238 subtests**
  (32.68 s), sin skips; `check_docs` aprobado. Código fuente en snapshot
  privado, no wheel instalado ni integración en main. Casos: revocación/reconsent,
  drenaje tras cancel/stop/retirar consentimiento/deadline, crash/restart de
  cuarentena, separación de propietarios, reloj, rollback concurrente,
  cancelación terminal, versión ausente y alertas revocadas sin bloquear otras.
- Go del mismo árbol: probe sintético aislado
  `go test -count=1 -run TestA3ForeignOwnerReplay ./internal/whatsapp`:
  **seis subcasos fallan**. Replay ajeno puede exponer provider ID o alterar ledger,
  tanto en entrada como tras conflicto del claim. Las suites habituales verdes
  no cierran ese gate. No se modificaron main/Go de B ni se contactó a nadie.
- A4 integrado en `aff1c05`; evidencia anterior aún aplicable al mismo código
  `078fd06`: 42/42 Docker offline, 44/44 RIE, sin skips/OOM; incluye limpieza
  Linux y dos regresiones de descendientes. No representa una red real ni AWS.
- A12 `11bf9ba` + A6 `5640f3b`: árbol combinado sin conflictos
  `7a3dce85d8add57ea799541bde2d470c226c03b7`, comprobado por el coordinador
  en snapshot fuera de main: **640 tests + 246 subtests** (45.12 s), docs
  aprobadas. Esta prueba cubre convivencia, no revisión B ni bot/IA reales.
- A12/A6 + A8 `6324b94` + A13 `d65421d`: árbol
  `08cfa0fa53f9754a762986019a397274a649b109`, también sin conflictos:
  **713 tests + 246 subtests** (45.60 s), docs aprobadas. CLI `--fixture`
  completa cuatro lecturas sintéticas (perfumes, empleo, eventos, artículos),
  declara `synthetic_offline` y `real_sources_verified=false`. Ninguna cuenta,
  cookie, fuente real o mensaje a terceros se usó para esta prueba.
- A12/A6/A8 + A9 `ea36afe` + A11 `ce2a4c4` + A13 `014f7b1`:
  árbol `cf1faaaa4d78e7f5a780f9c94f96628437e95b2c`, sin conflictos;
  **819 tests + 246 subtests** (51.85 s), cero skips/fallos, docs aprobadas.
  A11 área comprobada por el coordinador: **60/60** (8.74 s). Snapshot privado
  `../mor-a-governance/.local/all-offline-20261004/source`; no main, cuentas,
  bot, fuente social o mensajería reales. Incluye interoperación A6/A8/A9 y
  A6/A11, no una factory integrada que ejecute toda la UI en producción.
- Inspección A3: `approve_action`, `record_result` y `transition` de wire
  siguen explícitamente sin implementar. Sus equivalentes locales prueban
  intenciones y estado; no sustituyen age, boundary del proveedor, outbox de
  efectos y conformidad del puerto productivo. Este faltante es parte de F3/F7,
  no un permiso para usar el runner Go de memoria como adaptador durable.

## Cierre por entrega

| Entrega | Evidencia necesaria | Estado constatado / falta |
|---|---|---|
| A0/A0b | Gobierno, lock/CI, docs, revisión, integración | Integradas localmente; CI configurada. Ejecución remota no demostrada |
| B1/B7 + A1 | Contratos comunes Python/Node/Go; schemas empaquetados; puertos | Integrados; 221 ejemplos/22 schemas previos. Contratos de router nuevos aún pendientes de B |
| A2 | Invariantes Decimal/matching/estados; Docker/test | Integrada; suites de dominio incluidas en prueba conjunta; no utilidad/ROI real |
| B2/B5/B2b | Webhook, autenticación/contacto/consentimiento; tests negativos | Integrados y suites incluidas. Bot real/cableado productivo no comprobados aquí |
| B3/B3b/B4/B7b | Vault, contratos, protocolo/sync; ledger/autorización; licencia | Código integrado, fakes/test/vet anteriores. Gate Go replay abierto; pair/sync/envío real no verificados. Binario WhatsApp enlaza GPL-3.0, publicación exige revisar cumplimiento |
| B6 | Extracción, privacidad/cache/costo; evaluación antes habilitar | Integrado; B reportó humo real y activación. No prueba router general ni evaluación comparativa/humana completa |
| A3/A3b | Flujo persistente y correcciones obligatorias de B | Código corregido y probado en candidato. Nueva revisión B e integración pendientes; A3b no es trabajo de código por rehacer |
| A4 | Contratos, preflight, fixtures, límites/cleanup, Docker/RIE | Integrada/probada para fixtures. Helper age/sesiones reales y fuentes autorizadas siguen pendientes; F6 completo no se deduce solo del incremento A4 |
| A0g | Alcance general coherente, glosario/plan, tests | Candidato documental `e6d6279` listo; 387 + 238 subtests y docs aprobados tras reiteración del usuario. Revisión/integración pendientes |
| A12 | Polling durable, takeover explícito, reutilizar webhook; prueba propietario/bot real | Candidato `11bf9ba`: coordinador comprobó 52/52 área y suite 588 + 238 subtests (34.94 s), docs/diff verdes. Vault/factory/tick reales, revisión B e integración pendientes; no conectado al bot real |
| A6 | Router general tipado, registro, budgets, confirm/correct/cancel, callbacks; tests | Candidato `5640f3b`: implementador verificó 52 área/588 + 246 subtests. Coordinador Node229/229/23schemas y combinación A12+A6 640 + 246. Vault cifrado real, B/schema/prompt, webhook, ejecutores e integración pendientes |
| A8 | Fuentes web/social generales por capacidades, preflight, lectura/dedupe/evidencia | Candidato `6324b94` incluye 73 tests nuevos; prueba conjunta independiente 713 + 246. Lecturas inyectadas/cursores privados/CLI offline. Revisión, wiring, age/capacidades reales y dedupe entre corridas pendientes; no en main ni redes habilitadas |
| A7 | Descubrimiento/contacto/seguimiento genéricos, aprobaciones y comparación cuando aplica | En implementación aislada; depende A6/A8 y gate Go. Cotización es plantilla opcional; silencio no es precio cero |
| A9 | Investigación con citas/cobertura/costo y formato solicitado; PDF opcional | Candidato `ea36afe`: 46 pruebas propias, suite implementador 707 + 246, combinación independiente 819 + 246. Citas contrastadas con capturas, estados parciales y salidas privadas; extracción literal sin síntesis IA. Sourcing/proveedor/costo, reserva/vault/UI reales y PDF solicitado pendientes |
| A10 | Conversaciones habilitadas, destinatarios inequívocos, borradores/ledger, privacidad | No implementada; coordinación por canal, WhatsApp primero no implica único canal. Gate Go y cuentas reales siguen abiertos |
| A11 | Programación/zonas, replay, pausa/borrado, cuotas y autorización de efectos | Candidato `ce2a4c4`: coordinador 60/60 área y combinación 819 + 246. Local durable/DST/cuotas UTC/ocurrencias/outbox comprobados; `/tareas`, calendario confirmado/factory/ejecutor y trigger operativo pendientes. EventBridge no provisionado |
| A14 | Backend age durable compartido para los flujos privados | En preparación aislada, sin reemplazar por fakes ni cifrado Python. Pruebas criptográficas, límites de pipes, aislamiento owner/audience y permisos de host pendientes; no habilita cuentas por existir un helper |
| A5/F7 | DynamoDB/S3/SQS/SSM, conformidad local, outbox/reparación; experimento R1 | No implementada; depende A3. Pruebas con emuladores/moto no sustituyen AWS real |
| F8 | SAM build/local invoke, IAM/timeout/visibilidad/concurrencia/DLQ | Pendiente; infraestructura local verificable antes de autorización para desplegar |
| F9 | Threat model, stop/borrado/export, privacidad y revisión aplicable | Consentimiento parcial implementado. Borrado/logout/retención y revisión requerida para colaboradores no constatados |
| F10/F11/F12 | Presupuesto/free tier/canary, cuenta dedicada, piloto y primera fuente real | No autorizados ni probados por A. Periodo de recepción de 14 días no puede acreditarse con fixture ni una ejecución |
| F13/F14 | Evaluación IA/costos, feedback/reportes/runbooks y métricas reales | Parcial; módulos y documentos no prueban evaluación humana, operación desplegada o mejora en fuentes reales |

## Mejora de comprobaciones propuesta en esta rama

CI actual sólo prueba contratos Node y el lab, no `workers/browser` integrado.
Se añade un job Windows/Linux del worker (31 selftests), y en Linux dos tests
de limpieza de grupos propios; se incorpora `lab/sessions` a la matriz Go.
Usa acciones/Node/Go ya fijados, sin cuentas ni Chromium real. Ejecutar esta CI
en GitHub sigue pendiente de integración/push autorizados; su configuración
no se presenta como un check remoto aprobado.

Comprobación local de esta mejora: Python **387 tests + 238 subtests**,
worker Windows **31/31** sin skips, documentación y whitespace aprobados.
YAML parseado y matriz/condicional Linux/módulo de sesiones comprobados.
Ensayo Linux independiente de procesos: **2/2** (0.72 s), imagen A4 cacheada,
`--network none`, filesystem de sólo lectura, 256 MiB, 1 CPU y tmpfs de 64 MiB.
Guard de memoria: 10.221 GiB libres frente a 4.3125 GiB exigidos. Contenedor
propio eliminado; no se tocaron los cinco contenedores ajenos. Esta prueba
no acredita ejecución en un runner de GitHub ni funcionamiento con cookies.
El módulo `lab/sessions` añadido a CI pasa `go vet ./...` y
`go test -count=1 ./...` (0.717 s), con proxy/sumdb apagados y módulo readonly.

## Próximos gates, sin reducir el objetivo

1. Revisar A3 corregida/A0g y autorizar integración local aplicable; no sustituir
   la revisión B específica por la prueba independiente de A.
2. B corrige/revisa binding Go antes de `settled`, incluyendo la relectura tras
   conflicto; repetir los seis probes y suite habitual antes de cablear envíos.
3. Terminar/probar A12/A6 y luego A8/A9/A7/A10/A11/A5, preservando contratos y
   el alcance general. La preparación de ramas no equivale a integración.
4. Cuentas/fuentes/canary, gasto, contacto y takeover de bot sólo con encargo
   aplicable; registrar resultados observados y tiempos reales de piloto.

No cerrar el objetivo por haber completado solamente tests locales,
documentación o las tareas más pequeñas.
