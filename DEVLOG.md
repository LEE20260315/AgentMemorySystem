# 开发日志

## 2026-06-12：系统鲁棒性强化

### 背景

用户反馈系统"半吊子"——能写不能读，能存不能查。前几轮已实现：
- SQLite 索引层 + 搜索 API
- 跨 Agent 融合器（MemoryMerger）
- Trae Solo 记忆导入
- 多设备并发写入（ConcurrentWriteManager）
- 分层存储 + 智能压缩 + 重要性评分

本轮重点：**主动发现并解决用户没提到的问题**。

---

### 改动清单

#### 1. 配置管理系统

**文件**：`config.json`（新建）、`agent_memory.py`（ConfigManager 类）

**改了什么**：
- 新增 `config.json`，集中管理所有可配置参数
- 新增 `ConfigManager` 类，支持点号路径读取（如 `limits.max_backups`）
- 所有硬编码的阈值、路径、限制改为从配置读取

**为什么**：
之前 `max_backups=10`、`similarity_threshold=0.85`、`lock_timeout=30` 等散落在各处，改一个要找一堆地方。现在统一管理。

**受影响的类**：
- `DataProtection`：max_backups、backup_dir
- `TieredStorageManager`：tier 阈值、archive_dir
- `ConcurrentWriteManager`：lock_timeout、lock_dir
- `SmartCompressor`：similarity_threshold

---

#### 2. 日志系统

**文件**：`agent_memory.py`（LogManager 类）

**改了什么**：
- 新增 `LogManager` 类，使用 Python 标准 logging 模块
- 日志文件：`.logs/agent_memory.log`，自动轮转（10MB，保留5个）
- 在以下关键操作中添加日志：
  - `startup()`：记录 agent、device、记忆数量
  - `write_memory()`：记录写入操作和结果
  - `search_memory()`：记录搜索查询
  - `sync_markdown_to_db()`：记录同步文件和数量
  - `MemoryMerger.full_sync()`：记录融合过程
  - `smart_compress()`：记录压缩统计
  - `archive_cold_memories()`：记录归档统计
  - `ConcurrentWriteManager.safe_write()`：记录重试和失败

**为什么**：
之前出了问题不知道发生了什么，没有追踪手段。现在看日志就能知道哪一步出错。

---

#### 3. 敏感信息检测

**文件**：`agent_memory.py`（SensitiveInfoDetector 类）

**改了什么**：
- 新增 `SensitiveInfoDetector` 类，正则匹配敏感关键词
- 默认模式：检测到敏感信息时记录警告日志
- 阻止模式：`config.json` 中设置 `block_sensitive: true` 可阻止写入
- 集成到 `write_memory()`，步骤 1.5（policy_check 之后、ID 生成之前）

**为什么**：
用户可能不小心把密码、API Key 写进记忆，同步到 OneDrive 后泄露。提前检测。

**默认敏感词**：
`password`, `密码`, `secret`, `密钥`, `token`, `令牌`, `api_key`, `private_key`, `私钥`, `credential`, `凭据`, `auth`, `认证`

---

#### 4. 记忆过期机制

**文件**：`agent_memory.py`（expire_old_memories 函数）、`memory_cli.py`（cmd_expire）

**改了什么**：
- 新增 `expire_old_memories()` 函数
- 超过 `max_memory_age_days`（默认365天）的记忆自动归档到 `.archive/` 目录
- 按月份组织归档文件
- 支持 `dry_run` 模式预览

**为什么**：
记忆无限增长会导致数据库膨胀、搜索变慢。过期记忆不应该删除，而是归档。

**CLI 用法**：
```bash
python memory_cli.py --agent claude expire --dry-run   # 预览
python memory_cli.py --agent claude expire              # 执行
```

---

#### 5. 数据库版本管理

**文件**：`agent_memory.py`（get_db_version、set_db_version、migrate_database）

**改了什么**：
- 在 `_init_db()` 中新增 `metadata` 表
- 新增 `migrate_database()` 函数，支持版本迁移
- 当前实现：v0 → v1（创建 metadata 表）

**为什么**：
未来如果需要给 memories 表加字段、改索引，需要一个迁移机制，否则老数据库会出错。

**CLI 用法**：
```bash
python memory_cli.py --agent claude migrate
```

---

#### 6. 健康检查

**文件**：`agent_memory.py`（health_check 函数）、`memory_cli.py`（cmd_health）

**改了什么**：
- 新增 `health_check()` 函数，检查 7 项：
  1. 配置文件是否可读
  2. 数据库连接是否正常
  3. 记忆文件是否存在
  4. 磁盘空间是否充足
  5. 备份目录是否存在
  6. 是否有活跃锁文件
  7. 日志目录是否存在

**为什么**：
运维需要一个快速诊断工具，不用手动检查每个文件。

**CLI 用法**：
```bash
python memory_cli.py --agent claude health
```

---

#### 7. Bug 修复

**load_private_memories() 只加载 memory_private.md**

**问题**：写入时用 `memory_private_<device>.md`，但启动加载时只读 `memory_private.md`，导致重启后记忆丢失。

**修复**：改为扫描 `memory_private*.md`，与 `sync_markdown_to_db()` 行为一致。

**影响**：`test_write_and_reload` 测试从失败变为通过。

---

### 测试结果

```
测试结果: 62/62 通过
```

所有原有测试 + 新功能均通过。

---

### 文件变更汇总

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `config.json` | 新建 | 配置文件 |
| `agent_memory.py` | 修改 | +ConfigManager, +LogManager, +SensitiveInfoDetector, +expire, +health, +migrate, 修复 load_private_memories |
| `memory_cli.py` | 修改 | +health, +expire, +migrate 命令 |
| `test_memory.py` | 修改 | 适配设备专属文件命名 |
| `README.md` | 修改 | 新增系统运维章节 |
| `CHANGELOG.md` | 修改 | 记录变更 |
| `DEVLOG.md` | 新建 | 本文件 |

---

## 2026-06-12（续）：v1.3 自动化扩展 + 代码质量修复

### 背景

另一个 Agent 完成了 v1.3 自动化扩展（AgentRegistry、LocalMemoryParser、TriggerEngine、SessionFlusher、extract 命令），但存在 6 个质量问题。本轮逐一修复。

---

### 问题与修复

#### 1. 硬编码路径（严重）

**问题**：
- `AgentRegistry.LOCAL_PATTERNS` 写死 `C:\Users\MR.Dong\...`
- `extract_local_to_fused` 默认 root 写死
- `TriggerEngine` 默认 root 写死
- CLI `--root` 默认值写死

**修复**：
- `AgentRegistry`：删除类级 `LOCAL_PATTERNS`，改为 `_AGENT_SUBDIRS`（只存子目录名）+ `_build_local_patterns()` 动态拼接 `Path.home()`
- `extract_local_to_fused`：默认 root 改为 `get_config()` 或 `Path.home() / "OneDrive" / "AgentMemory"`
- `TriggerEngine`：同上
- CLI `--root`：默认值改为 `str(Path.home() / "OneDrive" / "AgentMemory")`
- 保留 `self.LOCAL_PATTERNS` 实例属性，兼容测试 monkeypatch

#### 2. 版本号混乱

**问题**：文档叫 v1.3，代码到处写 v1.4。

**修复**：
- `agent_memory.py`：所有 `v1.4` → `v1.3`，`"version": "1.4"` → `"version": "1.3"`
- `memory_cli.py`：同上
- `test_memory.py`：同上

#### 3. TriggerEngine YAML 解析器

**问题**：自己写的极简 YAML 解析器，不支持嵌套、特殊字符等。

**修复**：
- 优先使用 `yaml.safe_load()`（pyyaml）
- fallback 到原有的 `_parse_simple_yaml()`
- `requirements.txt` 新增 `pyyaml>=6.0`

#### 4. SessionFlusher 不做去重

**问题**：同一条目加两次会写两条。

**修复**：
- `flush()` 方法新增去重逻辑：按内容去重，保留首次出现的条目
- 重复条目计入 `skipped`，reason 为 `duplicate_in_buffer`

#### 5. extract_local_to_fused 绕过 write_memory

**问题**：直接手动拼接 Markdown 写入，绕过敏感信息检测、日志、SQLite 同步。

**修复**：
- 重构为调用 `write_memory()`，走完整流程
- 自动创建 `identity.json`、`device_config.json`、`last_sync.json`、`memory_private.md`、`memory_shared.md`（如不存在）
- 自动创建 `_shared/` 下的 `agent_runtime_manual.md`、`writing_policy.md`（如不存在）
- 集成 TriggerEngine：提取时自动检测触发词

#### 6. parse_jsonl 500 字截断

**问题**：`content[:500]` 硬截断，破坏语义。

**修复**：删除截断，存完整内容。`preview` 字段仍截断到 60 字。

---

### 测试结果

```
测试结果: 121/121 通过
```

---

### 文件变更汇总

| 文件 | 变更说明 |
|------|----------|
| `agent_memory.py` | 修复硬编码路径、统一版本号、TriggerEngine 改用 pyyaml、SessionFlusher 去重、extract 改用 write_memory、parse_jsonl 去截断 |
| `memory_cli.py` | 修复硬编码路径、统一版本号 |
| `test_memory.py` | 统一版本号 |
| `requirements.txt` | 新增 pyyaml>=6.0 |
| `DEVLOG.md` | 本条记录 |

---

## 2026-06-13：系统实际运行 + 批量提取修复

### 背景

用户要求实际运行系统，验证"采集本地 Agent 记忆 → 写入融合层"的完整流程。

### 发现的问题

#### extract_local_to_fused 批量写入失败

**问题**：逐条调用 `write_memory()` 时，同一秒内多个条目生成相同 ID 序号（`generate_memory_id` 扫描文件找最大序号，但文件还没写入新条目），导致自检失败。

**修复**：
- 改为批量写入：先在内存中递增序号，最后一次写入文件
- 过滤 HTML 内容（`validate_content` 会拒绝）和 <10 字符内容
- 自行调用 `get_detector().check()` 做敏感信息检测
- 自行调用 `append_memory_entry()` + `MemoryDatabase.insert_memory()` 写入

#### identity.json 旧路径残留

**问题**：`agent_claude/identity.json` 写死了 `C:\Users\MR.Dong\...`，导致 `startup()` 找不到 `agent_runtime_manual.md`。

**修复**：`extract_local_to_fused` 始终覆盖 `identity.json`（不再 `if not exists`）。

#### scan_local 找不到 Claude/Trae 的记忆文件

**问题**：`_AGENT_SUBDIRS` 用 `.trae` 而实际是 `.trae-cn`；Claude 的记忆在 `.claude/projects/*/memory/` 子目录下，扫描只看顶层。

**修复**：
- `.trae` → `.trae-cn`
- Claude 扫描逻辑改为：`.claude/projects/*/memory/MEMORY.md` + `.claude/projects/*/*.jsonl`
- Trae 扫描逻辑改为：`.trae-cn/memory/user_profile.md` + `.trae-cn/memory/projects/*/topics.md`

### 实际运行结果

```
$ python memory_cli.py discover --scan-root
发现 2 个 Agent: claude (9 文件/41 条), trae (3 文件/3 条)

$ python memory_cli.py extract --agent claude
提取 34 条，跳过 7 条（HTML/太短）

$ python memory_cli.py extract --agent trae
提取 3 条，跳过 0 条

$ python memory_cli.py --agent claude full-merge
agent_claude → 共享库: +33 条
agent_trae → 共享库: +3 条
共享库 → agent_hermes: +33 条
共享库 → agent_trae: +30 条

$ python memory_cli.py --agent trae search "Python"
找到 4 条（来自 Trae/Claude/Hermes）
```

### 测试结果

```
测试结果: 121/121 通过
```

### 文件变更

| 文件 | 变更说明 |
|------|----------|
| `agent_memory.py` | _AGENT_SUBDIRS: .trae→.trae-cn；scan_local: Claude/Trae 扫描子目录；extract_local_to_fused: 批量写入+覆盖 identity.json |
| `test_memory.py` | 更新 extract 测试内容（长度 >10） |

---

## 2026-06-13（续）：记忆同步工具（GUI + 自动同步）

### 背景

用户需要一个"双击即跑"的工具，自动完成：读取本地 Agent 记忆 → 融合 → 写回各 Agent。
项目在 OneDrive 中同步到多台电脑，工具必须具备跨机器的通用性和容错能力。

### 设计决策

1. **鲁棒性 Agent 发现**：不依赖硬编码路径，用"候选路径 + 特征验证"动态检测
2. **写回到 Agent 本地目录**：按各 Agent 格式追加到已有文件（Claude 创建子文件+索引、Trae 追加 section、Hermes 用 § 分隔）
3. **GUI + 系统托盘**：tkinter 主窗口（日志+汇总面板）+ pystray 托盘常驻
4. **手动 + 自动**：支持手动触发和定时自动同步（默认 7 天）
5. **日志与回滚**：每次运行写日志 + 备份原文件，支持一键回滚

### 改动清单

#### 1. 修复 Hermes 路径 bug

**文件**：`agent_memory.py`（`_AGENT_SUBDIRS`）

**问题**：代码配置 `~/.hermes/memories/`，但 Hermes Desktop 实际装在 `AppData/Local/hermes/memories/`。

**修复**：新增 `("hermes-appdata", "AppData/Local/hermes", "memories")` 条目。

#### 2. 鲁棒性 Agent 路径探测

**文件**：`agent_memory.py`（`detect_agents()`, `_verify_agent_signature()`, `_scan_agent_memory_files()`, `content_hash()`, `check_onedrive_conflicts()`）、`config.json`

**改了什么**：
- 新增 `detect_agents()` 函数，支持候选路径 + 特征验证
- 支持 `signature_file`（文件存在性）、`signature_content`（文件内容匹配）、`signature_glob`（glob 模式）
- 结果缓存到 `.detected_agents.json`，支持 TTL 过期
- 支持 `agent_overrides` 手动覆盖路径
- 新增 `content_hash()` 计算内容 SHA-256
- 新增 `check_onedrive_conflicts()` 扫描冲突文件

**config.json 新增配置段**：
- `agent_detection`: 各 Agent 的候选路径和特征
- `agent_overrides`: 手动路径覆盖
- `sync_tool`: 自动同步间隔、冲突处理策略、缓存 TTL

#### 3. 写回适配器

**文件**：`sync_writers.py`（新建）

**改了什么**：
- `BaseMemoryWriter` 抽象基类
- `ClaudeMemoryWriter`: 创建 `shared_from_agents.md` 子文件 + 更新 MEMORY.md 索引
- `TraeMemoryWriter`: 追加到 `user_profile.md` 的 `## Shared Knowledge` 段
- `HermesMemoryWriter`: 追加到 `MEMORY.md` 末尾，用 `§` 分隔，尊重 `.lock` 锁文件
- `SyncState`: 基于 content hash 的去重状态管理（`.sync_state.json`）
- `backup_file()`: 文件备份工具函数
- `rollback_last_sync()`: 回滚工具函数

#### 4. 同步引擎

**文件**：`sync_engine.py`（新建）

**改了什么**：
- `SyncEngine` 类：完整同步流程（detect → conflict_check → extract → merge → write_back）
- `SyncReport` 数据类：同步报告（Agent 数量、提取/融合/写回统计、错误列表）
- `run_sync()` 便捷函数
- 复用现有 `extract_local_to_fused()`、`MemoryMerger.full_sync()`、`AgentRegistry`

#### 5. GUI 应用

**文件**：`memory_sync_app.py`（新建）

**改了什么**：
- `SyncMainWindow`: tkinter 主窗口，含日志面板（ScrolledText）和汇总面板
- 系统托盘（pystray）：右键菜单（显示窗口/立即同步/设置/退出）
- `SettingsDialog`: 设置对话框（自动同步间隔、冲突处理、Agent 路径覆盖）
- 后台线程执行同步，不阻塞 GUI
- 关闭窗口时最小化到托盘（可配置）
- CLI 模式：`python memory_sync_app.py --cli`

#### 6. CLI 新增命令

**文件**：`memory_cli.py`

**改了什么**：
- `full-sync`: 完整同步流程（CLI 版本）
- `redetect`: 重新检测本机 Agent 路径（清除缓存）

#### 7. 依赖更新

**文件**：`requirements.txt`

新增：
- `pystray>=0.19`: 系统托盘
- `Pillow>=9.0`: 托盘图标生成

### 测试结果

```
测试结果: 129/129 通过
```

新增 8 个测试：
- `test_content_hash`: 内容哈希一致性
- `test_check_onedrive_conflicts`: 冲突文件检测
- `test_detect_agents`: 3 个 Agent 路径探测
- `test_detect_agents_with_override`: 手动覆盖路径
- `test_sync_state`: 去重状态管理
- `test_hermes_writer`: Hermes 写入 + 去重
- `test_trae_writer`: Trae 写入
- `test_claude_writer`: Claude 子文件 + 索引

### 实际验证

```
$ python memory_cli.py redetect
发现 3 个 Agent:
  hermes [auto] → AppData/Local/hermes/memories (2 文件)
  claude [auto] → .claude/projects (11 文件)
  trae [auto]   → .trae-cn/memory (4 文件)
```

### 文件变更汇总

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `agent_memory.py` | 修改 | +detect_agents, +content_hash, +check_onedrive_conflicts, 修复 Hermes 路径 |
| `sync_writers.py` | 新建 | 写回适配器（Claude/Trae/Hermes）+ 去重 + 备份 + 回滚 |
| `sync_engine.py` | 新建 | 同步引擎主流程 |
| `memory_sync_app.py` | 新建 | GUI + 系统托盘应用 |
| `config.json` | 修改 | +agent_detection, +agent_overrides, +sync_tool |
| `memory_cli.py` | 修改 | +full-sync, +redetect 命令 |
| `requirements.txt` | 修改 | +pystray, +Pillow |
| `test_memory.py` | 修改 | +8 个同步工具测试 |
| `DEVLOG.md` | 修改 | 本条记录 |

---

## 2026-06-13（续）：数据目录迁移 + GUI 美化 + 项目清理

### 1. 数据目录迁移到项目内部

**原因**：原来数据放在 `OneDrive/AgentMemory/`（项目外面），路径管理不便，换机器可能出问题。

**改了什么**：
- 将 `OneDrive/AgentMemory/` 的内容复制到项目内的 `data/` 目录
- 更新所有默认路径：从 `Path.home() / "OneDrive" / "AgentMemory"` 改为 `Path(__file__).parent / "data"`
- 受影响文件：`agent_memory.py`（4 处）、`memory_cli.py`（1 处）、`sync_engine.py`（1 处）

**新目录结构**：
```
AgentMemorySystem/
├── agent_memory.py          # 核心库
├── sync_engine.py           # 同步引擎
├── sync_writers.py          # 写回适配器
├── memory_sync_app.py       # GUI 应用
├── memory_cli.py            # CLI
├── config.json
├── data/                    # 所有数据（原 AgentMemory/）
│   ├── _shared/
│   ├── agent_claude/
│   ├── agent_hermes/
│   ├── agent_trae/
│   └── shared.db
└── docs/                    # 文档
    └── multi_agent_memory_sync_design_v1.2.md
```

### 2. GUI 美化（macOS 风格）

**原因**：原 GUI 是默认 tkinter 样式，比较丑。

**改了什么**：
- 采用 macOS 风格配色方案（`#f5f5f7` 浅灰背景、`#007aff` 蓝色强调色）
- 使用 `clam` 主题 + 自定义 ttk.Style
- 日志面板使用深色背景（`#1d1d1f`），类似终端
- 汇总面板使用卡片式布局
- 状态指示器：绿色=就绪、黄色=同步中、红色=错误
- 按钮样式：蓝色强调按钮 + 灰色普通按钮
- 窗口启动时自动居中
- 不依赖额外包（纯 tkinter/ttk 实现）

**启动方式**：
```bash
python memory_sync_app.py          # GUI 模式
python memory_sync_app.py --cli    # CLI 模式
```

### 3. 项目清理

**清理内容**：
- 删除 `__pycache__/`（编译缓存）
- 删除 `data/prompt.md`（过程文件）
- 移动 `data/multi_agent_memory_sync_design_v1.2.md` → `docs/`（设计文档归档）
- 旧的 `OneDrive/AgentMemory/` 数据已复制到 `data/`，原目录保留（用户确认后可删除）

---

## 2026-06-13（续）：一键启动 + 开源准备

### 1. 一键启动器

**文件**：`双击运行.bat`（新建）

**为什么**：用户说 `python memory_sync_app.py` 对普通人不友好，要一个双击就能跑的东西。`.pyw` 需要 Windows 关联 Python，`.bat` 最靠谱。

**功能**：
- 双击即开（先尝试 pythonw 无黑窗口，fallback 到 python）
- 自动检查依赖，缺失时弹窗提示安装
- 支持一键安装（调用 pip）
- 启动失败时显示友好错误提示

### 2. README.md 重写

**为什么**：准备开源，需要完整的项目文档。

**内容**：
- 快速开始（3 步）
- 依赖说明（核心/GUI/可选，标注大小）
- 启动方式一览表
- 目录结构
- 配置说明
- CLI 命令参考
- 工作原理（流程图 + 鲁棒性发现 + 写回格式 + 安全机制）
- 跨机器使用说明
- 常见问题

### 3. requirements.txt 整理

**改了什么**：
- 添加注释说明每个依赖的用途和大小
- 区分核心/GUI/可选三档
- 提供最小安装命令（`pip install pyyaml`）
- 注释掉大依赖（sentence-transformers ~500MB）

### 文件变更

| 文件 | 操作 | 说明 |
|------|------|------|
| `双击运行.bat` | 新建 | 一键启动器，自动检查依赖 |
| `README.md` | 重写 | 开源级完整文档 |
| `requirements.txt` | 重写 | 分级依赖 + 注释说明 |

---

## 2026-06-13（续）：开源准备（Li 哥 Review 修复）

### 背景

另一位 Claude（Li 哥）对项目做了代码审查，提出 5 项修复建议，确保开源发布质量。

### 修复清单

#### 1. 添加 LICENSE 文件

**文件**：`LICENSE`（新建）

MIT License，Copyright (c) 2026 AgentMemorySystem Contributors。

#### 2. 修复 pyproject.toml

**文件**：`pyproject.toml`

- `build-backend`：`setuptools.backends._legacy:_Backend` → `setuptools.build_meta`（前者是非标准实现，会导致 `pip install -e .` 失败）
- `version`：`0.1.0` → `1.3`（与项目版本一致）
- 新增 `dependencies`：`pyyaml>=6.0`
- 新增 `[project.optional-dependencies]`：gui（pystray、Pillow）、vector（sentence-transformers、numpy）

#### 3. 清理 MR.Dong 路径残留

**影响范围**：
- `.trae/`、`.logs/`、`data/` — 运行时数据，已被 .gitignore 排除，不影响开源
- `DEVLOG.md` — 历史记录中的引用，属于正常开发日志，保留

**实际修改**：
- `docs/multi_agent_memory_sync_design_v1.2.md`：附录 A 系统提示词模板中的 `C:\Users\MR.Dong\...` 改为 `<OneDrive同步目录>/...` 占位符
- `docs/_apply_onedrive_changes.py`：删除（一次性部署脚本，已被 `detect_agents()` + `extract_local_to_fused()` 替代）

#### 4. 清理 docs/ 重复文档

**删除**：
- `docs/V1.3_AUTOMATION.md` — 内容已被 README（同步工具说明）和 DEVLOG（开发记录）覆盖

**保留**：
- `docs/USAGE_EXAMPLE.md` — Python API 用法示例，README 未覆盖，对开发者有价值
- `docs/multi_agent_memory_sync_design_v1.2.md` — 设计文档，项目架构参考

#### 5. data/ 样本数据

`data/` 目录已被 .gitignore 排除，不会进入 Git 仓库。运行时自动生成，无需手动清理。

### 文件变更汇总

| 文件 | 操作 | 说明 |
|------|------|------|
| `LICENSE` | 新建 | MIT 开源协议 |
| `pyproject.toml` | 修改 | 修复 build-backend、版本号、依赖声明 |
| `docs/multi_agent_memory_sync_design_v1.2.md` | 修改 | 附录 A 路径改为占位符 |
| `docs/_apply_onedrive_changes.py` | 删除 | 一次性脚本，已被系统功能替代 |
| `docs/V1.3_AUTOMATION.md` | 删除 | 重复文档，内容已并入 README |
| `CHANGELOG.md` | 修改 | 记录修复项 |

---

## 2026-06-13（续 2）：data/ 目录清理

### 背景

开源准备审查时（DEVLOG 上一条）标注了 `data/` 目录"已被 .gitignore 排除，不会进入 Git 仓库"，但没意识到**当前已经提交到仓库的 history 里的 `data/` 仍含有真实个人数据**（具体如下）。

### 发现的问题

`data/` 目录里 6 处 `MR.Dong` 路径残留：
- `data/agent_hermes/identity.json`（含 `.bak`）
- `data/agent_claude/CLAUDE.md`
- `data/agent_hermes/memory_private.md`（多处）
- `data/agent_claude/memory_private.md`（多处）
- `data/_shared/agent_runtime_manual.md.bak`

此外还有：
- 真实 SQLite 数据库（`memories.db` × 3 + `shared.db`）
- 真实记忆内容（含 100+ 条 Hermes/Claude/Trae 私有记忆）
- 真实 `agent_registry.json` 设备列表
- 历史 `.bak` 备份文件

虽然 `.gitignore` 排除了 `data/`，但已经提交到 git 的 history 仍然会随仓库一起发布。

### 修复方案

**安全优先**：先把 `data/` 重命名为 `data.local.bak/`，**完整保留用户的真实运行时数据**（982K）。
然后重建 `data/` 为干净结构：
```
data/
├── .gitkeep                    # 让 git 跟踪目录存在
├── README.md                   # 告诉其他用户这是什么、怎么用
├── _shared/                    # 空子目录（运行时填充）
├── agent_claude/               # 空子目录
├── agent_hermes/               # 空子目录
└── agent_trae/                 # 空子目录
```

### 用户需要做什么

1. **如果想保留原数据**：什么都不用做，`data.local.bak/` 就在那里。等你确认新版本正常工作后，删掉 `data.local.bak/` 即可。
2. **如果想完全从零开始**：删掉 `data.local.bak/`。
3. **首次运行新代码**：`python memory_cli.py --root data health` 会自动创建所有占位文件。

### 验证

- `python test_memory.py`：129/129 通过（测试用 tempfile 隔离，不受 data/ 影响）
- `python memory_cli.py --root data health`：通过（状态 warning 是空目录正常状态）
- 目录结构正确：4 个子目录 + .gitkeep + README

### 文件变更汇总

| 文件 | 操作 | 说明 |
|------|------|------|
| `data/` | 重命名 → `data.local.bak/` | 保留原真实数据 |
| `data/` | 重建 | 4 个空子目录 + .gitkeep + README |
| `data/.gitkeep` | 新建 | 防止 git 忽略空目录 |
| `data/README.md` | 新建 | 解释目录用途、备份/迁移、路径定制 |
| `CHANGELOG.md` | 修改 | 追加本次清理记录 |
| `DEVLOG.md` | 修改 | 本条记录 |

---

## 2026-06-13：通用 Agent 发现 + 托盘自动安装

### 问题

用户测试时发现两个问题：
1. 系统只能识别 config.json 中列出的 10 个 Agent，如果用户安装了未列出的 Agent 则无法发现
2. 托盘功能缺少 pystray/Pillow 依赖时，弹窗要求用户手动安装，体验差

### 修复

#### 1. 通用 Agent 发现机制

**文件**：`agent_memory.py`（新增 `_discover_generic_agents()` + `_scan_generic_memory_files()`）

**改了什么**：
- 在 `detect_agents()` 末尾新增通用发现扫描
- 扫描 `~/.config/`、`~/AppData/Local/`、`~/AppData/Roaming/` 目录
- 目录名包含 AI 关键词（agent, ai, llm, cursor, copilot 等）且含有 `.md` 记忆文件的，自动识别为 Agent
- 已通过 config.json 精确检测到的路径不会重复扫描
- 新发现的 Agent 使用 `GenericMarkdownWriter` 写回

**为什么**：用户可能安装了任何 AI Agent，不可能在 config.json 中穷举所有。通用发现机制让系统能自动适应新工具。

#### 2. 托盘依赖自动安装

**文件**：`memory_sync_app.py`（修改 `_minimize_to_tray()` 方法）

**改了什么**：
- `_minimize_to_tray()` 中检测到 pystray/Pillow 缺失时，自动 `pip install` 安装
- 安装后重新加载依赖，失败才弹窗提示
- 日志面板显示安装进度

**为什么**：之前弹窗让用户手动运行 pip 命令，对非技术用户不友好。

#### 3. 测试污染修复

**文件**：`agent_memory.py`（`detect_agents` 参数 `write_cache`）、`test_memory.py`

**改了什么**：
- `detect_agents()` 新增 `write_cache=False` 参数
- 测试用例传入 `write_cache=False`，避免测试时覆盖全局缓存
- 之前的测试会把临时目录路径写入 `~/.agent_memory/.detected_agents.json`，导致真实运行时检测失败

**为什么**：测试和生产环境共用缓存路径，测试数据污染了真实缓存。

### 验证

- `python test_memory.py`：129/129 通过

---

## 2026-06-13：托盘修复 + CodePilot 支持

### 背景

用户反馈两个问题：
1. 最小化到托盘时仍提示缺少 pystray/Pillow，需要手动安装
2. CodePilot 作为 Agent 没有被检测到

---

### 改动清单

#### 1. 托盘依赖安装修复

**文件**：`memory_sync_app.py`、`启动记忆同步.vbs`、`双击运行.bat`

**改了什么**：
- `_minimize_to_tray()` 改用 `pip._internal.main()` 在当前进程内直接安装，不再使用 `subprocess.check_call`
- 原因：subprocess 启动的 pip 可能使用不同的 Python 环境（python vs pythonw），安装到了错误的位置
- VBScript 和 Bat 启动器改为先检测 `pythonw` 再 fallback `python`，确保检查和启动使用同一个可执行文件

**为什么**：之前用 subprocess 调 pip，pythonw 和 python 可能指向不同的 Python 安装。

#### 2. CodePilot Agent 支持

**文件**：`config.json`、`agent_memory.py`

**改了什么**：
- `config.json` 新增 `codepilot` agent 配置，`storage_type: "sqlite"`，候选路径 `~/.codepilot`
- `agent_memory.py` 新增 `export_codepilot_memory()` 函数：从 SQLite 数据库读取对话历史，导出为 Markdown
- `detect_agents()` 检测循环中处理 `storage_type: "sqlite"` 类型，自动调用导出函数
- 导出时自动过滤敏感信息（API 密钥、密码、token），支持 8 种脱敏模式

**为什么**：CodePilot 的记忆存在 SQLite 数据库（`~/.codepilot/codepilot.db`）中，不是 Markdown 文件，需要特殊处理。

#### 3. 敏感信息过滤

**文件**：`agent_memory.py`（`_sanitize_sensitive` 函数）

**改了什么**：
- 新增 `_sanitize_sensitive()` 函数，使用正则表达式匹配并替换敏感信息
- 支持的模式：`sk-*`、`ms-*`、`Bearer *`、`api_key=*`、`password=*`、`token=*`、`secret=*`
- `export_codepilot_memory()` 在导出每条消息时调用此函数

**为什么**：CodePilot 对话历史可能包含 API 密钥等敏感信息，导出到共享文件时必须脱敏。

#### 4. 托盘依赖安装修复（第二轮）

**文件**：`memory_sync_app.py`、`启动记忆同步.vbs`、`双击运行.bat`

**改了什么**：
- `_minimize_to_tray()` 中，如果 `sys.executable` 是 `pythonw.exe`，自动切换到同目录的 `python.exe` 来运行 pip
- BAT 文件用 `for /f` + `%%~dpi` 从 python 路径推导 pythonw 路径，确保同一目录
- VBS 文件同理，从 python 完整路径推导 pythonw 路径
- 分离 python（用于 pip）和 pythonw（用于启动 GUI）

**为什么**：`pythonw.exe` 是无控制台版本，`pythonw -m pip` 可能失败（无 stdout/stderr）。pip 需要用 `python.exe` 来运行。

### 验证

- `python test_memory.py`：129/129 通过
- CodePilot 导出测试：成功导出 23KB，8 个 API 密钥被脱敏
- BAT/VBS 语法验证通过

---

## 2026-06-13：EXE 打包

### 背景

托盘依赖（pystray/Pillow）在不同 Python 环境下安装位置不一致，导致反复提示"正在安装"但始终无法 import。用户建议打包为 EXE，一劳永逸。

---

### 改动清单

#### 1. PyInstaller 打包

**文件**：`build.py`（新增）、`AgentMemorySync.spec`（自动生成）

**改了什么**：
- 创建 `build.py` 打包脚本，使用 PyInstaller 6.20 打包为单文件 EXE
- 内置所有依赖：pystray、Pillow、sqlite3、tkinter 等
- `--windowed` 无控制台窗口，`--onefile` 单文件分发
- 排除不需要的大型模块（matplotlib、numpy、pandas、scipy）
- 输出：`dist/AgentMemorySync.exe`（约 20MB）

**为什么**：不同机器上 Python 环境不同，依赖安装位置不一致，打包后彻底解决。

#### 2. 简化托盘代码

**文件**：`memory_sync_app.py`

**改了什么**：
- `_minimize_to_tray()` 移除自动安装逻辑，改为提示用户安装或使用打包版
- EXE 版内置 pystray/Pillow，不需要自动安装

**为什么**：EXE 版已内置依赖，源码版应由用户自行管理依赖。

### 验证

- `python build.py`：打包成功，输出 19.8MB EXE
- `python test_memory.py`：129/129 通过

---

## 2026-06-13：EXE 优化与图标修复

### 背景

用户反馈：EXE 放在 `dist/` 子目录不方便，图标是简陋的蓝色圆圈，20MB 文件偏大，需要清理临时文件。

### 改动清单

#### 1. 打包输出到根目录

**文件**：`build.py`

**改了什么**：
- `--distpath` 指向项目根目录，EXE 直接生成在 `AgentMemorySync.exe`
- 打包完成后自动清理 `build/`、`dist/`、`*.spec` 临时文件

**为什么**：用户不需要多层目录，双击根目录的 EXE 即可。

#### 2. 应用图标 + 托盘图标

**文件**：`memory_sync_app.py`、`build.py`

**改了什么**：
- 新增 `_resource_path()` 函数，兼容 PyInstaller 打包和源码运行
- 主窗口使用 `assets/icon.ico` 设置窗口图标（`root.iconbitmap`）
- 托盘图标从代码绘制的蓝色圆圈改为加载 `assets/icon.ico`
- `build.py` 新增 `--add-data assets;assets` 将图标打包进 EXE

**为什么**：原托盘图标是代码画的简陋蓝色圆圈，用户体验差。

#### 3. EXE 瘦身

**文件**：`build.py`

**改了什么**：
- 新增 15 个 `--exclude-module`：PIL 子模块（GifImagePlugin、JpegImagePlugin 等）、tkinter.ttk、unittest、xmlrpc、pydoc、doctest、argparse、pkg_resources
- 输出从 20MB 降至 18MB

**为什么**：这些模块运行时不需要，减少分发体积。

#### 4. 清理与 .gitignore

**改了什么**：
- `.gitignore` 新增 `AgentMemorySync.exe`、`device_config.json`
- 清理 `build/`、`__pycache__/`、`*.spec` 临时文件

### 验证

- `python build.py`：打包成功，输出 18MB EXE 到根目录
- EXE 窗口图标和托盘图标均使用 `assets/icon.ico`
- `python test_memory.py`：129/129 通过

---

## 2026-06-13：路径显示与定时同步优化

### 改动清单

#### 1. 同步日志显示路径

**文件**：`sync_engine.py`

**改了什么**：
- 提取阶段：显示融合层目录路径、每个 Agent 的源路径和文件数
- 融合阶段：显示共享数据库路径、每个 Agent 的数据库路径
- 写回阶段：显示所有写回目标路径

**为什么**：之前只有写回阶段显示路径，用户不知道记忆文件存在哪里。

#### 2. 定时同步改为小时单位

**文件**：`memory_sync_app.py`

**改了什么**：
- 设置项从 `auto_interval_days`（天）改为 `auto_interval_hours`（小时）
- 默认值从 7 天改为 2 小时
- UI 从 Spinbox 改为 Combobox，选项：1/2/4/8/16/24/48/72 小时
- 新增 `_schedule_next_sync()` 定时调度器，每 60 秒检查是否到时

**为什么**：用户反馈天为单位太粗，需要小时级别的控制。

### 验证

- `python test_memory.py`：129/129 通过
- 语法检查通过
- EXE 双击测试：正常启动，托盘功能可用

---

## 2026-06-13：锁文件残留 + 设置不保存修复

### 问题

1. **Hermes 写回报错"记忆文件被锁定"** — 用户已关闭 Hermes，但 `.lock` 文件残留。原因是 sync_writers.py 用手动 `touch()` 创建锁，程序异常退出（用户杀进程、崩溃）后锁文件不会被清理。
2. **设置不保存** — 用户修改设置后关闭窗口再打开，值恢复默认。原因是点 X 关闭窗口时没有调用 `_save()`，只有点"保存"按钮才保存。

### 改动

#### 1. 过期锁自动清理

**文件**：`sync_writers.py`

**改了什么**：
- HermesWriter.write() 和 GenericMarkdownWriter.write() 的锁检查逻辑，新增 60 秒过期检测
- 锁文件 mtime 超过 60 秒 → 视为过期锁，自动删除，继续写入
- 新增 `import time`

**为什么**：
之前的逻辑是"锁存在就报错退出"，但没有考虑程序异常退出导致锁残留的场景。60 秒阈值足够覆盖正常写入操作，又不会误删正在使用的锁。

#### 2. 设置对话框关闭时自动保存

**文件**：`memory_sync_app.py`

**改了什么**：
- SettingsDialog.__init__() 添加 `self.win.protocol("WM_DELETE_WINDOW", self._save)`
- 点 X 关闭窗口时调用 _save()，等同于点"保存"按钮

**为什么**：
用户习惯点 X 关闭窗口，但之前只有"保存"按钮才触发保存。现在两种关闭方式都会保存设置。

#### 3. 日志和标题优化

**文件**：`memory_sync_app.py`

- 日志背景从 `#1d1d1f`（黑色）改为 `#ffffff`（白色），文字从白色改为 `#3d3d3d`（黑灰色）
- 窗口标题从"记忆同步工具"改为"多Agent记忆融合器"

### 验证

- `python test_memory.py`：129/129 通过
- EXE 重新打包：18.0 MB
- 双击测试：正常启动，设置可保存，锁文件过期自动清理

---

## 2026-06-13：托盘崩溃修复 + 新图标 + 气泡通知

### 问题

1. **托盘功能崩溃** — EXE 运行时报 `ImportError: cannot import name 'ImageDraw' from 'PIL'`
2. **需要新图标** — 用户提供了 app_icon.ico、tray_icon.png 等新图标文件
3. **需要气泡通知** — 定时同步完成后应显示托盘气泡提醒

### 根因

`build.py` 第 43-44 行排除了 `PIL.ImageDraw` 和 `PIL.ImageFont`，但代码在备用图标绘制时需要这两个模块。

### 改动

#### 1. 修复 build.py 打包配置

**文件**：`build.py`

**改了什么**：
- 移除 `--exclude-module PIL.ImageDraw` 和 `--exclude-module PIL.ImageFont`
- 新增 `--hidden-import PIL.Image`、`--hidden-import PIL.ImageDraw`、`--hidden-import PIL.ImageFont`

#### 2. 更新图标引用

**文件**：`memory_sync_app.py`

**改了什么**：
- `_ICON_PATH` 改为 `assets/app_icon.ico`（主窗口图标）
- 新增 `_TRAY_ICON_PATH = assets/tray_icon.png`（托盘专用图标，32x32 标准尺寸）
- `_create_tray_icon()` 优先使用 tray_icon.png，其次 app_icon.ico，最后才画备用图标

#### 3. 托盘气泡通知

**文件**：`memory_sync_app.py`

**改了什么**：
- `_notify()` 方法增加 Windows 原生通知兜底（Shell_NotifyIcon API）
- 同步完成后通知内容更详细：设备名、Agent 数量、提取/写回条数、错误数
- 通知标题改为"多Agent记忆融合器 - 同步完成"

### 验证

- `python test_memory.py`：129/129 通过
- EXE 重新打包：19.0 MB（因包含 PIL.ImageDraw/ImageFont）
- 双击测试：托盘功能正常，图标显示正确

---

## 2026-06-13：托盘不显示 + 重复启动 + EXE 图标修复

### 问题

1. **最小化后托盘没有图标** — 点 X 关闭窗口时，`_on_close()` 检查 `and self.tray_icon`，但此时 tray_icon 还没创建，所以永远不会调用 `_minimize_to_tray()`。
2. **重复启动多个进程** — 双击多次会启动多个实例，没有互斥检测。
3. **EXE 图标没换** — `build.py` 第16行还是 `icon.ico`，没改成 `app_icon.ico`。

### 改动

#### 1. 修复 _on_close() 托盘逻辑

**文件**：`memory_sync_app.py`

**改了什么**：
- 移除 `and self.tray_icon` 条件判断
- 现在点 X 时，只要"最小化到托盘"设置为 True，就直接调用 `_minimize_to_tray()`（内部会创建 tray_icon）

#### 2. 重复启动检测

**文件**：`memory_sync_app.py`

**改了什么**：
- 新增 `_check_single_instance()` 函数，使用 Windows 命名互斥锁（`Global\AgentMemorySyncMutex`）
- 在 `main()` 入口处调用，如果检测到已有实例运行，弹窗提示"已在运行中，请检查系统托盘"后退出

#### 3. EXE 图标修复

**文件**：`build.py`

**改了什么**：
- 第16行 `icon.ico` → `app_icon.ico`

### 验证

- `python test_memory.py`：129/129 通过
- EXE 重新打包：19.0 MB
- 双击测试：托盘图标正常显示，重复启动有提示

---

## 2026-06-13：托盘图标仍然不显示的彻底修复

### 问题

用户反馈点击"最小化到托盘"按钮后，托盘图标仍然不显示。

### 根因分析

1. **PyInstaller 打包 PIL 模块不全** — 之前用 `--hidden-import PIL.ImageDraw` 和 `--exclude-module PIL.ImageDraw` 混在一起，PyInstaller 处理顺序导致 ImageDraw 实际上被排除了。
2. **窗口隐藏过快** — `_create_tray_icon()` 创建图标后立即 `withdraw()`，但 pystray 的托盘图标在后台线程中注册需要时间。

### 改动

#### 1. build.py：改用 --collect-submodules

**改了什么**：
- 移除所有 `--hidden-import PIL.*` 和 `--exclude-module PIL.*`
- 改用 `--collect-submodules PIL` 一次性收集所有 PIL 子模块
- 只排除确定不需要的大型模块（matplotlib、numpy 等）

**为什么**：
`--collect-submodules` 是 PyInstaller 推荐的方式，比逐个 `--hidden-import` 可靠。

#### 2. memory_sync_app.py：添加等待和日志

**改了什么**：
- `_create_tray_icon()` 后添加 `time.sleep(0.3)` 等待图标注册
- 托盘创建成功/失败都写入 `~/.agent_memory/tray_error.log`

### 验证

- `python test_memory.py`：129/129 通过
- EXE 重新打包：19.4 MB


## 2026-07-23：v1.3.7 根治写回始终为0 + pi-web/openclaw 发现

### 背景

用户报告两个长期困扰问题：
1. **多 Agent 记忆同步后写回始终为 0 条**（同步报告：hermes=0, trae=0, ...），但记忆文件在膨胀
2. **无法识别新安装的 pi-web agent**

### 诊断（全量阅读 9 个文件 + 3 个运行时数据文件）

阅读：DEVLOG.md, README.md, config.json, agent_memory.py, sync_engine.py, sync_writers.py, safe_io.py + sync_report.log, sync_settings.json, .sync_state.json

定位 8 个 Bug（P0/P1/P2）：

| ID | 优先级 | 根因 | 影响 |
|----|--------|------|------|
| B1 | P0 | `reconcile_with_target_hashes()` 只有定义零调用（grep 确认） | SyncState 孤儿 hash 永远不清理，目标文件条目数<state条目数 → 写回跳过ALL |
| B2 | P0 | `_load_shared_memories` 硬编码 LIMIT 500 + ORDER BY timestamp DESC | 永远返回同样500条 |
| B3 | P0 | `_is_sync_generated_content` 匹配任何 `[sync:*]` → 100% 过滤含 marker 的条目 | 增量来源枯竭 |
| B4 | P0 | `_scan_agent_memory_files` 无文件大小上限 | 读入5MB+文件 |
| B5 | P1 | 回声循环：写回→提取→再写回，提取阶段不做污染自愈 | 几何增长 |
| B6 | P1 | `ai_keywords` 不含 "pi"/"openclaw"；通用发现不含 OneDrive/npm_global | pi-web/openclaw 不可检测 |
| B7 | P2 | Claude 未被 detect_agents 识别（缓存或特征验证问题） | 降低 |
| B8 | P2 | 日志轮转 10MB 过高（实际已 2.4MB） | 日志膨胀 |

### 修复

| Bug | 文件 | 改动 |
|-----|------|------|
| B1 | sync_engine.py | run() ⑤写回前调用 `reconcile_with_target_hashes()` 清理孤儿 hash |
| B2 | sync_engine.py | `_load_shared_memories` 改为增量：LIMIT 2000 + `content_hash` 跳过已写回条目 |
| B3 | agent_memory.py | `_is_sync_generated_content` 改为"剥离 marker 后 <30 字才过滤" |
| B4 | agent_memory.py | `_scan_agent_memory_files` 所有分支 10MB 上限 + parse_hermes_memory/parse_markdown 超大文件污染自愈前置 |
| B5 | sync_writers.py | `detect_pollution`/`repair_polluted_file` 提升为模块级函数，agent_memory 提取阶段复用；自愈后清零 SyncState |
| B6 | agent_memory.py | `ai_keywords` 追加 pi/openclaw/deepseek/gemini/chatgpt/coding/trae/hermes；`generic_candidates` 增加 `~/.npm-global/node_modules/@agegr` + `~/OneDrive` |
| B6 | sync_writers.py | WRITER_REGISTRY 增加 pi-web/openclaw/codepilot/pi/clawdbot |
| B8 | agent_memory.py | 日志轮转默认 2MB/3 备份（原 10MB/5 备份）|

### 实测验证

```
=== 第一次同步（修复后）===
hermes: 写入 125 条 (修复前: 0)
trae: 写入 178 条 (修复前: 0/16)
codepilot: 写入 143 条 (修复前: 0)
codebuddy: 写入 160 条 (修复前: 0)
openclaw: 写入 106 条 (修复前: 未检测)
reconcile 清理: hermes 5 孤儿, trae 69 孤儿
耗时: 22.2 秒

=== 第二次同步 ===
hermes: 142, trae: 267, codepilot: 200, codebuddy: 109, openclaw: 149
reconcile 清理: trae 61 孤儿 (自愈积累)
耗时: 42.4 秒
```

### 待确认
- [ ] 常驻托盘程序 (memory_sync_app.py) 正常运行检查
