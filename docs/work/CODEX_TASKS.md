# Primeras tareas para Codex (frente A)

Preparado por Claude (B), orquestador del frente B, a pedido del usuario el
2026-10-03. Cada bloque es autocontenido: se puede copiar tal cual en
ChatGPT/Codex. Marca tu fila en [BOARD.md](BOARD.md) al empezar y al terminar.

## Reglas comunes (todas las tareas)

- Lee primero: AGENTS.md, SECURITY.md, [BOARD.md](BOARD.md),
  [arquitectura final](../research/architecture-final-review.md) (tu documento,
  aprobado por el usuario con los refinamientos R1–R4 de
  [implementation-plan.md](../research/agent-b/implementation-plan.md) §1) y
  el plan §2–§4.
- Worktree propio fuera del checkout principal:
  `git worktree add ../mor-a-<tarea> -b codex/a-<tarea> main`. No edites el
  checkout principal salvo tu fila de BOARD.md.
- Solo los archivos reclamados en BOARD.md. Si necesitas un archivo caliente
  ajeno, pide un PR mínimo a su dueño.
- Sin secretos, cuentas reales, AWS ni mensajes. Dependencias fijadas (lockfile)
  e imágenes Docker por tag + digest. Docker: tags `market-radar/a<N>-…:test`,
  `--rm --network none` con límites de memoria, nunca `docker prune` ni tocar
  contenedores ajenos (el usuario tiene 5 activos). Docker pesado: reclámalo en
  BOARD.md.
- Commit en tu rama. Sin merge a `main` ni push: entrega la rama con la salida
  real de las pruebas para revisión de B y autorización del usuario.
- Ramas cortas (1–2 días). Si crece, divide en tareas más chicas.

## A0 — Gobierno y línea de base (puede empezar ya)

Rama `codex/a-governance`. Sin dependencias.

1. `docs/research/decisions.md`: registrar las respuestas del usuario del
   2026-10-03 que constan en el informe B, sección "Plan de implementación y
   trabajo en paralelo":
   - paso 0 autorizado y ejecutado: commit `6ae7cf2` fusionado en local a `main`,
     sin push;
   - arquitectura final A + R1–R4 aprobada;
   - B-Q006 = sí al respaldo local en el PC, sin proxies ni evasión.

   Registrar también B-D061–B-D064 con estado.
2. `AGENTS.md`:
   - método de trabajo paralelo (worktrees, ramas `codex/a-*` y `codex/b-*`,
     BOARD.md, archivos calientes);
   - Telegram como interfaz única del MVP;
   - nota de alcance R3 (implementar productos con nombres del núcleo general;
     ampliar verticales requiere confirmación formal del usuario).

   No relajes ninguna regla de seguridad existente.
3. `docs/ARCHITECTURE.md`: migrar al diseño aprobado (4 Lambdas + CLI local,
   DynamoDB/S3/SSM, outbox, colas, hexagonal) con enlace a tu revisión; marcar
   lo que sigue siendo propuesta.
4. `README.md` y `docs/ROADMAP.md`: enlazar BOARD.md, CODEX_TASKS.md y el
   plan; ROADMAP con las fases F0–F15 resumidas.
5. `.github/workflows/`: job de pytest para `tests/` además de `check_docs`.
   Los jobs de Node/Go se añaden cuando B1/B3 estén fusionadas; deja un
   comentario `TODO(B1/B3)` en lugar de jobs que fallarían.
6. Hecho cuando: `python scripts/check_docs.py` y `python -m pytest -q tests`
   pasan; diff solo en los archivos reclamados.

## A1 — Puertos (empieza cuando B1 esté fusionada)

Rama `codex/a-ports`. Depende de B1 (`pyproject.toml`, `src/radar/`).

1. `src/radar/ports/` con `typing.Protocol`, sin implementación:
   - unidad de trabajo/repositorio con transacción recibo + comando + outbox;
   - `Outbox`, `Ledger` (transiciones condicionales `proposed → approved →
     dispatch_committed → provider_confirmed | send_uncertain`), `LeaseStore`;
   - `BlobStore` inmutable, `TaskQueue`, `UserInterface` (send/edit/answer
     callback/send document), `StructuredLLM`, `Clock`, `Secrets`,
     `Capabilities`, `FxRates`.

   Tipos de entrada y salida alineados con `contracts/*.v1.json` (no
   redefinas los contratos; impórtalos o valida con `radar.contracts`).
2. `tests/test_architecture.py`: `radar.domain` no importa adaptadores, SDKs
   ni red; `radar.application` solo importa `domain` y `ports`.
3. Hecho cuando: pytest verde y `docker build -f docker/python.Dockerfile`
   (si ya existe por A2) o pytest local con salida pegada.

## A2 — Dominio de productos (empieza cuando B1 esté fusionada)

Rama `codex/a-domain`. Depende de B1. Puede ir en paralelo con A1 (no comparten
archivos).

1. `src/radar/domain/`:
   - `core.py`: `Entity`, `Signal`, `Thesis`, `Evidence`, `Scenario`,
     `Opportunity`, `ProposedAction`, `Outcome`, `Money` (Decimal + ISO 4217),
     `FxRate` fechada; inmutables.
   - `states.py`: transiciones válidas de oportunidad y acción.
   - `verticals/products.py`: `normalize`, `match`
     (compatible/conflict/uncertain + razones), `score` (banda determinística),
     `allowed_actions`.

   Reglas de COST_MODEL.md y EVALUATIONS.md: desconocido ≠ 0, precio
   publicado ≠ transacción, autenticidad declarada ≠ verificada.
2. Pruebas nivel 0 en `tests/domain/` con Hypothesis (propiedades: suma de
   costos, desconocido nunca cuenta como cero, banda monótona respecto del
   margen, conversión con tasa fechada).
3. `docker/python.Dockerfile`: imagen de pruebas Python fijada por digest que
   instala `.[test]` y corre `pytest -q`; se usará en CI y por B2.
4. Hecho cuando: pytest local y `docker build -f docker/python.Dockerfile -t
   market-radar/a2-python:test .` pasan; salida pegada.

## A4 — Worker de navegador por contrato (después de B1)

Rama `codex/a-browser-worker`. Depende de B1.

1. `workers/browser/` a partir de `lab/browser/` (el lab queda intacto como
   arnés). Entrada `browser.read.v1` dentro del sobre v1 y salida
   `browser.result.v1`; validación con ajv contra `contracts/`.
2. Sin lógica de negocio: campos crudos + evidencia + errores tipados.
3. Carga de estado: en esta tarea, solo fixtures (el helper age de B3 llega
   después; deja el punto de integración documentado).
4. Hecho cuando: selftest Node y el build/ensayo Docker del worker pasan con
   fixtures; Docker pesado reclamado en BOARD.md durante el ensayo.

## Siguientes (no empezar aún)

A3 flujo local con outbox (después de A1 + A2) y A5 adaptadores AWS
(después de A3). Están descritas en el plan §4 F3/F7.

---

# Ola 2 — Asistente general por Telegram (planificado por B, 2026-10-03)

Pedido del usuario: el sistema debe poder recibir por Telegram pedidos como estos:

- "Búscame los talleres que reparan silenciadores más baratos y que acepten Cashea;
  escríbeles, pídeles precio para mi carro (marca/modelo/año) y dime el más
  económico."
- "Investiga tal tema en toda la web y mándame un PDF de resumen."
- "Revisa mis mensajes de WhatsApp y respóndele tal cosa a tales personas."
- "Todos los lunes a las 8 revisa X y avísame" (tareas programadas pedidas por chat).

Piezas base ya existentes, probadas con simulaciones:
- bot y webhook con consentimiento y lista blanca (B2/B5/B2b);
- adaptador OpenRouter activo con DeepSeek V4 Flash y topes (B6 +
  `catalog.build_default`);
- worker WhatsApp con resolver contacto, envío con ledger/lease y sync paginada
  (B3/B4/B7/B7b);
- flujo local con outbox/ledger (A3, con cambios pedidos);
- worker de navegador (A4).

Esta ola las une.

## Reglas de la ola 2 (además de las reglas comunes)

- **Un solo patrón para todo:** texto libre → IA interpreta un `task_request` tipado →
  el bot muestra "¿Entendí bien?" con botones → flujo durable por pasos → resultado.
  La IA nunca ejecuta efectos externos por sí misma.
- **Todo mensaje saliente a terceros** (talleres, contactos de WhatsApp) requiere
  aprobación explícita del propietario en Telegram.
  - Puede aprobarse en lote solo si la pantalla muestra **cada destinatario y cada
    texto**.
  - Salvaguardas B-I09: no responderse a sí mismo, presupuesto por pasada,
    anti-ráfaga, nunca dos contactos iguales para el mismo propósito, y
    `send_uncertain` sin reenvío.
  - Los términos de WhatsApp prohíben el envío automático o masivo (B-F018).
- **Tareas programadas** pueden leer, buscar e informar solas; si generan un mensaje
  a terceros, este queda pendiente de aprobación cuando se dispara.
- **Datos privados** (mensajes, teléfonos, nombres) solo por `private_ref` cifrado y,
  si van a la IA, con `privacy_scope="personal"` (ZDR + `data_collection: deny`).
  Verificar que el modelo tenga endpoints ZDR con salida estructurada; si no hay,
  el paso falla cerrado y se informa.
- **Lo declarado no es verificado:** "acepta Cashea", precio citado y disponibilidad
  son declaraciones del proveedor hasta confirmarse.
- **Contratos:** los esquemas nuevos (`llm.*.v1`, `task.*.v1`) los puede escribir A
  en su rama, pero `src/radar/schemas/` y `contracts/` siguen con dueño B: **revisión
  de B obligatoria** antes del merge. Nunca editar un vN publicado.
- **Costos:** cada tarea tiene presupuesto (USD y número de llamadas/mensajes) que se
  reserva antes de ejecutar. Ninguna búsqueda ni API pagada nueva sin aprobar antes la
  opción y su costo en `decisions.md`.

## A3b — Correcciones pedidas en la revisión de A3 (bloquea todo lo demás)

Ver "Revisión B de A3" en [BOARD.md](BOARD.md): ítems 1 (bypass de autorización al
reconsentir) y 2 (cuarentena + ack de errores permanentes) obligatorios; 3–6 y 8
recomendados en la misma rama o en A3c.

## A12 — Modo de prueba local por Telegram (primero, para que el usuario pruebe)

Rama `codex/a-local-telegram`. Depende de A3b.

1. Runner local que lee updates con `getUpdates` (long polling) y los pasa por el
   mismo `Webhook.handle_update` y por el runtime local de A3. No se salta secreto,
   dedupe ni consentimiento: el runner construye headers equivalentes. Sin AWS.
2. El webhook actual del bot apunta a la URL muerta de inventarioIA. El runner llama
   a `deleteWebhook` **solo con el flag explícito** `--take-over-bot` (decisión del
   usuario en B-D065/`secrets.md`: el radar se queda con el bot). Documentar que al
   desplegar se vuelve a `setWebhook`.
3. Fuentes de configuración:
   - Token: SSM `/market-radar/telegram_token` o variable de entorno.
   - Lista blanca: `.local/allowlist.json` (`PhoneAllowlist.from_file`).
   - IA: `catalog.build_default` (Secrets → SSM `/market-radar/openrouter_api_key`).

   Nunca imprimir token ni teléfono.
4. Hecho cuando: con el bot real, el propietario escribe `/start`, comparte contacto,
   acepta consentimiento y recibe respuesta; pruebas automatizadas con transporte
   falso.

## A6 — Router de intenciones (`task_request`)

Rama `codex/a-task-router`. Depende de A3b.

1. Esquema `llm.task_request.v1` (revisión B):
   - `kind` ∈ [quote_request, research_report, whatsapp_reply, schedule_task,
     saved_search];
   - campos por tipo: necesidad, ítem o vehículo, zona, restricciones de pago,
     criterio "más barato", tema y profundidad, destinatarios y textos, expresión de
     horario + zona horaria America/Caracas;
   - `missing_fields` y `confidence`;
   - datos personales: por referencia, no texto.
2. `/pedir` y texto libre → prompt versionado → validación → mensaje "¿Entendí
   bien?" con botones Confirmar / Corregir / Cancelar (callbacks opacos de un uso,
   B2). Si faltan campos, el bot pregunta solo lo que falta.
3. Registro de tipos de tarea. Para cada uno: presupuesto por defecto, acciones
   permitidas, si requiere WhatsApp vinculado y qué aprobaciones exige.
4. Pruebas:
   - texto con inyección no cambia el tipo ni las acciones;
   - ambiguo → pide aclaración;
   - tipo desconocido → rechazo;
   - presupuesto excedido → no arranca.

## A7 — Cotizaciones a proveedores (`quote_request`)

Rama `codex/a-quotes`. Depende de A6, A8 y del worker WhatsApp de B.

1. Vertical "proveedores/servicios" en `domain/verticals/`, con el núcleo general:
   - Entity = negocio;
   - Signal = ficha, publicación o directorio;
   - atributos: servicio, zona, medios de pago **declarados**, contacto publicado
     (privado);
   - Opportunity = mejor cotización con evidencia;
   - Outcome = lo que el usuario contrató.
2. Flujo:
   1. pedido;
   2. descubrimiento (A8);
   3. lista corta (máx. N, configurable) y aprobación de la lista;
   4. `whatsapp.resolve_contact` por proveedor (aprobación ligada al blob cifrado,
      B7);
   5. borrador de mensaje por plantilla con hechos del pedido (vehículo, servicio,
      zona, pregunta por precio, disponibilidad y si aceptan Cashea) y aprobación;
   6. `whatsapp.send`;
   7. sync paginada de respuestas por `chat_ref`;
   8. extracción con `llm.quote_extraction.v1` (revisión B; `privacy_scope=personal`);
   9. comparación determinística: precio, moneda con tasa fechada, condiciones, pago,
      incertidumbre;
   10. informe en Telegram con la mejor opción y advertencias.
3. Seguimiento tras silencio opcional (una vez, aprobado). Si no hay respuesta en el
   plazo, el resultado es "sin cotización", nunca precio cero.
4. Pruebas de punta a punta con fakes:
   - talleres sintéticos;
   - respuestas con precios en USD y Bs;
   - sin respuesta y respuesta ambigua;
   - uno no acepta Cashea;
   - mensaje incierto.

## A8 — Fuente de negocios: directorio público de Cashea

Rama `codex/a-source-cashea`. Puede ir en paralelo con A6.

1. Investigar y registrar en `contracts/capabilities.json` (revisión B):
   - el sitemap público `https://www.cashea.app/sitemap.xml` lista ~4.700 URLs,
     incluidas fichas `/comercios/<slug>`;
   - `robots.txt` solo declara el sitemap (consultado 2026-10-03);
   - verificar términos de uso, qué datos trae cada ficha (categoría, ciudad,
     contacto) y si requiere render JS.
2. Conector:
   - HTTP primero (caché, ETag, límites por dominio, preflight);
   - navegador (A4) solo si la ficha lo exige;
   - clasificación de rubro (talleres, escapes, repuestos) determinística primero, y
     con IA solo si hace falta (esquema propio, texto público).
3. Fixtures sintéticos en Git; nunca volcar el directorio real al repositorio.
4. Siguientes fuentes candidatas, cada una con su tarea:
   - Google Maps (de pago; requiere decisión de costo);
   - Instagram/Facebook vía OpenCLI (lectura);
   - web abierta.

## A9 — Investigación web con informe PDF (`research_report`)

Rama `codex/a-research-pdf`. Depende de A6.

1. Decisión de proveedor de búsqueda, registrada en `decisions.md` con precio y
   límites. Comparar: plugin web de OpenRouter, Exa, lector Jina y fuentes abiertas.
   Presupuesto por informe (consultas, páginas, USD).
2. Flujo:
   1. plan de consultas;
   2. lectura acotada (anti-SSRF, tamaño, dominios);
   3. notas con cita por afirmación;
   4. síntesis con IA (texto público);
   5. informe HTML con fuentes y fechas.
3. PDF: reutilizar el worker de navegador (Chromium/Playwright `page.pdf()`) en lugar
   de añadir una librería; enviar con `sendDocument` (≤50 MB). Sin URLs públicas.
4. Pruebas con fixtures:
   - citas presentes;
   - fuente caída marcada como cobertura incompleta;
   - presupuesto agotado → informe parcial etiquetado.

## A10 — Asistente de bandeja WhatsApp (`whatsapp_reply`)

Rama `codex/a-whatsapp-assistant`. Depende de A6 y del worker WhatsApp de B.

1. Resumen de chats **habilitados** (sync v2 + `list_chats`): pendientes de
   respuesta, quién escribió y de qué trata. Los chats no habilitados se descartan en
   memoria (B-D023, arts. 20–22).
2. "Respóndele X a tales personas":
   1. borradores por destinatario;
   2. pantalla de aprobación con cada destinatario y texto;
   3. `whatsapp.send` por cada uno, con ledger.

   Nunca a chats no habilitados ni a desconocidos.
3. IA con `privacy_scope=personal` (ZDR). Si no hay endpoint ZDR, borradores solo con
   plantillas.
4. Pruebas:
   - inyección en mensajes entrantes no genera envíos;
   - destinatario ambiguo pide aclaración;
   - un lote aprobado envía exactamente lo mostrado.

## A11 — Tareas programadas por chat (`schedule_task`)

Rama `codex/a-scheduled-tasks`. Depende de A6.

1. "Todos los lunes a las 8…" → `task_request` con horario + zona America/Caracas →
   confirmación → programación.
   - Local: planificador falso en SQLite.
   - AWS: EventBridge Scheduler (free tier de 14 M invocaciones/mes), una regla por
     tarea, con límite de tareas por usuario.
2. `/tareas` para listar, pausar y borrar. Cada disparo encola la tarea con id
   idempotente: un disparo duplicado no la ejecuta dos veces.
3. Salidas: búsquedas e informes se entregan solos; mensajes a terceros quedan
   pendientes de aprobación.
4. Pruebas:
   - disparo duplicado;
   - zona horaria explícita (Venezuela no tiene horario de verano, pero la zona debe
     declararse igual);
   - tarea pausada no corre;
   - presupuesto mensual agotado.

## Orden sugerido

A3b → A12 (el usuario prueba por Telegram) → A6 → A8 ‖ A9 → A7 → A10 → A11 →
A5/F8 (AWS) → canary autorizado. B revisa cada entrega y los esquemas nuevos.
