# Piloto local (2026-10-05)

Estado real, no plan: el MVP corre en la PC del usuario. AWS y OpenRouter quedaron fuera del piloto.

## Piezas

| Pieza | Qué es | Dónde |
|---|---|---|
| Bot de Telegram | `radar.entrypoints.telegram_pilot run` (long polling) | `.local/pilot-state/` |
| IA | Plan del usuario en Claude: `claude -p --model sonnet` (siempre el Sonnet más nuevo) | `pilot_outreach.claude_cli` |
| Búsqueda | Agent Reach → OpenCLI `google search` (usa el Chrome del usuario) | `pilot_sources.OpenCliGoogle` |
| WhatsApp | Bridge local del usuario (whatsmeow), uno por sesión/puerto | `pilot.bridges` en `config.json` |
| Gmail | `gws` local (OAuth del usuario): leer, adjuntos, responder, enviar; sincroniza 30 días | `gmail.py`, `pilot.gmail` |
| Redes | Plugins OpenCLI locales en `workers/opencli/`: `x-dm` (leer + enviar 1 mensaje aprobado), `messenger` e `instagram-dm` (solo lectura); Marketplace con `facebook marketplace-inbox` | `social.py` |
| Voz | Notas de voz de Telegram → faster-whisper local (GPU) | `transcribe.py` |
| Archivo de análisis | Postgres 17 en Docker, solo loopback `127.0.0.1:5434` | `docker/local-db.compose.yml` |
| Arranque | `scripts/start_local.ps1`, acceso directo "Market Radar" en Inicio | — |

Límites de envío (los aplica el bridge): 2 s entre mensajes; contactos nuevos 8 cada 20 min y 30 al día.
Nada se envía sin el botón de aprobación con destinatarios y texto exactos. El control (estados,
aprobaciones, cola) sigue en SQLite cifrado; Postgres es una copia en claro para análisis y puede caerse
sin detener el bot.

## Flujo

Texto libre → plan de la IA (búsqueda + mensaje) → **Buscar** → números publicados (regex, no IA) →
**Enviar a N** → cola hacia el bridge → sincronización de WhatsApp cada minuto → resumen de respuestas
por Telegram cuando un pedido lleva 90 s sin mensajes nuevos.

## Consultas de ejemplo

Conectar: `docker exec -it radar-db psql -U radar -d radar`.

```sql
-- Buscar en todos tus chats (español, texto completo)
SELECT ts, chat_jid, content FROM wa_messages
WHERE to_tsvector('spanish', coalesce(content,'')) @@ plainto_tsquery('spanish', 'silenciador precio')
ORDER BY ts DESC LIMIT 20;

-- Respuestas a tus pedidos, con quién y cuándo
SELECT request_ref, phone, label, ts, content FROM outreach_replies ORDER BY ts DESC LIMIT 50;

-- Qué buscaste y qué números salieron
SELECT s.created_at, s.query, r.title, r.url, r.phones
FROM searches s JOIN search_results r ON r.search_id = s.id ORDER BY s.created_at DESC;

-- Envíos por estado
SELECT state, count(*) FROM outbound GROUP BY state;
```

## Formato en Telegram

Respuestas de la IA en `parse_mode` HTML: el texto se escapa y luego solo se convierten `**negrita**`,
`*cursiva*`, `` `código` ``, viñetas `• ` y títulos; mensajes partidos bajo 4096 caracteres
([Bot API](https://core.telegram.org/bots/api)). Pantallas de aprobación siguen en texto exacto sin formato.

## Pendiente

- Envío por Messenger e Instagram (hoy solo lectura). Messenger se lee solo bajo pedido: abrir su bandeja abre el chat más reciente y puede marcarlo como visto.

- Elegir sesión de WhatsApp en el pedido cuando exista una segunda (hoy se usa la primera).
- La búsqueda lee títulos y fragmentos de Google, no páginas completas.
