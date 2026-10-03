# Seguridad y privacidad

## Estado

Repo documental. No hay autenticación, conectores, servidor ni pruebas de seguridad
del producto implementadas. Ninguna fuente conectada se supone segura por estar
mencionada en la arquitectura. No desplegar este diseño como si fuera un servicio.

## Reglas de acceso

- Solo fuentes públicas o acceso autorizado, dentro de permisos y condiciones aplicables.
- No evadir captchas, bloqueos, controles de acceso ni restricciones de frecuencia.
- No extraer cookies de archivos del navegador ni registrar contraseñas/tokens.
- No entrar automáticamente en grupos ni leer chats/inbox privados por defecto.
- No comprar, reservar, pagar, publicar, enviar mensajes ni seguir cuentas en el MVP.
- Deshabilitar fuentes cuyo acceso requerido no esté verificado o autorizado.

## Amenazas y controles que deben implementarse

| Amenaza | Control y prueba prevista |
|---|---|
| URL maliciosa / SSRF | Validar esquema, hostname, DNS, destino y cada redirect; bloquear redes privadas, metadata cloud y URL con credenciales |
| Inyección de comandos | Argumentos de subproceso estructurados, allowlist de comandos; nunca shell armado con texto externo |
| Prompt injection | Contenido como datos; salida tipada, herramientas limitadas y permisos externos independientes del LLM |
| Oferta engañosa | Variantes, unidades, caducidad y condiciones verificables; no afirmar autenticidad por fotografía |
| Fuga de credenciales | Secretos fuera de Git/logs, mínimo privilegio, redacción y rotación ante exposición |
| Datos cruzados entre usuarios | Autenticación y autorización server-side por propietario/proyecto; pruebas negativas de acceso |
| Corridas duplicadas | Idempotencia, transacciones, checkpoints y auditoría; no garantías sobre efectos externos inexistentes |
| Consumo ilimitado | Presupuestos por fuente/corrida, límites de respuesta/tiempo y circuit breaker |

Leer una página no autoriza a ejecutar enlaces, scripts o instrucciones que aparezcan
en ella. Un guard de patrones no es defensa universal ni sustituye controles de permisos.

## Datos locales y publicación

`.local/`, cookies, `.env`, tokens, bases de datos, cachés, traces de navegador y
catálogos reales no se versionan. Publicar solo fixtures sintéticos o datos autorizados.
Minimizar información de vendedores; no recopilar teléfonos/perfiles como objetivo
del radar. Definir retención, borrado y permisos antes de importar datos reales.

Telemetría sin contenido sensible por defecto: IDs, estados, tiempos y clases de
error. Si el usuario autoriza evidencia adicional, limitarla y redactarla. Evitar
URLs con tokens/PII incluso en logs aparentemente técnicos.

## Antes de multiusuario o cloud

Identidades autenticadas, sesiones seguras, autorización por operación, aislamiento,
auditoría, rate limits y pruebas de acceso cruzado. Revisión del modelo de amenazas
antes de abrir acceso de red. No usar un campo de nombre como identidad autenticada.

## Reportar un problema

No publicar secretos o datos privados en issues. Contactar al propietario por un
canal privado acordado. Si se habilita GitHub Private Vulnerability Reporting,
utilizar ese canal; este documento no afirma que esté activado actualmente.

Referencias: [Meta Automated Data Collection Terms](https://www.facebook.com/legal/automated_data_collection_terms),
[MCP Security Best Practices](https://modelcontextprotocol.io/specification/draft/basic/security_best_practices).
MCP es una posible integración futura, no un servidor ya implementado.
