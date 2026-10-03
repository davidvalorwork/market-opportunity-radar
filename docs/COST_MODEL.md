# Modelo de costos y escenarios

Diseño pendiente de implementación. Resultados son estimaciones para comparar
escenarios, no asesoría financiera/fiscal ni rentabilidad garantizada.

## Entradas y trazabilidad

Cada importe tendrá valor decimal, moneda, unidad, procedencia, fecha y estado
`known`, `estimated` o `unknown`. El anuncio puede expresar precio por unidad, lote,
financiación, descuento condicionado o importe negociable; no son intercambiables.

Costo de compra por unidad solo se obtiene al conocer unidades del lote. El pedido
mínimo afecta el capital necesario aunque el margen unitario sea atractivo.
Dividir transporte de un lote necesita una regla de asignación visible; no duplicar
el costo completo en cada unidad ni asumir que es gratis.

## Definiciones de cálculo

```text
Ingreso bruto = precio unitario de venta del escenario × unidades vendidas del escenario
Deducciones de venta = comisiones + cobro/pago + logística de salida + costos de venta aplicables
Ingreso neto = ingreso bruto − deducciones de venta
Costo total = producto + adquisición + logística de entrada + costos asignados aplicables
Ganancia estimada = ingreso neto − costo total
Margen sobre venta = ganancia estimada / ingreso bruto
Retorno sobre costo = ganancia estimada / costo total
Capital requerido = desembolsos previos a venta para el pedido mínimo del escenario
```

Ganancia, margen y retorno se mantienen distintos. El capital no siempre es igual
al costo total: algunos cargos pueden pagarse después de vender. Explicar bases
de comisiones y momento de cada cargo para no contarlo dos veces.

Solo calcular porcentajes con denominadores positivos. Precio cero sospechoso,
valores negativos, monedas incompatibles y cantidades ausentes requieren revisión.

## Moneda y costos ausentes

Convertir importes a una moneda de escenario con tipo de cambio explícito: moneda
base, cotizada, unidades cotizadas por una unidad base, fuente y fecha. Tipos de cambio
caducados o inexistentes hacen incompleto el cálculo; no asumir paridad.

No rellenar impuestos, aranceles, seguro, transporte o comisiones desconocidos con
cero. Cero es una afirmación explícita que debe tener procedencia. Un candidato
incompleto conserva precios y datos faltantes, pero no entra como rentabilidad
confirmada ni se ordena como si tuviera costo total conocido.

No existen tarifas tributarias universales: los costos aplicables dependen de la
operación, país, ruta, categoría y vendedor. Se configuran o se obtienen de una
cotización autorizada; confirmar con fuentes pertinentes antes de operar.

## Ejemplo exclusivamente hipotético

Todos los valores son supuestos inventados, USD, una unidad; no describen una oferta:

| Concepto | Importe |
|---|---|
| Producto | 50 |
| Logística de entrada | 6 |
| Adquisición | 2 |
| Empaque | 2 |
| Otros costos de adquisición | 0, supuesto explícito del ejemplo |
| Venta del escenario | 90 |
| Comisión sobre venta, 10% | 9 |
| Cobro fijo | 1 |
| Otros costos de venta | 0, supuesto explícito del ejemplo |

Costo total 60; ingreso neto 80; ganancia estimada 20; margen sobre venta 22,22%;
retorno sobre costo 33,33%. No inferir que el precio de venta será alcanzado.

## Comparables y sensibilidad

Separar anuncios de venta de solicitudes de compra y de transacciones observadas.
Mediana de precios publicados compatibles es un comparable, no una predicción de
venta. Cantidad, confianza, fecha y rango de precios deben acompañarla.

Evaluar escenario conservador/base/optimista solo con supuestos identificados.
Mostrar qué pasa al variar venta, transporte, comisión y cambio. Estimar punto de
equilibrio requiere especificar qué cargos son fijos o proporcionales y sus bases.

## Criterios de aceptación futuros

Tests con aritmética decimal, redondeo documentado, lote/MOQ, múltiples monedas,
comisiones con bases diferentes, costos desconocidos, denominadores inválidos,
devoluciones registradas y entradas conflictivas. El parser y un LLM no pueden
alterar fórmulas ni inventar entradas para producir un margen positivo.
