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
