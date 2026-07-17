"""
Telegram bot message and callback handlers.

Registers all handlers on the provided bot instance.
"""

import logging
import os
import threading
import time
from pathlib import Path

import telebot
from telebot import types

import downloader
import mega_manager
from queue_manager import queue_manager
from utils import (
    format_date,
    format_duration,
    format_hz,
    format_number,
    format_size,
    make_progress_bar,
    truncate_title,
)

logger = logging.getLogger(__name__)

# ── In-memory state ──────────────────────────────────────────────────────────
# Keyed by message_id (the photo message shown after metadata fetch)
_msg_state: dict[int, dict] = {}
# cancel flags keyed by chat_id
_cancel_flags: dict[int, dict] = {}
# track whether a chat already has a running download
_active_downloads: dict[int, bool] = {}

# Shared back button / board
_ib_back = types.InlineKeyboardButton(text="❮❮ Back", callback_data="back_btn")
_inline_backboard = types.InlineKeyboardMarkup()
_inline_backboard.add(_ib_back)

# Rate-limit progress edits
_last_edit_time: dict[int, float] = {}
_EDIT_INTERVAL = 3  # seconds


def register_handlers(bot: telebot.TeleBot) -> None:
    """Attach all message and callback handlers to *bot*."""

    # ── YouTube link handler ──────────────────────────────────────────────
    @bot.message_handler(
        func=lambda m: m.text
        and ("youtube.com" in m.text or "youtu.be" in m.text)
    )
    def handle_youtube_link(message: types.Message) -> None:
        import re

        url_match = re.search(r"(https?://[^\s]+)", message.text)
        if not url_match:
            return
        url = url_match.group(1)

        fetch_msg = bot.reply_to(message, "◉ Fetching metadata…")
        try:
            info = _fetch_info(url)
        except Exception as exc:
            bot.edit_message_text(
                f"⚠️ Could not fetch video info:\n<code>{exc}</code>",
                message.chat.id,
                fetch_msg.message_id,
                parse_mode="HTML",
            )
            return

        _send_metadata_message(bot, message, info, url)
        try:
            bot.delete_message(message.chat.id, fetch_msg.message_id)
        except Exception:
            pass

    # ── Video resolution button ───────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("vid_"))
    def handle_video_resolution(call: types.CallbackQuery) -> None:
        chat_id = call.message.chat.id
        msg_id = call.message.message_id

        if _active_downloads.get(chat_id):
            bot.answer_callback_query(
                call.id,
                text="⚠️ A download is already running. Please wait.",
                show_alert=True,
            )
            return

        format_id = call.data[4:]  # strip "vid_"
        state = _msg_state.get(msg_id)
        if not state:
            bot.answer_callback_query(call.id, text="Session expired. Re-send the link.")
            return

        bot.answer_callback_query(call.id, text="⎙ Starting download…", show_alert=False)
        _cancel_flags[chat_id] = {"cancelled": False}
        _active_downloads[chat_id] = True

        def run():
            try:
                _run_video_download(bot, chat_id, msg_id, format_id, state)
            finally:
                _active_downloads[chat_id] = False

        queue_manager.submit(f"vid_{chat_id}_{msg_id}", run)

    # ── Audio bitrate button ──────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("aud_"))
    def handle_audio(call: types.CallbackQuery) -> None:
        chat_id = call.message.chat.id
        msg_id = call.message.message_id

        if _active_downloads.get(chat_id):
            bot.answer_callback_query(
                call.id,
                text="⚠️ A download is already running. Please wait.",
                show_alert=True,
            )
            return

        audio_format_id = call.data[4:]  # strip "aud_"
        state = _msg_state.get(msg_id)
        if not state:
            bot.answer_callback_query(call.id, text="Session expired. Re-send the link.")
            return

        bot.answer_callback_query(call.id, text="♬ Preparing audio…", show_alert=False)
        _cancel_flags[chat_id] = {"cancelled": False}
        _active_downloads[chat_id] = True

        def run():
            try:
                _run_audio_download(bot, chat_id, msg_id, audio_format_id, state)
            finally:
                _active_downloads[chat_id] = False

        queue_manager.submit(f"aud_{chat_id}_{msg_id}", run)

    # ── Cancel button ─────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "cncl_btn")
    def handle_cancel(call: types.CallbackQuery) -> None:
        chat_id = call.message.chat.id
        flag = _cancel_flags.get(chat_id)
        if flag:
            flag["cancelled"] = True
        bot.answer_callback_query(call.id, text="⌯ Cancelling…")

    # ── Back button ───────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "back_btn")
    def handle_back(call: types.CallbackQuery) -> None:
        msg_id = call.message.message_id
        state = _msg_state.get(msg_id)
        if not state:
            bot.answer_callback_query(call.id, text="Session expired.")
            return
        try:
            bot.edit_message_caption(
                chat_id=call.message.chat.id,
                message_id=msg_id,
                caption=state["caption"],
                parse_mode="HTML",
                reply_markup=state["inline"],
            )
        except Exception:
            pass
        bot.answer_callback_query(call.id)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _find_cookies() -> str | None:
    """Search for cookies.txt in the script dir, repo root, and CWD."""
    candidates = [
        Path(__file__).parent / "cookies.txt",
        Path(__file__).parent.parent / "cookies.txt",
        Path("cookies.txt").resolve(),
    ]
    for p in candidates:
        if p.exists():
            logger.info("Using cookies from: %s", p)
            return str(p)
    logger.warning("cookies.txt not found — proceeding without cookies")
    return None


def _fetch_info(url: str) -> dict:
    import yt_dlp

    opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "cookiefile": _find_cookies(),
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    opts = {k: v for k, v in opts.items() if v is not None}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _send_metadata_message(
    bot: telebot.TeleBot,
    message: types.Message,
    info: dict,
    url: str,
) -> None:
    """Build and send the metadata photo message with resolution/audio buttons."""
    duration_str = format_duration(info.get("duration"))
    views_str = format_number(info.get("view_count"))
    likes_str = format_number(info.get("like_count"))
    comments_str = format_number(info.get("comment_count"))
    subs_str = format_number(info.get("channel_follower_count"))
    date_str = format_date(info.get("upload_date") or info.get("release_date"))
    title = info.get("title", "Unknown")
    channel = info.get("channel", "")
    channel_url = info.get("channel_url", "")
    thumbnail_url = info.get("thumbnail", "")

    # --- Build resolution lists ---
    RESOLUTIONS = [
        "4320p", "2160p", "1440p", "1080p", "720p",
        "480p", "360p", "240p", "144p",
    ]
    RES_LABELS = {
        "4320p": "▹ 4320p [8K]", "2160p": "▹ 2160p [4K]",
        "1440p": "▹ 1440p [2K]", "1080p": "▹ 1080p [FHD]",
        "720p": "▹ 720p [HD]", "480p": "▹ 480p [SD]",
        "360p": "▹ 360p", "240p": "▹ 240p", "144p": "▹ 144p",
    }

    vid_res_dict: dict[str, dict] = {}
    for f in info.get("formats", []):
        if f.get("ext") != "mp4":
            continue
        note = f.get("format_note", "") or ""
        for res in RESOLUTIONS:
            if note.startswith(res):
                if res not in vid_res_dict:
                    vid_res_dict[res] = f
                break

    vid_text_lines = []
    for i, res in enumerate(r for r in RESOLUTIONS if r in vid_res_dict), 1:
        f = vid_res_dict[res]
        size = f.get("filesize") or f.get("filesize_approx")
        size_str = format_size(size) if size else "N/A"
        vid_text_lines.append(f"{i}. <i>mp4, {res}</i> <b>[{size_str}]</b>")

    # --- Build audio format list ---
    audio_fmt_dict: dict[str, dict] = {}
    for f in info.get("formats", []):
        if f.get("ext") != "m4a" or not f.get("abr"):
            continue
        key = str(int(f["abr"]))
        audio_fmt_dict[key] = f

    audio_text_lines = []
    offset = len(vid_res_dict) + 1
    for i, (abr_key, f) in enumerate(
        sorted(audio_fmt_dict.items(), key=lambda x: -float(x[0])), offset
    ):
        size = f.get("filesize") or f.get("filesize_approx")
        size_str = format_size(size) if size else "N/A"
        hz_str = format_hz(f.get("asr"))
        audio_text_lines.append(
            f"{i}. <i>m4a, {abr_key}kbps, {hz_str}</i> <b>[{size_str}]</b>"
        )

    all_res_text = "\n".join(vid_text_lines) or "No video formats found"
    all_audio_text = "\n".join(audio_text_lines) or "No audio formats found"

    caption = (
        f'<a href="{info.get("webpage_url", url)}">{title}</a>\n'
        f"{duration_str} · {views_str} views · {date_str} · "
        f"{likes_str} likes · {comments_str} comments · "
        f'<a href="{channel_url}">{channel}</a> {subs_str} subscribers\n\n'
        f"<b>▹ Video</b>\n{all_res_text}\n\n"
        f"<b>♬ Audio</b>\n{all_audio_text}"
    )

    # --- Build inline keyboard ---
    markup = types.InlineKeyboardMarkup()

    # Video resolution buttons (3 per row)
    available_vid_btns = []
    for res in RESOLUTIONS:
        if res in vid_res_dict:
            f_id = vid_res_dict[res].get("format_id", "bestvideo")
            available_vid_btns.append(
                types.InlineKeyboardButton(
                    text=RES_LABELS[res], callback_data=f"vid_{f_id}"
                )
            )
    for i in range(0, len(available_vid_btns), 3):
        markup.add(*available_vid_btns[i : i + 3])

    if not available_vid_btns:
        bot.reply_to(message, "⚠️ No downloadable video formats found.")
        return

    # Audio buttons (sorted by bitrate, highest first)
    sorted_audio = sorted(audio_fmt_dict.items(), key=lambda x: -float(x[0]))
    audio_btns = [
        types.InlineKeyboardButton(
            text=f"♬ {abr_key}kbps",
            callback_data=f"aud_{f.get('format_id', 'bestaudio')}",
        )
        for abr_key, f in sorted_audio
    ]
    if audio_btns:
        for i in range(0, len(audio_btns), 2):
            markup.add(*audio_btns[i : i + 2])

    # Send
    if thumbnail_url:
        sent = bot.send_photo(
            message.chat.id,
            thumbnail_url,
            caption=caption,
            parse_mode="HTML",
            reply_markup=markup,
        )
        _msg_state[sent.message_id] = {
            "inline": markup,
            "info": info,
            "url": url,
            "caption": caption,
        }
    else:
        bot.send_message(
            message.chat.id, caption, parse_mode="HTML", reply_markup=markup
        )


def _make_progress_caption(
    info: dict,
    url: str,
    stage: str,
    chunk_idx: int,
    total_chunks: int,
    chunk_pct: float,
    done_bytes: int,
    total_bytes: int,
) -> str:
    title = truncate_title(info.get("title", "video"))
    channel = info.get("channel", "")
    channel_url = info.get("channel_url", "")

    overall_pct = (done_bytes / total_bytes * 100) if total_bytes else 0
    bar = make_progress_bar(chunk_pct, width=12)
    overall_bar = make_progress_bar(overall_pct, width=12)

    return (
        f'<a href="{url}">{title}</a>\n\n'
        f"⎙ Processing Chunk {chunk_idx}/{total_chunks}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"▶ Stage: <b>{stage}</b>\n"
        f"[{bar}] {int(chunk_pct)}%\n\n"
        f"📦 <b>Total Progress:</b>\n"
        f"[{overall_bar}] {int(overall_pct)}%\n"
        f"Processed: <b>{format_size(done_bytes)}</b> / {format_size(total_bytes)}"
    )


def _run_video_download(
    bot: telebot.TeleBot,
    chat_id: int,
    msg_id: int,
    format_id: str,
    state: dict,
) -> None:
    info = state["info"]
    url = state["url"]
    cancel_flag = _cancel_flags.get(chat_id, {"cancelled": False})

    cancel_btn = types.InlineKeyboardButton(text="⌯ Cancel", callback_data="cncl_btn")
    progress_markup = types.InlineKeyboardMarkup()
    progress_markup.add(cancel_btn)

    last_edit = [0.0]

    def progress_cb(
        stage: str,
        chunk_idx: int,
        total_chunks: int,
        chunk_pct: float,
        done_bytes: int,
        total_bytes: int,
    ) -> None:
        now = time.time()
        if now - last_edit[0] < _EDIT_INTERVAL and chunk_pct not in (0.0, 100.0):
            return
        last_edit[0] = now
        new_cap = _make_progress_caption(
            info, url, stage, chunk_idx, total_chunks,
            chunk_pct, done_bytes, total_bytes,
        )
        try:
            bot.edit_message_caption(
                chat_id=chat_id,
                message_id=msg_id,
                caption=new_cap,
                parse_mode="HTML",
                reply_markup=progress_markup,
            )
        except Exception as e:
            logger.debug("Caption edit skipped: %s", e)

    mega_nodes: list[dict] = []
    final_path: Path | None = None

    try:
        final_path, mega_nodes = downloader.process_video(
            url, format_id, info, progress_cb, cancel_flag
        )

        if cancel_flag.get("cancelled"):
            raise InterruptedError("Cancelled")

        # Edit caption to "Sending…"
        try:
            bot.edit_message_caption(
                chat_id=chat_id,
                message_id=msg_id,
                caption=(
                    f'<a href="{url}">{truncate_title(info.get("title","video"))}</a>\n\n'
                    "📤 Sending video to Telegram…"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

        with open(final_path, "rb") as f:
            bot.send_video(
                chat_id,
                f,
                caption=f'<a href="{url}">{info.get("title","video")}</a>',
                parse_mode="HTML",
                supports_streaming=True,
            )

        # Restore original caption
        try:
            bot.edit_message_caption(
                chat_id=chat_id,
                message_id=msg_id,
                caption=state["caption"],
                parse_mode="HTML",
                reply_markup=state["inline"],
            )
        except Exception:
            pass

    except InterruptedError:
        try:
            bot.edit_message_caption(
                chat_id=chat_id,
                message_id=msg_id,
                caption="⌯⌲ Download cancelled.",
                reply_markup=_inline_backboard,
            )
        except Exception:
            pass

    except Exception as exc:
        logger.exception("Video download failed: %s", exc)
        try:
            bot.edit_message_caption(
                chat_id=chat_id,
                message_id=msg_id,
                caption=f"⚠️ Download failed:\n<code>{exc}</code>",
                parse_mode="HTML",
                reply_markup=_inline_backboard,
            )
        except Exception:
            pass

    finally:
        # Clean up local files
        if final_path and final_path.exists():
            try:
                import shutil
                shutil.rmtree(final_path.parent, ignore_errors=True)
            except Exception:
                pass

        # Clean up MEGA
        if mega_nodes:
            threading.Thread(
                target=mega_manager.delete_files,
                args=(mega_nodes,),
                daemon=True,
            ).start()


def _run_audio_download(
    bot: telebot.TeleBot,
    chat_id: int,
    msg_id: int,
    audio_format_id: str,
    state: dict,
) -> None:
    info = state["info"]
    url = state["url"]
    cancel_flag = _cancel_flags.get(chat_id, {"cancelled": False})

    def progress_cb(
        stage: str, chunk_idx: int, total_chunks: int,
        chunk_pct: float, done_bytes: int, total_bytes: int
    ) -> None:
        try:
            bot.edit_message_caption(
                chat_id=chat_id,
                message_id=msg_id,
                caption=(
                    f'<a href="{url}">{truncate_title(info.get("title","audio"))}</a>\n\n'
                    f"♬ {stage}…"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    audio_path: Path | None = None
    try:
        audio_path = downloader.process_audio(
            url, audio_format_id, info, progress_cb, cancel_flag
        )
        try:
            bot.edit_message_caption(
                chat_id=chat_id,
                message_id=msg_id,
                caption=(
                    f'<a href="{url}">{truncate_title(info.get("title","audio"))}</a>\n\n'
                    "📤 Sending audio…"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

        with open(audio_path, "rb") as f:
            bot.send_audio(
                chat_id,
                f,
                title=info.get("title", "audio"),
                performer=info.get("channel", ""),
                caption=f'<a href="{url}">{info.get("title","")}</a>',
                parse_mode="HTML",
            )

        try:
            bot.edit_message_caption(
                chat_id=chat_id,
                message_id=msg_id,
                caption=state["caption"],
                parse_mode="HTML",
                reply_markup=state["inline"],
            )
        except Exception:
            pass

    except Exception as exc:
        logger.exception("Audio download failed: %s", exc)
        try:
            bot.edit_message_caption(
                chat_id=chat_id,
                message_id=msg_id,
                caption=f"⚠️ Audio download failed:\n<code>{exc}</code>",
                parse_mode="HTML",
                reply_markup=_inline_backboard,
            )
        except Exception:
            pass

    finally:
        if audio_path and audio_path.exists():
            try:
                import shutil
                shutil.rmtree(audio_path.parent, ignore_errors=True)
            except Exception:
                pass
