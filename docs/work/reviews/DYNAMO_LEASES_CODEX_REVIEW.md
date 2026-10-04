# A26 — revisión A25 contra main

Fecha: 2026-10-04. Base elegida expresamente por el usuario: **main**,
congelada en `1d7e6a18279d9b341701a48d225c471dbf888721`.
Preparación revisada: `fd974fbd1af29545c0833d69833ceb943e9c5362`.
Diff: `git diff 1d7e6a18279d9b341701a48d225c471dbf888721...fd974fbd1af29545c0833d69833ceb943e9c5362`.
Doce archivos; commits adicionales `fd974fb`, `89d0a4d`, `12d6fe1`, `56f9773`.
Los dos revisores son distintos del autor A25 y trabajaron en solo lectura.

La especificación es el corte LeaseStore de F7, los puertos y conformidad A5,
el plan de implementación y las decisiones de arquitectura. Los estándares
son AGENTS/CONTRIBUTING, aislamiento de capas, contratos, seguridad y la
batería de heurísticas de code-review. La ausencia de issue-tracker/setup
se cubrió con estos documentos existentes; no se instaló una skill inexistente.

## Standards

**A26-S: apto para el corte local. Cero incumplimientos; cero smells accionables.**

Cliente inyectado, autorización fresca antes del IO, CAS completo, lecturas
fuertes, microsegundos enteros y epochs conservados al liberar. Solo el error
condicional se convierte en contención; errores desconocidos/timeouts se
propagan. El constructor rechaza configuración insegura de retries/timeouts.
Importar el adaptador no crea un cliente ni exige el SDK opcional.

El lock mantiene los 14 pins base y fija 37 paquetes con hashes. La CI dedicada
exige SDK/moto y no trata un skip por ausencia como evidencia. Los seis casos de
leases se comparten entre SQLite/moto sin duplicar negocio ni eliminar los otros
checks A5. No se detectó regresión de los dos gaps anteriores de A5.

## Spec

**A26-R: apto para LeaseStore local. Cero faltantes no declarados, scope creep
o implementaciones incorrectas.**

Acquire condicional, renew sobre snapshot vigente, release CAS con tombstone y
consulta fuerte cumplen el corte. Un owner string no demuestra autorización.
Las regresiones cubren referencias/snapshots alterados, pérdida de respuesta,
errores condicionales frente a servicio y precisión temporal. La misma batería
LeaseStore corre con SQLite y el cliente SDK emulado.

F7 sigue parcial: UoW/outbox DynamoDB, reparación/Streams, S3/SQS/SSM, IAM,
concurrencia distribuida y fencing de efectos externos no están implementados
ni acreditados por moto. No se habilitaron bot, cuenta real, cloud o mensajes.

## Evidencia ejecutada y límites

| Ejecutor y entorno | Resultado observado |
|---|---|
| Autor, Windows, SDK/moto fijados | Área 43/43 en 10,80 s; suite 594 + 238 subtests en 46,98 s, sin skips |
| Coordinador, preparación `fd974fb`, Windows | Suite 594 + 238 subtests en 47,30 s, sin skips; docs/diff verdes |
| Coordinador, wheel instalado offline en venv nuevo | 82 + 220 subtests en 12,13 s, sin skips; pytest `-o pythonpath=`; import de radar desde site-packages |
| Coordinador, Linux Docker empaquetado | Área 43/43 en 4,09 s; bloque Python 4,401 s; pico RSS proceso 127880 KiB (124,9 MiB) |
| Autor, entorno base sin SDK | 22 passed + un skip explícito; NO acredita DynamoDB; modo requerido falla colección como se esperaba |

Los revisores hicieron revisión estática/diff, no repitieron la suite completa;
los tiempos de pruebas se atribuyen a quienes realmente las ejecutaron.
El wheel usa el mismo código y lock revisados; las notas añadidas después son
solo documentación. Los checks de arquitectura inspeccionan el checkout.
Se verificó también que importar el adaptador en el entorno base no carga
boto3/botocore/moto. CI remota Windows/Linux preparada, **no ejecutada aquí**.

El primer ensayo Docker con bind mounts Windows se canceló tras más de 250 s:
progreso parcial, sin OOM observado, muestra 93,2 MiB (no pico). No se presenta
como verde. Se cambió solo el arnés: fuentes/tests/pyproject y dependencias
confiables se empaquetaron y extrajeron en `/tmp` Linux; código/lock iguales.

Ensayo exitoso: imagen local A2 existente
`sha256:3d7c44b202068ea080dd6f0ffacbd826e94d175317e9824be085d94b38fa2176`,
512 MiB, 1 CPU, raíz read-only, red deshabilitada, tmpfs 384 MiB, capabilities
eliminadas/no-new-privileges. No se incluyeron cookies ni archivos privados.
El contenedor propio se autoeliminó; los cinco contenedores ajenos quedaron
intactos. Los 4,401 s excluyen extracción/arranque e import inicial de pytest.
RSS es del proceso, no consumo total del contenedor/tmpfs. No es p95, benchmark
Lambda ni estimación de factura AWS. RAM libre inicial observada: 9,120 GiB.

SDK opcional fijado: boto3 1.43.108; emulador de pruebas moto 5.2.3, Apache-2.0
verificada en PyPI. Extras/lock/CI viven en la preparación privada del
coordinador; los seis archivos del autor están limpios en `89d0a4d`.
Las copias WIP del coordinador se retiraron del worktree autor solo después de
preservarlas y comprobar el lock idéntico. Se conservaron artefactos ignorados.

No hubo cambios a main, push ni llamadas AWS/modelo. Las pruebas no autorizan
integración o despliegue. Contexto y fuentes: [DYNAMO_LEASES](../../testing/DYNAMO_LEASES.md).

Resumen: Standards **0** (sin peor hallazgo); Spec **0** (sin peor hallazgo).
