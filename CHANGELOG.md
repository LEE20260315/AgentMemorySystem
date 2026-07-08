# Changelog

本项目的所有重要变更都记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [1.3.6] - 2026-07-08

### Fixed

- **修复托盘圖示靜默消失問題**（辦公室與家中兩台電腦均復現）：根因是 `_tray_wndproc` 回呼在每次滑鼠移過托盤圖示（`WM_MOUSEMOVE`，`lparam=512`）時都對 OneDrive 目錄下的 `tray_error.log` 做 `open/write/close`。OneDrive 同步鎖檔時回呼阻塞，Windows 因 wndproc 超時強制終止行程，Python 來不及記錄任何日誌。修復後 wndproc 不再做任何檔案 I/O，僅處理左鍵/右鍵點擊事件，其餘事件直接 `return 0`。

### Added

- **心跳日誌**：`_heartbeat()` 每 5 分鐘向 `tray_error.log` 寫入一次存活狀態（計數、時間戳、托盤是否啟用、是否同步中）。行程崩潰後心跳停止，下次啟動可據此判斷上次行程的死亡時刻
- **全域崩潰捕獲**：`_setup_crash_handlers()` 安裝 `sys.excepthook` + `threading.excepthook` + `atexit`，主/子線程未捕獲異常會寫入 `=== CRASH ===` 區塊（含完整 traceback），正常退出時寫入 `APP EXIT` 記錄
- **mainloop 自動重啟**：`main()` 用 `try/except` 包裹 `mainloop()`，崩潰後記錄日誌並自動重啟（最多 3 次，每次間隔 2 秒），超過上限彈窗提示使用者查看日誌後手動重啟。避免托盤靜默消失後使用者需手動重啟

## [1.3.5] - 2026-07-06

### Added

- **同步進度條**：`_start_sync` 啟動時顯示不確定型進度條，根據 `SyncEngine` 進度消息自動切換階段文字（檢測 Agent 中 / 提取記憶中 / 融合共享中 / 寫回 Agent 中 / 已完成），同步完成自動隱藏
- **日誌彩色 tag**：`_log()` 支援 `level` 參數與自動推斷，時間戳灰、錯誤紅、成功綠、警告橙、信息藍；在 `tk.Text` 上配置 `timestamp` / `error` / `success` / `warning` / `info` 五個 tag
- **錯誤卡片**：同步失敗時在主窗口頂部顯示紅色邊框錯誤卡片（標題 + 錯誤詳情 + 關閉按鈕），不再只靠日誌排查
- **統計數值狀態著色**：同步完成動態切換數值標籤樣式（`StatSuccess.TLabel` 綠 / `StatWarning.TLabel` 橙 / `StatError.TLabel` 紅）
- **COLORS token 擴展**：新增進度條（`progress_bg`/`progress_fill`/`progress_trough`）、日誌彩色（`log_timestamp`/`log_error`/`log_success`/`log_warning`/`log_info`）、錯誤卡片（`error_card_bg`/`error_card_border`）、統計狀色（`stat_success`/`stat_warning`/`stat_error`）、卡片陰影（`card_shadow`）
- **ttk 樣式新增**：`Horizontal.TProgressbar`（4px 細條）、`Stage.TLabel`、`ErrorTitle.TLabel`、`ErrorBody.TLabel`、`StatSuccess/Warning/Error.TLabel`；`Vertical/Horizontal.TScrollbar` 加 `active` hover map

### Changed

- **PIL 狀態點升級**：`_make_status_dot_image` 改為三層疊加繪製 —— 外圈半透明光暈（alpha=50）+ 中圈主光暈（alpha=90）+ 實心圓點 + 左上角中心高光（alpha=120），超採樣 4x 後 LANCZOS 縮小，視覺更精緻

### Fixed

- 修復托盤通知 `agents_found` 屬性不存在 bug：`SyncReport` 真實屬性名為 `agents_detected`，原寫法導致托盤通知 Agent 數永遠顯示 0

## [1.3.4] - 2026-07-02

### Changed

- **資料目錄回歸專案根**：`safe_io.get_data_root()` 解析邏輯簡化，專案根 `AgentMemory/` 成為預設位置（v1.3.2 曾改為 OneDrive 根下，但造成資料與專案割裂、雙 OneDrive 帳號定位錯誤等問題）
- 去除 OneDrive 環境變數獨立探測邏輯（`OneDrive`/`OneDriveConsumer`/`OneDriveCommercial` 掃描），跨裝置同步靠專案資料夾本身在 OneDrive 下即可
- 快捷方式圖示改用本地 TEMP 副本（`%TEMP%\AgentMemorySync_Run\_internal\assets\app_icon.ico`），修復 OneDrive 雲佔位符導致的白底圖示問題
- `build.py` 圖示選擇邏輯調整：本地副本優先 > 源構建目錄 > 專案根（原專案根優先會觸發雲佔位符問題）

### Added

- v1.3.4 升級自動遷移：`_migrate_old_data()` 自動從舊位置 `OneDrive\AgentMemory\` 複製資料到新位置 `專案根\AgentMemory\`（保留舊目錄作為備份，避免 OneDrive 同步衝突導致資料遺失）

### Fixed (2026-07-03 補丁，版本號不變)

- 修復小螢幕筆記本視窗顯示不全：保存的 `window_geometry` 直接套用未校驗是否超出當前螢幕。在解析保存的幾何後按當前螢幕工作區（`screen_w-40` / `screen_h-100`）裁剪寬高，並丟棄舊位置座標，由 `_center_window` 重新居中，避免換螢幕/多顯示器後視窗跑到屏外
- 修復 `_center_window` 只裁高度不裁寬度、且 `winfo_width()` 在某些時序下返回 1 的問題，新增 geometry 字串解析回退路徑
- 修復定時自動同步不生效：調度器 `_check()` 誤用 `auto_start`（啟動時立刻同步一次）作為門控，導致只設定間隔未勾「啟動時自動執行同步」時定時同步永不觸發、主界面同步日誌也無顯示。新增獨立開關 `auto_sync` 與 `auto_start` 解耦，設置面板新增「啟用定時自動同步」複選框
- 修復 `auto_interval_hours` 在調度器創建時閉包捕獲導致改設置後不重啟不生效的問題，改為每次檢查時動態讀取
- 修復 `_last_sync_time` 初始值為 0 導致首次檢查因 elapsed 巨大立刻觸發同步的問題，改為初始化為當前時間
- 啟用/保存定時同步時在主界面同步日誌輸出調度器狀態，觸發同步時打印間隔與距離上次同步的分鐘數，便於用戶確認調度器已就緒

## [1.3.3] - 2026-07

### Fixed

- 修复 EXE 打包缺少 tkinter.ttk 导致启动崩溃（ImportError: cannot import name 'ttk'）
- 修复托盘依赖安装：pythonw.exe 不支持 pip，改用同目录的 python.exe 安装
- 修复启动器 python/pythonw 路径不一致：从 python 路径推导 pythonw，确保同一安装
- 修复 Agent 检测缓存被测试污染问题（test_detect_agents 写入全局缓存导致真实检测失败）
- 修复 VBScript 启动器 UTF-8 编码问题（改用纯英文避免 Windows Script Host 解析失败）

### Changed

- 启动器重命名：`双击运行.bat` → `dev_run.bat`，`启动记忆同步.vbs` → `dev_run.vbs`（仅开发用）

### Added

- CodePilot Agent 支持：自动检测 `~/.codepilot/codepilot.db`，从 SQLite 导出对话历史为 Markdown
- 导出时自动过滤敏感信息（API 密钥、密码、token 等），8 种模式脱敏
- 通用 Agent 发现机制：`_discover_generic_agents()` 自动扫描常见 AI 工具目录
- PyInstaller 打包支持：`python build.py` 生成单文件 EXE（~18MB），内置所有依赖
- 应用窗口图标和托盘图标使用 `assets/icon.ico`
- 同步日志显示各阶段路径：融合层目录、Agent 源路径、共享数据库、写回目标
- 定时自动同步调度器：基于 `auto_interval_hours` 设置自动触发同步

### Changed

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
