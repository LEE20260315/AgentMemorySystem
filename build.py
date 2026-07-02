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
import time
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


def _next_available_dir(parent: Path, stem: str) -> Path:
    """生成一个不会与现有目录冲突的候选目录名。"""
    stamp = time.strftime("%Y%m%d%H%M%S")
    candidate = parent / f"{stem}_{stamp}"
    idx = 1
    while candidate.exists():
        idx += 1
        candidate = parent / f"{stem}_{stamp}_{idx}"
    return candidate


def _write_bundle_pointer(project_dir: Path, dist_name: str, bundle_dir: Path) -> Path:
    """写入当前 launcher 应使用的包目录名。"""
    pointer = project_dir / f"{dist_name}.current.txt"
    pointer.write_text(bundle_dir.name, encoding="ascii")
    return pointer


def _write_repo_launcher(project_dir: Path, dist_name: str) -> Path:
    """在项目根目录生成跨设备启动器。

    启动器职责：
    1. 从 OneDrive 同步包 AgentMemorySync/ 同步到本机 TEMP 本地副本
    2. 设置 AGENT_MEMORY_DATA_DIR 指向项目根目录下的 data/
    3. 从本地副本启动 EXE，避免直接从 OneDrive 运行触发托盘限制

    为避免 Windows 老旧 cmd.exe 编码问题，启动器仅使用纯 ASCII 文本。
    """
    bat_path = project_dir / f"{dist_name}.bat"
    bat_content = (
        '@echo off\r\n'
        'setlocal EnableExtensions EnableDelayedExpansion\r\n'
        'cd /d "%~dp0"\r\n'
        '\r\n'
        'set "REPO_DIR=%~dp0"\r\n'
        'if "%REPO_DIR:~-1%"=="\\" set "REPO_DIR=%REPO_DIR:~0,-1%"\r\n'
        f'set "CURRENT_FILE=%REPO_DIR%\\{dist_name}.current.txt"\r\n'
        f'set "SOURCE_NAME={dist_name}"\r\n'
        'if exist "%CURRENT_FILE%" set /p SOURCE_NAME=<"%CURRENT_FILE%"\r\n'
        'if not defined SOURCE_NAME set "SOURCE_NAME=AgentMemorySync"\r\n'
        'set "SOURCE_DIR=%REPO_DIR%\\%SOURCE_NAME%"\r\n'
        f'set "LOCAL_BASE=%TEMP%\\{dist_name}_Run"\r\n'
        'set "LOCAL_DIR=%LOCAL_BASE"\r\n'
        f'set "LOCAL_EXE=%LOCAL_DIR%\\{dist_name}.exe"\r\n'
        '\r\n'
        'if exist "%REPO_DIR%\\AgentMemory" (\r\n'
        '  set "AGENT_MEMORY_DATA_DIR=%REPO_DIR%\\AgentMemory"\r\n'
        ') else if exist "%REPO_DIR%\\data" (\r\n'
        '  set "AGENT_MEMORY_DATA_DIR=%REPO_DIR%\\data"\r\n'
        ') else (\r\n'
        '  set "AGENT_MEMORY_DATA_DIR=%REPO_DIR%\\AgentMemory"\r\n'
        ')\r\n'
        '\r\n'
        f'if not exist "%SOURCE_DIR%\\{dist_name}.exe" (\r\n'
        '  echo [AgentMemorySync] OneDrive package not found: %SOURCE_DIR%\r\n'
        '  echo Please run "python build.py" once on any device to generate the package.\r\n'
        '  exit /b 1\r\n'
        ')\r\n'
        '\r\n'
        'set "NEED_COPY=0"\r\n'
        'if not exist "%LOCAL_EXE%" set "NEED_COPY=1"\r\n'
        f'if exist "%SOURCE_DIR%\\{dist_name}.exe" if exist "%LOCAL_EXE%" (\r\n'
        f'  for %%I in ("%SOURCE_DIR%\\{dist_name}.exe") do set "SRC_TIME=%%~tI"\r\n'
        f'  for %%I in ("%LOCAL_EXE%") do set "LOCAL_TIME=%%~tI"\r\n'
        '  if /I not "%SRC_TIME%"=="%LOCAL_TIME%" set "NEED_COPY=1"\r\n'
        ')\r\n'
        '\r\n'
        'if "%NEED_COPY%"=="1" (\r\n'
        '  echo [AgentMemorySync] Synchronizing local runtime copy...\r\n'
        '  set "TARGET_DIR=%LOCAL_BASE%"\r\n'
        '  set "COPY_RC=0"\r\n'
        '  if exist "!TARGET_DIR!" rmdir /s /q "!TARGET_DIR!" >nul 2>nul\r\n'
        '  if exist "!TARGET_DIR!" (\r\n'
        '    set "TARGET_DIR=%TEMP%\\AgentMemorySync_Run_%RANDOM%_%RANDOM%"\r\n'
        '    echo [AgentMemorySync] Primary runtime dir is busy, using fallback: !TARGET_DIR!\r\n'
        '  )\r\n'
        '  robocopy "%SOURCE_DIR%" "!TARGET_DIR!" /MIR >nul\r\n'
        '  set "COPY_RC=!ERRORLEVEL!"\r\n'
        '  if not "!COPY_RC!"=="0" if not "!COPY_RC!"=="1" if not "!COPY_RC!"=="2" if not "!COPY_RC!"=="3" if not "!COPY_RC!"=="4" if not "!COPY_RC!"=="5" if not "!COPY_RC!"=="6" if not "!COPY_RC!"=="7" (\r\n'
        '    set "TARGET_DIR=%TEMP%\\AgentMemorySync_Run_%RANDOM%_%RANDOM%"\r\n'
        '    echo [AgentMemorySync] Primary copy failed, retrying with fallback: !TARGET_DIR!\r\n'
        '    robocopy "%SOURCE_DIR%" "!TARGET_DIR!" /MIR >nul\r\n'
        '    set "COPY_RC=!ERRORLEVEL!"\r\n'
        '  )\r\n'
        '  if not "!COPY_RC!"=="0" if not "!COPY_RC!"=="1" if not "!COPY_RC!"=="2" if not "!COPY_RC!"=="3" if not "!COPY_RC!"=="4" if not "!COPY_RC!"=="5" if not "!COPY_RC!"=="6" if not "!COPY_RC!"=="7" (\r\n'
        '    echo [AgentMemorySync] Failed to copy. Please check directory permissions.\r\n'
        '    exit /b 1\r\n'
        '  )\r\n'
        '  set "LOCAL_DIR=!TARGET_DIR!"\r\n'
        f'  set "LOCAL_EXE=!LOCAL_DIR!\\{dist_name}.exe"\r\n'
        ')\r\n'
        '\r\n'
        'echo [AgentMemorySync] data=%AGENT_MEMORY_DATA_DIR%\r\n'
        'echo [AgentMemorySync] source=%SOURCE_DIR%\r\n'
        'echo [AgentMemorySync] exe=%LOCAL_EXE%\r\n'
        'start "" /D "%LOCAL_DIR%" "%LOCAL_EXE%"\r\n'
        'exit /b 0\r\n'
    )
    bat_path.write_text(bat_content, encoding="ascii")
    return bat_path


def _cleanup_old_backups(parent_dir: Path, name_prefix: str, keep: int = 1):
    """清理同目录下的 .old_<timestamp> 备份，保留最近 keep 个。

    OneDrive 锁定 fallback 会生成 <name>.old_<14位时间戳> 目录，
    本函数扫描同目录下所有匹配的备份，按时间戳倒序保留最近 keep 个，
    删除更老的。避免无限累积。
    """
    import re
    pattern = re.compile(r"^{}\.old_(\d{{14}})$".format(re.escape(name_prefix)))
    backups = []
    try:
        for item in parent_dir.iterdir():
            m = pattern.match(item.name)
            if m and item.is_dir():
                backups.append((m.group(1), item))
    except Exception:
        return
    backups.sort(key=lambda x: x[0], reverse=True)  # 新到旧
    for _, old_path in backups[keep:]:
        try:
            shutil.rmtree(old_path, ignore_errors=True)
            # OneDrive 下 rmtree 可能失败，用 PowerShell 兜底
            if old_path.exists():
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Remove-Item -Recurse -Force '{}' -ErrorAction SilentlyContinue".format(old_path)],
                    capture_output=True,
                    creationflags=0x08000000,
                    timeout=30,
                )
            if old_path.exists():
                print(f"[警告] 旧备份无法删除: {old_path.name}")
            else:
                print(f"[清理] 删除旧备份: {old_path.name}")
        except Exception:
            pass


def _safe_remove_dir(path: Path):
    """尽量删除/重命名一个目录，OneDrive 锁定时退化为重命名到 .old_<时间戳>。

    rename 成功后自动调用 _cleanup_old_backups 保留最近 1 个备份，
    避免无限累积 .old_* 目录。
    """
    if not path.exists():
        return
    try:
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
    except Exception:
        pass
    # OneDrive 可能持有句柄导致 rmtree 失败：重命名为 .old_ 时间戳
    try:
        stamp = time.strftime("%Y%m%d%H%M%S")
        renamed = path.parent / f"{path.name}.old_{stamp}"
        path.rename(renamed)
        print(f"[警告] {path} 被 OneDrive 锁定，已重命名为 {renamed.name}")
        # 自动清理旧备份，保留最近 1 个
        _cleanup_old_backups(path.parent, path.name, keep=1)
    except Exception as e:
        # 真没救：尝试 robocopy + rmdir
        try:
            subprocess.run(
                ["cmd", "/c", "rmdir", "/s", "/q", str(path)],
                check=False,
                timeout=30,
            )
        except Exception:
            raise RuntimeError(f"无法清理 OneDrive 目录 {path}: {e}")


def _sync_repo_bundle(source_dir: Path, project_dir: Path, dist_name: str) -> Path:
    """把构建结果同步回项目根目录，供 OneDrive 跨设备分发。

    若固定目录被运行中的旧版本占用，则回退到带时间戳的新目录，
    并写入 current pointer 供 launcher 读取。
    """
    preferred_dir = project_dir / dist_name
    bundle_dir = preferred_dir
    print(f"正在同步 OneDrive 包: {preferred_dir}")
    try:
        _safe_remove_dir(preferred_dir)
        time.sleep(0.5)
        shutil.copytree(source_dir, preferred_dir)
    except Exception as e:
        fallback_dir = _next_available_dir(project_dir, dist_name)
        print(f"[警告] 固定包目录不可用，改用 fallback 包: {fallback_dir.name} ({e})")
        shutil.copytree(source_dir, fallback_dir)
        bundle_dir = fallback_dir
    _write_bundle_pointer(project_dir, dist_name, bundle_dir)
    return bundle_dir


def _install_local(source_dir: Path, dist_name: str, project_dir: Path = None) -> Path:
    """把构建结果复制到可运行位置（优先 Temp，避免 OneDrive 和权限限制）"""
    temp_base = Path(os.environ.get("TEMP", "."))
    preferred_dir = temp_base / "AgentMemorySync_Run"
    local_dir = preferred_dir
    local_exe = local_dir / f"{dist_name}.exe"

    print(f"正在安装到: {preferred_dir}")
    try:
        _safe_remove_dir(preferred_dir)
        time.sleep(0.5)
        shutil.copytree(source_dir, preferred_dir)
    except Exception as e:
        local_dir = _next_available_dir(temp_base, "AgentMemorySync_Run")
        local_exe = local_dir / f"{dist_name}.exe"
        print(f"[警告] 固定本地运行目录不可用，改用 fallback: {local_dir.name} ({e})")
        shutil.copytree(source_dir, local_dir)

    # 创建快捷方式：优先指向项目根目录的 bat（保留 OneDrive 同步能力 + 带图标）
    # bat 不存在时回退到本地 exe（兼容旧场景）
    desktop, start_menu = _get_shortcut_paths()
    icon = local_dir / "_internal" / "assets" / "app_icon.ico"
    if not icon.exists():
        icon = source_dir / "_internal" / "assets" / "app_icon.ico"

    # 优先用项目根目录的 bat 作为快捷方式目标
    bat_target = None
    if project_dir is not None:
        bat_candidate = project_dir / f"{dist_name}.bat"
        if bat_candidate.exists():
            bat_target = bat_candidate
        # 项目根的图标优先（更稳定，不依赖 TEMP 副本）
        project_icon = project_dir / "assets" / "app_icon.ico"
        if project_icon.exists():
            icon = project_icon

    # 快捷方式目标：bat 优先（带更新同步），否则回退到本地 exe
    shortcut_target = bat_target if bat_target is not None else local_exe

    shortcuts = []
    if desktop.exists():
        sc = desktop / f"{dist_name}.lnk"
        if _create_shortcut(shortcut_target, sc, icon):
            shortcuts.append(sc)
    if start_menu.exists():
        sc = start_menu / f"{dist_name}.lnk"
        if _create_shortcut(shortcut_target, sc, icon):
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

    # 输出到本地 Temp 目录，避免 OneDrive 路径锁定导致无法清理旧构建
    temp_build = Path(os.environ.get("TEMP", here)) / "AgentMemoryBuild"
    temp_work = Path(os.environ.get("TEMP", here)) / "AgentMemoryWork"
    for d in [temp_build, temp_work]:
        if d.exists():
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass
        d.mkdir(parents=True, exist_ok=True)
    cmd.extend(["--distpath", str(temp_build), "--workpath", str(temp_work)])

    result = subprocess.run(cmd, cwd=str(here))

    if result.returncode == 0:
        source_dir = temp_build / dist_name
        exe_path = source_dir / f"{dist_name}.exe"
        if exe_path.exists():
            exe_size = exe_path.stat().st_size / 1024 / 1024
            internal_size = sum(
                f.stat().st_size for f in (source_dir / "_internal").rglob("*") if f.is_file()
            ) / 1024 / 1024
            print()
            print("=" * 60)
            print("打包成功！")
            print(f"输出目录: {source_dir}")
            print(f"EXE 大小: {exe_size:.1f} MB")
            print(f"依赖大小: {internal_size:.1f} MB")
            print("=" * 60)
            print()
            # 1) 把完整 OneDrive 分发包同步回项目根目录（供跨设备分发）
            try:
                bundle_dir = _sync_repo_bundle(source_dir, here, dist_name)
                print(f"[OneDrive 包] {bundle_dir}")
            except Exception as e:
                print(f"警告：同步 OneDrive 包失败: {e}")
                bundle_dir = None
            # 2) 安装到本地并创建快捷方式（用户实际从这里启动）
            try:
                local_dir, shortcuts = _install_local(source_dir, dist_name, project_dir=here)
                print(f"[本地运行副本] {local_dir}")
                for sc in shortcuts:
                    print(f"[快捷方式] {sc}")
            except Exception as e:
                print(f"\n警告：本地安装失败: {e}")
                return
            # 3) 生成跨设备启动器 BAT（项目根目录唯一用户入口）
            try:
                launcher = _write_repo_launcher(here, dist_name)
                print(f"[跨设备启动器] {launcher}")
            except Exception as e:
                print(f"警告：写入跨设备启动器失败: {e}")

            print("=" * 60)
            print("使用方法（推荐）：")
            print(f"  双击项目根目录的 {dist_name}.bat（任意设备都可用）")
            print("  或双击桌面快捷方式")
            print("=" * 60)
            print("运行模型：")
            print(f"  - OneDrive 项目里只放 {dist_name}/（同步包）+ data/（共享数据）")
            print(f"  - 实际运行永远是机器本地的 %TEMP%\\{dist_name}_Run\\AgentMemorySync.exe")
            print(f"  - OneDrive 里的 EXE 请勿直接双击（托盘可能不显示）")
            print()
            print("注意：")
            print("  - 重新构建后再次运行 AgentMemorySync.bat 即可同步本地副本")
            print("  - 如必须从 OneDrive 直接运行，程序会尝试自动迁移到本地（兜底）")

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
