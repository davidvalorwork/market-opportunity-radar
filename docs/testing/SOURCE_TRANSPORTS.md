# A17 — Transporte HTTPS público con sockets y TLS reales

Preparación local del asistente **general**, sin vertical comercial obligatoria.
Este incremento implementa un cliente real con la biblioteca estándar Python;
no registra fuentes, capacidades, sesiones ni cuentas. Se prueba exclusivamente
contra un servidor TLS de fixtures sintéticos en loopback. No se consultó Internet,
ni se ejecutaron Docker, APIs, IA, cloud, cookies, logins o acciones externas.

## API y frontera A8

[PublicHTTPTransport](../../src/radar/adapters/sources/transports/https.py) acepta
una tupla de `SourceSpec` de A8, todos `route='http'`, operaciones de lectura y
`requires_session=False`. La construcción/importación no ejecuta I/O.
La API existente es `read(TransportCall, cancelled=callable) -> RawPage` y
`SourceFailure(Code)` en fallos. No importa adapters desde application ni cambia
contratos, registro de fuentes, capacidades o motor A8.

Configuración explícita: `max_body_bytes=512*1024`, `max_header_bytes=32768`,
`max_line_bytes=8192`; sólo permite bajar esos techos. `TransportCall.max_bytes`,
timeout y deadline absoluto pueden imponer límites menores. Owner/actor/request,
operación registrada, límites y deadline se revalidan antes de abrir sockets;
La llamada no puede subir el techo bytes/timeout de `ReadRequest.limits`.
Cuentas/sesiones se rechazan y paginación por cursor no está implementada.

El wiring debe seguir usando `Reader` de A8, su autorización fresca, consentimiento,
presupuestos y capacidad **probado_real** para una ruta pública real. El atributo
`guarded_live=True` describe controles técnicos, **no** una fuente probada ni una
autorización. La prueba negativa con capability ausente devuelve `CAPABILITY`
antes del primer socket. No se presenta un fixture como capacidad real.

## Red, HTTP y límites

El transporte vuelve a validar el target mediante el guard público de A8, usando
únicamente la tupla IP ya fijada: no llama DNS, no toma proxy del ambiente y no
conecta a un hostname. Aunque el caller forje `PinnedTarget`, producción rechaza
IPs privadas, loopback, link-local, mapeadas y targets fuera de la allowlist.
Sólo HTTPS/443. Un intento a la **primera** IP autorizada por llamada; no hay
failover, retries ni solicitudes ocultas. Un fallo de conexión no intenta otra IP.

Host y SNI corresponden al hostname permitido; TLS exige verificación de cadena
y hostname, mínimo TLS 1.2, ALPN HTTP/1.1. El peer se obtiene de `getpeername()`
del socket conectado y se compara con la IP elegida, también después del handshake.
No permite inyectar context TLS inseguro, puerto o flag `allow_private` en la API
pública. El CA público del sistema sólo verifica certificados, no importa cuentas.

GET fijo, sin cookies/auth/body, `Accept-Encoding: identity`, `Connection: close`.
No sigue redirects, recursos HTML ni JavaScript. Devuelve Location privado para
que `Reader` vuelva a validar cada salto, grant y presupuesto. Una URL/cita válida
no prueba la veracidad del contenido ni identidad comercial de su autor.

Conexión, escritura, handshake, headers, framing chunked y cuerpo usan sockets
no bloqueantes y **un** deadline monotónico, limitado además por deadline UTC
absoluto. No se reinicia el reloj por cada read. Cancelación se consulta en cada
operación y durante select con intervalos de hasta 50 ms; error `CANCELLED`,
cleanup síncrono de sockets propios y sin hilo de I/O pendiente. El callback de
cancelación pertenece al host y debe ser rápido/no bloqueante. No hay cancelación
remota de solicitudes ya recibidas por un proveedor.

Headers se adquieren incrementalmente, sin makefile bloqueante: máximo 64 líneas,
8192 bytes por línea y 32768 bytes acumulados de líneas
(incluye status/interim responses/framing/trailers). Falta de CRLF/terminador,
framing ambiguo, Content-Length duplicado o junto a Transfer-Encoding se rechazan.
Cuerpo binario, incluso con NUL o bytes no UTF-8; no interpretación de texto.
Un buffer acotado permite prefetch incidental de cuerpo: máximo 4096 bytes, o
cuerpo máximo + 1 si menor; adquisición también limitada por línea/header
restantes + 1 y presupuesto global. No es un recv por byte ni prefetch ilimitado.
Content-Length excesivo se rechaza antes del cuerpo; streaming/chunked tienen
techo durante lectura. Encodings comprimidos y Transfer-Encoding distinto de
chunked no están soportados. TLS puede mantener buffers internos de su librería;
no se afirma ausencia total de copias en memoria ni borrado criptográfico RAM.
Ante presupuesto exacto sin margen para observar EOF, el transporte puede fallar
cerrado con LIMIT en vez de inferir finalización.

`bytes_received` cuenta bytes HTTP de aplicación realmente recibidos por SSL
(headers, framing y cuerpo) **más** bytes UTF-8 de la URL privada retenida como
procedencia. No cuenta wire/IP/TLS, bytes de request ni consumo/costo del proveedor;
no es un benchmark por invocación. El presupuesto incluye esa procedencia y no
permite superar el techo con headers. No se lee deliberadamente un cuerpo de error
para explicar el fallo; prefetch incidental acotado se cuenta y descarta. Status
401/403/429 y redirects se proyectan sin corpus.
Reader mapea 401/403 a REAUTH, 429 a RATE; Retry-After se normaliza a 1–300 segundos,
default 30 si inválido. Otros errores y excepciones se traducen a códigos constantes
sin URL/query, headers, tokens, stderr o raw exception en mensajes/logs/repr.

## Gap de resolución y cableado

A8 `URLGuard` llama su resolver **antes** de construir TransportCall; ese contrato
no recibe deadline ni cancelación. A17 no introduce DNS implícito ni edita A8 para
ocultar el gap. Un wiring público real debe aportar un resolver acotado/cancelable
y considerar esa fase en el presupuesto total; la evidencia de este incremento
cubre desde el target ya fijado hasta cleanup HTTP/TLS, no DNS bloqueante previo.
Registro/capacidad de una fuente externa, factory opt-in, revisión B, integración
y canary autorizada siguen pendientes. CLI/Agent Reach/browser son rutas distintas,
no acceso universal concedido por este cliente.

## Reproducción offline y evidencia

[Pruebas](../../tests/source_transports/test_https_transport.py) compilan
[generador Go estándar](../../tests/source_transports/tls_cert_fixture.go) en
`.local/a17-build`, sin módulos nuevos, downloads ni binarios Git. Fixture crea
CA/certificado/clave efímeros sintéticos y restringe sólo su nueva carpeta temporal
con ACL real del backend A14. No usa claves del usuario. La subclase loopback y el
bridge Reader existen **sólo** en tests, con `fixture_only=True`, sin guard público
desactivado en producción. Certificados y puerto local reales no acreditan una
fuente pública real. Peer observado es realmente loopback y su rechazo contra un
pin público también se prueba; no se sustituye con un peer falso como evidencia.

```powershell
$env:PYTHONPATH='src;.'
$env:HYPOTHESIS_STORAGE_DIRECTORY='.local/hypothesis'
$env:GOPROXY='off'
$env:GOSUMDB='off'
$env:GOTOOLCHAIN='local'
$env:GOWORK='off'
python -m pytest -q tests/source_transports
python -m pytest -q tests
python scripts/check_docs.py
git diff --check
```

Se usa el entorno A15 ya preparado con locks existentes, sin install. Dependencia
TZ `4c5e4be` integrada sólo en esta rama. Los tests abarcan IP fija/no DNS/SNI/Host,
cadena y hostname de TLS reales, binario/chunked, redirects sin follow, estados de
error, headers/cuerpo lentos, deadline absoluto, cancelación durante I/O, buffers,
framing inválido, pins privados forjados, privacidad y sockets/hilos de fixture
cerrados incluso tras fallos. Las pocas pruebas de socket stub se identifican como
unitarias de argumentos/failover; no son evidencia de TLS ni acceso externo.

Microbenchmark sintético observado en el mismo host, seis GET secuenciales con
headers de ~25 KiB (**151800 bytes HTTP** agregados, excluye URL retenida):

| Implementación | Adquisiciones recv | Pico buffer propio | Wall | CPU proceso |
|---|---:|---:|---:|---:|
| Inicial bytewise | 151800 | 1 byte | 0.515000 s | 0.515625 s |
| Final acotada | 42 | 4096 bytes | 0.063000 s | 0.062500 s |

CPU incluye threads del servidor sintético en el mismo proceso; wall excluye
compilación/generación de certificados. Es una muestra, no garantía a escala ni
medición de memoria total, wire/TLS, Lambda, red real o costo. El test exige un
techo estructural de adquisiciones/buffer, no una latencia que permita skips.

Resultados observados Windows, 2026-10-04, mismo código final:

- Área **73/73** en **4.73 s**, suite completa **943 tests + 246 subtests** en
  **76.74 s**, cero skips/fallos; JUnit privado `.local/a17-area.xml` y
  `.local/a17-full.xml` sin corpus de respuesta.
- `check_docs.py`, `git diff --check`, `git diff --cached --check`, gofmt y
  `go vet tests/source_transports/tls_cert_fixture.go` offline aprobados.
- Generador sintético Go Windows/amd64 compilado localmente con `-trimpath
  -buildvcs=false`, SHA-256 binario
  `8f82a51dbb30bf8363ed5563fb09422350e6db1da3a614973eca081f8bcdd45a`.
  No se versionan binarios ni claves. Ningún contrato/SDK/lock de A8/B cambió.
- Verificación independiente y POSIX/Linux pendientes; Windows no acredita esos
  resultados. No se ejecutó Node ni se instalaron paquetes; no hay cambios
  Node/esquemas y no se infiere CI remota.

Durante desarrollo la primera corrida
dio **22 fallos/31 pases**: faltaba `flush()` en el fp de sólo lectura usado por
HTTPResponse.close y cleanup ocultaba el resultado original. Se añadió la
operación requerida; después **53/53**, **65/65** y **70/70** pasaron, sin reducir
gates ni convertir fallos a skips. Recursos totales, costo y fuentes reales no se
midieron; sólo la muestra CPU/buffer sintética descrita arriba.
