# Changelog

本项目的所有重要变更都记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [2.1.0] - 2026-08-06

### Fixed（稳定性）

- **统一数据根目录**：修复历史分裂——`SyncEngine` 曾硬编码 `<repo>/data`，而 GUI/SyncState/BAT 启动器使用 `AgentMemory/`（AGENT_MEMORY_DATA_DIR），导致同步引擎与状态/日志各写各的目录
  - `SyncEngine.root`、`detect_agents` 缓存、`memory_cli --root` 默认值、日志目录全部改为 `get_data_root()` 统一解析
  - 新增 `tools/unify_data_root.py` 一次性迁移脚本，将 `data/` 真实数据合并到 `AgentMemory/`
- **心跳写本地磁盘**：心跳日志主写入 `%LOCALAPPDATA%\AgentMemorySystem\heartbeat.log`，避免 OneDrive 锁阻塞主线程；数据根副本仍保留供跨设备诊断
- **崩溃日志双写**：CRASH/APP EXIT 同时写本地磁盘与数据根，OneDrive 锁时崩溃信息不再丢失
- **重启计数持久化**：崩溃重启计数写入数据根 `.restart_count`，正常退出时清除
- **FTS 索引孤儿修复（核心体积问题）**：
  - `MemoryDatabase` 新增 `delete_memory()` / `delete_memories()`，所有删除操作同步清理 `memories_fts`（历史 90%+ 孤儿行的根因）
  - 替换全部 10+ 处裸 `DELETE FROM memories` 调用点（含 sync_engine 的批量过期删除）
  - 新增 `repair_fts_orphans()` + `_repair_fts_if_needed()`，同步前自动检测修复
  - 新增 `tools/repair_fts.py`：10 个数据库已从 88.65MB 压缩至 4.86MB（回收 83.79MB）
- **旧数据清理**：`~/.agent_memory/`（6.8GB 历史遗留）重命名为 `~/.agent_memory_legacy_20260806` 移出使用路径（可恢复）

### Added（智能感）

- **知识简报 `knowledge_brief.md`**：每次同步为每个 Agent 生成精简知识摘要（top 15/域、非模板噪音、去重、≤20KB），替代让 Agent 加载数千行原始记忆
- **Agent 入口知识注入**：在 Agent 本地入口文件（`MEMORY.md` / `user_profile.md` 等）自动追加 `## Shared Knowledge (auto-synced)` 引导节，幂等（marker 去重），让 Agent 启动时真正主动加载共享知识
- **写回修复**：数据根统一后 reconcile 与状态对齐，写回从“提取 0/写回 0”恢复为实际生效（实测 492 条写回）

### Changed（UI/UX）

- **屏幕自适应**：设置对话框尺寸按屏幕可用空间裁剪（修复小屏 500x600 溢出）
- **DPI 感知**：启动时启用 Per-Monitor DPI Aware，修复高分屏模糊/错位
- **窗口尺寸自动保存**：用户拖拽主窗口大小后自动保存 geometry（防抖 1 秒）
- **新增“打开数据目录”按钮**：一键定位数据根
- **日志轮转调优**：`max_log_files=2`，日志目录统一到数据根 `.logs/`，清理旧 9.8MB 轮转文件

### Fixed（其他）

- `detect_agents` 缓存路径统一到数据根（旧版硬编码 `~/.agent_memory`）
- `memory_cli` 默认 root 从硬编码 `<repo>/data` 改为 `get_data_root()`

---

## [2.0.4] - 2026-07-31

### Fixed

- **圖示統一**：修復歷史遺留的多圖示分歧問題
  - 托盤圖示路徑從獨立的 `tray_icon.png` 改為與視窗相同的 `app_icon.ico`（`_TRAY_ICON_PATH = _ICON_PATH`）
  - `_create_default_icon()` fallback 不再生成藍色圓形 M 圖標（與 app_icon 內容差異 56.9%~61.9%），改為從 `app_icon.png` 加載並轉為 ICO
  - 刪除冗餘圖示資源 `assets/tray_icon.png`、`assets/tray_icon_64.png`
  - 保證視窗、任務欄、托盤三個場景圖示完全一致

### Removed

- **過程性文件清理**：
  - 刪除 `DEVLOG.md`（開發日誌，內容已併入 CHANGELOG.md）
  - 刪除 `test_memory.py`（舊測試套件，已由 `test_full.py` 取代）
  - 刪除 `docs/multi_agent_memory_sync_design_v1.2.md`（過程性設計文檔）
  - 刪除 `docs/assets/banner-prompt.md`（過程性提示詞文檔）
  - 刪除 `tools/migrate_v13_to_v14.py`（一次性遷移腳本，已完成歷史使命）
  - 刪除 `assets/tray_icon.png`、`assets/tray_icon_64.png`（冗餘圖示資源）

## [2.0.3] - 2026-07-31

### Added

- **寫回前體積保護（方案 0.5.6）**：`sync_writers.py` 的 `BaseMemoryWriter` 新增 `_enforce_write_volume_limit()` 方法，在 Hermes/Trae/GenericMarkdown writer 的 `write()` 寫入前檢查 content 大小，超限時按 front matter 邊界從頭部截斷舊內容，保留最新條目
  - 新增 `_truncate_head_at_boundary()` 和 `_truncate_at_boundary()` 兩個邊界感知截斷方法
  - 體積策略從 `volume_policy.json` 動態讀取，預設 256KB / 3000 行

### Changed

- **`agent_runtime_manual.md` 移除不可執行規則**：原要求 Agent 調用 `write_memory()` Python 函數，但 Agent 只能讀寫文件。改為「直接編輯 memory_private.md，在文件末尾追加 front matter 格式條目」的可執行指南
  - `id` 字段從「由系統生成, 不可手編」改為「Agent 自行生成 `mem_YYYYMMDD_HHMMSS_XXXXXX`」
  - 路徑權限從「通過 write_memory API」改為「直接編輯文件，遵循 §3 格式」

- **`data/prompt.md` 消除硬編碼路徑**：原文件包含 `C:\Users\MR.Dong\...` 絕對路徑，改為占位符 `<AgentMemorySystem 數據根目錄>` 和 `<AgentMemorySystem 安裝目錄>`，並添加路徑替換說明

- **`shrink_memory_files.py` 排序邏輯簡化**：原 6 次 sort/reverse 調用簡化為單次 `sorted()` + `reverse=True`，利用 priority 和 conf_rank 取反技巧實現三維排序（永久優先 → confidence 降序 → 時間倒序）

## [2.0.2] - 2026-07-31

### Fixed

- **徹底解決回聲污染問題**：`memory_shared.md` 中殘留的 `[sync:...]` 標記和嵌套「— 來自 xxx (date)」導致 sync 引擎寫回的內容被再次提取為新記憶，形成正反饋循環
  - `agent_memory.py` `_is_sync_generated_content()` 新增 RAW_JSON_START/END 包裝檢測和 2+ 個 echo marker 嵌套檢測
  - `sync_engine.py` 新增 `_purge_polluted_entries()` 在每次同步前清理 DB 和 .md 中的污染條目
  - `sync_engine.py` 新增 `_clean_md_files()` 從 front matter 格式的 .md 文件中移除污染條目塊
- **`extract_local_to_fused()` 硬編碼 `source_device="extracted"`**：歷史遺留問題導致同步報告設備名顯示為「extracted」
  - 改為從根目錄 `device_config.json` 解析當前機器的 source_device
- **`extract_local_to_fused()` 使用絕對路徑寫 identity.json**：多機同步時路徑不可移植
  - 改為相對路徑（`memory_root: "."`, `shared_root: "../_shared"`）
- **`_count_legacy_markers()` 語義錯誤**：統計所有 `[sync:...]` marker（含新格式），導致 legacy 計數被高估，觸發保守模式死鎖
  - 改為只統計無 `|h:` 字段的真正 legacy marker
- **`extract_target_info()` legacy 計算重複減法**：`legacy = count - len(hashes)` 在新 `_count_legacy_markers` 語義下重複扣減
  - 改為直接使用 `_count_legacy_markers()` 結果

### Changed

- 所有 `data/agent_*/identity.json` 統一改為相對路徑，支援跨設備 OneDrive 同步
- `sync_engine.py` 同步流程新增「②.5 污染清理」階段，在提取前確保 DB 和 .md 乾淨
- 測試套件 `test_full.py` 修正 6 個因 v2.0 API 變更而失敗的用例

## [1.4.0] - 2026-07-23

### Fixed

- **根治寫回始終為 0 條的核心 BUG**（`hermes=0, trae=0, codepilot=0, codebuddy=0` 但記憶檔案持續膨脹）：
  - `reconcile_with_target_hashes()` 只定義無調用 → SyncState 孤兒 hash 永不清零，目標條目數 < state 條目數導致全部跳過寫回
  - `_load_shared_memories()` 硬編碼 `LIMIT 500 + ORDER BY timestamp DESC`，每次載入相同 500 條，增量來源枯竭
  - `_is_sync_generated_content()` 匹配所有 `[sync:*]` marker → 含 marker 的用戶本體內容被誤殺
  - `_scan_agent_memory_files()` 無檔案大小上限，5MB+ 檔案直接讀入
  - 提取階段缺乏污染自愈（原僅在寫回階段自愈）
- **SyncState 去重狀態系統性偏離目標檔案真實內容**（target 內容增長但 state 不變，邏輯鎖死）

### Changed

- `sync_engine.py` `run()`：寫回前新增 `reconcile_with_target_hashes()` 調用，對齊 SyncState 與目標檔案實際 hash
- `sync_engine.py` `_load_shared_memories()`：改為增量加載（LIMIT 2000 + content_hash 跳過已寫回），不再固定返回 500 條
- `agent_memory.py` `_is_sync_generated_content()`：預檢查 sync 標記/前綴，無 sync 痕跡的原生內容直接放行；有 sync 痕跡才剝離後檢查長度 < 30 字
- `agent_memory.py` `_scan_agent_memory_files()`：所有分支添加 10MB 上限 + `_size_ok()` 校驗
- `agent_memory.py` `parse_hermes_memory()` / `parse_markdown()`：超大檔案前置污染自愈（備份 → 重建 → 解析）
- `sync_writers.py` `detect_pollution()` / `repair_polluted_file()`：提升為模塊級函數（供 agent_memory.py 提取階段復用）
- `sync_writers.py` `_repair_polluted_file()`：新增 `sync_state` 參數，自愈後清零對應 agent 的 SyncState hash 條目
- 日誌輪轉默認值：10MB → 2MB, 5 備份 → 3 備份

### Added

- **pi / pi-web Agent 精確檢測與寫回支援**：
  - `config.json` 新增 `pi` 檢測配置：`candidate_paths=['~/.pi']`, `signature_file='agent/auth.json'`
  - `_scan_agent_memory_files()` 新增 pi 專有分支：掃描 `.md`、`.jsonl`、`memory/` 子目錄
  - `WRITER_REGISTRY` 新增 `pi-web`、`pi`、`clawdbot` 寫回器
- **OpenClaw Agent 寫回支援**：`WRITER_REGISTRY` 新增 `openclaw` 映射
- **通用 Agent 發現擴展**：`ai_keywords` 追加 `pi`、`openclaw`、`deepseek`、`gemini`、`chatgpt`、`coding`、`trae`、`hermes`
- **`~/.npm-global/node_modules/@agegr` + `~/OneDrive`** 加入通用發現掃描目錄

### 實測數據

| Agent | 修復前寫回 | 修復後寫回 |
|-------|----------|----------|
| hermes | 0 | 252 |
| trae | 0/16 | 376 |
| codepilot | 0 | 297 |
| codebuddy | 0 | 208 |
| openclaw | 未檢測 | 193 |
| **pi** | **新支援** | **821** |

reconcile 自愈：trae 清理 208 條孤兒 hash。測試：129/129 全部通過。

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
