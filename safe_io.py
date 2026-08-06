"""Safe file I/O utilities with retry and atomic write support."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional


def get_data_root() -> Path:
    """获取数据根目录（用于同步数据、设置、备份等）。

    解析优先级（v2.1.0 修复打包模式路径分裂）：
    1. 环境变量 AGENT_MEMORY_DATA_DIR（启动器注入，含 BAT 启动器或 OneDrive 迁移）
    2. 项目根目录下的 AgentMemory/（跨设备同步靠 OneDrive 本身，
       项目文件夹在 OneDrive 下即可同步，无需独立探测 OneDrive 根）
    3. 打包模式 fallback（frozen）：从 EXE 所在目录向上回溯查找项目根 AgentMemory/，
       或回退到 EXE 同级 data/（避免解析到 PyInstaller _internal/ 导致数据分裂）
    4. 开发模式 fallback：脚本所在目录下的 data/
    5. LOCALAPPDATA 标准位置（仅作为最后兜底）

    设计理由：项目文件夹本身已在 OneDrive 下，再独立探测 OneDrive 根
    反而造成数据与项目割裂、用户难找、双 OneDrive 账户定位错误等问题。

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
            # env 指向的目录不可写，回退到项目根候选
            pass

    # 2. 项目根目录下的 AgentMemory/（v1.3.4 改为优先项）
    # 项目文件夹本身在 OneDrive 下即可跨设备同步，无需独立探测 OneDrive 根
    project_root = Path(__file__).resolve().parent
    if getattr(sys, "frozen", False):
        # v2.1.0: PyInstaller 下 __file__ 指向 _internal/，需从 EXE 位置向上回溯
        # 到项目根（AgentMemorySync.bat 所在目录），避免数据写到 _internal/ 内
        exe_dir = Path(sys.executable).resolve().parent
        # 情形 A: EXE 位于项目根或 TEMP 本地副本，向上回溯寻找项目根特征
        for candidate_root in (exe_dir, exe_dir.parent, exe_dir.parent.parent):
            cand = candidate_root / "AgentMemory"
            if cand.is_dir() and (cand / "sync_settings.json").exists():
                project_root = candidate_root
                break
        else:
            # 情形 B: 找不到项目根（例如 EXE 在 %TEMP% 下直接运行）
            # → 不再 fallback 到 exe 同级 data/（TEMP 会被清理导致数据丢失），
            #   而是直接跳过此级，交给 LOCALAPPDATA 兜底
            project_root = None
    project_data = None
    if project_root is not None:
        project_data = project_root / "AgentMemory"
        try:
            project_data.mkdir(parents=True, exist_ok=True)
            test = project_data / ".writable_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink()
            return project_data
        except OSError:
            pass

    # 3. 打包模式 fallback：EXE 同级目录（仅当能找到项目根时使用，
    #    否则直接跳过，避免在 TEMP 下创建数据目录）
    if getattr(sys, "frozen", False):
        root = None  # 数据根应由 BAT 注入的环境变量决定；此处留空走兜底
    else:
        # 4. 开发模式 fallback：脚本所在目录
        root = Path(__file__).resolve().parent / "data"

    # 5. 最后兜底 LOCALAPPDATA
    if root is not None:
        try:
            root.mkdir(parents=True, exist_ok=True)
            test = root / ".writable_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink()
            return root
        except OSError:
            pass
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
