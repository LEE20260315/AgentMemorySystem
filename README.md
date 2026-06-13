<div align="center"><br><br>
<img src="docs/assets/banner-xicheng.jpg" alt="Agent Memory System" width="720"/>
<br><br>

# AgentMemorySystem
<sub>Agent Memory Synchronization · Multi-Agent Shared Intelligence</sub>
<br>

<sub>— 多 AI Agent 记忆联通系统 —</sub>
<br><br>

![](https://img.shields.io/badge/license-MIT-141414?style=flat-square)
![](https://img.shields.io/badge/python-3.10%2B-141414?style=flat-square)
![](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-141414?style=flat-square)
![](https://img.shields.io/badge/language-Python-141414?style=flat-square)
![](https://img.shields.io/github/stars/LEE20260315/AgentMemorySystem?style=flat-square&color=9C2A2A&label=stars)

<br>

<sub><a href="#introduction">INTRODUCTION</a> · <a href="#features">FEATURES</a> · <a href="#quick-start">QUICK START</a> · <a href="#architecture">ARCHITECTURE</a> · <a href="#why-choose-it">WHY CHOOSE IT</a> · <a href="#faq">FAQ</a> · <a href="#support">SUPPORT</a></sub>

<br><br>

<img src="docs/assets/seal-xicheng.png" alt="seal" width="72"/>

<br></div>

---

## INTRODUCTION <a id="introduction"></a>

多 Agent 协同学习不是“共享数据库”那么简单：Claude、Hermes、Trae 的记忆格式不同、路径分散、同步策略各异。**AgentMemorySystem** 解决了“多源提取 + 去重融合 + 按原生格式回写”的完整链路。

This project is a practical memory sync system for **Claude / Hermes / Trae** and similar local AI clients.
- Local-first, no cloud dependency.
- Cross-device persistence through OneDrive/Sync folder.
- Full flow: discover → extract → merge → write back.

## FEATURES <a id="features"></a>

- **多 Agent 自动发现**：候选路径 + 特征校验（签名文件/签名内容/签名 glob）
- **记忆提取**：支持 Claude / Hermes / Trae 原生结构的多文件解析
- **融合去重**：基于内容哈希与多层存储策略（SQLite + `shared` 索引）
- **格式兼容写回**：
  - Claude：子文件 + `MEMORY.md` 索引
  - Trae：`user_profile.md` `## Shared Knowledge`
  - Hermes：`MEMORY.md` 尾部 `§` 分隔
- **安全**：文件备份、冲突检测、锁机制、敏感词检测
- **双入口**：GUI（系统托盘）+ CLI
- **可回滚**：每次同步前自动备份，可一键回滚
- **开箱即用**：双击 `双击运行.bat` 启动

## QUICK START <a id="quick-start"></a>

> 下面以 `Path/to/` 省略主机路径，实际按本机环境调整。

### 1) 安装

```bash
pip install -r requirements.txt
```

### 2) 启动

```bash
# 推荐
python memory_sync_app.py    # GUI
# 或
python memory_sync_app.py --cli
```

### 3) 一键同步

```bash
python memory_cli.py full-sync
```

### 4) 常用 CLI

```bash
# 重新检测 Agent
python memory_cli.py redetect

# 写入一条记忆
python memory_cli.py --agent claude write "记住今天的设计决策" --tags 开发 记忆

# 搜索记忆
python memory_cli.py --agent claude search "关键字"

# 健康检查
python memory_cli.py --agent claude health

# 清理历史并归档
python memory_cli.py --agent claude expire

# 融合写回
python memory_cli.py --agent claude full-merge
```

## ARCHITECTURE <a id="architecture"></a>

```
本地 Agent 记忆（claude/trae/hermes）
  └─> 解析 + 校验 + 采样
          └─> data/_shared/shared.db（SQLite 融合索引）
                  └─> 内容哈希去重
                         └─> 适配写回（Claude/Trae/Hermes）
                                └─> 本地 Agent 可见
```

## WHY CHOOSE IT <a id="why-choose-it"></a>

### 适合你如果你有：

- 多个 AI 客户端并行工作
- 希望知识在多个客户端复用
- 不想搭服务端，也不想管理复杂数据库
- 需要可审计、可回滚的本地同步链路

### 核心卖点（冲星版）

1. **跨客户端“带状态记忆”**：不是临时缓存，是持久记忆协同
2. **本地可控**：不上传，不依赖远程推理服务
3. **极简依赖**：几乎零配置上手，适合真实工程环境
4. **工程闭环**：从安装到同步到健康检查全流程

## FAQ <a id="faq"></a>

### 需要 OneDrive 吗？

不是必须，默认路径在仓库内 `data/`，可通过 `config.json` 指定。

### 支持 Linux / macOS 吗？

命令行可直接用；GUI（tkinter）在大多数 Linux 发行版可运行，但需本地图形环境。

### Hermes 记忆未写回怎么办？

请确认 Hermes 本地路径与签名文件 `MEMORY.md` 可达；或在 `config.json` 中手动覆盖路径。

### 我担心隐私安全

项目提供敏感词检测、备份策略、冲突检测。你可在 `config.json` 配置 `security.block_sensitive`。

## SUPPORT <a id="support"></a>

- 问题反馈（建议）：提 Issue / PR
- 许可证：MIT
- 安装体验问题优先在 `health` 子命令中先做快速诊断

## FILE MAP <a id="file-map"></a>

```bash
AgentMemorySystem/
├── 双击运行.bat             # 启动入口（GUI）
├── memory_sync_app.py        # 托盘 + GUI + 实时日志
├── memory_cli.py             # CLI 入口
├── agent_memory.py           # 核心引擎
├── sync_engine.py            # 同步编排
├── sync_writers.py           # Agent 写回适配器
├── setup_agent.py            # Agent 初始化/注册
├── config.json               # 配置入口
├── requirements.txt          # 依赖
├── pyproject.toml            # 包与依赖元信息
├── LICENSE
├── CHANGELOG.md
├── DEVLOG.md
├── docs/
│   ├── USAGE_EXAMPLE.md
│   ├── V1.3_AUTOMATION.md
│   └── multi_agent_memory_sync_design_v1.2.md
└── test_memory.py            # 129 个测试
```

## ROADMAP

- v1.4：多 Agent 写回冲突智能策略 + 更细粒度回滚比较
- v1.5：命令行输出国际化（中英双语切换）
- v1.6：新增 Web 面板（轻量查看版）

## LICENSE

MIT. See [LICENSE](LICENSE).

<div align="center"><br><sub>紙承墨，墨載意，意馭器</sub><br><sub>西城閒人 · 識</sub><br><sub>MIT © 2026 ・ LEE20260315</sub></div>
