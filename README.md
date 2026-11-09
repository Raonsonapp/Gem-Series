---
title: GEM SERIES BOT
emoji: 🎬
colorFrom: purple
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# GEM SERIES

Automatic Telegram channel-post importer using the official Telegram Bot API. No Telethon user session is used.

## Automatic workflow
1. The bot receives each new post from the configured source channel.
2. It copies the original post (video, document, photo, or text) into the private database channel.
3. It copies that stored message into the public GEM SERIES channel.
4. The post includes a bot deep link. After the user joins `@tm_score`, the bot sends the stored media to the user.
5. The optional partner bot button is informational only and is not a required membership gate.

## Required Space secrets
- `BOT_TOKEN`
- `WEBHOOK_SECRET`
- `PUBLIC_URL` — full HTTPS URL of this Space, without a trailing slash.

## Channel configuration
Defaults:
- Source: `@GEM_SERIES_TJ`
- Database: `@database_gem_series`
- Publishing: `@afsonaishab`
- Membership check: `@tm_score`
- Optional partner: `@FF_MARGEDAR_BOT`

Optional environment variables:
- `BOT_USERNAME`
- `SOURCE_CHANNEL`
- `DATABASE_CHANNEL`
- `DESTINATION_CHANNEL`
- `CHANNEL_URL`
- `SUBSCRIPTION_CHANNEL`
- `SUBSCRIPTION_URL`
- `SPONSOR_BOT_URL` (set empty to hide the optional partner button)
- `DB_PATH` (default `/data/gem_series.sqlite3`)

## Telegram permissions — important
- The bot **must be an administrator in the source channel** to receive new channel posts through the Bot API.
- The bot must be an administrator in `@database_gem_series` and `@afsonaishab`, with permission to post messages.
- The bot must be able to check members of `@tm_score`; make it an administrator there too.
- If the source channel owner cannot add the bot as an administrator, this Bot API listener cannot read that channel. A user-account MTProto listener would then be needed; this build intentionally does not request or collect Telegram login codes or account sessions.
- The Bot API cannot backfill old source-channel history. It automatically handles new posts after the bot is added and the webhook is active.

## Hosting
The Space uses the included Dockerfile and listens on port 7860. Free hosting can sleep or restart; use persistent storage for the SQLite database and an always-on host if continuous 24/7 monitoring is required.
