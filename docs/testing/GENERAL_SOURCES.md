# A8: fuentes generales de lectura, límites y prueba offline

Estado observado: implementación local aislada, sin fuente real conectada, sin
cookies importadas, sin red, IA, contactos ni infraestructura. El pedido actual
del usuario es general; perfumes, vacantes, eventos y artículos son ejemplos,
no una lista cerrada de categorías. El alcance sustituye para este frente la
restricción temática anterior, sin ampliar permisos sobre cuentas reales.

## Piezas y ejecución

- [Registro y lector](../../src/radar/adapters/sources/generic/engine.py):
  `Registry` acepta fuentes con plataforma, backend, ruta y operaciones de
  lectura revisadas; `Reader.read` y `read_many` devuelven cobertura y referencias.
- [DTOs internos](../../src/radar/adapters/sources/generic/model.py): permisos,
  transporte, contenido efímero, salida controlada y backend privado inyectados.
  No son nuevos contratos públicos ni reemplazan los schemas de B.
- [Límite de red](../../src/radar/adapters/sources/generic/network.py): URL,
  DNS, IPs fijadas y comprobación de la dirección conectada.
- [Extracción](../../src/radar/adapters/sources/generic/extraction.py): candidatos
  de email/teléfono, hasta 64 desde un prefijo UTF-8 de 256 KiB. Son **no
  verificados**; no prueba de WhatsApp, intención de contacto ni autorización.
- [Fixtures](../../src/radar/adapters/sources/generic/fixtures.py): contenido
  inventado y almacenamiento privado sólo en memoria, **sin cifrado age**.

Desde la raíz del worktree, con Python y dependencias del proyecto disponibles:

```powershell
$env:PYTHONPATH = 'src'
python -m radar.adapters.sources.generic --fixture
python -m pytest tests/sources_generic -q -p no:cacheprovider
```

La CLI sólo admite `--fixture`; no acepta URLs, sesión, credenciales, comandos
ni modo live. Produce cuatro informes con referencias privadas y procedencia;
`real_sources_verified=false`. Los tests de HTTP también usan transportes
inventados: simular una capacidad `probado_real` **no verifica una fuente real**.

## Preflight y privacidad

Antes de cada I/O y escritura privada, la política del host debe dar un permiso
actual ligado exactamente a propietario, actor, fuente, operación, cuenta y
sesión. Es el servicio autenticado, no el string `owner_ref`, quien concede ese
permiso. Se requiere consentimiento actual, expiración UTC y, cuando procede,
sesión expresamente entregada, verificada y vigente. Sólo referencias de vault:
no lectura de Chrome ni autologin. No se almacenan cookies en estos DTOs.

El puerto existente `Capabilities.get` se consulta por plataforma, backend,
operación y sesión en cada paso. Ausencia, `documentado`, `doctor_ok`, fecha futura,
evidencia de más de siete días (configurable 0–30) o falta de autorización bloquean.
`probado_local` sólo funciona si el transporte es explícitamente fixture-only;
un backend real exige `probado_real` y un transporte guardado, además del permiso.
La verificación de capacidades se hace fuera del lector y por operación.

Refs opacas, sesión, clave de blob y audiencia se validan con los patrones
publicados en [common.v1](../../src/radar/schemas/common.v1.json). Nombres de
plataformas/backends/operaciones no son refs. Un teléfono o ID numérico crudo no
puede devolverse como ref; los alias deben ser emitidos por el host de confianza,
nunca incluir PII disfrazada con un prefijo.

`PrivateSink` es obligatorio. Producción debe autorizar el propietario, cifrar
con age **antes** de persistir, hacer referencias inmutables, aplicar cuotas y
comprobar integridad al leer. Devuelve `PrivateWrite(owner_ref, pointer)`: el owner
y la audiencia age no son lo mismo. `private_scope` fija la audiencia esperada
(por defecto `worker:sources`); `sha256` identifica el **ciphertext** en producción.
El sink sintético usa digest de bytes inventados; no usarlo con datos reales.

El contenido binario, URL de procedencia (incluido query) y candidatos de contacto
sólo están en el blob privado. Los DTOs sensibles no los incluyen en `repr`.
`Report.control()` usa una allowlist de refs, contadores, códigos y procedencia,
sin URLs, consultas, cursor, texto, emails o teléfonos. Nunca registrar excepciones
externas: el lector las convierte a `source_failure` sin copiar el mensaje.

## Transporte y SSRF: qué está probado y qué no

Las rutas `http`, `browser` y `cli` son **puntos de inyección**, no clientes live
implementados. No hay shell, `eval`, subprocess ni construcción de argv aquí.
El administrador revisa un `handler_ref` estático de lectura; el usuario/LLM
no registra operaciones ni puede seleccionar comandos arbitrarios. `effect=write`
es inválido y `twitter/x reply-dm` se rechaza aunque se etiquete como lectura.
Una clasificación `read` declarada no prueba por sí sola la semántica del handler.

El guard admite HTTPS/443, dominios ASCII en allowlist exacta y DNS público.
Rechaza user-info, IP literal, localhost, metadata/private/shared/multicast,
DNS mixto público-privado, IPv6 mapeado/de transición, backslash y controles.
Cada redirect consume presupuesto, resuelve y valida de nuevo. El transporte
debe fijar las IPs entregadas, conservar Host/TLS y desactivar redirects automáticos;
la dirección conectada debe coincidir con una IP autorizada. DNS previo o
`peer_ip` declarado por un transporte no confiable **no previenen rebinding**.

Antes de habilitar un backend real hay que probar pinning/peer desde el socket,
TLS, redirecciones, streaming limitado, timeout/cancel efectivo y DNS rebinding.
Para browser/CLI también todos los subrequests, descargas, WebSocket/service
workers y comandos hijos; el guard top-level no los controla ni certifica.
`guarded_live=true` es un gate de configuración confiable, no un certificado.
No debe enviarse una URL privada o con credenciales a Jina u otro proxy público.

## Presupuestos, ritmo, concurrencia y continuación

Defaults por lectura: 5 páginas, 10 requests, 2 MB recibidos, 200 registros,
10 segundos por I/O, 3 redirects y deadline UTC obligatorio. Caps configurables:
100/300/20 MB/2000/60 s/10 respectivamente. Un timeout consume request; decoded
items deben caber en bytes declarados. El transporte debe imponer el límite
**durante** adquisición: comprobar después no evita un buffer demasiado grande.
El reloj inyectado debe ser confiable; Python no mata un transporte que ignore
timeout/cancel. El backend live todavía debe demostrar interrupción efectiva.

`read_many` admite hasta 100 pedidos y usa hasta 4 threads por defecto (1–16).
Hay límite global, por fuente y serialización por `(owner, account)` entre fuentes.
Ritmo/cooldown de la fuente se comparte entre propietarios. Estas reservas son
locales al proceso; múltiples workers requieren leases durables del host.
Aliases distintos de una misma cuenta deben resolverse a identidad canónica
antes de entrar aquí. Son recursos acotados, no costo AWS cero garantizado.

La pausa mínima no usa sleep/retry infinito: una página siguiente demasiado
pronta devuelve `rate_limited` parcial. Un 429 fija cooldown 1–300 segundos sin
reintento automático; consultar durante cooldown no lo extiende. 401/403 devuelve
`needs_reauth`. Cancelar no lee otra página ni escribe nuevos registros.

Para página/budget/rate parcial, `resume_ref` guarda cursor y bindings en el
backend privado. El host espera cooldown y reintenta **explícitamente** con
`ReadRequest.resume_ref`; debe mantener owner/actor/source/op/cuenta/sesión,
query y target originales. No se reabre página1 si se completó y el cursor
indica página2. No se guarda continuación en respuestas completas, sin cursor
o sin permiso vigente. No se exporta el cursor a cola/log. Un fallo a mitad de
página puede requerir releerla: todavía **no hay** transacción checkpoint+records,
consumo único de resume, retry budget durable ni dedupe entre corridas. Eso es
gate de integración A6/A3, no una promesa de exactly-once.

Dedupe exacto usa hash con framing de longitud de URL+bytes (no sólo título ni
teléfono). Dentro de una corrida une procedencias de duplicados; misma foto/texto
con otra URL queda separado. Se conservan bytes binarios vía base64 privado.
`complete=true` significa que el transporte terminó su cursor declarado, **no**
que se haya leído toda una plataforma/web. Las fallas de una fuente no impiden
el informe independiente de las demás. `Report.code` es interno; su conversión
a los códigos wire publicados corresponde al adaptador de frontera.

## Gates para integrar y prueba pendiente

1. Integrar por un puerto de aplicación aprobado; no importar estos adapters
   desde `application/` ni cambiar schemas/catálogo de B silenciosamente.
2. Backend age/vault real autorizado y owner isolation/replay verificados.
3. Capacidad fresh `probado_real` para **cada** operación desplegada, con contenido
   no vacío, evidencia fechada y consentimiento correspondiente.
4. HTTP/CLI/browser live con límites y aislamiento de red demostrados.
5. Scheduler/continuación/cooldowns durables, dedupe entre corridas y reservas de
   cuentas entre máquinas. No automatizar renovar sesiones sin consentimiento.

Consultar el [inventario del coordinador](../research/general-source-verification.md):
Marketplace observado lista ventas propias, Instagram search busca usuarios,
Threads post es lectura y no se ha comprobado contenido en cuentas. Agent Reach
es la ruta preferida para seleccionar backend, no prueba de cobertura universal.
Este frente no altera el catálogo de B, no despliega Lambda ni manda mensajes.
