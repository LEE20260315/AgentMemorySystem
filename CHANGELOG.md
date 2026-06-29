# Changelog

本项目的所有重要变更都记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [v1.3.2] - 2026-06-25

### Fixed

- **消除 `disk I/O error`（OneDrive 路径上同步崩溃）**：`MemoryDatabase._init_db()` 增加 `timeout=60` 连接、`PRAGMA busy_timeout=60000` 内部重试、`PRAGMA journal_mode=DELETE`（取代 WAL）、`PRAGMA synchronous=NORMAL`、`PRAGMA cache_size=-20000` 20MB 缓存
- **消除"卡死数百秒"的错觉**：新增 `MemoryDatabase.insert_memories_batch()`，一次事务插 50 条；`extract_local_to_fused` 改为 50 条批量 + 每批 emit 进度回调；UI 不再看得见空白
- **消除启动器 SQLite 锁导致的根因**：FTS5 + WAL 在 OneDrive 上间歇性 `disk I/O error` 是主因；改 DELETE 模式后 OneDrive 不再等 SHM/WAL 文件句柄
- **修复同步产物被再次提取导致递归膨胀**：在文件扫描、Markdown / Hermes / JSONL 解析、提取入库三层同时过滤 `shared_from_agents.md`、`## Shared Knowledge`、`[sync:mem_*]` 等写回产物，阻断自我回灌
- **修复跨运行重复提取**：`extract_local_to_fused()` 在入库前按标准化内容做跨运行去重，同一条本地记忆再次扫描时只记为 skipped，不再重复写入 `agent_*.db` / `shared.db`

### Changed

- **数据目录调整**：根目录默认从"项目内 `data/`"改为 **OneDrive 下的 `AgentMemory/`**（v1.2 时代的融合层）。`safe_io.get_data_root()` 重写解析顺序：`AGENT_MEMORY_DATA_DIR` → OneDrive/AgentMemory/ → EXE 同级 `data/` → LOCALAPPDATA；`memory_sync_app._data_dir()` 委托给同一个函数避免双轨
- **BAT 启动器 (`AgentMemorySync.bat`) 默认指向 `OneDrive/AgentMemory/`**：`<REPO_DIR>\AgentMemory` 存在则用之，否则回退到 `<REPO_DIR>\data`（兼容 v1.3.x 部署）
- 设置面板 (`SettingsDialog`) 全面可滚动 + **Agent 路径覆盖折叠到高级**：12+ 个 agent 不再裁切；窗口高度自适应，最大屏高 85%
- `Pyproject` / `BAT` / `memory_sync_app.py` / `safe_io.py` / `agent_memory.py` 等多处升级到 OneDrive 友好版本

### Added

- 新增 `tools/migrate_v13_to_v14.py`：一次性迁移脚本，把项目内 `data/` 合并到 OneDrive `AgentMemory/`，按 SQLite PRIMARY KEY 与 md 块 hash 去重；写入前自动备份
- 新增 `MemoryDatabase.insert_memories_batch()`：批量插入接口（自动事务 / fsync 一次 / tag 缓存）

### Security / Privacy

- 迁移后产生的 `data/` 仍在 `.gitignore` 内（`data/`、`*.db`）；不会进入仓库
- 迁移脚本写入前自动备份 `OneDrive/AgentMemory/` 到 `OneDrive/AgentMemory._backup_<时间戳>`，可手动还原
- 本轮清理前额外备份污染库到 `%TEMP%/AgentMemorySystem_cleanup_backup_<时间戳>/`，用于回看递归样本与紧急回滚

## [v1.3.1] - 2026-06-25

### Fixed

- 恢复并稳态化系统托盘常驻：跨设备启动器在每台机器上把 OneDrive 里的 `AgentMemorySync/` 分发包同步到 `%TEMP%\AgentMemorySync_Run\`，本地副本启动，从此避免直接从 OneDrive 目录运行 EXE 触发 `Shell_NotifyIconW` 失败
- `_data_dir()` 升级支持 `AGENT_MEMORY_DATA_DIR` 环境变量优先：跨设备启动器把数据目录显式绑定回 OneDrive 项目的 `data/` 子目录，保证本地副本与 OneDrive 数据真正共用
- `build.py` 同步 OneDrive 分发包时遇到 OneDrive 句柄锁导致 `WinError 183`：增加 `_safe_remove_dir()` 重命名兜底，让旧分发包稳定被替换，不再出现"打包失败"
- 跨设备启动器原计划包含中文提示，遇到编码问题；改为纯 ASCII + 显式变量回显，兼容 Windows 老版本 cmd.exe 与 ISE

### Changed

- `build.py` 流程改造：先构建到本地 Temp，再拷贝一份干净的 `AgentMemorySync/` 同步到项目根目录供 OneDrive 分发，最后安装一份到 `%TEMP%\AgentMemorySync_Run\` 作为本地运行副本
- 编写并提交跨设备启动器 `AgentMemorySync.bat`，仅作为项目唯一用户入口（双击即可在任意设备运行；不直接运行 OneDrive 包内 EXE）

### Removed

- 移除本轮排查遗留的本地日志（`bootstrap.log` / `early_boot.log` / `build_v3.txt` / `test_output.txt` / `$null` 等）和多个临时构建目录（`build_output/`、`AgentMemorySync.old_*/`）

### Security / Privacy

- 项目内不再附带任何用户路径、个人数据、real log：所有运行时数据写入 `.gitignore` 之外的 `data/`，不会进入 Git 仓库

## [Unreleased]

### Added

- 新增 CodeBuddy Agent 检测支持（`~/.codebuddy/memery/*_memery.md`）
- 新增 `safe_io.get_data_root()` 统一路径解析函数
- 新增 `ConfirmDialog` 统一风格确认对话框（替代系统 messagebox）
- 新增 `_copy_overwrite()` 构建后备方案（重命名失败时逐个复制文件）

### Fixed

- 修复 CodePilot SQLite 读取失败：改用只读 URI 连接（`mode=ro&immutable=1`），不再与 CodePilot 进程争抢 WAL 锁
- 修复 CodePilot MEMORY.md（230MB）可能被误读导致内存耗尽：超 1MB 时只读最后 512KB
- 修复 `_data_dir()` 非打包模式下 `sys.executable` 指向 python.exe 导致数据写入 Python 安装目录
- 修复 `sync_engine.rollback()` 引用不存在的 `self.report` 属性（改为 `self._last_report`）
- 修复写回时 `backup_dir=None` 禁用了所有备份，导致回滚功能完全失效
- 修复 `sync_engine` 在打包模式下 `Path(__file__).parent / "data"` 指向 _MEIPASS 临时目录
- 修复 `SyncState` 在打包模式下 `Path.home()` 可能异常
- 修复通知中 `agents_found`（不存在）改为 `agents_detected`（正确字段）
- 修复 `__init__` 中 `load_settings()` 被调用两次
- 修复 IMA Copilot（Chromium 壳）被误扫描导致同步缓慢
- 修复 `agent_memory.py` 中 `\A` 无效转义序列警告
- 修复 `agent_memory.py` 8.3 短路径 bug：`str.replace("~", str(home))` → `os.path.expanduser(pattern)`
- 修复 `memory_sync_app.py` `_WILL_RELOCATE` 重置逻辑：迁移失败返回时重置为 `False`
- 修复 `test_full.py` mock patch 目标：`patch.object(am, "detect_agents")` → `patch("sync_engine.detect_agents")`

### Fixed (Earlier)

- 修复 EXE 打包缺少 tkinter.ttk 导致启动崩溃（ImportError: cannot import name 'ttk'）
- 修复托盘依赖安装：pythonw.exe 不支持 pip，改用同目录的 python.exe 安装
- 修复启动器 python/pythonw 路径不一致：从 python 路径推导 pythonw，确保同一安装
- 修复 Agent 检测缓存被测试污染问题（test_detect_agents 写入全局缓存导致真实检测失败）
- 修复 VBScript 启动器 UTF-8 编码问题（改用纯英文避免 Windows Script Host 解析失败）

### Changed (Earlier)

- 启动器重命名：`双击运行.bat` → `dev_run.bat`，`启动记忆同步.vbs` → `dev_run.vbs`（仅开发用）

### Added (Earlier)

- CodePilot Agent 支持：自动检测 `~/.codepilot/codepilot.db`，从 SQLite 导出对话历史为 Markdown
- 导出时自动过滤敏感信息（API 密钥、密码、token 等），8 种模式脱敏
- 通用 Agent 发现机制：`_discover_generic_agents()` 自动扫描常见 AI 工具目录
- PyInstaller 打包支持：`python build.py` 生成单文件 EXE（~18MB），内置所有依赖
- 应用窗口图标和托盘图标使用 `assets/icon.ico`
- 同步日志显示各阶段路径：融合层目录、Agent 源路径、共享数据库、写回目标
- 定时自动同步调度器：基于 `auto_interval_hours` 设置自动触发同步

### Changed (Earlier)

- EXE 输出位置从 `dist/` 改为项目根目录
- 打包后自动清理 `build/`、`dist/`、`*.spec` 临时文件
- 排除 15 个不需要的 PIL 子模块，EXE 体积从 20MB 降至 18MB
- `.gitignore` 新增 `AgentMemorySync.exe`、`device_config.json`
- 自动同步间隔从"天"改为"小时"，默认 2 小时，选项：1/2/4/8/16/24/48/72 小时
- 设置对话框点 X 关闭时自动保存（之前只有点"保存"按钮才生效）
- 新图标：app_icon.ico（主窗口）、tray_icon.png（托盘），由用户提供
- 同步完成后托盘气泡通知：显示设备名、Agent 数量、提取/写回条数、错误数
- Windows 原生通知兜底：pystray 不可用时自动降级为 Shell_NotifyIcon

### Fixed

- 修复 Hermes 锁文件残留问题：sync_writers.py 的手动锁没有过期检测，程序异常退出后锁文件永久残留。新增 60 秒过期自动清理
- 修复通用 Writer（GenericMarkdownWriter）同样的锁文件残留问题
- 修复 PyInstaller 打包 PIL 模块不全导致托盘崩溃：改用 `--collect-submodules PIL` 一次性收集所有 PIL 子模块
- 修复托盘图标创建后窗口隐藏过快：添加 0.3s 等待确保图标注册完成
- 修复 build.py 图标路径仍为旧 `icon.ico`，改为 `app_icon.ico`
- 托盘创建失败时自动写入日志文件 `~/.agent_memory/tray_error.log` 方便排查
- 修复最小化到托盘不生效：`_on_close()` 中 `and self.tray_icon` 条件导致 tray_icon 未创建时直接跳过
- 修复 build.py 图标路径仍为旧 `icon.ico`，改为 `app_icon.ico`
- 日志背景从黑色改为白色，文字改为黑灰色
- 窗口标题改为"多Agent记忆融合器"

### Added

- 重复启动检测：Windows 命名互斥锁（Global\AgentMemorySyncMutex），重复双击时弹窗提示"已在运行中，请检查系统托盘"

### Fixed (Earlier)

- 修复 pyproject.toml build-backend（setuptools.backends._legacy → setuptools.build_meta）
- 修复 pyproject.toml 版本号（0.1.0 → 1.3）并添加依赖声明
- 清理 docs/ 中 MR.Dong 硬编码路径（设计文档附录 A 改为占位符）
- 删除一次性部署脚本 docs/_apply_onedrive_changes.py（已被 detect_agents + extract 替代）
- 删除重复文档 docs/V1.3_AUTOMATION.md（内容已并入 README + DEVLOG）
- 清理 data/ 目录中真实运行时数据（个人用户名、真实记忆内容、SQLite 数据库）
  原数据完整保留在 data.local.bak/，data/ 重建为干净结构 + .gitkeep + README
- 修复 fsync_file() Windows 兼容性（添加 PermissionError 降级处理）
- 修复文件锁 TOCTOU 竞态条件（改用 os.O_CREAT | O_EXCL 原子创建）
- 修复 recover_pending_if_exists() 非原子性（改用临时文件 + rename 模式）
- 修复 now_iso8601() 缺少时区信息（改为 UTC 时间）
- 添加 ID 序号溢出保护（超过 999 条时抛出异常）
- 修复 load_private_memories() 只加载 memory_private.md 的问题，改为扫描所有 memory_private*.md 文件
- 修复 happy_path 和 write_and_reload 测试失败（适配设备专属文件命名）
- 修复 Hermes 路径 bug（AppData/Local/hermes/memories 不在 _AGENT_SUBDIRS 中）

### Added

- 创建 pyproject.toml 和 requirements.txt
- 创建 README.md
- 配置管理系统：config.json + ConfigManager 类，替代硬编码参数
- 日志系统：LogManager 类，自动记录操作日志到 .logs/ 目录，支持轮转
- 敏感信息检测：SensitiveInfoDetector 类，写入时自动检测密码/密钥/token
- 记忆过期机制：expire_old_memories() 函数，自动归档超过 max_memory_age_days 的记忆
- 数据库版本管理：metadata 表 + migrate_database() 函数，支持结构迁移
- 健康检查：health_check() 函数，检查配置/数据库/文件/磁盘空间等
- 新增 CLI 命令：health、expire、migrate
- 核心函数集成日志：startup、write_memory、search_memory、sync_markdown_to_db、full_sync、smart_compress、archive_cold_memories
- 核心函数集成敏感信息检测：write_memory 写入前自动检测
- ConfigManager 集成：DataProtection、TieredStorageManager、ConcurrentWriteManager、SmartCompressor 均从 config.json 读取参数
- 更新 README.md：新增系统运维章节（健康检查、过期清理、配置文件、日志系统、敏感信息检测）
- v1.3 自动化扩展：AgentRegistry（自动发现）、LocalMemoryParser（多格式解析）、TriggerEngine（关键词触发器）、SessionFlusher（会话落盘钩子）
- 新增 CLI 命令：discover、dashboard、extract、flush
- setup_agent.py：初始化时自动创建 triggers.yaml 并注册到 Registry
- requirements.txt：新增 pyyaml>=6.0
- 鲁棒性 Agent 路径探测：detect_agents() 函数，候选路径 + 特征验证 + 缓存 + 手动覆盖
- OneDrive 冲突文件检测：check_onedrive_conflicts() 函数
- 写回适配器：ClaudeMemoryWriter、TraeMemoryWriter、HermesMemoryWriter，按各 Agent 格式追加共享记忆
- 同步去重：基于 content hash 的 SyncState，避免重复写入
- 同步引擎：SyncEngine 类，完整流程（detect → extract → merge → write_back）
- GUI 同步工具：memory_sync_app.py，tkinter 主窗口 + pystray 系统托盘常驻
- 设置面板：自动同步间隔、OneDrive 冲突处理、Agent 路径覆盖
- 备份与回滚：每次同步备份原文件，支持一键回滚
- 新增 CLI 命令：full-sync（完整同步）、redetect（重新检测 Agent）
- requirements.txt：新增 pystray、Pillow
- 数据目录迁移：从 OneDrive/AgentMemory/ 迁移到项目内 data/ 目录
- GUI 美化：macOS 风格配色 + 卡片式布局 + 状态指示器
- 项目清理：删除 __pycache__、过程文件，设计文档归档到 docs/
- 一键启动器：双击运行.bat，自动检查依赖并启动 GUI
- README.md 重写：开源级完整文档，含依赖说明、工作原理、常见问题

### Fixed (v1.3 质量修复)

- 修复所有硬编码路径（AgentRegistry、extract_local_to_fused、TriggerEngine、CLI --root），改用 Path.home() 动态构建
- 统一版本号：v1.4 → v1.3（代码、测试、文档）
- TriggerEngine YAML 解析：优先用 pyyaml，fallback 到简单解析器
- SessionFlusher.flush()：新增内容去重，避免重复条目写入
- extract_local_to_fused：改用 write_memory() 完整流程（敏感信息检测、日志、SQLite 同步）
- parse_jsonl：删除 500 字硬截断，存完整内容

## [v0.1.0] - 2026-05-20

### Added

- 多 Agent 记忆同步系统核心库 (`agent_memory.py`)
- 启动流程（8 步完整实现）
- 写入流程（落盘 7 步，MVP 无缓冲/无节流/无去重）
- 基于文件的分布式锁机制（带死锁超时检测）
- .pending 文件恢复机制
- OneDrive 冲突文件检测
- 自定义异常体系（6 种异常类型）
- 记忆条目 Markdown 解析与格式化
- 设备配置文件 (`device_config.json`)
- 一次性部署脚本 (`_apply_onedrive_changes.py`)
- 5 个测试用例 (`test_memory.py`)

