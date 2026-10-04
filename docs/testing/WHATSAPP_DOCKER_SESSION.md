# Comprobación offline de material de sesión WhatsApp (A32)

Preparación verificable; el autor no leyó la sesión real ni ejecutó Docker.
El coordinador tiene autorización humana separada para usar una copia privada
de la sesión y realizar la comprobación Docker. No constituye permiso de enviar,
leer chats, conectar red, usar IA, AWS ni publicar credenciales.

## Qué demuestra

`helpers/whatsapp-local/cmd/session-check` lee por stdin un único JSON cerrado
`declared_phone` (máximo 512 bytes, E.164 ASCII). No hay flag/env de teléfono.
El argumento `--session-dir` señala un directorio readonly que contiene el
snapshot independiente `whatsmeow.db`, regular, máximo 32 MiB y con cabecera
SQLite válida. Rechaza symlink del archivo/directorio y sidecars WAL/SHM: el
coordinador debe provisionar un snapshot previamente checkpointed, no copiar
una base viva. El programa verifica tamaño/mtime y copia únicamente ese archivo
a un subdirectorio privado nuevo de TMPDIR. Migraciones y mapping self se hacen
exclusivamente sobre esa copia; al terminar se elimina el leaf temporal.

Abre SAME `wameow.New` y lee identidad almacenada por `sqlstore.GetFirstDevice`
del mismo SDK. Compara el User del JID con el número declarado sólo en RAM,
comprueba `SelfChatRef`/`HasChat` y cuenta filas locales. Nunca llama Connect,
WaitPaired, Sync, Send ni descarga historial. Logger del SDK es Noop.
Los conteos corresponden a la copia después de materializar su mapping self;
no son un inventario exhaustivo de chats de la cuenta.

Stdout contiene solamente `paired`, `phone_matches`, `self_ref_known`,
`known_chat_count`, `pending_message_count` y código estático. Éxito exit0/`ok`;
fallo exit2. No JID, teléfono, refs, nombres, textos ni errores SQL/paths privados.
`paired` significa identidad de dispositivo almacenada; NO sesión activa ni
login real verificado, permiso, destinatario validado o evidencia de entrega.
El gate humano se describe como «material compatible + identidad propia declarada».

## Build offline sin secretos

Desde `helpers/whatsapp-local`, con dependencias ya disponibles y compilador Go:

```powershell
$env:GOPROXY="off"
$env:GOSUMDB="off"
$env:GOWORK="off"
$env:GOTOOLCHAIN="local"
go test ./cmd/session-check
go vet ./cmd/session-check
$env:CGO_ENABLED="0"
$env:GOOS="linux"
$env:GOARCH="amd64"
go build -mod=readonly -trimpath -buildvcs=false -o ../../.local/whatsapp-session-check ./cmd/session-check
```

El Dockerfile usa FROM scratch y USER65532:65532. No builds/downloads durante
Docker ni fuente/secreto en la imagen. El coordinador prepara un contexto NUEVO
que contiene sólo el binario Linux `whatsapp-session-check` y el Dockerfile,
inspecciona sus archivos y hashes antes del build. Nunca pasar checkout o .local
completa como contexto. La `.dockerignore` histórica no habilita este binario en
el checkout; no se modifica ni se evade enviando todo el repositorio.

## Ejecución coordinada, no realizada por el autor

El coordinador monta exclusivamente el snapshot verificado readonly en
`/input/whatsmeow.db`, usa `--network none --read-only --memory 512m --cpus 1
--pids-limit 64 --cap-drop ALL --security-opt no-new-privileges`, y configura
`--tmpfs /tmp/private:rw,noexec,nosuid,nodev,size=128m,mode=0700,uid=65532,gid=65532`.
La imagen no lleva CA ni shell ni credenciales. JSON privado entra mediante
stdin/pipe de RAM (`-i`); nunca shell echo, argumento, log o archivo de contexto.
UID65532 debe poder leer el bind readonly provisionado por el coordinador. No
afirmar cifrado del snapshot: protocolo SQLite contiene material sensible y
se protege por ACL/bind/tmpfs, fuera de Git y capas. Sólo conteos/booleanos salen.

Recoger inspección Docker de límites reales, red, UID, bytes de snapshot y
CPU/memoria observados. 512 MiB/CPU1 son límites configurados, no métricas
medidas ni garantías de coste USD. La prueba Windows del autor y el crossbuild
Linux no sustituyen esta ejecución Docker.

Pruebas offline usan SQLite/SDK reales con identidad y firmas **sintéticas**,
sin WhatsApp: compatible/mismatch/unpaired, fuente sin mutaciones ni nuevos
sidecars, stdin duplicado/extra/trailing/oversized, checkpoint ausente y formato
inválido. Nunca son prueba de cuenta real o sesión activa. Pausa del runtime
original/exclusión de sesiones reales continúa siendo requisito del coordinador.
