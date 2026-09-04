# TODO — 後續工作（唯一權威計劃）

> 2026-09-04 全量審計後重寫。每項待辦均給出**現狀 / 做法 / 驗收標準**，完成一項勾一項。
> 歷史計劃（基於 v2.0.4）存檔於 [docs/archive/2026-08-implementation-plan.md](docs/archive/2026-08-implementation-plan.md)；
> 歷史實證審計存檔於 [docs/archive/2026-08-31-sync-audit.md](docs/archive/2026-08-31-sync-audit.md)；
> 各版本完成明細見 [CHANGELOG.md](CHANGELOG.md)。

## 現狀基線（2026-09-04 審計結論）

- 版本 v2.4.1，遠端 main 與本地同步；測試套件 324 條，**實測 324/324 全綠**
  （2026-09-04 修復時間炸彈測試後首次全綠，此前兩條長期「歷史失敗」實為硬編碼
  時間戳 + 24h TTL 所致）
- CI 門禁已修復：自建立以來在 ubuntu 上全紅（5 條平台相關用例必然失敗，門禁形同虛設）；
  現改為 windows-latest 門禁（Python 3.10/3.11/3.12）+ ubuntu 觀察項（不阻斷）
- GitHub Releases 頁面為空，無預編譯 EXE（見 P0 #2）
- 2026-09-04 已完成倉庫治理：歷史文檔歸檔至 `docs/archive/`、一次性探針腳本移出倉庫視野、
  補交 `tools/__init__.py`、修正 `pyproject.toml` 入口、CI 依賴對齊 `requirements.txt`

---

## P0 —— 近期落地（按性價比排序）

### 1. `memory_shared.md` 截斷透明化（已知靜默丟數據，優先於智能保留）
- **現狀**：`_shared/volume_policy.json` 限 128KB / `truncate_oldest`，實測各檔頂格
  （122~132KB）只裝得下最新 51~55 條，而庫中有 127~134 條；日誌僅 INFO 一句
  「重建完成，51 條」，**不提示丟了多少**，Agent 永遠讀不到被截斷的舊記憶
- **做法**：`sync_writers.py` 的 `_enforce_write_volume_limit` 截斷時統計丟棄條數，
  日誌升級 WARN，並把「庫中 M 條 / 保留 N 條 / 丟棄 K 條」寫入 SyncReport 摘要
- **驗收**：新增回歸測試 `test_truncation_reports_dropped_count`；同步報告可見丟棄數；
  用戶可據此決定調大 `volume_policy.json` 上限
- **工作量**：S

### 2. 發佈首個 Release（原 T1：tag → CI 自動打包 EXE）
- **現狀**：Releases 頁面為空，用戶只能源碼運行或自行 `python build.py`
- **做法**：
  1. 新增 `.github/workflows/release.yml`：推送 `v*` tag 時在 `windows-latest` 執行
     `python build.py`，將 `AgentMemorySync/` 打包為 zip 上傳至該 tag 的 Release
  2. 先以 `v2.4.1` 或下一版本號手動打 tag 驗證全鏈路
  3. README「下載 EXE」鏈接在 Release 就緒後啟用（`requirements.txt` 頭部註釋已預留文案）
- **驗收**：tag 推送後 Actions 綠、Release 出現分發包、下載後雙擊 `AgentMemorySync.bat` 可用
- **備註**：SmartScreen 提示屬未簽名常態，README FAQ 已有指引；代碼簽名（付費證書）暫緩
- **工作量**：M

---

## P1 —— 核心能力補強

### 4. merge 衝突策略真實實現（原 T5）
- **現狀**：`conflict_strategy` 僅支援 `newer_wins`；v2.4.1 已刪除從不可達的 `"merge"`
  死分支與 `_merge_memories()`，實現時需以真實可達的調用路徑補回
- **做法**：`MemoryMerger._resolve_conflict` 新增 `merge` 分支（非衝突字段自動合併：
  標籤並集、內容取更詳細版本、置信度取高者）；config 支援 `merge` 檔位並在報告中
  列出被合併的條目；多機同寫的衝突檢測通知鉤子
- **驗收**：新增 `test_conflict_merge` / `test_conflict_newer_wins`；全量測試綠
- **工作量**：M

### 5. 語義去重實裝評估（v2.4.1 已接通形參，默認仍關閉）
- **現狀**：`create_merger(embedding_service=...)` 已可傳參，但同步管線默認傳 `None`，
  去重僅依賴「id 全等 / content 全等 / 歸一化全等」三檔，措辭不同語義相近的記憶仍會重複入庫
- **做法**：在融合入口構造 `EmbeddingService()`（lazy 加載，缺 sentence-transformers 時
  降級現行為），灰度啟用並實測同步耗時與去重率變化
- **驗收**：裝有 vector extras 時語義去重生效且有測試；未安裝時行為與現狀完全一致
- **工作量**：M

### 6. 體積保護智能保留 + cold tier 歸檔（原 T3，依賴 #2 先行）
- **現狀**：截斷按 front matter 邊界從舊內容開始砍，不看 priority/confidence，重要歷史可能先丟
- **做法**：`_enforce_write_volume_limit` 引入 confidence/priority 排序（低者先截）；
  超限內容經 `SmartCompressor` 壓縮歸檔至 cold tier 而非直接刪除；擴展 `volume_policy.json` 欄位
- **驗收**：新增 `test_volume_limit_priority_keep` / `test_volume_archive_to_cold`
- **工作量**：M

### 7. 插件式 Agent 適配架構（原 T4）
- **現狀**：新增 Agent 需改 `config.json` + `sync_writers.WRITER_REGISTRY` + 測試三處核心代碼
- **做法**：定義 `DetectorPlugin` / `WriterPlugin` 抽象基類與 `register_plugin()` 註冊表，
  現有適配器遷移為內置插件；CONTRIBUTING 增補「如何編寫 Agent 適配器」章節
- **驗收**：示例插件加載測試通過；既有適配器行為不變（全量回歸綠）
- **工作量**：L

---

## P2 —— 平台與體驗

### 8. macOS / Linux GUI 支援（原 T2，長週期分支）
- 托盤後端抽象（`WindowsTrayBackend` / `PystrayBackend` 按平台選擇）；macOS `.app` 打包、
  Linux 托盤驗證。CLI 已天然跨平台，此項只關乎 GUI/托盤
- **驗收**：Windows 行為不變；macOS/Linux 至少托盘可啟動

### 9. 檢索增強（原 T6 的搜索側）
- `search_memory` 支援 `mode="semantic"|"keyword"|"hybrid"` 與時間範圍 / Agent / 標籤多維篩選
- **驗收**：vector extras 未裝時關鍵詞搜索不受影響；裝後語義搜索有測試

### 10. 效能基準（原 T7）
- 新增 `tools/benchmark.py`（10 萬+ 條寫入 / 查詢 / 融合基準）；據結果做 SQLite 索引與分頁優化
- **驗收**：基準數據記錄在案，優化前後對比可複現

### 11. UI/UX（原 T8）
- 暗色模式（基於現有 `COLORS` token 擴展明暗兩套）、同步歷史時間軸、記憶可視化瀏覽器
- **驗收**：GUI 冒煙測試 + 手動驗證清單

---

## 已完成版本索引（明細見 CHANGELOG.md）

| 版本 | 日期 | 一句話摘要 |
|------|------|-----------|
| v2.4.1 | 2026-09-03 | dry-run 只讀閉環、merge 死代碼清理、`embedding_service` 形參接通、體積檔位 `policy_key`、托盤註冊自愈 |
| v2.4.0 | 2026-08-31 | 同步報告保真：根治「55 條新增」虛報與 replace churn、access_count 副作用移除、Agent 父子目錄重複登記修復 |
| v2.3.0 | 2026-08-30 | 墓碑機制（防已刪記憶跨設備復活）、日誌保留雙維裁剪、Windows 命名互斥量跨進程鎖 |
| v2.2.x | 2026-08 | SQLite 本機化 + 增量同步、OneDrive 衝突根治（原子寫）、數據根註冊點、托盤 GUID、運行目錄固定 |
| v2.1.x | 2026-08 | 數據根統一 `AgentMemory/`、FTS 孤兒清理（回收 83.79MB）、知識簡報層、UI 自適應 |
| v2.0.x | 2026-07 | 多機支援、front matter 格式、體積治理、回聲污染根治 |
| v1.x | 2026-05~07 | 核心庫、同步引擎、GUI + 托盤、跨裝置啟動器 |

---

*最後更新：2026-09-04（全量審計重寫）*
