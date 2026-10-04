# Inventario de secretos

Autor: Claude (frente B). Fecha: 2026-10-03. Este documento lista **nombres,
ubicaciones y quién lee cada secreto, nunca valores**. Ningún valor se copió al
repositorio, al chat ni a logs.

## Reglas

- En AWS: SSM Parameter Store, tipo `SecureString` estándar (sin cargo, hasta
  4 KB), región `us-east-1`, leído por rol IAM con permiso al ARN exacto y
  `kms:Decrypt` condicionado a `kms:ViaService = ssm.us-east-1.amazonaws.com`.
- En local: nunca en archivos versionados. Si hace falta en disco, solo bajo
  `.local/` (ignorado por Git y por `.dockerignore`). Preferir leerlo en el
  momento a una variable del proceso.
- Pruebas, CI y fixtures: valores sintéticos (`123456:TEST-TOKEN-NOT-REAL`). Ningún
  test necesita un secreto real.
- Código: el token o clave nunca aparece en logs, excepciones, URLs registradas
  ni respuestas (ya probado en el adaptador de Telegram).

## Inventario

| Secreto | Ubicación | Lo lee | Estado |
|---|---|---|---|
| Clave de OpenRouter | **Reutilizada de inventarioIA**: `/inventarioia/openrouter_api_key` (`SecureString`, versión 1, `us-east-1`; metadatos verificados el 2026-10-03 sin descifrar) | Función `app` (puerto LLM, tarea B6) | Configurable con `RADAR_OPENROUTER_KEY_PARAM` |
| Token del bot de Telegram | **Reutilizado de inventarioIA por pedido del usuario** (2026-10-03): `/inventarioia/telegram_token` (`SecureString`, versión 1, `us-east-1`, metadatos verificados) | `bot`, `app` (envíos) | Configurable con `RADAR_TELEGRAM_TOKEN_PARAM`; ver conflicto de webhook abajo |
| Secreto del webhook de Telegram | Propio del radar: `/market-radar/telegram_webhook_secret` (inventarioIA tiene el suyo en `/inventarioia/telegram_webhook_secret`) | `bot` | Se genera al registrar el webhook del radar (`secrets.token_urlsafe(32)`) |
| Lista blanca por teléfono | Local: `.local/allowlist.json` (ignorado por Git). AWS futuro: `/market-radar/allowlist_phones` (`SecureString`) | `bot` (solo para comparar al compartir contacto) | Creada en local con **un único número, rol dueño**; el número no aparece en el repositorio |
| Identidad age del worker WhatsApp | `/market-radar/age/worker-whatsapp` | `whatsapp` | Por generar con `sessions keygen` |
| Identidad age del worker navegador | `/market-radar/age/worker-browser` | `browser` (vía helper Go) | Por generar |
| Credenciales AWS de desarrollo | Perfil local del AWS CLI del usuario | Usuario y despliegues autorizados | Existente; nunca en el repo |

### Clave de OpenRouter reutilizada

**Origen:** inventarioIA (`~/projects/inventarioIA`) guarda la clave con
`scripts/configurar.py`. El script la pide por teclado y la sube directo a SSM
cifrada, sin escribirla en disco. Sus Lambdas la leen con `ssm:GetParameter`
`WithDecryption=True` (`src/common/python/config.py`, prefijo `SSM_PREFIX`
por defecto `/inventarioia`).

**Uso en el radar, sin copiar el secreto:**

- Configuración: `RADAR_OPENROUTER_KEY_PARAM=/inventarioia/openrouter_api_key`.
  Para cambiar a una clave propia basta con cambiar el nombre del parámetro.
- IAM de la función `app` (plantilla SAM, dueño A): `ssm:GetParameter` sobre
  `arn:aws:ssm:us-east-1:<cuenta>:parameter/inventarioia/openrouter_api_key`
  (solo ese ARN) y `kms:Decrypt` con la condición `kms:ViaService`, igual que
  la plantilla de inventarioIA.
- En local, solo cuando haga falta una prueba real autorizada, se carga en la
  variable del proceso sin imprimirla ni guardarla:

  ```bash
  export OPENROUTER_API_KEY="$(aws ssm get-parameter --region us-east-1 --name /inventarioia/openrouter_api_key --with-decryption --query Parameter.Value --output text)"
  ```

  Los tests usan un cliente falso y no la necesitan.

**Consecuencias de compartir la clave (`inf`):**

- Créditos, límites de tasa y consumo son comunes con inventarioIA; el
  consumo del radar se mezcla en la actividad de OpenRouter.
- Rotarla o revocarla en inventarioIA afecta a ambos en el acto. Una sola
  rotación cubre los dos proyectos, pero una revocación los corta a los dos.
- Recomendación: cuando el radar active la IA (B6/F13), crear en la misma
  cuenta de OpenRouter una clave dedicada con límite mensual (R2), guardarla en
  `/market-radar/openrouter_api_key` y cambiar solo `RADAR_OPENROUTER_KEY_PARAM`.
  Así se separan límite, revocación y medición de consumo.

**Modelos que inventarioIA ya usa en producción** (`config.py`, valores por
defecto). Son candidatos naturales para la evaluación B-D053:

| Uso | Modelo |
|---|---|
| Texto y herramientas | `deepseek/deepseek-v4-flash` |
| Audio | `google/gemini-2.5-flash-lite` |
| Visión | `qwen/qwen3.7-flash` |

### Bot de Telegram reutilizado (pedido del usuario, 2026-10-03)

El usuario pidió reutilizar el bot de inventarioIA y autorizar por ahora
**solo un número de teléfono** (guardado en `.local/allowlist.json`, nunca en
Git).

- **Conflicto de webhook (`doc`):** `setWebhook` define *una* URL por bot;
  mientras haya webhook, `getUpdates` no funciona
  ([Bot API](https://core.telegram.org/bots/api#setwebhook)). Si el radar
  registra su webhook con este token, **inventarioIA deja de recibir mensajes**.
  Hoy no hay conflicto porque el radar no está desplegado. Opciones evaluadas
  (resuelto abajo: opción 1):
  1. El radar toma el bot e inventarioIA queda sin Telegram.
  2. Un único webhook en inventarioIA que reenvía al radar los comandos del
     radar (acopla los dos proyectos).
  3. Un bot nuevo para el radar con BotFather (gratis, minutos), con
     `RADAR_TELEGRAM_TOKEN_PARAM` apuntando a `/market-radar/telegram_token`.
     Es la opción recomendada por B; el código no cambia, solo el parámetro.
- **Decisión del usuario (2026-10-03): opción 1.** El radar se queda con el bot;
  inventarioIA se va a borrar y no importa que pierda Telegram. Al registrar el
  webhook del radar (F10), el webhook actual de inventarioIA queda reemplazado.
- **Antes de borrar inventarioIA (`inf`, requiere autorización para escribir en
  AWS):** el radar depende de `/inventarioia/telegram_token` y
  `/inventarioia/openrouter_api_key`. Borrar esos parámetros junto con
  inventarioIA dejaría al radar sin bot y sin IA. Hay dos caminos:
  - Copiar los dos valores a `/market-radar/telegram_token` y
    `/market-radar/openrouter_api_key` de servidor a servidor, sin mostrarlos
    (`get-parameter --with-decryption` canalizado a
    `put-parameter --type SecureString`). Después se cambian
    `RADAR_TELEGRAM_TOKEN_PARAM` y `RADAR_OPENROUTER_KEY_PARAM`.
  - O conservar esos dos parámetros al borrar el resto de inventarioIA.

  Borrar el stack de CloudFormation de inventarioIA no borra estos parámetros:
  su `configurar.py` los creó fuera del stack. Hay que comprobarlo antes de
  borrar.
- **Lista blanca por teléfono:** Telegram no entrega el teléfono a un bot. El
  bot pide "compartir contacto" (`request_contact`) y acepta solo si
  `contact.user_id == from.id`, es decir, si la persona comparte su propio
  contacto y no uno reenviado. Además, el número en E.164 debe estar en la
  lista. Tras la comparación se guarda solo el `user_id` y el rol; el teléfono
  no se persiste. Implementación: tarea B2b.
- **No se reutilizan:** el secreto del webhook ni la lista de chats permitidos
  de inventarioIA (otro producto y otras personas).

## Alta futura de secretos del radar

Reutilizar el patrón de `inventarioIA/scripts/configurar.py`: pedir cada valor
por teclado (`getpass`), subirlo con `aws ssm put-parameter --type SecureString`
en `us-east-1` y no escribirlo nunca en disco ni en el historial de la shell.
Futuro comando del radar: tarea por asignar, no implementada.
