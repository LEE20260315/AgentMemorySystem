"""
打包脚本：将 AgentMemorySystem 打包为单个 EXE 文件

用法：
    python build.py

输出：
    AgentMemorySync.exe  — 双击即用，无需安装 Python
"""
import subprocess
import sys
from pathlib import Path


def build():
    here = Path(__file__).parent
    icon_path = here / "assets" / "app_icon.ico"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",                    # 单文件
        "--windowed",                   # 无控制台窗口
        "--name", "AgentMemorySync",    # EXE 名称
        "--clean",                      # 清理临时文件
        # 图标
        "--icon", str(icon_path) if icon_path.exists() else "NONE",
        # 添加数据文件
        "--add-data", f"{here / 'config.json'};.",
        "--add-data", f"{here / 'assets'};assets",
        # 收集所有 PIL 子模块（比逐个 --hidden-import 可靠）
        "--collect-submodules", "PIL",
        # 隐藏导入
        "--hidden-import", "pystray._win32",
        "--hidden-import", "pystray",
        "--hidden-import", "tkinter.ttk",
        "--hidden-import", "sqlite3",
        "--hidden-import", "json",
        "--hidden-import", "hashlib",
        "--hidden-import", "logging",
        # 排除不需要的大型模块（减小体积）
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
        exe_path = here / "AgentMemorySync.exe"
        if exe_path.exists():
            size_mb = exe_path.stat().st_size / 1024 / 1024
            print()
            print("=" * 60)
            print(f"打包成功！")
            print(f"输出: {exe_path}")
            print(f"大小: {size_mb:.1f} MB")
            print("=" * 60)
            print()
            print("使用方法：")
            print("  1. 双击 AgentMemorySync.exe 启动")
            print("  2. 首次运行会自动检测本机 Agent")
            print("  3. 点击「立即同步」执行记忆同步")
            print("  4. 点击「最小化到托盘」后台运行")

            # 清理 PyInstaller 产生的临时目录
            import shutil
            for d in [here / "build", here / "dist", here / "__pycache__"]:
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
