# Resultados locales del laboratorio Docker

Fecha: 2026-10-03. Datos sintéticos; sin cuentas, contactos, capturas de Chrome,
AWS ni llamadas a modelos. No son pruebas comerciales ni de compatibilidad con
Facebook, WhatsApp, X, Threads, Reddit o Instagram.

## Entorno y trazabilidad

Windows + Docker Desktop Linux, Engine 29.5.3, arquitectura amd64 nativa,
20 procesadores lógicos y 31,8 GiB de RAM física. VM Docker: 15,5 GiB.
Otros cinco contenedores del usuario permanecen activos y no se modifican.
Las builds y los ensayos se ejecutan secuencialmente.

Cada corrida guarda `.local/reports/<run_id>.jsonl`, excluido de Git. Incluye
imagen/digest, versión, límites, estado del contenedor, OOM, CPU cgroup, RAM peak,
tiempos, errores y cleanup. El peak abarca la vida del contenedor, incluidas
invocaciones previas; no sumar peaks warm como si fueran procesos independientes.
No publicar reportes de sesiones reales sin revisión y redacción.

## Sesiones Go/age: comprobado

Imagen scratch estática, usuario 65532, aproximadamente 1,66 MiB. Go 1.27.1;
age 1.3.2 con go.mod/go.sum verificados. La reconstrucción con módulos readonly
produjo el mismo binario/config de imagen que las primeras mediciones.

Run `1d566ad3ad94`: seis contenedores independientes, 1 CPU, tres ensayos por RAM.

| RAM asignada | Ensayos correctos | Ejecución selftest | Peak cgroup |
|---|---|---|---|
| 128 MiB | 3/3 | 7,53–10,25 ms | 4,46–4,73 MiB |
| 256 MiB | 3/3 | 8,27–16,10 ms | 4,78–5,38 MiB |

El tiempo es el selftest interno, no arranque de Docker ni duración AWS. Dos
destinatarios restauran el mismo fixture por ensayo; no son cuentas comerciales.
CPU cgroup: 0,0528–0,0643 segundos. Todos los contenedores se eliminaron.

Handoff real local entre contenedores: primer ensayo completo en 16,35 segundos
incluyendo múltiples arranques de Docker. Vaults separados, sólo recipient público
y ciphertext compartidos. Pasaron versiones 1→2, clave incorrecta, CAS obsoleto,
replay de versión 1 y preservación de versiones inmutables. El receptor sigue
`unverified`: importar estado no prueba login.

El helper definitivo añade límites de 30 s por operación y 180 s globales. Se
detectó y corrigió un problema Windows al resolver dos ejecutables `docker`.
El handoff repetido pasó sus 14 checks en **17,65 s**, con artifact local
`b62aa1d0cb51429585f585fa5ba33639`. Ocho verificaciones adicionales cubren scripts,
AST y resolución de ejecutables; no sustituyen este ensayo Docker completo.

## Navegador: incidencias verificadas y correcciones

Base oficial Lambda Node 24 por digest, Node 24.21.0; Playwright 1.63.0,
Sparticuz 153.0.0 y OpenCLI 1.8.8 por lockfile. Chromium observado: 153.0.8010.0.

1. `bd00b8974202` / `a94dfc451259`: arranque fallido; **no OOM**. La extracción
   intentaba chown sin capacidades y `/tmp` impedía ejecutar el binario.
   Se preextraen assets públicos al construir, se verifica binario completo por
   tamaño/hash y se usan rutas inmutables `/opt`. Runtime conserva cap-drop ALL.
2. `7fbf74d5c802`: Chromium/CDP ya funcionaban; crear contexto independiente
   fallaba con `single-process`. Peak 204,23 MiB; no OOM. Se retiró sólo ese flag.
3. El tmpfs ejecutable queda explícito para navegador, con nosuid/nodev; perfiles
   y HOME/cache por invocación. Para sesiones, tmpfs noexec de sólo 32 MiB.
4. Varios perfiles se bloquearon por RAM libre del host tras builds. El default
   conserva 4 GiB de reserva + 25% de margen; una reserva experimental distinta
   debe suministrarse y registrarse explícitamente. No parar programas ajenos.

## Navegador: primer comparativo correcto

Run `dcbe38c19566`: 480 MiB, 1 CPU, reserva **explícita experimental** de 3 GiB,
una creación de contenedor por modo y una invocación warm adicional. No se tocó
el default de 4 GiB. Cada invocación leyó 3 páginas/9 registros sintéticos exactos.
Las cuatro pasaron cookies + localStorage + IndexedDB, sessionStorage ausente,
aislamiento de red y cleanup. Ningún OOM. Hash del binario completo:
`53a15d6c3a3d27dfb54c4ba60278b1683136f70cf1e67e989da7dfbd3d451ef0`.

| Modo | Handler frío / warm | Invocación host fría / warm | Peak contenedor |
|---|---|---|---|
| Directo | 3,10 / 2,76 s | 4,19 / 3,11 s | 276,26 MiB |
| OpenCLI | 5,19 / 4,96 s | 6,20 / 5,31 s | 323,09 MiB |

Son samples pequeños bajo CPU limitado, no p95 ni estimación de una red real.
Los 36 registros son extracciones repetidas de fixtures, no 36 oportunidades.
OpenCLI invoca su CLI/adaptador real; no una simulación que devuelve el fixture.
La imagen de este ensayo pesa unos 303,92 MiB; digest:
`sha256:b17c4bf55fc1dfed637ccc98cdf7da42c57aaf88cde2bc51d67ff0f04ccbbc30`.

Pruebas negativas en un contenedor a 480 MiB: cookie sola, sin estado y evento
no-fixture se rechazaron como corresponde (3/3). Las dos primeras devolvieron
cero registros útiles y cleanup completo. Contenedor eliminado. No se deshabilitó
ningún check para aprobar el ensayo de baja RAM.

### Matriz con reserva predeterminada de 4 GiB

Run `0535f7c3018e`: 512/1.024/1.600 MiB, 1 CPU, 2 trials previstos, una invocación
warm adicional y 9 registros por invocación. **14/14 invocaciones ejecutadas
correctas**, cero OOM, cleanup completo. Cinco perfiles no ejecutados se registran
`skipped_resource`: cuatro a 1.600 MiB y una repetición directa a 1.024 MiB.
La corrida devuelve exit 1 por esa matriz incompleta, no por extracción fallida.

| RAM / modo | Invocaciones correctas | Handler observado | Peak observado |
|---|---|---|---|
| 512 MiB / directo | 4/4 | 2,52–2,87 s | 280,76–290,45 MiB |
| 512 MiB / OpenCLI | 4/4 | 4,70–5,00 s | 320,48–333,65 MiB |
| 1.024 MiB / directo | 2/2 | 2,58–2,70 s | 281,68–292,29 MiB |
| 1.024 MiB / OpenCLI | 4/4 | 4,65–5,10 s | 345,49–353,32 MiB |

El CPU siempre fue 1: no aumenta al asignar más RAM en Docker como puede ocurrir
en Lambda. Por eso esta matriz no elige un óptimo AWS. Para estos fixtures la
lectura directa resulta más ligera; OpenCLI conserva valor al reutilizar
adaptadores existentes. Ambas opciones quedan disponibles, no se descarta una
por un microbenchmark. No hay canary AWS ni red social probada.

El wrapper `upload.ps1` también se ejecutó con **los fixtures sintéticos** v1/v2:
upload y renew devolvieron `prepared_unverified` y versiones 1/2. Su flag
`--user-export` etiqueta la operación `fixture_only:false`, pero en esta prueba
el input siguió siendo el fixture público, nunca cookies reales.

Verificación final: 31 tests Python, 4 tests Node, 8 verificaciones PowerShell,
tests Go durante build y controles de documentación aprobados. Los counts de
tests no se suman como usuarios ni ofertas; los negativos esperan rechazo.

## Costos: aproximación, no factura

El runner no usa LLM ni API de IA durante las iteraciones. Descargar paquetes,
electricidad, PC y trabajo de desarrollo no equivalen a costo total cero.

Fórmula del runner: `MiB asignados / 1024 × segundos de invocación local`.
Para navegador registra aparte `handler_total_ms`: init + trabajo + cleanup,
sin sobrecosto Docker exec/RIE. La aproximación por defecto usa el tiempo de
invocación; no incluye build/arranque de Docker, requests, almacenamiento ni red.

Tarifa de ejemplo explícita: USD 0,0000166667/GB-s del primer tramo x86 publicado
en [AWS Lambda pricing](https://aws.amazon.com/lambda/pricing/), consultado
2026-10-03. No aplicar universalmente a toda región/arquitectura. Requests se
tarifican aparte; free tier, S3, KMS, SSM y egress no se certifican aquí. Sin tarifa,
costos son null; con cero trabajo útil el costo por resultado también es null.

Ejemplo de sensibilidad del run experimental de 480 MiB: con esa tarifa, el
compute aproximado por lote de 9 extracciones fue USD 0,0000243–0,0000327 directo
y USD 0,0000415–0,0000485 OpenCLI. Usa tiempos host del ensayo, no duración
facturada por AWS. No extrapolar a búsquedas reales, conversaciones o ganancias.

## Reproducir

Desde la raíz del repositorio, Docker Engine Linux y Python 3.11+:

```powershell
python -m lab.runner --suite sessions --build --memory-mib 128,256 --trials 3
pwsh -NoProfile -File lab/sessions/handoff.ps1
python -m lab.runner --suite browser --build --mode both --memory-mib 1024,1600,2048 --trials 3 --warm-invocations 1 --batch 3
pwsh -NoProfile -File scripts/test_browser_negative.ps1
```

Pruebas pequeñas requieren menos recursos, pero no justifican elegir para
producción una RAM menor que la recomendada por el proyecto Chromium. Si no cabe
ningún perfil, el runner falla antes de arrancar un contenedor. Si caben algunos,
los demás se registran `skipped_resource`; el exit code avisa que la matriz no
se completó.

Para reproducir exactamente el comparativo posterior con default de seguridad:

```powershell
python -m lab.runner --suite browser --mode both --memory-mib 512,1024,1600 --trials 2 --warm-invocations 1 --batch 3 --run-timeout 300 --price-per-gb-second 0.0000166667
```

Subida explícita y renovación: [wrapper de sesiones](../../lab/sessions/README.md).
El archivo debe tener formato storageState, no simplemente una lista de cookies.
Captura/login guiados, dominio de cookies ampliado, leases de workers, revocación
remota, UI y almacenamiento AWS siguen pendientes.

## Criterios para el siguiente ensayo autorizado

- Una cuenta/dispositivo de prueba por plataforma, nunca una sesión activa clonada.
- Lectura inocua validada, caducidad y reauth con intervención del propietario.
- Cookies/localStorage/IndexedDB necesarios por red; sesiones ligadas a IP/dispositivo.
- Canary AWS para CPU/RAM/reconexión y costo real, con presupuesto explícito.
- WhatsApp/whatsmeow y Telegram se prueban por separado: cookies web no sustituyen
  claves multidispositivo ni autorizan mensajes.

Ver [reparto y cierre de coordinación](WORKPLAN.md). No se alteró el informe de Claude.
