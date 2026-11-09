---
title: GEM SERIES BOT
emoji: 🎬
colorFrom: purple
colorTo: blue
sdk: gradio
app_file: app.py
pinned: false
---

# GEM SERIES BOT

Telegram bot for the GEM SERIES channel. It provides a /start welcome flow and can publish new posts from explicitly authorized Telegram source channels.

## Rights and access requirements

- Add a source channel ID to AUTHORIZED_SOURCE_CHANNEL_IDS only when you own that channel or have explicit permission from its rights holder to republish its posts and media.
- Add the bot as an administrator to each authorized source channel so Telegram sends it new channel_post updates.
- Add the bot as an administrator to the destination channel with permission to post.
- The bot processes new posts after it is added; it does not fetch old channel history.
- Telegram users must press Start themselves before the bot can send them private messages.

## Hugging Face Space secrets

Set these under Space Settings → Variables and secrets → Secrets:

| Secret | Description |
|---|---|
| BOT_TOKEN | Token from BotFather |
| DESTINATION_CHANNEL_ID | Numeric ID of your destination channel |
| AUTHORIZED_SOURCE_CHANNEL_IDS | Comma-separated numeric IDs of channels you are authorized to republish from |
| PUBLIC_URL | Public Space URL, e.g. https://name.hf.space |
| WEBHOOK_SECRET | Long random secret used to validate Telegram webhooks |
| OWNER_IDS | Comma-separated Telegram user IDs allowed to use owner-only status commands |
| CHANNEL_URL | Public URL of the destination channel |
| BOT_USERNAME | Username of your own bot, without @ (default: GEMSERIESTVBOT) |

Optional variable: LOG_LEVEL (default INFO).

Do not put real tokens or passwords in GitHub files.

## Automatic GitHub deployment

Add HF_TOKEN and HF_SPACE_ID under GitHub repository Settings → Secrets and variables → Actions. HF_SPACE_ID must be your Hugging Face Space identifier in owner/name format. Every push to the main branch then deploys the repository to that Space.

## Deploy

This is a Gradio SDK Space. The Gradio interface is mounted with the Telegram webhook API. Once the Space is running and all secrets are set, it configures the Telegram webhook automatically.

ZeroGPU supports Gradio Spaces, and this bot does not need GPU processing. Free Spaces can sleep or stop, so webhook delivery is not guaranteed to be uninterrupted 24/7. Use always-on hosting for reliable continuous delivery.

## Commands

- /start — welcome message and channel buttons
- /help — bot information
- /status — owner-only service status

## Current behavior

- Handles new posts received from configured, authorized source channels.
- Copies media posts and preserves the original caption with a GEM SERIES header.
- Sends text-only posts as formatted text.
- Ignores channels not present in the authorized source allowlist.
