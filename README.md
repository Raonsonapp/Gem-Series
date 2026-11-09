---
title: GEM SERIES BOT
emoji: 🎬
colorFrom: purple
colorTo: blue
sdk: gradio
app_file: app.py
pinned: false
---

# GEM SERIES Automation

Automates an authorized Telegram media workflow:
1. Uses the configured Telegram user session to access the source channel.
2. On startup, scans the configured historical date and archives one video/document post as a one-item test.
3. Stores the forwarded post in the database channel and writes an archive marker to prevent repeating the historical import on restart.
4. Publishes a text banner with a deep link in the destination channel.
5. On button press, checks membership in the subscription channel and copies the archived message to the user.
6. Monitors new source-channel posts and repeats the authorized archive-and-banner workflow.

Only use this system for media you own or are explicitly authorized to redistribute. The application does not bypass Telegram protected-content restrictions.

## Required Space Secrets

- BOT_TOKEN
- TELEGRAM_API_ID
- TELEGRAM_API_HASH
- TELETHON_SESSION (authorized Telethon StringSession; never commit or share it)
- WEBHOOK_SECRET
- PUBLIC_URL

## Optional Space Variables

- BOT_USERNAME (default GEMSERIESTVBOT)
- SOURCE_CHANNEL (default @GEM_SERIES_TJ)
- DATABASE_CHANNEL (default @database_gem_series)
- DESTINATION_CHANNEL (default @afsonaishab)
- CHANNEL_URL (default https://t.me/afsonaishab)
- SUBSCRIPTION_CHANNEL (default @tm_score)
- SUBSCRIPTION_URL (default https://t.me/tm_score)
- SPONSOR_BOT_URL (optional button only; default @FF_MARGEDAR_BOT)
- INITIAL_SCAN_DATE (default 2026-11-09)
- LOG_LEVEL (default INFO)

## Permissions

- The Telegram user account represented by TELETHON_SESSION must be able to read the source channel and post/forward into the database channel.
- The bot must be administrator in the database channel (to copy posts to users) and destination channel (to publish banners).
- The bot must be able to check members in the subscription channel; make it an administrator there for reliable membership checks.
- Users must press Start themselves before the bot can message them.
- The sponsor bot is optional and is not a subscription requirement.

## Important runtime note

This Space uses a webhook for the bot and a Telethon user client for channel history/live updates. Free Hugging Face Spaces may sleep; uninterrupted monitoring requires an always-on host. The historical scan imports only one matching video/document post for the configured date. New source posts are monitored after startup.
