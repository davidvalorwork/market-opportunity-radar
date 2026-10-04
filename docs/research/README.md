# Investigación coordinada: redes, sesiones y autonomía

Fecha de apertura: 2026-10-03. Estado: investigación A/B cerrada; laboratorio Docker probado con fixtures.

Revisión posterior: [arquitectura final propuesta por el coordinador](architecture-final-review.md)
contrasta la entrega B con el laboratorio y fuentes primarias. Incorpora outbox,
HTTP primero y límites de efectos/sesiones; responde B-Q015. Es propuesta, no
infraestructura instalada ni habilitación de cuentas o contactos.

Actualización: investigación A/B completada y coordinación cerrada. Encargo
nuevo de laboratorio Docker local: [tareas, contratos y respuestas finales](../testing/WORKPLAN.md).
Se conserva evidencia documental; [mediciones y fallos](../testing/LOCAL_RESULTS.md)
se registran por separado.

Planes de referencia: [testing Lambda local y recursos](../LOCAL_LAMBDA_TESTING.md)
y [gestión sencilla de sesiones cifradas](../SESSION_MANAGEMENT.md).
Interfaces ya implementadas: [browser](../../lab/browser/README.md) y
[sessions](../../lab/sessions/README.md). La captura, cuentas reales y cloud no
están habilitados por las pruebas sintéticas.

Objetivo: elegir componentes open source para descubrir oportunidades comerciales
y, en una fase posterior autorizada, consultar precios a vendedores. Evaluar
Facebook/Messenger, WhatsApp, X, Threads, Reddit, Instagram y otras redes útiles;
no declarar soporte universal por la existencia de un navegador o una sesión.

## División de trabajo y propiedad de archivos

| Frente | Responsable | Archivo exclusivo | Estado |
|---|---|---|---|
| A: capacidades por red, permisos, mensajes y ejecución Lambda | Codex coordinador de este chat | [Informe A](agent-a-platforms.md) | 27 hallazgos; B leído, respuestas e integración documental completas; conectores sin probar |
| B: sesiones portables, cookies, runtimes y alternativas open source | Claude, con acceso al mismo checkout | [Informe B de Claude](agent-b-sessions.md) | Informe disponible; diseño revisado Lambda-only; sin pruebas de cuentas/runtime |
| Integración, plan y decisiones | Solo coordinador A | Este README y [decisiones](decisions.md) | Provisional, sin selección definitiva |

El prompt para Claude (frente B) está en [prompt-agent-b.md](prompt-agent-b.md).
Codex es el frente A y coordinador; Claude es el frente B. Los nombres de archivo
e IDs A/B se conservan para mantener enlaces y coordinación estables.
Los dos agentes leen este índice y el informe del otro antes de investigar y al
cerrar cada bloque. Cada uno escribe únicamente su informe. Las preguntas y
respuestas se anotan en la sección de coordinación de su propio archivo, con ID
`A-Q001` / `B-Q001`, fecha y referencia al ID respondido. No se sobrescriben ni
se borran hallazgos del otro. El coordinador incorpora las conclusiones al índice
y al registro de decisiones tras leer B; no se asume sincronización automática.

No hacer checkout, merge, commit, push o formateos globales mientras ambos agentes
trabajan en el mismo checkout. Se permiten ediciones locales con `apply_patch` en
los archivos asignados; el coordinador verifica el diff al integrar.

## Plan de investigación

1. Verificar repositorio, licencia, mantenimiento y documentación primaria.
2. Separar búsqueda, lectura, comentarios públicos, DM nuevo, respuesta a DM y
   recepción de mensajes. Un comentario no sustituye un DM.
3. Comparar sesión de Chrome existente, perfil dedicado, estado exportado,
   cookies aisladas, OAuth y sesión multidispositivo. No son intercambiables.
4. Estimar operación local, servicio persistente y Lambda convencional:
   reinicio, caducidad, bloqueo, concurrencia por cuenta y reconexión.
5. Diseñar pruebas con fixtures/locales primero y pruebas reales solo con
   autorización específica. No contactar vendedores durante esta investigación.
6. Elegir una vertical mínima y redactar ADR final después de integrar ambos
   informes; conservar desacuerdos y alternativas descartadas.

## Formato obligatorio de cada hallazgo

- ID y fecha de consulta; plataforma y componente/versión o commit si se obtuvo.
- URL primaria que respalda cada capacidad, licencia y limitación.
- Estado: `documentado`, `inspeccionado`, `probado_local`, `probado_real`,
  `inferencia`, `pendiente` o `descartado`.
- Operación concreta, requisito de autenticación y entorno de ejecución.
- Costo de software separado de alojamiento, API, modelo y mantenimiento.
- Riesgo, condiciones de uso, recuperación, prueba pendiente y recomendación.

Estrellas, actividad de Git, README, comandos disponibles y login detectado no
son prueba de un flujo funcional. Si no se leyó una fuente o una prueba falla,
anotarlo; nunca sustituir el fallo por una afirmación de soporte.

## Sesiones: compartir referencias, no secretos

La documentación compartida solo contiene alias sintéticos como
`SessionRef(platform="x", account="seller-research", profile="research")`.
No contiene cookies, contraseñas, QR, tokens, URLs firmadas, cuentas reales,
conversaciones privadas ni capturas de autenticación. Una futura bóveda local
cifrada o un gestor de secretos mantendría el material fuera de Git y lo
entregaría solo al worker autorizado. Existe un vault local experimental age para
archivos explícitos; no es un servicio autenticado multiusuario de producción.

El acceso del usuario a una cuenta no elimina permisos de plataforma ni autoriza
contactos indiscriminados. Caducidad, CAPTCHA o MFA producen estado
`needs_reauth`; no se promete resolverlos automáticamente ni evadirlos.

## Preguntas de decisión

- ¿Podemos conservar Agent Reach/OpenCLI para leer y usar bridges específicos
  solo para mensajería, sin crear un adaptador API distinto por cada operación?
- ¿Cómo se comparte una sesión entre workers propios sin compartir un perfil
  writable ni exponer credenciales a un LLM?
- ¿Qué redes permiten iniciar DM, cuáles solo responder y cuáles solo publicar?
- ¿Qué parte funciona en Lambda y qué parte necesita un gateway persistente?
- ¿Qué opción minimiza cargos API y tokens sin prometer costo total cero?
- ¿Qué pruebas mínimas habilitan una red y qué licencia afecta distribución?

## Criterio de cierre

Integración 2026-10-03: Claude registra la preferencia del usuario por Lambda bajo
demanda. Propuesta conjunta revisada: Chromium/Playwright efímero + OpenCLI CDP
para lectura y whatsmeow intermitente para WhatsApp. Matrix/Beeper Desktop quedan
alternativas fuera del MVP Lambda-only. No se ha demostrado aceptación de sesiones
desde AWS ni recepción fiable por reconexión. Ver A-F26/A-F27 y B-F014/B-F015.
S3 CAS/FIFO protegen parte del estado; no garantizan envío único. Próximo paso:
fixtures y pruebas de runtime, sin contactar vendedores. Dispositivo WhatsApp y
fallback local siguen requiriendo decisión; presupuesto total pendiente.

Matriz por red, comparación de sesiones, arquitectura propuesta, presupuesto
explícito, pruebas de aceptación y ADR con enlaces primarios. El cierre de esta
investigación no instala componentes, conecta cuentas ni habilita envíos.
