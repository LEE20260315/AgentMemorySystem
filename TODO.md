# TODO — 後續工作

> 本文件記錄 AgentMemorySystem 未來可能改進的方向，按優先級排序。

## 已完成（v2.4.0，2026-08-31）

- [x] **融合報告保真**：統計只累加第一階段 `inserted`（第二階段回流不再計入「新增共享」）；報告改為「新增 / 更新」分列，無新增時明確提示
- [x] **replace churn 根治**：`_resolve_conflict` 前置判定「歸一化內容相同即無變化」（僅置信度更高才改寫）；實測副本回放三輪 synced 由 55 → 0
- [x] **融合去重兜底**：新增 `_normalize_memory_content`（只消除 CRLF/行尾空白/連續空行）與內容索引，解決同一記憶兩側 id 不同導致漏判
- [x] **`get_memory()` 寫副作用移除**：新增 `track_access` 參數，融合比對不再累加 `access_count`（該值曾污染到 755 且每輪 +6，現凍結）
- [x] **Agent 重複登記修復**：新增 `_is_path_related()`，父子目錄不再被當成兩個 Agent（`.trae-cn` vs `.trae-cn\memory`）
- [x] **`--dry-run` 真正只讀**：提取、融合、墓碑清理全部納入 dry_run 保護（舊版照常寫庫）

## 已完成（v2.3.0，2026-08-30）

- [x] **墓碑機制（P1-3）**：新增 `tombstones.py`，墓碑庫存數據根 `.tombstones.json`（OneDrive 同步跨設備生效）；reconcile 正常模式 vanish 記墓碑（保守模式/24h 寬限期/批量 vanish>50 三重防誤殺）；寫回、memory_shared.md 重建、DB 融合三處復活路徑全過濾；融合後寫回前 purge shared.db 命中行（FTS 同步清理，分塊 500 免疫 SQLite 變量上限）；失敗不阻斷主流程且如實返回 0；`refresh()` 每輪重讀盤保 GUI 常駐進程跨設備可見
- [x] **日誌保留策略（P1-2）**：輪轉檔名帶時間戳永不覆蓋 +（數量 3，天數 7）雙維裁剪（活躍檔案絕不動）；寫失敗計數、成功後補記 WARN；`tools/log_retention.py` 默認預覽、`--apply` 走回收站
- [x] **跨進程鎖（P1-1）**：`safe_io.CrossProcessLock` Windows 命名互斥量（真互斥：原子性≠隔離性）+ 遺留 `.lock` 清理
- [x] **托盤 GUID 接線 + 運行目錄固定（v2.2.3）**：`_NIF_GUID` 綁定顯示偏好、EXE 運行目錄固定 `%LOCALAPPDATA%\AgentMemorySystem\Run`、心跳退出 OneDrive
- [x] **對抗性審查兩輪**：緩存刷新 / 批量 vanish 保護 / 失敗日誌 / purge_db 分塊 / add 如實返回；測試 305 條（303 斷言全綠，2 條歷史 detect_agents 緩存用例與本版無關）

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
- **（v2.4.0 實測）`memory_shared.md` 靜默截斷，優先處理**：`_shared/volume_policy.json`
  限制 128KB / `truncate_oldest`，實測各檔已頂格（122~132KB），**只裝得下最新 51~55 條**，
  而庫中有 127~134 條 —— 舊記憶雖在 shared.db，卻永遠進不了 Agent 實際讀取的 md 檔，
  且日誌只說「重建完成，51 條」，不提示丟了多少。最小改動：截斷時打 WARN + 報告丟棄條數
- **（v2.4.0 實測）`_resolve_conflict` 從不返回 `"merge"`**，該分支（`agent_memory.py`
  merge 分支）為死代碼，需確認當初設計意圖後決定補齊或移除
- **（v2.4.0 實測）`create_merger()` 未傳 `embedding_service`**，向量相似度去重檔位
  永不生效，去重仍只依賴「id 全等 / content 全等 / 歸一化全等」三檔，語義相近但
  措辭不同的記憶仍會重複入庫

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

*最後更新：2026-08-30（v2.3.0）*
