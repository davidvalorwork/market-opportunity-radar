# Helper técnico DNS público

Go **1.27.1**, stdlib solamente. No módulos descargados, cuentas, SDK, shell,
forks ni efecto comercial. `cmd/resolve` no acepta argumentos; stdin JSON privado
<=1024 bytes, stdout JSON técnico <=2048, stderr sólo códigos constantes.
Contrato/error/budgets y límites en
[SOURCE_RESOLVER](../../docs/testing/SOURCE_RESOLVER.md).

Build explícito a un **nuevo leaf privado** preparado con A14; nunca binario Git:

```powershell
$env:GOPROXY='off'
$env:GOSUMDB='off'
$env:GOTOOLCHAIN='local'
$env:GOWORK='off'
# Desde helpers/public-dns; ruta absoluta elegida por el host y ACL comprobada:
go build -mod=readonly -trimpath -buildvcs=false -o <private-absolute-path> ./cmd/resolve
go test -mod=readonly -count=1 ./...
go vet -mod=readonly ./...
```

`<private-absolute-path>` es un placeholder, no un comando ejecutado ni una ruta
por defecto. Constructor Python requiere hash real del binario y ACL/owner de
archivo y parent. En POSIX el host configura sólo ese archivo propio a 0700.
No cambiar carpetas existentes del usuario ni importar claves/cookies.

`cmd/synthetic` es exclusivamente test: modo explícito en `.synthetic-only` junto
a su binario temporal propio. Servidor loopback, never-delegate, respuestas
sintéticas públicas/privadas y fallos/floods. Production no importa ese package
ni lee dicho archivo. No agregarlo a una factory productiva ni declarar su salida
como DNS/fuente externa verificados.
