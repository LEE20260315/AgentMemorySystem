<div align="center">
<img src="docs/assets/seal-xicheng.png" alt="西城閒人" width="120"/>
</div>

# AgentMemorySystem

<div align="center">

**多 AI Agent 記憶融合 · 跨裝置同步系統**

[English](README_en.md) | **中文**

![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)
![Python](https://img.shields.io/badge/python-3.10+-yellow?style=flat-square)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey?style=flat-square)

</div>

---

## 緣起

Claude、Hermes、Trae、Cursor、CodePilot 諸 Agent，各存其憶，格式互異，路徑散落，若孤島不相聞。

**AgentMemorySystem** 以一統之策，行「發現 → 提取 → 融合 → 寫回」四步，貫通各 Agent 記憶，使孤島成大陸。

**設計之道：**
- **本地為先** — 數據盡存本機，不上傳雲端
- **跨裝置同步** — 藉 OneDrive 或任意同步目錄，多機共享
- **即裝即用** — 單檔發佈，免 Python 環境
- **安全可靠** — 自動備份、衝突檢測、敏感詞過濾、一鍵回溯

## 特性

| 特性 | 說明 |
|------|------|
| **自動發現 Agent** | 候選路徑 + 特徵校驗，不依賴硬編碼路徑 |
| **原生格式寫回** | Claude 子檔案、Trae 章節、Hermes § 分隔、通用 Markdown |
| **融合去重** | 基於內容哈希之 SQLite 融合索引 |
| **分層儲存** | 熱 / 溫 / 冷三級資料，自動歸檔 |
| **安全機制** | 自動備份、檔案鎖、OneDrive 衝突檢測、敏感詞過濾 |
| **GUI + CLI** | 系統匣常駐程式 + 命令列工具 |

## 支援之 Agent

| Agent | 記憶格式 | 寫回方式 |
|-------|---------|---------|
| Claude | 子檔案 + `MEMORY.md` 索引 | 追加至 `shared/` 子檔案 |
| Hermes | `MEMORY.md` 尾部 `§` 分隔 | 追加 § 段落 |
| Trae | `user_profile.md` 章節 | 追加 `## Shared Knowledge` |
| CodePilot | SQLite (`codepilot.db`) | 匯出為 Markdown |
| Cursor / Windsurf / Cline / Continue / Aider / Roo-Code / Codex | 通用 Markdown | 自動適配 |

## 速覽

### 其一：發佈版（推薦）

自 [Releases](https://github.com/LEE20260315/AgentMemorySystem/releases) 下載 `AgentMemorySync.exe`，雙擊即行。

無需安裝 Python，諸依賴皆內置（約 18MB）。

### 其二：源碼執行

**所需環境：**
- Python 3.10+
- Windows 10+ / Linux（GUI 需圖形環境）

```bash
git clone https://github.com/LEE20260315/AgentMemorySystem.git
cd AgentMemorySystem
pip install -r requirements.txt
python memory_sync_app.py          # 啟動 GUI
python memory_sync_app.py --cli    # 啟動 CLI
```

### 常用之命

```bash
python memory_cli.py full-sync                     # 完整同步（發現 → 提取 → 融合 → 寫回）
python memory_cli.py redetect                      # 重新檢測 Agent
python memory_cli.py --agent claude write "記住此設計決策" --tags 開發
python memory_cli.py --agent claude search "關鍵字"
python memory_cli.py --agent claude health         # 健康檢查
python memory_cli.py --agent claude expire         # 清理過期記憶並歸檔
```

## 架構

```
本機 Agent 記憶檔案（Claude / Hermes / Trae / ...）
    │
    ▼
┌─────────────────────────────────────┐
│  sync_engine.py — 同步編排層        │
│  發現 → 提取 → 融合 → 寫回          │
└─────────────┬───────────────────────┘
              │
    ┌─────────┴─────────┐
    ▼                   ▼
┌──────────┐    ┌──────────────┐
│ SQLite   │    │ sync_writers │
│ 融合索引 │    │ 寫回適配器   │
└──────────┘    └──────────────┘
    │                   │
    ▼                   ▼
 內容哈希去重      按原生格式寫回
```

**分層之序：**
- **核心層** (`agent_memory.py`) — SQLite 儲存、並發控制、備份、壓縮、健康檢查
- **適配層** (`sync_writers.py`) — 各 Agent 寫回適配器
- **編排層** (`sync_engine.py`) — 發現 → 提取 → 融合 → 寫回
- **交互層** (`memory_sync_app.py`) — GUI + 系統匣 + CLI

## 配置

`config.json` 統轄諸參數：

```jsonc
{
  "paths": {
    "memory_root": "auto",      // 記憶根目錄，auto = 自動檢測
    "shared_root": "auto"       // 共享目錄
  },
  "limits": {
    "max_memories_per_agent": 10000,
    "max_memory_age_days": 365  // 記憶過期天數
  },
  "security": {
    "sensitive_patterns": ["password", "token", ...],
    "block_sensitive": false    // 是否攔截含敏感詞之寫入
  },
  "sync": {
    "conflict_strategy": "newer_wins",
    "lock_timeout_seconds": 30
  }
}
```

詳見倉庫中 `config.json` 檔案。

## 版次

| 版次 | 日期 | 要目 |
|------|------|------|
| **v1.3** | 2026-06 | GUI + 系統匣、EXE 封裝、自動同步排程、通用 Agent 發現、CodePilot 支援、鎖檔案過期修復 |
| **v1.2** | 2026-05 | 同步引擎、寫回適配器、SQLite 融合索引、OneDrive 衝突檢測 |
| **v1.1** | 2026-05 | 配置管理系統、日誌系統、敏感資訊檢測、健康檢查、記憶過期機制 |
| **v1.0** | 2026-05 | 核心庫、檔案鎖、裝置配置、Markdown 解析 |

詳見 [CHANGELOG.md](CHANGELOG.md)。

## 目錄

```
AgentMemorySystem/
├── agent_memory.py           # 核心引擎（SQLite、並發、備份、壓縮）
├── sync_engine.py            # 同步編排（發現 → 提取 → 融合 → 寫回）
├── sync_writers.py           # Agent 寫回適配器
├── memory_sync_app.py        # GUI + 系統匣 + CLI
├── memory_cli.py             # CLI 入口
├── build.py                  # 封裝腳本（python build.py → EXE）
├── config.json               # 配置檔
├── requirements.txt          # Python 依賴
├── pyproject.toml            # 包元資訊
├── assets/                   # 圖示資源
├── docs/                     # 文檔
├── CHANGELOG.md              # 變更日誌
├── DEVLOG.md                 # 開發日誌
├── LICENSE                   # MIT 許可證
└── test_memory.py            # 測試用例
```

## 常見之問

**Q: 必需 OneDrive 否？**
A: 非必需。預設資料存於專案內 `data/` 目錄，可於 `config.json` 指定任意路徑。OneDrive 僅供跨裝置同步。

**Q: 支援 macOS 否？**
A: CLI 直用無礙。GUI 基於 tkinter，macOS 安裝 Python 時需勾選 tcl/tk。

**Q: 記憶檔案被鎖定如何處置？**
A: 鎖檔案設有 60 秒自動過期機制。殘留鎖檔案會自動清理。

**Q: 如何回溯同步？**
A: 每次同步前自動備份原檔至 `.sync_backups/`，可藉 GUI 或 CLI 回溯。

**Q: 私隱安全如何保障？**
A: 寫入時自動檢測敏感資訊（密碼、密鑰、token 等），可配置攔截或僅警告。所有數據存於本機，不外傳。

## 共築

歡迎 Issue 與 Pull Request！

1. Fork 此倉庫
2. 建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交變更 (`git commit -m 'Add amazing feature'`)
4. 推送分支 (`git push origin feature/amazing-feature`)
5. 發 Pull Request

## 許可

[MIT License](LICENSE) © 2026 LEE20260315

---

<div align="center">

<img src="docs/assets/seal-xicheng.png" alt="西城閒人" width="64"/>

<sub>紙承墨，墨載意，意馭器</sub>

<sub>西城閒人 · 識</sub>

</div>