# A14 — Vault privado local con age real

Preparación aislada del asistente general, cualquier tema. Backend real offline,
no una interfaz de memoria ni prueba de Telegram/WhatsApp/cuentas reales. Rama
`codex/a-private-vault` desde A11/A12/A6 `ce2a4c4`; dependencia A9 `ea36afe5`
(incluye A8 `6324b94`) integrada **sólo en esta rama** para interoperación.
No modifica contratos, puertos, SDK, Go de B ni el laboratorio.

## Componentes y configuración explícita

[Helper técnico Go](../../helpers/private-vault/README.md),
[backend Python](../../src/radar/adapters/local/private_vault.py) y
[pruebas](../../tests/private_vault/test_vault.py). Usa age **1.3.2**, Go **1.27.1**,
los mismos `go.sum` y versiones transitivas del lab; build con caché existente,
`GOPROXY=off`, `GOSUMDB=off`, `GOTOOLCHAIN=local`, `GOWORK=off`, `-mod=readonly`,
`-trimpath -buildvcs=false` (sin metadatos Git variables dentro del binario).
No criptografía Python: SHA-256 verifica integridad; age cifra y autentica en Go.
Sin WhatsApp/GPL enlazado, modelos, red, Docker, AWS ni descargas.

`create_private_directory(absolute_new_leaf)` es setup **explícito** de una sola
carpeta nueva. No cambia ACL de padres, rutas del usuario ni carpetas existentes.
Se configura `PrivateVault(root, helper, helper_sha256, identity_file, recipient,
owner_ref, audiences, authorize, store=None, timeout=5, max_blobs=1024,
max_total_bytes=64 MiB)` mediante argumentos nombrados. Root y claves deben existir
y superar preflight privado. La identidad viene sólo de su archivo explícito,
acotado a 4096 bytes; viaja al helper por stdin. El helper se fija a su hash real.
No importa claves, tokens, cookies o cuentas del propietario.

`authorize(owner_ref=..., audience=...)` es una decisión fresca del wiring confiable
y debe devolver exactamente `True`. No es un campo que pueda suministrar una tarea
ni un LLM. Owner queda fijado por instancia y carpeta; audiencias son una allowlist
del host. Las vistas no conceden autoridad externa ni aprobación de destinatario.
Revocación/owner detenido impiden nuevas lecturas y publicaciones; age no revoca
copias de plaintext o ciphertext ya obtenidas. No es autenticación del productor:
quien posee la clave pública puede cifrar; el host exige autorización de escritura.
Cada instancia local usa un destinatario age del host por owner; las audiencias
se autorizan por vista y se verifican dentro del envelope, **no son llaves de
worker aisladas entre sí**. Quien tenga esa identidad fuera del backend puede
descifrar sus blobs. No se distribuye la identidad a workers; el cifrado para
destinatarios remotos distintos y el boundary de transferencia siguen como gates.

## Permisos y publicación

Windows: WinAPI (`GetNamedSecurityInfoW`, token/SID actual, DACL/ACEs) exige owner
actual, DACL no nula, ACE Allow propia y **ninguna** ACE Allow ajena salvo SYSTEM
`S-1-5-18`. Rechaza ACEs no soportadas. No usa nombres localizados, parse de icacls,
PowerShell, shell ni `chmod` como evidencia de ACL. Setup nuevo fija DACL protegida
con SID actual/SYSTEM e herencia privada; después verifica el resultado. Cada
operación verifica carpeta, identidad y archivos; reparse points se rechazan.
Permisos no comprobables → fallo cerrado; Windows no está deshabilitado por SO.

POSIX: owner efectivo, directorio privado sin permisos grupo/otros, archivos
regulares de un solo link, O_NOFOLLOW y dirfd anclado. Setup pide 0700/0600.
La prueba POSIX se ejecuta sólo al correr en POSIX; los resultados Windows no
acreditan Linux. En Windows se ensaya el rechazo de atributo reparse mediante
fixture sin afirmar que se creó un symlink privilegiado.

Sólo ciphertext age va al disco. Temporal exclusivo en la misma carpeta → flush
y `os.fsync` del archivo → publicación sin overwrite. POSIX: link exclusivo,
unlink temporal y fsync de directorio. Windows: `MoveFileExW` sin REPLACE_EXISTING
y con WRITE_THROUGH. No se equipara este primitivo Windows a un fsync POSIX de
directorio ni se afirma supervivencia a corte eléctrico físico. Todo error de
flush/publicación/registro falla cerrado: no entrega puntero de éxito. Un crash
tras publicación puede dejar ciphertext huérfano; no se inventa GC/borrado seguro.

## Bindings, cuotas y control

Nombre UUID opaco `.age`; sólo esa forma es legible, no paths/URLs externos.
Puntero `sha256` = hash de **ciphertext real**, nunca del texto. Dentro de age hay
un envelope propio binario+JSON versionado con owner, audiencia, mismo blob_key,
hash y tamaño del contenido. Al abrir comprueba todos; cambiar hash de transporte
no salta authentication age, y sustituir otro blob válido no salta key/owner/scope.
El envelope es técnico privado, no un nuevo contrato publicado de B.

Máximo plaintext de helper 1 MiB incluido envelope; máximo contenido backend
1 MiB − 1024 bytes. Máximo ciphertext 1 MiB + 4096. Tiempo de helper configurable
(0,10] segundos; pipes drenados concurrentemente y acotados, stderr descartado.
Timeout/overflow mata y espera sólo su hijo; cleanup adicional acotado. No shell,
forks del helper, plaintext en argv, raw exception/stderr en diagnósticos.

Máximo 1024 blobs / 64 MiB ciphertext por owner por defecto; configurable con
límites explícitos. `.vault.lock` guarda **sólo hashes de owner/recipient y cuotas** y es lock OS por
carpeta (Windows byte-range/POSIX flock), no un blob ni secreto. Dentro del lock
cuenta archivos/bytes existentes, incluidos huérfanos, antes de publicar. Cuotas
no se reinician con proceso/restart y concurrencia no permite sobrepasarlas.
Una carpeta ya ligada a otro owner no admite publicaciones. Límites o recipient
distintos entre procesos/restart se rechazan; configurar el mismo techo por owner.

SQLiteStore opcional: registra ciphertext, hash y scope, nunca corpus/plaintext.
`blobs(owner,key,hash,content)` conserva el índice que requiere `_private` de A6;
`private_vault_refs` conserva scope. Registro inmutable/comprobado usa SAVEPOINT
bajo el mismo lock serializado, componible con la transacción A6 ya abierta.
No usa `put_if_absent` anidado porque abre otro BEGIN. No llama `store.read` con
puntero privado ni interpreta scope=None como autorización para plaintext.
Rollback de A6 retira control, no borra el ciphertext publicado; una referencia
sin registro no se puede abrir a través de esa instancia con store.

## Vistas existentes, sin rescope implícito

| Vista fija | API / consumidor |
|---|---|
| `worker:polling` | `preflight(owner_ref)`, `put(owner_ref,content) → ref`, `get(owner_ref,ref) → bytes`, A12 |
| `worker:task-router` | `seal(owner_ref,plaintext) → BlobPointer`, `open(owner_ref,pointer) → bytes`, A6/A11 |
| `worker:research` | `seal/open`, A9 |
| `worker:sources` | `source_sink().put → PrivateWrite(owner_ref,pointer)`, `read(owner_ref,pointer,max_bytes)`, A8 |

`worker:sources` no se convierte en `worker:research`. Un resolver boundary
explícito autorizado abre A8, convierte su DTO y conserva su puntero original
en citas A9. Owner de `PrivateWrite` no equivale a audiencia criptográfica.
Polling preflight realiza un round trip real y durable con marcador técnico,
lo que consume un blob/cuota; no muestra datos ni toca bot. A10 puede usar una
audiencia registrada explícitamente por su wiring futuro; no se proclama una
integración de conversaciones no ensayada.

## Pruebas y límites observados

```powershell
$env:PYTHONPATH='src;.'
$env:HYPOTHESIS_STORAGE_DIRECTORY='.local/hypothesis'
python -m pytest -q tests/private_vault
python -m pytest -q tests
python scripts/check_docs.py
git diff --check
```

Las pruebas compilan el helper en `.local/private-vault-build`, sin binarios Git,
generan claves X25519 **efímeras sintéticas** y restringen únicamente carpetas
temporales propias. La matemática age y las ACL Windows son reales; las fuentes,
contenido, autoridad y transportes de interoperación se identifican como fixtures.
No se registra un parser/fake en el wiring real.

Cobertura: cifrado/restart, tamper age/cipher SHA, bindings internos, owner/scope,
key/path/reparse/hardlinks, ACL real permisiva negativa, permisos/claves alteradas,
flush/error estático, buffers/timeout/kill+await, cuotas threads+procesos/restart y
huérfanos, control SQLite inmutable/rollback, contexto y confirmación A6 reales,
A8 → A9 con audiencia original y contrato A12 put/get. Nada privado aparece en
SQLite, stdout de diagnósticos, repr/logs ni nombres de archivo; el pipe privado
de decrypt transporta intencionalmente plaintext en memoria.

La suite requiere Go 1.27.1 y dependencias ya cacheadas incluso en el job Python,
o preparación equivalente explícita; el CI remoto actual no garantiza ese setup.
Ese cambio de workflow pertenece al coordinador, no se incorpora por A14.

Resultados observados Windows, 2026-10-04, código congelado antes de este registro:

- Área A14: **45/45** en **16.50 s**, cero skips/fallos; Go helper dos tests reales
  en **0.456 s** y `go vet` aprobados, `-count=1` y offline.
- Suite completa: **864 tests + 246 subtests** en **71.08 s**, cero skips/fallos.
  Incluye A11/A12/A6 y A9/A8 de las dependencias de esta rama.
- `check_docs.py`, `git diff --check` y `git diff --cached --check` aprobados.
  Validador Go y módulo Go/B: `go test -mod=readonly ./...` y `go vet` aprobados,
  con red de dependencias apagada. No se ejecutó Node en esta rama (sin cambios
  Node/contratos nuevos y sin instalar paquetes); no se infiere CI remota.
- Helper Windows `vault.exe`, Go 1.27.1 windows/amd64, build flags anteriores:
  SHA-256 `dced4298b3fd4a03ce403b62b592c538711bb3551efe30b40b6da8a1cc11b7f4`.
  Blob Git del backend `2b32d108b129b4bdbada8d512c8507ed2394ad4b` y main Go
  `e5d492afada237e96ab92fdaff5a1a7bc6f99618`; éstos identifican el código probado,
  no son hashes de ciphertext ni de claves. Los binarios siguen sólo en `.local/`.
- ACL WinAPI y publicación Windows realmente observadas. POSIX/Linux y prueba
  independiente del coordinador siguen pendientes; no se hizo Docker en A14.

Fallos durante desarrollo se conservan como tales: 3 fallos de fixture de
interoperación (nombre local, firma ResearchRequest y reserve_read faltante),
corregidos sin cambiar A6/A8/A9. La primera suite completa encontró además una
colisión de basename entre pruebas A14/A9; se renombró sólo el archivo A14.
No fueron fallos crypto ni se contaron éxitos.

Pendientes: revisión B, verificación independiente, integración/autorización de
factory y bot reales, wiring A10/worker age/SSM/Go durable, canary y cuentas/fuentes
reales. No activa sesiones, Telegram/WhatsApp, envíos, IA, PDF real o AWS; cerrar
el backend local offline no completa las fases comerciales ni todos sus gates.
