#!/usr/bin/env python3
"""AgentMemorySync 看门狗（v2.1.0）

独立运行的轻量进程，监控 AgentMemorySync.exe 的存活：
- 每 30 秒检查一次主进程是否还在
- 主进程消失时自动重启（最多 N 次，防止崩溃循环）
- 主进程心跳记录在 %LOCALAPPDATA%\\AgentMemorySystem\\heartbeat.log
- 看门狗自身日志写在 %LOCALAPPDATA%\\AgentMemorySystem\\watchdog.log

用法（配合桌面快捷方式）：
    pythonw watchdog.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def _log(msg: str):
    try:
        local_dir = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))) / "AgentMemorySystem"
        local_dir.mkdir(parents=True, exist_ok=True)
        with open(local_dir / "watchdog.log", "a", encoding="utf-8") as f:
            f.write("[{}] {}\n".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _find_exe() -> Path:
    """定位 AgentMemorySync.exe：优先本地副本，其次 OneDrive 发布包。"""
    local = Path(os.environ.get("TEMP", "")) / "AgentMemorySync_Run" / "AgentMemorySync.exe"
    if local.exists():
        return local
    # 项目根
    repo = Path(__file__).resolve().parent.parent
    candidates = [
        repo / "AgentMemorySync" / "AgentMemorySync.exe",
        repo / "dist" / "AgentMemorySync.exe",
        repo / "build" / "AgentMemorySync.exe",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _inject_data_root_env() -> None:
    """从注册点读取数据根并注入环境变量（v2.1.1）。

    watchdog 直接 Popen 主程序时无 BAT 环境变量，显式注入注册点数据根，
    保证重启后的进程与当前所有进程使用同一数据根（单一事实来源）。
    """
    try:
        from safe_io import _read_registry
        reg = _read_registry()
        if reg is not None:
            os.environ["AGENT_MEMORY_DATA_DIR"] = str(reg)
    except Exception:
        pass


def _is_running(proc_name: str = "AgentMemorySync.exe") -> bool:
    """通过 tasklist 检查进程是否存活。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq {}".format(proc_name)],
            capture_output=True, text=True, timeout=10,
        ).stdout
        return proc_name.lower() in out.lower()
    except Exception:
        return True  # 无法检查时保守认为存活


def main():
    _log("看门狗启动 (PID {})".format(os.getpid()))
    # 注入数据根（与主程序保持一致，根治分裂）
    _inject_data_root_env()
    _log("数据根: {}".format(os.environ.get("AGENT_MEMORY_DATA_DIR", "(未注册)")))
    # 允许的最大连续重启次数
    max_restarts = 5
    restart_count = 0
    last_alive = True

    while True:
        time.sleep(30)
        try:
            if _is_running():
                last_alive = True
                restart_count = 0
                continue

            # 进程消失
            if not last_alive:
                # 连续两轮检测都未存活才重启（避免与启动延迟竞争）
                _log("检测到进程消失，准备重启 (连续次数 {})".format(restart_count + 1))
                restart_count += 1
                if restart_count > max_restarts:
                    _log("超过最大重启次数 {}，停止看门狗".format(max_restarts))
                    break

                exe = _find_exe()
                if exe is None:
                    _log("找不到 AgentMemorySync.exe，跳过重启")
                    continue
                try:
                    subprocess.Popen(
                        [str(exe)],
                        cwd=str(exe.parent),
                        creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                        close_fds=True,
                    )
                    _log("已重启: {}".format(exe))
                except Exception as e:
                    _log("重启失败: {}".format(e))
                last_alive = False
            else:
                # 第一次检测到消失，等下一轮确认
                last_alive = False
        except Exception as e:
            _log("看门狗循环异常: {}".format(e))
            time.sleep(5)


if __name__ == "__main__":
    main()
