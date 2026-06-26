# Cross-Device Launcher v1.3.1 收官文档

> 本文档记录本从库从“一段时间托盘偶发消失”到“v1.3.1 跨设备稳定运行轨”的完整结论、教训与推荐做法。
> 读者：未来接手本项目的人，以及任何在 Python + PyInstaller + OneDrive 环境上做 GUI/托盘的朋友。

## 1. 最终方案（v1.3.1）

- 项目主入口：`AgentMemorySync.bat`（位于 OneDrive 项目根目录）。
- 启动器职责只有 4 步：
  1. 比较 OneDrive 分发包与本地副本的时间戳，决定是否同步。
  2. 用 `robocopy /MIR` 把 OneDrive 里的 `AgentMemorySync/` 目录同步到本机 `%TEMP%\AgentMemorySync_Run\`。
  3. 设置环境变量 `AGENT_MEMORY_DATA_DIR=<OneDrive项目>\data`，让运行时数据写回 OneDrive 共享目录。
  4. 用 `start "" /D %LOCAL_DIR% "%LOCAL_EXE%"` 启动本地副本，托盘常驻。
- 不允许直接双击 OneDrive 包里的 `AgentMemorySync.exe`。
- 数据根目录仍以 OneDrive 的 `data/` 为准，所有机器看到的记忆是同一份。

## 2. 为什么之前会反复失败

旧版本（v1.3 之前）试图让：

- OneDrive 路径直接运行 EXE，或者
- EXE 自带 `_ensure_local_install()` 把 OneDrive 里的 EXE 复制到 `%TEMP%` 再启动，并且用它的数据目录

看似“自动”，实际踩到三类坑：

### 坑 1：OneDrive 直接跑 GUI EXE → Shell_NotifyIconW 失败
- 症状：`Shell_NotifyIconW add=0 last_error=5`
- 根因：OneDrive 同步 + Windows 11 UWP AppUserModelID 的组合对托盘 API 不友好；不是结构体大小问题，也不是 pystray 兼容性问题。

### 坑 2：`_ensure_local_install()` 自动迁移迷路
- 旧版代码从 OneDrive EXE 启动时，把 OneDrive 路径传给 `subprocess.Popen`，
  并设置 `AGENT_MEMORY_DATA_DIR` 指向 OneDrive 的 `data/`。
- 实际常因为 8.3 短路径、`subprocess.env` 行为、退避超时等导致
  「迁移看似成功、托盘失败、却又留下半启动状态」。

### 坑 3：`_data_dir()` 优先级反了
- 旧版在 Windows 下优先用 EXE 同级目录 `data/`，而不是环境变量。
- 结果运行副本里的 `data/` 永远是 `%TEMP%` 路径，
  等于跨设备数据成了「临时目录里的孤岛」，OneDrive `data/` 反而没人写。

### 坑 4：OneDrive 锁目录导致打包失败
- `build.py` 用 `shutil.rmtree` 清理旧分发包，
  当 OneDrive 还持有目录句柄时会失败 `WinError 183`，
  出现“打包失败”假象。

## 3. v1.3.1 的修法（一一对应）

| 坑 | 修法 |
|----|------|
| 坑 1 | 不再让 OneDrive 直接跑程序；改用 BAT 启动本地副本 |
| 坑 2 | 完全去掉“运行时自动迁移到本地”的依赖（保留兜底，但不再依赖） |
| 坑 3 | `_data_dir()` 增加 `AGENT_MEMORY_DATA_DIR` 优先；启动器负责注入 |
| 坑 4 | `build.py` 新增 `_safe_remove_dir()` 引入重命名兜底 |

## 4. 验证证据（关键一行）

- 启动器输出：
  - `data=C:\Users\MR.Dong\OneDrive\My Project\AgentMemorySystem\data`
  - `exe=C:\Users\<短路径>\AppData\Local\Temp\AgentMemorySync_Run\AgentMemorySync.exe`
- 托盘注册日志：
  - `RegisterClassW OK atom=...`
  - `Shell_NotifyIconW add=1 last_error=0`
- 进程路径：从 `%TEMP%` 启动，不从 OneDrive 启动 → 传给托盘图标的窗口句柄 + HICON 都在受限更少的环境下注册。

## 5. 教训与未来避免

### 关于 Python GUI + 跨设备
1. **“单 EXE 启动器”优于“EXE 自迁”**：让打包产物负责描述自己，让轻量启动器负责同步与运行，结构更清、更可调试。
2. **永远从本地路径跑 GUI 程序**：云同步盘只同步资源，不承担运行时职责。
3. **数据目录必须是显式单独的**：用 `AGENT_MEMORY_DATA_DIR` 这种独立的环境变量；绝不让运行副本的 EXE 自己决定数据目录。

### 关于 PyInstaller onedir
1. **托盘 API 与路径有关，与结构体大小无关**：`cbSize=968` 已经够了，调试要不就被 OneDrive 锁、AppData/Local 策略、PIL ImageDraw 缺打包，等等。
2. **本地运行时数据目录要显式设定**：不要依赖 `Path(sys.executable).parent / "data"` 的隐式行为。
3. **OneDrive 锁目录只能用重命名兜底**：`rmdir /s /q` + `rename` 是最稳的。

### 关于跨设备数据
1. **不要把“代码 + 数据 + 运行时”塞在一个同步目录**：要让用户看到“清晰的职责分离”。
2. **跨设备的项目应该有一个明确的“入口脚本”**：BAT、VBS、或一个真实的 launcher EXE（按预算选）。
3. **OneDrive `data/` 是“跨设备共享真相”，不是“项目目录”**：要在文档和 README 里讲清楚。

## 6. 给未来类似项目的推荐做法

1. **先决定运行模型**：
   - 谁是“同步资源”
   - 谁是“运行实例”
   - 谁是“共享真相”
2. **入口脚本只做最小事**：
   - 比对版本 / 时间戳
   - 同步本地副本
   - 设置环境变量
   - 启动本地副本
3. **启动器与 EXE 写在不同地方**：启动器可以在同步目录里，因为它是“轻量的文本脚本”；EXE 必须在本地。
4. **打包脚本的副作用清单要单测**：旧目录清理、OneDrive 锁、临时目录保留/复用、Unicode/ASCII BAT 兼容性、日常目录结构。
5. **隐私保护清单（推送前）**：
   - `.gitignore` 覆盖所有运行时数据与打包产物
   - 仓库内不再保留个人用户名、机器名、本地路径
   - 移除调试日志文件（`*.log`, `*_before.*`, 旧 `build_output*`）
   - 最终用 `git grep` 二次验证

## 7. 收官清单

- [x] 跨设备启动器（BAT）已落地并验证托盘 OK
- [x] `_data_dir()` 接受 `AGENT_MEMORY_DATA_DIR`
- [x] `build.py` OneDrive 锁目录安全兜底
- [x] 启动期排查残留清除（`bootstrap.log` / `early_boot.log` / 调试探针）
- [x] README / README_en / CHANGELOG / DEVLOG 已更新
- [x] `.gitignore` 与隐私扫描准备就绪
- [ ] Screenshot/录像：下次拓展保留

## 8. 谁来补

> 给未来的自己或接手人：

- 如果要继续演进：建议先做 **启动期 + 退出期的最小可观测性**，不要一上来就换 PyInstaller bootloader
- 如果换一台电脑：第一次用 `python build.py` 重建 → 双击 `AgentMemorySync.bat` 即可
- 如果 OneDrive 同步失败：先看本地 `%TEMP%\AgentMemorySync_Run\data\tray_error.log` 与 OneDrive `data/tray_error.log`
- 如果打包失败：看 build 输出中的 `[警告] ... 被 OneDrive 锁定，已重命名 ...`

—— 完 ——
