"""
多Agent记忆融合器
================
双击即跑的 GUI 工具，自动完成：
  读取本地 Agent 记忆 → 融合 → 写回各 Agent

功能：
- 系统托盘常驻，支持手动/自动同步
- 实时日志面板显示同步过程
- 汇总面板显示统计结果
- 一键回滚上次同步
- 设置面板配置自动同步间隔和 Agent 路径

用法：
  python memory_sync_app.py          # 启动 GUI
  python memory_sync_app.py --cli    # 命令行模式（不启动 GUI）
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import math
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from typing import Optional

from PIL import Image, ImageDraw, ImageTk

# Windows DPI 感知 —— 必须在创建任何 tkinter 窗口之前调用
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

# 字体配置
if sys.platform == "win32":
    _FONT = "Microsoft YaHei UI"   # Windows 10/11 清晰中文字体
elif sys.platform == "darwin":
    _FONT = "PingFang SC"          # macOS
else:
    _FONT = "Noto Sans CJK SC"    # Linux

# PyInstaller 兼容：打包后资源文件在临时目录
def _resource_path(relative: str) -> Path:
    """获取资源文件的绝对路径（兼容 PyInstaller 打包）"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / relative

_ICON_PATH = _resource_path("assets/app_icon.ico")
_TRAY_ICON_PATH = _resource_path("assets/tray_icon.png")

# 进程级单实例互斥锁句柄：必须常驻，不能只保存在局部变量里
# 否则函数返回后句柄可能被释放，后续重复启动会误判为首实例。
_SINGLE_INSTANCE_MUTEX = None


# ---------------------------------------------------------------------------
# Windows 标题栏配色 —— 把系统默认蓝条改成灰白黑配色
# ---------------------------------------------------------------------------
def _hex_to_dwm_color(hex_str: str) -> int:
    """把 '#ECEFF1' 或 'ECEFF1' 转成 DWM 期望的 0x00BBGGRR（DWORD 反序）。"""
    h = hex_str.lstrip("#")
    if len(h) != 6:
        return 0
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def apply_dwm_caption_colors(tk_window, caption_hex: str = "#ececf0",
                             text_hex: str = "#1d1d1f") -> bool:
    """把 Windows 系统标题栏底色/字色改成指定灰白色。失败时静默返回 False。

    Win10 1903+ / Win11 支持 DWMWA_CAPTION_COLOR(35) / DWMWA_TEXT_COLOR(36)。
    """
    if sys.platform != "win32":
        return False
    try:
        hwnd = tk_window.winfo_id()
        if not hwnd:
            return False
        # 顶层 Toplevel 的 winfo_id 拿到的是 client 窗口句柄，
        # 需要 GetAncestor(GA_ROOT) 拿到带标题栏的顶层 HWND
        try:
            GA_ROOT = 2
            _user32.GetAncestor.restype = ctypes.c_void_p
            _user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            root_hwnd = _user32.GetAncestor(hwnd, GA_ROOT)
            if not root_hwnd:
                root_hwnd = hwnd
        except Exception:
            root_hwnd = hwnd

        try:
            _dwm = ctypes.windll.dwmapi
            _dwm.DwmSetWindowAttribute.argtypes = [
                ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint
            ]
            _dwm.DwmSetWindowAttribute.restype = ctypes.HRESULT
        except Exception:
            return False

        cap_color = ctypes.c_uint(_hex_to_dwm_color(caption_hex))
        text_color = ctypes.c_uint(_hex_to_dwm_color(text_hex))
        # DWMWA_CAPTION_COLOR = 35, DWMWA_TEXT_COLOR = 36
        hr1 = _dwm.DwmSetWindowAttribute(root_hwnd, 35,
                                         ctypes.byref(cap_color), ctypes.sizeof(cap_color))
        hr2 = _dwm.DwmSetWindowAttribute(root_hwnd, 36,
                                         ctypes.byref(text_color), ctypes.sizeof(text_color))
        return (hr1 == 0 and hr2 == 0)
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Windows 原生系统托盘 API（不依赖 pystray，--windowed 模式也能正常工作）
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    _user32 = ctypes.windll.user32
    _shell32 = ctypes.windll.shell32
    _kernel32 = ctypes.windll.kernel32

    # 常量
    _NIM_ADD = 0x00000000
    _NIM_MODIFY = 0x00000001
    _NIM_DELETE = 0x00000002
    _NIM_SETVERSION = 0x00000004
    _NOTIFYICON_VERSION_4 = 4
    _NIF_MESSAGE = 0x00000001
    _NIF_ICON = 0x00000002
    _NIF_TIP = 0x00000004
    _NIF_INFO = 0x00000010
    _NIF_GUID = 0x00000020
    _NIF_REALTIME = 0x00000040
    _NIF_SHOWTIP = 0x00000080
    _NIIF_INFO = 0x00000001
    _WM_TRAYICON = 0x0400 + 0x1F00  # 自定义消息
    _WM_LBUTTONUP = 0x0202
    _WM_RBUTTONUP = 0x0205
    _WM_TASKBARCREATED = _user32.RegisterWindowMessageW("TaskbarCreated")
    _TRAY_CLASS_NAME = "AgentMemorySyncTray"
    _HWND_MESSAGE = ctypes.wintypes.HWND(-3)  # 消息专用窗口父句柄
    _APP_USER_MODEL_ID = "AgentMemorySync"
    # 固定 GUID 用于托盘图标识别（Win11 推荐用 GUID 而非 uID）
    _TRAY_GUID = bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0,
                        0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0])

    _user32.UnregisterClassW.argtypes = [ctypes.wintypes.LPWSTR, ctypes.c_void_p]
    _user32.UnregisterClassW.restype = ctypes.wintypes.BOOL

    # 64 位安全的 WPARAM/LPARAM
    _WPARAM = ctypes.c_size_t
    _LPARAM = ctypes.c_ssize_t

    # 设置 API 函数参数类型（64 位系统上句柄是 8 字节，必须声明否则溢出）
    # 注意：所有句柄类型统一用 c_void_p，避免 HINSTANCE/HWND 等子类型转换问题
    _user32.CreateWindowExW.argtypes = [
        ctypes.wintypes.DWORD,      # dwExStyle
        ctypes.c_wchar_p,           # lpClassName
        ctypes.c_wchar_p,           # lpWindowName
        ctypes.wintypes.DWORD,      # dwStyle
        ctypes.c_int,               # x
        ctypes.c_int,               # y
        ctypes.c_int,               # nWidth
        ctypes.c_int,               # nHeight
        ctypes.c_void_p,            # hWndParent
        ctypes.c_void_p,            # hMenu
        ctypes.c_void_p,            # hInstance
        ctypes.c_void_p,            # lpParam
    ]
    _user32.CreateWindowExW.restype = ctypes.c_void_p

    _user32.RegisterClassW.argtypes = [ctypes.c_void_p]
    _user32.RegisterClassW.restype = ctypes.wintypes.ATOM

    _user32.DestroyWindow.argtypes = [ctypes.c_void_p]
    _user32.DestroyWindow.restype = ctypes.wintypes.BOOL

    _user32.DestroyIcon.argtypes = [ctypes.c_void_p]
    _user32.DestroyIcon.restype = ctypes.wintypes.BOOL

    _user32.PeekMessageW.argtypes = [
        ctypes.c_void_p,            # lpMsg
        ctypes.c_void_p,            # hWnd
        ctypes.wintypes.UINT,       # wMsgFilterMin
        ctypes.wintypes.UINT,       # wMsgFilterMax
        ctypes.wintypes.UINT,       # wRemoveMsg
    ]
    _user32.PeekMessageW.restype = ctypes.wintypes.BOOL

    _user32.TranslateMessage.argtypes = [ctypes.c_void_p]
    _user32.TranslateMessage.restype = ctypes.wintypes.BOOL

    _user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
    _user32.DispatchMessageW.restype = _LPARAM

    _user32.DefWindowProcW.argtypes = [
        ctypes.c_void_p, ctypes.wintypes.UINT, _WPARAM, _LPARAM
    ]
    _user32.DefWindowProcW.restype = _LPARAM

    _shell32.Shell_NotifyIconW.argtypes = [
        ctypes.wintypes.DWORD, ctypes.c_void_p
    ]
    _shell32.Shell_NotifyIconW.restype = ctypes.wintypes.BOOL

    _shell32.ExtractIconW.argtypes = [
        ctypes.c_void_p, ctypes.c_wchar_p, ctypes.wintypes.UINT
    ]
    _shell32.ExtractIconW.restype = ctypes.c_void_p

    _kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    _kernel32.GetModuleHandleW.restype = ctypes.c_void_p

    class _NOTIFYICONDATAW(ctypes.Structure):
        # 匹配 Windows Vista+ NOTIFYICONDATAW 结构体 (cbSize=968)
        # 字段顺序必须与 commctrl.h 完全一致
        _fields_ = [
            ("cbSize", ctypes.wintypes.DWORD),          # 0
            ("hWnd", ctypes.wintypes.HWND),              # 8
            ("uID", ctypes.wintypes.UINT),               # 16
            ("uFlags", ctypes.wintypes.UINT),            # 20
            ("uCallbackMessage", ctypes.wintypes.UINT),  # 24
            ("hIcon", ctypes.wintypes.HICON),             # 32
            ("szTip", ctypes.c_wchar * 128),             # 40..295
            ("dwState", ctypes.wintypes.DWORD),          # 296
            ("dwStateMask", ctypes.wintypes.DWORD),      # 300
            ("szInfo", ctypes.c_wchar * 256),            # 304..815  ← 气泡内容
            ("uTimeout", ctypes.wintypes.UINT),          # 816 (union with uVersion)
            ("szInfoTitle", ctypes.c_wchar * 64),        # 820..947  ← 气泡标题
            ("dwInfoFlags", ctypes.wintypes.DWORD),      # 948
            ("guidItem", ctypes.c_byte * 16),            # 952..967
        ]

    class _WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", ctypes.wintypes.UINT),
            ("lpfnWndProc", ctypes.c_void_p),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", ctypes.wintypes.HINSTANCE),
            ("hIcon", ctypes.wintypes.HICON),
            ("hCursor", ctypes.wintypes.HANDLE),
            ("hbrBackground", ctypes.wintypes.HBRUSH),
            ("lpszMenuName", ctypes.c_wchar_p),
            ("lpszClassName", ctypes.c_wchar_p),
        ]

    def _load_icon_from_file(icon_path: str):
        """从 ICO/PNG 文件加载 HICON"""
        hIcon = _shell32.ExtractIconW(0, icon_path, 0)
        # ExtractIconW 返回 0=无图标, 1=非有效文件, >1=有效句柄
        if hIcon and hIcon > 1:
            return hIcon
        # 尝试用 PIL 转 ICO 再加载
        try:
            from PIL import Image
            img = Image.open(icon_path)
            ico_buf = str(_data_dir() / "_tray_tmp.ico")
            Path(ico_buf).parent.mkdir(parents=True, exist_ok=True)
            img.save(ico_buf, format="ICO", sizes=[(16, 16), (32, 32)])
            hIcon = _shell32.ExtractIconW(0, ico_buf, 0)
            if hIcon and hIcon > 1:
                return hIcon
        except Exception:
            pass
        return None

    def _create_default_icon():
        """创建默认蓝色圆形 M 图标"""
        try:
            from PIL import Image, ImageDraw
            img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse([2, 2, 30, 30], fill="#4A90D9")
            draw.text((9, 7), "M", fill="white")
            ico_buf = str(_data_dir() / "_tray_default.ico")
            Path(ico_buf).parent.mkdir(parents=True, exist_ok=True)
            img.save(ico_buf, format="ICO", sizes=[(16, 16), (32, 32)])
            hIcon = _shell32.ExtractIconW(0, ico_buf, 0)
            if hIcon and hIcon > 1:
                return hIcon
        except Exception:
            pass
        # 最终兜底：系统默认应用图标
        return _shell32.ExtractIconW(0, "shell32.dll", 0)


# ---------------------------------------------------------------------------
# 设置文件
# ---------------------------------------------------------------------------

def _safe_home() -> Path:
    """获取用户主目录（PyInstaller --windowed 模式下 Path.home() 可能返回异常路径）"""
    try:
        h = Path.home()
        if h.exists():
            return h
    except Exception:
        pass
    # 回退：Windows API SHGetFolderPathW 获取 CSIDL_PROFILE
    if sys.platform == "win32":
        try:
            buf = ctypes.create_unicode_buffer(260)
            ctypes.windll.shell32.SHGetFolderPathW(0, 0x0040, 0, 0, buf)
            if buf.value:
                p = Path(buf.value)
                if p.exists():
                    return p
        except Exception:
            pass
    # 最终回退：环境变量
    for var in ("USERPROFILE", "HOME", "HOMEPATH"):
        val = os.environ.get(var)
        if val:
            p = Path(val)
            if p.exists():
                return p
    return Path.home()


def _normalize_windows_path(path: Path) -> Path:
    """尽量把 Windows 短路径（8.3）展开成长路径。"""
    p = Path(path)
    if sys.platform != "win32":
        return p
    try:
        buf = ctypes.create_unicode_buffer(32768)
        result = ctypes.windll.kernel32.GetLongPathNameW(str(p), buf, len(buf))
        if result and buf.value:
            return Path(buf.value)
    except Exception:
        pass
    return p


def _reloc_log(msg: str):
    """记录迁移诊断信息到 tray_error.log。"""
    try:
        data_dir = _data_dir()
    except Exception:
        try:
            data_dir = Path(sys.executable).parent / "data"
        except Exception:
            return
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        with open(data_dir / "tray_error.log", "a", encoding="utf-8") as f:
            f.write(f"[RELOC] {msg}\n")
    except Exception:
        pass


def _data_dir() -> Path:
    r"""获取应用数据目录

    解析优先级（v1.3.4 调整，默认指向项目根/AgentMemory/）：
    1. 环境变量 AGENT_MEMORY_DATA_DIR（启动器注入）
    2. 项目根目录下的 AgentMemory/（跨设备同步靠 OneDrive 本身）
    3. EXE 同级目录下的 data/
    4. LOCALAPPDATA 标准位置

    与 safe_io.get_data_root() 保持一致的解析顺序，保证数据和设置在同一目录。
    """
    # 委托给 safe_io.get_data_root()，避免逻辑双轨
    from safe_io import get_data_root
    return get_data_root()


def _migrate_old_data():
    """把旧版本数据迁移到新目录。

    v1.3.4 升级迁移：从 OneDrive\\AgentMemory\\ 迁移到 项目根\\AgentMemory\\。
    判断条件：新目录中没有 sync_settings.json，但旧目录有。
    迁移采用复制（非移动），保留旧目录作为备份，避免 OneDrive 同步冲突导致数据丢失。
    """
    new_dir = _data_dir()
    new_settings = new_dir / "sync_settings.json"
    # 新目录已有配置 → 已迁移过或全新安装，跳过
    if new_settings.exists():
        return

    # v1.3.4 升级：从 OneDrive\AgentMemory\ 迁移
    old_candidates = []
    for env_var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = os.environ.get(env_var)
        if root:
            old_candidates.append(Path(root) / "AgentMemory")
    old_candidates.append(_safe_home() / "OneDrive" / "AgentMemory")

    # 旧版 fallback 位置
    old_candidates.append(_safe_home() / ".agent_memory")
    old_candidates.append(
        Path(os.environ.get("LOCALAPPDATA", _safe_home() / "AppData" / "Local")) / "AgentMemorySystem"
    )

    try:
        new_dir.mkdir(parents=True, exist_ok=True)
        import shutil
        for old_dir in old_candidates:
            if not old_dir.exists() or old_dir.resolve() == new_dir.resolve():
                continue
            old_settings = old_dir / "sync_settings.json"
            if not old_settings.exists():
                continue
            # 复制整个旧目录内容到新目录（保留旧目录作为备份）
            print(f"[迁移] 从 {old_dir} 迁移数据到 {new_dir}")
            for item in old_dir.iterdir():
                target = new_dir / item.name
                if target.exists():
                    continue  # 不覆盖已有文件
                try:
                    if item.is_dir():
                        shutil.copytree(item, target)
                    else:
                        shutil.copy2(item, target)
                except Exception:
                    pass
            print(f"[迁移] 完成（旧目录保留作为备份: {old_dir}）")
            break
    except Exception as e:
        print(f"[迁移] 警告: {e}")


SETTINGS_PATH = _data_dir() / "sync_settings.json"

DEFAULT_SETTINGS = {
    "auto_interval_hours": 2,
    "conflict_action": "prompt",
    "auto_start": False,
    "minimize_to_tray": True,
    "window_geometry": "880x620",
}


def load_settings() -> dict:
    loaded = {}
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    merged = {**DEFAULT_SETTINGS, **loaded}

    # 历史版本保存的窗口尺寸不足以容纳"操作卡片"四按钮 + 汇总，强制升级到 880x620，
    # 避免用户首次启动看不到"立即同步 / 回滚 / 设置 / 最小化到托盘"。
    geo = merged.get("window_geometry", "")
    if isinstance(geo, str) and geo:
        try:
            w_str, h_str = geo.split("x", 1)
            w_val, h_val = int(w_str), int(h_str.split("+")[0].split("-")[0])
            if w_val < 860 or h_val < 600:
                merged["window_geometry"] = DEFAULT_SETTINGS["window_geometry"]
        except (ValueError, IndexError):
            merged["window_geometry"] = DEFAULT_SETTINGS["window_geometry"]
    else:
        merged["window_geometry"] = DEFAULT_SETTINGS["window_geometry"]

    return merged


def save_settings(settings: dict):
    _data_dir().mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(SETTINGS_PATH)
    except OSError:
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except OSError:
            pass  # 设置写入失败不应崩溃


# ---------------------------------------------------------------------------
# 现代化 UI 样式 —— Windows 桌面工具风格，纯灰白黑配色
# ---------------------------------------------------------------------------

# COLORS 是配色的唯一来源；所有 widget 必须从这里取色，不允许硬编码 hex
COLORS = {
    # 应用/页面背景
    "bg": "#ececf0",            # 主窗口与设置窗内容背景：浅灰
    # 内容头部（沿用 mac 风格假标题栏的配色）
    "header_bg": "#ececf0",     # 头部背景 = 页面背景（保持浅灰基调）
    "header_fg": "#5b5b62",     # 头部主文字色（深灰加粗）
    # 卡片
    "card_bg": "#ffffff",       # 卡片白底
    # 文本
    "text": "#1d1d1f",          # 主文本（标题/正文/按钮文字）
    "text_secondary": "#86868b",  # 辅助说明、次文本
    # 边框 / 分隔
    "border": "#d2d2d7",        # 1px 边框颜色
    # 主按钮（石墨灰）
    "btn_primary_bg": "#3a3a3c",
    "btn_primary_hover": "#2c2c2e",
    "btn_primary_active": "#1c1c1e",
    # 次按钮（中灰）
    "btn_secondary_bg": "#e8e8ed",
    "btn_secondary_hover": "#d8d8de",
    "btn_secondary_active": "#c8c8ce",
    # 焦点强调（仍然用最深灰，避免彩色）
    "focus": "#1d1d1f",
    # --- 旧 token 名保留以兼容测试 / 外部抛错信息；颜色仍然走灰白黑 ---
    "accent": "#3a3a3c",        # 旧名 → 等同 btn_primary_bg（避免引入彩色高亮）
    "accent_hover": "#2c2c2e",
    "success": "#86868b",       # 旧名 → status_ready
    "warning": "#3a3a3c",       # 旧名 → status_running
    "error": "#1c1c1e",         # 旧名 → status_error
    "sidebar_bg": "#ececf0",
    # 状态点（就绪=绿、同步中=黄、错误=红 —— 保留项目原有的状态灯语义）
    "status_ready": "#34c759",
    "status_running": "#f5a623",
    "status_error": "#e53935",
    # 日志
    "log_bg": "#fafafa",
    "log_text": "#3d3d3d",
    "log_selection_bg": "#d8d8de",
    "log_insert_bg": "#1d1d1f",
    # 按钮 disabled 状态（中性化的浅/中灰）
    "btn_disabled_bg": "#9a9a9e",       # 主按钮 disabled：浅石板灰
    "btn_disabled_fg": "#ffffff",       # 主按钮 disabled 文字：白
    "btn_secondary_disabled_bg": "#f1f1f3",  # 次按钮 disabled：极浅灰
}


# 旧 token 名保留兼容 —— 新代码请直接用语义命名
_COLORS_LEGACY_ALIASES = {
    "accent": "btn_primary_bg",        # 数字高亮改为深灰加粗
    "accent_hover": "btn_primary_hover",
    "success": "status_ready",
    "warning": "status_running",
    "error": "status_error",
    "sidebar_bg": "bg",
}


def _resolve_color(token: str) -> str:
    """解析 token；优先新语义，回退到旧 token 名。"""
    if token in COLORS:
        return COLORS[token]
    alias = _COLORS_LEGACY_ALIASES.get(token)
    if alias and alias in COLORS:
        return COLORS[alias]
    return token  # 兜底：当作实际颜色值使用


# 移除旧的 RoundedButton —— 由统一的 ttk.Button 风格替代
# 旧类保留为占位以兼容尚未清理的引用，实例化时统一报错
class RoundedButton:  # pragma: no cover - 仅占位，不再被使用
    """已废弃：旧的 macOS Canvas 按钮。统一改用 ttk.Button + Primary/Secondary style。"""
    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "RoundedButton 已废弃，请改用 ttk.Button(style='Primary.TButton' 或 'Secondary.TButton')"
        )


def apply_modern_style(root: tk.Tk):
    """应用 Windows 桌面工具风格的统一样式 —— 灰白黑配色。

    所有按钮统一通过 Primary.TButton / Secondary.TButton style 渲染，
    不再使用 Canvas 自绘。设置弹窗与主窗口共享同一套按钮样式。
    """
    # 高 DPI 缩放修正
    if sys.platform == "win32":
        try:
            dpi = root.winfo_fpixels("1i")
            root.tk.call("tk", "scaling", dpi / 72.0)
        except Exception:
            pass

    style = ttk.Style(root)
    # clam 是 ttk 中最稳定的可定制主题，作为基础
    style.theme_use("clam")

    # ---- 全局基础样式 ----
    style.configure(".", background=COLORS["bg"], foreground=COLORS["text"])

    # 框架
    style.configure("Card.TFrame", background=COLORS["card_bg"], relief="flat")
    style.configure("TFrame", background=COLORS["bg"])

    # ---- 标签层级 ----
    # 主窗口内容头部标题：18 bold
    style.configure(
        "Title.TLabel",
        background=COLORS["bg"],
        foreground=COLORS["text"],
        font=(_FONT, 14, "bold"),
    )
    # 内容头部辅助说明 + 设置窗副标题：9 次文本
    style.configure(
        "Subtitle.TLabel",
        background=COLORS["bg"],
        foreground=COLORS["text_secondary"],
        font=(_FONT, 10),
    )
    # 卡片内正文
    style.configure(
        "Card.TLabel",
        background=COLORS["card_bg"],
        foreground=COLORS["text"],
        font=(_FONT, 10),
    )
    # 卡片标题：12 semibold
    style.configure(
        "CardTitle.TLabel",
        background=COLORS["card_bg"],
        foreground=COLORS["text"],
        font=(_FONT, 12, "bold"),
    )
    # 汇总数值：14 bold，颜色=主文本（不再使用蓝色）
    style.configure(
        "Stat.TLabel",
        background=COLORS["card_bg"],
        foreground=COLORS["text"],
        font=(_FONT, 14, "bold"),
    )
    # 汇总标签：9 次文本
    style.configure(
        "StatLabel.TLabel",
        background=COLORS["card_bg"],
        foreground=COLORS["text_secondary"],
        font=(_FONT, 9),
    )

    # ---- LabelFrame ----
    style.configure(
        "Card.TLabelframe",
        background=COLORS["card_bg"],
        foreground=COLORS["text"],
        borderwidth=0,
    )
    style.configure(
        "Card.TLabelframe.Label",
        background=COLORS["card_bg"],
        foreground=COLORS["text"],
        font=(_FONT, 11, "bold"),
    )

    # ---- 按钮统一样式 ----
    # 基线按钮高度通过 padding 控制；clam 下文字垂直居中由 layout 完成
    _BUTTON_LAYOUT = [
        (
            "Button.button",
            {
                "children": [
                    (
                        "Button.focus",
                        {
                            "children": [
                                (
                                    "Button.padding",
                                    {
                                        "children": [
                                            ("Button.label", {"sticky": "nswe"})
                                        ],
                                        "sticky": "nswe",
                                    },
                                )
                            ],
                            "sticky": "nswe",
                        },
                    )
                ],
                "sticky": "nswe",
            },
        )
    ]

    # 主按钮：石墨灰底、白字（用于"立即同步""保存"等）
    style.layout("Primary.TButton", _BUTTON_LAYOUT)
    style.configure(
        "Primary.TButton",
        background=COLORS["btn_primary_bg"],
        foreground=COLORS["btn_disabled_fg"],
        padding=(16, 8),
        font=(_FONT, 10, "bold"),
        borderwidth=1,
        bordercolor=COLORS["btn_primary_bg"],
        relief="flat",
        focusthickness=0,
        anchor="center",
    )
    style.map(
        "Primary.TButton",
        background=[
            ("active", COLORS["btn_primary_hover"]),
            ("pressed", COLORS["btn_primary_active"]),
            ("disabled", COLORS["btn_disabled_bg"]),
        ],
        foreground=[("disabled", COLORS["btn_disabled_fg"])],
    )

    # 次按钮：中灰底、深色字、1px 边框（用于"回滚""设置""取消""最小化到托盘"）
    style.layout("Secondary.TButton", _BUTTON_LAYOUT)
    style.configure(
        "Secondary.TButton",
        background=COLORS["btn_secondary_bg"],
        foreground=COLORS["text"],
        padding=(16, 8),
        font=(_FONT, 10),
        borderwidth=1,
        bordercolor=COLORS["border"],
        relief="flat",
        focusthickness=0,
        anchor="center",
    )
    style.map(
        "Secondary.TButton",
        background=[
            ("active", COLORS["btn_secondary_hover"]),
            ("pressed", COLORS["btn_secondary_active"]),
            ("disabled", COLORS["btn_secondary_disabled_bg"]),
        ],
        foreground=[("disabled", COLORS["text_secondary"])],
    )

    # 默认 TButton 兜底为 Secondary 风格（保持旧风格按钮可用）
    style.layout("TButton", _BUTTON_LAYOUT)
    style.configure(
        "TButton",
        background=COLORS["btn_secondary_bg"],
        foreground=COLORS["text"],
        padding=(16, 8),
        font=(_FONT, 10),
        borderwidth=1,
        bordercolor=COLORS["border"],
        relief="flat",
        focusthickness=0,
        anchor="center",
    )
    style.map(
        "TButton",
        background=[
            ("active", COLORS["btn_secondary_hover"]),
            ("pressed", COLORS["btn_secondary_active"]),
            ("disabled", COLORS["btn_secondary_disabled_bg"]),
        ],
        foreground=[("disabled", COLORS["text_secondary"])],
    )

    # ---- 输入控件 ----
    style.configure(
        "TEntry",
        padding=(8, 6),
        borderwidth=1,
        relief="solid",
        bordercolor=COLORS["border"],
        fieldbackground=COLORS["card_bg"],
        foreground=COLORS["text"],
        insertcolor=COLORS["log_insert_bg"],
        selectbackground=COLORS["log_selection_bg"],
        selectforeground=COLORS["text"],
    )

    style.configure(
        "TCombobox",
        padding=(8, 6),
        borderwidth=1,
        relief="solid",
        bordercolor=COLORS["border"],
        fieldbackground=COLORS["card_bg"],
        foreground=COLORS["text"],
        arrowcolor=COLORS["text_secondary"],
    )

    # ---- 单选/复选 ----
    style.configure(
        "TRadiobutton",
        background=COLORS["card_bg"],
        foreground=COLORS["text"],
        font=(_FONT, 10),
        focusthickness=0,
    )
    style.configure(
        "TCheckbutton",
        background=COLORS["card_bg"],
        foreground=COLORS["text"],
        font=(_FONT, 10),
        focusthickness=0,
    )

    # ---- 分隔线 / Spinbox ----
    style.configure("TSeparator", background=COLORS["border"])
    style.configure(
        "TSpinbox",
        padding=4,
        fieldbackground=COLORS["card_bg"],
        bordercolor=COLORS["border"],
    )

    # ---- 垂直滚动条 ----
    style.configure(
        "Vertical.TScrollbar",
        background=COLORS["bg"],
        troughcolor=COLORS["bg"],
        bordercolor=COLORS["bg"],
        arrowcolor=COLORS["text_secondary"],
        gripcount=0,
    )

    return style


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class SyncMainWindow:
    """多Agent记忆融合器主窗口 —— Windows 桌面工具风格。"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AgentMemorySync")
        # 设置窗口图标
        if _ICON_PATH.exists():
            try:
                self.root.iconbitmap(str(_ICON_PATH))
            except Exception:
                pass

        # ---- 自适应窗口尺寸：小屏幕下缩小默认尺寸 ----
        # 避免 1366x768 笔记本（尤其 125% 缩放）下窗口超出屏幕
        saved_geo = load_settings().get("window_geometry", "")
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        if saved_geo:
            self.root.geometry(saved_geo)
        else:
            # 默认尺寸根据屏幕自适应
            if screen_w <= 1366 or screen_h <= 768:
                default_w, default_h = 760, 540
            else:
                default_w, default_h = 880, 620
            # 确保不超过屏幕工作区（预留任务栏 60px）
            max_w = screen_w - 40
            max_h = screen_h - 100
            default_w = min(default_w, max_w)
            default_h = min(default_h, max_h)
            self.root.geometry("{}x{}".format(default_w, default_h))

        # 最小尺寸也根据屏幕自适应：小屏幕降到 640x460
        if screen_w <= 1366 or screen_h <= 768:
            self.root.minsize(640, 460)
        else:
            self.root.minsize(720, 540)
        self.root.configure(bg=COLORS["bg"])

        self.settings = load_settings()
        self.is_syncing = False
        self.last_report = None
        self.sync_thread = None
        self._tray_nid = None  # Windows 原生托盘 NOTIFYICONDATA
        self._tray_hicon = None
        self._tray_hwnd = None
        self._tray_class_registered = False  # 窗口类是否已注册
        self._start_minimized = "--minimized" in sys.argv
        self._last_sync_time = 0  # timestamp of last sync
        self._tray_ignore_until = 0.0

        apply_modern_style(self.root)
        self._build_ui()

        # Windows 系统标题栏改成灰白配色（Win10 1903+ / Win11）
        self.root.update_idletasks()
        apply_dwm_caption_colors(self.root)

        # ---- 居中窗口（提前到 __init__ 避免 run() 时闪烁）----
        self._center_window()

        # 关闭按钮 → 最小化到托盘（而非退出）
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _center_window(self):
        """将主窗口居中显示，并确保不超出屏幕工作区。"""
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        # 预留任务栏空间（底部 60px）
        max_h = screen_h - 60
        if h > max_h:
            h = max_h
            self.root.geometry("{}x{}".format(w, h))
        x = (screen_w - w) // 2
        y = max(0, (screen_h - 60 - h) // 2)
        self.root.geometry("+{}+{}".format(x, y))

    def _build_ui(self):
        """构建 Windows 桌面工具风格 UI —— 两栏内容 + 内容头部。"""
        # === 内容头部：左侧标题 + 右侧状态 ===
        self._build_content_header()

        # === 主内容区 ===
        content = ttk.Frame(self.root, style="TFrame")
        content.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

        # 左列：日志卡片
        left_panel = ttk.Frame(content, style="TFrame")
        left_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_log_card(left_panel)

        # 右列：汇总 + 操作（固定宽度 260）
        right_panel = tk.Frame(content, width=260, bg=COLORS["bg"])
        right_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=(16, 0))
        right_panel.pack_propagate(False)

        self._build_summary_card(right_panel)
        self._build_actions_card(right_panel)

    def _build_content_header(self):
        """内容头部：完整内容区一部分，承载标题与状态。

        视觉上沿用项目原有"浅灰 + 主文本色"配色，等同之前 mac 版本自定义标题栏
        的色调，避免出现 Windows 原生深色标题栏带来的违和感。
        """
        header = tk.Frame(self.root, bg=COLORS["header_bg"], height=44)
        header.pack(fill=tk.X, side=tk.TOP)
        header.pack_propagate(False)

        # 左侧：应用标题（主文本色）
        self.title_label = tk.Label(
            header,
            text="AgentMemorySync",
            bg=COLORS["header_bg"],
            fg=COLORS["header_fg"],
            font=(_FONT, 14, "bold"),
            anchor="w",
            padx=16,
        )
        self.title_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 右侧：状态点 + 状态文字
        status_frame = tk.Frame(header, bg=COLORS["header_bg"])
        status_frame.pack(side=tk.RIGHT, padx=16)

        self.status_var = tk.StringVar(value="就绪")
        status_label = tk.Label(
            status_frame,
            textvariable=self.status_var,
            bg=COLORS["header_bg"],
            fg=COLORS["header_fg"],
            font=(_FONT, 10),
        )
        status_label.pack(side=tk.RIGHT)

        self.status_dot = tk.Label(
            status_frame,
            bg=COLORS["header_bg"], highlightthickness=0,
        )
        self.status_dot.pack(side=tk.RIGHT, padx=(0, 6))
        self._status_dot_img = None  # 保留 PhotoImage 引用避免 GC
        self._draw_status_dot(COLORS["status_ready"])

    def _build_log_card(self, parent):
        """日志卡片：白底 + 1px 边框 + 等宽字体白底日志。"""
        log_card = tk.Frame(
            parent, bg=COLORS["card_bg"], bd=0,
            highlightthickness=1, highlightbackground=COLORS["border"],
        )
        log_card.pack(fill=tk.BOTH, expand=True)

        log_header = tk.Frame(log_card, bg=COLORS["card_bg"])
        log_header.pack(fill=tk.X, padx=16, pady=(12, 8))
        ttk.Label(
            log_header, text="同步日志", style="CardTitle.TLabel"
        ).pack(side=tk.LEFT)

        self.log_text = tk.Text(
            log_card,
            height=8,
            state=tk.DISABLED,
            wrap=tk.WORD,
            font=("Consolas", 9),
            bg=COLORS["log_bg"],
            fg=COLORS["log_text"],
            relief="flat",
            padx=12,
            pady=10,
            selectbackground=COLORS["log_selection_bg"],
            selectforeground=COLORS["text"],
            insertbackground=COLORS["log_insert_bg"],
            highlightthickness=0,
            borderwidth=0,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=1, pady=(0, 1))

    def _build_summary_card(self, parent):
        """同步汇总卡片：白底 + 标签/数值两列。"""
        card = tk.Frame(
            parent, bg=COLORS["card_bg"], bd=0,
            highlightthickness=1, highlightbackground=COLORS["border"],
        )
        card.pack(fill=tk.X)

        # 标题
        header = tk.Frame(card, bg=COLORS["card_bg"])
        header.pack(fill=tk.X, padx=16, pady=(12, 8))
        ttk.Label(header, text="同步汇总", style="CardTitle.TLabel").pack(side=tk.LEFT)

        # 数值列表
        self.summary_widgets = []
        summary_fields = [
            ("agents", "发现 Agent", "0"),
            ("extracted", "提取记忆", "0"),
            ("merged", "融合共享", "0"),
            ("written", "写回", "0"),
            ("skipped", "跳过", "0"),
            ("duration", "耗时", "--"),
        ]
        rows = tk.Frame(card, bg=COLORS["card_bg"])
        rows.pack(fill=tk.X, padx=16, pady=(0, 16))

        for key, label, default in summary_fields:
            row_frame = tk.Frame(rows, bg=COLORS["card_bg"])
            row_frame.pack(fill=tk.X, pady=4)

            ttk.Label(row_frame, text=label, style="StatLabel.TLabel").pack(side=tk.LEFT)
            val_label = ttk.Label(row_frame, text=default, style="Stat.TLabel")
            val_label.pack(side=tk.RIGHT)
            self.summary_widgets.append((key, val_label))

    def _build_actions_card(self, parent):
        """操作卡片：4 个统一按钮，纵向排列、宽度一致。"""
        card = tk.Frame(
            parent, bg=COLORS["card_bg"], bd=0,
            highlightthickness=1, highlightbackground=COLORS["border"],
        )
        card.pack(fill=tk.X, pady=(16, 0))

        # 标题
        header = tk.Frame(card, bg=COLORS["card_bg"])
        header.pack(fill=tk.X, padx=16, pady=(12, 8))
        ttk.Label(header, text="操作", style="CardTitle.TLabel").pack(side=tk.LEFT)

        btn_box = tk.Frame(card, bg=COLORS["card_bg"])
        btn_box.pack(fill=tk.X, padx=16, pady=(0, 16))

        # 4 个按钮：主按钮 + 3 个次按钮，纵向等距
        self.run_btn = ttk.Button(
            btn_box, text="立即同步", style="Primary.TButton", command=self._start_sync,
        )
        self.run_btn.pack(fill=tk.X, pady=(0, 8), ipady=4)

        self.rollback_btn = ttk.Button(
            btn_box, text="回滚上次", style="Secondary.TButton", command=self._rollback,
        )
        self.rollback_btn.pack(fill=tk.X, pady=(0, 8), ipady=4)

        self.settings_btn = ttk.Button(
            btn_box, text="设置", style="Secondary.TButton", command=self._open_settings,
        )
        self.settings_btn.pack(fill=tk.X, pady=(0, 8), ipady=4)

        self.minimize_btn = ttk.Button(
            btn_box, text="最小化到托盘", style="Secondary.TButton", command=self._minimize_to_tray,
        )
        self.minimize_btn.pack(fill=tk.X, ipady=4)

    def _make_status_dot_image(self, color: str, size: int = 16):
        """用 PIL 绘制抗锯齿圆点，超采样 4x 后缩小，彻底消除锯齿。"""
        try:
            from PIL import Image, ImageDraw, ImageTk
        except ImportError:
            return None
        scale = 4
        big = size * scale
        img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        margin = 2 * scale
        draw.ellipse(
            [margin, margin, big - margin, big - margin],
            fill=color, outline=(255, 255, 255, 180), width=scale,
        )
        img = img.resize((size, size), Image.LANCZOS)
        return ImageTk.PhotoImage(img)

    def _draw_status_dot(self, color: str):
        """更新状态指示点（PIL 抗锯齿圆，16x16）。"""
        img = self._make_status_dot_image(color, size=16)
        if img is not None:
            self._status_dot_img = img  # 保留引用避免 GC
            self.status_dot.config(image=img, text="")
        else:
            # PIL 不可用时回退到文字圆点
            self.status_dot.config(image="", text="●", fg=color,
                                   font=(_FONT, 12), bg=COLORS["header_bg"])

    def _log(self, msg: str):
        """向日志面板追加消息"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = "[{}] {}\n".format(timestamp, msg)

        def _append():
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, line)
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)

        if threading.current_thread() is threading.main_thread():
            _append()
        else:
            self.root.after(0, _append)

    def _update_summary(self, report):
        """更新汇总面板"""
        def _do():
            values = {
                "agents": str(len(report.agents_detected)),
                "extracted": str(report.total_extracted),
                "merged": str(report.total_merged),
                "written": str(report.total_written),
                "skipped": str(report.total_skipped),
                "duration": "{:.1f}s".format(report.duration_seconds),
            }
            for key, label in self.summary_widgets:
                label.config(text=values.get(key, "--"))

        if threading.current_thread() is threading.main_thread():
            _do()
        else:
            self.root.after(0, _do)

    def _start_sync(self):
        """开始同步（在后台线程执行）"""
        if self.is_syncing:
            return

        self.is_syncing = True
        self.run_btn.config(state=tk.DISABLED)
        self.rollback_btn.config(state=tk.DISABLED)
        self.status_var.set("同步中...")
        self._draw_status_dot(COLORS["status_running"])

        # 清空日志
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

        def _sync_thread():
            try:
                from sync_engine import SyncEngine
                engine = SyncEngine(on_progress=self._log)
                report = engine.run()
                self.last_report = report

                self._log("")
                self._log(report.summary_text())
                self._update_summary(report)

                if report.errors:
                    self.root.after(0, lambda: self.status_var.set(
                        "完成 ({} 个错误)".format(len(report.errors))
                    ))
                    self.root.after(0, lambda: self._draw_status_dot(COLORS["status_error"]))
                else:
                    self.root.after(0, lambda: self.status_var.set("同步完成"))
                    self.root.after(0, lambda: self._draw_status_dot(COLORS["status_ready"]))

                # 发送托盘气泡通知
                notify_body = "设备: {} | Agent: {} 个 | 提取: {} 条 | 写回: {} 条".format(
                    getattr(report, 'device', 'unknown'),
                    len(getattr(report, 'agents_found', [])),
                    report.total_extracted,
                    report.total_written
                )
                if report.errors:
                    notify_body += " | 错误: {} 个".format(len(report.errors))
                self._notify("AgentMemorySync", notify_body)

            except Exception as e:
                self._log("同步失败: {}".format(e))
                self.root.after(0, lambda: self.status_var.set("同步失败"))
                self.root.after(0, lambda: self._draw_status_dot(COLORS["status_error"]))
            finally:
                self._last_sync_time = time.time()
                self.is_syncing = False
                self.root.after(0, lambda: self.run_btn.config(state=tk.NORMAL))
                self.root.after(0, lambda: self.rollback_btn.config(state=tk.NORMAL))

        self.sync_thread = threading.Thread(target=_sync_thread, daemon=True)
        self.sync_thread.start()

    def _schedule_next_sync(self):
        """定时自动同步调度器"""
        import time as _time
        interval_hours = self.settings.get("auto_interval_hours", 2)
        interval_seconds = interval_hours * 3600

        def _check():
            if not self.settings.get("auto_start", False):
                # 未启用自动同步，继续检查
                self.root.after(60000, _check)
                return
            if self.is_syncing:
                self.root.after(60000, _check)
                return
            elapsed = _time.time() - self._last_sync_time
            if elapsed >= interval_seconds:
                self._log("[自动同步] 距离上次同步 {:.0f} 分钟，触发同步".format(elapsed / 60))
                self._start_sync()
            self.root.after(60000, _check)

        # 首次检查延迟 60 秒
        self.root.after(60000, _check)

    def _rollback(self):
        """回滚上次同步"""
        if self.is_syncing:
            return

        if not messagebox.askyesno("确认回滚", "确定要回滚上次同步吗？\n这将恢复被修改的文件。"):
            return

        self.status_var.set("回滚中...")
        self._draw_status_dot(COLORS["status_running"])

        def _rollback_thread():
            try:
                from sync_engine import SyncEngine
                engine = SyncEngine(on_progress=self._log)
                restored = engine.rollback()
                self._log("回滚完成, 恢复 {} 个文件".format(restored))
                self.root.after(0, lambda: self.status_var.set("回滚完成"))
                self.root.after(0, lambda: self._draw_status_dot(COLORS["status_ready"]))
            except Exception as e:
                self._log("回滚失败: {}".format(e))
                self.root.after(0, lambda: self.status_var.set("回滚失败"))
                self.root.after(0, lambda: self._draw_status_dot(COLORS["status_error"]))

        threading.Thread(target=_rollback_thread, daemon=True).start()

    def _open_settings(self):
        """打开设置窗口"""
        SettingsDialog(self.root, self.settings, self._on_settings_saved)

    def _on_settings_saved(self, new_settings):
        """设置保存回调"""
        self.settings = new_settings
        save_settings(new_settings)
        # 验证写入成功
        try:
            saved = load_settings()
            if saved.get("auto_interval_hours") != new_settings.get("auto_interval_hours"):
                self._log("⚠ 设置保存验证失败，文件可能未更新")
            else:
                self._log("设置已保存 (间隔={}小时)".format(saved.get("auto_interval_hours")))
        except Exception:
            self._log("设置已保存")

    def _minimize_to_tray(self):
        """最小化到系统托盘（Windows 原生 API）"""
        _tray_log = _data_dir() / "tray_error.log"
        _tray_log.parent.mkdir(parents=True, exist_ok=True)

        self._log("正在最小化到系统托盘...")

        tray_ok = False
        try:
            tray_ok = self._create_tray_icon()
        except Exception as e:
            import traceback
            try:
                with open(_tray_log, "a", encoding="utf-8") as f:
                    f.write("FAIL: tray create error: {}\n".format(e))
                    traceback.print_exc(file=f)
            except OSError:
                pass

        # 托盘创建成功后再隐藏主窗口；失败时保留窗口，避免"程序不见了"的错觉
        if tray_ok:
            try:
                self.root.withdraw()
            except Exception:
                pass
            self._log("已最小化到托盘")
            try:
                self._notify("AgentMemorySync", "I'm here")
                notify_status = "notify=OK"
            except Exception as e:
                notify_status = "notify_fail={}".format(e)

            try:
                with open(_tray_log, "a", encoding="utf-8") as f:
                    f.write("OK: tray created\n")
                    f.write("  hwnd={} hicon={} nid={}\n".format(
                        self._tray_hwnd, self._tray_hicon, self._tray_nid is not None))
                    f.write("  {}\n".format(notify_status))
                    f.write("  提示: Win11 托盘图标可能在溢出区(^箭头)，可拖到任务栏可见区域\n")
            except OSError:
                pass

        else:
            self._log("托盘图标创建失败，窗口保持显示")
            # 托盘失败时不隐藏窗口，让用户能继续操作；同时弹出提示
            try:
                self._notify("AgentMemorySync",
                             "托盘图标创建失败，窗口保持显示。请查看 tray_error.log")
            except Exception:
                pass
            try:
                with open(_tray_log, "a", encoding="utf-8") as f:
                    f.write("WARN: tray icon unavailable, window stays visible\n")
            except OSError:
                pass
            try:
                msg = (
                    "系统托盘图标创建失败，窗口保持显示。\n\n"
                    "可能原因：当前 EXE 位于 OneDrive 目录，系统限制了托盘 API。\n\n"
                    "解决方案（任选其一）：\n"
                    "1. 使用 build.py 打包后生成的桌面快捷方式启动\n"
                    "2. 手动复制整个 AgentMemorySync/ 目录到本地非 OneDrive 位置\n"
                    "3. 从该本地目录双击 AgentMemorySync.exe 启动\n\n"
                    "如果仍有问题，请检查 tray_error.log 并把内容反馈给我。"
                )
                messagebox.showwarning("托盘图标未创建", msg)
            except Exception:
                pass

    def _create_tray_icon(self):
        """用 Windows 原生 Shell_NotifyIconW 创建系统托盘图标

        Returns:
            bool: True 表示托盘图标创建成功
        """
        if self._tray_nid is not None:
            return True

        _tray_log = _data_dir() / "tray_error.log"

        # 加载图标
        self._tray_hicon = None
        icon_path_used = None
        if _ICON_PATH.exists():
            self._tray_hicon = _load_icon_from_file(str(_ICON_PATH))
            if self._tray_hicon:
                icon_path_used = str(_ICON_PATH)
        if self._tray_hicon is None and _TRAY_ICON_PATH.exists():
            self._tray_hicon = _load_icon_from_file(str(_TRAY_ICON_PATH))
            if self._tray_hicon:
                icon_path_used = str(_TRAY_ICON_PATH)
        if self._tray_hicon is None:
            self._tray_hicon = _create_default_icon()
            if self._tray_hicon:
                icon_path_used = "default"

        try:
            with open(_tray_log, "a", encoding="utf-8") as f:
                f.write("DEBUG: icon_path={} hicon={}\n".format(icon_path_used, self._tray_hicon))
        except OSError:
            pass

        if not self._tray_hicon:
            self._log("⚠ 无法加载托盘图标，将使用系统默认图标")
        else:
            self._log("托盘图标已加载: {}".format(icon_path_used))

        # 创建独立的隐藏 Win32 窗口来接收托盘消息
        self._log("正在创建托盘消息窗口...")
        self._tray_hwnd = self._create_message_window()
        if not self._tray_hwnd:
            err = ctypes.windll.kernel32.GetLastError()
            self._log("⚠ 托盘消息窗口创建失败 (error={})".format(err))
            try:
                with open(_tray_log, "a", encoding="utf-8") as f:
                    f.write("DEBUG: message window creation failed last_error={}\n".format(err))
            except OSError:
                pass
            return False

        self._log("托盘消息窗口已创建")
        try:
            with open(_tray_log, "a", encoding="utf-8") as f:
                f.write("DEBUG: message window created hwnd={}\n".format(self._tray_hwnd))
        except OSError:
            pass

        # 先删除同一 hWnd/uID 的旧图标，避免重复注册或脏状态
        del_nid = _NOTIFYICONDATAW()
        del_nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        del_nid.hWnd = ctypes.wintypes.HWND(self._tray_hwnd)
        del_nid.uID = 1
        _shell32.Shell_NotifyIconW(_NIM_DELETE, ctypes.byref(del_nid))

        # 构建 NOTIFYICONDATA（Vista+ 大小 968，使用 uID 标识）
        nid = _NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        nid.hWnd = ctypes.wintypes.HWND(self._tray_hwnd)
        nid.uID = 1
        nid.uFlags = _NIF_MESSAGE | _NIF_ICON | _NIF_TIP | _NIF_SHOWTIP
        nid.uCallbackMessage = _WM_TRAYICON
        nid.hIcon = ctypes.wintypes.HICON(self._tray_hicon) if self._tray_hicon else None
        nid.szTip = "多Agent记忆融合器"
        self._tray_nid = nid

        # 注册托盘图标
        self._log("正在注册系统托盘图标...")
        ok = _shell32.Shell_NotifyIconW(_NIM_ADD, ctypes.byref(nid))
        err = ctypes.windll.kernel32.GetLastError()
        try:
            with open(_tray_log, "a", encoding="utf-8") as f:
                f.write("DEBUG: Shell_NotifyIconW add={} last_error={} hwnd={} cbSize={} hicon={}\n".format(
                    ok, err, self._tray_hwnd, nid.cbSize, self._tray_hicon))
        except OSError:
            pass

        if not ok:
            self._tray_nid = None
            self._log("⚠ 系统托盘图标注册失败 (error={})".format(err))
            return False

        self._log("系统托盘图标注册成功")
        self._tray_ignore_until = time.time() + 2.0
        # 启动消息泵轮询
        self._pump_tray_messages()
        return True

    def _pump_tray_messages(self):
        """定期从隐藏窗口的消息队列中取出并分发消息"""
        if self._tray_nid is None:
            return
        msg = ctypes.wintypes.MSG()
        while _user32.PeekMessageW(ctypes.byref(msg), self._tray_hwnd, 0, 0, 1):  # PM_REMOVE=1
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
        # 在 tkinter 主线程中处理点击动作，避免在 ctypes 回调里调用 tkinter
        action = getattr(self, "_tray_click_action", None)
        if action:
            self._tray_click_action = None
            try:
                _tray_log = _data_dir() / "tray_error.log"
                with open(_tray_log, "a", encoding="utf-8") as f:
                    f.write("DEBUG: pump processing action={}\n".format(action))
            except Exception:
                pass
            if action == "show":
                self._show_window()
            elif action == "menu":
                self._show_tray_menu()
        # 每 50ms 轮询一次
        self._tray_after_id = self.root.after(50, self._pump_tray_messages)

    def _create_message_window(self):
        """创建一个隐藏的 Win32 消息窗口来接收托盘回调"""
        _tray_log = _data_dir() / "tray_error.log"

        WNDPROC = ctypes.WINFUNCTYPE(
            _LPARAM, ctypes.wintypes.HWND, ctypes.wintypes.UINT,
            _WPARAM, _LPARAM
        )

        # 保存为实例属性防止被 GC（关键：旧 wndproc 被 GC 后窗口类指向野指针）
        self._wndproc = WNDPROC(self._tray_wndproc)

        # 获取 hInstance 并单独保存（从 struct 读出会变成超大 int 导致溢出）
        self._hInstance = _kernel32.GetModuleHandleW(None)

        # 注册窗口类（处理崩溃残留：若类已存在则先注销再注册）
        if not self._tray_class_registered:
            wnd_class = _WNDCLASSW()
            wnd_class.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
            wnd_class.hInstance = self._hInstance
            wnd_class.lpszClassName = _TRAY_CLASS_NAME
            atom = _user32.RegisterClassW(ctypes.byref(wnd_class))
            reg_err = ctypes.windll.kernel32.GetLastError()
            if atom:
                self._tray_class_registered = True
                try:
                    with open(_tray_log, "a", encoding="utf-8") as f:
                        f.write("DEBUG: RegisterClassW OK atom={}\n".format(atom))
                except OSError:
                    pass
            else:
                try:
                    with open(_tray_log, "a", encoding="utf-8") as f:
                        f.write("DEBUG: RegisterClassW failed error={}; try unregister\n".format(reg_err))
                except OSError:
                    pass
                # 类已存在（上次崩溃未清理），注销旧类后重新注册
                _user32.UnregisterClassW(_TRAY_CLASS_NAME, ctypes.c_void_p(self._hInstance))
                atom = _user32.RegisterClassW(ctypes.byref(wnd_class))
                reg_err2 = ctypes.windll.kernel32.GetLastError()
                if atom:
                    self._tray_class_registered = True
                    try:
                        with open(_tray_log, "a", encoding="utf-8") as f:
                            f.write("DEBUG: RegisterClassW retry OK atom={}\n".format(atom))
                    except OSError:
                        pass
                else:
                    try:
                        with open(_tray_log, "a", encoding="utf-8") as f:
                            f.write("DEBUG: RegisterClassW retry failed error={}\n".format(reg_err2))
                    except OSError:
                        pass

        hwnd = _user32.CreateWindowExW(
            0, _TRAY_CLASS_NAME, _TRAY_CLASS_NAME,
            0, 0, 0, 0, 0,              # dwStyle, x, y, nWidth, nHeight
            _HWND_MESSAGE,             # hWndParent: 消息专用窗口，不显示也不占任务栏
            ctypes.wintypes.HMENU(0),  # hMenu
            ctypes.c_void_p(self._hInstance),  # hInstance
            None,                       # lpParam
        )
        cw_err = ctypes.windll.kernel32.GetLastError()
        try:
            with open(_tray_log, "a", encoding="utf-8") as f:
                f.write("DEBUG: CreateWindowExW hwnd={} error={}\n".format(hwnd, cw_err))
        except OSError:
            pass
        return hwnd

    def _tray_wndproc(self, hwnd, msg, wparam, lparam):
        """托盘消息窗口的窗口过程"""
        try:
            if msg == _WM_TRAYICON:
                try:
                    _tray_log = _data_dir() / "tray_error.log"
                    with open(_tray_log, "a", encoding="utf-8") as f:
                        f.write("DEBUG: tray msg lparam={}\n".format(lparam))
                except Exception:
                    pass
                if lparam == _WM_LBUTTONUP:
                    if time.time() < getattr(self, "_tray_ignore_until", 0.0):
                        return 0
                    self._tray_click_action = "show"
                    return 0
                elif lparam == _WM_RBUTTONUP:
                    if time.time() < getattr(self, "_tray_ignore_until", 0.0):
                        return 0
                    self._tray_click_action = "menu"
                    return 0
            elif msg == _WM_TASKBARCREATED:
                # 资源管理器重启后恢复托盘图标
                if self._tray_nid:
                    _shell32.Shell_NotifyIconW(_NIM_ADD, ctypes.byref(self._tray_nid))
                return 0
        except Exception as e:
            try:
                _tray_log = _data_dir() / "tray_error.log"
                with open(_tray_log, "a", encoding="utf-8") as f:
                    import traceback
                    f.write("ERROR: wndproc exception: {}\n".format(e))
                    traceback.print_exc(file=f)
            except Exception:
                pass
        return int(_user32.DefWindowProcW(hwnd, msg, wparam, lparam))

    def _show_tray_menu(self):
        """显示托盘右键菜单"""
        try:
            _tray_log = _data_dir() / "tray_error.log"
            with open(_tray_log, "a", encoding="utf-8") as f:
                f.write("DEBUG: show tray menu\n")
        except Exception:
            pass

        try:
            # 窗口被 withdraw 时 tk_popup 可能行为异常，先恢复再弹出
            was_withdrawn = str(self.root.wm_state()) == "withdrawn"
            if was_withdrawn:
                self.root.deiconify()
                self.root.update_idletasks()

            menu = tk.Menu(self.root, tearoff=0, font=(_FONT, 10))
            menu.add_command(label="显示主窗口", command=self._show_window)
            menu.add_command(label="立即同步", command=self._tray_sync)
            menu.add_separator()
            menu.add_command(label="设置", command=self._tray_settings)
            menu.add_separator()
            menu.add_command(label="退出", command=self._quit)

            # 在鼠标位置弹出
            try:
                x = self.root.winfo_pointerx()
                y = self.root.winfo_pointery()
                menu.tk_popup(x, y)
            finally:
                menu.grab_release()

            # 如果弹出前是隐藏状态且用户没选"显示主窗口"等会恢复窗口的命令，
            # 这里不再自动隐藏，避免窗口闪烁；由用户通过菜单命令控制。
        except Exception as e:
            try:
                _tray_log = _data_dir() / "tray_error.log"
                with open(_tray_log, "a", encoding="utf-8") as f:
                    import traceback
                    f.write("ERROR: show tray menu failed: {}\n".format(e))
                    traceback.print_exc(file=f)
            except Exception:
                pass

    def _show_window(self, icon=None, item=None):
        """显示主窗口"""
        try:
            _tray_log = _data_dir() / "tray_error.log"
            with open(_tray_log, "a", encoding="utf-8") as f:
                f.write("DEBUG: show window called\n")
        except Exception:
            pass
        try:
            self.root.after(100, self._deiconify_and_remove_tray)
        except Exception as e:
            try:
                _tray_log = _data_dir() / "tray_error.log"
                with open(_tray_log, "a", encoding="utf-8") as f:
                    import traceback
                    f.write("ERROR: show window failed: {}\n".format(e))
                    traceback.print_exc(file=f)
            except Exception:
                pass

    def _deiconify_and_remove_tray(self):
        """恢复窗口并移除托盘图标"""
        try:
            _tray_log = _data_dir() / "tray_error.log"
            with open(_tray_log, "a", encoding="utf-8") as f:
                f.write("DEBUG: deiconify and remove tray\n")
        except Exception:
            pass
        # 先移除托盘图标，再显示窗口，避免消息泵和窗口销毁冲突
        try:
            self._remove_tray_icon()
        except Exception as e:
            try:
                _tray_log = _data_dir() / "tray_error.log"
                with open(_tray_log, "a", encoding="utf-8") as f:
                    import traceback
                    f.write("ERROR: remove tray icon failed: {}\n".format(e))
                    traceback.print_exc(file=f)
            except Exception:
                pass
        try:
            self.root.deiconify()
        except Exception as e:
            try:
                _tray_log = _data_dir() / "tray_error.log"
                with open(_tray_log, "a", encoding="utf-8") as f:
                    import traceback
                    f.write("ERROR: deiconify failed: {}\n".format(e))
                    traceback.print_exc(file=f)
            except Exception:
                pass

    def _remove_tray_icon(self):
        """移除系统托盘图标"""
        # 先停止消息泵，避免销毁窗口时还在 PeekMessage
        try:
            if hasattr(self, "_tray_after_id") and self._tray_after_id:
                self.root.after_cancel(self._tray_after_id)
                self._tray_after_id = None
        except Exception:
            pass
        if self._tray_nid is not None:
            _shell32.Shell_NotifyIconW(_NIM_DELETE, ctypes.byref(self._tray_nid))
            self._tray_nid = None
        if self._tray_hicon:
            _user32.DestroyIcon(self._tray_hicon)
            self._tray_hicon = None
        if self._tray_hwnd:
            _user32.DestroyWindow(self._tray_hwnd)
            self._tray_hwnd = None
        # 注销窗口类，允许下次重新注册（旧 wndproc 可能已被 GC）
        if self._tray_class_registered:
            _user32.UnregisterClassW(_TRAY_CLASS_NAME, ctypes.c_void_p(self._hInstance))
            self._tray_class_registered = False

    def _tray_sync(self, icon=None, item=None):
        """从托盘触发同步"""
        self.root.after(0, self._deiconify_and_remove_tray)
        self.root.after(100, self._start_sync)

    def _tray_settings(self, icon=None, item=None):
        """从托盘打开设置"""
        self.root.after(0, self._deiconify_and_remove_tray)
        self.root.after(100, self._open_settings)

    def _quit(self, icon=None, item=None):
        """退出程序"""
        self._remove_tray_icon()
        self.root.after(0, self.root.destroy)

    def _on_close(self):
        """关闭窗口"""
        self._log("点击关闭按钮，根据设置最小化到托盘...")
        try:
            if self.settings.get("minimize_to_tray", True):
                self._minimize_to_tray()
            else:
                if messagebox.askyesno("退出", "确定退出多Agent记忆融合器？"):
                    self._quit()
        except Exception as e:
            self._log("关闭处理异常: {}".format(e))
            # 任何异常都直接退出，避免窗口卡死
            self._quit()

    def _notify(self, title: str, body: str):
        """发送 Windows Toast 通知"""
        # 方案1: Shell_NotifyIconW 气泡通知（托盘图标存在时最可靠）
        if self._tray_nid is not None and self._tray_hwnd:
            try:
                # 复用缓存的 nid，保留 uCallbackMessage 和 hIcon 字段
                nid = self._tray_nid
                nid.uFlags = _NIF_INFO
                nid.szInfoTitle = title[:63]
                nid.szInfo = body[:255]
                nid.dwInfoFlags = _NIIF_INFO
                nid.uTimeout = 2000  # 2 秒（Vista+ 系统可能忽略，但设置无害）
                ok = _shell32.Shell_NotifyIconW(_NIM_MODIFY, ctypes.byref(nid))
                # 重置 uFlags，避免影响后续操作
                nid.uFlags = _NIF_MESSAGE | _NIF_ICON | _NIF_TIP | _NIF_SHOWTIP
                if ok:
                    return
            except Exception:
                pass

        # 方案2: PowerShell Toast 通知（无托盘图标时使用）
        try:
            xml_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            xml_body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            ps_title = xml_title.replace("'", "''")
            ps_body = xml_body.replace("'", "''")
            # duration="short" 显式短时长（~3 秒），audio silent 静音
            ps_script = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
                "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] > $null; "
                f"$template = '<toast duration=\"short\"><visual><binding template=\"ToastText02\">"
                f"<text id=\"1\">{ps_title}</text>"
                f"<text id=\"2\">{ps_body}</text>"
                f"</binding></visual><audio src=\"ms-winsoundevent:Notification.Default\" silent=\"true\"/></toast>'; "
                "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument; "
                "$xml.LoadXml($template); "
                "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('AgentMemorySync').Show($toast)"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                creationflags=0x08000000
            )
        except Exception:
            pass

    def run(self):
        """启动主循环"""
        # 居中逻辑已移到 __init__ 的 _center_window(),这里不再重复
        # 自动同步：启动时执行一次 + 定时循环
        if self.settings.get("auto_start", False):
            self.root.after(1000, self._start_sync)

        # 启动时最小化到托盘（--minimized 参数）
        if self._start_minimized:
            self.root.after(500, self._minimize_to_tray)

        # 正常显示窗口启动时，不预创建托盘图标；仅在最小化到托盘时再创建。

        # 定时自动同步调度器（每 60 秒检查一次）
        self._schedule_next_sync()

        self.root.mainloop()


# ---------------------------------------------------------------------------
# 设置对话框
# ---------------------------------------------------------------------------

class SettingsDialog:
    """设置对话框 —— Windows 桌面工具风格，三段式布局（顶部说明 + 滚动表单 + 底部操作）。

    - 顶部：标题 + 副标题说明
    - 中部：Canvas + Scrollbar + 卡片，承载常见设置 + 高级路径覆盖
    - 底部：固定操作区，取消 / 保存（保存=主按钮，取消=次按钮）
    """

    def __init__(self, parent, settings: dict, on_save):
        self.settings = settings.copy()
        # 保存原始设置，用于检测是否有改动
        self._original_settings = settings.copy()
        self.on_save = on_save

        self.win = tk.Toplevel(parent)
        self.win.title("设置")
        self.win.geometry("500x600")
        self.win.minsize(480, 520)
        self.win.transient(parent)
        self.win.grab_set()
        self.win.configure(bg=COLORS["bg"])

        self._build()

        # Windows 系统标题栏改成灰白配色（Win10 1903+ / Win11）
        self.win.update_idletasks()
        apply_dwm_caption_colors(self.win)

        # 点 X 关闭时先询问是否保存改动
        self.win.protocol("WM_DELETE_WINDOW", self._on_window_close)

        # 居中
        self.win.update_idletasks()
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        screen_h = self.win.winfo_screenheight()
        max_h = int(screen_h * 0.85)
        if h > max_h:
            h = max_h
            self.win.geometry("{}x{}".format(w, h))
            self.win.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - w) // 2
        y = parent.winfo_y() + (parent.winfo_height() - h) // 2
        self.win.geometry("+{}+{}".format(x, y))

    def _build(self):
        # === 顶部说明区 ===
        header = tk.Frame(self.win, bg=COLORS["bg"])
        header.pack(fill=tk.X, padx=20, pady=(16, 12))

        ttk.Label(header, text="设置", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(
            header,
            text="管理自动同步、冲突处理和 Agent 路径覆盖",
            style="Subtitle.TLabel",
        ).pack(anchor=tk.W, pady=(2, 0))

        # === 中部滚动内容 ===
        scroll_box = tk.Frame(self.win, bg=COLORS["bg"])
        scroll_box.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 0))

        canvas = tk.Canvas(
            scroll_box, bg=COLORS["bg"], highlightthickness=0, borderwidth=0,
        )
        scrollbar = ttk.Scrollbar(
            scroll_box, orient="vertical", command=canvas.yview,
            style="Vertical.TScrollbar",
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Canvas 内卡片
        card = tk.Frame(
            canvas, bg=COLORS["card_bg"], bd=0,
            highlightthickness=1, highlightbackground=COLORS["border"],
        )
        inner = tk.Frame(card, bg=COLORS["card_bg"])
        inner.pack(fill=tk.BOTH, expand=True, padx=20, pady=16)

        canvas_window = canvas.create_window(0, 0, window=card, anchor="nw")

        def _on_canvas_resize(_evt):
            canvas.itemconfigure(canvas_window, width=canvas.winfo_width())
            canvas.config(scrollregion=canvas.bbox("all"))
        canvas.bind("<Configure>", _on_canvas_resize)
        card.bind(
            "<Configure>",
            lambda e: canvas.config(scrollregion=canvas.bbox("all")),
        )

        # 鼠标滚轮支持
        def _on_wheel(event):
            delta = -1 if event.delta > 0 else 1
            canvas.yview_scroll(delta, "units")
        canvas.bind_all("<MouseWheel>", _on_wheel)
        self._canvas = canvas  # 防止被 GC
        self._wheel_handler = _on_wheel

        # === 常用配置区 ===

        # 自动同步间隔
        row = tk.Frame(inner, bg=COLORS["card_bg"])
        row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(row, text="自动同步间隔", style="Card.TLabel").pack(side=tk.LEFT)
        hour_options = ["1", "2", "4", "8", "16", "24", "48", "72"]
        current_hours = str(self.settings.get("auto_interval_hours", 2))
        if current_hours not in hour_options:
            current_hours = "2"
        self.interval_var = tk.StringVar(value=current_hours)
        combo = ttk.Combobox(
            row, values=hour_options, textvariable=self.interval_var,
            state="readonly", font=(_FONT, 10), width=5,
        )
        combo.pack(side=tk.RIGHT)
        ttk.Label(row, text="小时", style="Card.TLabel").pack(side=tk.RIGHT, padx=(0, 8))

        ttk.Separator(inner, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=12)

        # OneDrive 冲突处理
        ttk.Label(inner, text="OneDrive 冲突时", style="Card.TLabel").pack(anchor=tk.W, pady=(0, 8))
        self.conflict_var = tk.StringVar(value=self.settings.get("conflict_action", "prompt"))
        conflict_frame = tk.Frame(inner, bg=COLORS["card_bg"])
        conflict_frame.pack(anchor=tk.W, pady=(0, 8))
        ttk.Radiobutton(conflict_frame, text="提示我", variable=self.conflict_var, value="prompt").pack(side=tk.LEFT)
        ttk.Radiobutton(conflict_frame, text="自动跳过", variable=self.conflict_var, value="skip").pack(side=tk.LEFT, padx=(20, 0))

        ttk.Separator(inner, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=12)

        # 选项
        self.tray_var = tk.BooleanVar(value=self.settings.get("minimize_to_tray", True))
        ttk.Checkbutton(
            inner, text="关闭窗口时最小化到托盘", variable=self.tray_var,
        ).pack(anchor=tk.W, pady=4)

        self.auto_start_var = tk.BooleanVar(value=self.settings.get("auto_start", False))
        ttk.Checkbutton(
            inner, text="启动时自动执行同步", variable=self.auto_start_var,
        ).pack(anchor=tk.W, pady=4)

        # === 高级区域（折叠） ===
        ttk.Separator(inner, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=12)
        self._advanced_label_text = "高级 ▾  Agent 路径覆盖（展开可手动填，留空=自动检测）"
        self._advanced_label = tk.Label(
            inner,
            text=self._advanced_label_text,
            bg=COLORS["card_bg"],
            fg=COLORS["text"],
            font=(_FONT, 11, "bold"),
            cursor="hand2",
        )
        self._advanced_label.pack(anchor=tk.W, pady=(4, 6))
        self._advanced_label.bind("<Button-1>", self._toggle_advanced)

        self._advanced_frame = tk.Frame(inner, bg=COLORS["card_bg"])
        self.sw_advanced = False  # 默认折叠

        overrides = self.settings.get("agent_overrides", {})
        self.override_vars = {}

        try:
            _config_file = Path(__file__).parent / "config.json"
            with open(_config_file, "r", encoding="utf-8") as f:
                app_config = json.load(f)
            agent_list = list(app_config.get("agent_detection", {}).keys())
        except Exception:
            agent_list = list(overrides.keys())

        # === 预置 Agent 区 ===
        ttk.Label(
            self._advanced_frame, text="预置 Agent（留空=自动检测）",
            style="Card.TLabel", font=(_FONT, 9, "bold"),
        ).pack(anchor=tk.W, pady=(4, 2))
        for agent in agent_list:
            row = tk.Frame(self._advanced_frame, bg=COLORS["card_bg"])
            row.pack(fill=tk.X, pady=4)
            ttk.Label(row, text="{}:".format(agent), style="Card.TLabel", width=12).pack(side=tk.LEFT)
            var = tk.StringVar(value=overrides.get(agent, ""))
            entry = ttk.Entry(row, textvariable=var, font=(_FONT, 10))
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
            self.override_vars[agent] = var

        # === 自定义 Agent 区 ===
        ttk.Separator(self._advanced_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        ttk.Label(
            self._advanced_frame, text="自定义 Agent（手动填名称和路径）",
            style="Card.TLabel", font=(_FONT, 9, "bold"),
        ).pack(anchor=tk.W, pady=(2, 4))

        # 自定义 agent 容器（动态行）
        self._custom_rows = []  # [(frame, name_var, path_var), ...]
        self._custom_container = tk.Frame(self._advanced_frame, bg=COLORS["card_bg"])
        self._custom_container.pack(fill=tk.X)

        # 加载已保存的自定义 agent（overrides 里不在 agent_list 中的 key）
        for name, path_val in overrides.items():
            if name not in agent_list and path_val:
                self._add_custom_row(name, path_val)

        # "+ 添加自定义 Agent" 按钮
        ttk.Button(
            self._advanced_frame, text="+ 添加自定义 Agent", style="Secondary.TButton",
            command=self._add_custom_row,
        ).pack(anchor=tk.W, pady=4)

        # === 底部固定操作区 ===
        ttk.Separator(self.win, orient=tk.HORIZONTAL).pack(fill=tk.X)

        btn_bar = tk.Frame(self.win, bg=COLORS["bg"])
        btn_bar.pack(fill=tk.X, padx=20, pady=(12, 16))

        ttk.Button(
            btn_bar, text="取消", style="Secondary.TButton",
            command=self._close,
        ).pack(side=tk.RIGHT, ipady=3, padx=(8, 0))

        ttk.Button(
            btn_bar, text="保存", style="Primary.TButton",
            command=self._save,
        ).pack(side=tk.RIGHT, ipady=3)

    def _add_custom_row(self, name: str = "", path_val: str = ""):
        """添加一行自定义 Agent（名称 + 路径 + 删除按钮）。"""
        row = tk.Frame(self._custom_container, bg=COLORS["card_bg"])
        row.pack(fill=tk.X, pady=4)
        name_var = tk.StringVar(value=name)
        path_var = tk.StringVar(value=path_val)
        ttk.Entry(row, textvariable=name_var, font=(_FONT, 10), width=12).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Entry(row, textvariable=path_var, font=(_FONT, 10)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(
            row, text="✕", style="Secondary.TButton", width=3,
            command=lambda r=row: self._remove_custom_row(r),
        ).pack(side=tk.LEFT)
        self._custom_rows.append((row, name_var, path_var))

    def _remove_custom_row(self, row):
        """删除一行自定义 Agent。"""
        row.destroy()
        self._custom_rows = [r for r in self._custom_rows if r[0] is not row]

    def _toggle_advanced(self, _evt=None):
        """展开/折叠高级区域。"""
        self.sw_advanced = not self.sw_advanced
        self._advanced_label.config(
            text="高级 ▴  Agent 路径覆盖（展开可手动填，留空=自动检测）"
            if self.sw_advanced else self._advanced_label_text,
        )
        if self.sw_advanced:
            self._advanced_frame.pack(fill=tk.X, pady=(0, 8))
        else:
            self._advanced_frame.pack_forget()
        # 重新计算滚动区域
        self.win.update_idletasks()
        self._canvas.config(scrollregion=self._canvas.bbox("all"))

    def _current_settings(self) -> dict:
        """根据当前控件值生成设置字典"""
        overrides = {}
        # 预置 agent
        for agent, var in self.override_vars.items():
            val = var.get().strip()
            if val:
                overrides[agent] = val
        # 自定义 agent
        for _row, name_var, path_var in self._custom_rows:
            name = name_var.get().strip()
            path_val = path_var.get().strip()
            if name and path_val:
                overrides[name] = path_val
        return {
            "auto_interval_hours": int(self.interval_var.get()),
            "conflict_action": self.conflict_var.get(),
            "minimize_to_tray": self.tray_var.get(),
            "auto_start": self.auto_start_var.get(),
            "agent_overrides": overrides,
            "window_geometry": self._original_settings.get("window_geometry", "720x520"),
        }

    def _has_changes(self) -> bool:
        """检测当前控件值是否与原始设置不同"""
        return self._current_settings() != self._original_settings

    def _on_window_close(self):
        """点击窗口 X 按钮时：有改动则询问是否保存"""
        if self._has_changes():
            answer = messagebox.askyesnocancel(
                "保存设置",
                "设置已修改，是否保存？\n\n"
                "- 是：保存并关闭\n"
                "- 否：放弃改动并关闭\n"
                "- 取消：返回设置",
                parent=self.win,
            )
            if answer is True:
                self._save()
            elif answer is False:
                self._close_without_save()
            else:
                # 取消：什么都不做，保持窗口打开
                return
        else:
            self._close_without_save()

    def _close_without_save(self):
        """直接关闭设置窗口，不保存"""
        self.win.grab_release()
        self.win.destroy()

    def _close(self):
        """取消按钮：行为与点 X 一致"""
        self._on_window_close()

    def _save(self):
        """保存按钮：把当前控件值写回设置并关闭"""
        self.settings = self._current_settings()
        self.on_save(self.settings)
        self.win.destroy()


# ---------------------------------------------------------------------------
# 命令行模式
# ---------------------------------------------------------------------------

def run_cli():
    """命令行模式同步"""
    # Windows 默认 GBK 编码无法输出 emoji/警告符号，强制 UTF-8
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # 收集所有输出（EXE 模式下 stdout 没有 console，写到文件）
    output_lines = []
    log_path = _data_dir() / "sync_report.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def flush_log():
        """渐进式写日志（防止 EXE 崩溃时丢失所有输出）"""
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(output_lines))
                f.write("\n")
        except OSError:
            pass

    def emit(msg=""):
        print(msg)
        output_lines.append(msg)
        flush_log()  # 每次输出都立即写日志

    emit("=== 多Agent记忆融合器 (CLI 模式) ===")
    emit("开始时间: {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    emit("")

    from sync_engine import SyncEngine

    def cli_progress(msg):
        emit("  {}".format(msg))

    engine = SyncEngine(on_progress=cli_progress)
    report = engine.run()

    emit("")
    emit(report.summary_text())
    emit("")
    emit("结束时间: {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    emit("日志文件: {}".format(log_path))
    flush_log()  # 最后再写一次确保完整

    return report


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def _check_single_instance():
    """Windows 互斥锁，防止重复启动。返回 True 表示是第一个实例。"""
    global _SINGLE_INSTANCE_MUTEX
    try:
        import ctypes
        if _SINGLE_INSTANCE_MUTEX:
            return True

        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\AgentMemorySyncMutex")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            ctypes.windll.user32.MessageBoxW(
                0,
                "多Agent记忆融合器已在运行中。\n请检查系统托盘（右下角）。",
                "多Agent记忆融合器",
                0x40 | 0x10000  # MB_ICONINFO | MB_TOPMOST
            )
            return False

        # 必须保留句柄到进程结束，防止被 GC/Close 导致互斥锁提前失效。
        _SINGLE_INSTANCE_MUTEX = mutex
        return True
    except Exception:
        return True


def _is_onedrive_path(path: Path) -> bool:
    """判断路径是否位于 OneDrive 同步目录内"""
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    parts = [p.lower() for p in resolved.parts]
    # 常见 OneDrive 目录特征
    if "onedrive" in parts:
        return True
    # 也检查环境变量指向的 OneDrive 根目录
    for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = os.environ.get(env)
        if root:
            try:
                if resolved.is_relative_to(Path(root)):
                    return True
            except Exception:
                pass
    return False


def _ensure_local_install():
    """OneDrive 内运行的 EXE 会被系统限制托盘图标 API，自动复制到本地再启动。

    仅对 PyInstaller 打包后的 Windows EXE 生效；本地 Python 源码运行时不处理。
    可通过命令行参数 --no-relocate 禁用。
    """
    _reloc_log(f"start frozen={getattr(sys, 'frozen', None)} argv={sys.argv}")
    if sys.platform != "win32":
        _reloc_log("skip: not win32")
        return
    if "--no-relocate" in sys.argv:
        _reloc_log("skip: --no-relocate")
        return
    if not getattr(sys, "frozen", False):
        _reloc_log(f"skip: not frozen (frozen={getattr(sys, 'frozen', None)})")
        return

    exe_path = _normalize_windows_path(Path(sys.executable))
    _reloc_log(f"exe_path={exe_path}")
    is_od = _is_onedrive_path(exe_path)
    _reloc_log(f"is_onedrive_path={is_od}")
    if not is_od:
        return

    # 候选本地目录：优先持久化的 LOCALAPPDATA，不可写时回退到 TEMP
    local_dir_candidates = [
        _normalize_windows_path(Path(os.environ.get("LOCALAPPDATA", _safe_home() / "AppData" / "Local")) / "AgentMemorySystem" / "App"),
        _normalize_windows_path(Path(os.environ.get("TEMP", "C:\\temp")) / "AgentMemorySystem" / "App"),
    ]
    # 测试环境可覆盖本地目录（不会暴露给最终用户）
    _env_local = os.environ.get("AGENT_MEMORY_LOCAL_DIR")
    if _env_local:
        local_dir_candidates.insert(0, _normalize_windows_path(Path(_env_local)))

    local_dir = None
    local_exe = None
    for candidate in local_dir_candidates:
        _reloc_log(f"trying local_dir={candidate}")
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            test_file = candidate / ".write_test"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink()
            local_dir = candidate
            local_exe = candidate / exe_path.name
            _reloc_log(f"local_dir selected={candidate}")
            break
        except Exception as e:
            _reloc_log(f"local_dir not writable: {e}")
            continue

    # 如果 Python 无法写入任何候选目录，尝试用 PowerShell 迁到 TEMP
    # （某些沙箱/工具会限制 OneDrive 内 EXE 的文件写入，但通常不限制 powershell.exe）
    if local_dir is None or local_exe is None:
        _reloc_log("no writable local dir via Python, trying PowerShell fallback")
        _try_powershell_relocate(exe_path)
        return

    # 判断是否需要复制：按 EXE 修改时间比较
    need_copy = True
    if local_exe.exists():
        try:
            if local_exe.stat().st_mtime >= exe_path.stat().st_mtime:
                need_copy = False
        except Exception:
            pass

    _reloc_log(f"need_copy={need_copy}")
    if need_copy:
        try:
            import shutil
            # 复制整个 EXE 目录（onedir 模式）
            src_dir = exe_path.parent
            _reloc_log(f"copying {src_dir} -> {local_dir}")
            if local_dir.exists():
                shutil.rmtree(local_dir, ignore_errors=True)
            shutil.copytree(src_dir, local_dir)
            _reloc_log("copy done")
        except Exception as e:
            _reloc_log(f"copy failed: {e}")
            _try_powershell_relocate(exe_path)
            return

    if not local_exe.exists():
        _reloc_log("local_exe missing after copy")
        _try_powershell_relocate(exe_path)
        return

    # 从本地副本重启，带上相同参数（去掉 --no-relocate 无关）
    args = [str(local_exe)] + [a for a in sys.argv[1:] if a != "--no-relocate"]
    _reloc_log(f"relaunching with args={args}")
    try:
        subprocess.Popen(args, cwd=str(local_dir))
        _reloc_log("relaunch Popen ok, exiting")
        sys.exit(0)
    except Exception as e:
        _reloc_log(f"relaunch failed: {e}")
        _try_powershell_relocate(exe_path)


def _try_powershell_relocate(exe_path: Path):
    """PowerShell 后备方案：把 EXE 目录复制到 TEMP 并启动本地副本。"""
    temp_dir = Path(os.environ.get("TEMP", "C:\\temp")) / "AgentMemorySystem" / "App"
    temp_dir_str = str(temp_dir)
    src_dir_str = str(exe_path.parent)
    local_exe = temp_dir / exe_path.name

    # 先用 PowerShell 完成复制（某些环境会限制 OneDrive 内 EXE 的文件写入）
    ps_cmd = (
        f"$src='{src_dir_str}'; $dst='{temp_dir_str}'; "
        f"if (Test-Path $dst) {{ Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $dst }}; "
        f"Copy-Item -Recurse -Force $src $dst"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            creationflags=0x08000000,
            timeout=30,
        )
        _reloc_log(f"powershell copy rc={result.returncode} stderr={result.stderr[:500]}")
    except Exception as e:
        _reloc_log(f"powershell copy failed: {e}")
        return

    if not local_exe.exists():
        _reloc_log("local_exe missing after powershell copy")
        return

    # 复制成功后再用 Python 启动本地副本
    args = [str(local_exe)] + [a for a in sys.argv[1:] if a != "--no-relocate"]
    _reloc_log(f"relaunching from temp with args={args}")
    try:
        subprocess.Popen(args, cwd=str(temp_dir))
        _reloc_log("relaunch from temp ok, exiting")
        sys.exit(0)
    except Exception as e:
        _reloc_log(f"relaunch from temp failed: {e}")


def main():
    # OneDrive 内运行会导致 Shell_NotifyIconW 拒绝访问，优先迁移到本地
    _ensure_local_install()

    # 全局替换 Path.home() 为 _safe_home()，确保所有模块（包括 agent_memory.py）
    # 在 PyInstaller --windowed 模式下也能获取正确路径
    _home = _safe_home()
    _original_home = Path.home
    Path.home = staticmethod(lambda: _home)

    # Windows 11 要求设置 AppUserModelID，否则托盘图标/任务栏可能不显示
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_APP_USER_MODEL_ID)
        except Exception:
            pass

    # 启动诊断日志（文件可能被锁定，不能因此崩溃）
    _diag_lines = [
        "APP STARTING v3-final\n",
        f"  _safe_home() = {_home}\n",
        f"  _data_dir() = {_data_dir()}\n",
        f"  _original_home() = {_original_home()}\n",
        f"  sys.executable = {sys.executable}\n",
        f"  _MEIPASS = {getattr(sys, '_MEIPASS', 'N/A')}\n",
        f"  SETTINGS_PATH = {SETTINGS_PATH}\n",
    ]
    _migrate_old_data()
    try:
        _startup_log = _data_dir() / "tray_error.log"
        _data_dir().mkdir(parents=True, exist_ok=True)
        # 追加模式，保留 _ensure_local_install() 可能已写入的迁移日志
        with open(_startup_log, "a", encoding="utf-8") as f:
            f.writelines(_diag_lines)
    except Exception as _log_err:
        # 主日志写入失败时，回退到项目根目录或临时目录，方便排查
        try:
            _fallback_root = Path(__file__).parent if "__file__" in globals() else Path.cwd()
            _fallback_log = _fallback_root / "tray_error.log"
            with open(_fallback_log, "w", encoding="utf-8") as f:
                f.writelines(_diag_lines)
                f.write(f"  PRIMARY_LOG_ERROR = {_log_err}\n")
        except Exception:
            try:
                _fallback_log2 = Path(os.environ.get("TEMP", "C:\\temp")) / "agent_memory_tray_error.log"
                with open(_fallback_log2, "w", encoding="utf-8") as f:
                    f.writelines(_diag_lines)
                    f.write(f"  PRIMARY_LOG_ERROR = {_log_err}\n")
            except Exception:
                pass

    if "--cli" in sys.argv:
        run_cli()
        return

    if not _check_single_instance():
        return

    app = SyncMainWindow()
    app.run()


if __name__ == "__main__":
    main()
