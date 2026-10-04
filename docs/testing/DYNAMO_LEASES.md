# Leases DynamoDB — corte local A25 de F7

Implementación **solo de `LeaseStore`**, con cliente low-level boto3 inyectado.
No crea clientes, tablas, roles ni recursos; importar el paquete no inicia login,
ni lee perfiles/cookies, instala SDKs o hace red. No está cableado al bot/runtime
general ni habilita cloud. Recibir pedidos de cualquier tema no exige productos,
precios, localidades o cuentas sociales para usar este control plane.

## Contrato y configuración explícita

`DynamoLeaseStore(client=..., table_name=..., authorize=...)` requiere una tabla
existente con partition key `pk` y sort key `sk` de tipo String. Las claves son
`owner#<owner_ref>` y `lease#<session_ref>`. Las referencias se validan contra las
definiciones canónicas `common.v1`; no se acepta un ID numérico crudo como owner.
El host debe suministrar un cliente DynamoDB configurado **sin retries SDK**
(`total_max_attempts=1`) y connect/read timeouts mayores que cero y de hasta
10 segundos. No se presupone que los defaults de boto3 sean seguros para este
contrato: el constructor los rechaza. No hay loop de reintentos en el adaptador.

`authorize(owner_ref)` es obligatorio y debe devolver exactamente `True` desde
una política confiable, vigente y server-side; se evalúa antes de cada operación.
El string del owner por sí solo no demuestra autoridad. La integración de
identidad/consentimiento y permisos AWS/IAM todavía no existe en este corte.
La comprobación del callback y el write remoto no forman una transacción de
revocación de permisos ni cercan un efecto posterior en otro proveedor.

- `acquire`: un `UpdateItem` condicionado a inexistencia o expiración explícita;
  el epoch se incrementa con `if_not_exists(version, 0) + 1`.
- `renew`: CAS de worker, token, versión y expiración original, además de lease
  todavía vivo; actualiza expiración e incrementa versión.
- `is_current`: un `GetItem` por clave completa con `ConsistentRead=True`, igualdad
  del snapshot completo y expiración estrictamente mayor que `now`.
- `release`: CAS del snapshot, cambia solo expiración a un tombstone permanente;
  no elimina la fila ni reinicia el epoch. No hay `DeleteItem`, `Scan` ni atributo
  TTL. Un TTL futuro sobre estas filas rompería la monotonía y no debe habilitarse.

Tiempo interno: microsegundos UTC enteros calculados con aritmética de
`timedelta`, nunca `datetime.timestamp()`/float. Se rechazan relojes naïve o con
offset distinto de UTC y TTL no positivos. La comparación de expiración incluye
el límite exacto; un lease vencido no puede revivir mediante renovación.

Solo `ConditionalCheckFailedException` de botocore se traduce a `None`/`False`
(ocupado/CAS perdido). Throttling, errores de servicio, transporte y respuestas
ambiguas se propagan sin presentarse como contención. Un timeout puede seguir a
un write ya comprometido; el host debe reconciliar, no repetirlo automáticamente.
El adaptador no registra refs, tokens ni cuerpos. El host debe evitar debug logs
del SDK y redactar sus errores antes de proyectarlos a usuarios/telemetría.

## Pruebas locales reproducibles

Instalación fijada/CI pertenece al coordinador: extra opcional `aws`, extra de
pruebas `aws-test`, `requirements-aws-test.in` y lock separado. boto3 es opcional
en runtime; moto es **solo** laboratorio. El lock base no cambia.

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:RADAR_REQUIRE_AWS_TESTS = '1'
$env:AWS_EC2_METADATA_DISABLED = 'true'
python -m pytest -q -p no:cacheprovider tests/aws/test_dynamo_leases.py tests/conformance
```

Sin SDK/moto, la suite base marca este módulo **skipped** con explicación: no
acredita DynamoDB. Con `RADAR_REQUIRE_AWS_TESTS=1`, dependencias ausentes hacen
fallar la colección, no producen éxito falso. El entorno dedicado del coordinador
ejecuta esa ruta requerida. No se instala nada ni se consulta IMDS desde el test:
las fixtures crean clientes con credenciales **sintéticas explícitas**, dentro de
`moto.mock_aws`, y solo ahí crean una tabla efímera emulada.
Una fixture autouse acotada a este módulo bloquea resolución DNS, creación de
conexiones y `socket.connect/connect_ex`, también si moto deja de interceptar el
SDK. Una regresión comprueba que los cuatro caminos fallan antes de hacer red;
los patches se restauran al terminar cada test.

`tests/conformance/lease_cases.py` contiene los mismos seis casos de A5,
extraídos sin cambios de semántica e importados por SQLite y moto: exclusión,
release/reacquire monotónico, expiración exacta, renew/restart y snapshots
obsoletos, worker/token/version falsificados y aislamiento entre owners.
Outbox y recepción atómica siguen probándose solo en SQLite; no se presentan
como implementados en DynamoDB.

Regresiones SDK adicionales: autorización revocada antes de IO, UTC/microsegundo
incluso año 2400, expiración/session alteradas, dos clientes y CAS secuencial,
tombstone/epoch, errores condicionales frente a throttling/servicio y write
comprometido con respuesta perdida sin retry. Se registran llamadas sintéticas
para comprobar que únicamente se usan `GetItem`/`UpdateItem` indexados.
Recrear el adaptador/cliente conserva el estado de la tabla moto en esa fixture;
no equivale a reiniciar un servidor DynamoDB ni probar durabilidad AWS real.
No se afirma concurrencia distribuida ni equivalencia de moto con AWS.

Verificación del implementador, Windows, 4 de octubre de 2026: área **43/43**
(21 SDK/moto + 22 SQLite/conformidad), 10,80 s; suite requerida completa
**594 tests + 238 subtests**, 46,98 s, sin skips. Documentación/diff verdes.
En el entorno base sin SDK: **22 passed, 1 skipped** (3,20 s), sin atribuir ese
skip a conformidad DynamoDB. En ese mismo entorno con
`RADAR_REQUIRE_AWS_TESTS=1`, la colección falla explícitamente
`aws_test_dependencies_required` (prueba negativa esperada). No hubo ensayo AWS
real ni un ciclo red/green previo del adaptador: la primera área implementada
pasó; los guardrails negativos se ejecutaron después como pruebas explícitas.

## Resultados de este corte

El implementador congeló sus seis archivos en `89d0a4d`: 43 pruebas del área
(21 SDK/moto y 22 SQLite), suite completa requerida 594 tests + 238 subtests
(46,98 s), sin skips. La preparación privada del coordinador `fd974fb` añadió
solo extras/lock/CI y repitió la suite completa: 594 + 238 (47,30 s), sin skips.
Ambas revisiones independientes contra main `1d7e6a1` resultaron aptas para el
corte local; no autorizan integrar a main ni completar F7.

El coordinador construyó e instaló el wheel offline en un entorno nuevo. Con
`pythonpath` de pytest desactivado, `radar` se importó desde `site-packages`, no
desde `src`: 82 tests + 220 subtests (12,13 s), sin skips, sobre SDK/conformidad,
contratos y arquitectura. Los checks de arquitectura inspeccionan el repositorio;
no se presentan como prueba de ejecución cloud del paquete. El código/lock son
los de `fd974fb`; las notas posteriores solo actualizan documentación.

Docker fue ejecutado por el coordinador, no por el implementador. El primer
arnés con dependencias/fuentes montadas directamente desde Windows se canceló
tras más de 250 s, con progreso parcial y sin OOM observado: no es prueba verde.
Se mantuvieron el código y lock, pero se extrajeron archivos empaquetados a
almacenamiento temporal Linux. Ese ensayo aprobó 43/43 en 4,09 s (bloque Python
4,401 s), pico RSS del proceso 127880 KiB (124,9 MiB), bajo límites de 512 MiB y
1 CPU, filesystem raíz read-only y red deshabilitada. Se utilizó la imagen A2
existente, sin tocar los cinco contenedores ajenos; el contenedor propio se
autoeliminó. Extracción/arranque no están incluidos en los 4,401 s.

Es una muestra de conformidad local y del arnés, no p95 ni benchmark Lambda/AWS.
El informe del coordinador conserva versiones, límites y resultados separados.

## Límites y fuentes primarias

F7 no está completo: faltan UoW/outbox DynamoDB, reparación/Streams, S3/SQS/SSM,
IAM, canary autorizado, costos reales y pruebas de fallo del servicio. No se
ejecutó cuenta cloud, API/modelo, bot real ni envío. Costo USD y recursos
Lambda/RAM siguen **desconocidos**; tests locales no son un benchmark de AWS.

Referencias primarias de especificación y paquetes. El coordinador verificó
Condition Expressions y las versiones/licencias de PyPI; los demás enlaces son
referencias complementarias, no pruebas consultadas contra una cuenta AWS real:

- [AWS: Condition Expressions](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Expressions.ConditionExpressions.html).
- [AWS: UpdateItem](https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_UpdateItem.html).
- [AWS: GetItem](https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_GetItem.html).
- [AWS: TTL](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/TTL.html).
- [Botocore: Config](https://botocore.amazonaws.com/v1/documentation/api/latest/reference/config.html).
- [PyPI: boto3 1.43.108](https://pypi.org/project/boto3/1.43.108/) y
  [PyPI: moto 5.2.3](https://pypi.org/project/moto/5.2.3/) (licencias Apache-2.0
  verificadas por el coordinador; los paquetes fijados no se habilitan solos).
