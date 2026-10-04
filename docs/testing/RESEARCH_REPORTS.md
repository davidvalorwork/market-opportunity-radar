# Preparación local de informes generales A9

Estado: **candidato aislado con extracción determinística y formatos privados**,
no servicio de investigación en toda la web ni A9 completa. Fuentes reales,
PDF solicitado, UI y entrega real todavía necesitan autorización, cableado y
pruebas. No hubo llamadas IA, red exterior, cuentas, cookies, AWS, Docker o envío.

La dirección general vigente prevalece sobre el antiguo ejemplo de investigación
comercial/PDF obligatorio. Se admiten temas arbitrarios, sin precio, vehículo,
taller, Cashea, contacto o WhatsApp requeridos. La UI de producto prevista sigue
siendo Telegram; estas APIs son preparación interna, no una segunda interfaz.

## Implementación y evidencia

[Application/research](../../src/radar/application/research/) depende sólo de
dominio, puertos y módulos puros propios/A6. No importa adaptadores, SQLite,
HTTP, navegador, crypto o SDKs. Cinco piezas pequeñas separan DTOs/protocolos,
extracción literal, verificación de citas, renderers y coordinación.

`ReportBuilder` requiere resolución de fuentes, autorización, vault cifrado y
reloj **inyectados**. `ResearchRequest` acota fuentes únicas, campos opcionales,
formatos, vencimiento y `ResearchBudget`: fuentes, notas, bytes de entrada,
bytes por artefacto y máximos Decimal de costo total/por lectura. No puede obtener
permiso por un campo del corpus o salida de modelo.

El resolver devuelve bytes UTF-8 observados, owner/source/capability refs, fecha,
hash, procedencia y referencia de evidencia. El constructor comprueba que el hash
coincide con **esos bytes**, no una URL o un hash inventado. Si no hay una referencia
inmutable de la captura, exige sellar los bytes en el vault. Referencias recibidas
mantienen su audiencia original: `worker:sources` no se transforma en
`worker:research`. Los hashes y audiencias no autentican por sí solos al productor.

Las notas por defecto copian líneas no vacías o campos literales `campo: valor`.
No hay resumen semántico, clasificación del contenido ni inferencia IA. Spans
son índices de caracteres del UTF-8 decodificado; cada cita conserva source ref,
fecha, hash del documento, hash del fragmento realmente observado y origen/página.
La codificación de evidencia distingue `raw_utf8` y `content_b64_json` de A8.
Un extracto exige igualdad literal entre afirmación y fragmento. Notas aportadas
por el caller se etiquetan `inference` o `unverified`, no se elevan a hechos.

Una cita válida demuestra asociación con la captura, **no verdad, revisión humana,
autenticidad, independencia o corroboración de la afirmación**. Una segunda fuente
puede copiar la primera. No se inventa una fuente al faltar evidencia: citas
desconocidas/ausentes/fuera de rango o texto mal copiado descartan esa nota y dejan
un resultado parcial con motivo. Fuentes fallidas, bloqueadas o no consultadas
tienen estados explícitos; no se cuentan como cobertura exitosa ni ausencia de datos.
`succeeded` sólo indica preparación completa del material/citas/formatos admitidos;
no prueba relevancia semántica, respuesta correcta o logro del objetivo del usuario.

## Privacidad, autorización y cuotas

Topic, corpus, notas, URLs y artefactos son privados por defecto. La respuesta
`Report` contiene sólo refs/hashes, cobertura/estado, clases estáticas de error,
cantidad de notas y fechas; no tiene body, topic ni texto del corpus. Los DTOs
de entrada ocultan esos campos en `repr`. No se persiste corpus/report plaintext
en SQLite, control o telemetría, ni se publica una URL de descarga.

La implementación exige un `ReportVault.seal(owner_ref, plaintext)` que entregue
punteros inmutables bajo `worker:research`. La frontera de producción debe cifrar
de verdad, verificar owner/autenticación, cuotas, metadatos opacos y durabilidad.
No hay implementación criptográfica ni fallback raw en A9. Las pruebas usan
vaults privados **en memoria, sintéticos y sin garantía criptográfica**; no pueden
cablearse a usuarios. Hash de esos fixtures no se describe como age/ciphertext real.

`ResearchAccess` debe revalidar owner/actor, consentimiento, tarea confirmada,
vencimiento/cancelación y capacidades/refs privadas actuales en cada check. Se
consulta antes/después de resolver y sellar, y se comprueban también fuentes
anteriores antes/después de crear artefactos. Revocación falla cerrada, sin entregar
informe; puede quedar un blob cifrado huérfano, no una autorización o envío.
Un URL sólo se muestra como texto si la frontera lo clasificó público; no se
obtienen URLs privadas o arbitrarias de un LLM. Las protecciones de DNS/redirect/
SSRF y adquisición acotada pertenecen al resolver autorizado, no a este renderer.

`reserve_read` es el puerto de reserva durable condicional antes de I/O. Su backend
debe contabilizar replays/concurrencia y tareas A6 junto con lecturas reales; A9 no
añade otro ledger SQLite ni promete una ejecución única. Los tests de reserva son
fakes explícitos. Max fuentes/notas/bytes/costo y deadline dejan resultados parciales;
un presupuesto menor que el esqueleto mínimo falla `output_budget_too_small`.
No se inicia otra lectura vencida. El resolver/vault debe aplicar también sus
propios timeouts/cancelación/límites durante I/O: este constructor síncrono no
cancela un SDK bloqueado ni prueba su implementación.

Costos reservados y costos declarados por el resolver son distintos. Una lectura
con costo desconocido conserva sus notas pero marca `source_cost_unknown` y no
inicia más lecturas. Una fuente fallida tampoco se contabiliza como costo cero.
Costo de cómputo y costo total real siempre permanecen desconocidos en este corte.
Precios/costos sintéticos de fixture no son mediciones de proveedor o AWS.

## Formatos solicitados

- `telegram`: chunks privados de texto plano, `parse_mode: null`, previews de
  enlaces deshabilitados, máximo 3800 unidades UTF-16 por chunk. No se envían
  mensajes ni se invoca el Bot API; su frontera futura debe respetar estas opciones.
- `json`: documento estructurado privado completo, con citas, coverage, cuotas,
  errores y límites. No es un nuevo contrato público/envelope de B.
- `document`: HTML UTF-8 autónomo, todo contenido escapado; sin JS, CSS, href,
  imágenes, recursos activos o URLs públicas de publicación. Los URLs clasificados
  se muestran sólo como texto. Un CSP estático añade `default-src 'none'`.
- `pdf`: **opcional y pendiente**, siempre `partial + pdf_renderer_pending`; no
  se fabrican bytes `%PDF`, no se modifica el worker ni se llama Chromium. El JSON
  privado sigue disponible para retomar con un renderer autorizado posterior.

El resultado depende determinísticamente de las capturas, spans, campos, reloj y
cuotas dados. Referencias aleatorias/ciphertext de un vault real pueden diferir:
no se promete identidad binaria entre cifrados nuevos ni determinismo de un LLM.
HTML escapado no demuestra que una afirmación maliciosa sea cierta o inocua; la
fuente permanece como datos y no modifica operaciones/permisos.

## Interoperación local, sin integración en main

Base A6 `5640f3b`; dependencia A8 `6324b9` fusionada **sólo en esta rama** para
pruebas, sin editar sus archivos. `from_confirmed` recarga la propuesta a través
del repositorio confiable, recarga la versión actual y comprueba estado, hash,
owner/actor, plazo y
presupuesto; sólo planes de investigación sin contacto/follow/programación se
admiten en esta frontera. `confirmation_hash` confirma intención, nunca sirve
como `Approval` por destinatario/mensaje ni autorización de un envío.

[Test A6/A8](../../tests/research/test_interoperation.py) usa SQLiteTaskStore real
con vault falso en memoria, un pedido estructurado de ciencia/literatura confirmado
y ninguna sesión WhatsApp. A8 se ejecuta con `Reader`/transport/sink **fixture-only**;
la conversión a `SourceMaterial` está únicamente en el boundary del test, no en
application ni en un factory de producción. Lee el JSON privado por owner,
decodifica `content_b64`, preserva hash de blob/audiencia y origen/página/fecha.
La URL con query permanece privada. Costo A8 no observado se conserva desconocido:
el informe de interoperación es parcial, no un éxito comercial o de sourcing real.

## Pruebas y reproducción

Corte de área observado: **46 tests A9 + 31 de arquitectura**, cero skips/fallos.
Suite completa final: **707 tests + 246 subtests**, cero skips/fallos, 46.55 s;
guarda documental y diff aprobados. Incluye A6 y A8 candidatas, no main integrado.
El corte anterior dio 701 + 246 antes de los seis casos adicionales; se volvió a
correr la suite entera sobre el código final, sin sustituir pruebas por omisiones.

Incluye citas ausentes/desconocidas, hash/fechas incorrectos, fuente caída,
presupuestos, timeout, owner/capability sustituidos, revocación dentro del vault
y de una fuente anterior, inyección, HTML inerte, Unicode/Telegram, ausencia de
corpus privado en control/repr/logs, parser rechazado, PDF pendiente y A6/A8.

Comandos desde el worktree (venv estable ya existente):

```powershell
$env:PYTHONPATH = "$PWD/src;$PWD"
$env:HYPOTHESIS_STORAGE_DIRECTORY = "$PWD/.local/hypothesis"
$reportPython = 'C:/Users/David/projects/mor-a-governance/.local/b1-review-venv/Scripts/python.exe'
& $reportPython -m pytest -q tests/research tests/test_architecture.py
& $reportPython -m pytest -q tests
& $reportPython scripts/check_docs.py
git diff --check
```

Gates pendientes: revisión cruzada B e integración serial, decisión de búsqueda/
proveedor/costo y sourcing real autorizado, vault y reserva durable de producción,
factory boundary A8/A9, webhook/UI y entrega privada real, renderer PDF si se pide.
Parser está **rechazado** en este incremento; habilitar síntesis IA requeriría
prompt/schema/catalog B, validación de citas/refs, política de privacidad, costo,
evaluación y autorización antes de uso real. No se declara A9 completa por estas
pruebas, un registro de capacidades o un documento.
