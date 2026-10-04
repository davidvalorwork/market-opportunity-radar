# Auditoría de cierre del tablero

Fecha: 2026-10-04. Base principal observada `09c1509`; tablero compartido de main
es autoridad de reclamos, no el snapshot de esta rama. Objetivo íntegro:
**terminar las tareas y comprobaciones del board**. No se declara conseguido.

Fuentes: [BOARD](BOARD.md), [CODEX_TASKS](CODEX_TASKS.md),
[plan F0–F15](../research/agent-b/implementation-plan.md),
[arquitectura](../ARCHITECTURE.md), [evaluaciones](../EVALUATIONS.md).
El usuario confirmó alcance general: la dirección A0g `dc0eb8f` (incluye `e6d6279/d38416b`) sustituye
restricciones temáticas anteriores. Productos/talleres son ejemplos opcionales.
Chats, respuestas e investigación contextual en cualquier etapa son requisitos,
no capacidades ya operativas por haberlos documentado.
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
- Ese árbol fue empaquetado con backend fijado y `uv build --offline`, e instalado
  en un target privado fuera del checkout, sin dependencias nuevas ni red.
  Imports comprobados desde `installed/radar`, **23 schemas** empaquetados;
  `pytest -o pythonpath=` contra el wheel instalado: **819 + 246 subtests**
  (51.92 s), cero skips/fallos. Incluye APIs generales nuevas, no binarios Go,
  transportes reales ni infraestructura desplegada.
- Inspección A3: `approve_action`, `record_result` y `transition` de wire
  siguen explícitamente sin implementar. Sus equivalentes locales prueban
  intenciones y estado; no sustituyen age, boundary del proveedor, outbox de
  efectos y conformidad del puerto productivo. Este faltante es parte de F3/F7,
  no un permiso para usar el runner Go de memoria como adaptador durable.
- `whatsapp.messages.private.v1` actual exige chat/text/fecha pero no ID estable
  por mensaje. `whatsapp.result.v2` usa ese mismo payload de sync; su campo
  provider ID es para resultados de envío. La petición previa A→B de un ID no
  equivale a que se haya implementado. Sin journal privado verificable o nueva
  versión revisada no se puede demostrar dedupe correcto de respuestas reales.
- Prueba de runtime Windows real A11: `ZoneInfo('America/Caracas')` falla por
  ausencia de `tzdata` en el venv bloqueado. Las fixtures de zonas no cubren ese
  gate. Corrección A15 `4c5e4be`: `tzdata==2026.4` runtime/lock, instalado offline
  en un venv privado nuevo. Cinco regresiones rojas antes de corregir; luego
  **6/6** IANA y **706 tests + 246 subtests** (51.48 s) aprobados. Caracas,
  gap/overlap DST NY, Londres y zona inválida reales, no offset fijo. El venv
  anterior y main no fueron modificados; revisión/integración siguen pendientes.
- A10 `463f815`: coordinador **44/44** (8.78 s), docs/diff verdes. Proyección
  privada de envío v1/envelope v2 y hash ledger SHA256 del texto UTF-8 real,
  distinto del manifest/pantalla de aprobación. Prueba offline, no Go real.
- A7 `3be4e4f`: coordinador **61/61** (13.57 s), docs/diff verdes. Flujo
  multitema A6/A8/A10 local, resolver separado, aprobación exacta y seguimiento
  opt-in único ligado a cuenta/destinatario original. No lectura/síntesis web
  contextual, resolución de personas o efectos reales demostrados.
- A14 `edfd63a`: coordinador **45/45 Windows** (15.93 s), docs/diff verdes,
  age real, ACL WinAPI y pruebas negativas, restart/bindings/cuotas. No cuentas.
  Ensayo POSIX independiente: **45/45 Linux** (51.68 s), cero skips/fallos,
  Docker imagen cacheada `sha256:3d7c44b202068ea080dd6f0ffacbd826e94d175317e9824be085d94b38fa2176`,
  512 MiB/1 CPU, read-only/cap-drop/pids128/tmpfs128, sin red exterior.
  Helpers Linux cross-compilados offline desde el SHA congelado con Go1.27.1:
  vault SHA256 `92a06dd78d608cb30024f02012432d77f660968335856e9be396c68f958331b6`.
  En snapshot privado sólo se reemplazó la fixture de build por binarios montados
  read-only/hash comprobado; fuente y cuerpos de tests idénticos. No acredita
  instalación Go en Docker/CI ni Lambda. Guard: 10.670 GiB libres frente a
  4.625 exigidos; contenedor propio autoeliminado, cinco ajenos sin cambios.
- Combinación A12/A6/A8/A9/A10/A11/A7/A14/A15, sin conflictos:
  árbol `4968c19848d855027a070aa45b0d5cf24677400e`, snapshot privado
  `../mor-a-governance/.local/general-vault-contact-20261004/source`.
  Coordinador: **975 tests + 246 subtests** (90.07 s), cero skips/fallos y
  docs verdes en venv del lock A15. Es convivencia del código fuente y sus
  interoperaciones ensayadas, no revisión B, main integrado, factory/bot real,
  wheel de este árbol ni end-to-end operativo de todas las redes.

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
| A0g | Alcance general coherente, glosario/plan, tests | Candidato documental `dc0eb8f` listo; 387 + 238 subtests y docs aprobados. Incluye investigación contextual y chats/respuestas cualquier etapa. Revisión/integración pendientes |
| A12 | Polling durable, takeover explícito, reutilizar webhook; prueba propietario/bot real | Candidato `11bf9ba`: coordinador comprobó 52/52 área y suite 588 + 238 subtests (34.94 s), docs/diff verdes. Vault/factory/tick reales, revisión B e integración pendientes; no conectado al bot real |
| A6 | Router general tipado, registro, budgets, confirm/correct/cancel, callbacks; tests | Candidato `5640f3b`: implementador verificó 52 área/588 + 246 subtests. Coordinador Node229/229/23schemas y combinación A12+A6 640 + 246. Vault cifrado real, B/schema/prompt, webhook, ejecutores e integración pendientes |
| A8 | Fuentes web/social generales por capacidades, preflight, lectura/dedupe/evidencia | Candidato `6324b94` incluye 73 tests nuevos; prueba conjunta independiente 713 + 246. Lecturas inyectadas/cursores privados/CLI offline. Revisión, wiring, age/capacidades reales y dedupe entre corridas pendientes; no en main ni redes habilitadas |
| A7 | Descubrimiento/contacto/seguimiento genéricos, aprobaciones y comparación cuando aplica | Candidato `3be4e4f`; coordinador 61/61 y combinación975 + 246. Preparación/proof separado/aprobación/seguimiento ensayados; síntesis contextual/UI/resolver/proveedor reales y revisión pendientes. Cotización opcional; silencio no es precio cero |
| A9 | Investigación con citas/cobertura/costo y formato solicitado; PDF opcional | Candidato `ea36afe`: 46 pruebas propias, suite implementador 707 + 246, combinación independiente 819 + 246. Citas contrastadas con capturas, estados parciales y salidas privadas; extracción literal sin síntesis IA. Sourcing/proveedor/costo, reserva/vault/UI reales y PDF solicitado pendientes |
| A10 | Conversaciones habilitadas, destinatarios inequívocos, borradores/ledger, privacidad | Candidato `463f815`; coordinador44/44 y combinación975 + 246. Canal general/borradores/exactscreen/proyección offline; sync con IDs estables, Go gate, delivery/UI/proveedor reales y revisión pendientes |
| A11 | Programación/zonas, replay, pausa/borrado, cuotas y autorización de efectos | Candidato `ce2a4c4`: coordinador 60/60 área y combinación 819 + 246. Local durable/DST/cuotas UTC/ocurrencias/outbox comprobados; `/tareas`, calendario confirmado/factory/ejecutor y trigger operativo pendientes. EventBridge no provisionado |
| A14 | Backend age durable compartido para los flujos privados | Candidato `edfd63a`; coordinador45/45 Windows y45/45 Linux, combinación975 + 246. age/ACL/bindings/cuotas y A6/A8/A9/A12 ensayados. Revisión, factory real, worker remoto/audiencias con claves distintas y CI remota pendientes; no habilita cuentas |
| A15 | Calendario IANA disponible en runtime/lock Windows | Candidato `4c5e4be` cierra fallo de dependencia en venv nuevo; 6/6 calendario real y combinación975 + 246. Main/integración/revisión pendientes |
| A16 | Telegram → pasos generales durables → investigación/contacto/conversaciones/scheduler | Implementación aislada en curso; no hay suite final acreditada. Privacidad ingreso, claims/replay, delivery exacta y composición contextual deben probarse. No sustituirlo por CLI fixture sola ni habilitar bot real |
| A17 | HTTP público real conforme a A8 | Iniciado aislado; transporte real pin/TLS/peer/stream/deadline y pruebas socket/TLS pendientes. No guarda cookies ni demuestra búsqueda social/CLI/browser por implementar HTTPS |
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

Nueva corrección necesaria tras A14: el job pytest prepara Go1.27.1 sólo si
existe `helpers/private-vault/go.mod`, descarga/verifica módulos bloqueados en
un paso explícito y después corre Go helper y pytest con proxy/sumdb apagados.
Sin ese setup, un runner Python limpio no tiene garantizado Go o caché age.
YAML y condiciones/env fueron comprobados localmente; no se afirma ejecución
remota ni se omiten las pruebas criptográficas para conseguir verde.

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
