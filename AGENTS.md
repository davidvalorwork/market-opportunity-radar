# Instrucciones para trabajar en Market Opportunity Radar

- Leer README.md, CONTEXT.md, docs/ROADMAP.md y SECURITY.md antes de implementar.
- Este es el radar de productos/reventa, no Job Radar, correo masivo ni Evidence Lab de facturas.
- Mantener explícito el estado: documentación no significa funcionalidad; comando propuesto no significa comando existente.
- Priorizar la vertical local con fixtures sintéticos y cálculos determinísticos, luego fuentes autorizadas verificadas.
- Arquitectura hexagonal modular; dominio independiente de Agent Reach, navegador, persistencia y LLM.
- Agent Reach es la ruta preferida para investigación y canales soportados. Leer su skill antes de usarla; no atribuir capacidades no probadas.
- Preflight de acceso/credenciales antes de correr fuentes. No leer cookies de archivos del navegador ni hacer login automáticamente.
- No compras, pagos, reservas, mensajes, publicaciones, follows o ingreso a grupos sin encargo/autorización separados.
- No APIs pagadas, descargas de modelos o infraestructura cloud implícitas.
- Comparar variante, unidad, condición y procedencia. Precio publicado no equivale a transacción; autenticidad declarada no equivale a verificada.
- Costos ausentes son desconocidos, no cero. Aritmética decimal y tipos de cambio fechados.
- Preservar salidas, fallos y desacuerdos; no bajar gates o cambiar etiquetas para fingir calidad.
- No declarar revisión humana, ROI o ganancias realizadas a partir de fixtures o IA.
- Secretos y datos privados permanecen en .local/, fuera de Git y telemetría.
- Usar apply_patch para edits, preservar cambios del usuario y evitar operaciones destructivas.
- Antes de commit/push, ejecutar `python scripts/check_docs.py`, `python -m pytest -q tests` y las pruebas del área que exista.
- Ramas de trabajo con prefijo codex/; main contiene entregas verificadas. No merge/push externo sin autorización aplicable.
- No contribuir a repos externos: el usuario excluyó ese frente.
- Subagentes solo con autorización o instrucciones aplicables; acordar contratos y propiedad de archivos para evitar solapamientos.
- Telegram es la interfaz de producto única del MVP. La CLI local queda para administración técnica, sesiones y pruebas, no como segunda UI comercial. No construir dashboard/Mini App en estas tareas.
- Implementar solo productos, con nombres generales del núcleo (`Entity`, `Signal`, `Opportunity`, etc.) desde el primer commit. Otras verticales requieren confirmación formal del usuario; no diseñar un framework universal por anticipado.

## Trabajo paralelo A/B

- Leer [tablero](docs/work/BOARD.md), [tareas A](docs/work/CODEX_TASKS.md), [plan B](docs/research/agent-b/implementation-plan.md) y [decisiones](docs/research/decisions.md) al empezar cada tarea.
- Un agente = un worktree fuera del checkout principal = una rama = una tarea. Usar `codex/a-*` para Codex y `codex/b-*` para Claude; reclamar archivos y dependencias en BOARD antes de editar.
- Respetar la tabla de archivos calientes: un solo dueño. Pedir cambios mínimos al dueño de un contrato/lockfile ajeno; no editar informes del otro frente.
- El coordinador actualiza el tablero principal; los snapshots de worktrees no son autoridad sobre reclamos nuevos. Integración serial con pruebas y revisión, únicamente bajo autorización aplicable; iniciar tareas no autoriza nuevos merges ni push.
- Reclamar Docker pesado y mantener benchmarks secuenciales. No tocar contenedores ajenos. AWS, cuentas reales y contactos siguen requiriendo autorización separada.
- Registrar resultados observados, aprobaciones reportadas por otro agente y propuestas por separado. R1 (Streams directo) es un experimento hasta pasar pruebas; límites de concurrencia/presupuesto no garantizan costo USD 0.

<!-- CODEGRAPH_START -->
## CodeGraph

El MCP CodeGraph indexa símbolos, aristas y archivos mediante tree-sitter. Preferirlo para preguntas estructurales (definiciones, llamadas, impacto y firmas); usar búsqueda nativa para texto literal (strings, comentarios o logs) y lecturas de archivos ya identificados.

| Pregunta | Herramienta |
|---|---|
| ¿Dónde está definido X? | `codegraph_search` |
| ¿Qué llama a Y? | `codegraph_callers` |
| ¿Qué llama Y? | `codegraph_callees` |
| ¿Qué rompería cambiar Z? | `codegraph_impact` |
| Firma, fuente o docstring de Y | `codegraph_node` |
| Contexto enfocado para una tarea | `codegraph_context` |
| Explorar un módulo desconocido | `codegraph_explore` |
| Archivos bajo una ruta | `codegraph_files` |
| Salud del índice | `codegraph_status` |

- Confiar en los resultados AST; no volver a verificarlos con grep. No buscar símbolos por nombre con grep primero.
- Para contexto usar `codegraph_context`, no encadenar search + node. `codegraph_explore` es intensivo en tokens; delegarlo solo cuando haya autorización de subagentes aplicable.
- El watcher puede llevar unos 500 ms de retraso; no reconsultar inmediatamente después de editar.
- Si `.codegraph/` no existe o el servidor indica "not initialized", preguntar antes de ejecutar `codegraph init -i`. El índice queda local y fuera de Git.
<!-- CODEGRAPH_END -->
