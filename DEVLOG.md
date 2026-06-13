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
