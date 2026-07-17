"""
MEGA cloud storage manager.

Handles:
- Login (singleton, thread-safe)
- Uploading chunk files
- Downloading chunk files back for final merge
- Deleting all temporary files after sending
"""

import logging
import os
import threading
from pathlib import Path

from mega import Mega

logger = logging.getLogger(__name__)

_mega_lock = threading.Lock()
_mega_instance: Mega | None = None


def _get_mega() -> Mega:
    """Return a logged-in Mega instance (lazy singleton)."""
    global _mega_instance
    with _mega_lock:
        if _mega_instance is None:
            email = os.environ["MEGA_EMAIL"]
            password = os.environ["MEGA_PASSWORD"]
            logger.info("Logging in to MEGA as %s", email)
            mega = Mega()
            mega.login(email, password)
            _mega_instance = mega
            logger.info("MEGA login successful.")
        return _mega_instance


def upload_file(local_path: str | Path) -> dict:
    """
    Upload a local file to MEGA.

    Returns the file node dict returned by mega.upload().
    """
    local_path = Path(local_path)
    if not local_path.exists():
        raise FileNotFoundError(f"File not found: {local_path}")

    mega = _get_mega()
    logger.info("Uploading %s to MEGA…", local_path.name)
    file_node = mega.upload(str(local_path))
    logger.info("Uploaded %s → MEGA node: %s", local_path.name, file_node)
    return file_node


def download_file(file_node: dict, dest_dir: str | Path) -> Path:
    """
    Download a previously uploaded MEGA file back to dest_dir.

    Returns the Path of the downloaded file.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    mega = _get_mega()

    # mega.download() places the file in dest_dir and returns its path
    downloaded = mega.download(file_node, dest_path=str(dest_dir))
    if downloaded is None:
        # Fallback: use the link-based approach
        link = mega.get_link(file_node)
        downloaded = mega.download_url(link, dest_path=str(dest_dir))

    downloaded_path = Path(downloaded) if downloaded else None
    if downloaded_path and downloaded_path.exists():
        logger.info("Downloaded from MEGA → %s", downloaded_path)
        return downloaded_path

    raise RuntimeError(f"MEGA download failed for node {file_node}")


def delete_file(file_node: dict) -> None:
    """Delete a file from MEGA by its node dict."""
    mega = _get_mega()
    try:
        # Extract node handle — mega.py stores it differently by version
        handle = None
        if isinstance(file_node, dict):
            # Typical structure: {handle_key: {node_dict}}
            for key, val in file_node.items():
                if isinstance(val, dict) and "h" in val:
                    handle = val["h"]
                    break
            if handle is None and "h" in file_node:
                handle = file_node["h"]

        if handle:
            mega.delete(handle)
            logger.info("Deleted MEGA node handle %s", handle)
        else:
            logger.warning("Could not determine MEGA handle from node: %s", file_node)
    except Exception as exc:
        logger.error("Failed to delete MEGA node: %s — %s", file_node, exc)


def delete_files(file_nodes: list[dict]) -> None:
    """Delete multiple MEGA files, logging but not raising on individual failures."""
    for node in file_nodes:
        try:
            delete_file(node)
        except Exception as exc:
            logger.error("Could not delete MEGA node %s: %s", node, exc)
