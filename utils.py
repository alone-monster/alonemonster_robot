"""
Shared utility functions for the Telegram Video Downloader Bot.
"""

import math
import re


def format_size(bytes_val: int | float | None) -> str:
    """Format bytes into a human-readable string."""
    if not bytes_val:
        return "0 B"
    bytes_val = float(bytes_val)
    if bytes_val < 1024:
        return f"{bytes_val:.0f} B"
    if bytes_val < 1_048_576:
        return f"{bytes_val / 1024:.1f} KB"
    if bytes_val < 1_073_741_824:
        return f"{bytes_val / 1_048_576:.1f} MB"
    if bytes_val < 1_099_511_627_776:
        return f"{bytes_val / 1_073_741_824:.1f} GB"
    return f"{bytes_val / 1_099_511_627_776:.1f} TB"


def format_number(num: int | None) -> str:
    """Format a large number into a short human-readable string."""
    if not num:
        return "0"
    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.1f}B".rstrip("0B").rstrip(".") + "B"
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M".rstrip("0M").rstrip(".") + "M"
    if num >= 1_000:
        return f"{num / 1_000:.1f}K".rstrip("0K").rstrip(".") + "K"
    return str(num)


def format_duration(total_seconds: int | None) -> str:
    """Format seconds into [HH:MM:SS] or [MM:SS]."""
    if not total_seconds:
        return "[00:00]"
    total_seconds = int(total_seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"[{hours}:{minutes:02d}:{seconds:02d}]"
    return f"[{minutes:02d}:{seconds:02d}]"


def format_hz(fhz: int | float | None) -> str:
    """Format a frequency in Hz to a human-readable string."""
    if not fhz:
        return "0 Hz"
    fhz = int(fhz)
    if fhz >= 1_000_000:
        return f"{fhz // 1_000_000} MHz"
    if fhz >= 1_000:
        return f"{fhz // 1_000} kHz"
    return f"{fhz} Hz"


def format_date(raw_date: str | int | None) -> str:
    """Parse an upload date string into a readable format."""
    if not raw_date:
        return ""
    try:
        from dateutil import parser as date_parser
        return date_parser.parse(str(raw_date)).strftime("%b %d, %Y")
    except Exception:
        return str(raw_date)


def make_progress_bar(percent: float, width: int = 10) -> str:
    """Generate a text-based progress bar."""
    filled = int(percent / 100 * width)
    empty = width - filled
    return "■" * filled + "□" * empty


def extract_youtube_url(text: str) -> str | None:
    """Extract a YouTube URL from a message string."""
    match = re.search(r"(https?://[^\s]+)", text)
    if match:
        url = match.group(1)
        if "youtube.com" in url or "youtu.be" in url:
            return url
    return None


def truncate_title(title: str, max_len: int = 60) -> str:
    """Truncate a long video title."""
    return title if len(title) <= max_len else title[:max_len] + "…"
