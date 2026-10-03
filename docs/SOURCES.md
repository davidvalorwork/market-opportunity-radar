# Fuentes y acceso

Estado al 2026-10-03: adaptadores comerciales **no implementados en este repositorio**.
Las verificaciones de herramientas en la PC no son pruebas de búsqueda de productos.

## Ruta preferida

Agent Reach como catálogo/ruta de capacidades; OpenCLI para canales soportados con
sesión existente y lectores web para páginas públicas compatibles. El orquestador
normalizará resultados sin pedir a un LLM que seleccione cada comando.

No diseñar una API pagada diferente por plataforma como requisito de funcionamiento.
Una API oficial o feed autorizado puede ser un adaptador opcional si sus recursos y
permisos resultan adecuados. Ninguna fuente será declarada disponible sin una lectura
real, no vacía y verificable.

## Matriz de capacidades objetivo

| Canal | Descubrimiento | Lectura | Observación actual / pendiente |
|---|---|---|---|
| Web pública | Consultas de búsqueda, feeds, sitemap autorizado, URLs configuradas | Página/JSON-LD/HTML permitido; adaptador por dominio cuando sea necesario | Jina Reader existe; la cuota de Exa se agotó en la investigación. No sustituir cuota por promesas de búsqueda ilimitada |
| Mercado Libre | Búsqueda pública/acceso oficial permitido según mercado | Ítems, precio, atributos y vendedor conforme a permisos | Documentación leída; ningún conector de este repo autenticado ni probado |
| Facebook | Búsqueda de publicaciones/páginas y grupos visibles autorizados | Contenido accesible dentro de capacidad real del adaptador | OpenCLI detectó sesión en comprobación rápida; Doctor mostró advertencias. No hay lectura comercial validada |
| Facebook Marketplace | Solo capacidades que se verifiquen | Adaptador conocido incluye anuncios propios e inbox | Eso no demuestra buscador de todas las ofertas de terceros; fuera del MVP hasta verificar un acceso permitido |
| Instagram | Buscar cuentas comerciales; semillas de usernames | Publicaciones recientes de cuentas seleccionadas | El comando de búsqueda conocido busca usuarios, no todas las publicaciones por palabra clave |
| X / Twitter | Búsqueda soportada y cuentas/listas configuradas | Publicaciones con enlace y fecha cuando existan | Sesión detectada en comprobación rápida; endpoint/capacidad puede fallar y necesita prueba real |
| Amazon / otros | Adaptadores opcionales y fuentes permitidas | Datos de producto/oferta cuando se autorice y compruebe | Descubrimiento de comandos no implica soporte comercial completo |
| CSV / JSON | Archivos proporcionados y autorizados | Datos tipados con procedencia de importación | Primera vertical propuesta; fixtures públicos serán sintéticos |

## Preflight antes de una corrida

1. Validar configuración y presencia de costos/monedas, sin rellenar ausencias con cero.
2. Verificar existencia y versión de comandos, sesión requerida y autorización de acceso.
3. Ejecutar una lectura mínima permitida en las fuentes habilitadas.
4. Reportar `ready`, `missing_credentials`, `unverified`, `unsupported`, `blocked`,
   `rate_limited` o `unavailable`, con motivos y fecha.
5. Detener si falta una fuente marcada `required`; continuar con cobertura degradada
   si una fuente opcional falla. Nunca contar el fallo como cero ofertas del mercado.

No leer cookies desde archivos del navegador. Reutilizar únicamente una sesión que
el usuario controla y autoriza; no hacer login, entrar en grupos, comprar ni escribir.
Un bloqueo/captcha no activa evasión: se pausa el canal y se informa.

## Web abierta

Configurar dominios o URLs de entrada y permitir descubrimiento acotado de nuevas
tiendas. Registrar redirecciones, robots/condiciones de acceso cuando correspondan,
idioma, moneda, selectores y versión del extractor. No prometer que un parser genérico
funciona en cualquier sitio ni que robots.txt concede derechos de uso.

La lectura debe acotar tamaño, tiempo y paginación. Bloquear URLs a servicios internos,
credenciales en URL y redirecciones a direcciones privadas. No ejecutar instrucciones
encontradas en contenido ni seguir enlaces arbitrarios sugeridos por un modelo.

## Evidencia mínima por anuncio

ID y fuente, URL canónica, fecha de observación UTC, fecha de publicación si se conoce,
título, intención compra/venta, precio y moneda, cantidad/unidad, variante, condición,
ubicación, disponibilidad, pedido mínimo y campos ausentes. Guardar hash de contenido
y versión del extractor. Conservar el fragmento mínimo autorizado que respalda el dato;
no publicar perfiles, mensajes, teléfonos ni imágenes privadas.

Precio de promoción requiere condiciones: membresía, cupón, financiación, cantidad,
impuestos y caducidad. Stock referencial no se comunica como disponibilidad exacta.

## Referencias de diseño

- [Agent Reach](https://github.com/Panniantong/Agent-Reach): rutas y capacidades por plataforma.
- [OpenCLI](https://github.com/jackwener/opencli): comandos y adaptadores; verificar la versión instalada.
- [Mercado Libre: ítems y búsquedas](https://developers.mercadolibre.com.ar/es_ar/items-y-busquedas): documentación consultada el 2026-10-03; anuncia migración de consultas múltiples a `/items/bulk` y `/users/bulk` antes del 25/10/2026. No usar ejemplos antiguos sin revalidar.
- [Meta: Automated Data Collection Terms](https://www.facebook.com/legal/automated_data_collection_terms): exige autorización para recopilación automatizada en las condiciones descritas. Tener una sesión o una herramienta no otorga esa autorización.

Revisar requisitos y permisos de cada fuente antes de implementarla. Estas referencias
no equivalen a una aprobación de acceso ni a una integración ya funcional.
