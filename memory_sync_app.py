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


def _data_dir() -> Path:
    r"""获取应用数据目录

    Windows 优先使用 EXE 所在目录下的 data 子目录（避免 OneDrive/Defender
    对 AppData/Local 的权限拦截），非 Windows 或无法写入时回退到标准位置。
    """
    if sys.platform == "win32":
        # 优先：EXE 同级目录下的 data/，便于读写且不触发 AppData 保护
        exe_dir = Path(sys.executable).parent
        candidate = exe_dir / "data"
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            # 验证可写
            test_file = candidate / ".write_test"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink()
            return candidate
        except Exception:
            pass
        # 回退：标准 LOCALAPPDATA
        base = Path(os.environ.get("LOCALAPPDATA", _safe_home() / "AppData" / "Local"))
        return base / "AgentMemorySystem"
    else:
        return _safe_home() / ".local" / "share" / "AgentMemorySystem"


def _migrate_old_data():
    """把旧版本数据迁移到新目录"""
    new_dir = _data_dir()
    if new_dir.exists():
        return
    candidates = [
        _safe_home() / ".agent_memory",
        Path(os.environ.get("LOCALAPPDATA", _safe_home() / "AppData" / "Local")) / "AgentMemorySystem",
    ]
    try:
        new_dir.mkdir(parents=True, exist_ok=True)
        for old_dir in candidates:
            if not old_dir.exists():
                continue
            old_settings = old_dir / "sync_settings.json"
            if old_settings.exists():
                import shutil
                shutil.copy2(old_settings, new_dir / "sync_settings.json")
                break
    except Exception:
        pass


SETTINGS_PATH = _data_dir() / "sync_settings.json"

DEFAULT_SETTINGS = {
    "auto_interval_hours": 2,
    "conflict_action": "prompt",
    "auto_start": False,
    "minimize_to_tray": True,
    "window_geometry": "720x520",
}


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                return {**DEFAULT_SETTINGS, **json.load(f)}
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULT_SETTINGS.copy()


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
# 现代化 UI 样式
# ---------------------------------------------------------------------------

# macOS 风格配色
COLORS = {
    "bg": "#f5f5f7",
    "card_bg": "#ffffff",
    "accent": "#007aff",
    "accent_hover": "#0056cc",
    "text": "#1d1d1f",
    "text_secondary": "#86868b",
    "border": "#d2d2d7",
    "success": "#34c759",
    "warning": "#ff9500",
    "error": "#ff3b30",
    "log_bg": "#ffffff",
    "log_text": "#3d3d3d",
    "sidebar_bg": "#2d2d2d",
}


class RoundedButton(tk.Canvas):
    """macOS 风格圆角按钮（基于 Canvas 绘制）"""

    def __init__(
        self,
        parent,
        text: str,
        command=None,
        style: str = "accent",
        width: int = 120,
        height: int = 32,
        font=(_FONT, 10),
        **kwargs,
    ):
        self.style = style
        self.command = command
        self._text = text
        self._width = width
        self._height = height
        self._font = font

        bg = kwargs.pop("bg", COLORS["bg"])
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=bg,
            highlightthickness=0,
            cursor="hand2",
            **kwargs,
        )

        self._normal_bg = COLORS["accent"] if style == "accent" else COLORS["card_bg"]
        self._hover_bg = COLORS["accent_hover"] if style == "accent" else "#e8e8ed"
        self._active_bg = "#0056cc" if style == "accent" else "#dcdce0"
        self._fg = "#ffffff" if style == "accent" else COLORS["text"]
        self._border = COLORS["border"] if style != "accent" else ""
        self._current_bg = self._normal_bg

        self._draw()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _draw(self):
        self.delete("all")
        radius = self._height // 2
        self._rect = self.create_rounded_rect(
            1, 1, self._width - 1, self._height - 1, radius,
            fill=self._current_bg, outline=self._border,
        )
        self.create_text(
            self._width // 2,
            self._height // 2,
            text=self._text,
            fill=self._fg,
            font=self._font,
        )

    def create_rounded_rect(self, x1, y1, x2, y2, radius, **kwargs):
        """用圆弧和线段绘制圆角矩形"""
        points = [
            x1 + radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1,
        ]
        return self.create_polygon(points, smooth=True, **kwargs)

    def _on_enter(self, _):
        self._current_bg = self._hover_bg
        self.itemconfig(self._rect, fill=self._current_bg)

    def _on_leave(self, _):
        self._current_bg = self._normal_bg
        self.itemconfig(self._rect, fill=self._current_bg)

    def _on_press(self, _):
        self.itemconfig(self._rect, fill=self._active_bg)

    def _on_release(self, _):
        self.itemconfig(self._rect, fill=self._current_bg)
        if self.command:
            self.command()

    def config_text(self, text: str):
        self._text = text
        self._draw()


def apply_modern_style(root: tk.Tk):
    """应用 macOS 风格的现代化样式"""
    # 高 DPI 缩放修正
    if sys.platform == "win32":
        try:
            dpi = root.winfo_fpixels("1i")
            root.tk.call("tk", "scaling", dpi / 72.0)
        except Exception:
            pass

    style = ttk.Style(root)

    # 使用 clam 主题作为基础
    style.theme_use("clam")

    # 全局背景
    style.configure(".", background=COLORS["bg"], foreground=COLORS["text"])

    # 按钮样式 - macOS 胶囊风格
    style.configure(
        "Accent.TButton",
        background=COLORS["accent"],
        foreground="white",
        padding=(20, 10),
        font=(_FONT, 10, "bold"),
        borderwidth=0,
        relief="flat",
        focusthickness=0,
    )
    style.map(
        "Accent.TButton",
        background=[("active", COLORS["accent_hover"]), ("disabled", "#d2d2d7")],
        foreground=[("disabled", "#ffffff")],
    )
    # 让 Accent 按钮尽可能圆角（clam 主题下 borderadius 有限，用大 padding 模拟胶囊）
    style.layout(
        "Accent.TButton",
        [
            (
                "Button.button",
                {
                    "children": [
                        ("Button.focus", {"children": [("Button.padding", {"children": [("Button.label", {"sticky": "nswe"})], "sticky": "nswe"})], "sticky": "nswe"})
                    ],
                    "sticky": "nswe",
                },
            )
        ],
    )

    style.configure(
        "TButton",
        background=COLORS["card_bg"],
        foreground=COLORS["text"],
        padding=(15, 8),
        font=(_FONT, 10),
        borderwidth=1,
        relief="solid",
    )
    style.map(
        "TButton",
        background=[("active", "#e8e8ed")],
        foreground=[("disabled", COLORS["text_secondary"])],
    )

    # 框架样式
    style.configure("Card.TFrame", background=COLORS["card_bg"], relief="flat")
    style.configure("TFrame", background=COLORS["bg"])

    # 标签样式
    style.configure(
        "Title.TLabel",
        background=COLORS["bg"],
        foreground=COLORS["text"],
        font=(_FONT, 18, "bold"),
    )
    style.configure(
        "Subtitle.TLabel",
        background=COLORS["bg"],
        foreground=COLORS["text_secondary"],
        font=(_FONT, 10),
    )
    style.configure(
        "Card.TLabel",
        background=COLORS["card_bg"],
        foreground=COLORS["text"],
        font=(_FONT, 10),
    )
    style.configure(
        "CardTitle.TLabel",
        background=COLORS["card_bg"],
        foreground=COLORS["text"],
        font=(_FONT, 11, "bold"),
    )
    style.configure(
        "Stat.TLabel",
        background=COLORS["card_bg"],
        foreground=COLORS["accent"],
        font=(_FONT, 14, "bold"),
    )
    style.configure(
        "StatLabel.TLabel",
        background=COLORS["card_bg"],
        foreground=COLORS["text_secondary"],
        font=(_FONT, 9),
    )

    # LabelFrame
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

    # 入口框
    style.configure(
        "TEntry",
        padding=6,
        borderwidth=1,
        relief="solid",
    )

    # Radiobutton
    style.configure(
        "TRadiobutton",
        background=COLORS["card_bg"],
        foreground=COLORS["text"],
        font=(_FONT, 10),
    )

    # Checkbutton
    style.configure(
        "TCheckbutton",
        background=COLORS["card_bg"],
        foreground=COLORS["text"],
        font=(_FONT, 10),
    )

    # Separator
    style.configure("TSeparator", background=COLORS["border"])

    # Spinbox
    style.configure("TSpinbox", padding=4)

    return style


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class SyncMainWindow:
    """多Agent记忆融合器主窗口"""

    def __init__(self):
        self.root = tk.Tk()
        # 必须在窗口显示前移除系统标题栏
        self.root.overrideredirect(True)
        self.root.title("AgentMemorySync")
        # 设置窗口图标
        if _ICON_PATH.exists():
            try:
                self.root.iconbitmap(str(_ICON_PATH))
            except Exception:
                pass
        self.root.geometry(load_settings().get("window_geometry", "720x520"))
        self.root.minsize(560, 420)
        self.root.configure(bg=COLORS["bg"])

        # macOS 风格自定义标题栏状态
        self._normal_geometry = load_settings().get("window_geometry", "720x520")
        self._is_maximized = False
        self._drag_offset_x = 0
        self._drag_offset_y = 0

        # macOS 风格窗口属性
        try:
            self.root.tk.call("tk", "scaling", 1.25)
        except Exception:
            pass

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

        apply_modern_style(self.root)
        self._build_ui()
        self._bind_window_drag()
        # 窗口显示后再裁剪圆角，否则 winfo_width/height 为 1
        self.root.after(100, self._apply_rounded_corners)

        # 关闭按钮 → 最小化到托盘（而非退出）
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        """构建 macOS 风格 UI（无边框 + 自定义标题栏）"""
        # 自定义标题栏
        self._build_title_bar()

        # 主内容区
        content = ttk.Frame(self.root)
        content.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))

        # 左侧：日志面板
        left_panel = ttk.Frame(content)
        left_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        log_card = tk.Frame(left_panel, bg=COLORS["card_bg"], bd=0, highlightthickness=1,
                            highlightbackground=COLORS["border"])
        log_card.pack(fill=tk.BOTH, expand=True)

        log_header = tk.Frame(log_card, bg=COLORS["card_bg"])
        log_header.pack(fill=tk.X, padx=12, pady=(10, 5))

        ttk.Label(log_header, text="同步日志", style="CardTitle.TLabel").pack(side=tk.LEFT)

        self.log_text = tk.Text(
            log_card,
            height=12,
            state=tk.DISABLED,
            wrap=tk.WORD,
            font=("Consolas", 9),
            bg=COLORS["log_bg"],
            fg=COLORS["log_text"],
            relief="flat",
            padx=12,
            pady=8,
            selectbackground="#404040",
            insertbackground="white",
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=2, pady=(0, 2))

        # 右侧：汇总 + 按钮
        right_panel = ttk.Frame(content, width=220)
        right_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=(15, 0))
        right_panel.pack_propagate(False)

        # 汇总卡片
        summary_card = tk.Frame(right_panel, bg=COLORS["card_bg"], bd=0, highlightthickness=1,
                                highlightbackground=COLORS["border"])
        summary_card.pack(fill=tk.X)

        summary_header = tk.Frame(summary_card, bg=COLORS["card_bg"])
        summary_header.pack(fill=tk.X, padx=12, pady=(10, 5))
        ttk.Label(summary_header, text="同步汇总", style="CardTitle.TLabel").pack(side=tk.LEFT)

        self.summary_widgets = []
        summary_fields = [
            ("agents", "发现 Agent", "0"),
            ("extracted", "提取记忆", "0"),
            ("merged", "融合共享", "0"),
            ("written", "写回", "0"),
            ("skipped", "跳过", "0"),
            ("duration", "耗时", "--"),
        ]

        for key, label, default in summary_fields:
            row_frame = tk.Frame(summary_card, bg=COLORS["card_bg"])
            row_frame.pack(fill=tk.X, padx=12, pady=2)

            ttk.Label(row_frame, text=label, style="StatLabel.TLabel").pack(side=tk.LEFT)
            val_label = ttk.Label(row_frame, text=default, style="Stat.TLabel")
            val_label.pack(side=tk.RIGHT)
            self.summary_widgets.append((key, val_label))

        # 底部留白
        tk.Frame(summary_card, bg=COLORS["card_bg"], height=8).pack()

        # 按钮区域 - macOS 胶囊按钮
        btn_frame = tk.Frame(right_panel, bg=COLORS["bg"])
        btn_frame.pack(fill=tk.X, pady=(15, 0))

        btn_width = 216
        btn_height = 34

        self.run_btn = RoundedButton(
            btn_frame, text="立即同步", style="accent",
            width=btn_width, height=btn_height,
            command=self._start_sync,
        )
        self.run_btn.pack(pady=(0, 8))

        self.rollback_btn = RoundedButton(
            btn_frame, text="回滚上次", style="secondary",
            width=btn_width, height=btn_height,
            command=self._rollback,
        )
        self.rollback_btn.pack(pady=(0, 8))

        self.settings_btn = RoundedButton(
            btn_frame, text="设置", style="secondary",
            width=btn_width, height=btn_height,
            command=self._open_settings,
        )
        self.settings_btn.pack(pady=(0, 8))

        self.minimize_btn = RoundedButton(
            btn_frame, text="最小化到托盘", style="secondary",
            width=btn_width, height=btn_height,
            command=self._minimize_to_tray,
        )
        self.minimize_btn.pack()

    def _build_title_bar(self):
        """构建 macOS 风格自定义标题栏"""
        self.title_bar = tk.Frame(self.root, bg=COLORS["bg"], height=40)
        self.title_bar.pack(fill=tk.X, side=tk.TOP)
        self.title_bar.pack_propagate(False)
        self.title_bar.grid_columnconfigure(1, weight=1)

        # 左侧：三个 macOS 风格圆点按钮
        btn_frame = tk.Frame(self.title_bar, bg=COLORS["bg"])
        btn_frame.grid(row=0, column=0, sticky="w", padx=(16, 0))

        self._title_close_btn = self._create_traffic_light_button(
            btn_frame, "#ff5f57", "#e0443e", "×", self._on_close
        )
        self._title_min_btn = self._create_traffic_light_button(
            btn_frame, "#febc2e", "#d89e24", "−", self._minimize_window
        )
        self._title_max_btn = self._create_traffic_light_button(
            btn_frame, "#28c840", "#24aa34", "+", self._toggle_maximize
        )
        self._title_close_btn.pack(side=tk.LEFT, padx=(0, 8))
        self._title_min_btn.pack(side=tk.LEFT, padx=(0, 8))
        self._title_max_btn.pack(side=tk.LEFT)

        # 中间：标题
        self.title_label = tk.Label(
            self.title_bar,
            text="AgentMemorySync",
            bg=COLORS["bg"],
            fg=COLORS["text_secondary"],
            font=(_FONT, 10),
        )
        self.title_label.grid(row=0, column=1, sticky="nsew")

        # 右侧：状态指示器
        status_frame = tk.Frame(self.title_bar, bg=COLORS["bg"])
        status_frame.grid(row=0, column=2, sticky="e", padx=(0, 16))

        self.status_var = tk.StringVar(value="就绪")
        status_label = tk.Label(
            status_frame,
            textvariable=self.status_var,
            bg=COLORS["bg"],
            fg=COLORS["text_secondary"],
            font=(_FONT, 9),
        )
        status_label.pack(side=tk.RIGHT)

        self.status_dot = tk.Canvas(
            status_frame, width=12, height=12, bg=COLORS["bg"], highlightthickness=0
        )
        self.status_dot.pack(side=tk.RIGHT, padx=(0, 6))
        self._draw_status_dot(COLORS["success"])

    def _create_traffic_light_button(self, parent, color, hover_color, symbol, command):
        """创建一个 macOS 风格交通灯按钮"""
        size = 14
        canvas = tk.Canvas(parent, width=size, height=size, bg=COLORS["bg"], highlightthickness=0)
        cid = canvas.create_oval(1, 1, size - 1, size - 1, fill=color, outline="")
        text_id = canvas.create_text(
            size // 2, size // 2,
            text=symbol,
            fill="#4a4a4a",
            font=(_FONT, 8, "bold"),
            state=tk.HIDDEN,
        )

        def on_enter(_):
            canvas.itemconfig(cid, fill=hover_color)
            canvas.itemconfig(text_id, state=tk.NORMAL)

        def on_leave(_):
            canvas.itemconfig(cid, fill=color)
            canvas.itemconfig(text_id, state=tk.HIDDEN)

        canvas.bind("<Enter>", on_enter)
        canvas.bind("<Leave>", on_leave)
        canvas.bind("<Button-1>", lambda _: command())
        return canvas

    def _bind_window_drag(self):
        """绑定标题栏拖动事件"""
        self.title_bar.bind("<Button-1>", self._on_title_bar_press)
        self.title_bar.bind("<B1-Motion>", self._on_title_bar_drag)
        self.title_label.bind("<Button-1>", self._on_title_bar_press)
        self.title_label.bind("<B1-Motion>", self._on_title_bar_drag)

    def _on_title_bar_press(self, event):
        """记录鼠标按下时的窗口位置偏移"""
        self._drag_offset_x = event.x_root - self.root.winfo_x()
        self._drag_offset_y = event.y_root - self.root.winfo_y()

    def _on_title_bar_drag(self, event):
        """拖动标题栏移动窗口"""
        if self._is_maximized:
            return
        x = event.x_root - self._drag_offset_x
        y = event.y_root - self._drag_offset_y
        self.root.geometry(f"+{x}+{y}")
        self._normal_geometry = self.root.geometry()

    def _minimize_window(self):
        """最小化窗口（本应用统一最小化到托盘）"""
        self._minimize_to_tray()

    def _toggle_maximize(self):
        """最大化/还原窗口"""
        if self._is_maximized:
            self.root.overrideredirect(False)
            self.root.geometry(self._normal_geometry)
            self.root.overrideredirect(True)
            self._is_maximized = False
        else:
            self._normal_geometry = self.root.geometry()
            self.root.overrideredirect(False)
            self.root.state("zoomed")
            self.root.overrideredirect(True)
            self._is_maximized = True
        self._apply_rounded_corners()

    def _apply_rounded_corners(self):
        """给无边框窗口裁剪圆角区域"""
        if sys.platform != "win32":
            return
        try:
            self.root.update_idletasks()
            hwnd = self.root.winfo_id()
            width = self.root.winfo_width()
            height = self.root.winfo_height()
            if hwnd == 0 or width < 10 or height < 10:
                self.root.after(100, self._apply_rounded_corners)
                return
            radius = 16
            hrgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, radius, radius)
            ctypes.windll.user32.SetWindowRgn(hwnd, hrgn, True)
        except Exception:
            pass

    def _draw_status_dot(self, color: str):
        """绘制状态指示点"""
        self.status_dot.delete("all")
        self.status_dot.create_oval(2, 2, 10, 10, fill=color, outline="")

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
        self._draw_status_dot(COLORS["warning"])

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
                    self.root.after(0, lambda: self._draw_status_dot(COLORS["error"]))
                else:
                    self.root.after(0, lambda: self.status_var.set("同步完成"))
                    self.root.after(0, lambda: self._draw_status_dot(COLORS["success"]))

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
                self.root.after(0, lambda: self._draw_status_dot(COLORS["error"]))
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
        self._draw_status_dot(COLORS["warning"])

        def _rollback_thread():
            try:
                from sync_engine import SyncEngine
                engine = SyncEngine(on_progress=self._log)
                restored = engine.rollback()
                self._log("回滚完成, 恢复 {} 个文件".format(restored))
                self.root.after(0, lambda: self.status_var.set("回滚完成"))
                self.root.after(0, lambda: self._draw_status_dot(COLORS["success"]))
            except Exception as e:
                self._log("回滚失败: {}".format(e))
                self.root.after(0, lambda: self.status_var.set("回滚失败"))
                self.root.after(0, lambda: self._draw_status_dot(COLORS["error"]))

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
                    self._tray_click_action = "show"
                    return 0
                elif lparam == _WM_RBUTTONUP:
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
            ps_script = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
                "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] > $null; "
                f"$template = '<toast><visual><binding template=\"ToastText02\">"
                f"<text id=\"1\">{ps_title}</text>"
                f"<text id=\"2\">{ps_body}</text>"
                f"</binding></visual></toast>'; "
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
        # 居中窗口
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 2
        self.root.geometry("+{}+{}".format(x, y))

        # 自动同步：启动时执行一次 + 定时循环
        if self.settings.get("auto_start", False):
            self.root.after(1000, self._start_sync)

        # 启动时最小化到托盘（--minimized 参数或设置中 auto_start）
        if self._start_minimized:
            self.root.after(500, self._minimize_to_tray)

        # 若启用最小化到托盘，启动时即创建托盘图标，方便用户随时找到
        if self.settings.get("minimize_to_tray", True):
            self.root.after(500, self._create_tray_icon)

        # 定时自动同步调度器（每 60 秒检查一次）
        self._schedule_next_sync()

        self.root.mainloop()


# ---------------------------------------------------------------------------
# 设置对话框
# ---------------------------------------------------------------------------

class SettingsDialog:
    """设置对话框 - macOS 风格"""

    def __init__(self, parent, settings: dict, on_save):
        self.settings = settings.copy()
        # 保存原始设置，用于检测是否有改动
        self._original_settings = settings.copy()
        self.on_save = on_save

        self.win = tk.Toplevel(parent)
        self.win.title("设置")
        self.win.geometry("420x400")
        self.win.resizable(False, False)
        self.win.transient(parent)
        self.win.grab_set()
        self.win.configure(bg=COLORS["bg"])

        self._build()

        # 点 X 关闭时先询问是否保存改动
        self.win.protocol("WM_DELETE_WINDOW", self._on_window_close)

        # 居中
        self.win.update_idletasks()
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        x = parent.winfo_x() + (parent.winfo_width() - w) // 2
        y = parent.winfo_y() + (parent.winfo_height() - h) // 2
        self.win.geometry("+{}+{}".format(x, y))

    def _build(self):
        # 卡片容器
        card = tk.Frame(self.win, bg=COLORS["card_bg"], highlightthickness=1,
                        highlightbackground=COLORS["border"])
        card.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        inner = tk.Frame(card, bg=COLORS["card_bg"])
        inner.pack(fill=tk.BOTH, expand=True, padx=20, pady=15)

        # 自动同步间隔
        row = tk.Frame(inner, bg=COLORS["card_bg"])
        row.pack(fill=tk.X, pady=6)
        ttk.Label(row, text="自动同步间隔", style="Card.TLabel").pack(side=tk.LEFT)
        hour_options = ["1", "2", "4", "8", "16", "24", "48", "72"]
        current_hours = str(self.settings.get("auto_interval_hours", 2))
        if current_hours not in hour_options:
            current_hours = "2"
        self.interval_var = tk.StringVar(value=current_hours)
        combo = ttk.Combobox(row, values=hour_options, textvariable=self.interval_var,
                             width=5, state="readonly", font=(_FONT, 10))
        combo.pack(side=tk.RIGHT)
        ttk.Label(row, text="小时", style="Card.TLabel").pack(side=tk.RIGHT, padx=(0, 8))

        ttk.Separator(inner, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=12)

        # OneDrive 冲突处理
        ttk.Label(inner, text="OneDrive 冲突时", style="Card.TLabel").pack(anchor=tk.W, pady=(0, 6))
        self.conflict_var = tk.StringVar(value=self.settings.get("conflict_action", "prompt"))
        conflict_frame = tk.Frame(inner, bg=COLORS["card_bg"])
        conflict_frame.pack(anchor=tk.W, pady=(0, 6))
        ttk.Radiobutton(conflict_frame, text="提示我", variable=self.conflict_var, value="prompt").pack(side=tk.LEFT)
        ttk.Radiobutton(conflict_frame, text="自动跳过", variable=self.conflict_var, value="skip").pack(side=tk.LEFT, padx=20)

        ttk.Separator(inner, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=12)

        # 选项
        self.tray_var = tk.BooleanVar(value=self.settings.get("minimize_to_tray", True))
        ttk.Checkbutton(inner, text="关闭窗口时最小化到托盘", variable=self.tray_var).pack(anchor=tk.W, pady=4)

        self.auto_start_var = tk.BooleanVar(value=self.settings.get("auto_start", False))
        ttk.Checkbutton(inner, text="启动时自动执行同步", variable=self.auto_start_var).pack(anchor=tk.W, pady=4)

        ttk.Separator(inner, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=12)

        # Agent 路径覆盖
        ttk.Label(inner, text="Agent 路径覆盖 (留空则自动检测)", style="Card.TLabel").pack(anchor=tk.W, pady=(0, 8))

        overrides = self.settings.get("agent_overrides", {})
        self.override_vars = {}

        # 从 config.json 动态读取 agent 列表
        try:
            _config_file = Path(__file__).parent / "config.json"
            with open(_config_file, "r", encoding="utf-8") as f:
                app_config = json.load(f)
            agent_list = list(app_config.get("agent_detection", {}).keys())
        except Exception:
            agent_list = list(overrides.keys())

        for agent in agent_list:
            row = tk.Frame(inner, bg=COLORS["card_bg"])
            row.pack(fill=tk.X, pady=3)
            ttk.Label(row, text="{}:".format(agent), style="Card.TLabel", width=12).pack(side=tk.LEFT)
            var = tk.StringVar(value=overrides.get(agent, ""))
            entry = ttk.Entry(row, textvariable=var, font=(_FONT, 9))
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
            self.override_vars[agent] = var

        # 按钮
        btn_frame = tk.Frame(self.win, bg=COLORS["bg"])
        btn_frame.pack(fill=tk.X, padx=20, pady=(0, 20))

        ttk.Button(btn_frame, text="取消", command=self._close).pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text="保存", style="Accent.TButton", command=self._save).pack(side=tk.RIGHT, padx=(0, 10))

    def _current_settings(self) -> dict:
        """根据当前控件值生成设置字典"""
        overrides = {}
        for agent, var in self.override_vars.items():
            val = var.get().strip()
            if val:
                overrides[agent] = val
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
    try:
        import ctypes
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\AgentMemorySyncMutex")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            ctypes.windll.user32.MessageBoxW(
                0,
                "多Agent记忆融合器已在运行中。\n请检查系统托盘（右下角）。",
                "多Agent记忆融合器",
                0x40 | 0x10000  # MB_ICONINFO | MB_TOPMOST
            )
            return False
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
    def _reloc_log(msg: str):
        """记录迁移诊断信息到 tray_error.log（ OneDrive 进程失败时这里最有价值）"""
        try:
            d = Path(sys.executable).parent / "data"
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "tray_error.log", "a", encoding="utf-8") as f:
                f.write(f"[RELOC] {msg}\n")
        except Exception:
            pass

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

    exe_path = Path(sys.executable)
    _reloc_log(f"exe_path={exe_path}")
    is_od = _is_onedrive_path(exe_path)
    _reloc_log(f"is_onedrive_path={is_od}")
    if not is_od:
        return

    # 候选本地目录：优先持久化的 LOCALAPPDATA，不可写时回退到 TEMP
    local_dir_candidates = [
        Path(os.environ.get("LOCALAPPDATA", _safe_home() / "AppData" / "Local")) / "AgentMemorySystem" / "App",
        Path(os.environ.get("TEMP", "C:\\temp")) / "AgentMemorySystem" / "App",
    ]
    # 测试环境可覆盖本地目录（不会暴露给最终用户）
    _env_local = os.environ.get("AGENT_MEMORY_LOCAL_DIR")
    if _env_local:
        local_dir_candidates.insert(0, Path(_env_local))

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
