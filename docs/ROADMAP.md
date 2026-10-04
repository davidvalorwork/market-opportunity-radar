# Roadmap y criterios de salida

Actualizado: 2026-10-03, primer incremento A3. Implementación local por fases, sin fechas ni
ingresos prometidos. Fuente de tareas/dueños/dependencias:
[BOARD](work/BOARD.md), [CODEX_TASKS](work/CODEX_TASKS.md) y
[plan detallado B](research/agent-b/implementation-plan.md).
Dirección y límites vigentes en [ARCHITECTURE](ARCHITECTURE.md),
[decisiones](research/decisions.md) y [SECURITY](../SECURITY.md).

## Estado observado y autoridad

`6ae7cf2` es ancestro de `main`: la línea base de investigación, documentación
y laboratorio está integrada localmente. No demuestra CI remota ni publicación.
El usuario autorizó integrar A1 (`52b6014`), A2 (`9f74031`) y A0b (`a30c05f`)
en main local, sin push, y continuar A3. A3 entrega su rama local para revisión;
esa autorización no implica integrar A3, push, cuenta real o cloud. Las respuestas de arquitectura
R1–R4 y B-Q006 están reportadas por Claude; ver registro de decisiones.

Lab habilitador: navegador Lambda/RIE con Playwright/OpenCLI, sesiones age,
versiones/CAS, upload/renew explícito y runner de recursos.
[Resultados sintéticos y límites](testing/LOCAL_RESULTS.md). No son soporte
social ni fase comercial terminada; la configuración de producto sigue documental.

Primer corte A3 observado: SQLite en disco con receipt/command/outbox atómicos,
replay durable, queue/worker/UI falsos, cálculo A2 derivado de evidencia, snapshot
de búsqueda, presupuesto/cursor, resultados y alert intents locales. Casos
de búsqueda/resultados probados en `tests/flow`; acciones/aprobación/reconciliación
simuladas aún pendientes de sus pruebas. La recepción UoW es parcial, no
conformidad completa A1; wire actions requieren age y proveedor verificado.
No se conecta el worker Go ni AWS. Consentimiento B y revisión de main vigente
son gates de integración. No se elimina `commands.fifo`: R1 aún no medido.

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
| F15 | Segunda vertical | **[U]** alcance formalmente confirmado después de F12; fixtures y casos reales antes de generalizar dominio |

R1 comparará comandos directo desde Streams frente a `commands.fifo`; el baseline
conserva cuatro colas hasta pruebas 0.5/1. R3 usa nombres generales justificados
en productos, no implementa otras verticales. R4 exige permisos/capacidades antes
de toda ruta HTTP. B-Q006 permite respaldo local sin proxies/evasión según el
informe B; no habilita fuente/cuenta ni instala el fallback automáticamente.

## Definición de hecho por tarea

- Archivos reclamados y dependencias respetados; una rama/worktree por tarea.
- Pruebas reales del área + `check_docs`; resultados/fallos y pendientes declarados.
- Sin datos privados, servicios/cuentas/contactos activos ni flags habilitados por inercia.
- Revisión del otro frente/coordinador antes de integrar; merge serial y push
  solo bajo autorización aplicable. No afirmar fase completa por un documento.

La visión sigue siendo productos/reventa con estimaciones, no campañas ni
compraventa automática. Telegram es UI única MVP; dashboard/Mini App,
vectores y otras verticales son posteriores, no dependencias instaladas.
Rentabilidad requiere resultados comerciales consentidos, no porcentajes de fixtures.
