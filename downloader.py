"""
Chunk-based download, merge, MEGA upload, and final reconstruction.

Workflow per request:
  1. Receive video format_id + URL + video info.
  2. Calculate chunk boundaries (target 7 MB each).
  3. For each chunk:
       a. Download video segment (yt-dlp download_ranges).
       b. Download best-audio segment (same time range).
       c. Merge with FFmpeg.
       d. Upload merged chunk to MEGA.
       e. Delete local temp files.
  4. Download all MEGA chunks back to a temp dir.
  5. Concatenate all chunks with FFmpeg.
  6. Return final file path + MEGA node list for cleanup.
"""

import logging
import math
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

import yt_dlp
from yt_dlp.utils import download_range_func

import mega_manager
from utils import format_size, make_progress_bar, truncate_title

logger = logging.getLogger(__name__)

# Target chunk size in bytes (7 MB midpoint of 5–10 MB range)
TARGET_CHUNK_BYTES = 7 * 1024 * 1024


def _find_cookies() -> str | None:
    """
    Search for cookies.txt in common locations:
      1. Same directory as this script (telegram-bot/)
      2. Parent directory (repo root, for when Render root dir = telegram-bot/)
      3. Current working directory
    Returns the absolute path string, or None if not found.
    """
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


# ---------------------------------------------------------------------------
# Progress callback type:
#   progress_cb(stage, chunk_idx, total_chunks, chunk_pct,
#               done_bytes, total_bytes)
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[str, int, int, float, int, int], None]


def _run_ffmpeg(*args: str) -> None:
    """Run an FFmpeg command, raising RuntimeError on failure."""
    cmd = ["ffmpeg", "-y", *args]
    logger.debug("FFmpeg: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed:\n{result.stderr[-2000:]}")


def _download_segment(
    url: str,
    format_id: str,
    start: float,
    end: float,
    output_path: str,
    is_audio: bool = False,
) -> None:
    """
    Download a time-range segment using yt-dlp.
    Uses download_ranges for precise segment extraction.
    """
    if is_audio:
        fmt = "bestaudio[ext=m4a]/bestaudio"
        ext_args = {
            "postprocessors": [],
        }
    else:
        # format_id is now a height string e.g. "1080"
        # Use flexible height-based selector so YouTube CDN quirks don't break it
        h = int(format_id) if format_id.isdigit() else 1080
        fmt = (
            f"bestvideo[height<={h}][ext=mp4]"
            f"/bestvideo[ext=mp4]"
        )
        ext_args = {}

    opts: dict = {
        "format": fmt,
        "outtmpl": output_path,
        "quiet": True,
        "no_warnings": True,
        "download_ranges": download_range_func(None, [(start, end)]),
        "force_keyframes_at_cuts": True,
        "cookiefile": _find_cookies(),
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
        **ext_args,
    }
    # Remove None values
    opts = {k: v for k, v in opts.items() if v is not None}

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


def _calculate_chunks(
    info: dict, format_id: str
) -> list[tuple[float, float, int]]:
    """
    Divide the video into approximately TARGET_CHUNK_BYTES segments.

    Returns a list of (start_sec, end_sec, approx_bytes) tuples.
    format_id is now a height string e.g. "1080".
    """
    duration: float = float(info.get("duration") or 0)
    if duration <= 0:
        raise ValueError("Video duration is unknown; cannot chunk.")

    # Find the best video-only format at or below the requested height
    h = int(format_id) if format_id.isdigit() else 1080
    vid_format = next(
        (
            f for f in sorted(
                info.get("formats", []),
                key=lambda f: f.get("height") or 0,
                reverse=True,
            )
            if (f.get("height") or 0) <= h
            and f.get("vcodec") not in (None, "none")
            and f.get("acodec") in (None, "none")  # video-only
        ),
        None,
    )
    vid_size = (
        vid_format.get("filesize") or vid_format.get("filesize_approx") or 0
        if vid_format
        else 0
    )

    # Best audio size
    audio_format = _best_audio_format(info)
    audio_size = (
        audio_format.get("filesize") or audio_format.get("filesize_approx") or 0
        if audio_format
        else 0
    )

    total_size = (vid_size + audio_size) or (50 * 1024 * 1024)  # fallback 50 MB

    n_chunks = max(1, math.ceil(total_size / TARGET_CHUNK_BYTES))
    chunk_duration = duration / n_chunks
    bytes_per_chunk = total_size / n_chunks

    chunks: list[tuple[float, float, int]] = []
    for i in range(n_chunks):
        start = i * chunk_duration
        end = min((i + 1) * chunk_duration, duration)
        chunks.append((start, end, int(bytes_per_chunk)))

    logger.info(
        "Split into %d chunks × %.1fs each (~%s/chunk)",
        n_chunks,
        chunk_duration,
        format_size(bytes_per_chunk),
    )
    return chunks


def _best_audio_format(info: dict) -> dict | None:
    """Return the highest-quality m4a audio format from info."""
    audio_formats = [
        f
        for f in info.get("formats", [])
        if f.get("ext") == "m4a" and f.get("abr")
    ]
    if not audio_formats:
        return None
    return max(audio_formats, key=lambda f: f.get("abr") or 0)


def _total_video_size(info: dict, format_id: str) -> int:
    """Approximate total output file size (video + audio).
    format_id is a height string e.g. '1080'."""
    h = int(format_id) if format_id.isdigit() else 1080
    vid = next(
        (
            f for f in sorted(
                info.get("formats", []),
                key=lambda f: f.get("height") or 0,
                reverse=True,
            )
            if (f.get("height") or 0) <= h
            and f.get("vcodec") not in (None, "none")
            and f.get("acodec") in (None, "none")  # video-only
        ),
        None,
    )
    aud = _best_audio_format(info)
    vid_bytes = (vid.get("filesize") or vid.get("filesize_approx") or 0) if vid else 0
    aud_bytes = (aud.get("filesize") or aud.get("filesize_approx") or 0) if aud else 0
    return vid_bytes + aud_bytes or 50 * 1024 * 1024


def process_video(
    url: str,
    format_id: str,
    info: dict,
    progress_cb: ProgressCallback,
    cancel_flag: dict,
) -> tuple[Path, list[dict]]:
    """
    Full chunk-based pipeline.

    Returns:
        (final_video_path, mega_nodes_to_cleanup)
    Caller is responsible for deleting local temp files and MEGA nodes.
    """
    title = truncate_title(info.get("title", "video"))
    total_size = _total_video_size(info, format_id)
    chunks = _calculate_chunks(info, format_id)
    n_chunks = len(chunks)
    bytes_per_chunk = total_size / n_chunks

    mega_nodes: list[dict] = []
    work_dir = Path(tempfile.mkdtemp(prefix="ytdl_"))
    logger.info("Working directory: %s", work_dir)

    try:
        # ------------------------------------------------------------------
        # Phase 1: Download → Merge → Upload each chunk
        # ------------------------------------------------------------------
        for idx, (start, end, _) in enumerate(chunks, start=1):
            if cancel_flag.get("cancelled"):
                raise InterruptedError("Cancelled by user")

            chunk_label = f"Chunk {idx}/{n_chunks}"

            # --- Download video segment ---
            progress_cb("Downloading video segment", idx, n_chunks, 0.0, 0, total_size)
            vid_path = str(work_dir / f"chunk_{idx:04d}_vid.%(ext)s")
            _download_segment(url, format_id, start, end, vid_path, is_audio=False)

            if cancel_flag.get("cancelled"):
                raise InterruptedError("Cancelled by user")

            # Resolve actual filename (yt-dlp fills in %(ext)s)
            vid_files = sorted(work_dir.glob(f"chunk_{idx:04d}_vid.*"))
            if not vid_files:
                raise FileNotFoundError(f"Video chunk {idx} was not downloaded.")
            vid_file = vid_files[0]

            # --- Download audio segment ---
            progress_cb("Downloading audio segment", idx, n_chunks, 33.0, 0, total_size)
            aud_path = str(work_dir / f"chunk_{idx:04d}_aud.%(ext)s")
            _download_segment(url, format_id, start, end, aud_path, is_audio=True)

            if cancel_flag.get("cancelled"):
                raise InterruptedError("Cancelled by user")

            aud_files = sorted(work_dir.glob(f"chunk_{idx:04d}_aud.*"))
            if not aud_files:
                raise FileNotFoundError(f"Audio chunk {idx} was not downloaded.")
            aud_file = aud_files[0]

            # --- Merge video + audio ---
            progress_cb("Merging video + audio", idx, n_chunks, 60.0, 0, total_size)
            merged_path = work_dir / f"chunk_{idx:04d}_merged.mp4"
            _run_ffmpeg(
                "-i", str(vid_file),
                "-i", str(aud_file),
                "-c:v", "copy",
                "-c:a", "aac",
                "-strict", "experimental",
                str(merged_path),
            )

            # Remove raw segment files to free disk
            vid_file.unlink(missing_ok=True)
            aud_file.unlink(missing_ok=True)

            # --- Upload merged chunk to MEGA ---
            progress_cb("Uploading to MEGA", idx, n_chunks, 80.0, 0, total_size)
            node = mega_manager.upload_file(merged_path)
            mega_nodes.append(node)

            # Remove local merged chunk to free disk
            merged_path.unlink(missing_ok=True)

            # Report chunk completion
            done_bytes = int(idx * bytes_per_chunk)
            progress_cb("Chunk complete", idx, n_chunks, 100.0, done_bytes, total_size)

        if cancel_flag.get("cancelled"):
            raise InterruptedError("Cancelled by user")

        # ------------------------------------------------------------------
        # Phase 2: Download all chunks from MEGA and concatenate
        # ------------------------------------------------------------------
        progress_cb("Downloading all chunks from MEGA", n_chunks, n_chunks, 0.0, total_size, total_size)
        reconstruct_dir = work_dir / "reconstruct"
        reconstruct_dir.mkdir()

        local_chunks: list[Path] = []
        for i, node in enumerate(mega_nodes, start=1):
            progress_cb(
                "Reconstructing",
                i,
                n_chunks,
                (i / n_chunks) * 100,
                total_size,
                total_size,
            )
            chunk_file = mega_manager.download_file(node, reconstruct_dir)
            local_chunks.append(chunk_file)

        # Sort by name to preserve order
        local_chunks.sort(key=lambda p: p.name)

        # Write FFmpeg concat list
        concat_list_path = work_dir / "concat.txt"
        with open(concat_list_path, "w") as f:
            for chunk in local_chunks:
                f.write(f"file '{chunk}'\n")

        progress_cb("Merging final video", n_chunks, n_chunks, 90.0, total_size, total_size)
        final_path = work_dir / f"{_safe_filename(title)}.mp4"
        _run_ffmpeg(
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_path),
            "-c", "copy",
            str(final_path),
        )

        # Clean up local chunk files
        for chunk in local_chunks:
            chunk.unlink(missing_ok=True)
        concat_list_path.unlink(missing_ok=True)

        progress_cb("Done", n_chunks, n_chunks, 100.0, total_size, total_size)
        return final_path, mega_nodes

    except Exception:
        # On error, still return the mega_nodes so the caller can clean up MEGA
        raise


def process_audio(
    url: str,
    audio_format_id: str,
    info: dict,
    progress_cb: ProgressCallback,
    cancel_flag: dict,
) -> Path:
    """
    Download audio-only (no chunking needed for audio — single download).
    """
    title = truncate_title(info.get("title", "audio"))
    work_dir = Path(tempfile.mkdtemp(prefix="ytdl_audio_"))

    progress_cb("Downloading audio", 1, 1, 0.0, 0, 0)

    opts: dict = {
        "format": f"{audio_format_id}/bestaudio",
        "outtmpl": str(work_dir / f"{_safe_filename(title)}.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "cookiefile": _find_cookies(),
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    opts = {k: v for k, v in opts.items() if v is not None}

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    files = list(work_dir.iterdir())
    if not files:
        raise FileNotFoundError("Audio download produced no file.")

    progress_cb("Done", 1, 1, 100.0, 0, 0)
    return files[0]


def _safe_filename(name: str) -> str:
    """Remove characters that are unsafe in filenames."""
    import re
    return re.sub(r'[\\/:*?"<>|]', "_", name)[:80]
