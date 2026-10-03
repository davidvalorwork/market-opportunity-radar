# Market Opportunity Radar

**Radar global de oportunidades de compra, venta y reventa con evidencia trazable.**

Busca ofertas y solicitudes de compra en tiendas, mayoristas, marketplaces y redes
sociales; compara productos realmente equivalentes y estima el margen después de
costos. Diseñado para empezar con perfumes y relojes, sin limitarse a una categoría,
país o plataforma.

Global product sourcing and resale intelligence: discover listings across the open
web and social channels, match equivalent products, and estimate cost-aware margins
with source evidence. Local-first, configurable and designed around Agent Reach.

**Estado: definición de producto y arquitectura, 2026-10-03.** Este repositorio aún
no contiene un buscador, comparador, CLI comercial ni dashboard operativo. La
configuración es una propuesta versionada; los controles documentales sí se pueden
ejecutar. No hay campañas, compras, ventas, mensajes o ganancias reales generadas
por este proyecto. No es Job Radar ni una demo de facturas.

## Qué queremos lograr

Ayudar a una persona que revende productos a responder:

- ¿Dónde encuentro el mismo producto más barato y disponible?
- ¿En qué mercado hay anuncios comparables o solicitudes de compra?
- ¿Qué margen estimado queda tras transporte, comisiones y demás costos?
- ¿Cuánto capital necesito y qué datos faltan antes de decidir?
- ¿La diferencia de precio es real o comparé cantidades, variantes o condiciones distintas?
- ¿Qué cambió desde la última revisión y qué evidencia respalda la oportunidad?

Un anuncio barato no es una oportunidad confirmada. Un precio publicado de venta
no prueba demanda ni una transacción completada. El sistema debe mostrar esa
diferencia, no inventar certezas para producir más resultados.

## Fuentes previstas

| Fuente | Uso previsto | Límite que debemos mostrar |
|---|---|---|
| Web abierta | Tiendas independientes, mayoristas, liquidaciones, clasificados y URLs configuradas | No todas las páginas son legibles ni ofrecen datos completos; soporte por dominio y fallos visibles |
| Mercado Libre | Publicaciones, variantes, precios, disponibilidad referencial y mercados por país | Acceso y autenticación dependen del recurso; precios publicados no equivalen a ventas |
| Facebook | Publicaciones, páginas comerciales y grupos accesibles con autorización | La existencia de un adaptador no garantiza buscar todo Marketplace ni todos los grupos |
| X / Twitter | Ofertas, proveedores, liquidaciones y publicaciones de intención de compra | Búsqueda puede fallar o estar limitada; una publicación no confirma disponibilidad |
| Instagram | Descubrir cuentas comerciales y leer publicaciones de vendedores seleccionados | El adaptador conocido busca usuarios; no equivale a búsqueda global de publicaciones |
| Otros marketplaces | Amazon, eBay u otros mediante adaptadores verificados posteriormente | No se declara soporte por incluir una plataforma en el roadmap |
| Importación autorizada | CSV/JSON y catálogos proporcionados por el usuario | No leer conversaciones privadas ni importar datos de otros proyectos automáticamente |

**Agent Reach será la puerta de entrada preferida** a capacidades existentes,
utilizando OpenCLI y lectores web cuando corresponda. No será un LLM decidiendo
cada paso. El programa debe invocar herramientas de forma estructurada, con
preflight, límites de frecuencia, caché y estados de salud por fuente.

La visión es amplia; la cobertura efectiva se medirá por adaptador, dominio, cuenta
y corrida. No prometemos consultar toda la web ni saltar restricciones de acceso.
Ver [fuentes y capacidades](docs/SOURCES.md).

## Flujo de funcionamiento propuesto

```text
Configuración + preflight
          ↓
Descubrimiento y lectura en paralelo, con cuotas por fuente
          ↓
Anuncios normalizados + evidencia + historial de precios
          ↓
Equivalencia de producto y deduplicación
          ↓
Comparación por mercado + escenario de costos
          ↓
Oportunidades ordenadas + datos faltantes + riesgos
          ↓
Revisión humana y alertas locales
```

Una corrida podrá detenerse y reanudarse. Las fuentes bloqueadas no se contarán
como búsquedas exitosas ni como mercados sin ofertas. No se comprarán productos,
contactarán vendedores ni publicarán anuncios automáticamente.

## Qué tendrá cada oportunidad

- Identidad y variante del producto; cantidad, presentación y condición.
- Anuncio de compra candidato y comparables de venta en el mercado de destino.
- Enlaces, país, moneda, fecha de publicación si existe y momento de observación.
- Precio por unidad, pedido mínimo, disponibilidad y condiciones del descuento.
- Costo puesto en destino, capital necesario y escenarios de precio de venta.
- Ganancia y margen **estimados**, con moneda, supuestos y fecha del tipo de cambio.
- Grado de confianza, posibles conflictos y costos todavía desconocidos.
- Evidencia de demanda cuando exista, separada de los precios de anuncios.
- Revisión pendiente y razones claras para priorizar, descartar o pedir información.

Un perfume de 30 ml no se compara directamente con uno de 100 ml. Un reloj usado
no se equipara a uno nuevo. Identificadores o fotografías similares no prueban
autenticidad. Una réplica no se mezcla con un original ni se promueve como original.

## Cálculo económico

```text
Ingreso neto estimado = precio de venta del escenario − deducciones de venta
Costo total = compra + adquisición + logística + otros costos del escenario
Ganancia estimada = ingreso neto estimado − costo total
Margen estimado = ganancia estimada / ingreso bruto de venta
Retorno sobre costo = ganancia estimada / costo total
```

Solo calcular resultados completos cuando las entradas obligatorias sean conocidas
y los denominadores sean válidos. `null` significa desconocido, no cero. Impuestos,
importación, transporte y comisiones requieren datos aplicables al mercado; no se
inventarán ni se dará asesoría tributaria a partir de una fórmula genérica.
Ver [modelo de costos](docs/COST_MODEL.md).

## Eficiencia y costo de ejecución

- Sin APIs pagadas por defecto; ninguna contratación o descarga de modelos implícita.
- Extracción estructurada, reglas y cálculos programáticos antes de recurrir a IA.
- IA local opcional para ambigüedades de texto o imágenes, con presupuesto explícito.
- Caché con caducidad, actualización incremental y almacenamiento por hash.
- Paralelismo limitado por dominio/cuenta; no miles de pestañas en una sesión compartida.
- Solicitudes batch solo cuando el backend realmente las soporte.
- Reintentos acotados, backoff y suspensión temporal de fuentes con fallos repetidos.
- Una ejecución CLI completa como objetivo; etapas reutilizables para depuración.

Gratuito en cargos API no significa cero tokens, electricidad, hardware o tiempo
humano. La cuota gratuita de una herramienta también puede agotarse.

## Arquitectura y calidad previstas

Arquitectura hexagonal: dominio de productos y costos separado de extracción,
persistencia, IA, CLI y dashboard. Base propuesta: Python para el pipeline,
SQLite para un primer uso local, contratos JSON versionados y una interfaz web
ligera. PostgreSQL/pgvector y TypeScript para una interfaz mayor son opciones de
evolución, no dependencias instaladas ni migraciones ya decididas.

La calidad se medirá sobre productos y oportunidades comerciales: equivalencias
correctas, precios extraídos, disponibilidad, citas, costos, falsas oportunidades y
resultados de revisión. No reutilizaremos porcentajes de otra demo como métricas
de este producto. [Arquitectura](docs/ARCHITECTURE.md) · [Evaluaciones](docs/EVALUATIONS.md).

## Documentación

- [Visión, alcance y usuarios](docs/VISION.md).
- [Lenguaje del dominio](CONTEXT.md).
- [Fuentes, acceso y evidencia](docs/SOURCES.md).
- [Arquitectura modular y flujo persistente](docs/ARCHITECTURE.md).
- [Configuración propuesta](docs/CONFIGURATION.md) · [ejemplo JSON](config/radar.example.json).
- [Costos, moneda y margen](docs/COST_MODEL.md).
- [Evaluaciones y criterios de aceptación](docs/EVALUATIONS.md).
- [Seguridad, privacidad y acciones humanas](SECURITY.md).
- [Roadmap y estado real](docs/ROADMAP.md).
- [Cómo contribuir a este repositorio](CONTRIBUTING.md).

## Verificar esta primera entrega

Requisitos para comprobar la documentación: Python 3.11+ y Git. No hace falta
iniciar sesión en redes sociales, instalar modelos ni proporcionar credenciales.

```text
git clone https://github.com/davidvalorwork/market-opportunity-radar.git
cd market-opportunity-radar
python scripts/check_docs.py
```

El control valida enlaces locales, configuración documental, archivos requeridos y
ausencia de archivos privados versionados. No busca productos ni prueba conectores.
GitHub Actions ejecuta el mismo control en Windows y Linux.

## Límites y siguiente entrega

La siguiente entrega será una vertical mínima: importar anuncios autorizados,
normalizarlos, comparar variantes y producir un reporte reproducible de costos,
sin acciones externas. Después se habilitarán fuentes una por una al verificar
acceso y extracción; la cuota de búsqueda no reemplazará la calidad de comparación.

Ni la publicación del repositorio ni un test aprobado demuestran utilidad real,
ROI o rentabilidad. Eso necesita pruebas con usuarios, revisión de casos y resultados
comerciales registrados con consentimiento.

## Licencia

[Apache-2.0](LICENSE). Copyright 2026 David Valor. La licencia del código propio
no concede derechos sobre contenidos, imágenes, marcas ni datos de terceros.
