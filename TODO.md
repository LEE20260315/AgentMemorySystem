# TODO — 後續工作

> 本文件記錄 AgentMemorySystem 未來可能改進的方向，按優先級排序。

## 已完成（v2.2.0，2026-08-13）

- [x] **SQLite 本機化（方案 A）**：shared.db 移出 OneDrive → `%LOCALAPPDATA%\AgentMemorySystem\shared.db` 本機查詢緩存；跨機事實源為 memory_shared.md；緩存缺失自動從 .md 重建；舊 OneDrive shared.db 自動遷移並標記 .migrated
- [x] **增量同步**：memory_shared.md 增量追加（僅追加新條目 id），文件缺失/格式損壞/超限降級全量重建；實測連續同步第 2、3 次零寫入
- [x] **條目解析加固**：頭部定位法解析 id，正文含 --- 不再錯位
- [x] **體積控制打包失效根治**：tools 變正式包 + 靜態導入 + build.py `--paths`/`--hidden-import` + 冒煙檢查 + `_shrink_md_fallback` 內置兜底
- [x] **FileLock UnboundLocalError 修復**
- [x] **回滾功能重寫**（backup_log.json 驅動，原引用不存在的 self.report 恒失效）
- [x] **備份名加 agent_id 前綴**（跨 Agent 同名文件不再互相覆蓋）
- [x] **跨機靜默冒名禁止**（無匹配即報錯，自動註冊當前機器；`load_identity`/`extract_local_to_fused`/`_resolve_device_name` 全路徑）
- [x] **刪除根目錄 stale device_config.json**（load_identity/SessionFlusher/memory_cli 統一指向數據根）
- [x] **DB 過期清理時區/格式偏差修復**（substr 日期前綴比較）
- [x] **VACUUM 低頻化**（僅實際刪除時執行）
- [x] **OneDrive 衝突檢測多語言**（中文/繁體/法語/德語）
- [x] **_inject_brief_pointer 相對路徑注入**
- [x] **_safe_read_text 死代碼修復**（MemoryError/OSError 分流）
- [x] **.sync_state.json 文件鎖 + 磁盤合併**
- [x] **心跳/托盤日誌 1MB 輪轉**
- [x] **新增 22 個回歸測試**（全量 174 斷言全綠）

## 已完成（v2.1.1，2026-08-06）

- [x] 数据根注册点（Single Source of Truth）：%LOCALAPPDATA%\AgentMemorySystem\data_root.txt 唯一事实来源
- [x] BAT 环境变量为最高权威，每次启动自动纠正注册点
- [x] watchdog 重启注入同一数据根
- [x] 直接双击任意 EXE 副本均收敛到项目根（实测验证）
- [x] 移除 LOCALAPPDATA 历史遗留入口（App.legacy_20260806）

## 已完成（v2.1.0，2026-08-06）

- [x] 数据根目录统一（data/ → AgentMemory/，含迁移工具 tools/unify_data_root.py）
- [x] FTS 索引孤儿清理（tools/repair_fts.py，回收 83.79MB，删除操作统一走 delete_memory()）
- [x] 写回策略修复（reconcile 与状态对齐，写回恢复实际生效）
- [x] 知识简报层（knowledge_brief.md + Agent 入口注入）
- [x] 稳定性加固（心跳写本地、崩溃日志双写、重启计数持久化）
- [x] 旧数据清理（~/.agent_memory/ 6.8GB 移出使用路径）
- [x] UI 屏幕自适应 + DPI 感知 + 窗口尺寸自动保存 + “打开数据目录”按钮
- [x] 日志轮转调优（统一到数据根 .logs/）

## 高優先級

### 1. 預編譯 EXE 發布
- 目前 Releases 頁面尚未發布預編譯 EXE，用戶需自行 `python build.py` 打包
- 考慮通過 GitHub Actions 自動化構建流程，在 tag 推送時自動生成 EXE 並發布
- 需處理代碼簽署問題（SmartScreen 警告），或提供清晰的下載+執行指引

### 2. macOS / Linux GUI 支援
- 當前 GUI 基於 tkinter + Windows 原生托盤 API，macOS/Linux 托盤不可用
- 評估引入跨平台托盤庫（如 `pystray`）或為各平台實作獨立托盤後端
- macOS 需處理 tcl/tk 依賴與 .app 打包格式

### 3. 體積保護算法優化
- 當前 `_enforce_write_volume_limit` 按 front matter 邊界截斷舊內容，可能丟失重要歷史條目
- 考慮基於 priority/confidence 的智能保留策略（低優先級先截斷）
- 加入壓縮歸檔機制：超限內容壓縮後歸檔至 cold tier，而非直接刪除

## 中優先級

### 4. 更多 Agent 支援
- 持續擴展 Agent 偵測範圍（關注新興 AI 編程工具）
- 考慮外掛式架構，允許社區貢獻 Agent 適配器而無需修改核心代碼
- 支援 AGENTS.md 標準的更多變體

### 5. 同步衝突解決策略
- 當前 `conflict_strategy: newer_wins` 較為簡單
- 考慮加入 `merge` 策略：自動合併非衝突部分，僅衝突部分提示用戶
- 多機同時寫入時的即時衝突檢測與通知

### 6. 記憶搜索與檢索增強
- 當前僅支援關鍵字搜索
- 考慮加入語義搜索（基於本地 embedding 模型）
- 支援時間範圍、Agent、標籤等多維度篩選

## 低優先級

### 7. 效能優化
- 超大記憶庫（10萬+ 條目）的查詢效能基準測試
- SQLite 索引優化與分頁加載
- 增量同步的進一步優化（僅同步變更部分）

### 8. UI/UX 改進
- 暗色模式支援
- 記憶條目的可視化瀏覽器（樹狀/圖譜視圖）
- 同步歷史時間軸

### 9. 文檔與測試
- 補充各 Agent 寫回適配器的單元測試覆蓋率
- 編寫貢獻者指南（Agent 適配器開發規範）
- 錄製演示影片

---

*最後更新：2026-07-31（v2.0.4）*
