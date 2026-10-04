# Informe A — Cobertura por red y autonomía operativa

Responsable: Codex coordinador (frente A). Frente B asignado a Claude.
Consulta: 2026-10-03.
Estado: frente A cerrado documentalmente; informe de Claude leído e integrado.
Evidencia: documentación primaria, código público y catálogo local.
No hay pruebas end-to-end de búsqueda, DM, cookies compartidas o Lambda.

## Conclusión inicial

Actualización tras leer B: Claude registra que el usuario prefiere Lambda bajo
demanda, sin host permanente. Bajo esa restricción, el candidato de MVP pasa a
Chromium efímero + Playwright storageState cifrado + OpenCLI CDP local, y WhatsApp
intermitente con whatsmeow. Son hipótesis de implementación, no conectores probados.
Las propuestas de gateways de esta conclusión inicial quedan como alternativas
históricas, fuera del MVP Lambda-only. Ver A-F26/A-F27 y coordinación con B.

No se ha identificado un componente que habilite todas las redes, búsqueda global
y contacto privado dirigido únicamente por compartir cookies. La combinación
provisional es Agent Reach/OpenCLI para capacidades de lectura verificadas,
bridges específicos para conversación y un Session Broker que entrega referencias
y leases a workers autorizados. El broker es una propuesta, no código existente.

Separar `discover`, `read_listing`, `public_reply`, `start_dm`, `reply_dm` y
`receive_messages` por plataforma/backend/cuenta. No inferir una capacidad a
partir de otra. Mantener Lambda como coordinador y la sesión persistente en un
gateway cuando lo requiera el protocolo. No habilitar publicaciones o mensajes
en el MVP documental por ampliar la lista de redes.

La ampliación encontró una superficie de mensajería común: Beeper Desktop API
o EasyMatrix sobre bridges Matrix. No reemplaza discovery ni garantiza DM nuevo
por red. Propuesta: lectura Agent Reach/OpenCLI + WhatsApp existente como primer
piloto, comparando la alternativa unificada con Claude antes de añadir servidores.
Ver A-F17 a A-F25; no se implementó ni se conectó ninguna cuenta.

## Evidencia local y alcance de las pruebas

- `agent-reach doctor --json`: puente OpenCLI conectado, pero el diagnóstico no
  ejecuta operaciones reales en Facebook, Instagram, X o Reddit; `active_backend`
  nulo no demuestra ni disponibilidad ni inexistencia del backend.
- `opencli auth status`: chequeo rápido, no login/lectura validada. Algunas redes
  muestran login detectado; Reddit queda desconocido. No se publican identidades
  ni salidas privadas del diagnóstico.
- `opencli list`: inspeccionado localmente, versión anunciada 1.8.6 y aviso de
  actualización a 1.8.8. No se actualizó ni instaló nada. El catálogo incluye
  Threads; no se encontró ese adaptador en el árbol upstream inspeccionado, por
  lo que su procedencia/portabilidad debe revisarse antes de distribuirlo.
- No se ejecutaron `reply-dm`, `publish`, `reply`, `comment`, comandos de login,
  extracción de cookies ni lectura de inbox. Ningún vendedor fue contactado.

## Matriz inicial de plataformas

| Red | Descubrimiento/lectura observado | Contacto documentado / limitación | Sesión y entorno | Estado |
|---|---|---|---|---|
| Facebook/Marketplace | OpenCLI: search de personas/páginas/posts; grupos visibles. marketplace-listings navega anuncios propios | marketplace-inbox enumera conversaciones; no demuestra envío ni búsqueda de ofertas de terceros | Chrome + bridge; cuenta autorizada | Código inspeccionado, no prueba real; F01-F02 |
| Messenger | mautrix-meta conecta Messenger con Matrix | Bridge de conversaciones; la cobertura específica de Marketplace y nuevos destinatarios necesita prueba | Sesión privada + servicio persistente + Matrix/PostgreSQL | Documentado, experimental; F03-F04 |
| WhatsApp | No usar como buscador universal; contactos proceden de publicaciones o importación autorizadas | whatsapp-mcp y WAHA documentan envío/recepción; no son API oficiales ni garantía de entrega o ausencia de bloqueo | Multidispositivo; QR inicial; bridge habitual persistente o propuesta whatsmeow intermitente en Lambda | Documentado, propuesta Lambda sin probar; F05-F07/A-F27 y B-F015 |
| X | OpenCLI search/thread/tweets | reply-dm recorre chats recientes; Twikit tiene send_dm(user_id); mautrix-twitter ofrece bridge DM con límites de historial/creación | Chrome o gateway no oficial | Código/documentación; DM actual pendiente; F08-F09/F18/F25 |
| Threads | Catálogo local: search, feed, post. API oficial: búsqueda por keyword con permiso específico | Catálogo local publish/reply son públicos; no se verificó un adaptador de DM. No confundir red Threads con threads de conversaciones | Chrome para adaptador local; OAuth en API oficial | Catálogo y documentación; procedencia pendiente; F10-F12 |
| Reddit | OpenCLI search/subreddit/read | comment/reply son públicos; catálogo inspeccionado no demuestra DM/Chat. PRAW documenta PM/modmail, no prueba Chat actual ni permisos de cuenta | Chrome o OAuth autorizado según backend | Catálogo/documentado; DM/Chat real pendiente; F13-F14 |
| Instagram | OpenCLI busca usuarios y lee posts de cuentas concretas, no posts globales por keyword | mautrix-meta incluye un bridge separado de Instagram DMs; OpenCLI de lectura no demuestra DM | Chrome o bridge privado persistente | Documentado, no probado; F01/F03 |
| TikTok | Catálogo local search/user | Comentarios públicos disponibles en catálogo; DM no verificado | Chrome y sesión autorizada | Solo catálogo, candidato secundario; F01 |
| Bluesky | OpenCLI local: search de usuarios; protocolo oficial: searchPosts por keyword | Contratos de conversación 1:1 y sendMessage, con errores de bloqueo/mensajes deshabilitados | OAuth recomendado por SDK; no cookies obligatorias | Lexicons inspeccionados; sin cuenta probada; F22 |
| Telegram | Canales/grupos accesibles bajo permisos; búsqueda global no probada | Telethon y mautrix-telegram documentan mensajería; bot no equivale a usuario | api_id/api_hash y sesión MTProto; no cookies | Documentado; Telethon migró a Codeberg; F23 |
| Discord | Bot en espacios autorizados, no buscador universal | No automatizar cuenta normal como self-bot; bot API bajo permisos | Token de bot y alcance verificado | Política oficial; sin pruebas; F24 |
| Otras redes | Pinterest, LinkedIn, Signal y otras si aportan vendedores | Beeper anuncia algunos canales, pero API común no prueba discovery ni DM nuevo | Depende de cuenta, permiso y backend | Pendiente; F17 |

## Hallazgos y fuentes primarias

Todos consultados el 2026-10-03. Los estados aplican al hecho indicado, no al flujo
completo. No se copian cookies, datos privados ni código de terceros al proyecto.

### A-F01 — Agent Reach/OpenCLI no iguala capacidades entre redes

`documentado` / `inspeccionado`: OpenCLI reutiliza Chrome con extension y daemon,
perfiles seleccionables y comandos determinísticos. Su licencia observada es
Apache-2.0. Agent Reach enruta plataformas, no sustituye al gateway ni da permiso
de enviar. Compartir un perfil no significa que cada worker deba conducir la
misma pestaña simultáneamente.

Fuentes: [Agent Reach](https://github.com/Panniantong/Agent-Reach),
[OpenCLI](https://github.com/jackwener/OpenCLI).

### A-F02 — Marketplace: anuncios propios e inbox no son buscador ni envío

`inspeccionado`: marketplace-listings navega `/marketplace/you/selling/`.
marketplace-inbox navega inbox y devuelve conversaciones visibles. El esquema de
capacidades debe representar ambas operaciones sin habilitar búsqueda de terceros
ni mensajería de manera implícita.

Fuentes: [listings](https://github.com/jackwener/OpenCLI/blob/main/clis/facebook/marketplace-listings.js),
[inbox](https://github.com/jackwener/OpenCLI/blob/main/clis/facebook/marketplace-inbox.js),
[search](https://github.com/jackwener/OpenCLI/blob/main/clis/facebook/search.js).

### A-F03 — Messenger/Instagram personal: mautrix-meta es un candidato distinto

`documentado`: repositorio AGPL-3.0, no archivado; incluye Messenger e Instagram
DM como bridges. No garantiza sesiones portables entre cuentas ni acceso a todos
los vendedores. Las instrucciones de autenticación advierten sobre CAPTCHA y
acciones de recuperación cuando Meta detecta actividad sospechosa. Inspeccionar
licencia antes de integrar/distribuir; no copiar AGPL al código Apache sin análisis.

Fuentes: [repositorio](https://github.com/mautrix/meta),
[autenticación](https://docs.mau.fi/bridges/go/meta/authentication.html),
[setup](https://docs.mau.fi/bridges/go/setup.html?bridge=meta).

### A-F04 — Messenger oficial no es Messenger personal de Marketplace

`documentado`: Meta describe envío desde páginas/profesionales, IDs de destinatario,
token y permiso; el origen de la conversación y ventanas de mensajería importan.
No atribuir acceso personal a vendedores de Marketplace a un bot de página.

Fuente: [Meta Send Messages](https://developers.facebook.com/docs/messenger-platform/send-messages/).

### A-F05 — WhatsApp existente: reutilización antes de migración

`documentado`: whatsapp-mcp (MIT) usa bridge Go/Whatsmeow con SQLite y QR inicial;
documenta mensajes y media. Se podría invocar su interfaz programáticamente sin
un LLM para decidir cada consulta. `inferencia`: un adaptador hacia el bridge
existente reduciría trabajo frente a cambiar de stack; salud del bridge y prueba
con cuenta de ensayo todavía pendientes en este proyecto.

Fuente: [whatsapp-mcp](https://github.com/lharries/whatsapp-mcp).

### A-F06 — WAHA: alternativa HTTP y novedad de licencia/precio

`documentado`: WAHA (Apache-2.0) ofrece HTTP y sesiones WhatsApp. Su anuncio
2026.6.1 incorpora funciones antes Plus a la imagen pública gratuita: sesiones,
media, almacenamiento, controles de acceso y observabilidad. Hosting y operación
no se vuelven gratis por ello. No se instaló, ni se ensayó compatibilidad local.

Fuentes: [repo](https://github.com/devlikeapro/waha),
[anuncio Community](https://waha.devlike.pro/blog/waha-community).

### A-F07 — WhatsApp oficial: permisos y costo separados

`documentado`: política empresarial contempla permiso del destinatario, plantillas
y ventana de servicio. Precio por categoría/mercado; algunos mensajes de servicio
son gratuitos. No prometer contacto inicial gratuito y arbitrario desde una API
oficial. Un número publicado no equivale por sí solo a cualquier permiso de envío.

Fuentes: [política](https://business.whatsapp.com/policy),
[precios](https://business.whatsapp.com/products/platform-pricing).

### A-F08 — X reply-dm tiene alcance masivo sobre conversaciones existentes

`inspeccionado`: el comando recibe texto y máximo de conversaciones, recorre inbox
y envía. No aporta en su interfaz inspeccionada selección explícita de un nuevo
vendedor. No ejecutar como fallback de consulta individual: un adaptador dirigido
debe verificar thread/destinatario, presupuesto e idempotencia antes de escribir.

Fuente: [reply-dm.js](https://github.com/jackwener/OpenCLI/blob/main/clis/twitter/reply-dm.js).

### A-F09 — X no oficial y política pendiente de lectura

`documentado`: Twikit es MIT y documenta cliente sin key de API. Es un candidato,
no sustituto probado del protocolo de mensajes actual. `pendiente`: la lectura de
la política X Automation por Jina devolvió 403; no se pudo contrastar su contenido
en esta corrida. No habilitar mensajes basándose en que existe una librería.

Fuentes: [Twikit](https://github.com/d60/twikit),
[política pendiente](https://help.x.com/en/rules-and-policies/x-automation).

### A-F10 — Threads: lectura y escritura pública, DM no comprobado

`inspeccionado`: catálogo local contiene feed/post/search/publish/reply; no se
ejecutaron. La procedencia de ese adaptador local debe revisar B. `documentado`:
la API oficial describe publicaciones, perfiles, replies e insights; no se verificó
en las fuentes consultadas un método DM equivalente a estas operaciones.

Fuentes: catálogo `opencli list` local (sin secretos),
[documentación oficial](https://developers.facebook.com/docs/threads/),
[sample oficial](https://github.com/fbsamples/threads_api).

### A-F11 — Threads keyword search tiene aprobación y cuota

`documentado`: permiso `threads_keyword_search`; sin aprobación se limita a posts
del usuario autenticado, después de aprobarse busca publicaciones públicas. La
documentación indica hasta 2.200 consultas por ventana móvil de 24 horas por usuario
y entre apps. No extrapolar esta cuota a OpenCLI ni afirmar acceso global por OAuth.

Fuente: [Keyword and Topic Tag Search](https://developers.facebook.com/documentation/threads/keyword-search).

### A-F12 — Threads: descartar clientes populares archivados como baseline

`inspeccionado` (metadatos GitHub): junhoyeo/threads-api y Danie1/threads-api
están archivados. No elegirlos por estrellas o ejemplos antiguos; valorar lector
local mantenido y ruta oficial, cada uno con su propia prueba y restricciones.

Fuentes: [junhoyeo](https://github.com/junhoyeo/threads-api),
[Danie1](https://github.com/Danie1/threads-api).

### A-F13 — Reddit PM/modmail no equivale a Reddit Chat

`documentado`: PRAW BSD-2-Clause documenta Redditor.message para PM/modmail.
No se ensayó el endpoint ni se confirmó soporte de Chat, permisos o límites
actuales. El catálogo OpenCLI inspeccionado muestra lectura y comentarios/replies,
no un conector de mensajería privada.

Fuentes: [PRAW](https://github.com/praw-dev/praw),
[Redditor.message](https://praw.readthedocs.io/en/stable/code_overview/models/redditor.html#praw.models.Redditor.message),
[OpenCLI](https://github.com/jackwener/OpenCLI).

### A-F14 — Reddit acceso y uso comercial: no cerrar con afirmaciones antiguas

`pendiente`: se intentaron páginas oficiales de acceso/API. El lector devolvió
navegación o contenido de bloqueo/no utilizable para verificar cuotas y aprobación.
No afirmar aprobación fácil, gratuidad comercial o una cuota universal a partir
de una skill o wrapper. Validar requisitos actuales antes de escoger OAuth/API.

Fuentes pendientes: [acceso](https://support.reddithelp.com/hc/en-us/articles/14945211791892-Reddit-API-Access),
[API Wiki](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki).

### A-F15 — Compartir estado no es clonación universal de sesiones

`documentado`: Playwright describe estado de cookies/localStorage/IndexedDB y
tratamiento diferente de sessionStorage; advierte que sus archivos de estado
permiten suplantación y no deben versionarse. `inferencia`: usar SessionRef,
estado cifrado separado y leases por cuenta; comprobar si el backend permite
importar/exportar ese formato, no convertir todos los protocolos a cookies.

Fuente: [Playwright Authentication](https://playwright.dev/docs/auth).

### A-F16 — Lambda: duraciones e idempotencia no garantizan efectos exactamente una vez

`documentado`: Lambda convencional tiene timeout de 900 segundos. SQS/Lambda
puede entregar eventos repetidos; AWS recomienda idempotencia y respuestas
parciales por lote. `inferencia`: dividir dispatch, recepción y reconciliación;
si un envío quedó incierto, no reenviar solo por reintento de cola.

Fuentes: [cuotas](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html),
[SQS](https://docs.aws.amazon.com/lambda/latest/dg/with-sqs.html),
[Powertools](https://github.com/aws-powertools/powertools-lambda-python).

## Arquitectura propuesta para contrastar con B

### Ampliación de investigación: A-F17 a A-F25

Consulta: 2026-10-03. No se ejecutó ninguna búsqueda social real ni envío.

#### A-F17 — Beeper: API común de mensajes, no búsqueda global

`documentado` / `inspeccionado`: API oficial local para WhatsApp, Messenger,
Instagram, Telegram, X y otros canales. SDK Python MIT; beepctl MIT no oficial.
Requiere Desktop activo, API habilitada y conexión aprobada; el README de la CLI
distingue permiso para acciones sensibles. No se verificó precio del servicio:
SDK open source no implica plataforma completa open source/gratuita.

El SDK incluye `chats.start(account_id, user, allow_invite, message_text)`:
puede crear invitación o primer mensaje, por lo que NO es preflight de lectura.
El nombre visible solo es pista de ranking; verificar identidad exacta. Cada red
necesita ensayo propio de inicio/recepción/recuperación.

Fuentes: [API oficial](https://developers.beeper.com/desktop-api),
[SDK](https://github.com/beeper/desktop-api-python),
[contrato start](https://github.com/beeper/desktop-api-python/blob/24190d32384c0c48b247a2c539594827f62bc180/src/beeper_desktop_api/types/chat_start_params.py),
[CLI](https://github.com/blqke/beepctl).

#### A-F18 — Matrix unifica transporte, no permisos ni cobertura

`documentado`: bridges AGPL-3.0 para Messenger/Instagram, WhatsApp, Telegram y X.
EasyMatrix declara AGPL-3.0-or-later y HTTP headless compatible con Desktop API
sobre gomuks. Exige operación persistente; no es una Lambda que conserva cuentas.

Roadmap mautrix-twitter marca texto/media bidireccional, no historial ni creación
privada invitando al usuario. Ticket abierto reporta bucle de sincronización XChat
sin conversation ID. Es reporte externo, no fallo reproducido ni prueba universal.
`inferencia`: MessagingPort común puede reducir adaptadores, pero agrega Matrix,
almacenamiento, bridges y mantenimiento. Evaluar obligaciones AGPL; procesos
separados no se declaran una exención automática. No se instaló EasyMatrix.

Fuentes: [EasyMatrix](https://github.com/batuhan/easymatrix),
[WhatsApp](https://github.com/mautrix/whatsapp),
[Telegram](https://github.com/mautrix/telegram),
[X](https://github.com/mautrix/twitter),
[roadmap X](https://github.com/mautrix/twitter/blob/84f4ab422e31c869bdd5ad0c1afd9d748f93f9fb/ROADMAP.md),
[ticket](https://github.com/mautrix/twitter/issues/132).

#### A-F19 — famabot: referencia específica para Marketplace

`documentado` / `inspeccionado`: MIT; Playwright, consultas configurables, perfil
Chromium, deduplicación SQLite, evaluación LLM, avisos Telegram/ntfy. Hay archivos
de tests de parser/DB/pipeline, NO ejecutados aquí. Scraper inspeccionado: URLs y
filtros, captura de respuestas, fallback DOM y detección de redirecciones.

No se encontró contacto automático con vendedores en lo inspeccionado. Evaluar
por Claude/Codex CLI sigue consumiendo modelo; no sería baseline sin LLM. Su
disclaimer declara propósito personal/experimental y advierte sobre restricciones
Meta y resultados degradados; no confundir esa advertencia con una restricción
comercial de MIT. Estudiar fixtures, dedup y contratos; no adoptar evasión de
controles ni ejecutarlo contra cuentas. Precio parseado no es precio confirmado.

Fuentes: [repo](https://github.com/davidlukac/famabot),
[disclaimer](https://github.com/davidlukac/famabot/blob/6a17637f2db0c6690b924eea5f6f39b08c6a3253/DISCLAIMER.md),
[scraper](https://github.com/davidlukac/famabot/blob/6a17637f2db0c6690b924eea5f6f39b08c6a3253/src/scrape/marketplace.ts).

#### A-F20 — Búsqueda geográfica no equivale a pipeline serverless

`documentado`: facebook-marketplace-nationwide es MIT, ofrece búsquedas por
país/radio, y sus instrucciones requieren Facebook logueado y pestañas. No
demuestra recolección estructurada, mensajes ni Lambda. No se instaló ni se
deshabilitaron bloqueadores. Referencia de planes geográficos, no motor validado.

Fuente: [repo](https://github.com/gmoz22/facebook-marketplace-nationwide).

#### A-F21 — Reddit: revisar migración, no reutilizar Chat antiguo

`inspeccionado`: reddit-chat.js no archivado, pero última actividad observada 2018,
README alpha/Node 8. Descartado como baseline. Un miembro de PRAW atribuye cambios
PM/modmail a migración API; otro issue de lectura fue cerrado al reportarse arreglo.
Issue cerrado por stale no prueba reparación; error antiguo corregido no prueba
fallo actual universal. PM/modmail no se declara Chat moderno funcional.

La página oficial de migración no devolvió contenido útil; la atribución se apoya
en comentarios identificables del mantenedor, no en pruebas propias ni permisos
actuales de uso comercial. Reddit: lectura candidata; Chat aún pendiente.

Fuentes: [cliente antiguo](https://github.com/Mega-Mewthree/reddit-chat.js),
[miembro PRAW](https://github.com/praw-dev/praw/issues/2092#issuecomment-3684063782),
[lectura reportada corregida](https://github.com/praw-dev/praw/issues/2081#issuecomment-3285539564),
[oficial pendiente](https://support.reddithelp.com/hc/en-us/articles/34720093903764-Enhancing-Messaging-on-Reddit-A-simpler-faster-and-easier-way-to-communicate).

#### A-F22 — Bluesky: contratos oficiales de búsqueda y DM

`inspeccionado`: lexicons searchPosts (query/filtros), getConvoForMembers (1:1) y
sendMessage. Contemplan bloqueos, suspensión, mensajes deshabilitados y conversación
bloqueada. Crear conversación tiene efectos: NO usarlo como health check. SDK
recomienda OAuth frente a username/password. No se configuró ninguna cuenta.

LICENSE general dual MIT/Apache-2.0, salvo excepciones por archivo; el detector
GitHub NOASSERTION no bastaba. Búsqueda puede requerir auth según implementación;
no se promete gratuidad ilimitada. docs.bsky.app falló por certificado en el
lector: se consultaron fuentes oficiales GitHub sin desactivar validación TLS.

Fuentes: [licencia](https://github.com/bluesky-social/atproto/blob/a7c8604d876a200a1e4fa4aec83d4189ac4f1c12/LICENSE),
[searchPosts](https://github.com/bluesky-social/atproto/blob/a7c8604d876a200a1e4fa4aec83d4189ac4f1c12/lexicons/app/bsky/feed/searchPosts.json),
[getConvoForMembers](https://github.com/bluesky-social/atproto/blob/a7c8604d876a200a1e4fa4aec83d4189ac4f1c12/lexicons/chat/bsky/convo/getConvoForMembers.json),
[sendMessage](https://github.com/bluesky-social/atproto/blob/a7c8604d876a200a1e4fa4aec83d4189ac4f1c12/lexicons/chat/bsky/convo/sendMessage.json),
[SDK](https://github.com/bluesky-social/atproto/blob/a7c8604d876a200a1e4fa4aec83d4189ac4f1c12/packages/api/README.md).

#### A-F23 — Telegram: sesión MTProto, no cookies

`documentado`: Telethon usa API ID/hash y archivo de sesión reutilizable;
Telegram documenta API de clientes sin cargo, registro y prevención de abuso.
Bot/usuario no tienen el mismo alcance. GitHub Telethon está archivado por traslado
a Codeberg, NO inferir abandono: repo nuevo muestra actividad septiembre 2026.
Verificar versión y fuente antes de instalar; no se probó autenticación/búsqueda.

Fuentes: [traslado](https://github.com/LonamiWebs/Telethon),
[repo actual](https://codeberg.org/Lonami/Telethon),
[sesiones](https://docs.telethon.dev/en/stable/basic/signing-in.html),
[API Telegram](https://core.telegram.org/api/obtaining_api_id).

#### A-F24 — Discord: automatización de bot, no sesión personal

`documentado`: política prohíbe automatizar cuenta normal fuera de OAuth2/bot API
y advierte sobre terminación. No usar cookies/bridge personal como ruta por
defecto. Bot en espacios autorizados es alternativa distinta, con permisos y
alcance acotado; no buscador universal ni contacto a cualquier vendedor.

Fuente: [política](https://support.discord.com/hc/en-us/articles/115002192352-Automated-User-Accounts-Self-Bots).

#### A-F25 — Runtime y reportes de fallos limitan autonomía

`documentado`: AWS congela/termina entornos Lambda; procesos de fondo no son
gateway continuo, `/tmp` no es bóveda persistente. Powertools protege claves de
idempotencia/concurrencia/timeouts, no garantiza un único efecto externo si el
proveedor aceptó el envío y se perdió la confirmación. Lambda Managed Instances
es otra modalidad con costos EC2/gestión, no gateway convencional gratis.

`inspeccionado`: Twikit tiene send_dm(user_id, text, ...) y reportes abiertos de
429/cambios de esquema. mautrix-meta tiene ticket MFA abierto del 1 de octubre.
Son reportes de terceros, no fallos reproducidos. Antes de elegir: prueba del
protocolo actual, reconexión, destinatario exacto y reconciliación de envío incierto.

Fuentes: [ciclo Lambda](https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtime-environment.html),
[Powertools](https://docs.powertools.aws.dev/lambda/python/latest/utilities/idempotency/),
[precios Lambda](https://aws.amazon.com/lambda/pricing/),
[Twikit](https://github.com/d60/twikit/blob/c3b7220866f8582009fe2d1155b6fe92192a2711/twikit/client/client.py),
[429](https://github.com/d60/twikit/issues/433),
[esquema](https://github.com/d60/twikit/issues/425),
[MFA Messenger](https://github.com/mautrix/meta/issues/375).

#### A-F26 — CDP directo: candidato Lambda, compatibilidad parcial inspeccionada

`inspeccionado`: OpenCLI selecciona CDPBridge cuando existe
`OPENCLI_CDP_ENDPOINT`. CDPPage implementa goto, evaluate y getCookies; `tabs()`
devuelve una lista vacía y `selectTab()` no hace nada. Por tanto, un adaptador
que necesite esas operaciones no puede darse por compatible. Fuentes:
[runtime](https://github.com/jackwener/OpenCLI/blob/24136945847afbfad266c6c46a8cd335377f9112/src/runtime.ts),
[CDP](https://github.com/jackwener/OpenCLI/blob/24136945847afbfad266c6c46a8cd335377f9112/src/browser/cdp.ts),
[pasos DSL](https://github.com/jackwener/OpenCLI/blob/24136945847afbfad266c6c46a8cd335377f9112/src/pipeline/steps/browser.ts).

Inspección estática de comandos de lectura; NO ejecución ni auditoría completa
de dependencias/runtime. No se encontraron llamadas explícitas a tabs/selectTab
en los once entrypoints upstream revisados:

| Lectores | Observación | Pendiente |
|---|---|---|
| Facebook search/groups | goto/evaluate o DSL navigate/evaluate | Sesión, DOM, respuesta y target en CDP |
| Instagram search/user | DSL; search consulta usuarios, no productos | Auth, endpoint y esquema actuales |
| X search/thread/tweets | Helpers/imports además del entrypoint | Auditar helpers y probar runtime completo |
| Reddit search/read | evaluate; search usa fetch relativo y COOKIE | Target/origen reddit.com y errores HTTP; JSON vacío no prueba ausencia |
| TikTok search / Bluesky user | Sin cambios explícitos de pestaña en entrypoint | Helpers, permisos y alcance útil |

Threads local: se leyeron search/feed/post y encabezado/helper de extracción;
usan goto/evaluate y, en feed/post, wait. B reporta autoría del usuario. No hay
tabs/selectTab explícitos en los archivos JS inspeccionados, pero aún deben
probarse wait, helpers completos, carga tardía y errores. No se copiaron fuentes.
No declarar ese adaptador como soporte upstream ni como DM.

`inferencia`: un contexto dedicado, un target CDP explícito y un worker por cuenta
pueden evitar dependencia del Chrome del PC. Chromium/Playwright debe cargar el
estado antes de que OpenCLI se conecte; el endpoint queda en loopback. Verificar
que ambos usan el mismo contexto/target y que los recursos necesarios sobreviven
export/import. CDP no exporta ni renueva automáticamente una sesión.

#### A-F27 — S3 CAS protege estado, no garantiza envío único

`documentado`: S3 If-Match compara el ETag del objeto y rechaza escrituras
obsoletas con 412; puede devolver 409 en concurrencia. SQS/Lambda puede repetir
eventos. Fuentes: [S3 escrituras condicionales](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html),
[Lambda con SQS](https://docs.aws.amazon.com/lambda/latest/dg/with-sqs.html).

`inferencia de diseño`: un worker obsoleto puede enviar un mensaje y luego perder
el CAS. El rechazo de la escritura no deshace el envío; no es un fencing token
que el servicio de mensajería valide. FIFO serializa el grupo, pero no elimina
timeouts, reintentos ni invocaciones fuera de cola. No prometer exactly-once.

Propuesta: ledger durable por operación antes del efecto, lease verificable con
owner/expiración, visibilidad SQS suficiente y exclusión de invocaciones paralelas;
tras aceptación incierta, send_uncertain y reconciliación por ID/conversación
antes de cualquier reenvío. Persistir estado SQLite mediante backup consistente
o cierre/checkpoint, no copiando únicamente un archivo activo con WAL pendiente.
Probar caída después del envío y antes de guardar; nunca reintentar a ciegas.

Costo: 400.000 GB-s / (2 GB × 60 s) ≈ 3.333 invocaciones es solo una ilustración
del componente cómputo Lambda. Duración real, cuota compartida, S3, SQS, cifrado,
red y almacenamiento siguen sin presupuesto medido; no asegura operación gratuita.
[Precios Lambda](https://aws.amazon.com/lambda/pricing/).

### Snapshot de mantenimiento

Consulta 2026-10-03; commit consultado, no versión instalada ni garantía operativa.
Fechas de commit, no simple push de tags. Ningún componente fue instalado.

| Componente | Licencia observada | Commit abreviado | Fecha |
|---|---|---|---|
| OpenCLI | Apache-2.0 | 24136945847a | 2026-09-24 |
| mautrix-meta | AGPL-3.0 | 0e6bf6c054c0 | 2026-10-02 |
| WAHA | Apache-2.0 | 90ba445a0e76 | 2026-10-02 |
| Whatsmeow | MPL-2.0 | 8b41cfe6d9c4 | 2026-09-29 |
| mautrix-twitter | AGPL-3.0 | 84f4ab422e31 | 2026-10-02 |
| mautrix-whatsapp | AGPL-3.0 | a0325e76df33 | 2026-09-30 |
| mautrix-telegram | AGPL-3.0 | e94715f823b5 | 2026-10-02 |
| Twikit | MIT | c3b7220866f8 | 2026-03-10 |
| PRAW | BSD-2-Clause | 718b90c75e0f | 2026-09-29 |
| famabot | MIT; disclaimer separado | 6a17637f2db0 | 2026-09-18 |
| Beeper SDK Python | MIT | 24190d32384c | 2026-09-25 |
| beepctl | MIT | ea2a60409b95 | 2026-03-23 |
| EasyMatrix | AGPL-3.0-or-later declarada | bfd3f72a959a | 2026-04-28 |
| ATProto | Dual MIT/Apache-2.0 salvo excepciones | a7c8604d876a | 2026-10-02 |

Fuente de revisiones: API GitHub commits/metadatos; LICENSE/README cuando el
detector no bastaba. Revalidar revisiones/licencias al fijar dependencias.

### Flujo propuesto

```text
CLI / scheduler / Lambda
        ↓
Registro de capacidades + preflight
        ↓
Colas separadas: discovery / lectura / consultas autorizadas
        ↓
Worker por backend → Session Broker (referencia + lease, sin secretos al LLM)
        ↓
OpenCLI CDP + Chromium efímero / lector web / whatsmeow intermitente
        ↓
Resultados + eventos de respuesta → evidencia → comparación económica
```

- Descubrimiento amplio, pero operaciones habilitadas por capacidad comprobada.
- Account lease y routing de pestaña explícito; varias lecturas no justifican
  compartir el mismo perfil writable o un inbox en paralelo sin coordinación.
- Canal y destinatario verificados; autorización de campaña acotada y registro de
  exclusiones/opt-out. No contacto indiscriminado porque apareció una cuenta.
- Sesiones reales fuera de documentación/LLM/logs; worker obtiene material mínimo.
- `needs_reauth`, `unsupported`, `rate_limited`, `blocked`, `empty_verified`,
  `timeout`, `send_uncertain` separados. Fuente caída no significa sin ofertas.
- Parser determinístico, plantillas y caché antes de IA; ninguna reparación de
  adaptadores por LLM en cada operación de producción.
- Endpoint CDP en loopback; no exponer CDP o daemon a internet.
- Nada está desplegado. Autenticación inicial y renovaciones por MFA/CAPTCHA no
  se prometen automáticas. Costo AWS/gateway/modelo separado de licencia gratuita.

## Propuestas de decisiones

- A-D001: matriz de capacidades por backend y operación, no flag `all_networks`.
- A-D002: separar discovery de contact; no reemplazar un DM faltante por comentarios.
- A-D003: priorizar WhatsApp existente para conversación y lectura OpenCLI por red;
  Messenger/Instagram experimental; X DM, Threads DM y Reddit Chat pendientes.
- A-D004: usar referencias/leases y aislar sessions; elegir formato/cifrado después
  de B. No exportar material privado durante la investigación.
- A-D005: gateway persistente cuando se requiera; Lambda para coordinación/eventos.
- A-D006: evaluar MessagingPort común Beeper/Matrix frente a bridges separados;
  distinguir software open source, servicio comercial, costos y prueba por red.
- A-D007: Bluesky/Telegram como candidatos con auth nativa; Discord solo bot
  permitido. No forzar cookies sobre OAuth/MTProto.
- A-D008: estudiar fixtures/dedup/contratos de famabot, no imponer LLM por anuncio
  ni adoptarlo como scraper comercial autorizado.
- A-D009: bajo la restricción Lambda-only reportada por Claude, probar CDP efímero
  y whatsmeow intermitente; A-D005/A-D006 quedan alternativas fuera del MVP.
- A-D010: S3 CAS/FIFO no equivalen a fencing del envío; ledger y reconciliación
  obligatorios antes de afirmar protección contra duplicados. Ver A-F27.

Estas propuestas no han sido aprobadas como selección final ni implementadas.

## Coordinación con B

- A-Q001 (2026-10-03): compara portabilidad real: cookies JSON vs storageState vs
  perfil completo vs sesión WhatsApp. ¿Cuál conserva cada backend y cuál no?
- A-Q002 (2026-10-03): ¿cómo impedir uso simultáneo de cuenta/perfil por workers y
  revocar una sesión sin entregar cookies a todos los agentes?
- A-Q003 (2026-10-03): revisa cifrado/gestión de estados de agent-browser y si
  Browser Use requiere LLM para nuestro camino determinístico o solo para Agent.
- A-Q004 (2026-10-03): catálogo instalado muestra Threads, pero el árbol upstream
  inspeccionado no lo contiene. Identifica procedencia sin extraer secretos ni
  confundir cambios locales con soporte upstream distribuible.
- A-Q005 (2026-10-03): distingue headless de serverless: ¿qué gateway sobrevive al
  reinicio y qué setup/renewal humano todavía necesita cada opción?
- A-Q006 (2026-10-03): comparar Beeper SDK/CLI y EasyMatrix: costos, Desktop,
  claves Matrix, límites de start-chat y complejidad frente al WhatsApp existente.
- A-Q007 (2026-10-03): sesiones MTProto/OAuth son formatos propios. Verificar
  portabilidad sin cookies; fuente actual de Telethon está en Codeberg.

Informe B leído íntegramente el 2026-10-03; no se modificó su archivo. Respuestas
A-Q001 a A-Q005 incorporadas como evidencia documental, no validación real.
El diseño inicial con gateways se revisa según B-D001r/B-D006r: Lambda-only.
Matrix, EasyMatrix y Beeper Desktop necesitan servicios persistentes y quedan
fuera de ese MVP; no son descartes universales. A-Q006/A-Q007 siguen pendientes
para esas alternativas y para futuras redes, no bloquean el piloto con fixtures.

- Respuesta A a B-Q003/B-Q007: CDP usa las APIs anteriores; no se encontró cambio
  explícito de pestaña en los entrypoints de lectura revisados (A-F26). No hay
  base para afirmar que todos funcionan: faltan dependencias, runtime y pruebas
  de cuenta. Reddit requiere origen correcto; X requiere revisar helpers.
- Respuesta A a B-D009 y prueba B-12: If-Match evita escribir sobre estado nuevo,
  pero no garantiza que el perdedor no haya enviado previamente. Registrar esta
  diferencia y probar pérdida de lease en torno al efecto externo (A-F27).
- B-Q005/B-Q006 requieren decisión del usuario: dispositivo WhatsApp propio del
  radar y fallback local si AWS es rechazado. No clonar el dispositivo del bridge
  activo ni asumir permiso de fallback. Esta investigación no vincula cuentas.
- B-D010: autoría Threads reportada por B; revisar alcance de distribución y
  dependencias antes de importar código. No se hizo esa importación.

## Cierre del frente A

Actualización de coordinación posterior: entrega final B integrada íntegramente;
[decisiones y tareas](../testing/WORKPLAN.md) cierran la investigación. B registra
dispositivo WhatsApp nuevo por vinculación, sin clonar el bridge existente, y
preferencia Lambda-only. Esas decisiones de diseño no vinculan ninguna cuenta
ni autorizan pruebas reales en este encargo. B-Q009/B-Q012/B-Q013 tienen respuesta
en el workplan; alternativas fuera del MVP se mantienen como pendientes no
bloqueantes. [Resultados locales](../testing/LOCAL_RESULTS.md) prueban el fixture
CDP, no una red social. Se conserva el resto de este cierre como snapshot de la
investigación anterior.

Entregados: 12 filas de redes/candidatos, 27 hallazgos acumulados, revisiones,
fuentes, límites por operación y protocolo de pruebas. Sin pruebas de red/envío:
investigación no equivale a conectores probados ni autorización de contactos.

Prioridad revisada: fixtures/contratos → CDP efímero contra fixture local → lectura
de una red desde Lambda con cuenta de ensayo autorizada → whatsmeow intermitente
con dispositivo exclusivo autorizado → otras redes por capacidad validada.
Mantener Threads/Reddit como discovery antes de prometer DM; baseline sin LLM.

Pendientes: política X (lector 403), aprobación/costo comercial Reddit,
precio/condiciones Beeper, distribución Threads local y pruebas del diseño B. No se ocultan
fallos de lectura ni se sustituyen por afirmaciones positivas. Selección final
y pruebas reales todavía pendientes.

## Pruebas propuestas antes de habilitar una capacidad

1. Contratos/fixtures sintéticos: IDs, sesión correcta, expiración, límites y errores.
2. Export/import entre dos contextos de ensayo propios, solo con autorización.
3. Reinicio gateway/worker, renovación y revocación; nunca loggear estado secreto.
4. Dos workers/cuenta: lease, routing y ausencia de interferencia.
5. DM de ensayo a cuenta propia consentida: destinatario exacto, recepción y
   reconciliación tras timeout; no usar vendedores reales para probar.
6. Lotes SQS duplicados y fallos parciales: no repetir una consulta ya confirmada.
7. Medir lecturas útiles, consultas autorizadas, mensajes confirmados, respuestas,
   límites, costo, tokens y reconexiones. Registro de operaciones no demuestra
   disponibilidad ni precio confirmado sin respuesta asociada.
