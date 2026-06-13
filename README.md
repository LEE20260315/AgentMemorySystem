<div align="center">

<img src="docs/assets/banner-xicheng.jpg" alt="AgentMemorySystem" width="680"/>

# AgentMemorySystem

**多 AI Agent 记忆融合 · 跨设备同步系统**

[English](README_en.md) | **中文**

![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)
![Python](https://img.shields.io/badge/python-3.10+-yellow?style=flat-square)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey?style=flat-square)
![Stars](https://img.shields.io/github/stars/LEE20260315/AgentMemorySystem?style=flat-square&color=orange)

</div>

---

## 项目简介

**AgentMemorySystem** 是一个本地优先的多 AI Agent 记忆同步系统。它解决了多个 AI 客户端（Claude、Hermes、Trae、Cursor、CodePilot 等）之间**记忆格式不同、路径分散、无法互通**的问题。

**核心流程：** 发现 Agent → 提取记忆 → 融合去重 → 按原生格式写回

**设计理念：**
- **本地优先** — 所有数据存在本地，不上传云端
- **跨设备同步** — 通过 OneDrive / 任意同步文件夹实现多设备记忆共享
- **零配置上手** — 双击 EXE 即可运行，无需 Python 环境
- **安全可靠** — 自动备份、冲突检测、敏感信息过滤、可一键回滚

## 功能特性

| 特性 | 说明 |
|------|------|
| **多 Agent 自动发现** | 候选路径 + 特征校验，不依赖硬编码路径 |
| **原生格式写回** | Claude 子文件、Trae section、Hermes § 分隔、通用 Markdown |
| **融合去重** | 基于内容哈希的 SQLite 融合索引 |
| **分层存储** | 热 / 温 / 冷数据分级，自动归档 |
| **安全机制** | 自动备份、文件锁、OneDrive 冲突检测、敏感词过滤 |
| **GUI + CLI** | 系统托盘常驻 + 命令行工具 |
| **开箱即用** | 单文件 EXE，内置所有依赖 |

## 支持的 Agent

| Agent | 记忆格式 | 写回方式 |
|-------|---------|---------|
| Claude | 子文件 + `MEMORY.md` 索引 | 追加到 `shared/` 子文件 |
| Hermes | `MEMORY.md` 尾部 `§` 分隔 | 追加 § 段落 |
| Trae | `user_profile.md` section | 追加 `## Shared Knowledge` |
| CodePilot | SQLite (`codepilot.db`) | 导出为 Markdown |
| Cursor / Windsurf / Cline / Continue / Aider / Roo-Code / Codex | 通用 Markdown | 自动适配 |

## 快速开始

### 方式一：EXE 打包版（推荐）

从 [Releases](https://github.com/LEE20260315/AgentMemorySystem/releases) 下载 `AgentMemorySync.exe`，双击即用。

无需安装 Python，所有依赖已内置（约 18MB）。

### 方式二：源码运行

**环境要求：**
- Python 3.10+
- Windows 10+ / Linux（GUI 需要图形环境）

```bash
# 克隆仓库
git clone https://github.com/LEE20260315/AgentMemorySystem.git
cd AgentMemorySystem

# 安装依赖
pip install -r requirements.txt

# 启动 GUI
python memory_sync_app.py

# 或启动 CLI
python memory_sync_app.py --cli
```

### 常用命令

```bash
# 完整同步（发现 → 提取 → 融合 → 写回）
python memory_cli.py full-sync

# 重新检测 Agent
python memory_cli.py redetect

# 写入记忆
python memory_cli.py --agent claude write "记住这个设计决策" --tags 开发

# 搜索记忆
python memory_cli.py --agent claude search "关键字"

# 健康检查
python memory_cli.py --agent claude health

# 清理过期记忆并归档
python memory_cli.py --agent claude expire
```

## 架构设计

```
本地 Agent 记忆文件（Claude / Hermes / Trae / ...）
    │
    ▼
┌─────────────────────────────────────┐
│  sync_engine.py — 同步编排层        │
│  detect → extract → merge → write   │
└─────────────┬───────────────────────┘
              │
    ┌─────────┴─────────┐
    ▼                   ▼
┌──────────┐    ┌──────────────┐
│ SQLite   │    │ sync_writers │
│ 融合索引 │    │ 写回适配器   │
└──────────┘    └──────────────┘
    │                   │
    ▼                   ▼
 内容哈希去重      按原生格式写回
```

**分层架构：**
- **核心层** (`agent_memory.py`) — SQLite 存储、并发控制、备份、压缩、健康检查
- **适配层** (`sync_writers.py`) — 各 Agent 写回适配器
- **编排层** (`sync_engine.py`) — 发现 → 提取 → 融合 → 写回
- **交互层** (`memory_sync_app.py`) — GUI + 系统托盘 + CLI

## 配置说明

配置文件 `config.json` 支持以下主要选项：

```jsonc
{
  "paths": {
    "memory_root": "auto",      // 记忆根目录，auto = 自动检测
    "shared_root": "auto"       // 共享目录
  },
  "limits": {
    "max_memories_per_agent": 10000,
    "max_memory_age_days": 365  // 记忆过期天数
  },
  "security": {
    "sensitive_patterns": ["password", "token", ...],
    "block_sensitive": false    // 是否阻止含敏感信息的写入
  },
  "sync": {
    "conflict_strategy": "newer_wins",
    "lock_timeout_seconds": 30
  }
}
```

完整配置项参见仓库中的 `config.json` 文件。

## 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| **v1.3** | 2026-06 | GUI + 系统托盘、EXE 打包、自动同步调度、通用 Agent 发现、CodePilot 支持、锁文件过期修复 |
| **v1.2** | 2026-05 | 同步引擎、写回适配器、SQLite 融合索引、OneDrive 冲突检测 |
| **v1.1** | 2026-05 | 配置管理系统、日志系统、敏感信息检测、健康检查、记忆过期机制 |
| **v1.0** | 2026-05 | 核心库、文件锁、设备配置、Markdown 解析 |

详细变更日志参见 [CHANGELOG.md](CHANGELOG.md)。

## 文件结构

```
AgentMemorySystem/
├── agent_memory.py           # 核心引擎（SQLite、并发、备份、压缩）
├── sync_engine.py            # 同步编排（发现 → 提取 → 融合 → 写回）
├── sync_writers.py           # Agent 写回适配器
├── memory_sync_app.py        # GUI + 系统托盘 + CLI
├── memory_cli.py             # CLI 入口
├── build.py                  # 打包脚本（python build.py → EXE）
├── config.json               # 配置文件
├── requirements.txt          # Python 依赖
├── pyproject.toml            # 包元信息
├── assets/                   # 图标资源
├── docs/                     # 文档
├── CHANGELOG.md              # 变更日志
├── DEVLOG.md                 # 开发日志
├── LICENSE                   # MIT 许可证
└── test_memory.py            # 测试用例
```

## 常见问题

**Q: 需要 OneDrive 吗？**
A: 不是必须。默认数据存在项目内 `data/` 目录，可通过 `config.json` 指定任意路径。OneDrive 仅用于跨设备同步。

**Q: 支持 macOS 吗？**
A: CLI 可直接使用。GUI 基于 tkinter，macOS 需安装 Python 时勾选 tcl/tk。

**Q: 记忆文件被锁定怎么办？**
A: 锁文件有 60 秒自动过期机制。如遇残留锁文件，程序会自动清理。

**Q: 如何回滚同步？**
A: 每次同步前自动备份原文件到 `.sync_backups/`，可通过 GUI 或 CLI 回滚。

**Q: 隐私安全如何保障？**
A: 写入时自动检测敏感信息（密码、密钥、token 等），可配置阻止写入或仅警告。所有数据本地存储，不上传。

## 参与贡献

欢迎 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

## 许可证

[MIT License](LICENSE) © 2026 LEE20260315

---

<div align="center">
<sub>紙承墨，墨載意，意馭器</sub><br>
<sub>西城閒人 · 識</sub>
</div>
