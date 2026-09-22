"""Logging and progress reporting.

Two different things, deliberately kept apart:

* **Logging** is for your application. The package attaches a
  :class:`~logging.NullHandler` and never calls ``basicConfig`` -- a library
  that hijacks the root logger is a bad guest. Records go to the
  ``dataportalen`` logger and you decide what happens to them.
  :func:`enable_logging` is there when you just want to see them.

* **Progress** is for a human watching a terminal. A five-minute catalogue
  download that prints nothing is user-hostile, so long operations report to
  stderr when stderr is a terminal, the way ``pip`` and ``curl`` do, and stay
  quiet when it is piped to a file.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any, Callable, Optional

__all__ = ["logger", "enable_logging", "progress_reporter"]

#: The package logger. Configure it as you would any other.
logger = logging.getLogger("dataportalen")
logger.addHandler(logging.NullHandler())


def enable_logging(
    level: Any = logging.INFO,
    stream: Any = None,
    fmt: str = "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
) -> logging.Logger:
    """Send this package's log records to ``stream`` (default stderr).

    A convenience for scripts and notebooks. Applications that already
    configure logging should ignore this and handle the ``dataportalen``
    logger themselves.

    Calling it twice replaces the handler rather than doubling the output.
    """
    if isinstance(level, str):
        level = logging.getLevelName(level.upper())
    for existing in list(logger.handlers):
        if getattr(existing, "_dataportalen", False):
            logger.removeHandler(existing)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))
    handler._dataportalen = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(level)
    return logger


class _TerminalProgress:
    """A single rewriting line on stderr, throttled to ~5 updates a second."""

    def __init__(self, label: str, stream: Any = None, min_interval: float = 0.2) -> None:
        self.label = label
        self.stream = stream or sys.stderr
        self.min_interval = min_interval
        self.started = time.time()
        self._last = 0.0
        self._width = 0

    def __call__(self, done: int, total: int) -> None:
        now = time.time()
        final = total and done >= total
        if not final and now - self._last < self.min_interval:
            return
        self._last = now

        elapsed = now - self.started
        rate = done / elapsed if elapsed > 0 else 0.0
        if total:
            pct = 100.0 * done / total
            remaining = (total - done) / rate if rate > 0 else 0
            text = "%s %6.1f%%  %s/%s  %.0f/s  eta %s" % (
                self.label, pct, f"{done:,}", f"{total:,}", rate, _duration(remaining))
        else:
            text = "%s %s  %.0f/s" % (self.label, f"{done:,}", rate)

        padded = text.ljust(self._width)
        self._width = max(self._width, len(text))
        try:
            self.stream.write("\r" + padded)
            if final:
                self.stream.write("\n")
            self.stream.flush()
        except Exception:  # pragma: no cover - a closed or odd stream
            pass


def _duration(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm%02ds" % (seconds // 60, seconds % 60)
    return "%dh%02dm" % (seconds // 3600, (seconds % 3600) // 60)


def progress_reporter(
    progress: Any,
    label: str,
    log_every: int = 2000,
) -> Optional[Callable[[int, int], None]]:
    """Turn the ``progress`` argument into a callback.

    ``"auto"`` (the default for long operations) draws a live line when
    stderr is a terminal, and otherwise logs a line every ``log_every`` items
    so a redirected run still leaves a trail. ``None`` is silent, and a
    callable is used as given.
    """
    if progress is None:
        return None
    if callable(progress):
        return progress
    if progress != "auto":
        raise ValueError("progress must be 'auto', None, or a callable")

    stream = sys.stderr
    if getattr(stream, "isatty", lambda: False)():
        return _TerminalProgress(label)

    state = {"next": log_every}

    def log_progress(done: int, total: int) -> None:
        if done >= state["next"] or (total and done >= total):
            state["next"] = done + log_every
            if total:
                logger.info("%s %d/%d (%.0f%%)", label, done, total, 100.0 * done / total)
            else:
                logger.info("%s %d", label, done)

    return log_progress
