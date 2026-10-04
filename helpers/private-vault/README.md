# Helper age privado

Módulo técnico independiente del worker WhatsApp: sólo `filippo.io/age v1.3.2`
y las mismas versiones/hashes transitivas del laboratorio de sesiones. No
importa sus paquetes ni la dependencia GPL del worker. age y sus dependencias
conservan sus licencias originales; este directorio no redistribuye binarios.

Desde este directorio, con Go 1.27.1 y módulos **ya presentes** en la caché:

```powershell
$env:GOPROXY='off'
$env:GOSUMDB='off'
$env:GOTOOLCHAIN='local'
$env:GOWORK='off'
go test -mod=readonly ./...
go vet -mod=readonly ./...
go build -mod=readonly -trimpath -buildvcs=false -o ../../.local/private-vault-build/vault.exe ./cmd/vault
```

No `go get`, `go mod tidy`, instalación global ni descarga implícita. Una caché
incompleta falla. El build no es un artefacto versionado. En POSIX omitir `.exe`.

`vault encrypt|decrypt`: stdin privado = uint32 big-endian de tamaño de clave,
clave X25519 (máximo 4096 bytes), cuerpo binario hasta EOF. Claves nunca en argv.
Encrypt recibe hasta 1 MiB de plaintext **incluido envelope**; decrypt admite
hasta 1 MiB + 4096 bytes de ciphertext. Sólo publica salida después de completar
age y verificar sus límites/authentication. Error único en stderr, sin contenido.
El stdout de decrypt es deliberadamente un **pipe privado**, nunca un log/consola.
La aplicación limita duración y buffers; el helper no crea procesos, plugins,
red, archivos ni sesiones. Las copias inmutables del runtime Go/Python no permiten
prometer borrado perfecto de secretos de memoria.

`synthetic-keys --synthetic-only ABSOLUTE_NEW_PRIVATE_DIRECTORY` es exclusivamente
una utilidad de pruebas offline: crea dos archivos nuevos de fixture con una
identidad efímera y su destinatario. Nunca imprime claves y no modifica archivos
existentes. El directorio privado se prepara/verifica antes con WinAPI o POSIX.
No genera ni importa claves de producción automáticamente.

Detalles del backend, fronteras y pruebas: [PRIVATE_VAULT](../../docs/testing/PRIVATE_VAULT.md).
