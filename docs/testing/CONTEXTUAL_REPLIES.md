# A18: composición contextual privada, investigación y borradores

Incremento local candidato para un asistente **general**, no una campaña o
vertical comercial. Astronomía, jardín, literatura, música y servicios son
fixtures intercambiables. No hay acceso real a chats/web, IA, cuentas, cookies,
Telegram/WhatsApp ni efectos reales. La pieza prepara contenido; A16 conserva
interpretación natural, planificación, pasos, leases y UI de producto.

## API y composición

[Aplicación pura](../../src/radar/application/contextual/): `ReplyRequest` liga
propietario/actor/run/task/event host-owned, cuenta/chat/mensaje estable del
proveedor, propósito, pregunta privada, capturas y plazo. `ContextualComposer`
requiere puertos de checkpoint, autorización fresca, contexto, evidencia, vault,
clock y opcionalmente `StructuredLLM`. No importa adapters, contratos B, SDK o red.

[Adaptador local](../../src/radar/adapters/local/contextual.py) contiene:

- `SQLiteContextualStore(tasks, authority, access, limits, clock)`: checkpoint
  por propietario/evento y cuota acumulativa por propietario/tarea, sin otro
  scheduler. Guarda refs/hashes, versión/hash de intención A6 y contadores.
- `EnabledConversationContext(repository_A10, authority)`: consulta exactamente
  owner/account/chat/provider-message ref en un chat habilitado. Recarga permisos,
  consentimiento, sesión y vínculo privado; no elige otro mensaje por parecido.
- `CapturedResearch(resolver_A9, state, query_runner=None)`: reutiliza capturas
  explícitas vigentes. Un query runner autorizado A8 puede adquirir capturas
  nuevas por consulta mínima aprobada y retorna refs/costo API conocido o None.
- `ContextualHandoff(composer, service_A10, context, vault, authority).draft`:
  recarga el resultado durable y mismo contexto/recipient/sesión. Crea sólo
  `Conversations.compose`, con evento derivado del invocation ref del host.
  La pantalla A10 muestra cada texto/destinatario exacto y exige nueva Approval.
- `compose_handler(handoff, request_factory, notice=None)`: callable pequeño
  compatible con A16 `(runtime, authority, run_ref, task_ref, step, outputs)`;
  devuelve documento privado por refs/state/reason. El factory confiable debe
  resolver ambigüedad y bindear esos parámetros; no importa código A16 mutable.
  El notice hook recibe `runtime, authority, batch, run_ref`; puede mostrar
  `service.screen` y `runtime.approval_notice(authority, screen, run_ref=run_ref)`.
  Nunca aprueba o envía. A16 final `a703849` está integrado y ensayado offline;
  el factory/producto y la UI real siguen pendientes.

`CompositionAccess.check` es política fresca del host, no un campo LLM: run/task
vivos y versión original, lease vigente, actor owner, consentimiento, capacidades,
cuenta/chat y sesiones. El checkpoint verifica además A6 confirmado/version/hash
y autoridad actual en cada transición. El host A16 debe ligar `_live_run` y lease;
un run_ref opaco por sí solo no concede acceso. Corregir/cancelar, revocar, retirar
consentimiento o cambiar sesión impide handoff y checkpoint; no revierte efectos.

## Tres modos, sin síntesis ficticia

- `literal`: el propietario dicta texto mediante `question_ref` privado. Mantiene
  el mensaje/contexto exacto; cero investigación y cero llamadas LLM. No permite
  query/evidence refs que agregarían trabajo no pedido.
- `extracts`: prepara extractos literalmente citados y etiqueta **no síntesis ni
  respuesta semántica**. No clasifica qué chats requieren respuesta.
- `reasoned`: usa backend `StructuredLLM` configurado, esquema/prompt y política
  personal/ZDR/precios aprobados por el host. Ausencia → `blocked`, no fallback a
  extractos etiquetados como síntesis. El razonamiento se solicita por paso, no
  por cada retry o tick. Ningún resultado LLM concede permisos ni tool arguments.

Una nueva respuesta dentro de la misma tarea/run puede iniciar otra investigación
y composición con nuevo evento host-owned y provider-message ref: no requiere
otra campaña. Las capturas vigentes conservan procedencia, hash, audiencia y
fecha; capturas caducadas/modificadas, contexto inexistente/ambiguo y falta de
evidencia no se transforman en éxito. El modo literal no busca por cada saludo.
Detectar automáticamente cuáles chats requieren respuesta sigue **pendiente**:
el caller debe seleccionar inequívocamente el mensaje autorizado.
La selección NL de «esto/aquello» y el filtro temporal «hoy» tampoco son
operativos en esta pieza; no se presentan lecturas sintéticas seleccionadas por
el host como identificación autónoma de pendientes reales.

## Investigación mínima y privacidad

La pregunta y conversación siguen privadas por referencias owner-bound A14.
Una búsqueda pública requiere `query_ref` distinto y aprobación de política
`approve_public_query`: el sistema no copia el chat para formularla. Guardas
adicionales bloquean email/URL/teléfono/dígitos largos/multilínea, pero **no son un
detector universal de nombres/PII**. La política humana/host debe verificar
desidentificación y necesidad. El query runner recibe sólo consulta revisada,
refs opacas, presupuesto/deadline y reserva previa por I/O; nunca el texto del
contexto. Fuentes/mensajes y declaraciones del interlocutor son datos, no órdenes.

No plaintext de prompts, chats, consultas, fuentes, respuestas o identidades en
SQLite/WAL/control/logs/repr. A14 sella entradas/resultados reales con age y ACL
en las pruebas; sus claves son sintéticas efímeras. Citas conservan la audiencia
original `worker:sources`; no se rescopea evidencia como si la etiqueta diera
autorización. Producción necesita factory A14 autorizado por owner y audiencias
explícitas `worker:contextual`/`worker:conversations`; no hay fallback en memoria.

## Formato candidato y compatibilidad B observada

[llm.contextual_reply.v1](../../src/radar/schemas/llm.contextual_reply.v1.json) es
**nuevo candidato A18, revisión B obligatoria antes de merge/live**, no un schema
de transporte ni edición de vN publicado. Objeto cerrado: scope exacto, claims
`extracted/inference/interlocutor`, `statement`, spans/citas, desconocidos y
contradicciones. Statement evita cambiar la guarda existente de schemas que
prohíbe `text` fuera de payloads privados. Estos campos siguen siendo privados.

La validación pura comprueba scope owner/account/chat/message, fuente capturada,
hash de bytes, vigencia, límites, spans A9 y hash de fragmento. Extracted debe
coincidir literalmente; interlocutor sólo cita literalmente el mensaje y no se
promueve a verdad web. Inference queda etiquetada y citada; asociación de una
cita **no demuestra verdad, independencia o corrección semántica**. Contradicciones
y desconocidos permanecen explícitos; cobertura parcial/costo desconocido deja
borrador parcial visible, no afirmación de tarea resuelta.

`build_reasoner` reutiliza cliente B real `OpenRouterLLM` con `Prompt` inyectado;
no duplica HTTP, catálogo, precios o modelos. Su default `enabled=False` falla
antes del transporte. El registro default de B rechaza el prompt contextual
en [client.py](../../src/radar/adapters/openrouter/client.py), línea 330; la prueba negativa
lo conserva. El factory candidato sí genera/parsea/contabiliza contra el schema
nuevo con FakeHTTP explícito, `privacy_scope=personal`, provider ZDR y
`data_collection=deny`, sin red ni modelo real. El formato de marcadores interno
de B sigue llamado LISTING, pero el prompt nuevo trata el JSON contextual como
datos; no se reutiliza el schema de extracción de anuncios.

El candidato usa refs sólo a common.v1 e inline para claims/citations, porque el
inliner B sólo soporta refs comunes, [client.py](../../src/radar/adapters/openrouter/client.py), línea 181.
Nada cambia B. Factory host/modelo/endpoints personales ZDR realmente vigentes,
precio/costo, revisión de prompt/schema y evaluación semántica siguen gates.
Simular un modelo/capacidad en FakeHTTP no los verifica en producción.

## Cuotas, incertidumbre y recuperación

Antes de I/O se reserva duramente el techo solicitado de llamadas/tokens/USD en
owner/task. Ceilings son emitidos por host, limitados además por llamadas/USD de
la intención original confirmada A6, y quedan estables en SQLite; cambios
de política/replay con contenido diferente se rechazan. Cada frontera I/O debita
una llamada antes de ejecutarla, incluyendo lecturas privadas/handoff. Query
runner debe debitar cada I/O adicional mediante el callback `reserve`; el resolver
y cliente deben respetar tamaños, timeouts y deadline. No se cancela un SDK
bloqueado por el mero hecho de comprobar plazo antes/después.
Si A16 está en la misma SQLite, la reserva de subllamadas debita atómicamente
`general_runs.calls`, además de la reserva owner/task A18. Exige owner/actor/task,
versión/hash de intención, run activo/deadline y CAS de versión; el host revalida
su lease vigente. El intento del handler ya contado por A16 no se descuenta.
Replay no reserva otra vez, y un fallo revierte ambas reservas o conserva las
comprometidas; no se reembolsa incertidumbre. El contador combinado significa
**intentos A16 + subllamadas I/O reservadas**, no sólo requests HTTP efectivos.
Son fronteras lógicas (contexto, entrada, investigación, captura, modelo,
sellado, handoff), no cada syscall/consulta SQL/lectura de autorización dentro
del chequeo fresco. Query runner debita además cada request externo por callback.
Las comprobaciones privadas/criptográficas del host tienen overhead desconocido,
no un costo nulo derivado de ese contador.
Sin tabla A16, sólo existe el cap asignado a la pieza standalone.
El host aún debe particionar tokens/USD entre otros pasos/proveedores: el
contador A16 no mide uso/costo real de otros intérpretes, lecturas ni API; no es
contabilidad global unificada de tokens/USD. Se conservan contadores y costos
conocidos A18/query, mientras costos globales/vault/CPU desconocidos siguen None.
Literal sin query reserva cero tokens/dólares API y conserva sus límites de I/O
privado; no se inventa costo real cero. A18 no instala un planificador.

Límites configurables: llamadas1–100, tokens1–1M, USD0–100, input hasta64KiB,
salida hasta8192 tokens, fuentes hasta32; texto final A10 hasta4096 bytes.
Costos API de investigación nuevos se restan del máximo disponible para LLM;
costo de búsqueda desconocido bloquea la siguiente llamada. Reutilizar capturas
no vuelve a imputar adquisición pasada; no significa costo computacional cero.
Usage B conocido se conserva incluso con salida inválida; uso/costo ausentes
quedan None. Costo total real, vault/CPU y fuentes originales desconocidos nunca
se inventan como cero. Reservas conservadoras no se reembolsan al fallar/reiniciar.

Marker `claimed` comprometido antes del primer I/O: reentrega/restart concurrente
devuelve `uncertain` si no hay resultado durable, sin volver a invocar LLM.
El primer trabajo todavía vivo puede terminar su checkpoint. Crash después de
respuesta/sellado y antes commit puede dejar ciphertext huérfano; no se genera
otra respuesta ni se finge recuperación. Resultado durable precede handoff;
evento A10 estable converge al mismo borrador si crash ocurre tras compose.
No hay publish/ACK propio, retry loop, envío, aprobación automática ni scheduler.

## Comprobaciones y pendientes

```powershell
$env:PYTHONPATH='src;.'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:HYPOTHESIS_STORAGE_DIRECTORY='.local/hypothesis'
$env:GOPROXY='off'
$env:GOSUMDB='off'
python -m pytest -q tests/contextual tests/test_contracts.py tests/test_architecture.py
python -m pytest -q tests
python scripts/check_docs.py
git diff --check
```

Pruebas offline con A6/A8/A9/A10/SQLite/A14 reales: varios temas, nuevo mensaje
misma tarea→investigación→draft, literal sin I/O innecesario, reuse, prompt/schema
B real con FakeHTTP/ZDR/usage, drift scope/citas/inyección, queries PII, budgets,
concurrencia/restart/lost-response, revocación/cancel/deadline/sesión y privacidad
SQLite/WAL/age. No afirman web real, lectura/identificación de pendientes real,
calidad semántica de un modelo ni envío.
La integración con A16 real comprueba respuesta llegada después de confirmar
intención, query mínima→captura→draft exacto con propósito original, notices
durables pendientes (sin entrega ficticia), cancelación antes de Approval,
presupuesto antes de contexto/LLM, lease vencido antes del backend y crash tras
draft que conserva incertidumbre sin duplicar modelo/batch. El host de prueba
liga `_live_run` y el token exacto del lease adquirido en cada frontera.
Backend sin respuesta devuelve documento privado `uncertain` y step A16
`blocked`, nunca `succeeded`; replay no reinvoca el modelo. También se comprueba
read previo→query/compose→paso posterior, carrera de dos composiciones sin
sobregirar el run y dos literales sin reserva ficticia de IA.

Pendientes: revisión B/candidato golden, factory A16 de producto, interpretación/resolución
de «esto/aquello» humana cuando ambigua, extractor de contexto con timestamp
auténtico/provider-message estable Go, fuentes/adquisición autorizadas, policy de
query/modelo ZDR/precios vigente, factory y evaluación del reasoner real, UX y
aprobación Telegram. El gate Go replay/efectos reales permanece cerrado.
