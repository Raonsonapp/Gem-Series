---
title: GEM SERIES BOT
emoji: 🎬
colorFrom: purple
colorTo: blue
sdk: gradio
sdk_version: "5.49.1"
app_file: app.py
app_port: 7860
pinned: false
---

# GEM SERIES BOT

Independent film/series importer. It uses its own Neon PostgreSQL project and an MTProto listener. It does not depend on Popular-Anime-Bot.

## Hugging Face Space secrets

Required:
- `BOT_TOKEN`
- `DATABASE_URL` — connection string from the new `gem-series-db` Neon project
- `WEBHOOK_SECRET` — random long secret used only for Telegram webhook validation
- `PUBLIC_URL` — full HTTPS URL of this Space
- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`

Optional:
- `BOT_USERNAME` (default `GEMSERIESTVBOT`)
- `SOURCE_CHANNEL` (default `GEM_SERIES_TJ`, username without @)
- `DATABASE_CHANNEL` (default `@database_gem_series`)
- `DESTINATION_CHANNEL` (default `@afsonaishab`)
- `SUBSCRIPTION_CHANNEL` (default `@tm_score`)
- `CHANNEL_URL`
- `SUBSCRIPTION_URL`
- `SPONSOR_BOT_URL` (empty by default; sponsor is optional)

## One-time MTProto session setup

The user-account listener is needed because the source channel is not owned by this bot and the bot cannot be added there. The session is stored in the dedicated PostgreSQL table `gem_listener_sessions`; there is no `TELETHON_SESSION` Hugging Face secret.

Run `tools/setup_mtproto_session.py` once on a trusted computer with Python and the requirements installed. Set `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and the new GEM SERIES `DATABASE_URL` in that local terminal environment. The script asks for the Telegram phone and login code locally and writes the resulting session into the GEM SERIES database. Never paste login codes or session strings into chat or GitHub.

## Telegram permissions

- The Telegram account used for the MTProto listener must already be a member of the source channel.
- That account must be able to post in `@database_gem_series` (make it an administrator there with posting permission).
- `@GEMSERIESTVBOT` must be an administrator in `@database_gem_series` and `@afsonaishab`, with permission to post/copy.
- The bot must be able to check membership in `@tm_score` (make it an administrator there).
- The bot does not need to be an administrator in the source channel.
- Content must be authorized for redistribution.

## Runtime

On startup the app creates its own `gem_posts` and `gem_listener_sessions` tables in the new Neon database. Check `/health` for database and listener status. A healthy web process does not mean the MTProto listener is active: `listener_connected` must be true.

Free hosting may sleep or restart; persistent Neon storage keeps records and the Telegram session, but does not guarantee 24/7 process uptime.
