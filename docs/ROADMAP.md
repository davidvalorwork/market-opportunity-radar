# Roadmap y criterios de salida

Actualizado: 2026-10-03. No se prometen fechas ni ingresos. La incorporación de una
fuente depende de acceso autorizado, capacidad comprobada y fixtures reproducibles.

## Fase 0 — Base documental (esta entrega)

- Nombre, descripción, README, visión, dominio y arquitectura.
- Matriz de fuentes con límites, configuración de ejemplo y modelo económico.
- Plan de evaluaciones, seguridad y roadmap.
- Licencia Apache-2.0 y controles documentales sin secretos ni servicios externos.

Criterio: enlaces/configuración/documentación coherentes y repositorio publicado.
No equivale a un buscador operativo.

## Laboratorio habilitador — entrega local 2026-10-03

Navegador Lambda/RIE con Playwright y OpenCLI, sesiones age con versiones/CAS,
wrapper de upload/renew explícito y runner de recursos/costos aproximados.
[Resultados y pruebas negativas](testing/LOCAL_RESULTS.md). Implementación
experimental con fixtures, no una fase comercial terminada ni soporte de redes
sociales demostrado. Captura guiada, cuenta de ensayo y canary AWS son posteriores.

## Fase 1 — Vertical mínima local

- Importar CSV/JSON autorizado y fixtures sintéticos tipados.
- Normalizar precio/moneda/variante/condición/unidad.
- Matching determinístico con conflictos y candidatos inciertos.
- Escenarios de costo decimal y reporte de datos faltantes.
- CLI única, SQLite, checkpoints y tests de dominio/reinicio/idempotencia.

Criterio: mismo input/configuración produce reporte reproducible; casos de comparación
inválida y costos desconocidos no generan oportunidades ficticias. Sin red ni IA obligatoria.

## Fase 2 — Web y descubrimiento

- Preflight, lector público permitido, URLs/feeds y adaptadores por dominio.
- Caché, evidencia, historial, cobertura, timeouts y reintentos acotados.
- Descubrimiento incremental y fuentes de búsqueda con cuotas explícitas.

Criterio: lecturas reales verificadas en dominios seleccionados, errores visibles,
sin claims de cobertura universal. Documentar fuentes que fallan.

## Fase 3 — Marketplaces y redes sociales

- Mercado Libre y canales Agent Reach/OpenCLI soportados y autorizados.
- Facebook/publicaciones/grupos disponibles; Instagram por cuentas; X según capacidad real.
- Configuración por cuenta/país, aislamiento de sesiones, cuotas y paginación.
- Fuentes opcionales degradadas y requeridas bloqueantes antes de gastar presupuesto.

Criterio: un conector probado por fuente habilitada y límites documentados. Marketplace
de terceros no se presume disponible porque existen comandos de anuncios propios.

## Fase 4 — IA opcional, búsqueda y evaluación rigurosa

- Parser determinístico primero; modelo local instalado para ambigüedades acotadas.
- Comparación textual/vectorial/híbrida si mejora un caso de recuperación concreto.
- Dataset revisado por humanos, conjunto reservado, múltiples intentos y gates por caso.
- Prompt/modelo/matcher/costo versionados; errores y salidas originales conservados.

Criterio: mejora demostrable frente a baseline equivalente y sin regresiones críticas.
No usar precisión de datos sintéticos como utilidad comercial validada.

## Fase 5 — Dashboard, observabilidad y piloto

- Interfaz ligera con filtros, detalle de evidencia y supuestos económicos.
- Eventos/trazas correlacionadas y métricas de revisión/cobertura/costo.
- Autenticación, permisos e aislamiento antes de cualquier uso multiusuario.
- Piloto consentido con búsqueda manual comparable y feedback del usuario.

Criterio: flujo end-to-end probado; resultados reales distinguidos de estimaciones;
seguridad y accesos negativos comprobados antes de exposición de red.

## Evolución posible, no compromiso actual

Más categorías/mercados, inventario propio, precios de transacciones autorizadas,
alertas consentidas, PostgreSQL/pgvector, exportación OpenTelemetry y servicios cloud.
Acciones externas requieren un diseño y autorización separados. No hay campañas
de contacto ni contribuciones a otros repos dentro de este roadmap.
