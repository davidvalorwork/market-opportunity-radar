# Plan: probar Lambda localmente y optimizar recursos

Fecha: 2026-10-03. Estado: **plan de referencia; laboratorio local implementado**.
El encargo posterior se ejecuta en [lab/browser](../lab/browser/README.md),
[lab/sessions](../lab/sessions/README.md) y `python -m lab.runner`.
[Reparto y contratos efectivos](testing/WORKPLAN.md). Se construyen y prueban
imágenes locales con fixtures; no se capturan sesiones reales ni se despliega AWS.
Los comandos hipotéticos del plan no sustituyen las interfaces reales de esos README.

Objetivo: probar los mismos handlers que irán a Lambda, sin gastar tokens por
operación y sin cuentas reales inicialmente. Optimizar trabajo útil por recurso,
no solo bajar la RAM asignada. Complemento: [gestión de sesiones](SESSION_MANAGEMENT.md).
Base: [investigación integrada](research/decisions.md), A-F26/A-F27 y B-F014/B-F015.

## 1. Entorno y elección tecnológica

Inspección local de solo lectura: 31,8 GiB RAM total, 20 procesadores lógicos y
7,3 GiB libres en ese instante. docker, sam, node, go y wsl están en PATH; age no.
Eso no confirma Docker Engine activo, WSL configurado ni versiones compatibles.
No modificar configuración de Docker/WSL o instalar paquetes automáticamente.

Propuesta mínima:

| Componente | Elección | Motivo / límite |
|---|---|---|
| Handler de navegador | TypeScript/Node + playwright-core + Chromium Sparticuz + OpenCLI CDP | Evitar Python→Node→MCP por operación; OpenCLI requiere prueba de compatibilidad |
| Handler WhatsApp | Go + whatsmeow | No cargar Chromium para mensajería; dispositivo exclusivo por decidir |
| Dominio y normalización | Contratos JSON versionados, pipeline Python previsto | Dominio independiente del runtime; no reescribir por elegir un navegador |
| Integración local | AWS SAM CLI + Docker Linux | Mismo handler/evento; no es AWS completo |
| Perfilado de recursos | Imagen Lambda + Runtime Interface Emulator (RIE), Docker/cgroup | Límites explícitos de RAM/CPU y procesos hijos incluidos |
| Estado de ensayo | Puertos fake/locales, fixtures sintéticos | Sin S3/SQS real, cuentas ni servicios pesados obligatorios |
| Sesiones portables | storageState + age + referencias/lease | Solo después de pruebas locales de export/import |

Fijar versiones exactas, lockfiles, digest de imagen, arquitectura y hash del
binario Chromium. No usar latest en resultados de benchmark. Sparticuz no sigue
semver convencional; su README recomienda 1.600 MB o más, aunque menciona mínimo
512 MB. No elegir 512 MB como producción antes de medir [S4].

Empezar linux/amd64 nativo en este PC. ARM64 será experimento separado: las
imágenes y packs deben coincidir; emulación en x64 no mide rendimiento nativo.
Sparticuz documenta packs/layers arm64 y diferencias de paquete [S4].

## 2. Flujo local y niveles de fidelidad

```text
Fixtures + evento versionado + sesión sintética
                    ↓
Preflight / permisos / lease / presupuesto
                    ↓
Handler directo → SAM local → contenedor RIE limitado
                    ↓
Puertos fake / servidor fixture local / CDP loopback
                    ↓
Resultados tipados + errores + métricas sin contenido privado
                    ↓
Comparar corrección, memoria, tiempo, costo aproximado y recuperación
```

- Nivel 0: tests de dominio/contratos sin Docker ni navegador. Rápidos en cada PR.
- Nivel 1: Playwright contra fixture local: cookies, localStorage, IndexedDB,
  paginación, login-wall, sesión vencida, DOM tardío y extracción de productos.
- Nivel 2: SAM local invoke del evento completo con transportes fake. AWS
  documenta esa ejecución local [S1]. Red privada solo para el fixture permitido;
  la excepción de test no habilita SSRF ni redes privadas en producción.
- Nivel 3: RIE con límites Docker, filesystem read-only salvo /tmp, salida de
  red deshabilitada o allowlist de servicios de ensayo. Probar reinicios y OOM.
- Nivel 4: ensayo local de lectura con cuenta propia, solo con encargo separado.
- Nivel 5: canary AWS autorizado con presupuesto; único que valida IAM, red AWS,
  CPU Lambda real, aceptación de IP y eventos SQS/S3 efectivos.

RIE no emula orquestación, IAM ni seguridad/red AWS [S2]. SAM/RIE no prueban
exactly-once, entrega FIFO ni permisos S3. Dobles locales prueban nuestros
contratos, no certifican AWS. No usar LocalStack u otro stack completo por defecto;
evaluarlo solo si un test concreto lo necesita, verificando edición y licencia.

## 3. Archivos y comandos que se implementarán

Estructura propuesta; no son rutas ya creadas:

```text
infra/sam/template.yaml
infra/docker/browser.Dockerfile
infra/docker/whatsapp.Dockerfile
workers/browser/                 # handler, CDP, extracción
workers/whatsapp/                # handler, sincronización, persistencia
contracts/                      # eventos/resultados sin secretos
tests/fixtures/web/              # sitio de ensayo y precios ficticios
tests/events/                   # JSON sintético para SAM/RIE
tests/contracts/ + tests/e2e/ + tests/faults/
scripts/bench-local.ps1          # orquestación y límites, sin secretos en argumentos
.local/reports/                  # resultados y artefactos privados ignorados
```

Interfaz futura: `radar test local`, `radar bench local --suite browser` y
`radar bench local --suite whatsapp`. **No ejecutables todavía**. Deben hacer
preflight, ejecutar etapas y producir un resumen JSON en una sola llamada.

Ejemplo de comando SAM oficial, ejecutable solo tras crear el template/handler:

```powershell
sam build --template-file infra/sam/template.yaml --use-container
sam local invoke BrowserWorker --template-file .aws-sam/build/template.yaml --event tests/events/read.synthetic.json
```

No añadir credenciales reales a env.json, eventos, imagen, build context o flags.
Excluir .local, perfiles, traces y secretos con .dockerignore además de .gitignore.
Un fichero ignorado por Git puede entrar en una imagen si no se excluye del build.

## 4. Diseño para usar menos recursos

1. Preflight antes de iniciar Chromium: operación soportada, fuente habilitada,
   sesión validada, lease, plazo y presupuesto. No arrancar navegador para fallar.
2. HTTP/JSON permitido y comprobado antes de navegador; no asumir que un endpoint
   interno es estable o autorizado. Caché incremental por fuente/cuenta/query y
   versiones; no compartir respuestas privadas entre cuentas.
3. Un Chromium por invocación y un contexto/target explícito por cuenta. Un solo
   OpenCLI leyendo ese target a la vez. Sin extensión, túnel ni servidor MCP por
   invocación; CDP exclusivamente en loopback. Auditar helpers antes de habilitar.
4. Batch inicial de 3 consultas, probar 5 y 10 secuenciales para amortizar arranque.
   Cortar por tiempo/resultados/memoria, hacer checkpoint y encolar continuación.
   No mantener un navegador infinito hasta alcanzar un objetivo comercial.
5. Cerrar páginas/contextos/subprocesos en finally; timeout de CLI y de Chromium.
   No dejar procesos en background ni datos de cuenta en una instancia warm.
   Cachear solo binarios/recursos públicos inmutables; /tmp no es almacenamiento
   durable. La reutilización del entorno Lambda no está garantizada [S5].
6. DOM acotado: devolver campos necesarios, máximos de páginas/filas/bytes y
   detener scroll cuando no crece. Nunca devolver HTML/inbox completos al LLM.
7. Esperar selectores o respuestas concretas, no sleeps largos ni networkidle
   como condición universal en redes con tráfico continuo. POM/fixtures
   compartidos según la skill E2E; browser único Chromium, no matriz de 4 browsers.
8. Perf profile sin videos/screenshots/traces. Debug profile solo con fixtures,
   artefactos privados con TTL. Traces reales pueden contener tokens/PII.
9. Bloqueo selectivo de videos/fuentes/imágenes solo si conserva extracción.
   Si fotos sirven para identidad del perfume, extraer URL/miniatura necesaria;
   no borrar evidencia por ahorrar RAM. Medir también modo sin bloqueo.
10. Routing Playwright puede deshabilitar caché HTTP e interferir con service
    workers [S6]. No prometer que bloquear recursos siempre acelera; comparar
    variantes y no bloquear scripts/XHR de sesión o contenido necesario.
11. WhatsApp: sin descargar media ni cargar historial completo para consultar
    estado. Sincronización limitada por deadline; SQLite consistente tras cierre
    o backup, WAL incluido correctamente. No clonar dispositivo del bridge activo.
12. Reintentos limitados solo en lecturas idempotentes. CAPTCHA/MFA/401 →
    needs_reauth; 429 → backoff/circuit breaker. Envío incierto → reconciliar,
    nunca reenviar a ciegas. CAS de estado no deshace un mensaje aceptado.

## 5. Presupuesto y benchmark reproducible

Propuesta inicial, NO resultados medidos:

| Suite | RAM contenedor a comparar | CPU local | Batch / deadline inicial |
|---|---|---|---|
| Lectura HTTP | 128, 256, 512 MiB | 0,25 / 0,5 / 1 CPU | 5 / 30 s |
| Chromium/OpenCLI | 1.600, 2.048, 3.072 MiB; 1.024 solo diagnóstico | 0,5 / 1 / 2 CPU | 3 / 60 s + 10 s de cierre |
| whatsmeow sintético | 128, 256, 512 MiB | 0,25 / 0,5 / 1 CPU | 1 evento / 30 s; sync real por medir |

Exploración por etapas, no todo el producto cartesiano: baseline 2.048 MiB/1 CPU,
descender RAM si pasa; aumentar CPU si baja tiempo útil; luego batch. Handler
timeout propuesto 90 s para navegador, presupuesto de trabajo 60 s, cierre 10 s
y margen. Supervisar deadline tanto en SAM como en RIE. Docker usa --memory,
--cpus y, donde lo soporte, memory-swap igual a memory para no ocultar OOM con
swap. No desactivar OOM-killer; registrar salida, throttling y reinicio [S7].

La CPU de AWS depende de la RAM: a 1.769 MB equivale a una vCPU [S3]. Las cuotas
CPU independientes de Docker son stress tests, no equivalencia exacta de Lambda.
RAM menor puede ser más lenta y gastar más GB-s. Conservar perfil funcional y
perfil de seguridad idénticos; nunca desactivar controles para ganar benchmark.

Paralelismo host adaptativo:

- Iniciar con 1 worker de navegador; parseo/fixtures pueden correr en paralelo.
- Reservar al menos 4 GiB para Windows/Chrome/otras apps; objetivo inicial del
  laboratorio <= 6 GiB, limitado por RAM libre real. Hoy 7,3 libres no permiten
  gastar 6 adicionales manteniendo esa reserva. No alterar .wslconfig sin permiso.
- workers <= floor(budget_disponible / (peak_por_worker × 1,25)), mínimo admisible
  1 solo si hay presupuesto; si no hay, esperar/liberar recursos, no forzar 1.
- Recalcular con presión Windows + WSL/Docker, no solo RSS de Node. Reservar RAM
  adicional para fixture y build; builds y benchmark no corren a la vez.
- Una cuenta = un worker activo aunque sobren núcleos. Paralelismo por cuentas
  independientes/fuentes permitidas; pool HTTP separado y acotado.

Protocolo: warm-up fuera de muestra; primero 5 smoke runs; luego 20 procesos
nuevos (cold local) y 50 invocaciones repetidas (warm local). Warm es etiqueta
local, no garantía AWS. Registrar tamaño de muestra y distribución completa;
p95 de muestras pequeñas no es SLA. Fixtures/dataset/hash iguales y orden de
perfiles alternado; desactivar actualizaciones/downloads durante medición.

Métricas mínimas JSONL: run_id, operation_id sintético, suite, image_digest,
browser/runtime_version, architecture, config_hash, cold_local, memory_limit,
cpu_limit, batch, init_ms, execution_ms, cleanup_ms, CPU-seconds, peak_cgroup,
bytes_io, tmp_bytes, child_process_count, status y useful_records. cgroup suma
hijos; muestreo puede perder picos: anotarlo y registrar eventos OOM/peak del
kernel cuando exista. Separar métricas del proceso, contenedor y VM Windows.

Comparación: GB-s aproximados por resultado correcto = memoria asignada GB ×
tiempo / resultados correctos. Si cero resultados correctos, no producir costo
por éxito ficticio. Añadir tasa de error, latencia y tráfico. No atribuir este
cálculo a factura AWS local; nube añade duración facturada, storage/red/servicios.

## 6. Pruebas y criterios de salida

| Caso | Aprobación requerida |
|---|---|
| StorageState: cookie/localStorage/IndexedDB | Export/import reproduce acceso al fixture; faltante produce fallo específico |
| sessionStorage/estado no exportable | Detectar requisito; no etiquetar sesión como portable universal |
| CDP: target/contexto equivocado | Fallar antes de leer otra cuenta; comandos tabs/selectTab incompatibles deshabilitados |
| Sesión vencida/renovación concurrente | needs_reauth; worker antiguo no puede publicar estado sobre versión nueva |
| Dos workers/mismo alias | Segundo no inicia operación; probar expiración/lease perdido en torno al efecto |
| OOM, timeout, proceso muerto | Checkpoint/reanudación sin basura ni éxito falso; fixture reproducible |
| Caída después de aceptación ficticia | send_uncertain + reconciliación; ninguna prueba envía mensajes reales |
| 50 invocaciones warm | Sin crecimiento sostenido inexplicado, sin fuga entre cuentas ni procesos huérfanos |
| Optimización de recursos | Mismo resultado esperado; 0 regresiones críticas frente a baseline |
| Revocación y cifrado | Destinatario sin clave no abre bundle; sesión revocada no sirve para nuevas operaciones |

Umbral inicial propuesto: peak <= 75% del límite en suite representativa, cero
OOM/timeouts en esa muestra, cierre dentro de presupuesto. Detectar crecimiento
con series y repetir antes de declarar leak. No exigir mejora porcentual inventada:
elegir el menor GB-s por resultado correcto con margen y latencia aceptables.

CI: contratos/fixtures sintéticos en PR; suite contenedor en Linux y ejecución
nocturna solo si se solicita/programa después. Sin cuentas sociales ni claves de
sesión en GitHub Actions. No cachear bundles/traces. Pruebas reales separadas y
manuales hasta autorización. El plan no crea workflows ni programaciones.

## 7. Orden de implementación y entregables

1. Contratos + servidor fixture + fake state/lease/message transport.
2. Handler navegador directo + target CDP validado + tests de export/import.
3. SAM/template e imágenes mínimas; RIE/restricciones/cierre/estado read-only.
4. bench-local con configuración, métricas, reproducción y reporte comparativo.
5. Session Broker local y UX capture/renew/share descritos en documento complementario.
6. Handler whatsmeow con fixtures/fallos; cuenta de ensayo solo tras decisión.
7. Canary AWS autorizado; presupuesto, IAM/cola/estado y métricas reales.

Responsabilidades si posteriormente se autoriza reparto: runtime/benchmark,
sesiones/UX y contratos/fallos en archivos distintos; coordinador integra. Este
plan no inicia agentes ni modifica el informe reservado de Claude.

## Fuentes primarias consultadas, 2026-10-03

- S1: [AWS SAM local invoke](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/serverless-sam-cli-using-invoke.html).
- S2: [AWS Runtime Interface Emulator: alcance y limitaciones](https://github.com/aws/aws-lambda-runtime-interface-emulator).
- S3: [AWS Lambda: configuración de memoria y CPU](https://docs.aws.amazon.com/lambda/latest/dg/configuration-memory.html).
- S4: [Sparticuz Chromium: Playwright, memoria, versiones y ARM64](https://github.com/Sparticuz/chromium).
- S5: [AWS Lambda: reutilización, seguridad y performance](https://docs.aws.amazon.com/lambda/latest/dg/best-practices.html).
- S6: [Playwright BrowserContext: estado y routing](https://playwright.dev/docs/api/class-browsercontext).
- S7: [Docker: límites de memoria, CPU y swap](https://docs.docker.com/engine/containers/resource_constraints/).

Las fuentes verifican herramientas/límites, no estos handlers ni presupuestos.
El enlace images-test de AWS no devolvió detalle utilizable en esta lectura;
se contrastó RIE con su README oficial. No desactivar TLS ante fallos del lector.
