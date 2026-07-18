"""
Alone Monster YouTube Downloader Bot
-------------------------------------
Single-file Telegram bot that:
  - Fetches YouTube metadata and shows resolution/audio-bitrate buttons
  - Downloads video/audio in small time-sliced chunks (~38MB each) using
    yt-dlp's download_ranges, so the full file is NEVER fully downloaded
    to disk/RAM at once — this keeps Render's free-tier storage & RAM safe.
  - Sends each chunk straight to Telegram as soon as it's ready, then
    deletes it immediately. No MEGA / third-party storage involved.
  - Shows a live progress bar during each chunk's download.
  - Ships with a tiny FastAPI wrapper so it can run as a Render Web Service
    (Render needs an open HTTP port) while the bot itself polls Telegram
    in a background thread.

Deployment notes (Render):
  - requirements.txt should contain (at least):
        pyTelegramBotAPI>=4.14.0
        yt-dlp[default]
        python-dateutil>=2.8.2
        fastapi>=0.110.0
        uvicorn>=0.27.0
  - packages.txt: ffmpeg   (already pre-installed on Render, kept for safety)
  - Build Command:
        pip install -r requirements.txt && mkdir -p $HOME/nodejs && \
        curl -fsSL https://nodejs.org/dist/v20.18.1/node-v20.18.1-linux-x64.tar.xz \
        | tar -xJ -C $HOME/nodejs --strip-components=1
  - Start Command:
        export PATH="$HOME/nodejs/bin:$PATH" && uvicorn app:app --host 0.0.0.0 --port $PORT
  - Environment Variables:
        BOT_TOKEN = <your telegram bot token>
  - cookies.txt (optional but recommended) should sit next to this file.

Node.js + yt-dlp[default] are required for YouTube's "n challenge" (anti-bot)
solving — without them most formats get silently dropped by YouTube.
"""

import glob
import logging
import math
import os
import re
import threading
import time
from pathlib import Path

import telebot
import uvicorn
import yt_dlp
from dateutil import parser as date_parser
from fastapi import FastAPI
from telebot import types
from yt_dlp.utils import download_range_func

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

BOT_TOKEN = os.environ["BOT_TOKEN"]

BASE_DIR = Path(__file__).resolve().parent
COOKIES_FILE = BASE_DIR / "cookies.txt"
WORK_DIR = Path("/tmp/ytdl_work")
WORK_DIR.mkdir(parents=True, exist_ok=True)

CHUNK_TARGET_MB = 38          # stay safely under Telegram's 50MB bot limit
CHUNK_TARGET_BYTES = CHUNK_TARGET_MB * 1024 * 1024
MIN_CHUNK_SECONDS = 8         # never slice thinner than this
DEFAULT_AUDIO_KBPS_ESTIMATE = 128

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app")

bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=8)

# --------------------------------------------------------------------------
# In-memory state
# --------------------------------------------------------------------------

imp_dict = {}          # message_id -> {info, url, caption, inline, audio_map}
download_cancel = {}   # chat_id -> bool
active_downloads = {}  # chat_id -> bool
_last_edit_time = {}   # chat_id -> float (throttle progress edits)

# --------------------------------------------------------------------------
# yt-dlp shared options
# --------------------------------------------------------------------------


def _base_ydl_opts() -> dict:
    """Options shared by every yt-dlp call (metadata + downloads)."""
    opts = {
        "cookiefile": str(COOKIES_FILE) if COOKIES_FILE.exists() else None,
        "js_runtimes": {"node": {}},
        "extractor_args": {
            "youtube": {
                "player_client": ["tv", "web"],
                "formats": ["missing_pot"],
            }
        },
        "quiet": False,
        "no_warnings": False,
        "verbose": True,
    }
    return {k: v for k, v in opts.items() if v is not None}


# --------------------------------------------------------------------------
# Formatting helpers (kept identical to the original bot's output style)
# --------------------------------------------------------------------------


def format_number(num):
    if not num:
        return "0"
    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.1f}B".replace(".0B", "B")
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M".replace(".0M", "M")
    if num >= 1_000:
        return f"{num / 1_000:.1f}K".replace(".0K", "K")
    return str(num)


def size_format(num_bytes):
    if not num_bytes:
        return "0"
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes >= 1_099_511_627_776:
        return f"{num_bytes / 1099511627776:.1f} TB".replace(".0 TB", " TB")
    if num_bytes >= 1_073_741_824:
        return f"{num_bytes / 1073741824:.1f} GB".replace(".0 GB", " GB")
    if num_bytes >= 1_048_576:
        return f"{num_bytes / 1048576:.1f} MB".replace(".0 MB", " MB")
    if num_bytes >= 1_024:
        return f"{num_bytes / 1024:.1f} KB".replace(".0 KB", " KB")
    return f"{num_bytes} B"


def hz_format(fhz):
    if not fhz:
        return "0"
    if fhz < 1000:
        return f"{fhz}Hz"
    if fhz >= 1_000_000:
        return f"{fhz // 1000000}MHz"
    return f"{fhz // 1000}kHz"


def bit_format(bit):
    return f"{bit // 1}".replace(".0", "")


def duration_format(total_seconds):
    total_seconds = int(total_seconds or 0)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"[{hours}:{minutes:02d}:{seconds:02d}]"
    return f"[{minutes:02d}:{seconds:02d}]"


def date_format(info):
    raw_date = info.get("upload_date") or info.get("release_date") or info.get("timestamp")
    if not raw_date:
        return ""
    try:
        return date_parser.parse(str(raw_date)).strftime("%b %d, %Y")
    except Exception:
        return str(raw_date)


def safe_filename(title: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", title)[:100] or "video"


def _cleanup_stale_files():
    for f in glob.glob(str(WORK_DIR / "*")):
        try:
            os.remove(f)
        except Exception:
            pass


# --------------------------------------------------------------------------
# /start & metadata fetch
# --------------------------------------------------------------------------


@bot.message_handler(commands=["start"])
def handle_start(message):
    bot.reply_to(
        message,
        "👋 Bhejo koi bhi YouTube link, main video/audio download options dikhaunga.",
    )


@bot.message_handler(
    func=lambda message: message.text
    and ("youtube.com" in message.text or "youtu.be" in message.text)
)
def handle_youtube_link(message):
    fetch = bot.reply_to(message, "◉ Fetching MetaData...")

    url_match = re.search(r"(https?://[^\s]+)", message.text)
    if not url_match:
        bot.edit_message_text("⚠️ Valid YouTube link nahi mila.", message.chat.id, fetch.message_id)
        return
    url = url_match.group(1)

    try:
        with yt_dlp.YoutubeDL({**_base_ydl_opts(), "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        bot.edit_message_text(f"⚠️ Metadata fetch failed: {e}", message.chat.id, fetch.message_id)
        return

    duration_formatted = duration_format(info.get("duration", 0))
    views_formatted = format_number(info.get("view_count", 0))
    likes_formatted = format_number(info.get("like_count", 0))
    comment_formatted = format_number(info.get("comment_count", 0))
    subscriber_formatted = format_number(info.get("channel_follower_count", 0))
    date_formatted = date_format(info)
    thumbnail_url = info.get("thumbnail")

    # ---- Video resolutions ----
    vid_res_dict = {}
    for f in info.get("formats", []):
        if f.get("ext") == "mp4":
            note = f.get("format_note", "") or ""
            resolution = next(
                (r for r in ("4320p", "2160p", "1440p", "1080p", "720p", "480p", "360p", "240p", "144p") if r in note),
                note,
            )
            if resolution:
                vid_res_dict[resolution] = f

    vid_res_lines = []
    for i, (res, f) in enumerate(reversed(list(vid_res_dict.items())), start=1):
        filesize = f.get("filesize") or f.get("filesize_approx")
        vid_res_lines.append(
            f"{i}. <i>{f.get('ext')}, {res}</i> <b>[{size_format(filesize) if filesize else 'N/A'}]</b>"
        )
    all_res_text = "\n".join(vid_res_lines) if vid_res_lines else "No resolutions found"

    # ---- Audio bitrates (keep matching format objects this time!) ----
    audio_formats = [a for a in info.get("formats", []) if a.get("ext") == "m4a" and a.get("asr")]
    audio_hz_dict = {}
    for a in audio_formats:
        audio_hz_dict[a["asr"]] = a  # last one wins per Hz, matches original behaviour

    audio_hz_lines = []
    audio_items = list(reversed(list(audio_hz_dict.items())))
    for i, (hz, a) in enumerate(audio_items, start=len(vid_res_dict) + 1):
        filesize = a.get("filesize") or a.get("filesize_approx")
        bit_rate = a.get("abr")
        bit_str = bit_format(bit_rate) if bit_rate else "N/A"
        audio_hz_lines.append(
            f"{i}. <i>{a.get('ext')}, {bit_str}kbps, {hz_format(hz)}</i> <b>[{size_format(filesize) if filesize else 'N/A'}]</b>"
        )
    all_hz_text = "\n".join(audio_hz_lines) if audio_hz_lines else "No Audio Files Found"

    # ---- Video resolution buttons ----
    res_labels = {
        "4320p": "▹ 4320p [8K]", "2160p": "▹ 2160p [4K]", "1440p": "▹ 1440p [2K]",
        "1080p": "▹ 1080p [FHD]", "720p": "▹ 720p [HD]", "480p": "▹ 480p [SD]",
        "360p": "▹ 360p", "240p": "▹ 240p", "144p": "▹ 144p",
    }
    available_buttons = []
    for res, label in res_labels.items():
        f = vid_res_dict.get(res)
        if f:
            available_buttons.append(
                types.InlineKeyboardButton(text=label, callback_data=f"id{f.get('format_id')}")
            )

    if not available_buttons:
        bot.reply_to(message, "Video cannot be downloaded ⚠️")
        return

    inline_resboard = types.InlineKeyboardMarkup()
    for i in range(0, len(available_buttons), 3):
        inline_resboard.add(*available_buttons[i:i + 3])

    # ---- Audio buttons (now correctly mapped to real format_ids) ----
    audio_map = {}
    if len(audio_items) >= 1:
        top_hz, top_fmt = audio_items[0]
        label = f"♬ {bit_format(top_fmt.get('abr'))}kbps"
        btn = types.InlineKeyboardButton(text=label, callback_data="aud_0")
        audio_map["aud_0"] = top_fmt.get("format_id")
        row = [btn]
        if len(audio_items) >= 2:
            second_hz, second_fmt = audio_items[1]
            label2 = f"♬ {bit_format(second_fmt.get('abr'))}kbps"
            btn2 = types.InlineKeyboardButton(text=label2, callback_data="aud_1")
            audio_map["aud_1"] = second_fmt.get("format_id")
            row.append(btn2)
        inline_resboard.add(*row)

    caption = f""" <a href="{info['webpage_url']}">{info['title']}</a>
{duration_formatted} · {views_formatted} views · {date_formatted} · {likes_formatted} likes · {comment_formatted} comments · <a href="{info['channel_url']}">{info['channel']}</a> {subscriber_formatted} subscribers

<b>▹ Video</b>
{all_res_text}

<b> ♬ Audio</b>
{all_hz_text}"""

    if thumbnail_url:
        sent = bot.send_photo(
            message.chat.id, thumbnail_url, caption=caption, parse_mode="HTML", reply_markup=inline_resboard
        )
        imp_dict[sent.message_id] = {
            "inline": inline_resboard,
            "info": info,
            "url": url,
            "caption": caption,
            "audio_map": audio_map,
        }
    else:
        bot.send_message(message.chat.id, caption, parse_mode="HTML")

    bot.delete_message(message.chat.id, fetch.message_id)


# --------------------------------------------------------------------------
# Chunk-size estimation
# --------------------------------------------------------------------------


def _estimate_chunk_seconds(duration, video_fmt=None, audio_fmt=None):
    """Rough time-slice length so each resulting chunk lands near CHUNK_TARGET_MB."""
    duration = duration or 0
    if duration <= 0:
        return None  # can't slice an unknown-length stream; download whole thing

    video_bps = 0
    if video_fmt:
        vbytes = video_fmt.get("filesize") or video_fmt.get("filesize_approx")
        if vbytes:
            video_bps = vbytes / duration
        else:
            tbr = video_fmt.get("tbr") or 1000  # kbps guess
            video_bps = (tbr * 1000) / 8

    audio_bps = 0
    if audio_fmt:
        abytes = audio_fmt.get("filesize") or audio_fmt.get("filesize_approx")
        if abytes:
            audio_bps = abytes / duration
        else:
            abr = audio_fmt.get("abr") or DEFAULT_AUDIO_KBPS_ESTIMATE
            audio_bps = (abr * 1000) / 8
    elif video_fmt:
        # video downloads always pull +bestaudio too
        audio_bps = (DEFAULT_AUDIO_KBPS_ESTIMATE * 1000) / 8

    total_bps = video_bps + audio_bps
    if total_bps <= 0:
        return duration

    chunk_seconds = CHUNK_TARGET_BYTES / total_bps
    chunk_seconds = max(MIN_CHUNK_SECONDS, chunk_seconds)
    return min(chunk_seconds, duration)


# --------------------------------------------------------------------------
# Progress bar (shared by video & audio downloads)
# --------------------------------------------------------------------------


def _make_progress_hook(chat_id, message_id, base_caption, chunk_index, total_chunks):
    def hook(d):
        if d["status"] != "downloading":
            return
        if download_cancel.get(chat_id):
            raise yt_dlp.utils.DownloadError("Cancelled by user")

        done_bytes = d.get("downloaded_bytes", 0)
        total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
        if total_bytes <= 0:
            return

        chunk_percent = (done_bytes / total_bytes) * 100
        overall_percent = ((chunk_index - 1) + chunk_percent / 100) / total_chunks * 100

        filled = int(chunk_percent // 10)
        bar = "■" * filled + "□" * (10 - filled)
        done_str = size_format(done_bytes)
        total_str = size_format(total_bytes)

        now = time.time()
        if chunk_percent == 0 or int(chunk_percent) == 100 or now - _last_edit_time.get(chat_id, 0) >= 3:
            new_caption = (
                f"{base_caption}\n\n"
                f"◉ Downloading Part {chunk_index}/{total_chunks}...\n"
                f"[{bar}] {int(chunk_percent)}% [{done_str}/{total_str}]  ·  Overall: {int(overall_percent)}%"
            )
            cancel_key = types.InlineKeyboardButton(text="Cancel", callback_data="cncl_btn")
            board = types.InlineKeyboardMarkup()
            board.add(cancel_key)
            try:
                bot.edit_message_caption(
                    chat_id=chat_id, message_id=message_id, caption=new_caption,
                    parse_mode="HTML", reply_markup=board,
                )
                _last_edit_time[chat_id] = now
            except Exception as e:
                logger.warning("Progress edit failed: %s", e)

    return hook


# --------------------------------------------------------------------------
# Core: chunked download + immediate Telegram send
# --------------------------------------------------------------------------


def _download_and_send(chat_id, message_id, url, base_caption, ydl_format, title,
                        duration, video_fmt, audio_fmt, kind):
    """
    kind: 'video' or 'audio'
    Downloads in time-sliced chunks, sends each straight to Telegram, deletes
    it, then moves to the next slice — the full file never sits on disk.
    """
    _cleanup_stale_files()
    session_prefix = f"{safe_filename(title)}_{chat_id}_{int(time.time())}"

    chunk_seconds = _estimate_chunk_seconds(duration, video_fmt, audio_fmt)
    if chunk_seconds is None:
        num_chunks = 1
        ranges = [(0, None)]
    else:
        num_chunks = max(1, math.ceil(duration / chunk_seconds))
        ranges = []
        for i in range(num_chunks):
            start = i * chunk_seconds
            end = duration if i == num_chunks - 1 else min((i + 1) * chunk_seconds, duration)
            ranges.append((start, end))

    for idx, (start, end) in enumerate(ranges, start=1):
        if download_cancel.get(chat_id):
            break

        outtmpl = str(WORK_DIR / f"{session_prefix}_part{idx:03d}.%(ext)s")
        opts = {
            **_base_ydl_opts(),
            "format": ydl_format,
            "outtmpl": outtmpl,
            "progress_hooks": [_make_progress_hook(chat_id, message_id, base_caption, idx, num_chunks)],
        }
        if kind == "video":
            opts["merge_output_format"] = "mp4"
        if end is not None:
            opts["download_ranges"] = download_range_func(None, [(start, end)])
            if kind == "video":
                opts["force_keyframes_at_cuts"] = True

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except Exception as e:
            if "Cancelled" in str(e):
                break
            bot.send_message(chat_id, f"⚠️ Chunk {idx}/{num_chunks} download failed: {e}")
            continue

        matches = glob.glob(str(WORK_DIR / f"{session_prefix}_part{idx:03d}.*"))
        matches = [m for m in matches if not m.endswith(".part")]
        if not matches:
            bot.send_message(chat_id, f"⚠️ Chunk {idx}/{num_chunks} produced no file, skipping.")
            continue
        chunk_path = matches[0]

        try:
            with open(chunk_path, "rb") as f:
                part_label = f" (Part {idx}/{num_chunks})" if num_chunks > 1 else ""
                if kind == "video":
                    bot.send_video(chat_id, f, caption=f"{title}{part_label}", supports_streaming=True, timeout=120)
                else:
                    bot.send_audio(chat_id, f, caption=f"{title}{part_label}", timeout=120)
        except Exception as e:
            bot.send_message(chat_id, f"⚠️ Sending part {idx}/{num_chunks} failed: {e}")
        finally:
            try:
                os.remove(chunk_path)
            except Exception:
                pass

    try:
        if download_cancel.get(chat_id):
            bot.edit_message_caption(
                chat_id=chat_id, message_id=message_id,
                caption=f"{base_caption}\n\n⌯⌲ Download Cancelled Successfully!",
                parse_mode="HTML",
            )
        else:
            bot.edit_message_caption(
                chat_id=chat_id, message_id=message_id,
                caption=f"{base_caption}\n\n✅ Done! Sent {num_chunks} part(s).",
                parse_mode="HTML",
            )
    except Exception:
        pass


# --------------------------------------------------------------------------
# Callback: video resolution chosen
# --------------------------------------------------------------------------


@bot.callback_query_handler(func=lambda call: call.data.startswith("id"))
def call_handle(call):
    chat_id = call.message.chat.id
    format_id = call.data.replace("id", "")

    if active_downloads.get(chat_id):
        bot.answer_callback_query(call.id, text="⚠️ A download is already in progress", show_alert=True)
        return

    entry = imp_dict.get(call.message.message_id)
    if not entry:
        bot.answer_callback_query(call.id, text="⚠️ Session expired, send the link again.", show_alert=True)
        return

    bot.answer_callback_query(call.id, text="⎙ Starting Download...[Please Wait]")

    info = entry["info"]
    url = entry["url"]
    base_caption = entry["caption"]
    title = info.get("title", "video")
    duration = info.get("duration", 0)

    video_fmt = next(
        (f for f in info.get("formats", []) if f.get("format_id") == format_id), None
    )
    ydl_format = f"{format_id}+bestaudio/best"

    active_downloads[chat_id] = True
    download_cancel[chat_id] = False

    def run():
        try:
            _download_and_send(
                chat_id, call.message.message_id, url, base_caption, ydl_format,
                title, duration, video_fmt, None, "video",
            )
        finally:
            active_downloads[chat_id] = False

    threading.Thread(target=run, daemon=True).start()


# --------------------------------------------------------------------------
# Callback: audio bitrate chosen
# --------------------------------------------------------------------------


@bot.callback_query_handler(func=lambda call: call.data in ("aud_0", "aud_1"))
def audio_call_handle(call):
    chat_id = call.message.chat.id

    if active_downloads.get(chat_id):
        bot.answer_callback_query(call.id, text="⚠️ A download is already in progress", show_alert=True)
        return

    entry = imp_dict.get(call.message.message_id)
    if not entry:
        bot.answer_callback_query(call.id, text="⚠️ Session expired, send the link again.", show_alert=True)
        return

    audio_format_id = entry["audio_map"].get(call.data)
    if not audio_format_id:
        bot.answer_callback_query(call.id, text="⚠️ Audio option not found.", show_alert=True)
        return

    bot.answer_callback_query(call.id, text="⎙ Starting Audio Download...[Please Wait]")

    info = entry["info"]
    url = entry["url"]
    base_caption = entry["caption"]
    title = info.get("title", "audio")
    duration = info.get("duration", 0)

    audio_fmt = next(
        (f for f in info.get("formats", []) if f.get("format_id") == audio_format_id), None
    )

    active_downloads[chat_id] = True
    download_cancel[chat_id] = False

    def run():
        try:
            _download_and_send(
                chat_id, call.message.message_id, url, base_caption, audio_format_id,
                title, duration, None, audio_fmt, "audio",
            )
        finally:
            active_downloads[chat_id] = False

    threading.Thread(target=run, daemon=True).start()


# --------------------------------------------------------------------------
# Cancel / Back
# --------------------------------------------------------------------------


@bot.callback_query_handler(func=lambda call: call.data == "cncl_btn")
def cancel_argument(call):
    download_cancel[call.message.chat.id] = True
    bot.answer_callback_query(call.id, text="Cancelling...")


@bot.callback_query_handler(func=lambda call: call.data == "back_btn")
def back_argument(call):
    entry = imp_dict.get(call.message.message_id)
    if not entry:
        return
    bot.edit_message_caption(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        caption=entry["caption"],
        parse_mode="HTML",
        reply_markup=entry["inline"],
    )


# --------------------------------------------------------------------------
# Render deployment wrapper — FastAPI (open port) + polling in background
# --------------------------------------------------------------------------

app = FastAPI()


@app.api_route("/", methods=["GET", "HEAD"])
def health():
    return {"status": "ok"}


def _run_polling():
    while True:
        try:
            logger.info("Starting bot in polling mode…")
            bot.infinity_polling(long_polling_timeout=15, timeout=20)
        except Exception as e:
            logger.error("Polling error: %s — reconnecting in 10s", e)
            time.sleep(10)


@app.on_event("startup")
def _startup():
    threading.Thread(target=_run_polling, daemon=True, name="bot-polling").start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
