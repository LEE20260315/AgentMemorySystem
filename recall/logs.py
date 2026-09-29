"""Local logging: index + logs live under %LOCALAPPDATA%\\recall-memory.

Never on OneDrive (a synced dir) — that was the old project's recurring problem.
Daily log files (recall-YYYYMMDD.log) with a retention cap. Logging must never
crash the sync, so all file ops are wrapped.

Line format (unchanged): `%(asctime)s [%(levelname)s] %(message)s`.
Health levels written by runner.sync_once (G-B10):
  * `[SYNC OK]`        — INFO    : genuine clean sync (exit 0)
  * `[SYNC DEGRADED]`  — WARNING : files=0 (exit 3) or errors>0 (exit 2)
  * `[SYNC FAIL]`      — ERROR   : the sync itself raised (exit 1)
The logger level is INFO, so WARNING/ERROR are always captured to file.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta

DEVICE_DIR = "recall-memory"

_root_cached: str | None = None


def _root() -> str:
    """Resolve a GUARANTEED-writable local root for index + logs.

    Primary is %LOCALAPPDATA%\\recall-memory (never OneDrive). If that can't be
    created/written (locked dir, synthetic environment), fall back to %TEMP%,
    then %TMP%, then the user home. Never raises; the tool never hard-crashes
    on filesystem trouble.
    """
    global _root_cached
    if _root_cached:
        return _root_cached
    candidates = [
        os.environ.get("LOCALAPPDATA"),
        os.environ.get("TEMP"),
        os.environ.get("TMP"),
        os.path.expanduser("~"),
    ]
    for base in candidates:
        if not base:
            continue
        d = os.path.join(base, DEVICE_DIR)
        probe = os.path.join(d, ".probe")
        try:
            os.makedirs(d, exist_ok=True)
            with open(probe, "w") as f:
                f.write("1")
            os.remove(probe)
        except OSError:
            continue
        _root_cached = d
        return d
    return os.path.join(os.path.expanduser("~"), DEVICE_DIR)


def logs_dir() -> str:
    d = os.path.join(_root(), "logs")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def index_db_path() -> str:
    return os.path.join(_root(), "index.db")


def today_log_path() -> str:
    name = "recall-" + datetime.now().strftime("%Y%m%d") + ".log"
    return os.path.join(logs_dir(), name)


def _prune_logs(keep_days: int = 14) -> None:
    """Delete daily logs older than keep_days. Best-effort."""
    try:
        cutoff = (datetime.now() - timedelta(days=keep_days)).date()
        for fn in os.listdir(logs_dir()):
            if not fn.startswith("recall-") or not fn.endswith(".log"):
                continue
            try:
                day = datetime.strptime(fn[len("recall-"):-len(".log")], "%Y%m%d").date()
            except ValueError:
                continue
            if day < cutoff:
                try:
                    os.remove(os.path.join(logs_dir(), fn))
                except OSError:
                    pass
    except OSError:
        pass


_logger: logging.Logger | None = None


def get_logger(name: str = "recall") -> logging.Logger:
    """Return a logger that writes to the daily log file AND stderr.
    Idempotent; never raises."""
    global _logger
    if _logger is not None:
        return _logger

    _prune_logs()
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    try:
        fh = logging.FileHandler(today_log_path(), encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    logger.propagate = False
    _logger = logger
    return logger