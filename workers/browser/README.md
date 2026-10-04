# Worker de navegador A4

Implementación local exclusivamente sintética. Recibe `envelope.v1` o
`envelope.v2` con `browser.read.v1` y devuelve la misma versión de sobre con
`browser.result.v1`. Conserva operación, correlación, intento, alias y versión
esperada; causation es el message_id de entrada, y el resultado tiene ID nuevo.
AJV 2020 valida entrada y salida contra [schemas únicos](../../src/radar/schemas/).
No contiene lógica de dinero, equivalencias, oportunidades ni LLM. No guarda
resultados/outbox/ACK durables: ese cableado corresponde al adaptador de aplicación.

Solo el operador local puede habilitar `RADAR_LOCAL_FIXTURE_TEST=1`. El booleano
`fixture_only` del request no concede acceso. Se exige propietario
`user:fixture-owner`, sesión `browser:fixture`, versión esperada 1 y capacidad
`fixture.playwright.read` o `fixture.opencli.read` según modo. Son bindings de
ensayo, no autenticación de usuarios ni verificación de login real. Fuentes reales,
otras sesiones y blobs referenciados fallan antes de I/O. No hay cookies,
storageState, claves, rutas de perfil, secretos ni private_ref de usuario admitidos.

El target lógico permitido es `https://localhost/listings` (también se acepta
`http://localhost` como origen de entrada), sin puerto ni query. Nunca se conecta
a ese destino: cada invocación crea su propio servidor `127.0.0.1` con puerto
aleatorio y mapea la ruta. El registro de campos `source=synthetic_fixture` y la
URL de evidencia `https://localhost/listings?query=N` identifican ese fixture
lógico; no afirman una conexión TLS, origen externo, autenticidad o permiso real.
`content_hash` es SHA-256 de los bytes UTF-8 de `JSON.stringify(fields)` observados
en el DOM, con el orden de propiedades observado. No es un hash del HTML ni una
prueba criptográfica de identidad del proveedor. Los campos son strings crudos;
Python decide cómo interpretar moneda, cantidad, condición y precio publicado.

El lab queda intacto. Fixture, adaptador OpenCLI y preparación de assets se
derivaron del laboratorio y residen aquí para evitar una dependencia productiva
de `lab/`. Chromium se extrae durante build y queda en `/opt`, con binario 0555,
hash, tamaño y versión comprobados al abrirlo. CDP escucha en loopback con puerto
efímero; OpenCLI recibe el WebSocket de la página exacta, sin selector de tabs.
Contextos frescos y rutas limitadas a GET sobre las cuatro rutas de nuestro
fixture; service workers y WebSockets bloqueados. Red exterior deshabilitada
en Docker complementa las guardas del navegador.

La sesión fixture creada en memoria demuestra transferencia de cookie,
localStorage e IndexedDB, y ausencia de sessionStorage al restaurar. No importa
estado del usuario. OPFS, WebAuthn y sessionStorage portable están pendientes.
El punto futuro para sesiones reales será el helper Go age compartido por pipes
acotados, con autorización externa, hash/version/CAS y lease comprobados; no hay
otra criptografía en Node ni helper real conectado en A4.

Entrada/salida máximo 32 KiB, batch 1–10, trabajo máximo 60 s y plazo UTC absoluto.
El límite del contexto Lambda reduce el tiempo disponible; se reservan 9 s para
cierre. AbortSignal cancela operaciones y los recursos adquiridos tarde se liberan
antes de rechazarlos. Los procesos son grupos propios en Linux, sin shell;
buffers CLI acotados, entorno allowlist, stderr descartado. Errores estáticos
(`unsupported`, `blocked`, `session_conflict`, `timeout`, `needs_reauth`, `internal`)
sin URLs, entorno, excepciones o contenido privado. Un sobre inválido lanza
`invalid_input` estático antes de abrir recursos; no se inventa correlación para
transporte inválido. Un fallo tras una lectura útil devuelve `partial`; fallo de
cleanup o tamaño/salida inválida devuelve `failed` validado, sin registros.

Pruebas ligeras desde la raíz:

```powershell
npm --prefix workers/browser ci --ignore-scripts --no-audit --no-fund
node --test workers/browser/selftest.mjs
```

Build con Docker pesado previamente reclamado en BOARD:

```powershell
docker build --platform linux/amd64 -f workers/browser/Dockerfile -t market-radar/a4-browser:test .
docker run --rm --network none --read-only --cap-drop ALL --pids-limit 256 --cpus 1 --memory 1024m --memory-swap 1024m --tmpfs /tmp:rw,exec,nosuid,nodev,size=512m --entrypoint node market-radar/a4-browser:test --test /var/task/workers/browser/selftest.mjs /var/task/workers/browser/process-selftest.mjs /var/task/workers/browser/e2e.mjs
```

RIE se prueba adicionalmente con el entrypoint oficial, sin publicar puertos y
con las mismas restricciones. `RADAR_LOCAL_FIXTURE_TEST=1` habilita solo fixtures;
`RADAR_TEST_RIE=1` habilita dos tests que hacen POST a RIE por loopback dentro del
contenedor. No ejecutarlo contra endpoints arbitrarios. Los tests usan condiciones
de página lista, bindings, hashes y cleanup, sin sleeps de readiness ni skips.

Estado observado, 2026-10-03:

- Local Windows: 31/31 selftests, sin skips, ejemplos dorados válidos/negativos
  de B, sobres v1/v2, preflight antes de I/O, buffers, malformed output, stderr
  sintético, correlación y recursos tardíos tras timeout. Línea base `26d9ec3`:
  Python 382 tests + 238 subtests, Node contratos 221/221 en 22 schemas, ambos
  módulos Go test/vet del worker y check_docs aprobados. Lab sin cambios.
- Primer corte imagen `sha256:68bb49858b9210ef949484cd562120cda101d3799734e4ab5a65f431dd8b185d`:
  build aprobado, Docker 768 MiB 39/39 (31 ligeros + 8 E2E), RIE 10/10
  (8 runtime + 2 invocaciones RIE), sin skips. Lecturas directas v1/v2 3.62–3.69 s,
  OpenCLI 4.31–4.50 s; dos páginas sintéticas por caso, no p95 ni benchmark real.
  RIE observó memory.peak 530292736 bytes y CPU acumulado 28.145 s, para la suite
  completa con runtime/CLI de pruebas. No son RAM/CPU por invocación Lambda.
- La revisión añadió después cierre de grupos Linux incluso si el líder ya salió,
  escaneo de descendientes vivos (zombies excluidos como procesos ya terminados),
  y regresiones de un nieto vivo tras exit/timeout. Rebuild código final aprobado:
  imagen `sha256:8911d1f1dbcd86740d7d3b6621e7c50223900269713e18c7d8aae9d9807d5dbb`.
  Linux ligero final 256 MiB: 33/33 sin skips (31 ligeros + 2 descendientes),
  con `process-selftest.mjs` montado read-only porque se añadió tras ese build.
  En ese corte los tests Chromium/RIE finales quedaron pendientes: el guard detuvo el reensayo
  antes de crear un contenedor por RAM insuficiente del host. El éxito inicial
  no se atribuye automáticamente al nuevo código de limpieza.

- Gate final tras recuperar RAM, mismo día, código `078fd06` sin cambios:
  build desde ese commit incluye todos los tests y README. Imagen inspect ID
  `sha256:5a0f7556d640defc54c0ef6c249c6cadeec4288b12bb211f95689bb0e40672cc`;
  config `sha256:1fe7058f46f47f659ec4559bfeb1a198458240eb3feb82ba5e479819eb065b0c`.
  Docker offline **42/42** (31 ligeros + 2 procesos + 9 E2E), 24.461 s;
  RIE **44/44** (misma suite + 2 invocaciones RIE), 31.168 s, sin skips,
  fallos ni OOM. Comprobado cierre de contextos, perfil, servidor y descendientes
  vivos, también ante fallo/deadline; bindings y estado incompleto rechazados.
  1024 MiB RAM/swap, CPU 1, 256 PIDs, red none, read-only, cap-drop ALL,
  tmpfs `/tmp:rw,exec,nosuid,nodev,size=512m`, sin puertos publicados ni mounts.
  Host libre antes de cada ensayo: 7.530 y 7.306 GiB; guard mínimo 5.25 GiB
  conserva reserva de 4 GiB y margen del 25% sobre los 1024 MiB solicitados.
  Cgroup offline: pico 367222784 bytes, CPU total 23.731 s; RIE: pico 551809024
  bytes, CPU total 30.439 s. Son agregados de suites/runtime/CLI de pruebas,
  no métricas por invocación, perfil CPU de Lambda ni factura AWS.
  Reporte privado en `workers/browser/.local/reports/a4-final-078fd06.json`:
  stdout, configuración y manifiesto de hashes de archivos tracked de worker y
  schemas. SHA-256 del manifiesto JSON UTF-8 ordenado por path:
  `0a8d8b2b8f310c57f7262b84b84596a58d3dca4e3357101b044411d9b08c1467`.
  Identifica archivos observados en el build, incluyendo README de `078fd06`;
  esta actualización posterior solo cambia documentación, no código de la imagen.
- Verificación independiente del coordinador, reportada sobre la misma imagen
  `5a0f7556...` y el mismo código `078fd06`: offline **42/42**, 24.651 s;
  RIE **44/44**, 31.400 s, cero skips, fallos u OOM. RIE observó pico cgroup
  566951936 bytes y CPU total 30.846648 s, agregados de la suite. Conservó
  1024 MiB, CPU 1, 256 PIDs, red none, read-only, cap-drop ALL y guard de host
  mínimo 5.25 GiB. Tras detener su contenedor propio quedaron únicamente los
  cinco contenedores ajenos. No se reconstruyó ni cambió código para esta prueba.

Se preservó la reserva del host en todos los cortes. 768 MiB fue un experimento
explícito previo; el gate final sí se ejecutó a 1024 MiB. Contenedores propios
`--rm` eliminados; los cinco contenedores ajenos no se modificaron. Claude registró
aprobación condicionada al gate final en BOARD; los ensayos del implementador y
la verificación independiente del coordinador satisfacen esa condición sobre
el mismo código `078fd06`. La integración sigue pendiente.
No son métricas AWS, facturación cero ni cobertura comercial/social.
