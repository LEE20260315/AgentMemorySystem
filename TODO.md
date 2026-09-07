# TODO — 後續工作（唯一權威計劃）

> 2026-09-04 全量審計後重寫。每項待辦均給出**現狀 / 做法 / 驗收標準**，完成一項勾一項。
> 歷史計劃（基於 v2.0.4）存檔於 [docs/archive/2026-08-implementation-plan.md](docs/archive/2026-08-implementation-plan.md)；
> 歷史實證審計存檔於 [docs/archive/2026-08-31-sync-audit.md](docs/archive/2026-08-31-sync-audit.md)；
> 各版本完成明細見 [CHANGELOG.md](CHANGELOG.md)。

## 現狀基線（2026-09-07 更新）

- 版本 v2.5.2，遠端 main 與本地同步；測試套件 333 條，**實測 388/388 全綠**
  （2026-09-07 新增語義去重回歸測試）
- CI 門禁已修復：自建立以來在 ubuntu 上全紅（5 條平台相關用例必然失敗，門禁形同虛設）；
  現改為 windows-latest 門禁（Python 3.10/3.11/3.12）+ ubuntu 觀察項（不阻斷）
- 首個 Release 已上線：`AgentMemorySync v2.4.2` 附預編譯 zip（2026-09-07，見 P0 #2）；
  發佈鏈路（release.yml）已驗證，後續推 tag 即自動出包
- 2026-09-04 已完成倉庫治理：歷史文檔歸檔至 `docs/archive/`、一次性探針腳本移出倉庫視野、
  補交 `tools/__init__.py`、修正 `pyproject.toml` 入口、CI 依賴對齊 `requirements.txt`

---

## P0 —— 近期落地（按性價比排序）

### 1. ✅ `memory_shared.md` 截斷透明化（v2.4.2，2026-09-07 完成）
- **已完成**：`_write_shared_md` 全量重建截斷（實測主要丟數據點）統計丟棄條數、
  日誌升 WARN、寫入 `SyncReport.volume_truncations`；`_enforce_write_volume_limit`
  以 sync marker 口徑統計並經 `_record_truncation()` 寫入 `WriteBackResult`；
  `_enforce_volume_control` 收縮同樣入報告；報告摘要可見
  「⚠ 體積保護截斷: 共丟棄 K 條」。回歸測試
  `test_truncation_reports_dropped_count` + `test_volume_truncation_writer_reports_dropped`

### 2. ✅ 發佈首個 Release（v2.4.2，2026-09-07 完成）
- **已完成**：`.github/workflows/release.yml` 落地（tag 推送 → windows-latest
  測試門禁 → `python build.py` → zip → `gh release create`，permissions:
  contents: write）；`v2.4.2` tag 推送後全鏈路驗證通過：Actions 綠、
  CI 門禁綠、Release「AgentMemorySync v2.4.2」上線並附
  `AgentMemorySync-v2.4.2-windows-x64.zip`；README（中/英）下載指引已啟用。
  代碼簽名（付費證書）按計劃暫緩，SmartScreen 指引見 README FAQ

---

## P1 —— 核心能力補強

### 4. ✅ merge 衝突策略真實實現（v2.5.0，2026-09-07 完成）
- **已完成**：`MemoryMerger` 新增 `conflict_strategy`（`newer_wins` 默认不变 /
  `merge`），同 id 异内容（多机同写）即触发；`_merge_memories` 标签并集 /
  内容取更详细 / 置信度取高 / 时间戳取新；`on_merge` 通知钩子接入同步日志；
  `SyncReport.merged_entries` + 摘要列出被合并条目；merge 档下归一化同内容
  不触发合并（稳态保护）。回归测试 `test_conflict_merge` /
  `test_conflict_newer_wins`，全量 367/367 全绿

### 5. 語義去重實裝（v2.5.1 管線已通，真模型灰度實測待做）
- **已完成（2026-09-07）**：灰度開關 `sync.semantic_dedup`（默認 False 行為
  不變）；開啟時融合入口構造 `EmbeddingService()`（lazy），記憶無向量時
  現場生成並隨條目落庫；缺 sentence-transformers / numpy 雙重降級文本三檔
  （WARN 一次、本輪不再重試）；向量搜索分支補異常防護；CI 補 numpy。
  測試 3 條（stub 向量命中 / 模型不可用降級 / 默認關閉），全量 379/379 全綠
- **剩餘**：裝 sentence-transformers（~500MB）後真模型灰度實測同步耗時
  與去重率，據此決定是否默認開啟

### 6. ✅ 體積保護智能保留 + cold tier 歸檔（v2.5.2，2026-09-07 完成）
- **已完成**：`_write_shared_md` 全量重建按（置信度降序、時間新者優先）排序，
  低置信度舊條目先截（`test_volume_limit_priority_keep` 驗證 10 high + 10 low
  頂格場景 md 中 low 為 0）；被截斷條目歸檔至 `memory_shared_cold.md`
  （512KB 檔位，md/cold 無重複、合計覆蓋全庫，`test_volume_archive_to_cold`
  驗證）；報告註明歸檔去向。全量 388/388 全綠。
  文本級 `_enforce_write_volume_limit` 維持 truncate_oldest（三形態文本
  重排序風險高收益低，主截斷點已在對像級實現，見 CHANGELOG 範圍裁剪註記）

### 7. 插件式 Agent 適配架構（原 T4）
- **現狀**：新增 Agent 需改 `config.json` + `sync_writers.WRITER_REGISTRY` + 測試三處核心代碼
- **做法**：定義 `DetectorPlugin` / `WriterPlugin` 抽象基類與 `register_plugin()` 註冊表，
  現有適配器遷移為內置插件；CONTRIBUTING 增補「如何編寫 Agent 適配器」章節
- **驗收**：示例插件加載測試通過；既有適配器行為不變（全量回歸綠）
- **工作量**：L

---

## P2 —— 平台與體驗

### 8. MemoryDatabase tags 存取斷鏈修復（2026-09-07 發現）
- `_row_to_entry` 的 tags 恒為 `[]`（註釋「單獨獲取」但 `get_memory` /
  `list_memories` 均未查 `memory_tags` 表），入庫標籤讀不回來
- 修復涉及 schema 與全部調用方；當前 merge 測試以內存級斷言繞開

### 9. macOS / Linux GUI 支援（原 T2，長週期分支）
- 托盤後端抽象（`WindowsTrayBackend` / `PystrayBackend` 按平台選擇）；macOS `.app` 打包、
  Linux 托盤驗證。CLI 已天然跨平台，此項只關乎 GUI/托盤
- **驗收**：Windows 行為不變；macOS/Linux 至少托盘可啟動

### 10. 檢索增強（原 T6 的搜索側）
- `search_memory` 支援 `mode="semantic"|"keyword"|"hybrid"` 與時間範圍 / Agent / 標籤多維篩選
- **驗收**：vector extras 未裝時關鍵詞搜索不受影響；裝後語義搜索有測試

### 11. 效能基準（原 T7）
- 新增 `tools/benchmark.py`（10 萬+ 條寫入 / 查詢 / 融合基準）；據結果做 SQLite 索引與分頁優化
- **驗收**：基準數據記錄在案，優化前後對比可複現

### 12. UI/UX（原 T8）
- 暗色模式（基於現有 `COLORS` token 擴展明暗兩套）、同步歷史時間軸、記憶可視化瀏覽器
- **驗收**：GUI 冒煙測試 + 手動驗證清單

---

## 已完成版本索引（明細見 CHANGELOG.md）

| 版本 | 日期 | 一句話摘要 |
|------|------|-----------|
| v2.5.2 | 2026-09-07 | 體積保護智能保留 + cold tier 歸檔：置信度優先裝填 + memory_shared_cold.md（TODO P1-6） |
| v2.5.1 | 2026-09-07 | 語義去重實裝：向量現場生成落庫 + 灰度開關 + 雙重降級保證（TODO P1-5，真模型實測待做） |
| v2.5.0 | 2026-09-07 | merge 衝突策略真實實現：conflict_strategy 接通 + 自動合併 + 通知鉤子 + 報告可見（TODO P1-4） |
| v2.4.2 | 2026-09-07 | 體積截斷透明化：memory_shared.md 截斷丟棄條數統計 + WARN + 同步報告可見（TODO P0-1） |
| v2.4.1 | 2026-09-03 | dry-run 只讀閉環、merge 死代碼清理、`embedding_service` 形參接通、體積檔位 `policy_key`、托盤註冊自愈 |
| v2.4.0 | 2026-08-31 | 同步報告保真：根治「55 條新增」虛報與 replace churn、access_count 副作用移除、Agent 父子目錄重複登記修復 |
| v2.3.0 | 2026-08-30 | 墓碑機制（防已刪記憶跨設備復活）、日誌保留雙維裁剪、Windows 命名互斥量跨進程鎖 |
| v2.2.x | 2026-08 | SQLite 本機化 + 增量同步、OneDrive 衝突根治（原子寫）、數據根註冊點、托盤 GUID、運行目錄固定 |
| v2.1.x | 2026-08 | 數據根統一 `AgentMemory/`、FTS 孤兒清理（回收 83.79MB）、知識簡報層、UI 自適應 |
| v2.0.x | 2026-07 | 多機支援、front matter 格式、體積治理、回聲污染根治 |
| v1.x | 2026-05~07 | 核心庫、同步引擎、GUI + 托盤、跨裝置啟動器 |

---

*最後更新：2026-09-07（v2.5.2：P0 全清 + P1-4/5/6 完成）*
