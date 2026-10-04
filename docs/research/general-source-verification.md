# Fuentes generales: inventario y límites comprobados

Fecha: 2026-10-04. Frente A8. El pedido del usuario es **general**: buscar,
leer, extraer contactos y preparar comunicaciones sobre cualquier tema.
No hay categoría de negocio, producto, vehículo ni medio de pago obligatorio.
Este documento no es un catálogo de capacidades autorizado ni una prueba de
acceso a cuentas; modificar `contracts/capabilities.json` corresponde a B.

## Método y evidencia

Se usó la skill Agent Reach con sus referencias social/web. Comandos ejecutados:

```powershell
agent-reach doctor --json
opencli list -f json
opencli facebook marketplace-listings --help
opencli threads search --help
opencli instagram search --help
opencli twitter reply-dm --help
agent-reach check-update
```

Sólo diagnóstico, inventario y ayuda; **ningún comando de contenido social,
login, importación de cookies ni envío**. Doctor detectó el puente OpenCLI,
pero no comprobó las sesiones de Facebook, Instagram, X o Reddit. Incluso
su etiqueta `ok` de LinkedIn admite no haber ejecutado la operación real.
Por tanto `doctor`, `--help` o un ejecutable instalado no habilitan una fuente.

Agent Reach informó versión local 1.5.0, sin actualización disponible en su
chequeo. OpenCLI global mostró 1.8.6 y anunció 1.8.8; no se actualizó ni se
confundió esa versión con el lock del worker. El inventario es una observación
de la instalación local, no garantía de la versión desplegada.

También se leyó el README público de OpenCLI por la ruta web de Agent Reach
(Jina Reader), exit 0. No se enviaron URLs privadas, cookies ni tokens al lector.
Los repos upstream se contrastaron con navegación web de sólo lectura:
[Agent Reach](https://github.com/Panniantong/Agent-Reach) y
[OpenCLI](https://github.com/jackwener/opencli). Sus descripciones de alcance
son documentación del autor, no pruebas ejecutadas de cada red.

## Matriz observada, no autorizada

| Fuente | Metadatos/ayuda observados | Lo que sigue sin probar |
|---|---|---|
| Web libre | Jina pudo leer un README público | HTTP directo, SSRF/redirects, caché, fuentes arbitrarias y sesiones en el runtime |
| Facebook | search/profile/feed/groups/events; marketplace-listings y marketplace-inbox en el inventario | Acceso a contenido, grupos concretos, búsqueda global de productos o Messenger |
| Marketplace | Ayuda de marketplace-listings: **listados propios del vendedor** | No es búsqueda global de ofertas; conector de descubrimiento distinto pendiente |
| Instagram | search busca **usuarios**; profile/user/explore en inventario | No equivale a búsqueda global de publicaciones por palabra ni DM probado |
| X/Twitter | search/thread/timeline/article/tweets en inventario | Cookies, acceso real, estabilidad y destinatarios autorizados |
| Threads | search/feed/post de lectura, backend UI | Sesión y lectura no comprobadas; `post` aquí lee, no publica |
| Reddit | search/read/subreddit/hot/popular en inventario | Sesión/contenido no comprobados; no asumir endpoints anónimos |
| Messenger | No apareció un site llamado messenger en ese inventario filtrado | No se concluye inexistencia global: requiere descubrimiento/adaptador y pruebas propias |
| MercadoLibre | No apareció mercadolibre en ese inventario filtrado | Igual: no afirmar soporte sólo por navegación genérica |
| WhatsApp | No apareció whatsapp en OpenCLI filtrado | Worker Go del repo es la ruta separada; replay entre propietarios sigue como gate |

La ayuda `twitter reply-dm` describe mensajes a conversaciones recientes, no
un envío dirigido inequívoco. **No se usará como envío genérico por defecto**:
una acción debe ligar destinatario y texto exactos al ledger/aprobación. También
se rechazan operaciones de login, publicaciones o mensajes dentro del adaptador
de fuentes de lectura aunque el inventario CLI las liste.

## Integración A8

1. Registro abierto de adaptadores y operaciones explícitas; argumentos tipados,
   nunca comandos/shell arbitrarios generados por IA.
2. Preflight por propietario, cuenta, operación, sesión y vigencia. Estado
   documentado/configurado no sustituye evidencia real ni autorización.
3. Referencias a sesiones cifradas entregadas expresamente; cookies no se extraen
   de Chrome. OpenCLI de escritorio y cookies exportadas para un worker no son
   intercambiables por su nombre: probar ambos despliegues por separado.
4. Transporte inyectado de lectura con límites de páginas, bytes, tiempo y
   concurrencia. HTTP primero cuando procede; navegador sólo si es necesario.
5. Contactos y contenido por referencias privadas del propietario; procedencia,
   cobertura parcial y errores tipados. Encontrar un número no demuestra que sea
   WhatsApp ni concede autorización de contacto.
6. Sin coste nuevo implícito, sin evadir captcha/429 ni reintentos infinitos.
   Una fuente falla o pide renovar sesión sin frenar el informe de las demás.

Próximas pruebas reales se harán **por operación**, en cuentas/fuentes autorizadas,
con contenido no vacío y evidencia fechada. No se declara que todas las redes
funcionen ni que esto esté conectado al bot o desplegado en Lambda.
