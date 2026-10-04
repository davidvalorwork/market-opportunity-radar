# A16 — cableado general local durable

## Alcance ejecutable

Un runtime general conecta las APIs existentes, no exige productos, empleo,
precios, ciudad, proveedor ni taller. Telegram es la interfaz del producto; el
CLI es una entrada técnica. Los ejemplos incluyen artículos, eventos, empleos y
perfumes, sin especializar el núcleo en ninguno de esos temas.

Cadena conectada: B Webhook → ingreso privado → A6 propuesta/confirmación →
pasos SQLite con reservas/versiones/dependencias → A8 lectura → A9 informe.
También conecta A7 descubrimiento y resolución independiente → A10 borrador y
pantalla exacta, y A11 creación/listado/pausa/eliminación de programación.
Polling A12 entrega los mismos updates al webhook, sin registrar texto en el
control plane. B sigue validando secreto, chat privado, contacto de admisión,
allowlist, consentimiento y callbacks. No se ejecutó Bot API real.

## Una ejecución offline

```powershell
$env:PYTHONPATH = 'src'
python -m radar.entrypoints.general_local --fixture
python -m radar.entrypoints.general_local --help
```

`--fixture` utiliza cuentas y fuentes sintéticas explícitas. No lee sesiones,
cookies, secretos del operador ni llama redes/modelos. Su vault de RAM **no es
cifrado ni persistencia de contenido**. El resumen indica `fixture_only`,
`network_calls`, `model_calls`, lecturas y envíos a terceros. Esta corrida crea
un informe; no contacta a terceros. El dispatcher de pruebas sólo puede
ejecutarse mediante `dispatch_fixture` con aprobación nueva A10.

Las pruebas adicionales sustituyen las vistas de RAM por **A14 age real**,
claves efímeras sintéticas y ACL WinAPI/permisos POSIX. Reinician el control
plane y descifran el checkpoint y el informe sin volver a leer la fuente.
Los binarios Go se compilan con módulos ya disponibles y `GOPROXY=off`;
no hay descargas ni cuentas reales.

## Factory real, sin sustitución por fakes

Entrada existente `from_config(state_db, phone_allowlist, secret, config_path,
api=None)` compone A3 SQLite, A14 PrivateVault, B Webhook, A12 PollingWiring,
A6 TaskRouter, A8 Reader y A10 Conversations. Configuración ausente o inválida
no se interpreta como autorización. La inicialización exige permisos reales
de dueño, archivos regulares, ausencia de enlaces/reparse points y directorio
privado **antes de leer configuración/allowlist o crear BotApi**. `.local`
es sólo una convención organizativa, no una comprobación de seguridad.
SQLite nuevo se crea de forma exclusiva bajo ese directorio privado.

Ejemplo de entrada técnica, **no ejecutado en esta tarea**:

```powershell
python -m radar.entrypoints.general_local --config C:/private/config.json `
  --state-db C:/private/control.sqlite --allowlist C:/private/allowlist.json `
  --authorize-bot-io --max-polls 100
```

El operador suministra `RADAR_TELEGRAM_TOKEN` y `RADAR_TELEGRAM_SECRET` por
entorno. No se imprimen. `--take-over-bot` autoriza expresamente la retirada
del webhook conservando updates pendientes; no se hace por defecto.

Configuración JSON privada:

| Clave | Contenido/configuración confiada al host |
| --- | --- |
| `owner_ref` | Identidad opaca del dueño, no teléfono |
| `vault` | Configuración A14 `root`, `helper`, `helper_sha256`, `identity_file`, `recipient` |
| `grants` | Mapa operación → capacidad opaca autorizada |
| `sources` | Lista de SourceSpec: plataforma/backend/ruta/operaciones/límites/hosts |
| `authorized_sources` | Referencias de fuentes autorizadas expresamente |
| `channels` | Registro A10; configurarlo no verifica cuenta ni destinatario |
| `timezone`, `allowed_timezones` | Zonas de programación aprobadas por el operador |
| `max_calls`, `max_messages`, `max_usd` | Techo host A6, nunca ampliado por el modelo |
| `backend_factory` | Módulo:función instalada y elegida por el operador, nunca por un pedido |
| `parser_enabled` | Activa únicamente un StructuredLLM y model_ref suministrados por el host |

La factory de backend recibe `(store, vault, host_authority, clock)` y devuelve
un diccionario: `transports`, `capabilities`, `session_authorizer`,
`query_policy`, `interpreter` determinista opcional, `parser`/`model_ref`
opcionales, `template_resolver`, `scheduler`, `contacts`, `inbox_sources` y
`handlers`. Puede incluir `configure(runtime, directory, vault)` para registrar
cuentas verificadas usando los puertos existentes. Es composición de código
confiable, no shell libre ni módulos/argv elegidos por el LLM.

Por defecto no hay transportes, resolver de contacto, sincronizador social,
sesión verificada ni dispatcher real: una tarea llega a `blocked` con causa
observable, no a éxito ficticio. `probado_real` escrito en configuración se
degrada a `documentado`; una capacidad fresca exige proveedor independiente.
`session_authorizer(request, authority, now)` debe devolver un Grant validado
con dueño/cuenta/sesión/vigencia exactos. Una versión en SQLite no es prueba de
login: los requests con cuenta/sesión sin ese proveedor se deniegan.
Las rutas/transportes marcados `fixture_only` no se admiten en configuración
real. El host sigue responsable de la veracidad de sus implementaciones.

## Durabilidad, privacidad y presupuestos

- Ingresos: recibos hasheados con binding dueño/actor/contenido y transacción
  atómica; contenido completo cifrado antes de persistir. Ningún texto B se
  inserta en `commands`/`outbox`. CAS y lease de ingreso evitan interpretación
  concurrente. Un parsing interrumpido sin propuesta queda incierto: no repite
  automáticamente una llamada de IA. A6 reserva su presupuesto antes del parser.
- Runs: identidad incluye versión de propuesta y ocurrencia. Corregir/cancelar
  invalida el run anterior; otra confirmación crea otro run, no lo revive.
  El callback Corregir está conectado; editar el documento vinculado usa la API
  A6 `router.correct` por el host. No se implementó todavía un formulario de
  corrección de contenido ni la vinculación automática de texto libre siguiente
  a esa propuesta en Telegram: un texto libre crea una propuesta nueva.
  Cada claim tiene ID propio y lease; un worker vencido no escribe checkpoints
  ni marca bloqueado el resultado del sucesor. Se revalidan autoridad, permiso,
  consentimiento, propuesta, sesión vinculada y deadline en cada etapa.
- Lecturas: una fuente y una página por intento; múltiples fuentes requieren
  pasos explícitos. Cursor e informes acumulados quedan privados y durables;
  continuación con cooldown y máximo tres intentos por defecto. Una caída antes
  del checkpoint puede releer la página: dedupe A8 no promete exactitud global
  entre corridas ni resumptions. No existe retry infinito.
- El contador del runtime reserva **intentos de operación**, no llamadas
  ocultas arbitrarias de handlers. La lectura A8 estándar realiza como máximo
  una petición por intento. Las lecturas de artefactos privados para A9 no son
  nuevas consultas externas y `reserve_read` sólo revalida acceso. Costos de
  fuente desconocidos conservan `partial`; no se afirma un techo USD medido.
  Un plugin con red/modelos debe reservar costos/tokens reales por su puerto.
- Notificaciones: cuerpo privado, control con refs/digests/estado. Claim CAS antes
  de envío; caída tras claim o confirmación ausente no se reenvía sola. El estado
  `dispatch_committed`/`send_uncertain` requiere reconciliación independiente.
- Contactos: teléfono/email publicado no equivale a canal comprobado. A7 prepara
  candidatos privados; un resolver independiente aprueba endpoint y binding.
  A10 muestra **cada** destinatario/texto/purpose/cuenta/sesión. A6 confirmar un
  plan no aprueba mensajes. La pantalla entera debe entregarse; luego callback
  A10 exacto. Replay tras caída aprobación→link no duplica el mensaje. A7 vuelve
  a exportar/revalidar antes incluso del efecto fixture.
- Programación: captura un template A6 confirmado; ocurrencias posteriores
  revalidan A11/host aunque expire la propuesta inicial. Catch-up/quota acotados.
  Los mensajes de una ocurrencia vuelven a requerir pantalla A10 nueva.

## Contexto y gates todavía pendientes

La secuencia `read inbox → search/read → compose` está conectada y probada con
un inbox A10 sintético habilitado. Datos entrantes no generan nuevas órdenes.
La lectura del cache está limitada a 20 mensajes y 131072 bytes de documentos
privados capturados, ordenados por referencia proveedor, **no por fecha**.
No hay filtro «hoy» ni continuación del cursor de inbox en este corte. La fecha
de Provenance es la observación/lectura local, no el momento original del mensaje;
su documento privado original queda referenciado, sin inventar timestamps.
La query pública exige `query_policy` independiente y minimización del host:
sin clasificación se bloquea **antes de IO**. No se copia texto privado del chat
a una consulta externa por defecto. Los informes conservan origen privado y
no lo convierten en evidencia web pública verificada.

En este corte `compose` usa un borrador privado exacto previamente preparado por
el host: **no es síntesis contextual automática fundamentada**. A18 es la pieza
de enriquecimiento/reasoner pendiente de integrar. Un handler host puede
conectarla: `(runtime, authority, run_ref, task_ref, step, outputs)` →
`(private_result_dict, state, static_reason)`. No gana permiso por estar registrado.
Extracción de campos solicitados arbitrarios tampoco está implementada: el
paso `extract` informa `partial`, no inventa resultados.

Gates: configuración real y UI/allowlist/consentimiento aprobados; transportes
verificados (HTTP A17, social/CLI/browser aún por cablear); vault de operador;
resolver y observaciones independientes; modelo/prompt aprobado y presupuesto;
sincronización de chats con ID proveedor estable (wire B actual carece de él);
Go replay/ledger/efecto real y revisión B. `/mis_datos` y `/borrar` dentro de este
runtime siguen `control_handler_pending`: no se declara exportación/borrado
general operativo. No se ejecutaron modelos, Telegram real, WhatsApp real,
cookies, navegador, Docker ni AWS. Fixtures no levantan esos gates.

## Verificación

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m pytest tests/general_flow -q -p no:cacheprovider
python -m pytest -q -p no:cacheprovider
python scripts/check_docs.py
git diff --check
```

La batería cubre cuatro temas generales; secreto/consentimiento/owner; ausencia
de PII en SQLite/WAL/ledger/outbox/age; doble ingestor; lease vencido y sucesor;
reinicio real age; polling; corrección; budgets/fanout; mensajes entrantes como
datos; consulta privada bloqueada; A7→A10 aprobación exacta y link crash;
notificación incierta; programación vencida/pausa y CLI sin fake en modo real.
