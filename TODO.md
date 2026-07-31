# TODO — 後續工作

> 本文件記錄 AgentMemorySystem 未來可能改進的方向，按優先級排序。

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
