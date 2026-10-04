# Informe B — Claude: sesiones portables y runtimes

Responsable: Claude (frente B). Consulta y redacción: 2026-10-03.
Estado: evidencia documental e inspección de código público y de instalaciones
locales (solo nombres de archivo, versiones y código; ningún dato de sesión).
No se ejecutó login, export de cookies, envío, lectura de inbox ni prueba end-to-end.
Archivo reservado: el coordinador no añade hallazgos aquí.

## Actualización 2026-10-03 — respuestas del usuario

El usuario respondió en chat (fuente: usuario, 2026-10-03):

- B-Q001: el adaptador Threads de `~/.opencli/clis/threads` lo escribió el usuario.
- B-Q002: los servicios corren en **AWS Lambda bajo demanda**, no en un host
  persistente.
- B-Q004: mautrix-meta solo si es gratis y fácil de probar e integrar.

Consecuencia: el resumen original (abajo, conservado) asumía gateways persistentes.
Con Lambda bajo demanda, la sesión ya no puede "quedarse en un gateway": debe
guardarse cifrada en almacenamiento AWS y cargarse en cada invocación, con un
único escritor por cuenta. Ver [Diseño Lambda bajo demanda](#diseño-lambda-bajo-demanda)
y decisiones revisadas B-D001r–B-D009. mautrix-meta queda **rechazado para el MVP**
(B-F016).

### Resumen revisado

1. **Navegador en Lambda:** Chromium (`@sparticuz/chromium`) + Playwright con
   `storageState` cifrado (incluyendo `indexedDB: true`). Los adaptadores OpenCLI
   (incluido Threads) se pueden reutilizar en modo CDP contra ese Chromium local
   de la invocación, sin extensión ni túnel (B-F014, `inf`, prueba pendiente).
2. **WhatsApp en Lambda:** whatsmeow en modo conectar → sincronizar offline →
   operar → desconectar, con su SQLite descargado de S3 y subido con escritura
   condicional. Dispositivo vinculado **nuevo y exclusivo** para el radar; no
   clonar el del bridge local (B-F015).
3. **Un escritor por cuenta:** SQS FIFO con `MessageGroupId = plataforma:cuenta`
   para serializar y escrituras condicionales S3 (`If-Match`) como fencing, sin
   DynamoDB (B-F017).
4. **Alta y renovación de sesiones siguen siendo humanas y locales:** login manual
   en un navegador de captura y QR/código de emparejamiento; Lambda nunca inicia
   sesión.
5. **Riesgo principal no resuelto:** sesiones creadas en la IP de casa y usadas
   desde IPs de AWS pueden disparar verificaciones o bloqueos (`inf`). No se
   mitiga con proxies residenciales (excluido); se mide la tasa de `needs_reauth`.
6. **Costo:** software 0; el free tier de Lambda cubre ~3 300 lecturas con
   Chromium de 2 GB × 60 s al mes (cálculo sobre precios documentados); S3 y KMS,
   centavos, pendiente cuantificar.

## Resumen original (antes de las respuestas)

1. **No existe un formato de sesión universal.** Perfil Chrome, `storageState`,
   cookies sueltas y dispositivo vinculado de WhatsApp son materiales distintos,
   con portabilidad, expiración y revocación distintas (ver A-Q001).
2. **La opción más segura es que la sesión no viaje.** Para lectura, OpenCLI sobre
   un perfil Chrome dedicado mantiene la sesión dentro de Chrome y solo expone un
   daemon en loopback; no hay export que filtrar. Para WhatsApp, el bridge
   whatsmeow ya instalado retiene el dispositivo vinculado en su SQLite local.
3. **Estado portable solo donde una prueba lo justifique.** Playwright
   `storageState` (con `indexedDB: true`) o agent-browser (AES-256-GCM integrado)
   sirven para workers efímeros, pero cada sitio debe probar que el estado
   importado mantiene la sesión; no se asume.
4. **Lambda convencional no aloja gateways.** WhatsApp, Matrix/mautrix y Chrome
   con extensión necesitan proceso persistente. Lambda coordina y, como mucho,
   ejecuta lecturas efímeras con estado cifrado cargado bajo lease.
5. **Ningún componente del camino determinístico requiere LLM.** OpenCLI,
   Playwright, agent-browser (salvo su comando `chat`), whatsmeow, WAHA y
   mautrix-meta funcionan sin modelo. Browser Use `Agent` sí requiere LLM; su
   CLI no, pero no aporta nada que falte en el camino recomendado.
6. **Descartados para este proyecto:** Browser Use Cloud y servicios similares
   como medio de stealth/CAPTCHA/proxies residenciales (regla del encargo);
   Browserless por licencia SSPL/comercial para distribuir.

## Matriz comparativa

Leyenda de estado: `doc` documentado, `insp` inspeccionado, `inf` inferencia,
`pend` pendiente. Nada en esta tabla está `probado_local` ni `probado_real`.

| Componente | Licencia / mantenimiento (2026-10-03) | Auth inicial | Material de sesión y reutilización | Export/import | Expiración / recuperación | Aislamiento / concurrencia | Despliegue | Costos |
|---|---|---|---|---|---|---|---|---|
| Agent Reach | MIT; push 2026-09-15 (`insp`) | Delega en backends; cookies Twitter/XHS pegadas por el usuario | `~/.agent-reach/config.yaml` permisos 600, texto plano (`doc`) | Manual por el usuario (Cookie-Editor) | Del backend | Router, no gestiona leases | Local | Software 0 |
| OpenCLI | Apache-2.0; release v1.8.8 2026-08-30, local 1.8.6 (`insp`) | Usuario inicia sesión en Chrome | Sesión queda en el perfil Chrome; daemon 127.0.0.1:19825 (`insp`) | No existe export: por diseño | Exit 77 `auth required` → `needs_reauth`; re-login humano | Perfiles por alias; leases de pestaña propios; no hay lease por cuenta (`doc`) | Host con Chrome+extensión, o `OPENCLI_CDP_ENDPOINT` | Software 0; host persistente |
| Playwright `storageState` | Apache-2.0; v1.63.0 2026-09-04 (`insp`) | Script de login o navegador manual | JSON: cookies, localStorage, IndexedDB opcional, OPFS, WebAuthn virtual; sin sessionStorage (`doc`) | Sí, JSON portable | Sin renovación automática; borrar al expirar (`doc`) | Un contexto por worker; escritura concurrente del archivo no coordinada | Local, contenedor o Lambda con Chromium | Software 0 |
| Playwright perfil persistente | Igual | Login en el perfil | `userDataDir` completo (`doc`) | Copiar directorio; cifrado ligado a directorio/usuario (`inf`, ver B-F005) | Igual que Chrome | Un solo proceso por `userDataDir` (`doc`) | Disco persistente | Software 0 |
| agent-browser | Apache-2.0; v0.38.2 2026-10-01 (`insp`) | Login manual, `--auto-connect`+`state save`, o vault | `--session` aislado; `--restore` guarda cookies+localStorage; `--profile <path>` incluye IndexedDB (`doc`) | `state save/load`, cifrado AES-256-GCM con `AGENT_BROWSER_ENCRYPTION_KEY` (`doc`) | Estados se borran tras 30 días por defecto; `--restore-check-text` detecta sesión perdida (`doc`) | Sesiones con nombre; `--pin-tab` por sesión sobre un CDP compartido (`doc`) | Local; ejemplo Lambda con `@sparticuz/chromium` (`doc`) | Software 0; `chat` usa AI Gateway de pago |
| Browser Use (lib/CLI) | MIT; 0.13.10 2026-09-04 (`insp`) | Perfil real o `storage_state` | `storage_state` cookies+localStorage, guardado periódico con merge (`doc`) | JSON | `export_storage_state`; sin renovación | `from_system_chrome` exige cerrar Chrome (`doc`) | Local o CDP remoto | Lib 0; `Agent` necesita LLM; Cloud $0.02/hora-navegador (`doc`) |
| whatsapp-mcp upstream | MIT; último push 2025-07-13; whatsmeow fijado a 2025-03 (`insp`) | QR | `store/whatsapp.db` (claves del dispositivo) + `messages.db` | Copiar SQLite = clonar dispositivo (`inf`) | Reautenticar ~20 días según README (`doc`) | Un proceso por dispositivo (`inf`) | Proceso persistente; REST en `:8080` todas las interfaces, sin auth (`insp`) | Software 0 |
| Fork local whatsapp-mcp | Sin licencia nueva; whatsmeow 2026-09-04 (`insp`) | QR ya hecho | Igual que upstream | No exportar | `wa.py status`; lectura sigue con datos viejos si bridge cae (`doc` skill local) | Igual | REST en `127.0.0.1` (`insp`) | Software 0; PC encendido |
| WAHA | Apache-2.0; 2026.9.2 2026-10-02 (`insp`) | QR o código por API | Session storage local o PostgreSQL; media local/PostgreSQL/S3 (`doc`) | Por base de datos | Reconexión del motor; QR si se desvincula | Multisesión en un servidor; API key `X-Api-Key` (`doc`) | Contenedor persistente | Imagen gratis desde 2026.6.1; hosting aparte (`doc`) |
| mautrix-meta | AGPL-3.0; v0.2609.0 2026-09-16 (`insp`) | Pegar cookies (`datr,c_user,sb,xs` / `sessionid,csrftoken,mid,ig_did,ds_user_id`) o usuario/contraseña en modos lite/android (`doc`) | Estado en PostgreSQL del bridge | No aplica | Meta puede bloquear, pedir CAPTCHA o reset (`doc`) | Por login de usuario Matrix | Homeserver Matrix + appservice + PostgreSQL ≥16 + Go 1.25 (`doc`) | Software 0; stack Matrix persistente |
| Steel browser (alt.) | Apache-2.0; push 2026-09-28 (`insp`) | Igual que Playwright/Puppeteer | API `/sessions` mantiene cookies/localStorage (`doc`) | Por API | `pend` | Pool de sesiones | Servicio self-host | Software 0 |
| Browserless | SSPL-1.0 o comercial (`insp`) | — | — | — | — | — | — | `descartado` para copiar/distribuir |

## Fuentes y hallazgos

Todas las URLs consultadas el 2026-10-03.

### B-F001 — OpenCLI: la sesión no sale de Chrome (`insp`/`doc`)

Daemon HTTP+WebSocket escucha en `127.0.0.1:19825`; rechaza `Origin` que no sea
`chrome-extension://` y exige cabecera `X-OpenCLI`. Esa cabecera no es secreto:
protege contra CSRF desde páginas web, no contra otro proceso del mismo usuario.
Frontera de confianza = usuario del sistema operativo. Cada perfil Chrome corre su
propia instancia de extensión; `opencli profile list/rename/use` y
`OPENCLI_PROFILE` eligen perfil. `opencli browser <session>` mantiene lease de
pestaña; adaptadores usan ventana de fondo y `siteSession` efímera o persistente.
Exit codes: `66` vacío, `69` bridge caído, `75` timeout, `77` auth requerida.
Mapeo directo a `empty_verified`/`unsupported`/`timeout`/`needs_reauth` de A.

Fuentes: [README](https://github.com/jackwener/OpenCLI/blob/main/README.md),
[daemon.ts](https://github.com/jackwener/OpenCLI/blob/main/src/daemon.ts),
[exit codes](https://github.com/jackwener/OpenCLI/blob/main/docs/guide/exit-codes.md).

### B-F002 — Agent Reach guarda cookies en texto plano local (`doc`)

`~/.agent-reach/config.yaml` con permisos 600; no las inyecta en OpenCLI. Recomienda
cuentas dedicadas por riesgo de bloqueo y de fuga. Para nuestro broker: tratar ese
archivo como secreto existente, no como bóveda.

Fuente: [README Agent Reach](https://github.com/Panniantong/Agent-Reach).

### B-F003 — Procedencia del adaptador Threads (responde A-Q004) (`insp`)

El adaptador vive en `~/.opencli/clis/threads/` (`feed`, `post`, `publish`,
`reply`, `search` + `_shared/`), dentro de un runtime de usuario
(`package.json` `opencli-user-runtime`). Archivos fechados 2026-08-19, sin cabecera
de licencia ni autor; el ejemplo usa una consulta en español. El árbol `main` de
OpenCLI upstream no contiene `clis/threads` al 2026-10-03. Conclusión: código
local, probablemente creado por el usuario o una sesión anterior de agente; no es
soporte upstream. `search` declara `access: 'read'`, `Strategy.UI`, navegador
obligatorio. `pend`: confirmar autoría con el usuario antes de copiarlo al repo.

Fuente: [árbol upstream](https://github.com/jackwener/OpenCLI/tree/main/clis);
inspección local sin ejecutar comandos.

### B-F004 — Playwright `storageState`: qué contiene y qué no (`doc`)

Contiene cookies, localStorage, snapshot IndexedDB (opción `indexedDB: true` desde
v1.51), OPFS y credenciales WebAuthn virtuales. No persiste sessionStorage; la
documentación da un snippet manual. El archivo permite suplantación y no debe
versionarse; hay que borrarlo cuando expira. `inf`: servidores que atan tokens a
IP, huella de dispositivo o cookies httpOnly de vida corta pueden rechazar un
estado importado en otra máquina.

Fuentes: [Authentication](https://playwright.dev/docs/auth),
[BrowserContext.storageState](https://playwright.dev/docs/api/class-browsercontext#browser-context-storage-state).

### B-F005 — Perfil persistente: bloqueo y cifrado ligado al directorio (`doc`/`inf`)

Los navegadores no permiten dos instancias con el mismo `userDataDir`. Desde
Chrome 136, `--remote-debugging-port` no funciona sobre el directorio por defecto;
hay que usar uno no estándar, que "usa una clave de cifrado diferente". `inf`: en
Windows las cookies del perfil se cifran con material ligado al usuario/sistema;
copiar el directorio a otro host o a Linux no garantiza cookies descifrables.
`pend`: probar con un perfil de ensayo propio.

Fuentes: [BrowserType.launchPersistentContext](https://github.com/microsoft/playwright/blob/main/docs/src/api/class-browsertype.md),
[Chrome remote debugging changes](https://developer.chrome.com/blog/remote-debugging-port).

### B-F006 — agent-browser: estado cifrado y modo sin LLM (responde A-Q003) (`doc`)

Seis modos de persistencia: perfil Chrome por nombre (copia temporal de solo
lectura; en Windows exige cerrar Chrome), perfil por ruta (cookies, localStorage,
IndexedDB, service workers, caché), `--session --restore` (cookies+localStorage),
`--auto-connect`+`state save`, `--state <path>` y vault de credenciales.
Con `AGENT_BROWSER_ENCRYPTION_KEY` (hex de 64 caracteres) los estados se cifran con
AES-256-GCM; `AGENT_BROWSER_STATE_EXPIRE_DAYS` borra estados tras 30 días por
defecto. El vault siempre cifra; si no hay clave, genera
`~/.agent-browser/.encryption-key` junto a los datos: protege contra fuga por Git,
no contra compromiso del mismo usuario. `--allowed-domains` es incompatible con
replay de estado y perfiles. LLM solo para `chat` (`AI_GATEWAY_API_KEY`); el resto
de comandos son determinísticos. Ejemplo Lambda documentado con
`@sparticuz/chromium`.

Fuente: [README agent-browser](https://github.com/vercel-labs/agent-browser).

### B-F007 — Browser Use: separar biblioteca, CLI, modelo y nube (responde A-Q003) (`doc`)

Biblioteca MIT gratuita; `Agent` exige un LLM (proveedor de pago, gateway BU2 o
Ollama local). La CLI (`browser-use`, sobre Browser Harness MIT) ejecuta Python
directo (`new_tab`, `page_info`) sin LLM propio; por defecto se adjunta al Chrome
del usuario por CDP con aprobación manual. `storage_state` guarda cookies y
localStorage periódicamente con merge: dos workers con el mismo archivo se pisan.
Cloud: $0.02 por hora-navegador, stealth, CAPTCHA y proxies residenciales; su
"profile sync" solo transfiere cookies. `descartado` para nuestro camino: no aporta
capacidad ausente y su nube es justo lo que el encargo excluye.

Fuentes: [README](https://github.com/browser-use/browser-use),
[CLI](https://docs.browser-use.com/open-source/browser-use-cli.md),
[Authentication](https://docs.browser-use.com/open-source/customize/browser/authentication.md),
[parámetros](https://docs.browser-use.com/open-source/customize/browser/all-parameters).

### B-F008 — whatsapp-mcp upstream está parado y expone REST sin auth (`insp`)

Último push 2025-07-13, 252 issues abiertos; `go.mod` fija whatsmeow de 2025-03.
`main.go` escucha `fmt.Sprintf(":%d", port)` (todas las interfaces) con `/api/send`
y `/api/download` sin autenticación. La sesión es `store/whatsapp.db` (sqlstore de
whatsmeow); el historial, `messages.db`. README: QR inicial y posible re-auth tras
~20 días; para desincronización, borrar ambas bases y re-vincular.

El checkout local `~/Documents/whatsapp-mcp` tiene modificaciones sin commit:
whatsmeow actualizado a 2026-09-04, REST ligado a `127.0.0.1`, `wa.py status` y
traducción `@lid`. La skill local advierte que las lecturas responden con datos
viejos si el bridge está apagado. `pend`: no se ejecutó `status` en esta corrida.

Fuentes: [whatsapp-mcp](https://github.com/lharries/whatsapp-mcp),
[main.go upstream](https://github.com/lharries/whatsapp-mcp/blob/main/whatsapp-bridge/main.go).

### B-F009 — whatsmeow y la regla de dispositivos vinculados (`doc`/`inf`)

whatsmeow es MPL-2.0 (copyleft por archivo: usable como dependencia desde un
proyecto Apache-2.0; cambios en sus archivos se publican bajo MPL). `sqlstore`
soporta SQLite y PostgreSQL. WhatsApp permite hasta cuatro dispositivos vinculados
y los desconecta si el teléfono principal no se usa en más de 14 días. `inf`: el
SQLite contiene las claves del dispositivo; copiarlo a dos procesos produce dos
clientes con la misma identidad y conflictos de sesión. Nunca compartir: un
gateway por número.

Fuentes: [whatsmeow](https://github.com/tulir/whatsmeow),
[sqlstore](https://pkg.go.dev/go.mau.fi/whatsmeow/store/sqlstore),
[WhatsApp linked devices](https://faq.whatsapp.com/378279804439436).

### B-F010 — WAHA: multisesión HTTP con almacenamiento externo (`doc`/`insp`)

Motores WEBJS/WPP (Chromium vía Puppeteer), NOWEB (WebSocket, Node) y GOWS
(WebSocket, Go). Session storage local o PostgreSQL (MongoDB obsoleto); media
local, PostgreSQL o S3. API protegida con `WAHA_API_KEY` (`X-Api-Key`); también
acepta query parameter, que conviene no usar porque acaba en logs. Desde 2026.6.1
las funciones Plus están en la imagen gratuita. El repo `devlikeapro/gows` está
archivado (2026-06-27) y GitHub no detecta licencia: procedencia del motor GOWS
`pend`. La documentación justifica WEBJS por "evitar bloqueos"; no se adopta como
objetivo.

Fuentes: [engines](https://waha.devlike.pro/docs/how-to/engines/),
[storages](https://waha.devlike.pro/docs/how-to/storages/),
[security](https://waha.devlike.pro/docs/how-to/security/),
[anuncio](https://waha.devlike.pro/blog/waha-community),
[gows](https://github.com/devlikeapro/gows).

### B-F011 — mautrix-meta: aquí las cookies sí son la sesión (`doc`)

El login consiste en pegar cookies concretas (o "Copy as cURL") en el bot del
bridge; también hay modos usuario/contraseña. Es el único backend estudiado donde
la documentación afirma que cookies solas establecen sesión. `mautrix-manager`
(AGPL) automatiza la extracción de cookies; choca con la regla de no extraer
cookies salvo acción manual del usuario. Requiere homeserver Matrix con appservice,
PostgreSQL ≥16 y Go 1.25. `inf`: hablar con el bridge por la red desde código
Apache no copia código AGPL, pero modificar y operar el bridge activa sus
obligaciones; revisión legal `pend` antes de distribuir.

Fuentes: [autenticación](https://docs.mau.fi/bridges/go/meta/authentication.html),
[setup](https://docs.mau.fi/bridges/go/setup.html?bridge=meta),
[repo](https://github.com/mautrix/meta).

### B-F012 — Lambda: sin estado entre invocaciones (`doc`)

Timeout 900 s (90 min en Managed Instances para invocaciones asíncronas), memoria
hasta 10 240 MB, paquete zip 250 MB descomprimido, imagen de contenedor 10 GB. AWS
lo describe como cómputo efímero que no conserva estado entre invocaciones. `inf`:
WebSocket de WhatsApp, appservice Matrix y Chrome con extensión no caben en ese
modelo; una lectura Chromium efímera sí cabe (`@sparticuz/chromium`, MIT).

Fuentes: [cuotas Lambda](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html),
[Sparticuz/chromium](https://github.com/Sparticuz/chromium).

### B-F013 — Alternativas adicionales

- Steel browser (Apache-2.0): servicio self-host de sesiones de navegador con API;
  candidato solo si hace falta un pool remoto de navegadores. `doc`.
- Browserless: SSPL-1.0 o comercial. `descartado` para distribuir.
- Gestores de perfiles anti-detect, proxies residenciales y resolución de CAPTCHA:
  excluidos por regla, no evaluados.

Fuentes: [Steel](https://github.com/steel-dev/steel-browser),
[Browserless LICENSE](https://github.com/browserless/browserless/blob/main/LICENSE).

### B-F014 — OpenCLI en modo CDP: sin extensión, con límites (`doc`/`insp`)

`OPENCLI_CDP_ENDPOINT` conecta OpenCLI directamente a un Chrome con
`--remote-debugging-port` y `--user-data-dir` no estándar; la guía lo presenta
para servidores headless y ejecuta adaptadores normales (`opencli bilibili hot`).
En `src/browser/cdp.ts` el backend CDP soporta `evaluate` y `Network.getCookies`,
pero `tabs()` devuelve lista vacía y `selectTab` no hace nada: adaptadores que
abren o cambian pestañas pueden fallar. La guía sugiere exponer CDP con ngrok;
**rechazado**: CDP da control total del navegador. `inf`: dentro de una
invocación Lambda, Chromium escucha en `127.0.0.1` y OpenCLI corre en el mismo
entorno; no hace falta túnel. Prueba pendiente por adaptador (Threads primero).

Fuentes: [CDP guide](https://github.com/jackwener/OpenCLI/blob/main/docs/advanced/cdp.md),
[remote Chrome](https://github.com/jackwener/OpenCLI/blob/main/docs/advanced/remote-chrome.md),
[cdp.ts](https://github.com/jackwener/OpenCLI/blob/main/src/browser/cdp.ts).

### B-F015 — whatsmeow bajo demanda: viable con condiciones (`doc`/`inf`)

`doc`: whatsmeow emite `OfflineSyncPreview`/`OfflineSyncCompleted` al reconectar,
y `LoggedOut`/`StreamReplaced` cuando la sesión se pierde o otro cliente toma el
mismo dispositivo. WhatsApp guarda mensajes no entregados cifrados hasta 30 días.
Lambda ofrece `/tmp` de 512 MB a 10 240 MB por entorno.

`inf`: una invocación puede descargar el SQLite de sesión, conectar, esperar
`OfflineSyncCompleted`, recoger respuestas, enviar lo autorizado, desconectar y
subir el SQLite con `If-Match`. Las respuestas de vendedores llegan en la próxima
invocación programada, no en tiempo real. `StreamReplaced` indica dos clientes
con la misma identidad: copiar la base del bridge local a Lambda mientras el
bridge sigue activo lo provocaría. Por eso: dispositivo vinculado propio del
radar (ocupa 1 de 4). Pendiente: cuánto tolera WhatsApp un dispositivo vinculado
que se conecta solo a ratos, y el patrón de conexiones cortas desde IPs de AWS.

Fuentes: [events](https://pkg.go.dev/go.mau.fi/whatsmeow/types/events),
[privacidad WhatsApp](https://www.whatsapp.com/legal/privacy-policy),
[Lambda /tmp](https://docs.aws.amazon.com/lambda/latest/dg/configuration-ephemeral-storage.html).

### B-F016 — mautrix-meta no cumple "gratis y fácil" con Lambda (`doc`/`inf`)

Software gratis, pero exige un homeserver Matrix que registre el appservice y le
envíe eventos por HTTP, más el proceso del bridge, ambos persistentes. El bridge
admite SQLite para instancias pequeñas, pero eso no elimina el homeserver. Usar
directamente su librería `pkg/messagix` sin Matrix implica escribir código Go
enlazado con AGPL-3.0 (`inf`). Ninguna de las dos opciones cabe en Lambda bajo
demanda ni es fácil de probar. **Rechazado para el MVP**; DM de
Messenger/Instagram queda `unsupported` hasta que aparezca otra ruta.

Fuentes: [setup](https://docs.mau.fi/bridges/go/setup.html?bridge=meta),
[initial config](https://docs.mau.fi/bridges/general/initial-config.html),
[árbol mautrix/meta](https://github.com/mautrix/meta/tree/main/pkg).

### B-F017 — Piezas AWS para guardar sesiones y serializar (`doc`)

- SQS FIFO + Lambda: mensajes del mismo `MessageGroupId` llegan en orden; si la
  función falla, Lambda agota reintentos del grupo antes de recibir más mensajes de
  ese grupo; la concurrencia queda limitada por el número de grupos.
- S3 conditional writes: `If-None-Match` impide crear si la clave existe;
  `If-Match` impide sobrescribir si el ETag cambió. Sirve de lease y de
  compare-and-swap del estado de sesión.
- SSM Parameter Store: parámetros estándar sin cargo, máximo 4 KB (avanzados 8 KB,
  $0.05/mes). Cabe una clave de cifrado, no un `storageState` ni un SQLite.
- Secrets Manager: $0.40 por secreto al mes; innecesario si la clave va en SSM.
- Lambda: free tier de 1 M solicitudes y 400 000 GB-s al mes; $0.0000166667 por
  GB-s después. 2 GB × 60 s = 120 GB-s → ~3 333 lecturas/mes gratis (`inf`).
- Pendientes: costo de S3 (almacenamiento y requests) y de llamadas KMS para
  SecureString; Lambda con `/tmp` grande y memoria alta puede salir del free tier.

Fuentes: [SQS scaling](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-scaling.html),
[S3 conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html),
[SSM pricing](https://aws.amazon.com/systems-manager/pricing/),
[SSM tiers](https://docs.aws.amazon.com/systems-manager/latest/userguide/parameter-store-advanced-parameters.html),
[Secrets Manager pricing](https://aws.amazon.com/secrets-manager/pricing/),
[Lambda pricing](https://aws.amazon.com/lambda/pricing/).

## Diseño Lambda bajo demanda

Propuesta, no código existente. Sustituye la sección siguiente cuando hay
conflicto.

```text
PC del usuario (manual, ocasional)          AWS (bajo demanda)
──────────────────────────────────          ──────────────────────────────
capture: navegador headed dedicado   ─┐     EventBridge/CLI → SQS FIFO
  usuario inicia sesión a mano        │       group = plataforma:cuenta
  → storageState (+indexedDB)         │              ↓
pair: QR/código WhatsApp en un        ├→ S3  Lambda worker
  dispositivo nuevo del radar         │   sessions/<plat>/<cuenta>.enc
  → SQLite whatsmeow                  │   leases/<plat>/<cuenta>
cifrado local con clave de SSM       ─┘    1. lease: PUT If-None-Match
                                           2. GET estado → descifrar en /tmp
                                           3. operar (Chromium/OpenCLI-CDP
                                              o whatsmeow)
                                           4. PUT estado If-Match (ETag)
                                           5. borrar /tmp y liberar lease
```

- **Alta y renovación:** comando local futuro (`capture`/`pair`) que abre un
  navegador dedicado (no el perfil diario), el usuario inicia sesión a mano y se
  guarda el estado del propio navegador de automatización. No se leen archivos de
  cookies de Chrome. Para WhatsApp, emparejar un dispositivo nuevo.
- **Lease:** objeto `leases/...` creado con `If-None-Match` y fecha de expiración
  mayor que el timeout de la función. Un lease vencido se toma con `If-Match`
  sobre su ETag. SQS FIFO ya serializa; el lease cubre reintentos, timeouts y
  ejecuciones manuales fuera de la cola.
- **Write-back:** solo si el estado cambió y con `If-Match` del ETag leído. Si
  falla (otro escritor), descartar la copia local y marcar `state_conflict`.
- **Cifrado:** clave en SSM SecureString estándar; el blob en S3 cifrado en el
  cliente (AES-256-GCM) más cifrado del servidor S3. IAM: solo la función worker
  lee `sessions/` y la clave; el coordinador ve `SessionRef`, nunca el blob.
- **Errores → estados de A:** OpenCLI exit 77 o página de login →
  `needs_reauth`; `LoggedOut` → `needs_reauth`; `StreamReplaced` →
  `session_conflict` y parar; timeout de Lambda tras enviar → `send_uncertain`,
  sin reintento automático (A-F16).
- **Higiene /tmp:** Lambda reutiliza entornos entre invocaciones; borrar el estado
  descifrado al terminar, también en error.

## Piloto WhatsApp y mitigación del riesgo de origen

Instrucción del usuario (2026-10-03): mitigar el riesgo de sesiones usadas desde
AWS en todas las redes posibles; empezar con WhatsApp como piloto, con alta
"solo escaneando un QR", y escalar después.

### B-F018 — Términos de WhatsApp: la automatización de mensajes es el riesgo mayor (`doc`)

Los términos prohíben comunicaciones como "bulk messaging, auto-messaging,
auto-dialing", el uso no personal salvo autorización de WhatsApp y el acceso por
medios automatizados no autorizados. Un cliente no oficial (whatsmeow, WAHA) que
escribe a vendedores para un radar comercial entra en esas cláusulas, sea cual
sea la IP. Consecuencia `inf`: el riesgo real es el **bloqueo del número**. Si el
dispositivo vinculado cuelga del número personal, se arriesga el WhatsApp
personal. La ruta conforme para uso comercial es WhatsApp Business Platform, con
plantillas de pago para iniciar conversación (A-F07).

Fuente: [WhatsApp Terms of Service](https://www.whatsapp.com/legal/terms-of-service).

### B-F019 — Alta por QR en whatsmeow (`doc`)

`GetQRChannel` debe llamarse antes de conectar y con un store sin ID de
dispositivo. Genera un QR nuevo cuando expira el anterior y emite `timeout` si no
se escanea. `PairPhone` es la alternativa: un código para escribir en el
teléfono, sin cámara. `inf`: el emparejamiento cabe en una sola invocación
Lambda (<15 min). La función publica el texto del QR vigente en un objeto S3 de
vida corta. Un comando local lo lee y lo dibuja en la terminal, el usuario
escanea y la función guarda la sesión cifrada. No escribir el QR en logs:
permite vincular la cuenta mientras está vigente.

Fuente: [whatsmeow](https://pkg.go.dev/go.mau.fi/whatsmeow).

### B-F020 — IP fija en Lambda no es gratis (`doc`/`inf`)

Para salir a internet con IP fija, Lambda necesita VPC con subred privada y NAT
gateway. AWS cobra el NAT por hora (ejemplo de la página: $0.045/h, ~$32/mes)
más el tráfico procesado. `inf`: sin VPC, la IP de salida es de AWS y puede
cambiar entre invocaciones. Una IP fija contradice el objetivo de costo cero; se
deja como opción medible, no como default.

Fuentes: [Lambda VPC internet](https://docs.aws.amazon.com/lambda/latest/dg/configuration-vpc-internet.html),
[VPC pricing](https://aws.amazon.com/vpc/pricing/).

### Qué significa "mitigar" en este proyecto

Se mitiga operando de forma transparente y con poco volumen, no ocultando la
automatización. Quedan **fuera** proxies residenciales, falsificación de huella,
navegadores anti-detect, resolución de CAPTCHA y rotación de cuentas para eludir
bloqueos (encargo, AGENTS.md y SECURITY.md).

| Medida legítima | Aplica a | Costo | Estado |
|---|---|---|---|
| Coherencia de origen: emparejar o iniciar sesión desde el mismo entorno que opera (QR en Lambda) | WhatsApp sí; redes con login web solo si el usuario puede iniciar sesión dentro de AWS (difícil sin navegador visible) | 0 | `inf`, prueba 13 |
| Cuenta/número dedicado al radar, nunca el personal | Todas | SIM o cuenta nueva | Recomendado |
| API oficial donde exista (Threads keyword search, WhatsApp Business Platform, Reddit OAuth) | Threads, WhatsApp, Reddit | Threads/Reddit según aprobación; WhatsApp de pago al iniciar | `doc` A-F07/A-F11/A-F13 |
| Leer y recibir antes que enviar; cada envío aprobado por el usuario | Todas | 0 | Requerido por AGENTS.md |
| Presupuesto bajo por cuenta/día y lista de exclusión; solo vendedores que publicaron contacto para la venta | Todas | 0 | Propuesto |
| Chequeo de salud programado: detectar `needs_reauth` antes de una operación real | Todas | Free tier | Propuesto |
| Respaldo local en el PC por red si AWS dispara verificaciones | Redes web | 0 | Depende de B-Q006 |
| IP fija vía NAT | Todas | ~$32/mes | Solo si las mediciones lo justifican |

### Plan del piloto WhatsApp

Nada implementado. Cada fase necesita encargo explícito; P3 necesita además
autorización separada para enviar mensajes.

| Fase | Qué hace | Criterio de salida |
|---|---|---|
| P0 local | Fixtures sintéticos: `SessionRef`, lease S3 (412), CAS de estado, mapeo `LoggedOut`/`StreamReplaced`/timeout a estados | Pruebas verdes sin red |
| P1 alta | Lambda `pair` + comando local que dibuja el QR; número dedicado; SQLite cifrado en S3 | Sesión guardada; ningún QR ni clave en logs |
| P2 solo recepción | EventBridge cada N min: conectar, `OfflineSyncCompleted`, guardar mensajes de chats en lista blanca, desconectar | 14 días sin `needs_reauth` ni `session_conflict`; costo medido |
| P3 envío aprobado | Borradores generados; el usuario aprueba cada uno; presupuesto diario; exclusiones; `send_uncertain` sin reenvío | Mensajes a cuentas propias de ensayo primero; luego vendedores con contacto publicado |
| P4 escalar | Repetir P0–P2 por red: Threads (API oficial o adaptador propio), luego otras | Matriz A + tasa de reautenticación por red |

"Automático" en el piloto significa recepción, sincronización y redacción
automáticas. El envío sigue aprobado por el usuario: lo exige AGENTS.md y reduce
el riesgo de la cláusula de auto-messaging, aunque no lo elimina.

## Servicio multiusuario: alta por Telegram

Instrucción del usuario (2026-10-03): el piloto usa una cuenta WhatsApp vinculada
por escaneo. Después, el sistema envía el QR a otra persona (por ejemplo, por
Telegram) para que lo escanee y obtenga el servicio. Investigar lo más eficiente
y rápido de implementar.

### B-F021 — Código de emparejamiento mejor que QR para entrega remota (`doc`/`inf`)

`doc`: WhatsApp permite vincular un dispositivo con el número principal y un
código de ocho caracteres que se escribe en el teléfono. `PairPhone` de whatsmeow
lo genera; hay que conectar primero y pedirlo enseguida, porque el websocket de
login se cierra al agotarse los QR: unos 160 s en total. El nombre del cliente debe
tener formato `Browser (OS)` válido o el servidor responde 400.

`inf`: quien recibe el QR por Telegram en el mismo teléfono donde tiene WhatsApp
no puede escanearlo, porque necesita otra pantalla. El código funciona en un solo
teléfono y además queda ligado al número indicado: solo el dueño de ese WhatsApp
puede completarlo. El QR queda como opción secundaria, con imagen rotada por
`editMessageMedia` y más complejidad.

Fuentes: [link with phone number](https://faq.whatsapp.com/1324084875126592),
[whatsmeow PairPhone](https://pkg.go.dev/go.mau.fi/whatsmeow#Client.PairPhone).

### B-F022 — Telegram Bot API cubre alta e identidad sin costo (`doc`)

- `setWebhook` con `secret_token`: cada update llega con la cabecera
  `X-Telegram-Bot-Api-Secret-Token`; la Lambda rechaza el resto.
- `KeyboardButton.request_contact`: el usuario comparte su número de Telegram con
  un toque (solo chats privados). Sirve para proponer el número a vincular; se
  compara con el JID que devuelve WhatsApp tras el emparejamiento.
- `protect_content`: impide reenviar y guardar el mensaje con el código.
- Límites: ~1 mensaje/s por chat y ~30/s en difusión; suficiente para un piloto.
- Lambda Function URL admite `AuthType NONE`, con el webhook validado por la
  cabecera secreta.

Fuentes: [Bot API](https://core.telegram.org/bots/api),
[Bot FAQ](https://core.telegram.org/bots/faq),
[Function URLs](https://docs.aws.amazon.com/lambda/latest/dg/urls-configuration.html).

### B-F023 — Servidores listos frente a librería directa (`insp`/`doc`)

| Opción | Licencia / actividad | Multiusuario | Código de emparejamiento | Cabe en Lambda | Nota |
|---|---|---|---|---|---|
| whatsmeow directo (Go) | MPL-2.0; push 2026-09-29 | Un SQLite por usuario | `PairPhone` | Sí | Ya usado en el bridge local del usuario |
| go-whatsapp-web-multidevice (GOWA) | MIT; push 2026-10-03 | `/devices`, webhooks por dispositivo | `/app/login-with-code` | No, servidor persistente | Sobre whatsmeow |
| wuzapi | MIT; push 2026-10-02 | Usuarios con token, admin token, webhooks HMAC | QR documentado; código no verificado | No | Advierte riesgo de bloqueo por ToS |
| WAHA | Apache-2.0 | Multisesión | Ver docs WAHA | No | GOWS archivado (B-F010) |
| Baileys (TypeScript) | MIT; v7.0.0-rc14 | Librería | Sí (`requestPairingCode`, `pend` verificar) | Sí | Versión estable 7 aún en RC |
| Evolution API | Apache-2.0 + condiciones extra (aviso obligatorio, licencia comercial) | Sí | Sí | No | `descartado` por condiciones de licencia |

`inf`: un servidor listo da el primer emparejamiento en horas, pero necesita un
host 24/7, que contradice Lambda bajo demanda. La superficie que necesita el radar
es pequeña: emparejar, sincronizar y enviar. Escribirla sobre whatsmeow en una
Lambda Go es más trabajo inicial (días, estimación no medida) y luego no tiene
host que mantener.

Fuentes: [GOWA](https://github.com/aldinokemal/go-whatsapp-web-multidevice),
[wuzapi](https://github.com/asternic/wuzapi),
[Baileys](https://github.com/WhiskeySockets/Baileys),
[Evolution LICENSE](https://github.com/evolution-foundation/evolution-api/blob/main/LICENSE).

### B-F024 — Multiusuario agrava ToS y privacidad (`doc`/`inf`)

Los términos de WhatsApp prohíben además ofrecer el servicio a través de una red
donde varios dispositivos lo usen a la vez sin herramientas autorizadas, y crear
software o APIs que funcionen sustancialmente como WhatsApp para ofrecerlos a
terceros sin autorización. `inf`: vincular el WhatsApp de otras personas a un
servicio propio expone al operador y a cada número vinculado. Cada vinculación
entrega al servicio las claves del dispositivo de esa persona y, salvo que se
descarte, su historial sincronizado. Control disponible (`doc`): `Logout`
desvincula el dispositivo en el servidor de WhatsApp, a diferencia de borrar un
archivo. SECURITY.md exige autenticación, autorización, aislamiento y pruebas de
acceso cruzado antes de multiusuario o cloud.

Fuentes: [WhatsApp Terms](https://www.whatsapp.com/legal/terms-of-service),
[SECURITY.md](../../SECURITY.md).

### B-F025 — Lambda Go: runtime y SQLite (`doc`/`inf`)

`go1.x` está obsoleto; Go se despliega en `provided.al2023` (OS-only), con arm64
disponible. `go-sqlite3` necesita CGO (README whatsapp-mcp). `inf`: compilar con
CGO para Amazon Linux o usar un driver SQLite en Go puro; verificar compatibilidad
con `sqlstore`.

Fuentes: [Lambda Go](https://docs.aws.amazon.com/lambda/latest/dg/lambda-golang.html),
[whatsapp-mcp](https://github.com/lharries/whatsapp-mcp).

### Arquitectura mínima recomendada

```text
Telegram usuario ──/start──► Lambda tg-webhook (Function URL, valida secret_token)
   ▲  consentimiento + botón request_contact → número
   │                         │ encola pair(user) en SQS FIFO, group = user
   │                         ▼
   │ código 8 caracteres ◄── Lambda wa-worker (Go, whatsmeow, provided.al2023)
   │ (protect_content)        pair: Connect → PairPhone → espera PairSuccess ≤160 s
   │                                → verifica JID == número → sube SQLite cifrado
   │                          sync: EventBridge cada N min → lease → descargar →
   │                                OfflineSyncCompleted → guardar chats permitidos → CAS
   │                          send: solo borradores aprobados → send_uncertain sin reintento
   └── /stop ──────────────►  logout: Logout() en WhatsApp + borrar S3 + confirmar
S3: sessions/<user>.db.enc, leases/<user>   SSM: clave, token bot, secret webhook
```

- Dos funciones, una cola FIFO, un bucket y tres parámetros SSM. Sin servidor
  persistente ni base de datos administrada.
- Telegram por HTTPS directo (`sendMessage`, `setWebhook`); no hace falta SDK.
- Ignorar `HistorySync` salvo los chats que el usuario habilite; mínimo de datos.
- Costo `inf`: precios documentados x86 ($0.0000166667/GB-s, free tier
  400 000 GB-s). Con 256 MB × 20 s por sync = 5 GB-s, 50 usuarios cada 15 min
  serían ~144 000 invocaciones y ~720 000 GB-s al mes: ~$5 sobre el free tier.
  Duración real de sync `pend`; la frecuencia de sync es el control de costo.

### Fases revisadas

| Fase | Qué | Criterio de salida |
|---|---|---|
| P0 | Fixtures: lease, CAS, verificación de webhook Telegram, mapeo de eventos | Pruebas verdes sin red |
| P1 | Bot Telegram + `pair` por código para la cuenta piloto del usuario | Vinculado; código y claves fuera de logs |
| P2 | Solo recepción con sync programado, 14 días | Tasa de `LoggedOut`/`StreamReplaced`, duración y costo medidos |
| P3 | Envío con aprobación por mensaje | Ensayo con cuentas propias |
| P4 | 2–3 personas invitadas con consentimiento, `/stop` y borrado | Revisión SECURITY.md multiusuario + ToS/legal cerrada |
| P5 | Otras redes | Matriz A |

## Colaboradores en Venezuela

Respuesta del usuario (2026-10-03): quienes se vinculan son **colaboradores del
usuario**, que solo vinculan su WhatsApp, y viven **solo en Venezuela**.

### B-F026 — Marco legal venezolano aplicable (`doc`; no es asesoría legal)

- No hay ley general de protección de datos ni autoridad de control. Rigen el
  artículo 28 de la Constitución (habeas data: acceder, conocer el uso, rectificar
  o destruir datos), el artículo 60 (privacidad) y la sentencia TSJ 1318/2011.
  Esa sentencia fija principios de finalidad, consentimiento, proporcionalidad,
  seguridad y confidencialidad, y desaconseja transferir datos a Estados sin
  protección similar.
- Ley Especial contra los Delitos Informáticos: art. 20 (apoderarse, usar,
  modificar o eliminar datos personales ajenos sin consentimiento del dueño),
  art. 21 (acceder, capturar o interceptar comunicaciones ajenas) y art. 22
  (revelarlas), con prisión de dos a seis años.
- `inf`: el consentimiento del colaborador cubre sus propios datos. Sus chats con
  otras personas también contienen mensajes de terceros, que no consintieron.
  Procesar solo los chats que el colaborador habilite y descartar el resto en
  memoria, sin guardarlo, reduce la exposición a los arts. 20–21. Las sesiones
  se guardan en AWS fuera de Venezuela: el consentimiento debe decirlo.
- Revisión por abogado venezolano `pend` antes de vincular colaboradores.

Fuentes: [DLA Piper Venezuela](https://www.dlapiperdataprotection.com/?c=VE&t=law),
[GDPRI Venezuela](https://dataprotection.gi/jurisdictions/venezuela/),
[Constitución art. 28](https://www.ley.com.ve/constitucion/constitucion-de-la-republica-bolivariana-de-venezuela-articulo-28),
[Ley Especial contra los Delitos Informáticos (SUSCERTE)](https://www.suscerte.gob.ve/wp-content/uploads/2022/10/LeyEspecialcontralosDelitosInformaticos.pdf).

### B-F027 — Bloqueos de red en Venezuela afectan el canal de alta (`doc`/`pend`)

VE Sin Filtro reportó el 2025-01-10 bloqueos de Telegram (web y app) en CANTV,
Movistar, Digitel, Inter, Supercable, Airtek y G-Network. X y Signal siguen
restringidos según su sitio. En septiembre de 2026 CONATEL levantó bloqueos a
algunos medios, pero seguían ~177–200 dominios bloqueados. El estado actual de
Telegram por proveedor está `pend`.

`inf`: los workers corren en AWS, fuera de Venezuela; los bloqueos no afectan la
lectura de fuentes ni la conexión a WhatsApp desde Lambda. Sí pueden impedir que
un colaborador reciba el código por Telegram dentro de la ventana de ~160 s.
Canal de respaldo sin dependencias nuevas: la cuenta piloto ya vinculada envía el
código por WhatsApp al colaborador, con el envío aprobado por el usuario. Otra
opción: el usuario lee el código en su Telegram y se lo dicta al colaborador.

Fuentes: [elDiario 2025-01-10](https://eldiario.com/2025/01/10/reportaron-bloqueo-telegram-venezuela/),
[VE Sin Filtro](https://vesinfiltro.org/),
[TalCual 2026-09-25](https://talcualdigital.com/noticias/177-dominios-web-siguen-bloqueados-en-el-pais-denuncia-ve-sin-filtro/),
[CNN 2026-09-17](https://cnnespanol.cnn.com/2026/09/17/venezuela/conatel-levanta-bloqueo-medios-digitales-orix).

### Efecto en ToS y diseño

- Colaboradores en lugar de clientes reduce el riesgo de la cláusula de "ofrecer a
  terceros", pero no el de uso no personal ni el de envíos automáticos (B-F018,
  B-F024). Cada colaborador debe aceptar que su número puede ser bloqueado.
- El texto de consentimiento en Telegram debe cubrir: qué chats se procesan, que
  los datos se guardan en AWS fuera de Venezuela, el riesgo de bloqueo por ToS,
  cómo usar `/stop` y el borrado. Guardar fecha y versión aceptada.
- Región AWS: `us-east-1` o `sa-east-1` (São Paulo, en un país con ley general de
  datos). Latencia irrelevante para sync; precio por región `pend`.

## Competencia y productos similares

Pregunta del usuario (2026-10-03): ¿hay algo parecido ya construido? Búsqueda con
Exa y GitHub; las páginas se leyeron con Jina. Los datos de precios son los
publicados en cada sitio a esa fecha. Ningún producto se probó.

### B-F028 — Alertas para revendedores en Facebook Marketplace: mercado lleno (`doc`)

Flipify (Marketplace + comparación con vendidos de eBay; $29, $49 y $99 al mes),
Outpost Alerts (busca desde sus servidores sin pedir login; el usuario escribe al
vendedor en Facebook; solo Marketplace), SuperFlip AI (verifica ganancia contra
ventas reales y descuenta comisiones y envío), FlipDar, DealSonar, FB Flip Finder
y Marketplace Flipper. `inf`: están orientados a EE. UU./Reino Unido y a eBay
como referencia de precio. En las páginas leídas no aparece WhatsApp, Venezuela ni
contacto automático con vendedores.

Fuentes: [Flipify](https://getflipify.com/), [Outpost Alerts](https://outpostalerts.com/),
[SuperFlip](https://www.superflip.ai/), [FlipDar](https://www.flipdar.com/),
[DealSonar](https://www.dealsonar.io/).

### B-F029 — Open source más cercano: ai-marketplace-monitor (`insp`)

AGPL-3.0, 359 estrellas, push 2026-09-01. Monitorea Facebook Marketplace con
Playwright, evalúa anuncios con OpenAI, Claude, DeepSeek, Gemini u Ollama y
notifica por Telegram, ntfy, PushBullet o email. Su imagen Docker incluye noVNC:
el humano resuelve login y CAPTCHA dentro del contenedor remoto. Es la misma
"coherencia de origen" de B-D014 resuelta con un contenedor persistente. Usa
usuario y contraseña de Facebook por variables de entorno y no contacta
vendedores. AGPL: sirve como referencia, no para copiar código al repo
Apache-2.0. Otros repos de monitores de Marketplace tienen 0–25 estrellas.

Fuente: [ai-marketplace-monitor](https://github.com/BoPeng/ai-marketplace-monitor).

### B-F030 — Negociación automática: hay para vendedores, no para compradores (`doc`/`inf`)

PilotAgent responde y negocia en Facebook Marketplace del lado del vendedor (la
página muestra un plan de $5 400/mes). Revendor hace lo mismo para vendedores de
Vinted. Del lado comprador (escribir a vendedores para pedir precio) solo se
encontraron repos de hackathon sin mantenimiento (DealBot, DealScout, FlipIt).
`inf`: el hueco probablemente existe porque iniciar conversaciones automáticas
choca con los términos de Meta y WhatsApp (B-F018), no por falta de demanda.

Fuentes: [PilotAgent](https://www.pilotagent.ai/),
[Revendor](https://revendor.app/features/ai-agent),
[DealBot](https://github.com/Matthieu-Andre/DealBot).

### B-F031 — Sourcing por WhatsApp: redes de proveedores con opt-in (`doc`)

Sauda (pymes de India: el comprador pide por WhatsApp, a los proveedores les
llegan los leads, cotizaciones en horas), Yaar (repuestos industriales, login con
WhatsApp, consulta a su red de distribuidores), Sorsa (más de 50 plataformas de
sourcing, cotizaciones y seguimiento por WhatsApp). `inf`: funcionan porque los
proveedores aceptaron participar. Eso evita el problema de contactar en frío y es
un modelo a considerar para Venezuela: proveedores que se registran en el radar.

Fuentes: [Sauda](https://getsauda.com/), [Yaar](https://www.yaar.buzz/),
[Sorsa](https://sorsa.ai/).

### B-F032 — WhatsApp no oficial como servicio: competidores de nuestra pieza de infraestructura (`doc`)

Green API (plan Developer gratis: 1 instancia, solo 3 chats), Whapi.Cloud
(sandbox gratis hasta 5 conversaciones activas al mes), Z-API, UltraMsg y WaAPI
venden lo mismo que B-D017 construiría: WhatsApp vinculado por QR con API
HTTP. Los planes gratis no alcanzan para varios colaboradores, y la sesión
queda en manos de un tercero. Confirma B-D017: construir sobre whatsmeow.

Fuentes: [Green API tarifas](https://green-api.com/en/docs/about-tariffs/),
[Whapi precios](https://whapi.cloud/price), [Z-API](https://z-api.io/en),
[UltraMsg](https://ultramsg.com/).

### B-F033 — Venezuela: herramientas para vendedores, no radar de compra (`insp` solo títulos)

La búsqueda devolvió Vendey ("Business OS para emprendedores en Venezuela"), el
procurement de Tarantín, Vercatalogo y Mayorista Plus (dropshipping). Por título,
son herramientas o marketplaces para vender, no radares de oportunidades de
compra. Sin lectura detallada: `pend` confirmar que ninguno hace lo mismo.

Fuentes: [Vendey](https://vendey.app/),
[Tarantín procurement](https://tarantin.app/procurement).

### Lectura para el proyecto (`inf`)

- Descubrimiento + alertas sobre Facebook Marketplace ya está resuelto y es
  barato para EE. UU.; competir ahí no tiene sentido.
- El hueco visible es mercado venezolano/WhatsApp-first: vendedores que publican
  en WhatsApp, Instagram y Marketplace locales, precios en USD y Bs con tasa
  fechada, costos desconocidos ≠ 0, y consulta de precio con aprobación humana.
  Ninguno de los productos leídos combina eso.
- ai-marketplace-monitor confirma dos ideas del diseño: login humano dentro del
  entorno remoto y notificación por Telegram. También enseña su límite: necesita
  contenedor persistente y contraseña guardada, que este proyecto evita.
- Los modelos de sourcing con opt-in (B-F031) ofrecen una ruta conforme a ToS para
  escalar la consulta de precios sin contactar en frío.

## Funciones a adoptar de la competencia

Instrucción del usuario (2026-10-03): tomar lo bueno de los productos revisados
para integrarlo. B solo edita este informe. La integración en ROADMAP, CONTEXT y
ARCHITECTURE corresponde al coordinador. Se adoptan **ideas y comportamientos**,
no código: ai-marketplace-monitor es AGPL-3.0 y el resto es propietario.
Términos en negrita = lenguaje de [CONTEXT.md](../../CONTEXT.md).

### Adoptar

| ID | Idea | Origen | Adaptación al radar | Fase ROADMAP |
|---|---|---|---|---|
| B-I01 | Búsqueda guardada: palabra clave, rango de precio, ubicación y radio, frecuencia, palabras que incluyen/excluyen, vendedores excluidos | Flipify, Outpost, AIMM | Ligada a **Producto canónico**/**Variante**; exclusiones explícitas; frecuencia limitada por presupuesto de fuente | 2–3 |
| B-I02 | Tarjeta de oportunidad: precio pedido, valor de referencia, % bajo referencia, ganancia | Flipify | **Precio publicado** vs referencia con fecha y fuente; **Ganancia estimada** sobre **Costo puesto en destino**; costos faltantes visibles como desconocidos, nunca cero | 1 |
| B-I03 | Ganancia neta tras comisiones, envío y demanda | SuperFlip | Escenario de costos decimal con supuestos listados y tasa USD/Bs fechada | 1 |
| B-I04 | Precio de referencia por ventas reales y velocidad de venta | SuperFlip, Flipify (eBay vendidos) | **Evidencia de demanda** con procedencia; fuente venezolana de precios de transacción `pend` | 2–4 |
| B-I05 | Calificación por bandas con explicación corta | Flipify (STEAL…), AIMM (1–5 + motivo) | Banda determinística por margen y calidad de evidencia; explicación que cita datos; IA solo para ambigüedad acotada | 1, IA en 4 |
| B-I06 | Alertas por Telegram con límite por instancia y global, repetición configurable | AIMM, Outpost, SuperFlip | Reusar el bot de alta (B-D016); deduplicar por **Anuncio**; volver a avisar solo si baja el precio; horario silencioso | Piloto |
| B-I07 | Ciclo de vida: seguir, marcar comprado, descartar; registro de compras y ventas con ROI | Outpost, Flipify (Flip Tracker) | Estados de **Oportunidad candidata**: nueva → revisando → consultada → descartada / comprada → vendida; ROI solo desde **Ganancia realizada** registrada por el usuario | 5 |
| B-I08 | Lectura de anuncios públicos sin conectar cuentas personales | SuperFlip, Outpost | Preferir lectura pública permitida antes que sesión; si hace falta sesión, cuenta dedicada (B-D012) | 2 |
| B-I09 | Salvaguardas de mensajería: no responderse a sí mismo; parar si se repite; presupuesto por pasada; espera anti-ráfaga; ignorar mensajes viejos; si no se puede verificar el estado de la conversación, no enviar; control humano en cualquier momento; nunca contactar dos veces a la misma persona | Revendor | Reglas obligatorias de P3 para la **consulta de precio**, junto con `send_uncertain` y la aprobación humana | P3 |
| B-I10 | La IA solo afirma hechos de una lista; idioma según el país | Revendor | Plantillas de consulta en español venezolano con hechos verificables del **Anuncio**; nada inventado | P3 |
| B-I11 | Estrategia con piso/techo y escalones | Revendor (lado vendedor) | Lado comprador: precio máximo por oportunidad derivado de la ganancia mínima; contraofertas solo con aprobación | Posterior |
| B-I12 | Rotación ponderada de plantillas para medir conversión | Revendor | Medir tasa de respuesta por plantilla en evaluaciones; sin optimizar persuasión engañosa | 4 |
| B-I13 | Registro de cotizaciones por canal, precio por unidad, seguimiento tras silencio | Sorsa | Libro de cotizaciones: respuesta ↔ anuncio, unidad normalizada, seguimiento propuesto y aprobado | P3 |
| B-I14 | Red de proveedores con opt-in que reciben solicitudes compatibles | Sauda, Yaar | "Proveedor inscrito": vía conforme a ToS para consultas de precio sin contacto en frío | Evolución |
| B-I15 | Pedido por foto, nota de voz o especificación con preguntas de aclaración | Yaar | Colaboradores registran **Solicitudes de compra** por Telegram; el sistema pide los atributos de **Variante** que falten | Posterior |
| B-I16 | Login humano dentro del entorno remoto | AIMM (noVNC) | Opción para coherencia de origen en redes con login web: tarea efímera solo para capturar (`pend` costo y diseño); no en Lambda | Posterior |
| B-I17 | Editor de configuración y logs en vivo vía web | AIMM | Dashboard de fase 5; autenticación obligatoria también en localhost expuesto | 5 |

### No adoptar

- "Ganancia verificada", promedios de ganancia por usuario y cifras de prueba
  social (SuperFlip): contradicen AGENTS.md (no declarar ROI ni ganancias sin
  operaciones registradas). Usar **Ganancia estimada** con supuestos.
- Stealth, proxies residenciales y resolución de CAPTCHA (Browser Use Cloud,
  servicios similares): excluidos (B-D014).
- Aceptación automática de ofertas y contraofertas sin aprobación (Revendor).
- Mensajes a quien marca favorito u otras formas de contacto en frío (Revendor
  "Favorites"): equivalen a campañas de contacto, fuera del roadmap.
- Contraseñas de plataformas en variables de entorno (AIMM): el proyecto usa
  captura manual y estado cifrado.
- Cobertura "mundial en una búsqueda": sin afirmaciones de cobertura universal.
- Copiar código de AIMM (AGPL) al repositorio Apache-2.0.

### Prioridad sugerida para el primer entregable útil (`inf`)

1. B-I02 + B-I03 + B-I05: tarjeta con ganancia estimada, costos desconocidos y
   banda explicada. Encaja en Fase 1 sin red.
2. B-I01 + B-I08: búsqueda guardada sobre una fuente pública permitida.
3. B-I06: alertas Telegram con deduplicación, reutilizando el bot del piloto.
4. B-I07: ciclo de vida y registro de compra/venta; cierra el ciclo de evaluación
   con **Ganancia realizada** real.
5. B-I09 + B-I10 + B-I13: requisitos del piloto WhatsApp P3 antes de enviar nada.

### Términos de dominio que el coordinador podría añadir a CONTEXT.md

Búsqueda guardada, Consulta de precio, Cotización recibida, Proveedor inscrito y
Estado de oportunidad. Propuesta; B no edita CONTEXT.md.

## Modelo general de oportunidades

Instrucción del usuario (2026-10-03): el modelo debe servir para productos,
acciones, emprendimientos, temas virales, contactos y empresas, entre otros.

**Choque de alcance:** AGENTS.md dice "Este es el radar de productos/reventa".
Generalizar cambia la visión del proyecto. Lo decide el usuario, y el
coordinador actualiza AGENTS.md, VISION.md y CONTEXT.md. B solo propone.

### Núcleo común (generaliza CONTEXT.md sin romperlo)

| Concepto general | Qué es | En productos/reventa (actual) |
|---|---|---|
| **Entidad** | Lo que se evalúa: producto/variante, emisor de acciones, empresa, emprendimiento, tema | **Producto canónico** / **Variante** |
| **Señal** | Observación fechada con fuente y procedencia | **Anuncio**, **Precio publicado** |
| **Contexto** | Dónde aplica: mercado, región, moneda, plataforma, idioma | **Mercado de origen/destino** |
| **Tesis** | Por qué podría haber oportunidad, con regla de evaluación de la vertical | Diferencia adquisición ↔ reventa |
| **Evidencia** | Señales a favor y en contra, independencia entre fuentes, límites | **Evidencia de demanda** |
| **Escenario** | Costos, ingresos y supuestos; decimal; tasa fechada; desconocido ≠ 0 | **Costo puesto en destino**, **Ganancia estimada** |
| **Oportunidad candidata** | Entidad + tesis + evidencia + escenario + banda + estado | Igual |
| **Acción propuesta** | Siguiente paso con compuertas: capacidad, autorización, aprobación humana, presupuesto | Consulta de precio |
| **Resultado registrado** | Lo que pasó de verdad, registrado por el usuario | **Ganancia realizada** |

Las tesis se encadenan entre verticales. Ejemplo: un tema viral sube la demanda de
un producto, que tiene un proveedor (empresa) y una reventa local. Una sola base
de entidades y señales permite ese encadenamiento sin duplicar fuentes.

### Verticales

Cada vertical es un módulo con cuatro funciones: `normalize`, `match`, `score`
y `allowed_actions`. Más su configuración. Sin framework de plugins.

| Vertical | Entidad e identidad | Señales | Tesis y puntuación determinística | Acciones permitidas | Compuerta legal / ToS |
|---|---|---|---|---|---|
| Productos/reventa | Producto + variante + condición + unidad | Anuncios, precios, vendidos | Diferencia neta tras costos | Alerta; consulta de precio aprobada | B-F018, B-F026 |
| Acciones | Emisor + ticker + bolsa + moneda | Precios, presentaciones regulatorias, noticias | Anomalía o evento informativo (presentación nueva, volumen inusual); **sin recomendación** | Solo alerta informativa; **nunca** comprar/vender ni asesoría personalizada | SUNAVAL lleva registro de asesores de inversión; revisión legal `pend` |
| Emprendimientos | Categoría de negocio + zona | Búsquedas, menciones, densidad de oferta local, anuncios de traspaso | Brecha: demanda creciente con oferta local escasa | Alerta; investigación adicional | Atribución ODbL de datos OSM |
| Temas virales | Tema normalizado + idioma + región | Menciones por plataforma, noticias, vistas de Wikipedia, búsquedas | Velocidad y aceleración, difusión entre plataformas independientes, novedad frente a línea base | Alerta; enlazar a productos/emprendimientos | Solo metadatos y enlaces; no copiar contenido |
| Empresas/contactos | Organización (registro/dominio) + persona de contacto con rol | Contrataciones, aperturas, licitaciones, presentaciones, web | Encaje con lo que se ofrece + evento disparador reciente | Contacto **individual** aprobado; nunca masivo | Datos personales (B-F026 art. 20); AGENTS.md excluye correo masivo |

### Fuentes gratuitas por vertical (`doc` salvo indicación)

- **Acciones:** SEC `data.sec.gov` (JSON sin autenticación ni API key);
  edgartools (MIT, activo); OpenBB (Apache-2.0 en su LICENSE actual; repo bajo
  `openbq-org`); yfinance (Apache-2.0, cliente no oficial de Yahoo; términos
  `pend`). Bolsa de Valores de Caracas: fuente `pend`.
- **Temas virales:** GDELT (gratis, noticias en más de 100 idiomas, actualización
  cada 15 min); API de vistas de Wikimedia; Google Trends API en alfa con
  solicitud de acceso (pytrends archivado desde 2024); canales sociales de Agent
  Reach/OpenCLI según matriz A.
- **Emprendimientos:** OpenStreetMap vía Overpass para densidad de comercios. La
  instancia pública pide menos de ~100 consultas/día para uso regular y
  recomienda servidor propio o extractos de Geofabrik para uso comercial.
- **Empresas:** SEC EDGAR (EE. UU.), OSM (comercios locales), sitios web vía lector
  web. OpenCorporates comercial es de pago (`pend`). LinkedIn solo lectura según
  capacidad A, con sus términos.

Fuentes: [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces),
[edgartools](https://github.com/dgunning/edgartools),
[OpenBB](https://github.com/openbq-org/OpenBB),
[yfinance](https://github.com/ranaroussi/yfinance),
[GDELT](https://www.gdeltproject.org/),
[Wikimedia page views](https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/reference/page-views.html),
[Google Trends API alpha](https://developers.google.com/search/apis/trends),
[pytrends](https://github.com/GeneralMills/pytrends),
[Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API),
[SUNAVAL asesores](https://www.sunaval.gob.ve/asesores-de-inversion-nat/),
[OpenCorporates pricing](https://opencorporates.com/pricing/).

### Reglas que no cambian al generalizar

- Señal ≠ hecho confirmado: una mención no es demanda, un precio publicado no es
  una transacción, un registro de empresa no es interés de compra.
- Toda acción externa pasa por compuertas y aprobación humana. Acciones: nunca
  órdenes ni recomendaciones personalizadas.
- Costos y datos faltantes son desconocidos, nunca cero; las bandas se calculan
  igual de forma determinística en todas las verticales y la IA es opcional.
- Contactos de personas: mínimo de datos, finalidad explícita y sin campañas.

### Cómo implementarlo sin sobrediseñar (`inf`)

1. Fase 1 sigue siendo productos, pero con los nombres del núcleo: `Entity`,
   `Signal`, `Thesis`, `Evidence`, `Scenario`, `Opportunity`, `ProposedAction`,
   `Outcome`.
2. Segunda vertical con fixtures para probar que el núcleo es general: **temas
   virales**. Es de solo lectura, tiene fuentes gratuitas, no contacta a nadie,
   no tiene compuerta regulatoria y alimenta a productos.
3. Emprendimientos después: reutiliza las señales virales y añade oferta OSM.
4. Empresas/contactos y acciones al final: tienen las compuertas legales más
   exigentes.
5. Un cambio de nombre en el núcleo es barato ahora y caro después; los
   módulos de vertical se agregan sin tocar el núcleo.

## Memoria entre sesiones e informes

Instrucción del usuario (2026-10-03): contemplar memoria entre sesiones e informes,
y buscar mejores ideas para implementar.

### B-F034 — Sistemas de memoria para agentes: ideas sí, dependencia no (`doc`/`insp`)

| Sistema | Licencia / actividad | Requiere | Idea aprovechable |
|---|---|---|---|
| Mem0 | Apache-2.0; push 2026-10-01 | LLM obligatorio (por defecto `gpt-5-mini`) + embeddings; extracción en una llamada, solo agrega | Acumular sin sobrescribir |
| Graphiti (Zep) | Apache-2.0; push 2026-10-02 | Neo4j, FalkorDB o Neptune (Kuzu obsoleto) + LLM con salida estructurada | Hechos bi-temporales: se invalidan, no se borran; consultar "qué era cierto en tal fecha" |
| Letta | Apache-2.0 | Servidor de agentes persistente | Bloques de memoria por usuario (preferencias) |
| Cognee / LangMem | Apache-2.0 / MIT | LLM + almacenes | — |
| basic-memory | AGPL-3.0 | Markdown local | `descartado` por licencia |

`inf`: todos agregan costo de LLM por escritura o infraestructura persistente, y
eso choca con Lambda bajo demanda y con la regla de mínima dependencia de LLM. La
memoria del radar debe ser **estructurada y determinística**. Un asistente
conversacional sobre esa memoria puede venir después (Fase 4) sin cambiarla.

Fuentes: [Mem0](https://github.com/mem0ai/mem0), [Graphiti](https://github.com/getzep/graphiti),
[Letta](https://github.com/letta-ai/letta), [Cognee](https://github.com/topoteretes/cognee),
[LangMem](https://github.com/langchain-ai/langmem), [basic-memory](https://github.com/basicmachines-co/basic-memory).

### B-F035 — Almacenes que encajan con Lambda (`doc`)

- **DynamoDB:** free tier mensual por región y cuenta pagadora de 25 GB de
  almacenamiento (capacidad provisionada, clase Standard), "suficiente para unos
  200 M solicitudes/mes" según AWS. Escrituras condicionales y TTL nativos.
  Elegibilidad de la cuenta del usuario `pend`.
- **DuckDB** (MIT): extensión `httpfs` lee, escribe y recorre con globs archivos en
  S3 (`FROM 's3://…/*.parquet'`). Sirve para informes sobre un registro en S3
  sin base de datos encendida.
- **SQLite FTS5:** búsqueda de texto completo con ranking `bm25()` incluida en
  SQLite. Vectores opcionales: sqlite-vec (Apache-2.0) o LanceDB (Apache-2.0),
  solo si una evaluación demuestra que hacen falta.
- **datasketch** (MIT): MinHash/LSH para detectar anuncios casi duplicados
  (republicados con otro texto) de forma determinística.

Fuentes: [DynamoDB pricing](https://aws.amazon.com/dynamodb/pricing/),
[DuckDB S3](https://duckdb.org/docs/stable/core_extensions/httpfs/s3api),
[SQLite FTS5](https://www.sqlite.org/fts5.html),
[sqlite-vec](https://github.com/asg017/sqlite-vec), [LanceDB](https://github.com/lancedb/lancedb),
[datasketch](https://github.com/ekzhu/datasketch).

### Diseño de memoria propuesto

Tres capas, cada una con un solo trabajo:

| Capa | Dónde | Qué guarda | Por qué ahí |
|---|---|---|---|
| Registro de eventos (fuente de verdad) | S3, JSONL/Parquet por corrida y fecha | Señales observadas, oportunidades, acciones, aprobaciones, resultados, feedback, fallos | Solo agrega, barato, reproducible; DuckDB lo consulta para informes |
| Memoria operativa | DynamoDB, tabla única | Claves de deduplicación, último precio por anuncio, cursores por fuente, registro de alertas enviadas, registro de contactos ("nunca dos veces"), leases, estado del bot por colaborador | Escrituras concurrentes y condicionales desde muchas Lambdas; TTL para expirar |
| Blobs | S3 cifrado | Sesiones (B-D009), adjuntos e informes completos | Ya decidido |

Con DynamoDB, los leases pueden pasar de S3 a escrituras condicionales en la
misma tabla. S3 `If-Match` queda solo para publicar estados de sesión.

Ideas tomadas y adaptadas:

- **Hechos bi-temporales** (de Graphiti): cada hecho lleva `observed_at` (cuándo lo
  vimos) y `valid_from`/`valid_to` (cuándo era cierto). Un precio nuevo invalida el
  anterior sin borrarlo. Así hay historial de precios, tiempo en el mercado y
  bajas de precio, que es una señal de negociabilidad.
- **Acumular, no sobrescribir** (de Mem0): las proyecciones se pueden
  reconstruir desde el registro, lo que sirve para pruebas e idempotencia.
- **Preferencias por persona** (de Letta): umbrales, categorías y horario de
  alertas por colaborador en la memoria operativa.
- **Feedback como memoria que aprende:** cada oportunidad marcada útil, descartada
  o comprada calibra los umbrales de banda por vertical y alimenta el dataset de
  evaluación de la Fase 4. Sin IA.
- **Choque con el derecho a borrar:** un registro que solo agrega choca con
  `/stop` y habeas data (B-F026). Solución: datos personales fuera del registro
  general, con seudónimo y clave por persona. Borrar la clave hace ilegibles sus
  datos (crypto-shredding).

### Informes

| Informe | Canal | Contenido | Frecuencia |
|---|---|---|---|
| Alerta | Telegram (máx. 4096 caracteres por mensaje) | Una oportunidad de banda alta: tarjeta B-I02 + enlace | Inmediata, con deduplicación y límite |
| Resumen | Telegram + documento | Nuevas por vertical, cambios (bajas de precio, temas acelerando), acciones esperando aprobación, resultados registrados | Diario o semanal, configurable |
| Salud operativa | Telegram | Fuentes caídas, `needs_reauth`, cuotas, costo AWS de la corrida, qué no se pudo revisar | Con el resumen; inmediato si falla una fuente requerida |
| Aprendizaje | Documento | Precisión por banda según resultados, tasa de respuesta por plantilla, falsos positivos frecuentes | Semanal/mensual |
| Ficha de oportunidad | Documento | Evidencia, supuestos, costos desconocidos, historial de precio, fuentes | A pedido |

Implementación mínima (`inf`): consultas DuckDB sobre el registro → plantillas
Jinja2 (BSD-3) → texto HTML de Telegram para mensajes cortos y un archivo HTML
enviado con `sendDocument` para el informe completo. Así no hay URLs públicas ni
URLs firmadas que filtrar. Cada informe declara `run_id`, versión de
configuración, cobertura y fallos (AGENTS.md: preservar fallos). Evidence.dev
(MIT) o Datasette (Apache-2.0) quedan para el dashboard de la Fase 5.

Fuentes: [Telegram Bot API](https://core.telegram.org/bots/api),
[Jinja](https://github.com/pallets/jinja), [Evidence](https://github.com/evidence-dev/evidence),
[Datasette](https://github.com/simonw/datasette).

### Mejores ideas adicionales para pensar

- **Informe de cobertura** ("por qué no apareció"): qué fuentes y búsquedas se
  revisaron, cuáles fallaron y cuáles se omitieron por presupuesto. Distingue
  "sin ofertas" de "no se pudo mirar".
- **Tiempo en el mercado y bajas de precio** como señales del registro bi-temporal.
- **Backtesting:** volver a correr reglas de banda nuevas sobre el registro con
  resultados conocidos antes de activarlas.
- **Reconstrucción desde cero:** borrar proyecciones y regenerarlas desde el
  registro debe dar el mismo estado; es la prueba de idempotencia.
- **Retención por tipo:** señales públicas largas, datos personales cortos,
  mensajes de chats no habilitados nunca (B-D023).

### Memoria de los agentes de desarrollo

Para Codex y Claude, la memoria entre sesiones ya es este repositorio:
AGENTS.md, `docs/research/` y el registro de decisiones. No hace falta otra
herramienta. Los informes A/B y `decisions.md` deben seguir siendo el punto de
entrada de cada sesión nueva.

## Telegram como interfaz principal

Decisión del usuario (2026-10-03): por ahora el asistente se usa mediante un bot
de Telegram. El bot deja de ser solo canal de alta y alertas (B-D016, B-I06) y
pasa a ser la interfaz completa. El dashboard web de la Fase 5 se reemplaza por
el bot, y más adelante por una Mini App.

### B-F036 — Capacidades de la Bot API para una interfaz completa (`doc`)

- Teclados inline con `callback_data` de 1–64 bytes; `answerCallbackQuery` confirma
  el toque al usuario. Sirve para aprobar/descartar desde la misma alerta.
- Con webhook, la respuesta HTTP puede llevar una llamada a un método de la API
  (por ejemplo, `sendMessage`), lo que ahorra una petición y latencia en Lambda.
- `allowed_updates` filtra qué tipos de update llegan; `setMyCommands` publica el
  menú de comandos.
- Archivos: hasta 50 MB por subida de documento, suficiente para informes HTML.
- Mini Apps: la página recibe `initData` y el servidor debe validarla (hash
  derivado del token del bot) antes de confiar; `initDataUnsafe` no es confiable.
- Bot API 10.3 (2026-08-24) añadió "Rich Messages" (`sendRichMessage`) con
  tablas y botones: candidato para tarjetas y resúmenes; formato exacto `pend`.
- `BotAccessSettings` (acceso restringido a usuarios elegidos) aparece ligado a
  "managed bots"; no está claro que aplique a un bot propio, así que la lista
  blanca se implementa en el servidor (`pend` verificar).

Fuentes: [Bot API](https://core.telegram.org/bots/api),
[Mini Apps](https://core.telegram.org/bots/webapps).

### B-F037 — Librerías (`insp`)

| Librería | Licencia | Nota |
|---|---|---|
| aiogram (Python) | MIT; push 2026-09-30 | Routers, filtros, estados de conversación |
| python-telegram-bot | **GPL-3.0** | `descartado`: copyleft fuerte en un repo Apache-2.0 |
| grammY (TypeScript) | MIT | Fuera del stack Python del repo |
| go-telegram/bot (Go) | MIT | Útil si el worker WhatsApp en Go necesita enviar avisos directos |

El repo es Python (`scripts/`, `tests/`). Recomendación `inf`: empezar con HTTPS
directo (unos pocos métodos: `sendMessage`, `editMessageText`,
`answerCallbackQuery`, `sendDocument`, `setWebhook`, `setMyCommands`) y adoptar
aiogram solo si los flujos de conversación crecen.

### Diseño de interacción

**Roles:** propietario (el usuario: aprueba, configura y ve todo) y colaborador
(vincula WhatsApp, recibe las alertas que le correspondan, registra solicitudes).

| Comando | Qué hace | Rol |
|---|---|---|
| `/start` | Consentimiento versionado y alta | Todos |
| `/vincular` | Emparejar WhatsApp con código (B-D016) | Todos |
| `/buscar` | Asistente con botones para crear una búsqueda guardada (B-I01): vertical, palabra clave, zona, precio, exclusiones | Propietario |
| `/busquedas` | Listar, pausar o editar búsquedas | Propietario |
| `/oportunidades` | Últimas oportunidades por banda | Todos según permiso |
| `/pendientes` | Acciones esperando aprobación | Propietario |
| `/resumen` y `/salud` | Informes de B-D036 bajo pedido | Propietario |
| `/pedir` | Registrar una solicitud de compra con foto o texto (B-I15) | Colaborador |
| `/mis_datos` y `/borrar` | Ver y borrar sus datos (habeas data, art. 28) | Todos |
| `/stop` | Desvincular WhatsApp (`Logout`) y borrar | Todos |

**Tarjeta de alerta** con botones: `Útil` · `Descartar` · `Comprada` · `Ficha` ·
`Consultar precio`. Cada toque es feedback (B-D032) o abre una acción.

**Aprobación:** el bot muestra el borrador exacto con `Aprobar` · `Editar` ·
`Cancelar`. Al aprobar, edita el mensaje con el estado final, de modo que no se
puede aprobar dos veces. Los mensajes que nunca se confirmaron quedan como
`send_uncertain`, sin reintento automático.

**Texto libre:** primero botones y comandos determinísticos. Más adelante (Fase 4)
un parser con LLM puede convertir "busca iPhone 13 en Caracas bajo $300" en una
búsqueda, pero siempre muestra "¿Entendí bien?" con botones antes de guardar.
El texto del usuario y el contenido de las fuentes se tratan como datos, nunca
como instrucciones.

### Seguridad y operación en Lambda

- **Lista blanca por `user_id`** de Telegram (no por `username`, que se puede
  cambiar). Quien no esté autorizado recibe una respuesta neutra y nada más.
- Cada webhook se valida con `X-Telegram-Bot-Api-Secret-Token` (B-F022).
- `callback_data` lleva solo un ID corto opaco. El servidor comprueba en cada
  toque que ese usuario puede actuar sobre ese objeto.
- **Idempotencia:** guardar `update_id` y el estado de cada aprobación con
  escritura condicional en DynamoDB y TTL. Un update repetido no duplica efectos
  (política de reintentos del webhook `pend` de verificar).
- Responder rápido: el webhook encola el trabajo pesado en SQS y contesta. Usar
  la respuesta HTTP con método para acuses inmediatos.
- Token del bot y secreto del webhook en SSM; nunca en logs ni en `callback_data`.
- **Disponibilidad en Venezuela (B-F027):** si Telegram falla en un proveedor, la
  interfaz entera cae para ese colaborador. Respaldo mínimo: alertas críticas por
  WhatsApp desde la cuenta piloto, con aprobación del usuario.

### Guía de buenas prácticas

Detalle operativo de Telegram + Lambda (webhook, seguridad, idempotencia,
límites, SQS, durable functions, secretos, logs, costos y cómo mantenerse
actualizado): [telegram-lambda-best-practices.md](agent-b/telegram-lambda-best-practices.md).

### Mini App (posterior)

Para tablas largas, edición de búsquedas o la ficha completa: una Mini App
estática servida desde S3 privado, que manda `initData` al backend para que lo
valide. Sustituye al dashboard web de B-I17. Hosting y costo `pend`. Antes,
evaluar si los Rich Messages con tablas alcanzan.

## Costo mínimo: AWS free tier + OpenRouter

Respuestas del usuario (2026-10-03): la cuenta AWS entra en el tramo gratuito y
todo debe ser lo más barato posible. La IA será un modelo pequeño vía OpenRouter.
Nota: AGENTS.md prohíbe APIs pagadas *implícitas*; OpenRouter queda elegido de
forma explícita por el usuario.

### B-F038 — El free tier de AWS cambió en julio de 2025 (`doc`)

- Cuentas nuevas: $100 de crédito al crear la cuenta y hasta $100 más por
  actividades. Se elige **Free plan** o **Paid plan**.
- **Free plan:** sin cargos, solo ofertas "Always Free" activas y algunos
  servicios no disponibles. Termina a los **6 meses** o al agotar créditos, lo que
  ocurra primero. Entonces AWS **suspende la cuenta**, guarda los datos 90 días y
  luego **borra la cuenta y todo su contenido** si no se pasa a Paid plan.
- **Paid plan:** conserva créditos y ofertas Always Free; se paga solo lo que
  excede.
- Cuentas anteriores (o personas que ya tuvieron una) no reciben el Free plan ni
  los créditos.

Consecuencia `inf`: un radar en producción sobre Free plan se apaga solo. Hay que
pasar a Paid plan antes del mes 5, con alertas de AWS Budgets ya configuradas.
Con el diseño de abajo, el costo en Paid plan sigue siendo de centavos.

**Cuenta del usuario (respuesta 2026-10-03: creada antes de 2025, sin certeza):**
AWS mantiene en el **free tier antiguo** a las cuentas creadas antes del
2025-07-15: pruebas cortas, pruebas de 12 meses y ofertas Always Free. `inf`: para
una cuenta de antes de 2025, las ofertas de 12 meses (por ejemplo, S3 o ECR del
primer año) ya vencieron. Las Always Free de B-F039 siguen vigentes. No hay
créditos nuevos ni cierre a los 6 meses; todo lo que exceda Always Free se cobra
a la tarifa normal. Por eso la alerta de Budgets es obligatoria desde el primer
despliegue. Confirmar en la consola: Billing → Free Tier muestra el programa y
las ofertas activas.

Fuente: [AWS blog, 2025-07-15](https://aws.amazon.com/blogs/aws/aws-free-tier-update-new-customers-can-get-started-and-explore-aws-with-up-to-200-in-credits/).

Fuentes: [AWS Free Tier](https://aws.amazon.com/free/),
[Free Tier docs](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier.html),
[Free Tier FAQ](https://aws.amazon.com/free/free-tier-faqs/).

### B-F039 — Límites Always Free relevantes (`doc`)

| Servicio | Gratis cada mes | Nota |
|---|---|---|
| Lambda | 1 M solicitudes + 400 000 GB-s | arm64 más barato fuera del free tier |
| DynamoDB | 25 GB | El free tier usa **capacidad provisionada**, no on-demand |
| SQS | 1 M solicitudes | Todas las regiones sumadas |
| EventBridge Scheduler | 14 M invocaciones | — |
| KMS | 20 000 solicitudes | Claves administradas por AWS: almacenamiento gratis, solicitudes cuentan |
| SSM Parameter Store | Parámetros estándar sin cargo | 4 KB máx. |
| AWS Budgets | Monitoreo y alertas gratis | 2 presupuestos con acciones gratis |
| S3 | No aparece como Always Free | $0.023/GB-mes + cargos por PUT/GET |
| ECR | 500 MB/mes solo el primer año | Evitar imágenes de contenedor si se puede |

Fuentes: [Lambda](https://aws.amazon.com/lambda/pricing/),
[DynamoDB](https://aws.amazon.com/dynamodb/pricing/),
[SQS](https://aws.amazon.com/sqs/pricing/),
[EventBridge](https://aws.amazon.com/eventbridge/pricing/),
[KMS](https://aws.amazon.com/kms/pricing/),
[SSM](https://aws.amazon.com/systems-manager/pricing/),
[Budgets](https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/),
[S3](https://aws.amazon.com/s3/pricing/), [ECR](https://aws.amazon.com/ecr/pricing/).

### Palancas de costo mínimo (`inf`)

1. **Nada encendido 24/7:** sin NAT, VPC, API Gateway, Secrets Manager, RDS,
   OpenSearch ni instancias. Webhook por Function URL.
2. **Sin ECR si cabe:** Chromium como capa zip (`@sparticuz/chromium`) dentro del
   límite de 250 MB descomprimidos, en lugar de imagen de contenedor (`pend`
   verificar tamaño real).
3. **arm64 + memoria justa:** webhook pequeño, WhatsApp en Go, Chromium solo
   cuando no haya lectura HTTP permitida.
4. **DynamoDB en modo provisionado** dentro del free tier, sin autoescalado que lo
   saque del límite; TTL para expirar registros sin costo.
5. **S3 mínimo:** subir el estado solo si cambió; reglas de ciclo de vida para
   expirar informes y eventos viejos.
6. **Frecuencia adaptativa:** el scheduler es la palanca principal. Menos syncs de
   noche o sin chats activos; más cuando hay una consulta abierta.
7. **Logs baratos:** retención de 7–14 días, nivel WARN por defecto y sin métricas
   personalizadas de CloudWatch. Los conteos salen del registro de eventos en los
   informes.
8. **KMS bajo el límite:** cachear parámetros con la Parameters and Secrets
   Extension.
9. **Región `us-east-1`:** los precios citados son de esa región. `sa-east-1`
   queda como alternativa por ley de datos (B-F027), pero con precio `pend`.

Estimación `inf` del piloto (propietario + 5 colaboradores; sync WhatsApp cada
15 min con 256 MB × 20 s; 1 000 lecturas Chromium/mes con 2 GB × 60 s):

| Recurso | Uso mensual | Free tier | Costo |
|---|---|---|---|
| Lambda GB-s | ~72 000 (WhatsApp) + ~120 000 (Chromium) + webhook/informes ≈ 195 000 | 400 000 | $0 |
| Lambda solicitudes | ~20 000 | 1 M | $0 |
| SQS | ~60 000 | 1 M | $0 |
| DynamoDB | < 1 GB | 25 GB | $0 |
| S3 | ~15 000 PUT + < 1 GB | — | ~$0.10 |
| **Total AWS** | | | **~$0.10/mes** |

Las duraciones reales están `pend` (LOCAL_LAMBDA_TESTING §5). Si Chromium dura
el doble, sigue dentro del free tier.

### B-F040 — OpenRouter: precios, gratuitos y privacidad (`doc`)

- Sin recargo sobre el precio del proveedor; **comisión del 5.5% (mínimo $0.80)
  al comprar créditos**, 5% con cripto.
- Modelos `:free`: 20 solicitudes/min; **50/día** si se compraron menos de $10 en
  total, **1 000/día** si se compraron $10 o más. Con saldo negativo, incluso los
  gratuitos devuelven 402.
- Privacidad: la cuenta tiene ajustes separados para modelos pagos y gratuitos
  sobre proveedores que entrenan con los prompts. Por solicitud:
  `provider.data_collection: "deny"` y `provider.zdr: true` (solo endpoints sin
  retención).
- Salida estructurada con `response_format` tipo `json_schema`. El soporte depende
  de cada proveedor del modelo: usar `provider.require_parameters: true`. También
  existen `sort: "price"` y `max_price`.

Candidatos pequeños con salida estructurada (API `/api/v1/models`, 2026-10-03,
USD por millón de tokens entrada/salida):

| Modelo | Entrada | Salida | Nota |
|---|---|---|---|
| `openai/gpt-oss-20b` | 0.018 | 0.090 | Pesos abiertos |
| `deepseek/deepseek-v4-flash` | 0.028 | 0.056 | Contexto 1 M |
| `qwen/qwen3.7-flash` | 0.030 | 0.130 | — |
| `mistralai/mistral-small-24b-instruct-2501` | 0.050 | 0.080 | — |
| `google/gemma-4-26b-a4b-it` | 0.068 | 0.225 | También existe `:free` |
| `openai/gpt-5-nano` | 0.050 | 0.400 | — |
| `:free` (gemma-4, qwen3.8-27b, nemotron-3-super) | 0 | 0 | Sujetos a límites diarios y políticas de datos del proveedor |

Calidad en español venezolano: **sin evaluar** (`pend`). Costo `inf`: una
extracción típica (~800 tokens de entrada, ~150 de salida) cuesta ~$0.00003 con
los dos primeros; 10 000 llamadas ≈ $0.30.

Fuentes: [OpenRouter FAQ](https://openrouter.ai/docs/faq),
[límites](https://openrouter.ai/docs/api-reference/limits),
[provider routing](https://openrouter.ai/docs/guides/routing/provider-selection),
[structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs),
[provider logging](https://openrouter.ai/docs/guides/privacy/logging),
[modelos](https://openrouter.ai/api/v1/models).

### Política de uso del modelo (`inf`)

1. **Solo cuando falla lo determinístico:** texto libre del bot (B-D040),
   atributos ambiguos de un anuncio, resumen de un tema. Nunca para calcular
   precios, costos o bandas.
2. **Caché por hash del texto** en DynamoDB: el mismo anuncio nunca se paga dos
   veces.
3. **Dos niveles de privacidad:**
   - Texto público (anuncios, titulares): modelo `:free` con respaldo al pago
     más barato.
   - Cualquier dato personal (mensajes de colaboradores o vendedores): solo
     modelo pago con `zdr: true` y `data_collection: "deny"`. Mejor aún, no
     enviarlo: seudonimizar o extraer con reglas primero.
4. **Esquema JSON estricto,** `temperature: 0`, `max_tokens` bajo, sin modos de
   razonamiento. Si la salida es inválida, se vuelve a la regla determinística o
   se marca "incierto"; no hay reintentos en bucle.
5. **Topes:** comprar $10 una vez (pagas $10.80 por la comisión mínima) habilita
   1 000 solicitudes gratuitas diarias. Límite de gasto en la clave de API
   (`pend` verificar opción) y en la configuración del radar.
6. **Modelo fijado y evaluado:** comparar 2–3 candidatos con fixtures en español
   venezolano ("Bs", "verdes", "pago móvil", tallas, condición). Elegir por
   aciertos por dólar, versionar el ID del modelo y usar una lista de respaldo
   (Fase 4).
7. Clave de OpenRouter en SSM; contenido de prompts fuera de logs.

## Propuesta de arquitectura optimizada

Instrucción del usuario (2026-10-03): leer toda la investigación y el plan,
optimizar, proponer arquitectura, interconexiones, modularización y testing.
Documento completo: [architecture-proposal.md](agent-b/architecture-proposal.md).

Resumen:

- **Diagnóstico:** había tres mecanismos de cifrado de sesiones, tres lugares para
  leases, el CAS de S3 presentado como protección de envíos, dominio en riesgo de
  duplicarse en Node/Go y contratos solo en prosa.
- **Arquitectura:** monolito modular hexagonal en Python (un paquete) desplegado
  como `bot` + `app`; workers delgados `browser` (Node) y `whatsapp` (Go); CLI
  `sessions` (Go, age). Tres colas FIFO + `results`; DynamoDB tabla única para todo
  lo mutable; S3 para blobs inmutables; SSM para secretos.
- **Modularización:** `domain/` (núcleo general + verticales), `application/`,
  `ports/`, `adapters/{aws,local,telegram,openrouter,workers}`, `entrypoints/`;
  `contracts/` con JSON Schema compartido; `go/` con un módulo para WhatsApp y
  vault; `lab/` se conserva como arnés de benchmark.
- **Interconexiones:** sobre común con `id` idempotente, `session_ref`, lease y
  deadline; errores tipados; efecto externo con ledger y fencing verificado por el
  worker (§5.3).
- **Testing:** pirámide con niveles 0 (dominio, arquitectura, contratos en tres
  lenguajes), 0.5 (flujo completo en proceso), 1 (conformidad de adaptadores
  contra DynamoDB Local/moto, Telegram, workers), 2 (fallos), 3–5 como en
  LOCAL_LAMBDA_TESTING, más evaluación de calidad aparte.

## Plan de implementación y trabajo en paralelo

Instrucción del usuario (2026-10-03): revisar la arquitectura final de A,
generar los checklists de inicio a fin y buscar una metodología para que A y B
trabajen a la vez sin chocar. Documento completo:
[implementation-plan.md](agent-b/implementation-plan.md).

- **Dictamen sobre la [arquitectura final de A](architecture-final-review.md):**
  aceptada como base. B retira de su propuesta el lease en el mensaje, la
  ausencia de outbox, el ledger de contacto absoluto y DuckDB/Jinja en el MVP.
  Añade cuatro refinamientos:
  - R1: Streams consume los comandos directamente.
  - R2: techos gratuitos con concurrencia reservada y límite de la clave
    OpenRouter.
  - R3: nombres generales del núcleo desde ya.
  - R4: "HTTP primero" sujeto a capacidades y permisos.
- **Metodología:**
  - Trunk-based con ramas cortas.
  - Un agente, un worktree, una rama, una tarea.
  - Contratos y puertos fusionados primero.
  - Dueños por puerto y archivos calientes con un solo dueño.
  - Tablero de reclamos, flags y merge serial por el coordinador.
- **Checklists F0–F15** con dueño A/B y marcas [U] donde decide el usuario.

**Respuestas del usuario (2026-10-03, "Sí los 3"):**
1. Autoriza el paso 0: commit de la línea base en `codex/baseline-research-lab`
   y merge local a `main` (B-D064). Push no incluido.
2. Confirma la arquitectura final de A con los refinamientos R1–R4 (B-D061,
   B-D062).
3. B-Q006 → **sí**: si una red rechaza sesiones usadas desde AWS, se permite
   ejecutarla desde el PC del usuario como respaldo, sin proxies ni evasión.

El coordinador debe registrar estas tres respuestas en `decisions.md`.

## Diseño propuesto: referencias, leases y revocación

Propuesta original, para host persistente. Ver el diseño Lambda arriba.

```text
SessionRef(platform, account_alias, backend, kind, version)
kind ∈ chrome_profile | storage_state | wa_device | matrix_login
```

- **El material se queda donde nace.** `chrome_profile` vive en el host del
  gateway y nunca se exporta. `wa_device` vive en la base del gateway WhatsApp.
  `matrix_login` vive en PostgreSQL del bridge. Solo `storage_state` viaja, cifrado.
- **Workers piden operaciones, no secretos.** Un worker manda
  `(SessionRef, operación)` al gateway dueño de la sesión. Solo los workers
  efímeros de `storage_state` reciben el blob, y solo con lease vigente.
- **Lease por cuenta con fencing token.** Un escritor por `(platform, account)`;
  lease con TTL y token monotónico. El gateway rechaza operaciones con token
  viejo. Solo el titular del lease escribe de vuelta un `storage_state`
  renovado, con compare-and-swap sobre `version`. Implementación local: fila
  SQLite o lock de archivo; en AWS, escritura condicional en DynamoDB (`inf`).
  Desactivar el autoguardado con merge de Browser Use si se usa.
- **Transporte.** Gateways en loopback. Si un worker está en otra máquina: túnel
  SSH, WireGuard o mTLS; nunca exponer CDP, el daemon OpenCLI ni WAHA a internet.
  WAHA con `X-Api-Key` en cabecera.
- **Cifrado en reposo.** `storage_state` cifrado (AES-256-GCM de agent-browser o
  equivalente); clave fuera del directorio de datos (variable de entorno local o
  gestor de secretos). Costo del gestor de secretos en AWS `pend`.
- **Revocación real ≠ borrar archivo.** Borrar el blob o rotar la clave impide
  usos futuros de esa copia, pero no invalida la sesión en el servidor. Revocación
  efectiva: cerrar sesión en la plataforma (desvincular dispositivo en WhatsApp,
  "dónde has iniciado sesión" en Meta/X). Por eso se minimizan las copias.
- **Renovación humana.** OpenCLI exit 77, desvinculación de WhatsApp o fallo de
  credenciales del bridge → `needs_reauth` y aviso. Sin login automático con
  contraseñas guardadas para redes sociales, aunque agent-browser tenga vault.

## Propuestas de decisiones

| ID | Propuesta | Motivo / fuente |
|---|---|---|
| B-D001 | Lectura de redes con OpenCLI sobre perfil Chrome dedicado (no el de uso diario); `SessionRef.kind=chrome_profile`, sin export | B-F001, B-F005 |
| B-D002 | WhatsApp: adaptar el fork local whatsmeow (loopback) como primer gateway; WAHA (GOWS/NOWEB + PostgreSQL) solo si hacen falta varias sesiones HTTP; nunca dos gateways sobre el mismo dispositivo | B-F008–B-F010 |
| B-D003 | Messenger/Instagram vía mautrix-meta solo como servicio separado experimental, tras aceptar el stack Matrix y revisar AGPL; cookies pegadas manualmente por el usuario | B-F011 |
| B-D004 | `storage_state` (Playwright con `indexedDB: true` o agent-browser cifrado) solo para sitios con prueba de import/export superada | B-F004, B-F006 |
| B-D005 | Lease por cuenta con fencing token; solo el titular escribe estado renovado | Diseño, B-F007 |
| B-D006 | Lambda solo coordina y ejecuta lecturas efímeras; gateways en host persistente (PC local primero) | B-F012 |
| B-D007 | Excluir Browser Use Cloud, Browserless y herramientas anti-detect; Browser Use OSS no entra en el MVP | B-F007, B-F013 |
| B-D008 | Mapear códigos de salida y eventos de backend a estados de A (`needs_reauth`, `timeout`, `empty_verified`, `unsupported`) | B-F001 |

Revisión tras las respuestas del usuario (2026-10-03). Prevalece sobre la tabla
anterior; B-D005, B-D007 y B-D008 siguen vigentes.

| ID | Estado | Propuesta | Motivo |
|---|---|---|---|
| B-D001r | Sustituye B-D001 | Lecturas en Lambda: Chromium + Playwright `storageState` cifrado; reutilizar adaptadores OpenCLI en modo CDP local a la invocación, empezando por Threads | B-F004, B-F014 |
| B-D002r | Sustituye B-D002 | WhatsApp en Lambda: whatsmeow conectar-sincronizar-desconectar, SQLite en S3, dispositivo vinculado propio del radar; el bridge local sigue siendo de uso personal | B-F015 |
| B-D003r | Sustituye B-D003 | mautrix-meta rechazado para el MVP; DM de Messenger/Instagram `unsupported` | B-F016, respuesta del usuario |
| B-D004r | Sustituye B-D004 | `storageState` pasa de opcional a formato principal del navegador | Lambda no conserva perfiles |
| B-D006r | Sustituye B-D006 | Sin gateways persistentes; todo worker es una invocación con lease | Respuesta del usuario |
| B-D009 | Nueva | SQS FIFO por `plataforma:cuenta` + lease y CAS con escrituras condicionales S3; clave en SSM estándar | B-F017 |
| B-D010 | Nueva | El adaptador Threads, autoría del usuario, puede entrar al repo bajo Apache-2.0 con fixtures sintéticos | B-F003, respuesta del usuario |
| B-D011 | Nueva | Alta y renovación de sesiones solo por comando local manual; Lambda nunca inicia sesión | AGENTS.md, SECURITY.md |
| B-D011r | Matiza B-D011 | WhatsApp: el emparejamiento corre en Lambda, pero el usuario escanea el QR en persona; la función nunca autentica sola | B-F019, coherencia de origen |
| B-D012 | Nueva | Piloto WhatsApp con número dedicado, no el personal | B-F018 |
| B-D013 | Nueva | Piloto por fases P0–P4; P2 solo recepción; P3 con aprobación humana por mensaje | B-F018, AGENTS.md |
| B-D014 | Nueva | Mitigación solo con medidas legítimas de la tabla; excluidos proxies, anti-detect, CAPTCHA y rotación de cuentas | Encargo, SECURITY.md |
| B-D015 | Nueva | Sin NAT/IP fija por defecto; reconsiderar si P2 mide reautenticaciones atribuibles a cambio de IP | B-F020 |
| B-D016 | Nueva | Alta por Telegram con código de emparejamiento; QR solo como alternativa | B-F021, B-F022 |
| B-D017 | Nueva | Implementación: Lambda Go + whatsmeow directo; Telegram por HTTPS; sin GOWA/wuzapi/WAHA en producción (sirven solo como referencia de código) | B-F023, Lambda bajo demanda |
| B-D018 | Nueva | Un SQLite y un lease por usuario; verificar que el JID vinculado coincide con el número declarado | B-F021, B-F022 |
| B-D019 | Nueva | Consentimiento explícito antes de emparejar; `/stop` ejecuta `Logout` y borra datos | B-F024 |
| B-D020 | Nueva | No conservar historial sincronizado salvo chats habilitados por el usuario | B-F024, SECURITY.md |
| B-D021 | Nueva | Terceros (P4) bloqueados hasta cumplir "Antes de multiusuario o cloud" y cerrar revisión ToS/legal | B-F024 |
| B-D021r | Matiza B-D021 | P4 limitado a colaboradores en Venezuela, con consentimiento versionado, solo chats habilitados y revisión de abogado venezolano antes del primer colaborador | B-F026 |
| B-D022 | Nueva | Canal de alta: Telegram principal; respaldo por WhatsApp desde la cuenta piloto (envío aprobado) o código dictado por el usuario | B-F027 |
| B-D023 | Nueva | Descartar en memoria los mensajes de chats no habilitados; no persistirlos ni registrarlos | B-F026 arts. 20–22 |
| B-D024 | Nueva | Adoptar B-I01–B-I10 y B-I13 en las fases indicadas; B-I11, B-I12 y B-I14–B-I17 como evolución | Funciones a adoptar |
| B-D025 | Nueva | Las salvaguardas de mensajería B-I09 son criterio de salida de P3 | B-I09, AGENTS.md |
| B-D026 | Nueva | Rechazar explícitamente la lista "No adoptar" | AGENTS.md, SECURITY.md, licencias |
| B-D027 | Nueva, requiere al usuario | Ampliar la visión de radar de productos a radar general de oportunidades; coordinador actualiza AGENTS.md, VISION.md y CONTEXT.md | Instrucción del usuario |
| B-D028 | Nueva | Núcleo común Entidad/Señal/Tesis/Evidencia/Escenario/Oportunidad/Acción/Resultado; verticales como módulos de cuatro funciones | Modelo general |
| B-D029 | Nueva | Orden: productos → temas virales (prueba de generalidad) → emprendimientos → empresas/contactos → acciones | Riesgo legal creciente |
| B-D030 | Nueva | Vertical acciones solo informativa: sin recomendaciones personalizadas, sin órdenes, revisión legal SUNAVAL antes de habilitarla | SUNAVAL, reglas de seguridad |
| B-D031 | Nueva | Contacto con empresas solo individual y aprobado; sin campañas ni correo masivo | AGENTS.md, B-F026 |
| B-D032 | Nueva | Memoria estructurada sin LLM: registro de eventos en S3 + memoria operativa en DynamoDB + blobs S3; Mem0/Graphiti/Letta no entran en el MVP | B-F034, B-F035 |
| B-D033 | Matiza B-D009 | Leases por escritura condicional en DynamoDB; S3 `If-Match` solo para publicar estado de sesión | B-F035 |
| B-D034 | Nueva | Hechos bi-temporales (`observed_at`, `valid_from`, `valid_to`) para precios y atributos | Graphiti, B-F034 |
| B-D035 | Nueva | Datos personales seudonimizados y cifrados con clave por persona; borrar la clave cumple `/stop` | B-F026 |
| B-D036 | Nueva | Informes: alerta, resumen, salud, aprendizaje y ficha; DuckDB + Jinja2 → Telegram (`sendMessage`/`sendDocument`), sin URLs públicas | Informes |
| B-D037 | Nueva | Informe de cobertura obligatorio en cada resumen | AGENTS.md |
| B-D038 | Nueva (decisión del usuario) | Telegram es la interfaz única del MVP; el dashboard web pasa a Mini App posterior | Usuario, B-F036 |
| B-D039 | Nueva | Lista blanca por `user_id`, roles propietario/colaborador y autorización en cada callback | B-F036, SECURITY.md |
| B-D040 | Nueva | Comandos y botones determinísticos primero; parser LLM opcional con confirmación | AGENTS.md (mínima dependencia LLM) |
| B-D041 | Nueva | Aprobaciones por teclado inline con idempotencia (`update_id` + escritura condicional) | B-F036, A-F16 |
| B-D042 | Nueva | HTTPS directo a la Bot API; aiogram (MIT) si crece; python-telegram-bot descartado por GPL-3.0 | B-F037 |
| B-D043 | Nueva | Comandos `/mis_datos` y `/borrar` desde el MVP | B-F026 art. 28 |
| B-D044 | Nueva | Adoptar la guía Telegram + Lambda como checklist de puesta en marcha (§5) y su tabla de actualización (§6) | Guía B |
| B-D045 | Nueva | Powertools for AWS Lambda (MIT-0) para idempotencia, lotes SQS, logs y parámetros en las funciones Python | Guía B §3.4 |
| B-D046 | Nueva | Lambda durable functions solo para flujos de varios pasos con espera larga (seguimiento de consultas); no para emparejar WhatsApp | Guía B §3.5 |
| B-D047 | Nueva | Invitaciones de colaboradores por deep link de un solo uso que agrega el `user_id` a la lista blanca | Guía B §2.3 |
| B-D048 | Nueva, requiere al usuario | Pasar la cuenta AWS a Paid plan antes del mes 5 y crear alertas de Budgets antes del primer despliegue | B-F038 |
| B-D048r | Sustituye B-D048 | Cuenta antigua (antes de 2025): sin cierre automático; diseñar solo sobre Always Free y crear alerta de Budgets (por ejemplo, $1 y $5/mes) antes del primer despliegue | B-F038, respuesta del usuario |
| B-D049 | Nueva | Nada encendido 24/7; región `us-east-1`; capa zip de Chromium antes que ECR; DynamoDB provisionado dentro del free tier | B-F039, palancas |
| B-D050 | Nueva | Logs con retención corta y sin métricas personalizadas; conteos desde el registro de eventos | B-F039 |
| B-D051 | Nueva (decisión del usuario) | IA vía OpenRouter con modelo pequeño, solo como respaldo de lo determinístico, con caché por hash | B-F040 |
| B-D052 | Nueva | Datos personales solo hacia endpoints pagos con `zdr: true` y `data_collection: "deny"`; modelos `:free` solo para texto público | B-F040, B-F026 |
| B-D053 | Nueva | Evaluar 2–3 modelos candidatos con fixtures en español venezolano antes de fijar uno | B-F040, Fase 4 |
| B-D054 | Sustituye cifrado de B-F017/diseño Lambda | age como único cifrado de sesiones; identidad age del worker en SSM SecureString | Propuesta §1 #1, lab/sessions |
| B-D055 | Sustituye B-D009/B-D033 en lo mutable | DynamoDB tabla única para leases, puntero de versión, ledger, idempotencia, deduplicación y memoria; S3 solo blobs inmutables | Propuesta §5.2 |
| B-D056 | Acepta A-F27/A-D010 (R-013) | Ledger antes del efecto, token de fencing verificado por el worker justo antes de enviar, `send_uncertain` + reconciliación; nunca reenvío automático | Propuesta §5.3 |
| B-D057 | Nueva | Dominio solo en Python; workers Node/Go sin lógica de negocio | Propuesta §1 #5 |
| B-D058 | Nueva | JSON Schema compartido en `contracts/` con ejemplos dorados validados en Python, Node y Go | Propuesta §5, §7 |
| B-D059 | Nueva | Estructura `src/radar/` hexagonal + `go/` + `workers/browser/` + `infra/sam/`; `lab/` queda como arnés | Propuesta §4 |
| B-D060 | Nueva | Pirámide de pruebas con nivel 0.5 en proceso y suites de conformidad de adaptadores | Propuesta §7 |
| B-D061 | Acepta arquitectura final A | Lease adquirido por el worker (no en el mensaje), outbox transaccional, HTTP primero, ledger de contacto por propietario/destinatario/propósito, informes JSON + Telegram en el MVP | [Revisión A](architecture-final-review.md) |
| B-D062 | Nueva | R1–R4 del plan: Streams como consumidor de comandos, techos gratuitos, núcleo con nombres generales, HTTP sujeto a capacidades | Plan §1 |
| B-D063 | Nueva | Metodología de trabajo paralelo de §2 y mapa de propiedad de §3 | Plan §2–§3 |
| B-D064 | Nueva, requiere al usuario | Paso 0: commitear la línea base y fusionarla a `main` antes de abrir worktrees | Plan §2.1, AGENTS.md |
| B-D065 | Nueva (pedido del usuario) | Reutilizar la clave de OpenRouter de inventarioIA por referencia al parámetro SSM `/inventarioia/openrouter_api_key` (`RADAR_OPENROUTER_KEY_PARAM`), sin copiar el valor; clave dedicada con límite mensual cuando se active la IA | [Inventario de secretos](agent-b/secrets.md) |

## Coordinación con A

Respuestas (2026-10-03):

- **A-Q001 → portabilidad real.**

  | Material | Qué conserva | Portable entre hosts | Backend que lo usa |
  |---|---|---|---|
  | Cookies JSON sueltas | Solo cookies | Sí, pero insuficiente si el sitio usa localStorage/IndexedDB | mautrix-meta (documentado como suficiente), Agent Reach/twitter-cli |
  | `storageState` | Cookies + localStorage + IndexedDB opcional + OPFS; no sessionStorage | Sí, salvo atadura a IP/dispositivo (`inf`) | Playwright, agent-browser, Browser Use |
  | Perfil completo | Todo, incluido service workers y caché | Débil: bloqueo de instancia única y cifrado ligado a directorio/usuario | OpenCLI (Chrome real), perfiles persistentes |
  | Sesión WhatsApp | Claves de dispositivo vinculado en SQLite/PostgreSQL | No compartible: copiar = clonar dispositivo | whatsmeow, WAHA |

- **A-Q002 → uso simultáneo y revocación.** Lease por cuenta con fencing token;
  los workers llaman al gateway en vez de recibir cookies. Revocar = cerrar sesión
  en la plataforma + borrar/rotar copias. Ver "Diseño propuesto".
- **A-Q003 → agent-browser y Browser Use.** agent-browser cifra estados con
  AES-256-GCM si hay clave; el vault siempre cifra, con clave autogenerada junto a
  los datos si no se define. Su camino determinístico no usa LLM (solo `chat`).
  Browser Use necesita LLM para `Agent`; la CLI no, pero queda fuera del MVP.
- **A-Q004 → Threads.** Adaptador local en `~/.opencli/clis/threads/`, no upstream
  (B-F003). Confirmar autoría antes de distribuir.
- **A-Q005 → headless ≠ serverless.** Headless es Chrome sin ventana; puede vivir en
  un host persistente. Serverless (Lambda) pierde el proceso al terminar la
  invocación. Sobreviven reinicios: perfil Chrome en disco (OpenCLI), SQLite o
  PostgreSQL de whatsmeow/WAHA, PostgreSQL de mautrix. Renovación humana: login en
  Chrome cuando caduca la sesión; QR de WhatsApp al vincular o tras 14 días sin
  usar el teléfono; cookies nuevas o CAPTCHA en mautrix-meta cuando Meta bloquea.

Preguntas de B:

- **B-Q001 (2026-10-03):** ¿quién escribió `~/.opencli/clis/threads`? Si fue el
  usuario o un agente para él, ¿se puede mover al repo con licencia Apache-2.0?
- **B-Q002 (2026-10-03):** ¿qué host aloja los gateways: el PC del usuario o una
  máquina siempre encendida? Cambia transporte, costo y renovación.
- **B-Q003 (2026-10-03):** ¿alguna capacidad de A requiere `storage_state` o todas
  las lecturas pueden pasar por OpenCLI? Si es lo segundo, B-D004 se aplaza.
- **B-Q004 (2026-10-03):** ¿aceptable operar mautrix-meta (AGPL) como servicio
  separado sin modificarlo? Necesita decisión del usuario.

Respuestas del usuario y preguntas nuevas (2026-10-03):

- B-Q001 → respondida: el usuario escribió el adaptador Threads (B-D010).
- B-Q002 → respondida: AWS Lambda bajo demanda (diseño Lambda, B-D006r).
- B-Q003 → resuelta por B-Q002: `storage_state` es necesario porque Lambda no
  puede usar el Chrome del usuario. A debe indicar qué lecturas no funcionan en
  modo CDP (sin pestañas múltiples).
- B-Q004 → respondida: solo si es gratis y fácil; no cumple (B-F016).
- **B-Q005 (2026-10-03, al usuario):** ¿se acepta un dispositivo vinculado nuevo
  de WhatsApp para el radar, o se prefiere apagar el bridge local y mover su
  sesión a Lambda? Las dos cosas a la vez no son posibles (B-F015).
- **B-Q006 (2026-10-03, al usuario):** si una red invalida sesiones usadas desde
  IPs de AWS, ¿se permite ejecutar esa red desde el PC como respaldo?
- **B-Q007 (2026-10-03, a A):** ¿qué adaptadores de lectura de la matriz A abren o
  cambian pestañas? Son los que pueden fallar en modo CDP.
- B-Q005 → respondida (2026-10-03): alta por QR, es decir, dispositivo vinculado
  nuevo para el radar. Pendiente B-Q008.
- **B-Q008 (2026-10-03, al usuario):** ¿el piloto usará un número/SIM dedicado o el
  número personal? Se recomienda dedicado (B-F018).
- B-Q008 → respondida (2026-10-03): el piloto usa una cuenta WhatsApp vinculada
  por escaneo; luego otras personas se vinculan vía Telegram.
- **B-Q010 (2026-10-03, al usuario):** ¿las personas que se vinculan son clientes
  externos que reciben un servicio, o colaboradores del usuario? Cambia el riesgo
  frente a la cláusula de ofrecer WhatsApp a terceros (B-F024).
- **B-Q011 (2026-10-03, al usuario):** ¿en qué país o países viven esas personas?
  Define la ley de protección de datos aplicable.
- B-Q010 → respondida (2026-10-03): colaboradores del usuario, que solo vinculan.
- B-Q011 → respondida (2026-10-03): solo Venezuela (B-F026, B-F027).
- **Respuesta a R-013 / A-F27 (2026-10-03):** de acuerdo. El `If-Match` de S3
  protege el estado, no el envío. B-D009 queda corregido por B-D056: ledger antes
  del efecto, fencing verificado por el worker y reconciliación de
  `send_uncertain`. Ver [propuesta §5.3](agent-b/architecture-proposal.md).
- **B-Q015 (2026-10-03, a A):** cuatro preguntas de integración en
  [propuesta §10](agent-b/architecture-proposal.md): age único, DynamoDB único
  almacén mutable, estructura de carpetas y actualización de ARCHITECTURE/AGENTS.
- **B-Q014 (2026-10-03, al usuario):** ¿la cuenta AWS se creó después del
  2025-07-15 y está en Free plan? Si es así, se suspende a los 6 meses (B-F038).
- B-Q014 → respondida (2026-10-03): creada antes de 2025, "creo". Free tier
  antiguo; confirmar en Billing → Free Tier (B-D048r).
- **B-Q013 (2026-10-03, a A):** X está restringido en Venezuela desde 2024.
  Lambda lee desde fuera, pero ¿la matriz A debe marcar redes que los
  colaboradores no pueden abrir sin VPN cuando haya que revisar un hallazgo?
- **B-Q012 (2026-10-03, a A):** registrar Telegram en la matriz A como canal de
  alta y notificación (Bot API oficial), no como fuente de ofertas.
- **B-Q009 (2026-10-03, a A):** A-F07 cubre la ruta oficial de WhatsApp. ¿Puede A
  estimar el costo por plantilla de primer contacto en el país objetivo, para
  compararlo con el riesgo de bloqueo de la ruta no oficial?

## Pruebas pendientes

Ninguna se ejecutó. Las que tocan cuentas reales requieren encargo separado.

1. Fixtures sintéticos: `SessionRef` sin secretos, lease con fencing token,
   compare-and-swap de `version`, mapeo de exit codes OpenCLI a estados.
2. `storageState` de ensayo con un sitio local: export, import en otro contexto,
   con y sin `indexedDB`; verificar que sessionStorage se pierde.
3. Perfil de ensayo copiado a otro usuario/host: ¿cookies descifrables? (B-F005).
4. agent-browser: estado cifrado ilegible sin clave; borrado a los N días.
5. Bridge WhatsApp local: `wa.py status`, reinicio, reconexión sin QR, comportamiento
   con bridge caído; confirmar bind en loopback.
6. Dos workers contra la misma cuenta: el segundo recibe rechazo por lease.
7. Revocación: desvincular dispositivo de ensayo y verificar `needs_reauth`.
8. Lambda: lectura efímera con `@sparticuz/chromium` y estado cifrado; medir
   arranque en frío y tamaño.
9. Lambda + OpenCLI `OPENCLI_CDP_ENDPOINT` contra Chromium local de la
   invocación: `threads search` con fixture local primero, luego cuenta de ensayo.
10. Sesión de navegador capturada en casa y usada desde Lambda: ¿la red la acepta,
    pide verificación o la cierra? Medir por red.
11. whatsmeow bajo demanda con dispositivo de ensayo: conectar, `OfflineSyncCompleted`,
    desconectar y subir con `If-Match`; repetir tras horas y días sin conexión.
12. Lease S3: dos invocaciones simultáneas sobre la misma cuenta; la segunda
    recibe 412 y no escribe estado.
13. Coherencia de origen WhatsApp: dispositivo emparejado en Lambda frente a uno
    emparejado en el PC y movido a Lambda; comparar tasa de `LoggedOut` en P2.
14. QR en Lambda: el QR vigente nunca aparece en CloudWatch y el objeto S3 se
    borra al terminar el emparejamiento o al expirar.
15. `PairPhone` desde Lambda: tiempo desde `/start` hasta código entregado en
    Telegram (debe caber holgado en ~160 s); código ingresado → `PairSuccess`.
16. Número declarado distinto al JID vinculado → desvincular y rechazar.
17. `/stop` → `Logout` → el dispositivo desaparece de "Dispositivos vinculados" y
    el objeto S3 se borra.
18. Webhook Telegram sin cabecera secreta o con una incorrecta → 401 sin efectos.
19. Driver SQLite elegido funciona con `sqlstore` en `provided.al2023` arm64.
20. Alta desde Venezuela: un colaborador con CANTV/Movistar/Digitel recibe el
    código por Telegram y lo ingresa dentro de la ventana; si falla, probar el
    respaldo por WhatsApp.
21. Chat no habilitado: un mensaje entrante no deja rastro en S3, logs ni métricas.
22. Usuario fuera de la lista blanca: respuesta neutra, sin efectos ni datos.
23. Callback de un objeto ajeno (otro colaborador): rechazado.
24. Mismo `update_id` dos veces y doble toque en `Aprobar`: un solo efecto.
25. Webhook lento: el acuse llega en la respuesta HTTP y el trabajo sigue en SQS.
26. Rich Message con tabla de oportunidades se ve bien en Android, iOS y escritorio.

## Pruebas y limitaciones

- Sin ejecuciones de navegador, bridge, WAHA, mautrix ni Lambda.
- Inspección local limitada a nombres de archivos, versiones (`go.mod`,
  `opencli --version`), código y documentación de skills; no se leyeron perfiles,
  bases de datos, cookies ni configuración con credenciales.
- Lecturas web vía GitHub API y Jina Reader; las páginas de Playwright,
  Browser Use, WAHA, mautrix, WhatsApp FAQ, Chrome y AWS se leyeron con éxito.
- Precios de alojamiento y gestor de secretos no verificados.
