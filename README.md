---
title: TG Bot
emoji: 🤖
colorFrom: blue
colorTo: purple
sdk: gradio
sdk_version: 4.44.0
app_file: app.py
pinned: false
---

# YouTube Video Downloader Telegram Bot

Deploy on **Render.com** (free) — no GPU needed, no ZeroGPU issues.

## Environment Variables (set in Render dashboard)

| Variable | Value |
|---|---|
| `BOT_TOKEN` | Telegram bot token |
| `MEGA_EMAIL` | MEGA account email |
| `MEGA_PASSWORD` | MEGA account password |
| `WEBHOOK_URL` | Your Render app URL e.g. `https://tg-bot.onrender.com` |

## Render.com Setup

1. Go to [render.com](https://render.com) → sign up free
2. New → **Web Service**
3. Connect GitHub repo (upload files there first)
4. Settings:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn app:app --host 0.0.0.0 --port $PORT`
   - **Instance type:** Free
5. Add environment variables (above table)
6. Deploy

## UptimeRobot

Ping `https://your-app.onrender.com/health` every 5 minutes to keep it awake.
