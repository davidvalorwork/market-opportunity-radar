# Zonas IANA en el runtime A15

La prueba independiente de A11 detectó que el entorno Windows bloqueado no tenía
base IANA: `ZoneInfo('America/Caracas')` fallaba con `ZoneInfoNotFoundError`.
Las pruebas de recurrencia con zonas inyectadas no cubrían esta instalación.

Se añade `tzdata==2026.4` tanto a dependencias de runtime como al lock. Es el
proveedor de datos IANA de Python Software Foundation, versión ya disponible
en la caché local; no añade un SDK, API, IA ni servicio de pago. Su metadata
declara Apache-2.0 y la distribución cacheada contiene IANA `2026d`. No se
afirma que sea la última versión disponible en internet. El fallback estándar
de `zoneinfo` carga el paquete cuando el sistema no ofrece esos datos.

No se modifica el calendario A11 ni se reemplazan zonas por offsets fijos.
No se fuerzan datos empaquetados en producción: en Linux puede prevalecer la
base del sistema. Las pruebas limpian la caché y vacían `TZPATH` temporalmente
para exigir el fallback del paquete en cualquier SO, y restauran el estado.

## Regresiones reales

[Tests](../../tests/test_timezone_runtime.py): Caracas por defecto; salto de
primavera de Nueva York; ambas políticas del fold de otoño con un solo disparo;
semana de Londres tras cambio de hora; zona desconocida falla explícitamente.
Son fechas y zonas IANA reales, no cuentas, fuentes o programaciones activadas.

Antes de la dependencia: **5 fallos y 1 aprobado**, falta de paquete/base IANA.
La prueba con el proveedor cacheado en un target privado da **6/6**. Un venv
nuevo instalado offline desde el lock contiene las 15 dependencias fijadas:
**6/6** pruebas de zonas (2.41 s), **706 tests + 246 subtests** de la suite
(51.48 s), sin skips/fallos; docs y diff aprobados. El venv de revisión anterior
no se modificó. Es un candidato aislado, no está integrado en main ni desplegado.
No demuestra `/tareas`, EventBridge, autorización o ejecución de mensajes.

Configuración reproducible: instalar `requirements.lock` y el paquete `radar`
antes de ejecutar pruebas. Si la base requerida está ausente/corrupta, A11 sigue
fallando cerrado con `timezone_unavailable`, sin elegir la zona local a escondidas.
