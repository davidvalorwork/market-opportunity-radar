# A7: preparación general de contactos, evidencia y seguimiento local

Estado: candidato local offline; no Telegram `/contactar` conectado, resolver real,
cuentas, sesiones reales, envíos, IA ni infraestructura. El alcance solicitado es
general: astronomía, jardinería, libros, empleo y servicios en las pruebas son
ejemplos; talleres, vehículos, productos o Cashea no son requisitos del modelo.
No se presenta un fixture como verificación de un canal o destinatario real.

## Fronteras ejecutables

[Modelo puro](../../src/radar/application/contact/model.py) contiene selección
acotada, DTOs internos, plantillas determinísticas y comparación opcional de
cotizaciones declaradas. No importa adapters, contratos B, SDK ni red.
[LocalContacts](../../src/radar/adapters/local/contact.py) comparte las
transacciones SQLite A3 y consulta las entregas reales A6 y A8. Requiere
`SQLiteTaskStore`, `source_sink`, `vault`, `host_authority`, `resolver`,
`observations` y `clock`; no tiene backends sintéticos por defecto.

- `start(owner_ref, actor_ref, event_ref, task_ref, reports, purpose_ref,
  template, request_facts, maximum=10, filters=None, dedupe_seconds=0,
  silence_seconds=86400, followup_purpose_ref=None)` captura una plantilla
  privada sobre intención A6 confirmada y vigente al registrarla. Devuelve
  `CampaignView` con referencias, descartes, duplicados, truncación y cobertura.
- `prepare(... campaign_ref, candidate_ref, event_ref, phase='initial',
  followup_template=None)` guarda un borrador privado, no una aprobación.
  `PreparedContact.content_hash` es SHA-256 del texto UTF-8 exacto.
- `resolve(... operation_ref, proof_ref, expected_version=1)` consulta un
  repositorio independiente de resolución aprobada; no acepta un booleano ni un
  `ResolvedContact` aportado por el usuario como prueba. El binding incluye
  propietario, actor, operación, candidato, propósito, referencia privada y hash.
- `export(... operation_ref)` revalida prueba, autorización, consentimiento,
  capacidades y sesión; entrega `ConversationPreparation` para A10. El host usa
  después `Conversations.compose`, `screen` y `approve`; confirmar intención A6
  **nunca** equivale a `Approval` para enviar el mensaje.
- `note_outbound(... operation_ref, outbound_ref)` enlaza una operación A3/A10
  aprobada con destinatario, hash, propósito y sesión exactos. No crea Approval.
- `observe(... operation_ref, proof_ref)` consulta journal/lectura independiente
  y persiste estado, horizonte, fecha de confirmación y referencia privada.
  `cancel(... campaign_ref, expected_version=1)` es un tombstone idempotente;
  `status(... campaign_ref, after='', maximum=20)` pagina por clave owner-bound.

El host autenticado emite refs opacas; no se admite PII disfrazada con un prefijo.
`host_authority` se resuelve de nuevo en cada transición: propietario activo,
actor con rol owner y consentimiento vigente, capacidad contact/fuentes y
sesiones actuales. Corrección/cancelación A6 invalida la campaña. La autoridad
efímera original de A6 no concede permiso permanente: el host debe emitir una
autoridad actual y revisar de nuevo el presupuesto/capacidades del plan.

## Evidencia, privacidad y límites

Los contactos se extraen de bytes privados de `Record` A8 con procedencia, no de
un array aportado como supuesta verdad. Teléfono/email publicado es candidato
**no verificado**: no prueba WhatsApp ni disponibilidad, identidad o consentimiento.
Filtros sólo comparan hechos explícitos; duplicados combinan evidencia y retiran
hechos contradictorios. Los valores de respuestas/publicaciones son datos, nunca
instrucciones para ejecutar acciones o código.

Máximo 20 informes, 200 registros, 512 KiB por lectura privada; extracción A8
limitada a 64 candidatos por registro. Lista corta 1–50, documento privado hasta
4 MB, 32 hechos de 1 KiB, plantilla y texto renderizado hasta 4096 bytes UTF-8.
No hay campos dinámicos, atributo/index lookup, conversión/format code, LLM ni
hechos inventados. Colisiones entre hecho publicado y dato del usuario bloquean.
Cobertura parcial, errores de fuente, descartes y truncación permanecen visibles.

SQLite/WAL/eventos sólo contienen refs, hashes, estados, versiones, contadores
y timestamps UTC de seis microsegundos. Teléfonos, nombres, texto, hechos,
plantillas, URLs, condiciones y respuestas requieren vault/sink privado
autenticado, cifrado e inmutable owner-bound. Una etiqueta `recipient_scope` o
digest no es autorización ni prueba de cifrado. Los vaults de tests son RAM
sintética, **no age**. Fallo privado/resolver produce diagnóstico estático y no
convergencia ficticia; los fallos de transacción se propagan y hacen rollback.

Selección/render no ejecutan envíos. La deduplicación local se reserva
atómicamente por propietario/destinatario canónico/propósito/fase al resolver.
`dedupe_seconds=0` significa permanente; una ventana explícita 1–90 días permite
un nuevo intento local del mismo propósito después del plazo, pero **no** rebaja la deduplicación
permanente independiente de A10 por cuenta/destinatario/propósito. Repetir eventos
idénticos conserva IDs; cambiar contenido con el mismo event_ref se rechaza.

## Un seguimiento, con aprobación fresca

No hay seguimiento por defecto. El host configura al registrar la plantilla un
`followup_purpose_ref` opaco, estable, explícito y distinto del propósito inicial;
no se generan propósitos rotativos para evadir dedupe. La fase local `followup`
versión 1 se liga a la campaña, candidato, propósito base y operación inicial.
Unicidad SQLite y reserva por destinatario/propósito impiden dos seguimientos,
incluyendo confirmaciones repetidas, reinicio y concurrencia. Cambiar una
plantilla no abre otra fase dentro de la campaña.

Sólo `PROVIDER_CONFIRMED` más prueba independiente de lectura completa con
silencio durante el plazo configurable 60 segundos–90 días habilita preparar
esa única fase. Una lectura vacía/fallida, envío incierto, respuesta o estado
ambiguo no prueban silencio. La fecha de confirmación viene del journal del
proveedor; no se inventa un timestamp ausente en `LedgerRecord`.

El texto lleva prefijo visible `SEGUIMIENTO:` y pasa por nueva resolución y
nueva pantalla A10 con destinatario, identidad, cuenta, sesión, propósito y
texto completos, antes de otra Approval exacta. La aprobación anterior no se
hereda. Una respuesta posterior registrada bloquea export/resolve del borrador;
el host real todavía debe sincronizar evidencia fresca antes de la aprobación
y del efecto. No se supone conocimiento continuo de mensajes no leídos.

## Cotización opcional, no precio de venta ni ganancia

`declared_quote` admite declaración explícita, ambigua, sin respuesta o no
disponible. Moneda no soportada, precio ausente/ambiguo y silencio quedan como
desconocidos, no cero. `compare_quotes` usa `Money`/`FxRate` existentes, con tasa
direccional única fechada y vigente. Conserva faltantes y condiciones privadas;
ordena precios nominales declarados, no decide calidad ni comercialidad ni
afirma transacciones, ganancias o costos completos. Se mantiene el límite
actual del dominio: 18 monedas, sin ampliar tablas o fórmulas.

## Verificación y gates pendientes

Desde el worktree y entorno locked del proyecto, sin red:

```powershell
$env:PYTHONPATH = 'src;.'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:HYPOTHESIS_STORAGE_DIRECTORY = '.local/hypothesis'
python -m pytest -q tests/contact
python -m pytest -q tests
python scripts/check_docs.py
git diff --check
```

Las pruebas ejecutan A6 confirmado, Reader/PrivateSink A8, SQLite A3 y
Conversations A10 reales con transporte, vault, resolver y journal sintéticos.
Incluyen multitema, E2E de pantalla/aprobación nueva, replay/restart/concurrencia,
rollback, separación de propietarios, revocación/cancel/sesión/expiración,
silencio/ambiguo/uncierto, doble seguimiento, SQLite/WAL y errores sin PII,
cotizaciones/FX y referencias privadas. `dispatch_fixture` está autorizado
explícitamente sólo por el TEST y canales fixture-only: no verifica efecto real.

Pendientes para A7 completo: comandos/UX Telegram general, vault cifrado real,
host/proof resolver autorizado y aprobado por usuario, journal de confirmación
y cobertura de conversaciones real, sincronización antes del efecto, integración
de ejecutores y revisión B/Go. `enable_chat` A10 es habilitación administrativa,
no prueba de que el teléfono hallado pertenece al destinatario. No se utilizan
schemas B nuevos ni un envío ficticio como contrato wire. El gate de replay
Go/WhatsApp real permanece cerrado: no se envía, reconcilia ni habilita live
por el simple hecho de que los tests offline pasen.

Cancelar A7 conserva el outbox A10 y añade tombstone A3 para operaciones enlazadas
propuestas/aprobadas/claimed: el claim local no puede enviar lo cancelado. No
reescribe confirmaciones o incertidumbre como si un efecto ya ocurrido se pudiera
deshacer. El host debe enlazar `note_outbound` antes del enqueue/efecto y revalidar
la campaña para corrección/cancelación A6: A10 por sí solo no conoce el origen A7.
El seguimiento mantiene cuenta/chat/canal/destinatario del mensaje inicial;
cambiar un proof a otra persona no es un seguimiento permitido.

## Frontera de investigación y respuesta en cualquier etapa

El usuario también requiere leer una pregunta/mensaje nuevo, investigar el tema
en la web y preparar una respuesta fundada, no sólo la plantilla inicial. Este
corte conserva referencias de evidencia A8 y trata hechos/respuestas como datos,
pero **no implementa** el motor general `contexto privado + investigación por
refs + síntesis de respuesta` ni lectura web/chats real. Ese wiring pertenece al
runtime general y debe aceptar evidencia privada adicional en cualquier etapa,
mantener fuentes/fechas/desacuerdos y pasar cada mensaje por nueva aprobación
exacta A10. No enviar PII de chats ni mensajes privados como queries públicas;
una instrucción de un proveedor o publicación no autoriza investigación, acceso
a una cuenta ni envío. No se ejecutó investigación web ni autoaprobación aquí.
