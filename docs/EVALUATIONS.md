# Evaluaciones comerciales y evidencia

Plan de validación; no hay benchmarks ejecutados de productos o ganancias en este
repo. Los resultados del Evidence Lab profesional no son métricas de este proyecto.

## Qué debemos demostrar

| Dimensión | Caso de prueba / evidencia |
|---|---|
| Extracción | Precio correcto, moneda, lote, descuento condicionado, disponibilidad y campos ausentes |
| Equivalencia | Misma marca/modelo/variante/unidad/condición; rechazo de originales vs réplicas y tamaños distintos |
| Economía | Cálculo decimal correcto, costos desconocidos, conversión fechada, MOQ y bases de comisiones |
| Recuperación documental | Documento/anuncio correcto en búsquedas textual/vectorial/híbrida sobre el mismo corpus |
| Evidencia | Campo respaldado por fuente y fecha; fragmento existente no equivale a conclusión sustentada |
| Persistencia | Reinicio, reintento, deduplicación, invalidación y checkpoints no alteran resultados |
| Seguridad | Autorización, aislamiento, SSRF, contenido malicioso y ausencia de acciones externas |
| Utilidad | Revisión humana, comparación con procedimiento manual y resultado real cuando exista |

## Fixtures y conjuntos

Comenzar con pares y anuncios sintéticos que representen errores frecuentes: 30/100
ml, paquetes/unidades, usados/nuevos, precios en monedas distintas, cuotas de pago,
promoción vencida, originales/réplicas y solicitudes de compra sin presupuesto.

Etiquetas incluyen autor, procedencia, criterio, reviewer y fecha. `pending_human_review`
no puede convertirse en validado porque un agente generó el archivo o el test pasó.
Un revisor debe resolver desacuerdos y documentar cambios de política.

Separar desarrollo, capacidades y regresiones. Mantener un conjunto reservado fuera
del ajuste de prompts/reglas. Su independencia depende de quién lo creó y quién pudo
verlo; un archivo llamado holdout no acredita por sí solo independencia estadística.

Los snapshots reales autorizados se mantienen privados o anonimizados con permiso.
No publicar anuncios, imágenes o contactos si no hay derechos y consentimiento aplicables.

## Repeticiones y gates

Para componentes determinísticos: resultado estable y tests exactos. Para IA opcional:
varios intentos por caso con entorno limpio y configuración/modelo/versiones fijados.
Reportar todos los intentos, consistencia, errores y costo; no elegir el mejor intento.

Regresiones por caso y checks críticos deben bloquear cambios aun si mejora el promedio.
Las pruebas de capacidades pueden tener umbrales de producto distintos, definidos
antes de ejecutar; no bajar el umbral para producir un estado verde.

Verificar el estado final persistido y el cálculo, no solo el mensaje de éxito del
agente. Ningún LLM juez se considera calibrado sin contraste con evaluación humana.

## Métricas

- Cobertura: consultas/lecturas intentadas, exitosas, fallidas y limitadas por fuente.
- Extracción: acierto por campo, errores de unidad/moneda y datos desconocidos.
- Matching: precisión, recall, conflictos y pares críticos incorrectamente aceptados.
- Búsqueda: Recall@k/MRR con relevancia anotada y mismo corpus/consultas para variantes.
- Economía: cálculos válidos, escenarios incompletos y supuestos desactualizados.
- Operación: duplicados, fallos, recuperación, caché y latencia mediana/p95 con muestra.
- Revisión: candidatos aceptados/corregidos/rechazados y minutos por decisión útil.
- Costo: API, tokens e inferencias; cómputo y revisión desconocidos quedan `null`.
- Negocio: resultados comerciales reales separados de escenarios de margen.

Si el denominador es cero o no hay etiquetas, la métrica no se inventa. No presentar
likes, resultados recuperados o diferencias entre anuncios como ingresos reales.

## Piloto de utilidad

Con una persona y sus datos autorizados: elegir productos y mercados, registrar
búsqueda manual comparable, revisar recomendaciones sin ocultar fallos y observar
costos/tiempo. Publicar solo evidencia autorizada. Tamaño/periodo se documentan;
una sesión no demuestra demanda global ni ROI sostenible.

## Referencias

- [Anthropic: Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents): repeticiones, outcomes y separación capacidades/regresiones.
- [Eugene Yan: Product Evals](https://eugeneyan.com/writing/product-evals/): criterios específicos del producto y contraste humano.
- [Promptfoo con Ollama](https://www.promptfoo.dev/docs/providers/ollama/): comparador opcional local futuro; no está instalado por este repo.
