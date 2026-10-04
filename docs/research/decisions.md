# Registro de decisiones de investigación

Fecha: 2026-10-03. Responsable exclusivo: coordinador A.
Este registro distingue límites de trabajo acordados de alternativas provisionales.

| ID | Estado | Decisión / propuesta | Motivo |
|---|---|---|---|
| R-001 | Acordado para esta investigación | Codex (A) y Claude (B) escriben informes separados; solo Codex integra índice y decisiones | Evitar sobrescrituras y conservar desacuerdos |
| R-002 | Regla de seguridad | Compartir documentación y referencias de sesión, nunca credenciales en Git | El repositorio es público; cookies pueden suplantar cuentas |
| R-003 | Alcance actual | Investigación sin contactos, login, instalaciones ni despliegues | El encargo actual pide investigación y reparto de trabajo |
| R-004 | Provisional | Agent Reach/OpenCLI para lectura estructurada; mensajería mediante adaptadores con capacidades verificadas | Reutilizar código sin atribuir DM a herramientas que solo leen/publican |
| R-005 | Revisada por B; alternativa fuera del MVP | Gateways persistentes solo si cambia la restricción Lambda-only reportada por Claude | No afirmar que toda operación intermitente necesita gateway; no hay recepción continua en Lambda |
| R-006 | Propuesta integrada; pendiente de pruebas | storageState cifrado para Chromium efímero; estado WhatsApp separado, dispositivo exclusivo por decidir | B-D004/B-D005; cookies, navegador y claves multidispositivo no son equivalentes |
| R-007 | Pendiente de pruebas | Seleccionar la primera red habilitada y pruebas end-to-end | Catálogo/login/documentación no equivalen a soporte operativo |
| R-008 | Alternativa; fuera del MVP Lambda-only | Beeper Desktop API/SDK y EasyMatrix como MessagingPort común si se permiten servicios persistentes | A-F17/A-F18 contrastados con B-D006r; no selección ni instalación |
| R-009 | Propuesta A; no habilitado | Añadir Bluesky y Telegram con auth nativa; Discord solo bot permitido | OAuth/MTProto no son cookies; permisos por operación; A-F22 a A-F24 |
| R-010 | Propuesta A | Estudiar deduplicación/fixtures de famabot, sin adoptar evaluador LLM obligatorio ni scraper comercial | Aporta patrones Marketplace, no contacto ni autorización; A-F19 |
| R-011 | Runtime Docker/RIE probado; cuentas/AWS pendientes | Chromium/Playwright + OpenCLI CDP local en Lambda | [Fixture y comparativo](../testing/LOCAL_RESULTS.md); B-D001r/B-F014 y A-F26; no E2E de una red real |
| R-012 | Experimental; decisión de dispositivo pendiente | whatsmeow conectar/sincronizar/operar/desconectar en Lambda | B-D002r/B-F015; nunca clonar sesión de bridge activo; recepción diferida y fallos por medir |
| R-013 | Corrección de diseño A | CAS S3 no es fencing de un envío; ledger durable y reconciliación de send_uncertain | A-F27 responde B-D009; no prometer exactly-once con FIFO/ETag |
| R-014 | Pendiente | Presupuesto total y respuesta ante rechazo de IP AWS | Free tier parcial no garantiza costo cero; B-Q006 no respondida aquí |
| R-015 | Laboratorio local probado; AWS pendiente | Fixtures → handler → RIE/Docker → benchmark limitado → canary AWS autorizado | [Resultados](../testing/LOCAL_RESULTS.md); no confundir emulador con AWS |
| R-016 | CLI y handoff probados; captura guiada pendiente | Export explícito, storageState cifrado con age, versiones y renew atómico | [CLI](../../lab/sessions/README.md); no cookies por chat ni perfiles writable |
| R-017 | Encargo actual autorizado | Implementar laboratorio Docker aislado con sesiones sintéticas, tareas separadas y ejecución secuencial | [Reparto y contratos](../testing/WORKPLAN.md); no habilita cloud/cuentas/mensajes |
| R-018 | Investigación B cerrada; validación pendiente | WhatsApp directo Go y Telegram Bot API para alta; código/QR humanos, chats habilitados y privacidad | B-D011r/B-D016–B-D023; no inferir permiso de vincular colaboradores ni pruebas reales |
| R-019 | Propuesta consolidada; no implementada | age único; DynamoDB autoritativo de control, S3 blobs y SSM secretos; cuatro Lambdas más CLI | Responde B-Q015; [revisión final](architecture-final-review.md); SQLite de protocolo/local no desaparece |
| R-020 | Corrección propuesta | Inbox/outbox transaccionales, resultados durables y reconciliación sin reenvío ciego | B-D056/R-013 no cierran por sí solos la doble escritura ni cercan al proveedor |
| R-021 | Optimización propuesta; medir en fuentes reales | HTTP/feed primero; Chromium/OpenCLI cuando la capacidad lo requiera; grupos públicos separados de cuentas | Laboratorio muestra overhead en fixture, no prueba superioridad universal ni cobertura social |
| R-022 | Alcance preservado | Mantener producto vigente y documentos B; cambios de AGENTS/arquitectura operativa al implementar alcance confirmado | El encargo actual pide verificación/propuesta, no activar infraestructura o ampliar producto automáticamente |
| R-023 | Observado en Git local, 2026-10-03 | `6ae7cf2` contiene la línea base de investigación/lab/plan y es ancestro de `main`; A0 parte de `2d39a19` | Comprobado con log/show/merge-base; no demuestra push ni CI remota |
| R-024 | Aprobación reportada por B; implementación A autorizada en este chat | Adoptar arquitectura final A con refinamientos R1–R4 como dirección de trabajo local | Informe B, sección "Plan de implementación y trabajo en paralelo", registra "Sí los 3"; no se afirma verificación independiente de ese chat |
| R-025 | Experimento aceptado, no aplicado | R1: probar consumo directo de comandos desde Streams para evitar `commands.fifo`; conservar baseline de cuatro colas hasta decidir con pruebas 0.5/1 | Medir orden por agregado, replay, fallos de shard y reparación; volver a FIFO si complica consistencia |
| R-026 | Control propuesto, pendiente de medición | R2: concurrencia reservada, cuotas de trabajos y reservas de presupuesto limitan consumo; no son techo duro universal ni garantizan USD 0 | Budgets es alerta tardía; billing/free tier y costo de almacenamiento/red/reintentos siguen pendientes |
| R-027 | Alcance local de implementación | R3: productos con nombres del núcleo general justificados por sus casos; otras verticales pendientes; R4: HTTP primero solo con permiso/capacidad verificados | No habilita todo sitio ni cambia reglas de SECURITY; Telegram única UI MVP reportada en B-D038 |
| R-028 | B-Q006 respondida según informe B; fallback no implementado | Si una fuente rechaza la sesión desde AWS, permitir respaldo local en el PC autorizado; sin proxies, evasión ni login oculto | El informe B registra la respuesta; cada fuente/cuenta real y canary todavía requiere encargo separado |
| R-029 | Encargo vigente | Iniciar tareas A en worktrees aislados y entregar commits locales para revisión | "Empieza tus codex tasks" autoriza implementación local; no nuevos merges, push, AWS o contactos |

## Adopción de B-D061–B-D064 — A0, 2026-10-03

| ID de B | Estado integrado por A | Límite |
|---|---|---|
| B-D061 | Adoptado como diseño de implementación local | Lease adquirido por worker, inbox/outbox, HTTP permitido primero, ledger por propietario/destinatario/propósito e informes JSON + Telegram; código todavía pendiente |
| B-D062 | Adoptado con precisiones R-025–R-027 | R1 se prueba, no se declara eliminación implementada; R2 no promete gratuidad ni techo universal; R3 no activa otras verticales; R4 conserva preflight |
| B-D063 | Método de worktrees/tablero y propiedad adoptado | La tarea A0 no integra ramas ni cambia archivos reclamados por B |
| B-D064 | Línea base observada localmente (R-023); autorización registrada por B | No se infiere permiso para otro merge ni que se haya publicado |

Fuente conservada: [informe B](agent-b-sessions.md), sección "Plan de
implementación y trabajo en paralelo" y tabla B-D061–B-D064;
[plan](agent-b/implementation-plan.md), §1–§4. La dirección de implementación
se refleja ahora en AGENTS/ARCHITECTURE/ROADMAP; estos documentos no prueban
componentes desplegados. El contexto previo R-003/R-022 conserva su fecha y
alcance histórico, no bloquea las nuevas tareas locales autorizadas.

## Cierre A — 2026-10-03

Codex completó su investigación documental: 27 hallazgos y snapshot de 14
componentes. Mensajería real, sesiones portables y despliegue no están probados.
Informe Claude leído e integrado; no se alteró su archivo. Se preserva el diseño
inicial como alternativa y se incorpora la restricción Lambda-only que B registra
del usuario. R-011/R-012 son candidatos, no implementaciones verificadas. La
discrepancia sobre fencing del efecto externo se documenta en R-013 para revisión
de Claude. Autoría Threads reportada por B; código no importado ni redistribuido.

## Cómo integrar propuestas de Claude (B)

Conservar el ID B-Dxxx y la fuente original. Marcar aceptación, rechazo o revisión
pendiente con motivo y fecha. No convertir una inferencia en validación al resumir.
Si ambos informes discrepan, registrar el desacuerdo y la prueba que lo resolvería.

## Fuentes iniciales

- [Playwright: autenticación](https://playwright.dev/docs/auth): archivos de estado
  sensibles; no versionarlos; documenta diferencias entre almacenes del navegador.
- [OpenCLI](https://github.com/jackwener/OpenCLI): perfiles de navegador, bridge y
  comandos por plataforma; requiere verificar cada capacidad.
- [AWS Lambda: cuotas](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html):
  timeout de Lambda convencional; no garantiza continuidad de sesiones.

Consulta: 2026-10-03. Selección tecnológica definitiva pendiente de ambos informes.

Actualización posterior: ambos informes están cerrados; sigue pendiente selección
de conectores reales. El laboratorio demuestra sólo fixtures/runtime local.

## Cierre de coordinación B — 2026-10-03

Entrega final de Claude leída íntegramente: 27 hallazgos, piloto y onboarding
revisados. Investigación coordinada completa; pruebas de factibilidad pendientes.
Se asignaron T1–T4 en el [workplan](../testing/WORKPLAN.md). Las respuestas nuevas
B-Q009/B-Q012/B-Q013 quedan registradas allí. No se editó el informe de Claude.
R-003 describe el encargo de investigación anterior; R-017 autoriza únicamente
la nueva implementación/prueba local, sin ampliar a cloud ni contactos reales.
