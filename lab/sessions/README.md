# Laboratorio local de sesiones cifradas

CLI Go ejecutable para cifrar, compartir, importar y renovar un `storageState`
aportado explícitamente por el usuario. El ensayo por defecto usa exclusivamente
fixtures sintéticos en memoria y directorios temporales. No lee perfiles de Chrome,
no abre navegadores, no captura cookies, no hace login ni contacta plataformas.

Usa la librería oficial `filippo.io/age v1.3.2` (release verificada en GitHub), con
destinatarios X25519 de age. No instala el CLI age global. El Dockerfile fija
`golang:1.27.1-alpine` por digest verificado y conserva `go.mod`/`go.sum`; la
construcción verifica módulos y usa `-mod=readonly`. El runtime final es scratch, un binario estático y usuario
65532. El runner del repositorio registra el digest efectivo de la imagen.

## Ensayo sin red

Desde la raíz del repositorio:

```powershell
docker build -f lab/sessions/Dockerfile -t market-radar-lab/sessions:local .
docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m --memory 128m --cpus 0.5 market-radar-lab/sessions:local
```

La construcción ejecuta `go test ./...`. El CMD `selftest` produce una sola línea
JSON con `schema_version: 1`, `suite: sessions`, `fixture_only: true`, `status`,
`checks`, `check_timings_ms`, `timings.execution_ms`, `versions`, `cgroup` y
`useful_records`. Una falla devuelve código de salida 1. `useful_records: 2`
significa dos destinatarios que restauraron el bundle sintético; no dos cuentas
reales ni resultados comerciales. Métricas cgroup v2 incluyen peak/current de
memoria, memory.events y cpu.stat, o null cuando el host no ofrece el archivo.

Comprueba los dos destinatarios, tercero sin clave, ciphertext alterado, ausencia
de plaintext en ciphertext/telemetría, origen/dominio/alias/esquema/tamaño, keygen,
prepare/share/import/status, versiones inmutables, bloqueo, CAS perdido y
preservación de la versión anterior ante renovación fallida. IndexedDB se conserva
como JSON; este laboratorio comprueba transferencia, no restauración en navegador.
`event.synthetic.json` documenta el evento equivalente construido por selftest;
el ensayo no importa eventos ni rutas suministradas por terceros.

## Interfaz implementada

Cada comando produce JSON resumido, sin cookies, localStorage, IndexedDB ni claves.
Los errores son códigos constantes; no se imprimen diagnósticos con el input.

| Comando | Argumentos propios |
|---|---|
| `keygen` | `--vault RUTA` crea `keys/identity.agekey` y `keys/recipient.txt`; no sobreescribe |
| `prepare` | `--vault RUTA --alias SLUG --state-file JSON [--recipient-file TXT]...` crea versión 1 |
| `renew` | mismos argumentos + `--expected-version N`; crea N+1 por CAS |
| `share` | `--vault RUTA --alias SLUG --recipient-file TXT [--recipient-file TXT]... --out NUEVO.age` |
| `import` | `--vault RUTA --alias SLUG --bundle ARCHIVO.age [--expected-version N]` |
| `status` | `--vault RUTA --alias SLUG` devuelve `absent` o `unverified` y versión |

`prepare`/`renew` incluyen automáticamente la clave pública del propietario,
además de los destinatarios explícitos (máximo 16 en total). `share` cifra de nuevo
para exactamente los destinatarios indicados y no sobreescribe su salida. `share`
e `import` usan por defecto la identidad del vault; `--identity-file RUTA` permite
indicar otra identidad privada sin pasar la clave por los argumentos.

`--allowed-origin` por defecto es `http://localhost:8765`; fixtures aceptan
únicamente localhost/127.0.0.1/::1. Para estado real suministrado por el usuario,
hay que añadir explícitamente `--user-export --allowed-origin https://DOMINIO`
tanto al preparar como al compartir/importar. No se declara compatibilidad con
ninguna red social. Importar no verifica el propietario remoto ni la vigencia.

El `storageState` debe tener objetos `cookies` y `origins` como arrays, no un array
Cookie-Editor. Acepta cookies y localStorage con la estructura de Playwright, y
`indexedDB` como array opcional. Rechaza sessionStorage/campos desconocidos.
El alcance actual es **un solo origen**, sin path/query/fragment/credenciales en
su URL y con dominio de cookie exactamente igual al hostname. Cookies con dominio
inicial `.` o `partitionKey` se rechazan; muchos exports reales requieren una
adaptación posterior. No filtra silenciosamente cookies de otros dominios.
Tamaño máximo de plaintext/state: 1 MiB; bundle: 2 MiB. Aliases minúsculos de
1–48 caracteres, letra inicial y luego letras/dígitos/guiones; nombres reservados
de Windows, separadores, traversal, ADS y symlinks se rechazan.

## Dos vaults privados y un directorio de intercambio

Prueba completa **entre contenedores**, con PowerShell 7.2+ y la imagen ya
construida, desde la raíz del repositorio:

```powershell
pwsh -NoProfile -File lab/sessions/handoff.ps1
```

Un comando ejecuta keygen de owner/worker/tercero, prepare/share/import/status de
versión 1 y renew/share/import/status de versión 2. Comprueba rechazo del tercero,
CAS obsoleto, replay de la versión vieja y ausencia de activación tras rechazo.
Cada operación corre en un contenedor nuevo con `--pull never`, red deshabilitada,
filesystem read-only, 128 MiB RAM/swap, 0.5 CPU, 32 PIDs, capabilities retiradas y
no-new-privileges. Los mounts de input/shared son read-only salvo publicación de
un `.age`; cada contenedor recibe únicamente su vault privado.

Cada operación tiene presupuesto de 30 segundos, reservando hasta 2 segundos
para limpieza, y todo el handoff comparte un deadline monotónico de 180 segundos.
El helper usa `ProcessStartInfo.ArgumentList`, sin shell, captura stdout/stderr y
asigna un nombre UUID propio a cada contenedor. En `finally` cancela el cliente
si hace falta y ejecuta `docker rm --force` únicamente para ese nombre; nunca
borra volúmenes, vaults o contenedores ajenos. Si Docker no acepta o no completa
la limpieza dentro del presupuesto, la prueba falla; no promete limpieza bajo
un daemon inaccesible ni borrado seguro. `upload.ps1` usa el mismo límite de
30 segundos por operación. No expone diagnósticos ni excepciones en su salida.

Validación AST sin Docker ni acceso a vaults:

```powershell
pwsh -NoProfile -File lab/sessions/verify-scripts.ps1
```

Comprueba sintaxis, ausencia de Docker invocado por shell/borrado del host y uso
de ArgumentList, UseShellExecute=false y esperas con timeout. Es verificación
estática complementaria; incluye regresión del resolver con dos coincidencias
simuladas de PATH y rechazo de rutas concatenadas. El resolver selecciona un único
archivo existente sin interpretar argumentos. No sustituye el handoff ejecutado.

Fixtures explícitos versionados bajo `fixtures/` se copian a
`.local/session-handoff/UUID/input`. Los vaults owner/worker/outsider permanecen
separados. `shared/` contiene exactamente el recipient público del worker y dos
`.age`; ninguna clave privada. El script imprime únicamente un JSON de checks,
versiones/tiempo/directorio de artifacts y devuelve exit 1 si falla. No imprime
stdout/stderr crudo de Docker ni secretos. Conserva todos los artifacts sintéticos
para inspección/reproducción; no borra directorios ni ejecuta docker prune.
No verifica ACL de Windows ni restaura sesiones en un navegador.

Wrapper para un export real **suministrado explícitamente** por el usuario, con
vault ya inicializado mediante keygen:

```powershell
pwsh -NoProfile -File lab/sessions/upload.ps1 -Action upload -StorageState C:/ruta/privada/state.json -Vault C:/ruta/privada/owner-vault -Alias cuenta-demo -AllowedOrigin https://example.com -RecipientFile C:/ruta/publica/worker-recipient.txt
pwsh -NoProfile -File lab/sessions/upload.ps1 -Action renew -StorageState C:/ruta/privada/nuevo-state.json -Vault C:/ruta/privada/owner-vault -Alias cuenta-demo -AllowedOrigin https://example.com -ExpectedVersion 1 -RecipientFile C:/ruta/publica/worker-recipient.txt
```

`upload.ps1` siempre añade `--user-export`, monta exclusivamente el archivo de
estado indicado y cada archivo público recipient por separado y no copia ni
elimina el export. No monta su carpeta ni lee perfiles. Produce únicamente
estado/versión/código de error; ambos comandos dejan la sesión `unverified`.
Compartir/importar al worker siguen siendo operaciones explícitas independientes.
La limitación de dominio exacto/origen único se aplica también al wrapper; no
autoriza captura/login ni certifica cookies reales. No ejecutar estos ejemplos
con cuentas reales como parte del ensayo sintético.

Ejemplo manual para Docker Desktop/PowerShell; no lo ejecuta selftest. Crear bajo
`.local/` los directorios `owner-vault`, `worker-vault`, `session-shared` y `session-input`.
`session-input/state.json` será un export local aportado por el usuario. No subir
este archivo ni sus bundles a Git, chat, artifacts o CI.

```powershell
$ownerVault = (Resolve-Path .local/owner-vault).Path
$workerVault = (Resolve-Path .local/worker-vault).Path
$sessionShared = (Resolve-Path .local/session-shared).Path
$sessionInput = (Resolve-Path .local/session-input).Path
$sessionImage = 'market-radar-lab/sessions:local'

docker run --rm --network none --read-only --mount "type=bind,source=$ownerVault,target=/vault" $sessionImage keygen --vault /vault
docker run --rm --network none --read-only --mount "type=bind,source=$workerVault,target=/vault" $sessionImage keygen --vault /vault
Copy-Item -LiteralPath .local/worker-vault/keys/recipient.txt -Destination .local/session-shared/worker-recipient.txt

docker run --rm --network none --read-only --mount "type=bind,source=$ownerVault,target=/vault" --mount "type=bind,source=$sessionShared,target=/shared,readonly" --mount "type=bind,source=$sessionInput,target=/input,readonly" $sessionImage prepare --vault /vault --alias demo-session --state-file /input/state.json --recipient-file /shared/worker-recipient.txt
docker run --rm --network none --read-only --mount "type=bind,source=$ownerVault,target=/vault,readonly" --mount "type=bind,source=$sessionShared,target=/shared" $sessionImage share --vault /vault --alias demo-session --recipient-file /shared/worker-recipient.txt --out /shared/demo-v1.age
docker run --rm --network none --read-only --mount "type=bind,source=$workerVault,target=/vault" --mount "type=bind,source=$sessionShared,target=/shared,readonly" $sessionImage import --vault /vault --alias demo-session --bundle /shared/demo-v1.age
docker run --rm --network none --read-only --mount "type=bind,source=$workerVault,target=/vault,readonly" $sessionImage status --vault /vault --alias demo-session
```

Estos comandos usan el origen localhost predeterminado: para un export real,
agregar los flags explícitos descritos antes. Los mounts privados del propietario
y del worker son distintos; el directorio compartido lleva únicamente recipient
público y `.age`. Las claves privadas permanecen bajo `keys/` de cada vault.

Renovar usa `renew --expected-version 1` con un nuevo archivo suministrado por el
usuario y los destinatarios requeridos; luego `share --out /shared/demo-v2.age`
e `import --expected-version 1`. Cada versión nueva es inmutable bajo
`sessions/ALIAS/N.age`; `registry.json` cambia mediante archivo temporal + rename.
Un worker que importa una versión vieja recibe `version_conflict`.

## Límites de seguridad y operación

Directorios 0700 y archivos 0600 se solicitan en Linux. En Windows/Docker Desktop
estas llamadas **no garantizan ACL privadas del host**. Este no es un vault de
producción para Windows; restringir permisos del host y evitar carpetas compartidas,
backups generales y sincronización. El propietario del directorio debe ser de
confianza; no hay aislamiento frente a otro proceso que controla el mismo vault.

El lock `.lock` serializa publicación local y falla cerrado si otro proceso lo
posee. No es un lease de navegador, no pausa un worker externo y no garantiza
exactly-once. Si hay crash, detener/verificar todos los procesos antes de recuperar
manualmente un lock; el programa no borra automáticamente locks viejos. Un crash
entre bundle y registry puede dejar un bundle huérfano: falla cerrado al reintentar,
sin sobreescritura. Revisar/reconciliar manualmente antes de recuperar.

age verifica integridad y acceso por clave; **no autentica quién creó el bundle**.
Recibirlo por un canal privado autenticado de un productor de confianza. El campo
producer_version es compatibilidad de formato, no firma del productor. La lista
de orígenes controla la validación local, no limita criptográficamente los poderes
de la cookie. Solo entregar bundles a consumidores propios de confianza.

No hay comando de revocación remota, retención automática, borrado forense,
captura/logins, lector de navegador, S3, AWS ni restauración Playwright en esta
suite. Eliminar un destinatario de una versión nueva no revoca copias antiguas;
invalidación/logout remoto requiere un flujo separado. El CLI conserva versiones
anteriores; gestionar retención explícitamente en el vault privado. El export
plaintext ya aportado permanece bajo control del usuario. Selftest borra solamente
su directorio temporal sintético; no se promete borrado seguro de SSD o memoria.
