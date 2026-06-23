"""Safe file I/O utilities with retry and atomic write support."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional


def _pending_path(target: Path) -> Path:
    """Return the .pending path for a target file (used for locked-file fallback)."""
    return target.with_suffix(target.suffix + ".pending")


def _safe_write_text(path, content: str, encoding: str = "utf-8", retries: int = 3) -> bool:
    """Write text to file with retry on PermissionError.

    Uses atomic write (tmp + rename) when possible, falls back to direct write.
    Returns True on success.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")

    for attempt in range(retries):
        try:
            with open(tmp, "w", encoding=encoding) as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            try:
                tmp.replace(path)
            except OSError:
                # Target locked (OneDrive / another process) → direct write
                with open(path, "w", encoding=encoding) as f:
                    f.write(content)
            return True
        except PermissionError:
            # File locked by another process → write to .pending
            try:
                pending = _pending_path(path)
                with open(pending, "w", encoding=encoding) as f:
                    f.write(content)
                return True
            except OSError:
                pass
        except OSError:
            pass
        if attempt < retries - 1:
            time.sleep(0.3 * (attempt + 1))
    return False


def _safe_read_text(path, default: str = "", encoding: str = "utf-8") -> str:
    """Read text from file with retry on PermissionError.

    Returns default if file doesn't exist or can't be read.
    """
    path = Path(path)
    if not path.exists():
        return default

    for attempt in range(3):
        try:
            with open(path, "r", encoding=encoding) as f:
                return f.read()
        except PermissionError:
            # Try reading .pending file
            try:
                pending = _pending_path(path)
                if pending.exists():
                    with open(pending, "r", encoding=encoding) as f:
                        return f.read()
            except OSError:
                pass
        except OSError:
            pass
        if attempt < 2:
            time.sleep(0.3 * (attempt + 1))
    return default
