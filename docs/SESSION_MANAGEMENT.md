# Plan: compartir y renovar sesiones sin exponer cookies

Fecha: 2026-10-03. Estado: **diseño y CLI experimental implementado parcialmente**.
Complementa [testing local Lambda](LOCAL_LAMBDA_TESTING.md) e investigación A/B.
[Interfaz real y límites de sesiones](../lab/sessions/README.md): prepare/share,
import, renew, status y selftest. Se generan claves y sesiones exclusivamente
sintéticas para medir el laboratorio. No se accede a Chrome personal ni se hace
login. El navegador de captura, leases activos y almacenamiento cloud siguen pendientes.

## 1. UX propuesta: una captura y una renovación guiadas

No pedir JSON de cookies por chat ni extraer bases del Chrome personal.
La opción principal será un navegador de captura dedicado controlado por el
usuario, no acceso automático a todas sus sesiones.

```text
capture / renew <alias>
        ↓
Abrir navegador local dedicado, visible, dominio permitido
        ↓
Usuario completa login/MFA si hace falta
        ↓
Prueba de lectura sin efectos → exportar estado → cifrar
        ↓
Publicar versión nueva de forma atómica
        ↓
Workers autorizados cargan la versión por SessionRef
```

La promesa es pocos pasos y ninguna copia manual de cookies, no login eterno
ni MFA/CAPTCHA automáticos. Sesión ligada a IP/dispositivo puede no aceptar AWS;
si sucede, needs_reauth/blocked, sin proxies de evasión ni fallback no autorizado.

Interfaz futura bajo `radar session`; **no existe aún**:

| Operación propuesta | Qué hace | Qué no hace |
|---|---|---|
| capture <alias> | Navegador dedicado + login manual + validar + cifrar | Leer cookies del Chrome personal o chats privados |
| check <alias> | Estado y lectura de prueba específica, sin contenido en salida | Renovar a la fuerza o enviar mensaje de prueba |
| renew <alias> | Abrir sesión de captura existente; completar login si caducó; publicar versión nueva | Compartir un perfil writable con worker |
| share <alias> --recipient-file <ruta> | Bundle cifrado para destinatarios permitidos | Imprimir cookies o crear enlace público |
| import --bundle <ruta> | Descifrar localmente, comprobar metadatos y validar antes de activar | Ejecutar scripts/configuración incluidos por un tercero |
| revoke <alias> | Denegar nuevas operaciones, invalidar versiones y guiar logout remoto | Deshacer mensajes o borrar copias ya descargadas en otro equipo |

Comando único futuro `radar session renew <alias>` debe completar captura,
validación y publicación, sin tener que parar/editar cada worker a mano.
Para fuentes opcionales vencidas, el pipeline sigue con otras; una fuente
requerida falla preflight antes del navegador, mostrando exactamente qué renovar.

## 2. Material mínimo y límites de portabilidad

- Web: Playwright storageState, cookies/localStorage y `indexedDB: true` cuando
  la fuente lo necesita. Restaurar en un contexto nuevo, comprobar cuenta/alias
  esperado y target CDP antes de leer. Estado debe capturarse tras redirects y
  readiness del login, no inmediatamente tras pulsar entrar [S1/S2].
- sessionStorage no se persiste automáticamente; una fuente que dependa de él
  requiere soporte explícito y prueba, o se marca no portable. No exportar por
  defecto OPFS, cachés completas, service workers ni claves WebAuthn/passkeys.
  Revisar los campos que añade la versión fijada de Playwright.
- Cookie-Editor JSON puede importarse como alternativa manual si está autorizado;
  no reemplaza localStorage/IndexedDB. Validar esquema/dominio, tamaño y rechazo
  de cookies de redes ajenas. No fabricar valores de autenticación faltantes.
- WhatsApp: claves de dispositivo, no cookies. Vinculación propia/exclusiva
  mediante QR/código cuando se autorice. Copiar la SQLite de un bridge en ejecución
  clona identidad y puede causar conflictos. No compartir ese dispositivo entre
  PC y Lambda; el diseño específico debe decidirse antes de conectarlo.
- Telegram/OAuth, si se añaden, usan tipos separados. No hay conversor universal
  de cookies ni una sesión válida para todas las plataformas.

## 3. Compartir archivos cifrados con age

Reutilizar [age](https://github.com/FiloSottile/age), en lugar de diseñar criptografía.
CLI/librería con cifrado a uno o varios destinatarios [S3]. age no está instalado
en el PATH inspeccionado; verificar versión/licencia/build antes de incorporarlo.

Cada consumidor genera su identidad privada y comparte solo su clave pública.
El capturador cifra el bundle para el worker y el propietario; puede repetir
destinatarios. Los agentes de investigación intercambian únicamente referencias
y documentación, no reciben secretos por tener acceso al checkout.

Ejemplo sintético de manifiesto interno, cifrado junto al estado:

```json
{
  "schema_version": 1,
  "session_ref": "threads:research-demo",
  "version": 3,
  "state_type": "playwright_storage_state",
  "created_at": "2026-10-03T12:00:00Z",
  "last_verified_at": "2026-10-03T12:00:00Z",
  "renew_after": "2026-10-04T12:00:00Z",
  "allowed_origins": ["https://www.threads.com"],
  "allowed_operations": ["search", "read_post"],
  "producer_version": "capture-tool-pinned"
}
```

Solo muestra contrato; alias/fechas sintéticos. `renew_after` es política local,
no expiración garantizada. El estado real nunca se pone en este documento.
Registry público para tooling contiene alias sintéticos; cuentas/rutas reales,
ACL, destinatarios y configuración operativa permanecen privados.

- Bundle en .local/sessions/<alias>/<version>.age; identidad privada fuera del
  repo/build, protegida por ACL/keyring del OS. No guardar clave junto al bundle
  en almacenamiento compartido. Copiar solo .age por canal autenticado privado.
- Alias usado como directorio: slug validado sin colon, separadores ni traversal
  (Windows compatible). SessionRef puede incluir colon como dato, no como ruta;
  resolverlo mediante registro y nunca concatenar input libre al filesystem.
- Importación requiere origen confiable además de integridad del cifrado: age
  no certifica quién creó el bundle. Transporte autenticado, política del emisor
  y firma de manifiesto si el modelo multiusuario lo exige.
- No subir bundles, ni siquiera cifrados, al repo público, issues o CI. Evitar
  links públicos; URL firmada también es secreto, con TTL y sin logs.
- Exportar a memoria → cifrar → escritura atómica, evitando JSON plaintext en
  disco. Si una librería exige temporal, usar ruta privada/ACL restrictiva y
  cleanup; borrar archivo no garantiza borrado forense en SSD.
- Transporte local inicial sin cloud; worker descifra solo cuando tiene lease.
  Mantener material el mínimo tiempo. No pasar claves en argumentos/history ni
  stdout/logs de procesos. No enviar secreto a LLM, MCP ni telemetría.
- Cloud futuro: objeto S3 privado cifrado por cliente y control de acceso IAM;
  identidad del worker mediante un proveedor de secretos autorizado. Evaluar
  SSM/KMS/alternativa y sus costos antes de habilitar. No meter identidad privada
  en imagen o repositorio ni activar cloud en este encargo.

Compartir cifrado otorga al destinatario capacidad real de usar la cuenta;
la allowlist local no reduce criptográficamente los poderes de esas cookies.
Dar acceso solo a workers propios confiables; tareas no confiables usan un broker
de operaciones, sin recibir el bundle ni tener permisos de descifrado.

## 4. Renovación, concurrencia y revocación

Registry privado con estados valid, needs_reauth, renewing, revoked, unsupported.
Metadatos sin cookies: SessionRef, versión, verificación, políticas y lease owner.
Solo una operación activa por cuenta; no confiar en la fecha de una cookie para
declarar que la sesión sigue válida. Prueba barata cuando la verificación esté
vencida; observar errores de cada operación. Evitar healthchecks excesivos.

Renovación:

1. Marcar renewing y pausar nuevos leases; esperar cierre/deadline del worker
   activo. No reemplazar archivos que otro proceso está escribiendo.
2. Reabrir el navegador de captura del alias con política de origen; el usuario
   completa login solo si hace falta. Nunca usar contraseña guardada por el script.
3. Verificar lectura y propietario esperado; cifrar nueva versión immutable.
4. Publicar puntero activo atómicamente, por CAS contra versión esperada.
5. Reanudar workers; fallos de captura no destruyen estado anterior, pero tampoco
   lo marcan válido si ya expiró. Worker viejo no publica sobre versión nueva.

Cloud usaría S3 If-Match para proteger publicación de estado, no como garantía
de fencing del mensaje: el servicio remoto no comprueba nuestro lease [S4].
Para futuros efectos externos: ledger, checks de lease/plazo y send_uncertain
con reconciliación. Leer SessionRef no autoriza escribir a vendedores.

Revocación: denegar nuevos leases, rotar autorizaciones/claves según exposición,
cerrar sesión en la plataforma y retirar copias controladas. Quitar destinatario
de un bundle nuevo no revoca uno antiguo que ya descifró; logout/invalidation
remoto es necesario. Memoria/disco local pueden persistir tras crash: en siguiente
arranque verificar y limpiar temporales privados; no alegar borrado garantizado.

Retención inicial propuesta: 1 versión activa y como máximo 1 anterior por 24 h,
salvo revocación/exposición (no rollback). Plazos ajustables por plataforma;
datos secretos no entran en backups generales ni artifacts. Telemetría solo
versiones sintéticas/clases de error/tiempos, sin identidad, cabeceras o cookies.

## 5. Pruebas de aceptación

1. Fixture con cookie + localStorage + IndexedDB: captura/restauración correcta.
2. Fixture sessionStorage: dependencia ausente detectada, no éxito falso.
3. Bundle alterado, clave equivocada, origen ajeno, tamaño excesivo: rechazo seguro.
4. Dos destinatarios autorizados descifran; un tercero no; sin plaintext en logs.
5. Importación de JSON solo cookies falla explícitamente si falta otro almacén.
6. Renovación con worker activo y CAS perdido: sin sobreescritura ni sesión cruzada.
7. Revocación: nuevos workers rechazados; prueba distingue copia vieja aún útil
   de invalidación real del servidor. El fixture simula esta diferencia.
8. Error/MFA/CAPTCHA: needs_reauth y guía de renew; sin loops de login ni envíos.
9. Captura real autorizada: portátil entre contextos/hosts y prueba AWS separada.
   No extrapolar éxito local a todas las redes o IPs.

## Fuentes primarias consultadas, 2026-10-03

- S1: [Playwright: autenticación y sessionStorage](https://playwright.dev/docs/auth).
- S2: [BrowserContext: storageState e IndexedDB](https://playwright.dev/docs/api/class-browsercontext).
- S3: [age: destinatarios, identidades y librería](https://github.com/FiloSottile/age).
- S4: [AWS S3: escrituras condicionales](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html).

Documentación primaria consultada por Agent Reach/Jina/GitHub. El diseño de UX,
retención, leases y cifrado todavía necesita implementación y pruebas; las
fuentes no demuestran que una sesión de una red concreta sea portable.
