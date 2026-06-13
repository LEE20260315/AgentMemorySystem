"""
记忆同步工具
===========
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

import json
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from typing import Optional

# 延迟导入，避免在 CLI 模式下加载 GUI 依赖
pystray = None
PIL = None


def _load_tray_deps():
    """延迟加载系统托盘依赖"""
    global pystray, PIL
    try:
        import pystray as _pystray
        from PIL import Image, ImageDraw
        pystray = _pystray
        PIL = type("PIL", (), {"Image": Image, "ImageDraw": ImageDraw})()
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# 设置文件
# ---------------------------------------------------------------------------

SETTINGS_PATH = Path.home() / ".agent_memory" / "sync_settings.json"

DEFAULT_SETTINGS = {
    "auto_interval_days": 7,
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
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


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
    "log_bg": "#1d1d1f",
    "log_text": "#f5f5f7",
    "sidebar_bg": "#2d2d2d",
}


def apply_modern_style(root: tk.Tk):
    """应用 macOS 风格的现代化样式"""
    style = ttk.Style(root)

    # 使用 clam 主题作为基础
    style.theme_use("clam")

    # 全局背景
    style.configure(".", background=COLORS["bg"], foreground=COLORS["text"])

    # 按钮样式 - 圆角感
    style.configure(
        "Accent.TButton",
        background=COLORS["accent"],
        foreground="white",
        padding=(20, 10),
        font=("", 10, "bold"),
    )
    style.map(
        "Accent.TButton",
        background=[("active", COLORS["accent_hover"]), ("disabled", "#ccc")],
    )

    style.configure(
        "TButton",
        background=COLORS["card_bg"],
        foreground=COLORS["text"],
        padding=(15, 8),
        font=("", 10),
        borderwidth=1,
        relief="solid",
    )
    style.map(
        "TButton",
        background=[("active", "#e8e8ed")],
    )

    # 框架样式
    style.configure("Card.TFrame", background=COLORS["card_bg"], relief="flat")
    style.configure("TFrame", background=COLORS["bg"])

    # 标签样式
    style.configure(
        "Title.TLabel",
        background=COLORS["bg"],
        foreground=COLORS["text"],
        font=("", 18, "bold"),
    )
    style.configure(
        "Subtitle.TLabel",
        background=COLORS["bg"],
        foreground=COLORS["text_secondary"],
        font=("", 10),
    )
    style.configure(
        "Card.TLabel",
        background=COLORS["card_bg"],
        foreground=COLORS["text"],
        font=("", 10),
    )
    style.configure(
        "CardTitle.TLabel",
        background=COLORS["card_bg"],
        foreground=COLORS["text"],
        font=("", 11, "bold"),
    )
    style.configure(
        "Stat.TLabel",
        background=COLORS["card_bg"],
        foreground=COLORS["accent"],
        font=("", 14, "bold"),
    )
    style.configure(
        "StatLabel.TLabel",
        background=COLORS["card_bg"],
        foreground=COLORS["text_secondary"],
        font=("", 9),
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
        font=("", 11, "bold"),
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
        font=("", 10),
    )

    # Checkbutton
    style.configure(
        "TCheckbutton",
        background=COLORS["card_bg"],
        foreground=COLORS["text"],
        font=("", 10),
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
    """记忆同步工具主窗口"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("记忆同步工具")
        self.root.geometry(load_settings().get("window_geometry", "720x520"))
        self.root.minsize(560, 420)
        self.root.configure(bg=COLORS["bg"])

        # macOS 风格窗口属性
        try:
            self.root.tk.call("tk", "scaling", 1.25)
        except Exception:
            pass

        self.settings = load_settings()
        self.is_syncing = False
        self.last_report = None
        self.sync_thread = None
        self.tray_icon = None

        apply_modern_style(self.root)
        self._build_ui()

        # 关闭按钮 → 最小化到托盘（而非退出）
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        """构建 macOS 风格 UI"""
        # 顶部标题区域
        header = ttk.Frame(self.root)
        header.pack(fill=tk.X, padx=20, pady=(20, 10))

        title = ttk.Label(header, text="记忆同步工具", style="Title.TLabel")
        title.pack(side=tk.LEFT)

        subtitle = ttk.Label(header, text="Agent Memory Sync", style="Subtitle.TLabel")
        subtitle.pack(side=tk.LEFT, padx=(10, 0), pady=(5, 0))

        # 状态指示器
        self.status_dot = tk.Canvas(header, width=12, height=12, bg=COLORS["bg"], highlightthickness=0)
        self.status_dot.pack(side=tk.RIGHT, padx=(0, 8))
        self._draw_status_dot(COLORS["success"])

        self.status_var = tk.StringVar(value="就绪")
        status_label = ttk.Label(header, textvariable=self.status_var, style="Subtitle.TLabel")
        status_label.pack(side=tk.RIGHT)

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

        # 按钮区域
        btn_frame = tk.Frame(right_panel, bg=COLORS["bg"])
        btn_frame.pack(fill=tk.X, pady=(15, 0))

        self.run_btn = ttk.Button(
            btn_frame, text="立即同步", style="Accent.TButton",
            command=self._start_sync
        )
        self.run_btn.pack(fill=tk.X, pady=(0, 8))

        self.rollback_btn = ttk.Button(
            btn_frame, text="回滚上次", command=self._rollback
        )
        self.rollback_btn.pack(fill=tk.X, pady=(0, 8))

        self.settings_btn = ttk.Button(
            btn_frame, text="设置", command=self._open_settings
        )
        self.settings_btn.pack(fill=tk.X, pady=(0, 8))

        self.minimize_btn = ttk.Button(
            btn_frame, text="最小化到托盘", command=self._minimize_to_tray
        )
        self.minimize_btn.pack(fill=tk.X)

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

                # 发送托盘通知
                self._notify("同步完成", "提取 {} 条, 写回 {} 条".format(
                    report.total_extracted, report.total_written
                ))

            except Exception as e:
                self._log("同步失败: {}".format(e))
                self.root.after(0, lambda: self.status_var.set("同步失败"))
                self.root.after(0, lambda: self._draw_status_dot(COLORS["error"]))
            finally:
                self.is_syncing = False
                self.root.after(0, lambda: self.run_btn.config(state=tk.NORMAL))
                self.root.after(0, lambda: self.rollback_btn.config(state=tk.NORMAL))

        self.sync_thread = threading.Thread(target=_sync_thread, daemon=True)
        self.sync_thread.start()

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
        self._log("设置已保存")

    def _minimize_to_tray(self):
        """最小化到系统托盘"""
        if not _load_tray_deps():
            messagebox.showwarning(
                "缺少依赖",
                "系统托盘需要 pystray 和 Pillow 库。\n"
                "请运行: pip install pystray Pillow"
            )
            return

        self._create_tray_icon()
        self.root.withdraw()

    def _create_tray_icon(self):
        """创建系统托盘图标"""
        if self.tray_icon is not None:
            return

        # 生成一个简洁的图标
        image = PIL.Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = PIL.ImageDraw.Draw(image)
        # 蓝色圆形 + 白色 "M"
        draw.ellipse([4, 4, 60, 60], fill=COLORS["accent"])
        draw.text((18, 14), "M", fill="white")

        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", self._show_window),
            pystray.MenuItem("立即同步", self._tray_sync),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("设置", self._tray_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._quit),
        )

        self.tray_icon = pystray.Icon(
            "memory_sync",
            image,
            "记忆同步工具",
            menu,
        )

        # 在后台线程运行托盘
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _show_window(self, icon=None, item=None):
        """显示主窗口"""
        self.root.after(0, self.root.deiconify)
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None

    def _tray_sync(self, icon=None, item=None):
        """从托盘触发同步"""
        self.root.after(0, self._show_window)
        self.root.after(100, self._start_sync)

    def _tray_settings(self, icon=None, item=None):
        """从托盘打开设置"""
        self.root.after(0, self._show_window)
        self.root.after(100, self._open_settings)

    def _quit(self, icon=None, item=None):
        """退出程序"""
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        self.root.after(0, self.root.destroy)

    def _on_close(self):
        """关闭窗口"""
        if self.settings.get("minimize_to_tray", True):
            self._minimize_to_tray()
        else:
            self._quit()

    def _notify(self, title: str, body: str):
        """发送通知"""
        try:
            if self.tray_icon:
                self.tray_icon.notify(body, title)
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

        # 自动同步检查
        if self.settings.get("auto_start", False):
            self.root.after(1000, self._start_sync)

        self.root.mainloop()


# ---------------------------------------------------------------------------
# 设置对话框
# ---------------------------------------------------------------------------

class SettingsDialog:
    """设置对话框 - macOS 风格"""

    def __init__(self, parent, settings: dict, on_save):
        self.settings = settings.copy()
        self.on_save = on_save

        self.win = tk.Toplevel(parent)
        self.win.title("设置")
        self.win.geometry("420x400")
        self.win.resizable(False, False)
        self.win.transient(parent)
        self.win.grab_set()
        self.win.configure(bg=COLORS["bg"])

        self._build()

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
        self.interval_var = tk.IntVar(value=self.settings.get("auto_interval_days", 7))
        spin = ttk.Spinbox(row, from_=1, to=30, textvariable=self.interval_var, width=5, font=("", 10))
        spin.pack(side=tk.RIGHT)
        ttk.Label(row, text="天", style="Card.TLabel").pack(side=tk.RIGHT, padx=(0, 8))

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

        for agent in ["hermes", "claude", "trae"]:
            row = tk.Frame(inner, bg=COLORS["card_bg"])
            row.pack(fill=tk.X, pady=3)
            ttk.Label(row, text="{}:".format(agent), style="Card.TLabel", width=8).pack(side=tk.LEFT)
            var = tk.StringVar(value=overrides.get(agent, ""))
            entry = ttk.Entry(row, textvariable=var, font=("", 9))
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
            self.override_vars[agent] = var

        # 按钮
        btn_frame = tk.Frame(self.win, bg=COLORS["bg"])
        btn_frame.pack(fill=tk.X, padx=20, pady=(0, 20))

        ttk.Button(btn_frame, text="取消", command=self.win.destroy).pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text="保存", style="Accent.TButton", command=self._save).pack(side=tk.RIGHT, padx=(0, 10))

    def _save(self):
        self.settings["auto_interval_days"] = self.interval_var.get()
        self.settings["conflict_action"] = self.conflict_var.get()
        self.settings["minimize_to_tray"] = self.tray_var.get()
        self.settings["auto_start"] = self.auto_start_var.get()

        overrides = {}
        for agent, var in self.override_vars.items():
            val = var.get().strip()
            if val:
                overrides[agent] = val
        self.settings["agent_overrides"] = overrides

        self.on_save(self.settings)
        self.win.destroy()


# ---------------------------------------------------------------------------
# 命令行模式
# ---------------------------------------------------------------------------

def run_cli():
    """命令行模式同步"""
    print("=== 记忆同步工具 (CLI 模式) ===")
    print()

    from sync_engine import SyncEngine

    def cli_progress(msg):
        print("  {}".format(msg))

    engine = SyncEngine(on_progress=cli_progress)
    report = engine.run()

    print()
    print(report.summary_text())
    return report


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main():
    if "--cli" in sys.argv:
        run_cli()
        return

    app = SyncMainWindow()
    app.run()


if __name__ == "__main__":
    main()
