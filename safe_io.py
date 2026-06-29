"""Safe file I/O utilities with retry and atomic write support."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional


def get_data_root() -> Path:
    """获取数据根目录（用于同步数据、设置、备份等）。

    解析优先级（v1.3.2 调整）：
    1. 环境变量 AGENT_MEMORY_DATA_DIR（启动器注入，含 BAT 启动器或 OneDrive 迁移）
    2. 跨设备默认：OneDrive 下的 AgentMemory/（同步数据真相源）
       先按 OneDrive / OneDriveConsumer / OneDriveCommercial 环境变量找，
       再兜底探测用户主目录下常见的 OneDrive/AgentMemory/
    3. 打包模式 fallback（frozen）：EXE 所在目录下的 data/
    4. 开发模式 fallback：脚本所在目录下的 data/
    5. LOCALAPPDATA 标准位置（仅作为最后兜底）

    Returns
    -------
    Path
        数据根目录路径（已确保存在）
    """
    # 1. 环境变量（最优先 - 启动器或 OneDrive 迁移时由 BAT 传递）
    env_data = os.environ.get("AGENT_MEMORY_DATA_DIR")
    if env_data:
        p = Path(env_data).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
            # 写探针确保可写
            test = p / ".writable_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink()
            return p
        except OSError:
            # env 指向的目录不可写，回退到 OneDrive 候选
            pass

    # 2. 跨设备默认（OneDrive/AgentMemory/）—— v1.3.2 改为优先项
    onedrive_candidates = []
    for env_var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = os.environ.get(env_var)
        if root:
            onedrive_candidates.append(Path(root) / "AgentMemory")
    # 兜底：拼用户目录下常见位置（开发机）
    home_onedrive = Path.home() / "OneDrive" / "AgentMemory"
    if home_onedrive not in onedrive_candidates:
        onedrive_candidates.append(home_onedrive)

    for cand in onedrive_candidates:
        try:
            cand.mkdir(parents=True, exist_ok=True)
            test = cand / ".writable_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink()
            return cand
        except OSError:
            continue

    # 3. 打包模式 fallback：EXE 同级目录
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).parent / "data"
    else:
        # 4. 开发模式 fallback：脚本所在目录
        root = Path(__file__).resolve().parent / "data"

    # 5. 最后兜底 LOCALAPPDATA
    try:
        root.mkdir(parents=True, exist_ok=True)
        test = root / ".writable_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink()
        return root
    except OSError:
        local_appdata = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        fallback = local_appdata / "AgentMemorySystem"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


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
