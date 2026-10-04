# A19 — DNS público acotado, previo a HTTPS

Preparación local del asistente **general**, cualquier tema. Cierra el gap técnico
previo a TransportCall observado en A17, sin cambiar A8, A17, contratos/capacidades
de B ni A16. DNS no autoriza leer una fuente. No se ejecutó DNS de Internet, HTTP
externo, Docker, APIs, cuentas, cookies, cloud, IA ni descargas.

## API host explícita

[PublicDNSResolver](../../src/radar/adapters/sources/resolvers/public_dns.py) se
configura con argumentos nombrados: `helper`, `helper_sha256`, `specs` (tupla de
SourceSpec HTTP públicos sin sesión), `max_timeout_seconds=10`,
`max_dns_bytes=16384`, `max_total_dns_bytes=65536`, `max_queries=8`, `max_calls=4`,
`max_ips=16`, `max_output_bytes=2048`. Import/construcción no ejecutan DNS; el
constructor verifica sólo registro, ruta, hash y permisos de archivos.

`prepare(ReadRequest, cancelled=callable) -> PreparedRead` no ejecuta DNS. Devuelve
un nuevo request cuyo deadline es el menor de: original, hora UTC actual + timeout
de Limits, hora actual + límite host. `PreparedRead.request`, `.resolver` y
`.guard` quedan ligados a ese pedido. Resolver callable recibe sólo hostname, con
deadline monotónico/UTC y callback de cancelación inmutables; no resolver global
mutable ni deadline reiniciado por hostname. `.resolver.metrics` es metadata
técnica numérica sin hostname/owner/URL/PII.

[BoundPublicReader](../../src/radar/adapters/sources/resolvers/reader.py) es una
subclase adapter opt-in de A8, no otra implementación de Reader. Configuración:
`resolver=factory` y los argumentos existentes `registry`, `capabilities`,
`access`, `private_sink`, `transports`, `now`, límites del Reader. No permite otro
`url_guard`. Usa un ContextVar **por instancia**, asigna el PreparedRead dentro de
cada `read` y hace reset en finally. Concurrencia, read_many, redirects, pacing,
consentimiento, owners y almacenamiento siguen siendo los métodos/locks A8.
No crea un Reader nuevo por pedido que reinicie el throttle de la fuente.

El mismo request clamped llega al guard DNS y a TransportCall. A17 calcula plazo
HTTP residual, no un timeout nuevo después de DNS. Redirect vuelve a resolver y
revalidar allowlist/IPs, con el mismo plazo/cuota; no se reutiliza un contexto de
otra solicitud. No cambia el request original del caller. Quien use prepare sin
BoundPublicReader debe pasar **PreparedRead.request**, no el original, a su flujo.
El bridge no es presupuesto durable entre replay/restart de invocaciones.

A8 URLGuard actualmente convierte cualquier excepción del resolver a NETWORK.
`RequestURLGuard` reutiliza esa política, captura el fallo sólo en una variable
local a pin y reemite TIMEOUT/CANCELLED/LIMIT originales. No copia ni relaja el
guard. La capacidad/grant base siguen fallando antes de DNS/HTTP si no autorizados;
resolver/clase/helper/metadatos no crean una capacidad probado_real.

## Helper Go estándar y proceso propio

[Helper](../../helpers/public-dns/README.md), Go **1.27.1**, módulo propio sin
dependencias externas, go.sum ni SDK. El único entry productivo es
`cmd/resolve`, sin argv adicionales. stdin JSON técnico privado, máximo 1024 bytes:
v1, hostname, timeout_ms, max_ips, max_dns_bytes, max_queries. stdout JSON máximo
2048 bytes con hostname ligado a entrada, IPs canónicas y contadores numéricos.
No es un nuevo contrato público de B. Datos nunca se ponen en shell/argv, logs,
SQLite ni archivo intermedio. Versiones, unknown fields, tipos y duplicados se
rechazan; errores stderr son sólo códigos constantes.

Go usa `net.Resolver{PreferGo:true, StrictErrors:true, Dial:...}`, LookupNetIP con
context deadline y nombre DNS absoluto (sin sufijos de búsqueda). Producción usa
la configuración DNS del sistema y DialContext estándar, no endpoint/proxy elegido
por el pedido. Allowlist host ASCII exacta, labels acotados, sin IP literal,
userinfo, inyección, trailing dot o labels IDN `xn--` ambiguos. A8 vuelve a validar
hosts e IPs globales; privados/metadata/mapped/transición se rechazan antes de HTTP.
Una respuesta DNS válida no demuestra titularidad ni contenido verdadero.

Cada lookup limita DNS bytes de aplicación de lectura+escritura compartidos entre
A/AAAA, retries y TCP fallback, con adquisiciones de hasta 4096 bytes y reserva
concurrente de capacidad. Máximo 65536 bytes / 32 write attempts configurables;
resultado 1–16 IPs sin duplicados. Presupuesto de reserva puede fallar cerrado
antes de consumir todos los bytes físicamente posibles, especialmente con techos
muy pequeños y A/AAAA paralelos. El wrapper conserva el marcador PacketConn de UDP
para no introducir framing TCP accidental. No cambia protocolo/resolver Go.

Go puede realizar UDP-connect de selección de ruta RFC6724 al ordenar direcciones;
el código estándar `net/addrselect.go` indica que no envía paquetes en esos probes.
Son sockets dentro del hijo, no llamadas HTTP ni evidencia de disponibilidad.
No se contabilizan como payload DNS. El helper no hace forks; al finalizar o matar
y esperar su proceso, el SO cierra sus sockets/goroutines. No queda un getaddrinfo
irresoluble en un thread del proceso Python.

Python fija hash SHA-256 del binario real, tamaño máximo 16 MiB y archivo/parent
privados mediante verificaciones WinAPI SID/ACL y POSIX A14; rechaza symlinks,
reparse y hardlinks. No se cambia ACL de carpetas existentes del usuario. El host
prepara explícitamente sólo un nuevo leaf con create_private_directory y, en
POSIX, modo ejecutable privado 0700. Permisos/hash no comprobables fallan cerrado.
Protege frente a sustitución detectada antes de ejecutar; no promete resistencia
a un proceso host comprometido con el mismo owner que cambie archivo entre checks.

Pipes privados se drenan concurrentemente, stdout 2048 y stderr 256 bytes máximos,
lectura incremental de 512 bytes; exceso mata/espera sólo ese hijo. Un deadline
único incluye preflight/spawn/pipes/lookup; comprobación también después de spawn
tardío, sin dejar recurso creado tras timeout. Cancelación se consulta hasta cada
10 ms durante espera/drains. Cleanup síncrono tiene un presupuesto adicional único
de 1 s, no se devuelve éxito con cleanup/thread pendiente. Fallos OS de kill/reap
son fallos, no éxito inventado; no es una garantía hard-real-time bajo host colgado.

Windows requiere SystemRoot para Winsock. Se deriva con GetWindowsDirectoryW,
**no** del ambiente/tarea; es el único env suministrado. POSIX env vacío. No hereda
PATH, proxy, tokens, perfiles, credenciales o configuración arbitraria del proceso.
Cancel callback debe devolver bool y no bloquear; excepción/valor inválido falla
cerrado con error estático, sin reflejar su contenido.

## Presupuestos y qué mide la evidencia

Por pedido se reserva max_dns_bytes por lookup hasta max_total_dns_bytes; no se
reacredita tras error. Límite calls también se reduce a Limits.requests. Estos
contadores DNS son **separados** de requests/bytes_received HTTP de Report A8:
no son una cuota unificada de costo/proveedor, ni se agregan silenciosamente.
Calls son intentos lógicos, incluso preflight/cuota rechazada; queries son write
attempts del helper. dns_bytes/queries observados sólo vienen de respuestas de
helper válidas y exitosas. `usage_unknown=True` indica fallo tras intentar helper;
bytes de un proceso cancelado/fallido son desconocidos, no cero medido. Se conserva
la reserva conservadora. No son wire/IP/TLS ni bytes HTTP.

heap_alloc y heap_sys son snapshots de runtime Go, máximos entre respuestas, **no**
RSS, working set, memoria total ni peak de proceso. Python tracemalloc sólo mide
asignaciones Python trazadas. No existe techo RSS/costo cloud probado; DNS OS,
hosts file, runtime Go y procesos pueden usar memoria fuera de esos contadores.
Una lectura sintética no prueba una fuente, desempeño a escala o costo USD 0.

## Pruebas reproducibles y límites observados

[Tests Python](../../tests/source_resolvers/test_public_dns.py),
[bridge](../../tests/source_resolvers/test_dns_reader_bridge.py) y tests Go usan
sólo archivos nuevos privados efímeros, sin claves de cuenta,
y un servidor DNS UDP loopback real que nunca delega. cmd/synthetic y
internal/synthetic **no** se importan en entry productivo ni aceptan un endpoint
privado como parámetro de producción. Fixtures forged/slow/flood son explícitos.
El bridge usa HTTP/capabilities/private sink **scripted TEST ONLY** para probar
composición/residual/concurrencia; no los presenta como fuentes reales. TLS real
pertenece a las pruebas A17 congeladas, no se inventa un peer aquí como prueba HTTP.

```powershell
$env:PYTHONPATH='src;.'
$env:HYPOTHESIS_STORAGE_DIRECTORY='.local/hypothesis'
$env:GOPROXY='off'
$env:GOSUMDB='off'
$env:GOTOOLCHAIN='local'
$env:GOWORK='off'
python -m pytest -q tests/source_resolvers
python -m pytest -q tests
python scripts/check_docs.py
git diff --check
# En helpers/public-dns:
go test -mod=readonly -count=1 ./...
go vet -mod=readonly ./...
```

Se usa el venv A15 y caché Go existentes; no install/download. Compilación offline
de ambos entrypoints sólo a temporales propios; no binarios, corpus o certificados
en Git. Cobertura: protocolo DNS real loopback, bytes/queries/IPs limitados,
respuestas forjadas/duplicadas/desconocidas, malformed/IDN/inyección, owner/context
concurrentes y read_many, redirects frescos, throttle retenido, deadline residual,
cancel durante DNS y proceso lento, spawn tardío, kill/reap/pipes cerrados, ACL/hash,
stderr privado, cancel callback inválido y preflight base sin I/O.

Muestra sintética Windows 2026-10-04, tres lookups secuenciales: **594 bytes DNS
de aplicación**, **6 write attempts**, **0.406000 s** wall, tracemalloc peak
**262713 bytes**, snapshots máximos Go HeapAlloc **699952 bytes**, HeapSys
**8159232 bytes**. Excluye build/setup y no equivale a RSS ni p95/benchmark cloud.

Fallos de desarrollo conservados: **6 fallos/31 pases** por env vacío que rompía
Winsock Windows (repro fixture heredado funciona, vacío falla, SystemRoot WinAPI
único funciona), luego 38/38. Bridge inicial **7 fallos/39 pases** por fixture
Capability sin campo source, corregido sin cambiar A8/B; bridge 8/8 después.
Primera orden gofmt apuntó a path relativo incorrecto y falló; se corrigió el path,
sin atribuir aquel fallo a un formato válido. El primer go list de metadata también
se lanzó fuera del módulo y falló; repetido desde helpers/public-dns. No se bajaron
gates ni añadieron skips.

Resultados finales observados Windows, Go 1.27.1 windows/amd64, mismo código:

- Área **49/49** en **25.33 s**, suite completa **992 tests + 246 subtests** en
  **105.72 s**, cero skips/fallos. JUnit privado `.local/a19-area.xml` y
  `.local/a19-full.xml`, sin corpus de DNS/HTTP real.
- Go: tres tests principales + cuatro subcasos, **0.561 s**, `-count=1` offline;
  `go vet`, gofmt, `check_docs.py` y diff checks aprobados. Módulo sin dependencias
  externas; el grafo productivo sólo incluye su resolver interno y stdlib.
- No Node ni packages instalados, no contratos nuevos; no se infiere CI remota.
  Linux/POSIX y prueba independiente no se atribuyen a estos resultados Windows.

Verificación independiente, revisión B, factory general real, CI remota, fuentes
probado_real y canary autorizada siguen pendientes. Cerrar la resolución técnica
local no demuestra investigación de Internet, bot/WhatsApp operativos o fases
comerciales completas.
