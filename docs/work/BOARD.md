# Tablero de trabajo A/B

Método: [plan de implementación](../research/agent-b/implementation-plan.md) §2–§3.
Un agente = un worktree = una rama = una tarea. Reclamar aquí **antes** de
empezar y liberar al fusionar. A = Codex (ChatGPT), B = Claude. Ramas
`codex/a-<tarea>` y `codex/b-<tarea>`. Merge serial a `main` solo con pruebas
verdes y autorización del usuario; push solo con autorización explícita.

Última actualización: 2026-10-03, por B.

## Tareas

| ID | Fase | Dueño | Rama | Archivos reclamados | Depende de | Estado |
|---|---|---|---|---|---|---|
| B1 | F1 contratos | B (subagente) | `codex/b-contracts-v1` | `contracts/**`, `src/radar/__init__.py`, `src/radar/contracts.py`, `pyproject.toml` (creación inicial), `tests/test_contracts.py` | — | en curso |
| B3 | F5 Go | B (subagente) | `codex/b-go-module` | `go/**` | — | en curso |
| B2 | F4 Telegram | B | `codex/b-telegram` | `src/radar/adapters/telegram/**`, `src/radar/entrypoints/lambda_bot.py`, `tests/telegram/**` | B1, A1 (puerto UI) | pendiente |
| A0 | F0 gobierno | A | `codex/a-governance` | `docs/research/decisions.md`, `AGENTS.md`, `docs/ARCHITECTURE.md`, `README.md`, `docs/ROADMAP.md`, `.github/workflows/**` | — | **lista para A** |
| A1 | F1 puertos | A | `codex/a-ports` | `src/radar/ports/**`, `tests/test_architecture.py` | B1 fusionada | **lista para A tras B1** |
| A2 | F2 dominio | A | `codex/a-domain` | `src/radar/domain/**`, `tests/domain/**`, `docker/python.Dockerfile` | B1 fusionada | **lista para A tras B1** |
| A3 | F3 flujo local | A | `codex/a-local-flow` | `src/radar/application/**`, `src/radar/adapters/local/**`, `tests/flow/**` | A1, A2 | pendiente |
| A4 | F6 navegador | A | `codex/a-browser-worker` | `workers/browser/**` | B1 fusionada | pendiente |
| A5 | F7 AWS | A | `codex/a-aws-adapters` | `src/radar/adapters/aws/**`, `tests/conformance/**` | A3 | pendiente |

Detalle de las tareas de A: [CODEX_TASKS.md](CODEX_TASKS.md).

## Archivos calientes (un solo dueño)

| Archivo | Dueño | Regla |
|---|---|---|
| `contracts/**` | B | Cambios solo con PR de contrato y versión nueva; A revisa |
| `src/radar/ports/**` | A | B pide cambios por PR mínimo |
| `pyproject.toml` | A (tras su creación en B1) | B pide dependencias nuevas por PR mínimo |
| `go/go.mod`, `go/go.sum` | B | — |
| `workers/browser/package-lock.json` | A | — |
| `infra/sam/**` | A | B revisa IAM/seguridad |
| `.github/workflows/**` | A | — |
| README, AGENTS, ROADMAP, ARCHITECTURE, `decisions.md` | A | B propone en su informe |
| `docs/work/BOARD.md` | Ambos | Solo filas propias; edición breve, sin reordenar |

## Recursos compartidos (reclamar en la columna "en uso")

| Recurso | En uso por | Nota |
|---|---|---|
| Docker pesado (Chromium, benchmarks del lab) | — | Uno a la vez; builds ligeros (Go/Python) pueden convivir |
| AWS | — | No autorizado todavía (F10) |
| Cuenta de ensayo WhatsApp | — | No autorizada todavía (F11) |
