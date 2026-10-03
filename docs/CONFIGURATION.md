# Configuración propuesta

[radar.example.json](../config/radar.example.json) describe el contrato inicial.
No existe todavía una CLI que lo ejecute; nombres y esquema cambiarán de forma
versionada al implementar. No incluir secretos, sesiones ni datos de contactos.

## Dimensiones configurables

| Grupo | Configuración prevista |
|---|---|
| Productos | Categorías, marcas, modelos, palabras clave, exclusiones, atributos obligatorios |
| Mercado | Orígenes, destinos, idioma, moneda de reporte y tipos de cambio fechados |
| Fuentes | Canales habilitados/requeridos, dominios/URLs permitidos, cuentas y grupos autorizados |
| Descubrimiento | Consultas, páginas, ventana temporal, anuncios objetivo y presupuesto total de trabajo |
| Concurrencia | Workers globales y por backend/dominio; serialización de sesión compartida |
| Extracción | Adaptador, tamaño/tiempo máximo, campos requeridos y caducidad |
| Matching | Marca/modelo/variante/condición/unidad; confianza y revisión por conflicto |
| Economía | Capital, pedido mínimo, costos, comisión, escenarios, ganancia y margen mínimo |
| Alertas | Cambio de precio, nueva oferta, pérdida de disponibilidad, candidatos revisados |
| Evaluación | Conjunto versionado, repeticiones, baseline y presupuesto sin API pagada |
| Seguridad | Modo solo lectura, límites de acceso, redacción de datos y retención |

## Defaults y descubrimiento amplio

El ejemplo no activa conectores sociales sin preflight. No restringe la futura
categoría a perfumes/relojes, aunque son semillas iniciales. El destino está vacío
para evitar asumir costos de importación o una estrategia de venta no confirmada.
Costos y tasas vacíos generan escenarios incompletos, no ceros ficticios.

Si hay pocos candidatos, ampliar de forma ordenada sin romper equivalencia:

1. Sinónimos, idioma y escritura del mismo producto.
2. Más tiendas/fuentes permitidas y paginación dentro del presupuesto.
3. Ventana temporal mayor, marcando antigüedad y disponibilidad pendiente.
4. Más mercados de origen con logística/costos pendientes visibles.
5. Productos cercanos como una categoría exploratoria separada, nunca el mismo SKU.

Nunca relajar silenciosamente condición, presentación o autenticidad para inflar
oportunidades. Los candidatos sin costos completos se muestran por separado.

## Credenciales y recursos

Configuración pública solo indica la necesidad de una sesión o nombre de una
variable de entorno; jamás valores de cookies/tokens. Datos locales van a `.local/`.
Falta de credenciales produce estado explícito antes de gastar presupuesto.

Presupuesto de trabajo y límites de reintentos deben ser finitos por defecto. Un
modo continuo futuro requiere activación explícita, cancelación y checkpoints. No
prometer una cantidad de oportunidades cuando las fuentes o el mercado no la ofrecen.

## IA y tokens

IA desactivada por defecto. Si se habilita, elegir proveedor local, modelo instalado,
tipos de casos permitidos, límite de inferencias/tokens y datos que puede recibir.
Prohibido descargar modelos o pasar a una API pagada como fallback sin aprobación.

Antes de inferir: extracción estructurada, reglas y caché por hash. Conservar salidas,
errores y decisión de revisión; no llamar a un LLM para cálculos monetarios simples.
