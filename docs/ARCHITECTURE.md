# Arquitectura propuesta

Estado: diseño, no componentes instalados o funcionales. Un monolito modular local
con arquitectura hexagonal es la primera opción; no microservicios por cada plataforma.

## Límites y puertos

| Área | Responsabilidad | Puerto / contrato conceptual |
|---|---|---|
| Dominio | Producto, variante, anuncio, moneda, costo y oportunidad | Entidades y reglas sin navegador, red, LLM ni base de datos |
| Aplicación | Planificar corrida, coordinar etapas y reanudar trabajo | Casos de uso con IDs y configuración versionada |
| Descubrimiento | Encontrar URLs/anuncios y solicitudes de compra | `discover(query, cursor) -> page + coverage` |
| Lectura | Extraer datos permitidos de anuncios encontrados | `fetch(reference) -> evidence + typed_fields` |
| Equivalencia | Resolver identidad/variante/condición/unidad | `match(a, b) -> compatible/conflict/uncertain + reasons` |
| Economía | Evaluar escenarios con datos conocidos | `estimate(scenario) -> complete/incomplete + breakdown` |
| Persistencia | Guardar anuncios, hashes, historial y checkpoints | Repositorios transaccionales y migraciones explícitas |
| IA opcional | Resolver casos ambiguos con presupuesto limitado | Entrada/salida estructurada y evidencia; nunca controla acciones externas |
| Presentación | CLI, reportes y dashboard | Mismos casos de uso; no duplicar cálculos en frontend |
| Telemetría | Trazas, métricas y errores sin secretos | Eventos correlacionados y exportador opcional OpenTelemetry |

Los nombres de métodos son contratos de diseño, no APIs ejecutables existentes.

## Tecnología inicial propuesta

Python 3.11+ para orquestación y cálculo decimal; SQLite para uso local y checkpoints;
JSON versionado para contratos/configuración. Subprocesos acotados invocan Agent
Reach/OpenCLI; nunca concatenar contenido de anuncios en un shell.

La interfaz inicial puede ser HTML/JS ligera. TypeScript/React, PostgreSQL y pgvector
se introducirían cuando haya requisitos comprobados de interfaz, concurrencia o
recuperación semántica. No instalar frameworks/modelos para aparentar progreso.
Conservar tests de dominio independientes de credenciales y redes sociales.

## Etapas persistentes

```text
planned → preflight → discovering → extracting → normalizing
        → matching → estimating → pending_review → reported
```

Corridas también pueden estar `paused`, `failed`, `cancelled` o `degraded`; tareas
individuales conservan etapa, último error y fecha del próximo intento. Una fuente
agotada es distinta de una fuente que falló. Detener la corrida no debe perder lo leído.

El checkpoint incluye configuración/hash, cursor, fuente, anuncio, versión de
extractor/matcher/calculadora, escenario de costos y dependencias. Reanudar no
duplica anuncios u oportunidades y no usa resultados obsoletos sin invalidación.
No hay efectos de compra/venta o mensajes que deduplicar en el MVP de solo lectura.

## Paralelismo y batch

- Pool global limitado y cuota por dominio/cuenta/backend.
- Lecturas HTTP independientes pueden ser concurrentes; una sesión de navegador
  compartida que cambia de pestaña requiere serialización o workers aislados compatibles.
- APIs batch donde exista soporte probado; no simular batch con pestañas ilimitadas.
- Deduplicar antes de lecturas caras y de inferencia; cachear entradas por contenido/versiones.
- Reintentar solo errores transitorios; backoff con jitter y límites por corrida.
- Circuit breaker por fuente; mantener estado explícito de cobertura incompleta.

## Matching y recuperación

Primero GTIN/EAN/SKU cuando sean fiables y aplicables, marca/modelo/variante,
condición, volumen, unidad y tamaño de lote. Conflictos críticos no se resuelven por
similitud de texto o fotos. Identificadores declarados no prueban autenticidad.

Como evolución, comparar búsqueda textual, vectorial e híbrida sobre el mismo
corpus y consultas anotadas. pgvector ofrece un ejemplo de [búsqueda híbrida](https://github.com/pgvector/pgvector#hybrid-search).
Embeddings no son obligatorios para la primera comparación ni prueban equivalencia.

## Telemetría y observabilidad

Trazar `run -> discovery -> fetch -> parse -> match -> estimate -> review`, con
IDs correlacionados, tiempos, intentos, fuente, estado de caché y clase de error.
Métricas: anuncios únicos, cobertura por fuente, extracción correcta, candidatos
compatibles, costos desconocidos, oportunidades revisadas/aceptadas, duplicados,
latencia, consumo local/API e inferencias evitadas.

Las convenciones GenAI de [OpenTelemetry](https://github.com/open-telemetry/semantic-conventions-genai)
están en desarrollo; fijar versiones al adoptar el exportador. No registrar prompts,
cookies, mensajes privados o contenido sensible por defecto. Un fallo de exportación
no debe bloquear cálculos locales; sí debe quedar visible.

## Despliegue y seguridad

Uso local primero. Antes de un dashboard multiusuario: identidades autenticadas,
permisos por proyecto/operación, aislamiento de datos, auditoría y protección de
sesiones. No exponer el servidor local en Internet sin esa implementación y pruebas.
No hay despliegue cloud ni servicios provisionados en esta entrega.
