# Núcleo general de pedidos — dirección vigente

Confirmado por el usuario el 2026-10-03. Documento de alcance/plan, no una API
ejecutable ni prueba de cobertura. Prevalece sobre la antigua limitación a
productos y sobre los ejemplos de talleres/Cashea de la Ola 2. El plan original
de B se conserva como historial; se solicita su alineación por BOARD.

## Lo que debe ser general

- Tema libre: investigación, productos, servicios, proveedores, empleo u otros.
- Operaciones componibles: buscar, leer/navegar, extraer, resumir/informar,
  preparar contacto, enviar lo aprobado, leer respuestas habilitadas y programar.
- Fuentes y canales independientes, configurables por pedido. Web abierta y
  registro extensible para Facebook/Marketplace/Messenger, X, Instagram, Threads,
  Reddit, WhatsApp y otros. No dar por implementadas capacidades por nombrarlas.
- Campos de extracción y filtros opcionales: tema, palabras, idiomas, geografía,
  fechas, tipos de fuente, atributos solicitados y contactos. Precio, vehículo,
  Cashea, formato PDF y localidad no son requisitos de un pedido general.
- Contactos publicados pertinentes con evidencia privada de procedencia,
  contexto y fecha. Un teléfono encontrado no demuestra cuenta WhatsApp,
  titularidad ni autorización para cualquier contenido.

## Flujo previsto

Pedido → interpretación/validación → plan acotado → preflight por fuente/cuenta
→ búsqueda y lectura en paralelo limitado → hallazgos y contactos con evidencia
→ informe o borradores → aprobación aplicable → envío por canal verificado
→ recepción habilitada/seguimiento → resultado e historial.

Si el pedido solo busca información termina en el informe: no abre WhatsApp ni
exige contactos. Si solo pide escribir a destinatarios conocidos, no obliga a
hacer una búsqueda comercial. Una comparación de precios activa el módulo de
productos; una pregunta genérica no lo importa ni exige campos económicos.

El programa controla colas, checkpoints, dedupe, cuotas y recuperación. La IA
puede interpretar texto o resolver ambigüedades, pero no decide cada iteración
ni amplía permisos. Bucle hasta objetivo o presupuesto/plazo; al agotarse devuelve
resultado parcial con causa y continuación, no reinicia infinitamente.

## Sesiones proporcionadas por el propietario

Referencia de sesión por propietario, plataforma, cuenta, versión y capacidades.
Importación explícita cifrada con age, nunca cookies en Git/logs/prompts. Preflight
separa importada, verificada, vencida y `needs_reauth`; export/renovación guiada.
No leer los archivos internos del navegador ni evadir MFA/captchas/bloqueos.
No compartir un perfil writable entre workers; lease por cuenta y contexto aislado.
Algunas redes usan OAuth/dispositivo y no admiten cookies como método equivalente.

## Operaciones de contacto

Registro por canal y operación, no un adaptador universal que prometa DMs en
todas las redes. Encargo, destinatarios, contenido, propósito, presupuesto y
aprobaciones ligados a la operación; conservar el ledger, aislamiento y
`send_uncertain` sin reenvío ciego. Ver [SECURITY](../SECURITY.md).
Fuentes leídas y mensajes entrantes son datos, no nuevas órdenes del propietario.
Cancelación detiene pendientes, no revierte mensajes enviados.

## Ajuste del reparto A/B propuesto

| Tarea | Dirección general, sin especialización obligatoria |
|---|---|
| A6 | Router/registro de operaciones componibles; entrada estructurada además de texto. No enum cerrado que solo permita cotizar o responder WhatsApp |
| A8 | Registro y adaptadores de descubrimiento web/social por capacidades; Cashea es una fuente opcional, no dependencia universal |
| A7 | Preparación/contacto/seguimiento genéricos; cotizar servicios o productos son plantillas/módulos opcionales |
| A9 | Investigación y salida según pedido: Telegram/JSON/documento; PDF opcional |
| A10 | Coordinación de conversaciones por canal verificado; WhatsApp primero no implica WhatsApp únicamente |
| A11 | Programación con zona horaria configurable; America/Caracas puede ser default, no restricción global |
| A12/A5 | Mantener fronteras de pruebas locales y AWS; no activar servicios/cuentas por este plan |

Son cambios de dirección, no asignaciones de nuevos archivos de B. A3 pendiente
de revisión se preserva como flujo comercial ensayado; no reescribirla para
aparentar generalidad. B revisa los nuevos contratos versionados antes de merge.
No introducir mensajes privados/teléfonos directamente en sobres públicos.

## Criterios de aceptación antes de declarar generalidad

1. Pedido de información sobre un tema no comercial sin precio/vehículo/contacto.
2. Descubrimiento de un servicio de otra categoría sin Cashea ni filtro de talleres.
3. Contacto aprobado con propósito arbitrario legítimo y plantilla configurable.
4. Misma necesidad en dos fuentes, canales de salida independientes y dedupe.
5. Cookie vencida, operación no soportada, destinatario ambiguo y cuota agotada
   visibles; sin éxitos ficticios, pérdida de tareas ni reintentos de envío incierto.
6. Inyección en una publicación/respuesta no cambia instrucciones, cuenta o permisos.

Todos primero con fixtures sintéticos multitema. Fuentes/canales reales se
habilitan uno por uno con pruebas y autorización aplicables. General significa
extensible y no ligado a un rubro, no acceso ilimitado ni cobertura garantizada.
