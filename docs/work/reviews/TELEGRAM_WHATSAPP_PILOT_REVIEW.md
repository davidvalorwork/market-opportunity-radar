# A30 — Revisión independiente del piloto Telegram/WhatsApp

Base observada: `78aa0ad`, rama `codex/a-pilot-review`. Revisión local;
no aprobación de despliegue, cuenta, envío ni integración.

## Gates antes del piloto

1. **Replay del worker antiguo no está aislado.**
   `go/internal/whatsapp/worker.go:444–447` llama `settled` antes de comprobar
   propietario/destinatario/aprobación/hash; `478–479` repite esa omisión tras CAS.
   Un estado confirmado devuelve el ID de otro propietario (`509–510`);
   `dispatch_committed` puede cambiar a incierto (`511–512`). Contradice
   SECURITY: «autorización server-side por propietario/proyecto».
   El piloto acordado usa SAME `wameow.Client`, **no Worker**; debe mantener
   replay owner/account/operation y revalidar consentimiento, cuenta/versión,
   chat habilitado, Approval/texto y lease inmediatamente antes del efecto.

2. **Falta identidad estable del mensaje recibido.**
   `go/internal/contract/contract.go:225–229` conserva chat/texto/fecha, no ID;
   `wameow/wameow.go:182,233` inserta cada evento sin dedupe del proveedor.
   GENERAL_TASKS exige contexto «al mensaje, conversación y cuenta concretos».
   Solución mínima acordada: journal privado de `e.Info.ID` y accessor por cursor,
   dentro de la sesión/cuenta/chat; no modificar contratos publicados.

3. **Falta cableado de producto.** `/vincular` existe en webhook (`:91`), pero
   `general_runtime.py:221` lo bloquea como `command_unsupported`.
   Configuración/factory (`general_bindings.py:255–283`) debe instalar pairing,
   callbacks owner-bound, selección explícita de chats y bridge real. A10 exige
   «pantalla de aprobación con cada destinatario y texto» y chats habilitados.

4. **Filtro de lectura demasiado tarde.** `wameow.go:176–183,233` persiste texto
   de todos los mensajes entrantes; sólo `pendingPage:316` elimina los chats no
   habilitados después. A10:302–304 exige descartarlos «en memoria».
   Filtrar antes de INSERT/Connect mediante allowlist del host; filtrar sólo la
   salida no evita persistencia en SQLite/WAL. No declarar este gate satisfecho.

## Evidencia y límites

Probe sintético independiente: `/vincular` HTTP 200 → entrada durable bloqueada;
el esquema privado acepta chat/texto/fecha y rechaza añadir ID del proveedor.
No se tocaron fuentes, cuentas, secretos ni red.

**No hay desajuste SHA del texto:** A10 (`conversations.py:490,541`) y Go send
(`worker.go:458`) usan SHA256(texto). Sí existe framing A14 (`private_vault.py:504–507`):
host debe abrir/validar propietario antes del pipe; no afirmar compatibilidad wire.

Root tiene autorización para leer secretos existentes por SSM para este piloto,
no provisionar/desplegar cloud ni generar costo. Pairing requiere código introducido
por la persona en su teléfono. IA pagada apagada. Gates pendientes de revisión
independiente del helper y wiring nuevos; AWS no es requisito del piloto local.
Sin parser activado, `general_bindings.py:166–173,277` sólo interpreta `/pedir`
JSON estructurado; el piloto determinista no debe prometer lenguaje natural.

Verificación independiente del baseline: `check_docs.py` aprobado;
`go test ./internal/whatsapp ./internal/whatsapp/wameow` aprobado, offline.
`pytest -q tests`: 1262 passed, 251 subtests, 1 skipped y 6 failed (356,78 s).
Los seis fallos son ausencia de `tzdata` en el venv usado (UTC/IANA y metadata),
no cambios del piloto; no se instalaron dependencias ni se declaró suite verde.
