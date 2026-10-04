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
| Token del bot de Telegram | Propio del radar: `/market-radar/telegram_token` | `bot`, `app` (envíos) | Por crear con un bot nuevo |
| Secreto del webhook de Telegram | `/market-radar/telegram_webhook_secret` | `bot` | Por generar (`secrets.token_urlsafe(32)`, caracteres válidos para `secret_token`) |
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

### Lo que NO se reutiliza

- **Token del bot de Telegram de inventarioIA:** un bot tiene un solo webhook
  activo; compartirlo rompería uno de los dos productos. El radar necesita su
  propio bot.
- **Secreto del webhook y lista de chats permitidos de inventarioIA:** son de
  otro producto y de otras personas; el radar mantiene su propia lista blanca
  (B-D039).

## Alta futura de secretos del radar

Reutilizar el patrón de `inventarioIA/scripts/configurar.py`: pedir cada valor
por teclado (`getpass`), subirlo con `aws ssm put-parameter --type SecureString`
en `us-east-1` y no escribirlo nunca en disco ni en el historial de la shell.
Futuro comando del radar: tarea por asignar, no implementada.
