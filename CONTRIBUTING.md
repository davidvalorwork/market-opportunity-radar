# Contribuir a este repositorio

Esta guía se refiere al proyecto propio; no inicia contribuciones a repos externos.

1. Leer visión, dominio, roadmap, fuentes y seguridad.
2. Trabajar un caso pequeño y verificable de la vertical local antes de sumar frameworks.
3. Usar rama `codex/<tema>` cuando trabaje Codex; cambios humanos pueden usar su convención acordada.
4. Agregar fixtures sintéticos y pruebas positivas/negativas para cambios funcionales.
5. Documentar límites, contratos, campos desconocidos y cómo reproducir el resultado.
6. Ejecutar controles documentales y tests reales antes de proponer la integración a main.

```text
python scripts/check_docs.py
```

Todavía no existe suite funcional del radar. No afirmar cobertura por tener un
adaptador vacío, una credencial presente o un comando descubierto.

Mensajes de commit claros: `docs:`, `feat:`, `fix:`, `test:` o `chore:` según el cambio.
Dependencias nuevas necesitan motivo, licencia y costo/impacto operacional. Mantener
un modo de tests local sin redes, credenciales ni APIs pagadas.

No enviar imágenes, precios o contactos reales a un modelo ni versionarlos sin
autorización. No copiar código externo sin respetar licencia/atribución. Informar
fallos o resultados negativos; no ocultarlos con retries o edición de etiquetas.
