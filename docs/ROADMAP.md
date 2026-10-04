# Roadmap y criterios de salida

Actualizado: 2026-10-04, preparación A0g/A3. Implementación local por fases, sin fechas ni
ingresos prometidos. Fuente de tareas/dueños/dependencias:
[BOARD](work/BOARD.md), [CODEX_TASKS](work/CODEX_TASKS.md) y
[plan detallado B](research/agent-b/implementation-plan.md).
Dirección y límites vigentes en [ARCHITECTURE](ARCHITECTURE.md),
[decisiones](research/decisions.md) y [SECURITY](../SECURITY.md).

## Estado observado y autoridad

Alcance general confirmado por el usuario el 2026-10-03. La nueva dirección es
buscar información y contactos, navegar fuentes y contactar por distintos canales
sobre cualquier tema; no solo productos o talleres. [GENERAL_TASKS](GENERAL_TASKS.md)
actualiza la dirección de F15/Ola 2 sin declarar operaciones implementadas.
Conservar entregas y contratos existentes; proponer versiones nuevas con B.

`6ae7cf2` es ancestro de `main`: la línea base de investigación, documentación
y laboratorio está integrada localmente. No demuestra CI remota ni publicación.
El usuario autorizó integrar A1 (`52b6014`), A2 (`9f74031`) y A0b (`a30c05f`)
en main local, sin push, y continuar A3. A3 entrega su rama local para revisión;
la integración de A3 en main exige la nueva revisión B pendiente. Esta preparación
A0g/A3 no la sustituye ni autoriza push, cuenta real o cloud. Las respuestas de arquitectura
R1–R4 y B-Q006 están reportadas por Claude; ver registro de decisiones.

Lab habilitador: navegador Lambda/RIE con Playwright/OpenCLI, sesiones age,
versiones/CAS, upload/renew explícito y runner de recursos.
[Resultados sintéticos y límites](testing/LOCAL_RESULTS.md). No son soporte
social ni fase comercial terminada; la configuración de producto sigue documental.

Primer corte A3 observado: SQLite en disco con receipt/command/outbox atómicos,
replay durable, queue/worker/UI falsos, cálculo A2 derivado de evidencia, snapshot
de búsqueda, presupuesto/cursor, resultados y alert intents locales. Casos
de búsqueda/resultados y aprobación/reconciliación simulada probados en
`tests/flow`, incluidos roles, consentimiento, leases, crashes y proof independiente
del diario falso. La recepción UoW es parcial, no
conformidad completa A1; wire actions requieren age y proveedor verificado.
No se conecta el worker Go ni AWS. La rama A3 incorpora la compatibilidad con
consentimiento y alta de contacto B2b (`070ce46`), con directorio/allowlist
sintéticos; revisión e integración siguen pendientes.
La compatibilidad B7 acepta sobres v1/v2, conserva su versión en descendientes
y mantiene payloads browser/Telegram v1; no añade handlers WhatsApp.
No se elimina `commands.fifo`: R1 aún no medido. `/stop` solo cancela estado local durable,
sin Logout real; otros comandos se aparcan durablemente como `unsupported`.
Corrección de revisión B en esta rama: revocación no reversible por reconsentir,
cuarentena owner/message para rechazos terminales conocidos antes del ACK,
cuota por propietario, timestamps UTC fijos y cancel terminal sin retroceso.
La conexión local serializa lecturas/escrituras y transacciones; la prueba de
reader/rollback no observa cambios provisionales. Conflictos desconocidos,
crashes y fallos de persistencia siguen pendientes sin ACK. Estas pruebas no
demuestran DLQ AWS, privacidad productiva ni fencing del proveedor real.

Preparación A0g: concilia README/ROADMAP con el candidato A3 sin eliminar sus
evidencias y límites ni restaurar la antigua restricción a productos. Los
candidatos A6/A8/A9/A7/A10/A11/A14/A16/A18/A19 ya tienen pruebas offline; no son
integración en main ni acceso a cuentas. Investigación contextual es componible
en cualquier etapa. Lectura real de inbox, selección natural «esto/aquello»,
clasificación de pendientes y filtro temporal «hoy» siguen sin acreditarse.

## Fases F0–F15

El orden no es estrictamente numérico: F2/F4/F5 pueden avanzar cuando sus
contratos/puertos estén disponibles; F3 integra sus entregas. Estados concretos
de ramas en BOARD, no inferidos de esta tabla. **[U]** exige encargo/decisión
separada antes de ejecutar el gate real.

| Fase | Entrega y dueño | Criterio de salida / gate |
|---|---|---|
| F0 | Gobierno, baseline y CI (A) | Documentos coherentes, tablero, pytest/check_docs; integración/push solo autorizado |
| F1 | Contratos v1 (B) y puertos (A) | Mismos ejemplos válidos/negativos en Python/Node/Go; capas sin SDK/red; revisión de contrato |
| F2 | Dominio productos (A) | Decimal/tasas fechadas, estados/matching con razones y propiedades; desconocido ≠ 0 |
| F3 | Flujo local completo (A integra B) | Telegram/worker falsos, SQLite receipt+command+outbox, replay/fallos; sin perder tareas ni reenviar incertidumbre |
| F4 | Adaptador/webhook Telegram (B) | Secreto/roles, receipt idempotente, callbacks y 429 con updates sintéticos; única UI de producto |
| F5 | Módulo Go y WhatsApp falso (B) | Vault age compartido, pair/sync/send por contrato, WAL/snapshot y clientes falsos; sin cuenta real |
| F6 | Worker navegador (A) | Contratos, fixtures y helper age; HTTP permitido antes de Chromium; benchmark aislado |
| F7 | Adaptadores AWS (A, revisión B) | Suites de conformidad local/moto/DynamoDB Local; outbox reparable y experimento R1, no provisionamiento |
| F8 | SAM y controles de recursos (A, revisión B) | Build/local invoke sintéticos, IAM mínimo, DLQ/logs/visibilidad; R2 limita consumo sin prometer USD 0 |
| F9 | Seguridad/privacidad (B, revisión A) | Amenazas, consentimiento y borrado/stop; **[U]** revisión aplicable antes de colaboradores/cuentas reales |
| F10 | Pre-despliegue y canary | **[U]** Billing/free tier, Budgets, región/presupuesto y canary bot de prueba; costo real/IAM medidos |
| F11 | Piloto WhatsApp | **[U]** número dedicado, vinculación/JID; recepción/reconexión medidas; envío y colaboradores con autorización separada |
| F12 | Primera fuente real/evaluación (A) | **[U]** fuente/cuenta autorizadas; lectura inocua, dataset revisado, cobertura y límites visibles |
| F13 | IA opcional (B) | **[U]** credenciales/presupuesto si aplica; flag apagado, caché y evaluación; mejora sin regresiones ni fallback pagado implícito |
| F14 | Feedback, informes y operación (A/B) | JSON + Telegram, cobertura/salud, runbooks para DLQ/send_uncertain/needs_reauth y actualización de dependencias |
| F15 | Núcleo general y módulos temáticos | Alcance general confirmado; router/fixtures multitema en candidatos locales, revisión/integración pendientes; nuevas fuentes/cuentas/efectos siguen con autorización específica |

R1 compara comandos directo desde Streams frente a `commands.fifo`; el baseline
conserva cuatro colas hasta pruebas 0.5/1. R3 y su limitación a productos
describían el alcance histórico; el encargo actual lo amplía al núcleo
general de pedidos. R4 sigue exigiendo permisos/capacidades antes
de toda ruta HTTP. B-Q006 permite respaldo local sin proxies/evasión según el
informe B; no habilita fuente/cuenta ni instala el fallback automáticamente.

## Definición de hecho por tarea

- Archivos reclamados y dependencias respetados; una rama/worktree por tarea.
- Pruebas reales del área + `check_docs`; resultados/fallos y pendientes declarados.
- Sin datos privados, servicios/cuentas/contactos activos ni flags habilitados por inercia.
- Revisión del otro frente/coordinador antes de integrar; merge serial y push
  solo bajo autorización aplicable. No afirmar fase completa por un documento.

La visión vigente es investigación y contacto general; productos/reventa es un
módulo opcional. No habilita compraventa automática. Telegram es UI única MVP;
dashboard/Mini App y vectores quedan posteriores, no dependencias instaladas.
Rentabilidad requiere resultados comerciales consentidos, no porcentajes de fixtures.
