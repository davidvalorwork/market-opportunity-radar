# Runbook: borrar inventarioIA en AWS

Preparado por Claude (frente B) el 2026-10-03, a pedido del usuario. Inventario
hecho **en modo solo lectura**. Los comandos de borrado los ejecuta el usuario:
borrar datos de forma permanente queda fuera de lo que el agente puede hacer.

El radar **no depende** de nada de esto. Sus secretos ya son copias propias en
`/market-radar/telegram_token` y `/market-radar/openrouter_api_key`
([inventario de secretos](secrets.md)).

## Inventario (us-east-1)

| Recurso | Nombre | Lo borra el stack |
|---|---|---|
| Stack CloudFormation | `inventarioia` (UPDATE_COMPLETE, creado 2026-08-22) | — |
| Lambdas | `inventarioia-agent`, `inventarioia-alerts`, `inventarioia-webhook` | Sí |
| Roles IAM | 3, uno por Lambda | Sí |
| DynamoDB | `inventarioia-inventario`: 498 ítems, ~98 KB, on-demand, PITR activado, sin protección de borrado | Sí (`DeletionPolicy: Delete`) — **datos permanentes** |
| SQS | `inventarioia-updates.fifo` y su DLQ | Sí |
| API Gateway HTTP | `y99tcu5d4d` (destino del webhook actual del bot) | Sí |
| EventBridge | Regla horaria de alertas | Sí |
| Capa Lambda | `inventarioia-common` versión 10 | Sí (`DeletionPolicy: Delete`) |
| Log groups | `/aws/lambda/inventarioia-{agent,alerts,webhook}` (~0,6 MB, sin retención) | **No**: los crea Lambda fuera del stack |
| SSM | `/inventarioia/openrouter_api_key`, `/inventarioia/telegram_token`, `/inventarioia/telegram_webhook_secret` | **No**: los creó `configurar.py` |
| Bucket SAM | `aws-sam-cli-managed-default-samclisourcebucket-q5u2jew8n8zv` | No; compartido con otros proyectos. `sam delete` borra solo los artefactos de este stack. **No borrar el bucket** |

## Pasos (en orden)

1. Respaldo opcional de la tabla (cuesta centavos; se puede borrar después):
   `aws dynamodb create-backup --region us-east-1 --table-name inventarioia-inventario --backup-name inventarioia-final-2026-10-03`
2. Borrar el stack y sus artefactos, desde `~/projects/inventarioIA`:
   `sam delete --stack-name inventarioia --region us-east-1 --no-prompts`
3. Borrar los log groups (en Git Bash, anteponer `MSYS_NO_PATHCONV=1`):
   `aws logs delete-log-group --region us-east-1 --log-group-name /aws/lambda/inventarioia-agent`
   (repetir con `-alerts` y `-webhook`).
4. Borrar los parámetros SSM de inventarioIA:
   `aws ssm delete-parameters --region us-east-1 --names /inventarioia/openrouter_api_key /inventarioia/telegram_token /inventarioia/telegram_webhook_secret`
5. Comprobar que el radar conserva los suyos:
   `aws ssm describe-parameters --region us-east-1 --query "Parameters[?starts_with(Name,'/market-radar/')].Name"`

## Después

- El webhook del bot queda apuntando a una URL que ya no existe. Telegram
  reintenta y descarta los updates a las 24 h. Se resuelve solo cuando el radar
  registre su webhook (F10).
- Si se hizo el respaldo del paso 1 y ya no se necesita:
  `aws dynamodb delete-backup --region us-east-1 --backup-arn <arn>`.
- El repositorio local `~/projects/inventarioIA` y su remoto en GitHub no se
  tocan con estos pasos; decidir aparte.

## Estado (2026-10-03)

- Paso 4 ejecutado por el usuario: los tres parámetros `/inventarioia/*` están
  borrados. Los del radar (`/market-radar/*`) siguen presentes y legibles
  (verificado sin mostrar valores).
- Pendientes del usuario: pasos 2 (`sam delete`) y 3 (log groups). Mientras el
  stack exista, sus Lambdas fallan al leer secretos, lo cual es esperado.
