# A28 — continuación y caché privada local

Preparación general de investigación sobre cualquier tema. No es un buscador,
adaptador de redes, permiso de acceso ni evidencia de cobertura de toda la web.
El coordinador conecta este módulo al runtime; esta entrega no modifica A8/A16.

## API y frontera de autoridad

`radar.application.research.cache` define `CacheBinding`, `CachePolicy`,
`CacheRecord`, `CachedRecord`, `CacheView`, `Reservation`, `PageResult` y
`CacheError`. `radar.adapters.local.research_cache.SQLiteResearchCache` requiere
el `SQLiteStore` A3 real, una vista A14 `worker:research` y
`authorize(binding, now)` que devuelva exactamente `True`. Se vuelven a comprobar
owner activo, actor, consentimiento y autorización antes de acceso/transacción
y después de descifrado/cifrado. El host debe verificar además capacidades,
cuenta, sesión, versión, lease y reloj actual: la política no los concede.

`CacheBinding(owner_ref, actor_ref, task_ref, source_ref, query_sha256,
account_ref=None, session_ref=None, session_version=None)` liga la investigación
estable. Cuenta/sesión/versión son opcionales solamente como conjunto completo.
Un nuevo pedido confirmado puede continuar la investigación anterior únicamente
si el host selecciona explícitamente ese binding, no por referencia del LLM.

Operaciones, con `binding` posicional y los demás argumentos por nombre:

- `current(binding, now)` devuelve versión y contadores.
- `open(binding, pass_ref, mode, policy, now, expected_version=None)` inicia
  una pasada aprobada; `mode` es `continue`, `refresh` o `cache-only`.
- `reserve(binding, pass_ref, request_ref, expected_version, now,
  max_bytes=524288)` reserva una página/petición y bytes **antes** de I/O.
- `commit_page(binding, pass_ref, request_ref, expected_version,
  records=tuple[CacheRecord], continuation=bytes|None, received_bytes=int,
  now, report_bytes=bytes|None)` guarda resultado y checkpoint atómicamente.
- `drain(binding, pass_ref, expected_version, now)` entrega pendientes sin
  volver a consultar la fuente; no se permite durante una petición en curso.
- `abandon(binding, request_ref, expected_version, now)` marca I/O incierto,
  manteniendo bytes reservados como consumidos; no reintenta automáticamente.
- `recover_page(binding, pass_ref, request_ref, now)` recupera `PageResult`
  confirmado tras crash antes del checkpoint del host, incluso con cuota de
  items ya agotada. Revalida autoridad/binding/TTL; recibo ausente devuelve None
  sin reservar, reservado/incierto falla cerrado. No I/O ni consumo adicional.
- `seen_url(binding, url, now)` consulta índice fresco antes de que el host
  cree otro blob de A8. `lookup(binding, url, now, mode='continue')` devuelve
  `CachedRecord` fresco o `None`; `None` nunca autoriza un fetch.
- `read_record(binding, record_ref, now)` devuelve URL/cuerpo privados en memoria.
- `continuation(binding, now)` y `report(binding, now)` devuelven bytes privados.

`CacheRecord(url, content:bytes, observed_at)` es una entrada privada; su repr
oculta URL y contenido. `CachedRecord` lleva refs/digests/ordinal/timestamps.
`PageResult.records` son nuevos resultados **entregados**, no todos los vistos.
El host puede pasar como `report_bytes` JSON UTF-8 de un Report A8; este módulo
no interpreta ese contrato ni confunde report privado con datos de control.

## Más resultados, límites y repetidos

Cada `pass_ref` es una nueva cuota revisada por el host: defaults 100 **nuevos**
items, 10 páginas, 10 peticiones, 8 MiB, deadline UTC obligatorio, TTL 24 horas.
Topes de política: 5000/1000/10000/64 MiB/30 días respectivamente. Repetir el
mismo pass conserva sus contadores; cambiar política con el mismo ID se rechaza.
Otra pasada exige CAS de la versión corriente y conserva vistos/cursor/buffer.
`max_items` no es el total acumulado de toda la investigación: pedir más permite
otra cuota explícita, sin descartar el trabajo anterior.

La pasada informa `inspected`, `skipped`, `new`, páginas/peticiones/bytes. Los
registros nuevos sobrantes de una página quedan como refs pendientes. Se drenan
primero; el host no debe interpretar `new=0` como fuente exhaustiva. Un proveedor
sin cursor puede repetir búsquedas de estrategia; eso no es paginación nativa.
`seen_url` permite omitir URLs ya observadas en continue; refresh deliberadamente
relee para detectar cambios. Saltar una URL no demuestra cobertura completa.

Normalización conservadora: HTTP(S), host IDNA/lowercase, puerto default,
fragmento removido, path vacío `/`; query intacta (no eliminar parámetros que
puedan distinguir contenido). No sustituye SSRF/robots/permiso del transporte.
Huella con framing y owner + SHA-256; dedupe exacto URL/cuerpo por fuente y
cuenta/sesión/versión, con vistos por investigación. Un cuerpo cambiado es nuevo;
el mismo cuerpo no duplica ciphertext. Dentro de página también se deduplica.

Reserve cobra una página y petición; replay no las cobra dos veces. El host
debe pasar `received_bytes` del transporte real; el módulo valida mínimo de
cuerpos y máximo reservado, pero no mide la red. Fallo/timeout no se disfraza de
cero: reservation replay indica I/O ambiguo, nunca permiso para repetirlo.
Commit devuelve diferencia de bytes reservados no usados. CAS y transacciones
protegen contadores/seen/checkpoint; no convierten proveedor externo en atómico.

## Privacidad, espacio y caducidad

Seis tablas de índice guardan referencias opacas, fingerprints, versión,
contadores, timestamps y punteros cifrados: no query/URL/cuerpo/cursor/report en
claro. Un frame de registros por página, gzip nivel 3 antes de age; mismo cursor
y report se reutilizan si su hash no cambia. Inflado limitado a 1,047,552 bytes,
sin miembros gzip adicionales ni trailing bytes. Cuerpo+URLs+cursor+report por
commit ≤512 KiB, cursor ≤4096 bytes. No compresión adaptativa ni afirmación de
ahorro RAM sin benchmark; existe prueba pequeña de compresión/cifrado real.

Para índice compacto con archivos age externos, configure PrivateVault A14 sin
su `store` opcional: sus propias ACL/manifest siguen vigentes. Si el host habilita
el registro A14 en SQLite, puede conservar también ciphertext en ese backend;
esta entrega no cambia esa política ni promete almacenamiento externo exclusivo.

Cuotas default por owner: 5000 registros/128 investigaciones, 5000 pasadas y
50000 recibos de petición. Se rechaza más metadata al llegar al tope; no crece
indefinidamente. TTL bloquea acceso vencido y exige refresh; continue/cache-only
no renuevan TTL solos. Observación fresca renueva vigencia, sin duplicar cuerpo.
TTL es **frescura/acceso**, no borrado físico. GC seguro de índices/ciphertext y
huérfanos por rollback/crash queda pendiente del host; no se borra ningún archivo
compartido de vault. Cuotas A14 siguen siendo una segunda barrera.

## Verificación local y límites

Pruebas con SQLite real, helper Go/age real offline y claves sintéticas: restart,
cursor/report, nuevos presupuestos, pendientes, replay, dedupe, aislamiento de
binding, revocación, límites, CAS concurrente, TTL, gzip adversarial y marcadores
privados ausentes en SQLite/WAL/repr. No cuentas/cookies/red/modelos/AWS/Docker.
El helper se compila con módulos Go ya cacheados, `GOPROXY=off`.

Evidencia inicial `600f93b`: 35/35 pruebas del área (16.56 s), checker de
documentación y diff verdes. La suite completa con SDK obligatorio terminó
1316 passed +251 subtests y 8 fallos (443.14 s): una importación `urllib.parse`
en aplicación violaba el guard de capas. Se movió el parsing al adaptador sin
relajar el guard. Seis fallos fueron de timezone con `tzdata` ausente en ese
entorno antiguo; otro fue una aserción de tiempo/requests del puente DNS bajo
carga. No se modificaron esos módulos ni el entorno. El follow-up verifica
38 pruebas de caché y 31 de arquitectura, incluidas recuperación de commit
sin checkpoint del host y recepción incierta sin reintento. El coordinador hará
la validación completa conjunta; este corte no declara full verde.

No envío, transporte real, GC, métricas de red, cobertura global ni continuación
NL de un pedido anterior se proclaman operativos por estas pruebas. El host
integra selección de investigación, contadores globales y fuentes verificadas.
