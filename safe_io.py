"""Safe file I/O utilities with retry and atomic write support."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional


def get_data_root() -> Path:
    """获取数据根目录（用于同步数据、设置、备份等）。

    解析优先级：
    1. 环境变量 AGENT_MEMORY_DATA_DIR（OneDrive 迁移时由 _ensure_local_install 传递）
    2. 打包模式（frozen）：EXE 所在目录下的 data/
    3. 开发模式：脚本所在目录下的 data/

    Returns
    -------
    Path
        数据根目录路径（已确保存在）
    """
    # 1. 环境变量（OneDrive → 本地迁移时传递原始数据目录）
    env_data = os.environ.get("AGENT_MEMORY_DATA_DIR")
    if env_data:
        p = Path(env_data)
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p
        except OSError:
            pass

    # 2. 打包模式：EXE 同级目录
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).parent / "data"
    else:
        # 3. 开发模式：脚本所在目录
        root = Path(__file__).resolve().parent / "data"

    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return root


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


def _safe_read_text(path, default: str = "", encoding: str = "utf-8", max_size: int = 50 * 1024 * 1024) -> str:
    """Read text from file with retry on PermissionError.

    Returns default if file doesn't exist or can't be read.
    Files larger than max_size (default 50MB) are truncated to avoid MemoryError.
    """
    path = Path(path)
    if not path.exists():
        return default

    for attempt in range(3):
        try:
            # 检查文件大小，超大文件截断读取避免 MemoryError
            try:
                file_size = path.stat().st_size
            except OSError:
                file_size = 0

            if file_size > max_size:
                # 只读取最后 max_size 字节
                with open(path, "r", encoding=encoding, errors="replace") as f:
                    f.seek(max(0, file_size - max_size))
                    return f.read()
            else:
                with open(path, "r", encoding=encoding, errors="replace") as f:
                    return f.read()
        except PermissionError:
            # Try reading .pending file
            try:
                pending = _pending_path(path)
                if pending.exists():
                    with open(pending, "r", encoding=encoding, errors="replace") as f:
                        return f.read()
            except OSError:
                pass
        except (OSError, MemoryError):
            # MemoryError 或其他 OSError：返回默认值而非崩溃
            if isinstance(OSError, MemoryError):
                return default
            pass
        if attempt < 2:
            time.sleep(0.3 * (attempt + 1))
    return default
