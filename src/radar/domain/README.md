# Dominio A2: productos, sin conectores ni acciones externas

`core.py` contiene valores inmutables; `states.py` guardas puras de transición;
`verticals/products.py` normalización, matching y economía determinísticos.
No importa adaptadores, puertos, SDK, red, persistencia o LLM.

## Entradas y límites explícitos

- Money acepta Decimal/string decimal ordinario, nunca bool/float. Soporta una
  lista MVP explícita de 18 códigos ISO 4217; no todos los códigos históricos o
  actuales. No autodetecta monedas ni unidades desde símbolos/títulos.
- Sumas/productos monetarios exactos con límite 64 dígitos: si redondearían, fallan
  con precision_exceeded en lugar de perder valor. Ratios usan precisión 64 y
  HALF_EVEN independientes del contexto ambiental. Redondear para mostrar es
  responsabilidad del reporte; asignación explícita es la única excepción.
- FxRate expresa unidades cotizadas por unidad base, fuente, fecha UTC y
  vencimiento exclusivo. No hay inversión, triangulación ni paridad implícitas.
  Economics conserva las tasas utilizadas para trazabilidad.
- Scenario modela liquidación del lote completo: unidades vendidas = adquiridas.
  MOQ es mínimo de lotes; capital es el desembolso previo del pedido seleccionado.
  Inventario parcial, financiación, cuotas y devoluciones necesitan otro modelo:
  no se equiparan al precio total de adquisición/venta de este escenario.
- Acquisition_lot es precio por lote. Incoming/outgoing son importes totales del
  escenario, nunca precios por unidad. Listas de cargos requeridos + fuente del
  plan explicitan aplicabilidad; impuestos/aranceles/seguros desconocidos deben
  figurar como cargos requeridos desconocidos. El llamador no debe omitirlos para
  fabricar rentabilidad; el dominio no puede adivinar qué tributo aplica.
- Percentage_charge exige base, tasa fraccional y fijo explícitos, incluyendo un
  cero con procedencia si no existe fijo. No incluye tarifas de ningún país.
- Allocate reparte costo conocido por pesos positivos y quantum explícito
  (p. ej. Decimal("0.01")); último elemento conserva el residual. No infiere que
  transportar unidades faltantes sea gratis.
- Costos ausentes/futuros, FX inexistente/vencido/ambiguo dan incomplete y no
  profit. Estimated sigue estimated incluso con todos los campos completos.
  Cero en precio de adquisición/venta exige revisión; no se promueve por score.
- Matching devuelve compatible/conflict/uncertain y razones. No verifica
  autenticidad ni usa una foto/título para completar volumen, edición o condición.
- Score es una banda de margen estimado, no probabilidad, ROI realizado ni prueba
  de demanda. Monotonía solo manteniendo iguales costos y evidencia/matching.
- Las guardas de aprobación ligan acción exacta/expiración. Autorización server-
  side, CAS, leases y ledger durable siguen correspondiendo al flujo/puertos.
  Send_uncertain jamás vuelve a approved/dispatch_committed automáticamente.
- Allowed_actions del MVP solo devuelve inspección/revisión, nunca compra/envío.

## Pruebas

`python -m pytest -q tests/domain` usa fixtures sintéticos y Hypothesis; no acredita
revisión humana, operación comercial, ganancias o fuente real. El Dockerfile
`docker/python.Dockerfile` prueba el wheel instalado y contratos sin red; contexto
allowlist y dependencias fijadas en requirements.lock, extra test del paquete.
