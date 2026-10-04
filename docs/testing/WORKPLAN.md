# Coordinación cerrada: investigación → laboratorio Docker

Fecha: 2026-10-03. Encargo actual: implementar y medir pruebas locales.
Investigación de Claude leída íntegramente (B-F001–B-F027, B-D001–B-D023 y
revisiones). No se modifica su informe. La investigación documental se cierra;
las preguntas de validación pasan a experimentos con resultados registrados.

## Decisiones integradas

- Navegador: Playwright/Sparticuz con storageState y OpenCLI CDP local, sin
  extensión. Comparar lectura directa vs CLI sobre el mismo fixture.
- WhatsApp: candidato whatsmeow directo en Go, no WAHA/GOWA/Matrix permanente
  para Lambda. Sus benchmarks reales necesitan cuenta/dispositivo de ensayo;
  el laboratorio sin credenciales no demuestra conexión/sync/entrega.
- Telegram Bot API: alta y notificaciones, no discovery global de ofertas. Su
  webhook y código de vinculación son un módulo futuro; no crear bot ni vincular
  colaboradores durante estas mediciones. protect_content no impide capturas de
  pantalla ni reemplaza controles de acceso/consentimiento.
- Sesiones web: bundle age, registro de versión/lease, importación y renovación
  desde archivos entregados explícitamente. Sin lectura de Chrome personal.
- S3 CAS/FIFO: protección de escritura, no fencing de efectos externos. Mantener
  send_uncertain y reconciliación en el diseño; fixtures no certifican AWS.
- No cloud, NAT, proxies, modelos, API pagada o mensajes reales en este encargo.

## Reparto de trabajo autorizado

| Frente | Responsable | Propiedad exclusiva | Entrega verificable |
|---|---|---|---|
| T1 navegador | Subagente Codex market_browser_lab | lab/browser/ | Handler Lambda/RIE; fixture local; Playwright y OpenCLI; export/import de estado |
| T2 sesiones | Subagente Codex market_session_lab | lab/sessions/ | Binario Go/age; bundles, destinatarios, renovación/CAS; pruebas de seguridad sintéticas |
| T3 medición | Subagente Codex market_benchmark_runner | lab/runner.py, lab/__init__.py, tests/test_lab_runner.py | Runner CLI, límites, métricas cgroup, fallos y cleanup |
| T4 integración | Codex coordinador | Docs, README, .dockerignore, controles comunes | Ejecutar Docker secuencialmente, revisar evidencia, informe de resultados |
| Investigación B | Claude, entrega completa | docs/research/agent-b-sessions.md | Se conserva como fuente; no se asume trabajo nuevo de Claude |

Sin commits/merges/push durante checkout compartido. Dependencias se descargan
solo para imágenes de laboratorio y se fijan; no instalar herramientas globales.
Solo el coordinador ejecuta builds/benchmarks para evitar competencia de RAM.
No tocar otros contenedores, imágenes, volúmenes o proyectos del usuario.

## Contratos de integración

Browser event JSON: schema_version=1, suite=browser, fixture_only=true obligatorio,
mode=direct|opencli, batch 1–10, repeats 1–5, session_mode=full|cookies_only|none.
No aceptar URLs arbitrarias, secrets o acciones write desde eventos.

Salida JSON: schema_version=1, suite, status=passed|failed, checks, timings,
useful_records y versiones. Métricas host/cgroup independientes de la salida del
handler. Unknown/unsupported no se convierten en passed para cerrar el informe.

Browser: imagen AWS Node/RIE, invocación interna desde docker exec para poder
usar network none sin publicar CDP/HTTP. Sessions: CLI sin servidor/red, selftest
sintético por defecto. Todos los datos generados en /tmp o .local, nunca Git.

## Orden de pruebas

1. Unit/contratos/documentación y sintaxis; verificar lockfiles/dependencias.
2. Build images y registrar imagen, digest, versión, tamaño y tiempo de build.
3. Smoke a 2.048 MiB navegador y 128 MiB sesiones, un contenedor cada vez.
4. Medir RAM/CPU/tiempo frío local; comparar direct/OpenCLI y 1.024/1.600/2.048
   MiB según presupuesto disponible. No extrapolar Docker CPU a Lambda.
5. Repetir invocaciones warm; deadline, OOM y cierre. Resultados negativos se
   preservan; no bajar checks para etiquetar opción como viable.
6. Probar bundle alterado/clave incorrecta/lease ocupado/CAS obsoleto/origen
   incorrecto. Simular estado vencido y renovación; no sesiones reales.
7. Publicar informe trazable y comando reproducible, límites y próximos canaries.

Cada benchmark tiene presupuesto finito. No lanzar una matriz exhaustiva si ya
hay un fallo funcional o falta RAM. Registro detallado privado en .local/reports;
resumen público solo datos sintéticos agregados. El runner no llama a un LLM.

## Costos y factibilidad

Medir trabajo útil correcto, no cantidad de invocaciones. Registrar GB-s
aproximados a partir de RAM configurada y tiempo; tarifa opcional y fechada,
almacenamiento/red/SSM/KMS desconocidos permanecen null. Coste de electricidad y
hardware local no se infiere de la RAM. El free tier no garantiza factura cero.

Referencia consultada: [AWS Lambda pricing](https://aws.amazon.com/lambda/pricing/)
publica para el primer tramo x86 un ejemplo de USD 0,0000166667/GB-s y solicitudes
por separado. No es tarifa universal de región/arquitectura ni medición AWS.

Respuesta a B-Q009: [Meta pricing](https://developers.facebook.com/docs/whatsapp/pricing/)
requiere categoría/mercado/fecha/tier para elegir rate card; la página consultada
incluye cambios de servicio de octubre 2026. No reutilizar «todo servicio gratis»
ni inventar precio de Venezuela. Comparación por plantilla pendiente de escoger
categoría y rate card aplicable; en estos tests no se paga ni envía un mensaje.

Respuesta a B-Q012: rol Bot API de Telegram incorporado al plan como onboarding,
distinto de MTProto como posible fuente futura. B-Q013: añadir a los canaries
accesibilidad del colaborador por proveedor/país y accesibilidad del worker por
separado. Reportes históricos de bloqueo no prueban estado actual por ISP; no
declarar acceso universal porque Lambda pudo leer una URL.

## Criterio de cierre del laboratorio

Código ejecutable, pruebas con salida real, resultados positivos y negativos,
comando reproducible y clasificación: probado local / pendiente AWS / pendiente
cuenta real. Ningún resultado sintético habilita DM, colaboración multiusuario,
sincronización WhatsApp o cloud por sí solo. Ese alcance requiere autorización y
validación posterior, incluyendo los requisitos de privacidad señalados por B.

## Entrega integrada y mediciones

T1–T4 implementados localmente. [Resultados, incidencias y comandos](LOCAL_RESULTS.md):
Go/age 6/6 mediciones iniciales; handoff entre contenedores 14 checks; wrapper
upload/renew con archivos sintéticos; direct/OpenCLI 14/14 invocaciones en la
matriz con reserva default y cuatro adicionales en prueba de estrés explícita;
tres casos negativos. Perfiles sin RAM permanecen skipped_resource.

Se conservó la reserva default de 4 GiB +25%; se añadió override manual 3–16 GiB
registrado en cada corrida. Ningún cambio automático para fabricar un passed.
El smoke previsto de 2.048 MiB no se admitió por RAM; el comparativo efectivo usó
512/1.024 MiB. 1.600 MiB y AWS siguen pendientes. No iniciar canaries ni login a
cuentas por el hecho de cerrar el laboratorio sintético.
