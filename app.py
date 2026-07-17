"""
Main entry point — FastAPI + Telegram bot (no Gradio, Render.com compatible).

Endpoints:
  GET  /health  → UptimeRobot health check
  POST /webhook → Telegram webhook (when WEBHOOK_URL is set)

Bot mode:
  - WEBHOOK_URL set → webhook mode (recommended for Render)
  - WEBHOOK_URL not set → polling mode (local testing)
"""

import logging
import os
import sys
import threading
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

import telebot
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise EnvironmentError("BOT_TOKEN environment variable is not set.")

bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=8)

import handlers
handlers.register_handlers(bot)

from queue_manager import queue_manager
queue_manager.start()

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").rstrip("/")
USE_WEBHOOK = bool(WEBHOOK_URL)

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI()


@app.get("/health")
async def health_check():
    """UptimeRobot / Render health-check endpoint."""
    return JSONResponse({"status": "ok", "bot": "running"})


@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Receive Telegram updates via webhook."""
    try:
        data = await request.json()
        update = telebot.types.Update.de_json(data)
        bot.process_new_updates([update])
    except Exception as exc:
        logger.error("Webhook error: %s", exc)
    return JSONResponse({"ok": True})


@app.get("/", response_class=HTMLResponse)
async def home():
    import psutil
    try:
        ram_gb = psutil.virtual_memory().used / 1024**3
        total_gb = psutil.virtual_memory().total / 1024**3
        ram_info = f"{ram_gb:.1f} GB / {total_gb:.1f} GB"
    except Exception:
        ram_info = "N/A"

    mode = "Webhook" if USE_WEBHOOK else "Polling"
    active = queue_manager.active_count
    queued = queue_manager.queue_size

    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>YT Downloader Bot</title>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
      body {{ font-family: sans-serif; max-width: 500px; margin: 40px auto; padding: 20px; }}
      .badge {{ display: inline-block; padding: 4px 10px; border-radius: 20px;
                background: #22c55e; color: white; font-weight: bold; }}
      table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
      td {{ padding: 10px 0; border-bottom: 1px solid #eee; }}
      td:first-child {{ color: #888; }}
    </style>
    </head>
    <body>
    <h2>🤖 YT Video Downloader Bot</h2>
    <span class="badge">● Running</span>
    <table>
      <tr><td>Mode</td><td>{mode}</td></tr>
      <tr><td>Active tasks</td><td>{active}</td></tr>
      <tr><td>Queued tasks</td><td>{queued}</td></tr>
      <tr><td>RAM usage</td><td>{ram_info}</td></tr>
    </table>
    <p style="color:#888;font-size:13px;margin-top:30px">
      Send any YouTube link to the bot on Telegram.<br>
      Health check: <code>/health</code>
    </p>
    </body>
    </html>
    """


# ── Bot runner ────────────────────────────────────────────────────────────────

def _register_webhook() -> None:
    webhook_url = f"{WEBHOOK_URL}/webhook"
    for attempt in range(1, 6):
        try:
            bot.remove_webhook()
            time.sleep(1)
            bot.set_webhook(url=webhook_url)
            logger.info("Webhook registered: %s", webhook_url)
            return
        except Exception as exc:
            logger.warning("Webhook attempt %d failed: %s", attempt, exc)
            time.sleep(5 * attempt)
    logger.error("Could not register webhook after 5 attempts.")


def _run_polling() -> None:
    logger.info("Starting bot in polling mode…")
    while True:
        try:
            bot.infinity_polling(
                timeout=20,
                long_polling_timeout=15,
                allowed_updates=["message", "callback_query"],
                skip_pending=True,
            )
        except Exception as exc:
            logger.error("Polling error: %s — reconnecting in 10s", exc)
            time.sleep(10)


def start_bot() -> None:
    if USE_WEBHOOK:
        threading.Thread(target=_register_webhook, daemon=True, name="webhook-reg").start()
    else:
        threading.Thread(target=_run_polling, daemon=True, name="bot-polling").start()


start_bot()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    logger.info("Starting on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
