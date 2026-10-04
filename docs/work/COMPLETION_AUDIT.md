# Auditoría de cierre del tablero

Fecha: 2026-10-04. Base principal observada `09c1509`; tablero compartido de main
es autoridad de reclamos, no el snapshot de esta rama. Objetivo íntegro:
**terminar las tareas y comprobaciones del board**. No se declara conseguido.

Fuentes: [BOARD](BOARD.md), [CODEX_TASKS](CODEX_TASKS.md),
[plan F0–F15](../research/agent-b/implementation-plan.md),
[arquitectura](../ARCHITECTURE.md), [evaluaciones](../EVALUATIONS.md).
El usuario confirmó alcance general: la dirección A0g `d38416b` sustituye
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
| A0g | Alcance general coherente, glosario/plan, tests | Candidato documental `d38416b` listo; revisión/integración pendientes |
| A12 | Polling durable, takeover explícito, reutilizar webhook; prueba propietario/bot real | Preparación aislada sobre A3 corregida. Artefacto/tests y prueba real todavía deben verificarse; no considerar factory sin cablear como éxito |
| A6 | Router general tipado, registro, budgets, confirm/correct/cancel, callbacks; tests | Preparación aislada; entrega, revisión schema/prompt por B y cableado webhook todavía pendientes |
| A8 | Fuentes web/social generales por capacidades, preflight, lectura/dedupe/evidencia | No implementada en main. Cashea opcional; no limitar a talleres. Cada adaptador requiere pruebas/capacidades, no una lista de redes |
| A7 | Descubrimiento/contacto/seguimiento genéricos, aprobaciones y comparación cuando aplica | No implementada; depende A6/A8 y gate Go. Cotización es plantilla opcional; silencio no es precio cero |
| A9 | Investigación con citas/cobertura/costo y formato solicitado; PDF opcional | No implementada; requiere búsqueda autorizada, decisión de proveedor/costo y pruebas de citas/fallos; PDF sólo si solicitado |
| A10 | Conversaciones habilitadas, destinatarios inequívocos, borradores/ledger, privacidad | No implementada; coordinación por canal, WhatsApp primero no implica único canal. Gate Go y cuentas reales siguen abiertos |
| A11 | Programación/zonas, replay, pausa/borrado, cuotas y autorización de efectos | No implementada; zona configurable. Local y EventBridge requieren pruebas separadas; no provisionar por inferencia |
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
