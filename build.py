"""
打包脚本：将 AgentMemorySystem 打包为桌面应用

用法：
    python build.py

输出：
    AgentMemorySync/ 目录 — 包含 EXE 和依赖，双击即用
"""
import os
import subprocess
import sys
import shutil
from pathlib import Path


def _get_shortcut_paths():
    """获取当前用户的桌面和开始菜单路径（兼容中英文 Windows）"""
    home = Path.home()
    desktop = home / "Desktop"
    if not desktop.exists():
        # 某些中文系统桌面文件夹叫 "桌面"
        desktop = home / "桌面"
    start_menu = home / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return desktop, start_menu


def _create_shortcut(target: Path, shortcut_path: Path, icon: Path = None):
    """使用 PowerShell 创建 .lnk 快捷方式"""
    ps = (
        f"$WshShell = New-Object -ComObject WScript.Shell; "
        f"$Shortcut = $WshShell.CreateShortcut('{shortcut_path}'); "
        f"$Shortcut.TargetPath = '{target}'; "
        f"$Shortcut.WorkingDirectory = '{target.parent}'; "
    )
    if icon and icon.exists():
        ps += f"$Shortcut.IconLocation = '{icon}'; "
    ps += "$Shortcut.Save()"
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True,
            creationflags=0x08000000,
            timeout=30,
        )
        return shortcut_path.exists()
    except Exception:
        return False


def _install_local(source_dir: Path, dist_name: str) -> Path:
    """把构建结果复制到本地 AppData 并创建快捷方式，避免 OneDrive 权限限制"""
    local_dir = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "AgentMemorySystem" / "App"
    local_exe = local_dir / f"{dist_name}.exe"

    print(f"正在安装到本地: {local_dir}")
    if local_dir.exists():
        shutil.rmtree(local_dir, ignore_errors=True)
    shutil.copytree(source_dir, local_dir)

    # 创建快捷方式
    desktop, start_menu = _get_shortcut_paths()
    icon = local_dir / "_internal" / "assets" / "app_icon.ico"
    if not icon.exists():
        icon = source_dir / "_internal" / "assets" / "app_icon.ico"

    shortcuts = []
    if desktop.exists():
        sc = desktop / f"{dist_name}.lnk"
        if _create_shortcut(local_exe, sc, icon):
            shortcuts.append(sc)
    if start_menu.exists():
        sc = start_menu / f"{dist_name}.lnk"
        if _create_shortcut(local_exe, sc, icon):
            shortcuts.append(sc)

    return local_dir, shortcuts


def build():
    here = Path(__file__).parent
    icon_path = here / "assets" / "app_icon.ico"
    dist_name = "AgentMemorySync"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onedir",                     # 目录模式（避免 onefile 临时目录问题）
        "--windowed",                   # 无控制台窗口
        "--name", dist_name,            # 输出目录名称
        "--clean",                      # 清理临时文件
        "-y",                           # 覆盖已有输出目录
        # 图标
        "--icon", str(icon_path) if icon_path.exists() else "NONE",
        # 添加数据文件
        "--add-data", f"{here / 'config.json'};.",
        "--add-data", f"{here / 'assets'};assets",
        # 收集所有 PIL 子模块
        "--collect-submodules", "PIL",
        # 隐藏导入
        "--hidden-import", "tkinter.ttk",
        "--hidden-import", "sqlite3",
        "--hidden-import", "json",
        "--hidden-import", "hashlib",
        "--hidden-import", "logging",
        "--hidden-import", "safe_io",
        # 排除不需要的大型模块
        "--exclude-module", "matplotlib",
        "--exclude-module", "numpy",
        "--exclude-module", "pandas",
        "--exclude-module", "scipy",
        "--exclude-module", "unittest",
        "--exclude-module", "test",
        "--exclude-module", "xmlrpc",
        "--exclude-module", "pydoc",
        "--exclude-module", "doctest",
        "--exclude-module", "argparse",
        "--exclude-module", "pkg_resources",
        # 排除向量搜索栈
        "--exclude-module", "sentence_transformers",
        "--exclude-module", "transformers",
        "--exclude-module", "torch",
        "--exclude-module", "torchvision",
        "--exclude-module", "sklearn",
        "--exclude-module", "scikit-learn",
        "--exclude-module", "cv2",
        "--exclude-module", "opencv-python",
        "--exclude-module", "faiss",
        "--exclude-module", "IPython",
        "--exclude-module", "jedi",
        "--exclude-module", "black",
        "--exclude-module", "pylint",
        "--exclude-module", "astroid",
        "--exclude-module", "sentry_sdk",
        "--exclude-module", "opentelemetry",
        # 入口文件
        str(here / "memory_sync_app.py"),
    ]

    # UPX 压缩（如果可用）
    upx_dir = here / "upx"
    if upx_dir.exists():
        cmd.extend(["--upx-dir", str(upx_dir)])

    print("=" * 60)
    print("AgentMemorySync 打包中...")
    print("=" * 60)

    # 输出到根目录
    cmd.extend(["--distpath", str(here)])

    result = subprocess.run(cmd, cwd=str(here))

    if result.returncode == 0:
        exe_path = here / dist_name / f"{dist_name}.exe"
        if exe_path.exists():
            exe_size = exe_path.stat().st_size / 1024 / 1024
            internal_size = sum(
                f.stat().st_size for f in (here / dist_name / "_internal").rglob("*") if f.is_file()
            ) / 1024 / 1024
            print()
            print("=" * 60)
            print(f"打包成功！")
            print(f"输出目录: {here / dist_name}")
            print(f"EXE 大小: {exe_size:.1f} MB")
            print(f"依赖大小: {internal_size:.1f} MB")
            print("=" * 60)
            print()
            # 安装到本地并创建快捷方式（避免 OneDrive 路径导致托盘图标失败）
            try:
                local_dir, shortcuts = _install_local(here / dist_name, dist_name)
                print("=" * 60)
                print("已安装到本地目录并创建快捷方式")
                print(f"本地安装目录: {local_dir}")
                for sc in shortcuts:
                    print(f"快捷方式: {sc}")
                print("=" * 60)
                print()
                print("使用方法（推荐）：")
                print(f"  双击桌面快捷方式 '{dist_name}.lnk' 启动")
                print("  或从上述本地安装目录启动，不要从 OneDrive 目录直接运行")
                print()
                print("注意：")
                print("  - OneDrive 内的 EXE 可能因系统权限限制无法创建托盘图标")
                print("  - 如必须从 OneDrive 运行，程序会尝试自动迁移到本地目录")
            except Exception as e:
                print(f"\n警告：本地安装失败: {e}")
                print(f"请手动复制整个 {dist_name}/ 目录到本地非 OneDrive 位置后再运行")
                print("注意：分发时需要整个目录一起复制，不能只复制 EXE")

            # 清理 PyInstaller 产生的临时目录
            for d in [here / "build", here / "__pycache__"]:
                if d.exists():
                    shutil.rmtree(d, ignore_errors=True)
            for spec in here.glob("*.spec"):
                spec.unlink(missing_ok=True)
            print("已清理临时文件。")
        else:
            print("打包完成但找不到 EXE，请检查输出目录")
    else:
        print(f"打包失败，退出码: {result.returncode}")
        sys.exit(1)


if __name__ == "__main__":
    build()
