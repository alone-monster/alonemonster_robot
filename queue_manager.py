"""
RAM monitoring and FIFO queue manager.

Rules:
- If RAM usage >= 14 GB → pause new task starts, queue incoming requests.
- If RAM usage drops to <= 12 GB → resume processing from queue.
- Running tasks are never interrupted.
"""

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import psutil

logger = logging.getLogger(__name__)

# --- RAM thresholds (bytes) ---
RAM_PAUSE_THRESHOLD = 14 * 1024 ** 3   # 14 GB → stop starting new tasks
RAM_RESUME_THRESHOLD = 12 * 1024 ** 3  # 12 GB → resume queued tasks
RAM_POLL_INTERVAL = 5                   # seconds between RAM checks


@dataclass(order=False)
class QueuedTask:
    """A pending download/processing task."""
    task_id: str
    fn: Callable
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    enqueued_at: float = field(default_factory=time.time)


class QueueManager:
    """
    Thread-safe FIFO queue with RAM-aware throttling.

    Usage:
        qm = QueueManager()
        qm.start()
        qm.submit(task_id, my_function, arg1, arg2, kwarg=value)
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[QueuedTask] = queue.Queue()
        self._active_count = 0
        self._lock = threading.Lock()
        self._paused = False
        self._running = False
        self._worker_thread: threading.Thread | None = None
        self._monitor_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background worker and RAM monitor threads."""
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="queue-worker"
        )
        self._monitor_thread = threading.Thread(
            target=self._ram_monitor_loop, daemon=True, name="ram-monitor"
        )
        self._worker_thread.start()
        self._monitor_thread.start()
        logger.info("QueueManager started.")

    def stop(self) -> None:
        """Signal threads to stop (graceful)."""
        self._running = False
        # Unblock the worker if it's waiting
        self._queue.put_nowait(None)  # type: ignore[arg-type]

    def submit(self, task_id: str, fn: Callable, *args: Any, **kwargs: Any) -> None:
        """Add a task to the FIFO queue."""
        task = QueuedTask(task_id=task_id, fn=fn, args=args, kwargs=kwargs)
        self._queue.put(task)
        logger.info("Task %s queued. Queue size: %d", task_id, self._queue.qsize())

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active_count

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_ram_used(self) -> int:
        """Return current process + children RAM usage in bytes."""
        try:
            proc = psutil.Process()
            mem = proc.memory_info().rss
            for child in proc.children(recursive=True):
                try:
                    mem += child.memory_info().rss
                except psutil.NoSuchProcess:
                    pass
            return mem
        except Exception:
            # Fallback to system-wide used memory
            return psutil.virtual_memory().used

    def _worker_loop(self) -> None:
        """Continuously pull tasks from the queue and run them."""
        while self._running:
            # Block until an item is available
            task = self._queue.get()

            # Sentinel to stop the worker
            if task is None:
                break

            # Wait while paused (RAM too high)
            while self._paused and self._running:
                time.sleep(1)

            if not self._running:
                break

            with self._lock:
                self._active_count += 1

            logger.info("Starting task %s (active=%d)", task.task_id, self._active_count)
            try:
                task.fn(*task.args, **task.kwargs)
            except Exception as exc:
                logger.exception("Task %s raised an exception: %s", task.task_id, exc)
            finally:
                with self._lock:
                    self._active_count -= 1
                self._queue.task_done()
                logger.info(
                    "Task %s finished (active=%d)", task.task_id, self._active_count
                )

    def _ram_monitor_loop(self) -> None:
        """Periodically check RAM and toggle the paused flag."""
        while self._running:
            try:
                used = self._get_ram_used()
                if not self._paused and used >= RAM_PAUSE_THRESHOLD:
                    self._paused = True
                    logger.warning(
                        "RAM usage %.1f GB >= %.1f GB threshold — pausing new tasks.",
                        used / 1024 ** 3,
                        RAM_PAUSE_THRESHOLD / 1024 ** 3,
                    )
                elif self._paused and used <= RAM_RESUME_THRESHOLD:
                    self._paused = False
                    logger.info(
                        "RAM usage %.1f GB <= %.1f GB threshold — resuming tasks.",
                        used / 1024 ** 3,
                        RAM_RESUME_THRESHOLD / 1024 ** 3,
                    )
            except Exception as exc:
                logger.error("RAM monitor error: %s", exc)

            time.sleep(RAM_POLL_INTERVAL)


# Module-level singleton
queue_manager = QueueManager()
