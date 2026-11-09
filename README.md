---
title: GEM SERIES BOT
emoji: 🎬
colorFrom: purple
colorTo: blue
sdk: gradio
app_file: app.py
pinned: false
---

# GEM SERIES

Sessionless mode: the app polls the public Telegram preview page at `https://t.me/s/<channel>`.

## What it does
- Checks the public channel page every `POLL_SECONDS` (default 180 seconds).
- On startup, tries to publish one media post visible on `INITIAL_SCAN_DATE` (default 2026-11-09).
- Stores the post caption and source URL in the configured database channel.
- Publishes a banner with a link to the original public Telegram post.
- Checks membership in the configured subscription channel when the user opens the bot deep link.
- Sponsor bot button is optional only.

## Important limitation
This mode does **not** download, copy, or redistribute the actual video from the source channel. It archives metadata and a link to the original post. Telegram's public preview page does not reliably expose the original media file. This avoids requiring a personal Telegram session and avoids bypassing protected-content restrictions.

## Required Space secrets
- BOT_TOKEN
- WEBHOOK_SECRET
- PUBLIC_URL

## Optional variables
- BOT_USERNAME (default GEMSERIESTVBOT)
- SOURCE_CHANNEL (default GEM_SERIES_TJ)
- DATABASE_CHANNEL (default @database_gem_series)
- DESTINATION_CHANNEL (default @afsonaishab)
- CHANNEL_URL (default https://t.me/afsonaishab)
- SUBSCRIPTION_CHANNEL (default @tm_score)
- SUBSCRIPTION_URL (default https://t.me/tm_score)
- SPONSOR_BOT_URL (optional; default https://t.me/FF_MARGEDAR_BOT)
- INITIAL_SCAN_DATE (default 2026-11-09)
- POLL_SECONDS (default 180; minimum 60)
- DB_PATH (default /data/gem_series.sqlite3)

## Telegram permissions
- Bot must be admin in database and destination channels.
- Bot must be able to check members in the subscription channel; admin rights are recommended.
- Users must start the bot themselves before the bot can send them messages.

## Hosting
Free Hugging Face Spaces can sleep or restart. Polling is best-effort and is not guaranteed to run 24/7 on a sleeping instance. Persistent storage should be enabled if SQLite history must survive restarts.
