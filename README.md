# Market Opportunity Radar

**Asistente general de búsqueda, investigación y contacto multicanal con evidencia trazable.**

Alcance aclarado por el usuario el 2026-10-03: recibir pedidos sobre cualquier
tema, buscar información y contactos publicados pertinentes, navegar fuentes
autorizadas, preparar comunicaciones, enviar las aprobadas y seguir respuestas.
Productos, perfumes, talleres y Cashea son ejemplos, no filtros obligatorios.
Ver [alcance general y plan vigente](docs/GENERAL_TASKS.md). El nombre del repo
se conserva; el nuevo alcance no implica capacidades ya implementadas.

Registro de fuentes previsto: web abierta, Facebook/Marketplace/Messenger, X,
Instagram, Threads, Reddit y otros adaptadores que se vayan verificando. Cookies
o sesiones proporcionadas explícitamente se gestionan por cuenta/plataforma;
no garantizan acceso ni soporte universal. Cada operación requiere capacidad
comprobada, permisos y preflight. Mensajería real no está activada por este cambio.

## Modo general: información, chats y respuestas

El núcleo admite pedidos sobre cualquier tema: buscar y contrastar información,
leer conversaciones habilitadas, preparar respuestas, contactar a destinatarios
autorizados y programar tareas. «Lee mis chats», «responde esto» e «investiga este
tema y responde» son ejemplos de lenguaje natural, no comandos ya disponibles
en todas las cuentas. La investigación puede abrirse en cualquier etapa ante un
dato faltante o una nueva pregunta, conservando la tarea y evidencia vigentes.
Una respuesta literal dictada por el propietario no obliga a buscar ni usar IA.

Flujo previsto: pedido → plan y preflight → lectura/investigación según necesidad
→ informe o borrador contextual → aprobación del texto/destinatario aplicable
→ acción por canal verificado → recepción y seguimiento. Productos y talleres
no son campos obligatorios. Mensajes y páginas externas aportan datos, no órdenes.

Los candidatos A16/A18/A19 tienen pruebas locales de composición, persistencia,
privacidad, presupuestos y lectura HTTP/DNS acotados. Son ramas de preparación,
no un bot desplegado ni soporte social universal. Selección automática de
«esto/aquello», clasificación de chats pendientes, filtro «hoy», cuentas y fuentes
reales siguen pendientes. Estado y revisión por entrega en el tablero principal;
un snapshot de worktree no sustituye ese registro.

## Módulo opcional: oportunidades comerciales

Busca ofertas y solicitudes de compra en tiendas, mayoristas, marketplaces y redes
sociales; compara productos realmente equivalentes y estima el margen después de
costos. Diseñado para empezar con perfumes y relojes, sin limitarse a una categoría,
país o plataforma.

Global product sourcing and resale intelligence: discover listings across the open
web and social channels, match equivalent products, and estimate cost-aware margins
with source evidence. Local-first, configurable and designed around Agent Reach.

**Estado: preparación local, 2026-10-04.** A1/A2/A0b están
integradas localmente; A3 añade un incremento en su rama para revisión: implementación
del webhook Telegram con updates/directorio sintéticos, SQLite durable, cola/worker/UI falsos y
comparación de registros con evidencia. Incluye también el laboratorio Docker
y el CLI de sesiones cifradas. No hay buscador comercial ni fuente real verificada.
Esta rama A0g incorpora A3 sólo para conciliar documentación y probar preparación;
no integra A3 en `main` ni sustituye su revisión B pendiente.
No hay campañas, compras, ventas, mensajes o ganancias reales generadas por este
proyecto. No es Job Radar ni una demo de facturas. La implementación A/B empieza
por contratos, dominio y flujo local; ver [tablero](docs/work/BOARD.md),
[tareas de Codex](docs/work/CODEX_TASKS.md) y
[plan de implementación](docs/research/agent-b/implementation-plan.md).
Iniciar estas tareas no autoriza nuevos merges/push, cloud ni contactos reales.

## Qué queremos lograr

Ayudar a una persona que revende productos a responder:

- ¿Dónde encuentro el mismo producto más barato y disponible?
- ¿En qué mercado hay anuncios comparables o solicitudes de compra?
- ¿Qué margen estimado queda tras transporte, comisiones y demás costos?
- ¿Cuánto capital necesito y qué datos faltan antes de decidir?
- ¿La diferencia de precio es real o comparé cantidades, variantes o condiciones distintas?
- ¿Qué cambió desde la última revisión y qué evidencia respalda la oportunidad?

Un anuncio barato no es una oportunidad confirmada. Un precio publicado de venta
no prueba demanda ni una transacción completada. El sistema debe mostrar esa
diferencia, no inventar certezas para producir más resultados.

## Fuentes previstas

| Fuente | Uso previsto | Límite que debemos mostrar |
|---|---|---|
| Web abierta | Tiendas independientes, mayoristas, liquidaciones, clasificados y URLs configuradas | No todas las páginas son legibles ni ofrecen datos completos; soporte por dominio y fallos visibles |
| Mercado Libre | Publicaciones, variantes, precios, disponibilidad referencial y mercados por país | Acceso y autenticación dependen del recurso; precios publicados no equivalen a ventas |
| Facebook | Publicaciones, páginas comerciales y grupos accesibles con autorización | La existencia de un adaptador no garantiza buscar todo Marketplace ni todos los grupos |
| X / Twitter | Ofertas, proveedores, liquidaciones y publicaciones de intención de compra | Búsqueda puede fallar o estar limitada; una publicación no confirma disponibilidad |
| Instagram | Descubrir cuentas comerciales y leer publicaciones de vendedores seleccionados | El adaptador conocido busca usuarios; no equivale a búsqueda global de publicaciones |
| Otros marketplaces | Amazon, eBay u otros mediante adaptadores verificados posteriormente | No se declara soporte por incluir una plataforma en el roadmap |
| Importación autorizada | CSV/JSON y catálogos proporcionados por el usuario | No leer conversaciones privadas ni importar datos de otros proyectos automáticamente |

**Agent Reach será la puerta de entrada preferida** a capacidades existentes,
utilizando OpenCLI y lectores web cuando corresponda. No será un LLM decidiendo
cada paso. El programa debe invocar herramientas de forma estructurada, con
preflight, límites de frecuencia, caché y estados de salud por fuente.

La visión es amplia; la cobertura efectiva se medirá por adaptador, dominio, cuenta
y corrida. No prometemos consultar toda la web ni saltar restricciones de acceso.
Ver [fuentes y capacidades](docs/SOURCES.md).

## Flujo de funcionamiento propuesto

```text
Configuración + preflight
          ↓
Descubrimiento y lectura en paralelo, con cuotas por fuente
          ↓
Anuncios normalizados + evidencia + historial de precios
          ↓
Equivalencia de producto y deduplicación
          ↓
Comparación por mercado + escenario de costos
          ↓
Oportunidades ordenadas + datos faltantes + riesgos
          ↓
Revisión humana e informes por Telegram (previstos)
```

Una corrida podrá detenerse y reanudarse. Las fuentes bloqueadas no se contarán
como búsquedas exitosas ni como mercados sin ofertas. Hallar un contacto no
autoriza un envío: contactar exige encargo, destinatario/texto aprobados y canal
verificado. Compras, pagos y publicaciones requieren un encargo separado.

## Qué tendrá cada oportunidad

- Identidad y variante del producto; cantidad, presentación y condición.
- Anuncio de compra candidato y comparables de venta en el mercado de destino.
- Enlaces, país, moneda, fecha de publicación si existe y momento de observación.
- Precio por unidad, pedido mínimo, disponibilidad y condiciones del descuento.
- Costo puesto en destino, capital necesario y escenarios de precio de venta.
- Ganancia y margen **estimados**, con moneda, supuestos y fecha del tipo de cambio.
- Grado de confianza, posibles conflictos y costos todavía desconocidos.
- Evidencia de demanda cuando exista, separada de los precios de anuncios.
- Revisión pendiente y razones claras para priorizar, descartar o pedir información.

Un perfume de 30 ml no se compara directamente con uno de 100 ml. Un reloj usado
no se equipara a uno nuevo. Identificadores o fotografías similares no prueban
autenticidad. Una réplica no se mezcla con un original ni se promueve como original.

## Cálculo económico

```text
Ingreso neto estimado = precio de venta del escenario − deducciones de venta
Costo total = compra + adquisición + logística + otros costos del escenario
Ganancia estimada = ingreso neto estimado − costo total
Margen estimado = ganancia estimada / ingreso bruto de venta
Retorno sobre costo = ganancia estimada / costo total
```

Solo calcular resultados completos cuando las entradas obligatorias sean conocidas
y los denominadores sean válidos. `null` significa desconocido, no cero. Impuestos,
importación, transporte y comisiones requieren datos aplicables al mercado; no se
inventarán ni se dará asesoría tributaria a partir de una fórmula genérica.
Ver [modelo de costos](docs/COST_MODEL.md).

## Eficiencia y costo de ejecución

- Sin APIs pagadas por defecto; ninguna contratación o descarga de modelos implícita.
- Extracción estructurada, reglas y cálculos programáticos antes de recurrir a IA.
- IA local opcional para ambigüedades de texto o imágenes, con presupuesto explícito.
- Caché con caducidad, actualización incremental y almacenamiento por hash.
- Paralelismo limitado por dominio/cuenta; no miles de pestañas en una sesión compartida.
- Solicitudes batch solo cuando el backend realmente las soporte.
- Reintentos acotados, backoff y suspensión temporal de fuentes con fallos repetidos.
- Telegram como interfaz única del producto MVP; herramientas CLI locales para
  administración técnica, sesiones y pruebas, no una segunda UI comercial.

Gratuito en cargos API no significa cero tokens, electricidad, hardware o tiempo
humano. La cuota gratuita de una herramienta también puede agotarse.

## Arquitectura y calidad previstas

Monolito modular hexagonal: dominio de productos/costos independiente de
extracción, persistencia y LLM. Diseño de implementación: **cuatro Lambdas**
(`bot` y `app` Python, `browser` Node, `whatsapp` Go) más CLI local
`sessions-admin` Go. DynamoDB para control mutable, S3 para blobs inmutables,
SSM para secretos y age para sesiones; SQLite/fakes para desarrollo local.
Inbox/outbox transaccionales evitan perder tareas entre guardar y encolar;
ledger durable e incertidumbre explícita evitan reenvíos ciegos, sin prometer
exactly-once. Ningún componente cloud está instalado o desplegado por A0.

HTTP permitido primero, navegador como respaldo y cuotas por dominio/cuenta.
R1 (comandos directo desde Streams) se probará antes de retirar `commands.fifo`;
la topología baseline conserva cuatro colas. Concurrencia/presupuesto limitan
consumo, no garantizan USD 0. El alcance general confirmado separa pedidos,
hallazgos, contactos y comunicaciones de las reglas opcionales de productos.
Telegram es la UI única del MVP; dashboard/Mini App y vectores quedan posteriores.

La calidad se medirá sobre productos y oportunidades comerciales: equivalencias
correctas, precios extraídos, disponibilidad, citas, costos, falsas oportunidades y
resultados de revisión. No reutilizaremos porcentajes de otra demo como métricas
de este producto. [Arquitectura](docs/ARCHITECTURE.md) · [Evaluaciones](docs/EVALUATIONS.md).

## Documentación

- [Tablero A/B y propiedad de archivos](docs/work/BOARD.md)
  · [primeras tareas A](docs/work/CODEX_TASKS.md)
  · [plan F0–F15](docs/research/agent-b/implementation-plan.md)
  · [revisión de arquitectura final](docs/research/architecture-final-review.md).
- [Reparto de implementación y cierre de coordinación con Claude](docs/testing/WORKPLAN.md).
- [Resultados Docker, incidencias y límites de costos](docs/testing/LOCAL_RESULTS.md).
- Laboratorios ejecutables: [navegador Lambda/RIE](lab/browser/README.md)
  · [sesiones cifradas, intercambio y renovación](lab/sessions/README.md).
- [Plan de testing local de Lambda y optimización RAM/CPU](docs/LOCAL_LAMBDA_TESTING.md)
  · [captura, intercambio cifrado y renovación de sesiones](docs/SESSION_MANAGEMENT.md).
- [Investigación coordinada de redes, sesiones y autonomía](docs/research/README.md)
  · [prompt para Claude (investigador B)](docs/research/prompt-agent-b.md)
  · [decisiones y pendientes](docs/research/decisions.md).
- [Visión, alcance y usuarios](docs/VISION.md).
- [Lenguaje del dominio](CONTEXT.md).
- [Fuentes, acceso y evidencia](docs/SOURCES.md).
- [Arquitectura modular y flujo persistente](docs/ARCHITECTURE.md).
- [Configuración propuesta](docs/CONFIGURATION.md) · [ejemplo JSON](config/radar.example.json).
- [Costos, moneda y margen](docs/COST_MODEL.md).
- [Evaluaciones y criterios de aceptación](docs/EVALUATIONS.md).
- [Seguridad, privacidad y acciones humanas](SECURITY.md).
- [Roadmap y estado real](docs/ROADMAP.md).
- [Cómo contribuir a este repositorio](CONTRIBUTING.md).

## Ejecutar el laboratorio local

Docker Desktop con Engine Linux, Python 3.11+ y recursos disponibles. Los builds
descargan dependencias; las mediciones usan contenedores sin red exterior. No
necesitan cuentas, cookies reales, AWS, modelos ni APIs de IA.

```powershell
python -m lab.runner --suite sessions --build --memory-mib 128,256 --trials 3
python -m lab.runner --suite browser --build --mode both --memory-mib 1024,1600,2048 --trials 3 --warm-invocations 1 --batch 3
```

Cada corrida genera JSONL privado en `.local/reports/`. Un solo contenedor a la
vez; compara lectura directa y OpenCLI sobre el mismo fixture. Registra RAM peak,
CPU, tiempos, imagen, errores y cleanup. Mantiene 4 GiB de reserva en el host y
25% de margen; perfiles sin RAM se registran `skipped_resource`, no como éxitos.
No borra ni detiene otros contenedores. Los límites Docker no reproducen el CPU,
networking ni la facturación de AWS.

## Probar el flujo sintético A3

En un entorno Python 3.11+ aislado, instalar las dependencias fijadas y ejecutar:

```powershell
python -m pip install --no-deps -r requirements.lock
python -m pip install --no-deps setuptools==84.0.0
python -m pip install --no-deps --no-build-isolation ".[test]"
python -m pip check
$env:PYTHONPATH="src"
python -m pytest -q tests/flow
```

La integración programática está en `radar.adapters.local.runtime.LocalRuntime`:
recibe una ruta SQLite privada (por ejemplo `.local/control.sqlite`), fixtures
de campos crudos y `synthetic_authorized=True`. Se configura un directorio
numérico **sintético**, consentimiento, `SavedSearch` por propietario y capacidad
autorizada; `webhook.handle_update(...)` recibe `/buscar fixture`. Después,
`pump(owner_ref, search_ref="search:perfume")` procesa la cola durable baseline
`commands.fifo`, worker falso y resultados v1 validados hasta el informe/UI falsa.
Acepta sobres canónicos v1/v2 de B7 y conserva la versión del comando en cada
tarea, continuación y resultado; browser/Telegram siguen usando payloads v1.
El webhook actual emite sobre v2. Esto no habilita los nuevos handlers WhatsApp.
[Ejemplo programático reproducible](tests/flow/test_search_flow.py) y
[configuración sintética explícita](tests/flow/conftest.py).

SQLite guarda receipt + comando + outbox en una transacción; conserva IDs al
replay, snapshot de búsqueda/costos, presupuestos de trabajos/páginas, cursor,
resultados y alert intents locales. Estos intents tienen tipo propio y **no** son
un nuevo kind de transporte v1. Publicar antes de marcar puede repetir;
consumidores y UI falsa convergen. Los resultados admitidos antes del deadline
pueden proyectarse después; no se inicia otra página vencida.

El presupuesto incluye un máximo explícito de comparaciones por corrida, con
pares ya evaluados, contador y razón visible al limitar candidatos. Es un arnés
acotado, no un benchmark ni un índice de búsqueda optimizado.
El dominio normaliza, compara y calcula con A2; el reporte muestra cobertura,
errores, descartes, faltantes y IDs. El costo de cómputo USD sigue desconocido.
Límites actuales: 18 monedas, liquidación de lotes completos y costos aplicables
declarados; los precios publicados no acreditan ventas ni ganancias realizadas.
Datos sintéticos públicos permanecen en blobs locales sin cifrar; este adaptador
rechaza referencias privadas y no simula age. IA y fuentes reales siguen apagadas.

Este corte cubre búsqueda/resultados y fallos locales; aprobación/envío/
reconciliación simulados tienen pruebas de autoridad vigente, crashes, leases,
owners separados y proofs del diario independiente del proveedor falso.
Solo un actor de rol owner puede aprobar; cambiar permiso, consentimiento,
sesión o vencimiento después del claim impide el efecto simulado. Caídas dejan
`send_uncertain`, sin reenvío; una referencia de proof resuelta por propietario
y correlación previa permite reconciliar. Estas transacciones del proveedor falso
no son fencing ni evidencia de comportamiento de WhatsApp real.
El adaptador de `UnitOfWork` es parcial: recepción/claim están cableados;
`approve_action`, `record_result` y `transition` con wire outbox se rechazan sin
mutación, hasta disponer de payload privado/proveedor verificado. No acredita
conformidad completa A1 ni integración con Go real/AWS. `/stop` es cancelación
local durable, no Logout en WhatsApp. Los demás comandos B2 se aparcan en una
cuarentena local durable como `unsupported`; no se presentan como implementados.
La cuota de cola es por propietario.

El relay local opta por `pause_on_capacity=True`: la capacidad conocida durante
publish cede a consumidores y conserva la entrada pendiente sin marcar/ACK.
En ese modo `None` puede significar pausa, no outbox vacío; la pasada siguiente
retoma paginación keyset de pendientes. El helper por defecto conserva su
excepción de capacidad. Errores de almacenamiento, OverflowError genérico,
mark y failpoints posteriores al publish siguen propagándose. La regresión
`tests/flow/test_backpressure.py` reprodujo 101 updates aceptados bloqueados
antes de consumir; ahora prueba límites 1/2/100, FIFO, restart y cuarentena.

Cancelación, stop, revocación,
consentimiento retirado, deadline y resultado obsoleto se aparcan antes del ACK,
con IDs, hash y código estático, sin texto privado. La allowlist exacta está en
`PERMANENT_REJECTIONS`; conflictos desconocidos, almacenamiento y crashes
siguen visibles y no se ACKean. La cuarentena no es una DLQ de AWS ni un nuevo
handler: conserva comando/evidencia para inspección local, sin reenvío automático.
Reconsentir no reactiva un actor revocado.
Las alertas denegadas por directorio permanecen sin marcar; se omiten solo en
esa pasada keyset para no bloquear otros destinatarios ni siguientes lotes.
El diagnóstico devuelve únicamente referencia y código estático; los demás
errores de UI/persistencia siguen propagándose.
La conexión serializa sus operaciones y mantiene el lock hasta commit/rollback; cursores se materializan
bajo ese lock. Esto permite hilos locales, no garantiza paralelismo de consultas
ni fencing de proveedores. Timestamps UTC llevan seis decimales fijos; la cola
sigue leyendo timestamps históricos de segundo completo. Cancelar una corrida
terminal no altera su estado ni versión. Telegram sigue siendo
la única UI comercial prevista; este arnés no añade otra CLI de producto.

`--host-reserve-gib` permite configurar explícitamente la reserva (3–16 GiB,
default 4). Una reducción queda marcada `reduced_reserve_experiment`; no se hace
automáticamente para conseguir un test aprobado.

`--price-per-gb-second` acepta una tarifa explícita para una aproximación; sin
ella el costo permanece desconocido. No demuestra factura cero ni rentabilidad.

## Verificar documentación y contratos

Requisitos para comprobar la documentación: Python 3.11+ y Git. No hace falta
iniciar sesión en redes sociales, instalar modelos ni proporcionar credenciales.

```text
git clone https://github.com/davidvalorwork/market-opportunity-radar.git
cd market-opportunity-radar
python scripts/check_docs.py
python -m unittest discover -s tests -q
python -m pytest -q tests
node --test lab/browser/selftest.mjs
```

El control valida enlaces locales, configuración documental, archivos requeridos y
ausencia de archivos privados versionados. Los tests Python cubren también el
runner con Docker simulado; los de Node comprueban contratos sintéticos sin
dependencias externas. Ninguno reemplaza las pruebas Docker ni prueba redes
sociales. GitHub Actions ejecuta guardas documentales y pytest en Windows y Linux.
Pytest requiere las dependencias fijadas en `requirements.lock` y el paquete
local con su extra `.[test]`, según las instrucciones anteriores. A0b ya añade
jobs Python Windows/Linux, contratos Node y ambos módulos Go. Las pruebas
locales no demuestran una corrida de GitHub Actions ni validan cuentas reales.

## Límites y siguiente entrega

### Piloto integrado posterior a A3

[Piloto Telegram → investigación → WhatsApp](docs/testing/TELEGRAM_PILOT.md):
host local opt-in, comandos determinísticos, caché compacta persistente y
aprobación exacta de mensajes. `/mas` conserva la investigación y omite contenido
visto; `/limites` controla resultados, consultas, rondas, bytes y tiempo.
No requiere llamadas LLM para estos comandos. Sesión WhatsApp y envío real
son gates independientes: una copia compatible en Docker sin red no acredita
un trayecto Telegram → proveedor completo. El piloto no interpreta pedidos
arbitrarios en lenguaje natural ni incorpora todas las redes sociales.

El incremento A3 permite normalizar fixtures y producir informes reproducibles
de costos sin acciones externas. Falta revisar/integrar la rama y construir
conectores de fuentes autorizadas y adaptadores wire para las acciones. Después
se habilitarán fuentes una por una al verificar
acceso y extracción; la cuota de búsqueda no reemplazará la calidad de comparación.

Ni la publicación del repositorio ni un test aprobado demuestran utilidad real,
ROI o rentabilidad. Eso necesita pruebas con usuarios, revisión de casos y resultados
comerciales registrados con consentimiento.

## Licencia

[Apache-2.0](LICENSE). Copyright 2026 David Valor. La licencia del código propio
no concede derechos sobre contenidos, imágenes, marcas ni datos de terceros.
El worker Go incorpora dependencias con obligaciones propias: B registra una
dependencia transitiva GPL-3.0 en [su inventario](go/README.md). Distribuir un
binario/imagen del worker exige resolver esas obligaciones; A3 no distribuye
binarios ni imágenes y no convierte todas las dependencias en Apache-2.0.
