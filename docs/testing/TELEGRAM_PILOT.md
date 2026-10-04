# Piloto local general: Telegram, investigación y WhatsApp

Este corte conecta módulos existentes con un host local opt-in. No es un
despliegue Lambda ni prueba de cobertura universal de redes sociales. Estado
real de vinculación/envío debe registrarse separado de las pruebas offline.

## Procedimiento de producto

1. Telegram autentica contacto propio en la allowlist y consentimiento vigente.
2. `/ayuda` lista comandos determinísticos. No llama a un modelo ni OpenRouter.
3. `/vincular +numero` crea una cuenta/sesión aislada y muestra el código sólo
   por Telegram privado. El propietario confirma desde su teléfono. El helper
   comprueba que el JID autenticado corresponde al número declarado.
4. `/chats` lista hasta 20 chats conocidos y un alias por chat. **No descarga
   todo el historial**: una sesión nueva puede conocer inicialmente sólo self.
5. `/leer alias` habilita la captura de ese chat antes de conectar. Sólo sus
   mensajes nuevos/sincronizados se importan. El ID real del proveedor sirve
   para dedupe; dos textos iguales no son el mismo mensaje. ACK sólo después
   de importar de forma durable en el inbox privado A10.
6. `/investigar tema público` propone un plan. Al confirmar, consulta Exa mediante
   la instalación Agent Reach/mcporter del operador, con cuotas persistentes.
   No copia automáticamente conversaciones privadas a la búsqueda pública.
7. `/responder alias texto` propone un borrador literal. Confirmar el plan NO
   envía: la pantalla siguiente muestra destinatario y texto exactos y solicita
   aprobar el envío A10. El bridge usa el mismo cliente Go wameow autenticado.
8. `/prueba_whatsapp` usa únicamente el self chat observado por el proveedor.
   El permiso host opt-in sólo acepta propósito y texto de prueba fijos; la
   prohibición general de self-contact sigue siendo el comportamiento por defecto.
9. El envío anota intención durable antes del efecto. Confirmación del proveedor
   no demuestra lectura ni entrega final. Una salida incierta nunca se reenvía
   automáticamente; conserva estado para revisión.

Vincular una sesión no acredita capacidad de envío. Antes de la prueba propia,
compose/contact permanecen `documentado`: el host sólo permite el destinatario,
propósito y texto fijos de `/prueba_whatsapp`, con aprobación exacta. Sólo una
confirmación real del proveedor habilita respuestas ordinarias en ese piloto.

`/estado` muestra corridas; `/stop` revoca ejecución local. No hace Logout del
dispositivo por sí solo. No se interpreta texto libre con IA en este piloto.

## Continuar y limitar investigación

`/mas 20` pide hasta 20 resultados nuevos del último tema, no 20 consultas. El
ID de investigación se conserva aunque cambie la propuesta Telegram. URLs/cuerpos
vistos se omiten; datos sobrantes de una respuesta se guardan para la siguiente
pasada sin volver a consultar. Un reinicio no renueva el presupuesto del mismo
pass_ref. Una pasada nueva requiere un nuevo pedido/confirmación.

Exa MCP no ofrece cursor nativo en esta ruta: el cursor representa rondas de
descubrimiento con consultas alternativas, **no** páginas exhaustivas del proveedor.
Puede devolver URLs repetidas; se contabilizan y no duplican contenido cifrado.
`/refrescar` vuelve a consultar: contenido idéntico renueva observación sin duplicar
blobs; un cuerpo distinto cuenta como novedad. No promete descubrimiento infinito.

Ejemplo de configuración por Telegram:

```text
/limites resultados=20 consultas=3 paginas=3 tiempo=60 bytes=524288
```

Se detiene al alcanzar el primer techo: resultados nuevos, llamadas, páginas/rondas,
bytes recibidos o plazo. Defaults: 20 resultados, 3 consultas, 3 rondas, 60 s,
2 MiB. Topes del piloto: 100 resultados, 10 consultas/rondas, 300 s, 2 MiB.
El plazo empieza al ejecutar, no al presentar el plan. Una petición por paso
y por tick evita mantener una lease de corrida durante todo el lote.

## Persistencia y privacidad

`control.sqlite` guarda refs, hashes por propietario, contadores, estados,
versiones y pointers; no guarda el texto de consultas/chats en esas tablas.
Documentos de fuente y continuaciones quedan como blobs externos privados,
gzip nivel 3 antes de age, con índice compacto y límites de descompresión.
La caché comprueba nuevamente propietario, actor y consentimiento: no concede
autoridad. TTL inicial 7 días y cuota de almacenamiento; TTL no equivale a borrado
físico. GC físico queda pendiente de una herramienta explícita segura.

La sesión de transporte WhatsApp incluye la DB whatsmeow en una carpeta privada
con ACL del operador. **No se afirma cifrado age en reposo de esa DB SQLite viva**.
Mensajes proyectados, teléfono de vinculación y resultados IPC se guardan cifrados
por el host A14; código de vinculación sólo RAM/Telegram privado. No exportar la
carpeta de sesión sin un proceso explícito de cifrado/renovación.

## Administración técnica

Dependencias fijadas del proyecto; helper age, keygen y WhatsApp compilados del
mismo corte revisado. Agent Reach/mcporter/Node deben estar instalados y funcionar.
El setup valida rutas instaladas, no demuestra capacidad; cada consulta real
requiere éxito de lectura. No instala herramientas, modelos ni fuentes sociales.

```text
python -m radar.entrypoints.telegram_pilot setup --state-dir PRIVATE_NEW_DIR --allowlist EXISTING_ALLOWLIST --vault-helper VAULT_EXE --keygen-helper KEYGEN_EXE --whatsapp-helper WHATSAPP_EXE
python -m radar.entrypoints.telegram_pilot run --state-dir PRIVATE_DIR --ssm-token-ref /market-radar/telegram_token --region us-east-1 --take-over-bot
```

Rutas son argumentos administrativos, no instrucciones enviadas al bot. Secretos
nunca en argv; token por entorno o lectura SSM explícita. Setup crea un directorio
nuevo y no sobrescribe claves/configuración. `--take-over-bot` requiere autorización
para retirar el webhook: `drop_pending_updates=False`. No se restaura el webhook
sin conocer/verificar su configuración anterior (certificado, secreto y servidor).

## Verificación

Áreas: `tests/telegram_pilot`, `tests/research_cache`, `tests/whatsapp_bridge`,
`tests/general_flow`, `tests/conversations`, `tests/local_telegram`.
Helper: `go test ./...` y `go vet ./...` en `helpers/whatsapp-local` y
`helpers/private-vault`; módulo `go/` prueba el mismo cliente real sin cuenta.
Fixture/RPC simulado no son evidencia de vinculación ni envío real.

Gate local observado por el coordinador, 2026-10-04: `RADAR_REQUIRE_AWS_TESTS=1`
con entorno fijado y tzdata: **1388 tests +251 subtests**, todos pasan en426,33s.
Tres módulos Go (helpers WhatsApp, vault y módulo común): test/vet verdes;
worker navegador31/31, check_docs y diff --check verdes. Revisión independiente
del piloto y de la copia offline: sin hallazgos abiertos del corte revisado.
No se ejecutó GitHub Actions ni se desplegó AWS como parte de esta verificación.

## Reutilizar una sesión entregada por el propietario

La herramienta administrativa `radar.entrypoints.session_snapshot` requiere
`--authorize-session-copy`, una DB de protocolo explícita y un directorio NUEVO.
Usa backup consistente SQLite, incluyendo transacciones WAL ya confirmadas;
no copia `messages.db`, fotos ni archivos de la carpeta del bridge. Conserva el
original y no conecta red. La copia contiene credenciales: sólo carpeta privada,
nunca Git, contexto de build ni imagen Docker.

[El comprobador Docker](WHATSAPP_DOCKER_SESSION.md) verifica la copia sin red.
**Observado por el coordinador, 2026-10-04:** copia de 13.983.744 bytes, cliente
compatible, número propio coincidente y mapping self válido; exit0, 0,781 s,
sin OOM. Contenedor UID65532, raíz readonly, red none, límite512MiB/CPU1/pids64,
tmpfs privado128MiB. No se midió RAM pico ni coste Lambda. El contenedor temporal
se retiró; la copia privada se conserva para la prueba autorizada.

Esto no prueba login activo, lectura ni envío. No hay importación automática a
una identidad Telegram: se mantiene autenticación/consentimiento. Tampoco se
conecta un clon en paralelo al bridge original; un handoff vivo requiere
coordinar pausa/restauración del cliente anterior y verificar estado del proveedor.

Gate real pendiente hasta observar: polling Telegram → vinculación humana → chat
propio verificado → texto fijo aprobado → respuesta del proveedor → lectura del
mensaje de prueba sin duplicación. Sólo entonces declarar completo ese trayecto.
