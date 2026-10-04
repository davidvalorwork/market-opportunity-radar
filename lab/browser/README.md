# Laboratorio sintético de navegador

Este handler implementa un ensayo local de Playwright y OpenCLI con Chromium
Sparticuz. No prueba acceso a cuentas, plataformas sociales, permisos AWS ni
utilidad comercial. El servidor fixture y CDP nacen dentro de la invocación,
escuchan en loopback y se cierran al terminar. El coordinador registra por separado
el resultado observado del build y de las invocaciones en Docker.

## Contrato

Entrada mínima:

```json
{"schema_version":1,"suite":"browser","fixture_only":true}
```

Opcionales: `mode` (`direct` o `opencli`, default `direct`), `batch` (1–10,
default 3), `repeats` (1–5, default 1), `session_mode` (`full`, `cookies_only`
o `none`, default `full`), `deadline_ms` (1000–60000, default 60000). No acepta
URLs, cookies, rutas de perfil ni otros campos. `cookies_only` y `none` son
casos negativos: deben devolver `failed` por estado incompleto.

Salida: `schema_version`, `suite`, `mode`, `status`, `timings`, `checks`,
`useful_records`, `versions` y anuncios sintéticos correctos. `checks.runs`
expone recuperación de cookie, localStorage e IndexedDB, ausencia de
sessionStorage y comparación exacta del resultado esperado. Cada consulta tiene
tres anuncios y cada repetición restaura un contexto nuevo; `useful_records`
cuenta extracciones correctas, incluyendo repeticiones, no productos únicos.
No devuelve cookies, storageState, stderr del CLI ni capturas. Los fallos incluyen
fase y mensaje de error acotado/redactado; Chromium aporta como diagnóstico solo
stderr de arranque, capturado antes de abrir el fixture, junto a permisos de
HOME/tmp y estado del binario. Nunca se vuelca el entorno del proceso.

## Build y ejecución local

Desde la raíz del repositorio:

```powershell
docker build --platform linux/amd64 -f lab/browser/Dockerfile -t market-radar-browser-lab .
docker run -d --name radar-browser-test --network none --memory 2048m --memory-swap 2048m --cpus 1 --read-only --cap-drop ALL --pids-limit 256 --tmpfs /tmp:rw,exec,nosuid,nodev,size=768m market-radar-browser-lab
'{"schema_version":1,"suite":"browser","fixture_only":true,"mode":"direct","batch":3}' | docker exec -i radar-browser-test node /var/task/lab/browser/invoke.mjs
'{"schema_version":1,"suite":"browser","fixture_only":true,"mode":"opencli","batch":3}' | docker exec -i radar-browser-test node /var/task/lab/browser/invoke.mjs
```

`invoke.mjs` envía el evento a RIE dentro del contenedor, escribe un único JSON
en stdout y sale con código distinto de cero si falla. El entrypoint oficial de
la imagen AWS utiliza RIE localmente. No se publica un puerto ni se necesita red
durante el ensayo; las descargas ocurren durante el build. El runner debe
registrar el digest real de la imagen base y de la imagen construida. La base
está fijada en Dockerfile por el digest verificado durante el build, además del
tag `nodejs:24`. Lambda/RIE no impone aquí un timeout AWS;
el handler limita trabajo a 60 segundos y reserva cierre, el cliente a 85 segundos.

Pruebas ligeras sin dependencias de navegador:

```powershell
node --test lab/browser/selftest.mjs
```

## Implementación y límites

OpenCLI 1.8.8 ejecuta el adaptador JS `radar-fixture extract` registrado en un HOME
temporal. `OPENCLI_CDP_ENDPOINT` apunta al WebSocket de una página concreta,
obtenido de `Target.getTargetInfo`; el adaptador comprueba URL completa y título
antes de leer tres filas. No usa selección automática de tabs, extensión ni daemon.
`CI=1` desactiva la comprobación de actualización de OpenCLI. Los lifecycle
scripts npm se deshabilitan porque descargan adaptadores externos sin pinning.

Se usa storageState con `indexedDB:true`; sessionStorage no es portable por este
mecanismo. Se mantiene solo en memoria. El perfil y HOME de cada invocación se
eliminan. Los assets públicos de Sparticuz se descomprimen durante el build en
una etapa separada: binario/SwiftShader en `/opt/radar-chromium`, fuentes en
`/opt/fonts` y bibliotecas en `/opt/al2023`. El handler verifica tamaño, permisos
y hash del binario inmutable; nunca extrae assets ni cambia ownership durante la
invocación. Esto evita `EPERM chown /tmp/fonts` bajo `--cap-drop ALL` y deja fuera
del runtime cualquier extracción parcial del build. No se ejecutan cuentas,
mensajería ni fuentes reales.

El arranque conserva los flags de Sparticuz salvo `--single-process`: la prueba
local confirmó el cierre del target al crear páginas en contextos independientes
con ese flag. Mantiene `--no-zygote`, `--no-sandbox` y las restantes opciones del
paquete; el aislamiento externo sigue siendo red deshabilitada, filesystem
read-only, capacidades eliminadas y perfiles temporales separados.

API verificada en fuentes primarias de las versiones fijadas:

- [OpenCLI CDPBridge: endpoint WS y evaluate](https://github.com/jackwener/opencli/blob/v1.8.8/src/browser/cdp.ts).
- [OpenCLI: elección CDP por variable de entorno](https://github.com/jackwener/opencli/blob/v1.8.8/src/runtime.ts).
- [OpenCLI: descubrimiento de adaptadores JS y shims](https://github.com/jackwener/opencli/blob/v1.8.8/src/discovery.ts).
- [OpenCLI: API pública de registro](https://github.com/jackwener/opencli/blob/v1.8.8/src/registry-api.ts).
- [OpenCLI: actualizaciones desactivadas por CI](https://github.com/jackwener/opencli/blob/v1.8.8/src/update-check.ts).
- [Sparticuz: flags de Chromium y binario](https://github.com/Sparticuz/chromium/blob/v153.0.0/source/index.ts).

Dependencias directas consultadas con `npm view`: `@sparticuz/chromium` 153.0.0,
`playwright-core` 1.63.0, `@jackwener/opencli` 1.8.8; dependencias transitivas
fijadas por package-lock.json con integridad npm. La imagen requiere Linux amd64
y Node 24. Un test aprobado solo certifica este fixture y configuración.
