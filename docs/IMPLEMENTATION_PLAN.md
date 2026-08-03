# AgentMemorySystem — 代码审查 · 待办梳理 · 实施计划

> 本文档基于对当前代码库（`v2.0.4`，commit `2c54bd0`）的完整审查生成，是后续所有开发的**唯一权威计划**。
> 配套 PR 工作流规范见第 4 节；本次交付物本身也严格走 PR 流程（见 4.5）。

---

## 1. 项目架构审查

### 1.1 总体架构（分层）

系统采用四层 + 两入口的分层结构，自底向上依赖清晰、无循环依赖：

```
┌──────────────────────────────────────────────────────────────┐
│ 交互层  memory_sync_app.py (tkinter GUI + Windows 托盘 + CLI)  │  ← 入口 A
│        memory_cli.py (argparse CLI)                            │  ← 入口 B
└───────────────┬───────────────────────────┬──────────────────┘
                │ 懒导入                       │ 直接导入
                ▼                             ▼
┌───────────────────────────┐   ┌──────────────────────────────┐
│ 编排层  sync_engine.py     │   │ setup_agent.py (初始化脚本)   │
│ SyncEngine.run(): 发现→    │   └──────────────────────────────┘
│ 提取→融合→写回→体积控制     │
└───────────┬───────────────┘
            │ 导入
   ┌────────┴─────────┐
   ▼                  ▼
┌────────────────┐  ┌────────────────────────────┐
│ 适配层          │  │ 核心层 agent_memory.py      │
│ sync_writers.py│  │ SQLite/并发/备份/压缩/检测/  │
│ 各 Agent 写回器 │  │ 解析/注册表/触发器/会话落盘  │
└────────┬───────┘  └──────────────┬─────────────┘
         │ 导入                     │ 导入
         └──────────┬───────────────┘
                    ▼
         ┌────────────────────┐
         │ 叶子层 safe_io.py  │  (仅依赖标准库)
         │ 安全读写/数据根解析 │
         └────────────────────┘

配置：config.json / device_config.json / pyproject.toml / requirements.txt
测试：test_full.py (47 函数 / 146 断言，自定义 TestRunner，失败退出码 1)
打包：build.py (PyInstaller --onedir)
```

### 1.2 模块职责与依赖关系

| 模块 | 行数 | 职责 | 直接依赖（本项目内） | 对外关键接口 |
|------|------|------|----------------------|--------------|
| `safe_io.py` | 165 | 原子写、重试、数据根解析（`get_data_root`）、`.pending` 回退 | 无（仅 stdlib） | `get_data_root()`, `_safe_write_text()`, `_safe_read_text()` |
| `agent_memory.py` | 7193 | **核心层**：配置、日志、敏感词、数据模型、SQLite(`MemoryDatabase`)、Embedding、检测(`detect_agents`/`_scan_agent_memory_files`)、解析(`LocalMemoryParser`)、融合(`MemoryMerger`)、去重、压缩(`SmartCompressor`)、分层存储、注册表(`AgentRegistry`)、触发器、会话落盘、健康检查 | `safe_io` | `detect_agents()`, `extract_local_to_fused()`, `MemoryMerger`, `write_memory()`, `health_check()`, `AgentRegistry` |
| `sync_writers.py` | 1858 | **适配层**：`BaseMemoryWriter` ABC + Claude/Trae/Hermes/Generic 写回器、`SyncState` 去重、污染检测/修复、`WRITER_REGISTRY`、`rollback_last_sync` | `agent_memory`, `safe_io` | `get_writer()`, `SyncState`, `rollback_last_sync()` |
| `sync_engine.py` | 998 | **编排层**：`SyncEngine.run()` 串联发现→提取→融合→写回→体积控制；`SyncReport`；回滚 | `agent_memory`, `sync_writers` | `SyncEngine().run()`, `run_sync()`, `SyncEngine().rollback()` |
| `memory_cli.py` | 1096 | **CLI 入口**：`full-sync`/`redetect`/`write`/`search`/`health`/`expire` 等 | `agent_memory` | `python memory_cli.py <cmd>` |
| `memory_sync_app.py` | 2911 | **GUI 入口**：tkinter 主窗、Windows 原生托盘、自动同步调度、设置面板 | `sync_engine`(函数内懒导入), `agent_memory` | 双击 `memory_sync_app.py` / `AgentMemorySync.bat` |
| `setup_agent.py` | 131 | **初始化**：创建 `identity.json`/`device_config.json`/记忆文件/triggers，并注册到 `AgentRegistry` | `agent_memory` | `python setup_agent.py --agent X --device Y --root Z` |
| `build.py` | 448 | **打包**：PyInstaller `--onedir`，生成 `AgentMemorySync/` 分发包 + 启动器 + 快捷方式 | 仅 stdlib（`subprocess` 调 `pyinstaller`） | `python build.py` |
| `test_full.py` | — | **测试套件**：6 模块、146 断言、退出码 0/1 | 全部被测模块 | `python test_full.py [--module X]` |

### 1.3 依赖拓扑序（编译/加载顺序）

```
safe_io  →  agent_memory  →  { sync_writers, memory_cli, setup_agent }
                                │
                                ▼
                            sync_engine  →  memory_sync_app
```

> 关键结论：**无任何循环依赖**。`memory_sync_app` 对 `sync_engine` 采用函数内懒导入（`from sync_engine import SyncEngine`），避免 GUI 启动时的硬耦合，也使测试可独立导入核心层。

### 1.4 一次 `full-sync` 的调用链

```
memory_cli.py full-sync
  → run_sync() / SyncEngine().run()
      ① 发现：agent_memory.detect_agents()  (候选路径 + 特征校验 + 缓存 + 通用发现)
      ②.5 污染清理：_purge_polluted_entries() + _clean_md_files()   [v2.0.2 新增]
      ③ 提取：agent_memory.extract_local_to_fused() (LocalMemoryParser 多格式解析)
      ④ 融合：MemoryMerger.full_sync()  (content-hash 去重 + _resolve_conflict)
      ⑤ 加载共享：_load_shared_memories()  (增量加载)
      ⑥ 写回：sync_writers.get_writer(agent).write()  (WRITER_REGISTRY 按 Agent 分发)
      ⑦ 体积控制：_enforce_volume_control() + _enforce_db_limit()
      ⑧ 报告：SyncReport.summary_text()
```

### 1.5 质量现状与一致性问题（审查发现）

| 项 | 现状 | 风险 |
|----|------|------|
| 测试 | `test_full.py` 146/146 通过，退出码正确 | ✅ 可用作 CI 门禁 |
| `CONTRIBUTING.md` | 仍写 `python test_memory.py`，但该文件 **v2.0.4 已删除** | ❌ 误导贡献者；命令失效 |
| `CONTRIBUTING.md` | 无「禁止直推 main」「必须 review/CI」等硬性规则 | ❌ 无法满足本次 PR 工作流要求 |
| CI | 无 `.github/workflows/` | ❌ 无法自动验证分支 |
| 分支命名 | 既有 `fix/*`，CONTRIBUTING 写 `feature/*` | ⚠ 需统一规范 |
| `pyproject.toml` | `version = "1.3.6"`，但 README/CHANGELOG 已到 v2.0.4 | ❌ 版本号漂移 |
| `agent-memory-redesign/` | 未跟踪目录，来自 `solo-design` 技能的 UI 原型（HTML/CSS/JSON），非核心代码 | ⚠ 与核心待办无关，建议独立管理或忽略 |

---

## 2. 待办事项梳理（TODO.md 分析）

### 2.1 来源

- **主来源**：`TODO.md`（9 项，按高/中/低优先级分组，最后更新 2026-07-31 v2.0.4）。
- **补充来源**：`CHANGELOG.md` 的 `[Unreleased]` 区（当前为空，无在途项）；`.github/ISSUE_TEMPLATE/`（仅模板，无实际 Issue）。
- **排除**：`agent-memory-redesign/` 是一次性 UI 设计原型，不属于工程待办，单列管理。

### 2.2 条目清单（优先级 / 所属模块 / 依赖 / 工作量）

| # | TODO 项 | 优先级 | 主要影响模块 | 依赖 | 估工作量 |
|---|---------|--------|--------------|------|----------|
| T1 | 预编译 EXE 发布（GitHub Actions 自动构建 + 代码签名/SmartScreen 指引） | 高 | `build.py` + 新增 CI | 需 CI 基建（见 P0）；可复用 `python build.py` | M |
| T2 | macOS / Linux GUI 支持（跨平台托盘 / `.app` 打包） | 高 | `memory_sync_app.py` | 与 T1 部分重叠（打包）；改动面大 | L |
| T3 | 体积保护算法优化（按 priority/confidence 智能保留 + 压缩归档至 cold tier） | 高 | `sync_writers._enforce_write_volume_limit` + `agent_memory.TieredStorageManager`/`SmartCompressor` | 复用 `volume_policy.json` | M |
| T4 | 更多 Agent 支持（插件式架构，社区可贡献适配器） | 中 | `agent_memory.detect_agents`/`_scan_agent_memory_files` + `config.json` + `sync_writers.WRITER_REGISTRY` | 需先定插件接口（设计任务） | L |
| T5 | 同步冲突解决策略（`merge` 策略 + 实时冲突检测/通知） | 中 | `sync_engine` + `config.sync.conflict_strategy` + `agent_memory.MemoryMerger._resolve_conflict` | 独立；触碰融合核心 | M |
| T6 | 记忆搜索与检索增强（本地 embedding 语义搜索 + 多维筛选） | 中 | `agent_memory.EmbeddingService`/`SearchOptimizer`/`MemoryDatabase.search_by_vector` | 可选重依赖 `sentence-transformers` | M |
| T7 | 效能优化（10万+ 基准 / 索引 / 分页 / 增量同步） | 低 | `MemoryDatabase` + `sync_engine` 增量逻辑 | 需基准数据（T9 部分） | M |
| T8 | UI/UX 改进（暗色模式 / 可视化浏览器 / 同步时间轴） | 低 | `memory_sync_app.py` | 部分依赖 T2（跨平台） | M |
| T9 | 文档与测试（单元覆盖率 / 贡献者指南 / 演示视频） | 低* | `test_full.py` + `docs/` | **横向赋能所有项**，应前置 | S~M |

> *T9 虽列低优先级，但是**安全合并的前置条件**——每完成一项功能都必须补测试，故在执行顺序上前置为持续活动。

### 2.3 依赖关系图

```
        P0 工作流基建 (CONTRIBUTING/CI/PR模板)  ← 本次交付，无依赖
              │
              ├──────────────► T1 (EXE/CI 发布)  ──┐
              │                                    │
        T9 (测试/文档, 持续) ──┬───────────────────┤
              │                │                   │
              ├────────────► T3 (体积保护)         │
              ├────────────► T5 (冲突策略)         │
              ├────────────► T4 (插件架构)         │
              ├────────────► T6 (语义搜索)         │
              │                                    │
              └────────────► T7 (效能, 需基准)      │
                                                   │
        T2 (mac/linux GUI) ── 独立长轴 ──┐         │
        T8 (UI/UX) ── 依赖 T2 与 设计 ──┴─────────┘
```

**依赖要点**：
- T1、T3、T5、T4、T6、T7、T8 **全部依赖** P0 工作流基建与 T9 测试基线（合并安全）。
- T1 与 T2 共享打包链路（`build.py`）——建议 T1 先落地，T2 复用其 CI/打包经验。
- T4「插件式架构」是一项**设计决策**，应先于具体 Agent 适配器扩展。
- T8 依赖 T2（跨平台 GUI 定型后 UI 才好统一）。
- T7 效能基准依赖 T9 的基准测试脚手架。

### 2.4 风险与前提

- **T2/T8（GUI 跨平台）**：当前托盘基于 Windows 原生 API（`ctypes` + `Shell_NotifyIcon`），macOS/Linux 需替换为 `pystray` 后端或多后端抽象；改动面大、风险高，建议独立长生命周期分支。
- **T6（语义搜索）**：`sentence-transformers` 约 500MB，且为可选依赖；需保证「不装也能跑」的降级路径（已在 `pyproject` 的 `vector` extras 中预留）。
- **T1（代码签名）**：SmartScreen 消除需付费代码签名证书；若暂不购买，至少需自动化构建 + 清晰执行指引（已在 TODO 中确认）。
- **版本号漂移**：开工前应先把 `pyproject.toml` 对齐到 `2.0.4`（见 3.3 阶段 0 收尾项）。

---

## 3. 实施计划

### 3.1 阶段划分（按依赖 + 价值排序）

| 阶段 | 目标 | 包含 TODO | 出口标准 |
|------|------|-----------|----------|
| **阶段 0 — 工作流基建**（本次交付） | 建立 PR 工作流与测试基线 | P0 + 版本号对齐 + 测试冒烟 | CI 通过、CONTRIBUTING 更新、PR 模板就绪 |
| **阶段 1 — 高价值低风险** | 体积保护 + 发布 + 测试补强 | T3, T1, T9(持续) | 三项各自独立 PR 合并 |
| **阶段 2 — 核心逻辑增强** | 冲突/插件/搜索 | T5, T4, T6 | 每项独立 PR |
| **阶段 3 — 平台与体验** | 跨平台 GUI + UI + 效能 | T2, T8, T7 | 长生命周期分支 |

### 3.2 任务拆分（每项 TODO → 子任务 → 产出 → 完成标准）

#### P0 — PR 工作流基建（本次交付，见第 4 节）
- P0.1 重写 `.github/CONTRIBUTING.md`（修正 `test_memory.py`→`test_full.py`，加入禁止直推 main、分支命名、review+CI 门禁）
- P0.2 新增 `.github/workflows/ci.yml`（PR 与 push main 时跑 `python test_full.py`，失败阻断）
- P0.3 新增 `.github/PULL_REQUEST_TEMPLATE.md`（含分支验证/测试证据/完成标准勾选）
- P0.4 对齐 `pyproject.toml` 版本号至 `2.0.4`
- **产出**：三个新/改文件 + 计划文档；**完成标准**：`python test_full.py` 仍 146/146，CI 配置语法有效

#### T3 — 体积保护优化
- T3.1 在 `BaseMemoryWriter._enforce_write_volume_limit` 引入 priority/confidence 排序，低优先级先截断（复用 `MemoryEntry` 字段）
- T3.2 对超限内容调用 `SmartCompressor`/`TieredStorageManager` 压缩归档至 cold tier，而非删除
- T3.3 `volume_policy.json` 扩展策略字段（保留策略/归档阈值）
- **产出**：改写 `sync_writers.py` + 配置；**完成标准**：新增单元测试 `test_volume_limit_priority_keep`/`test_volume_archive_to_cold`，`test_full.py` 全绿，CHANGELOG `[Unreleased]` 记录

#### T1 — 预编译 EXE 发布
- T1.1 在 CI 中复用 `python build.py`（需先确认 `--onedir` 在无显示环境可构建，或拆出 headless 打包步骤）
- T1.2 Release workflow：打 tag 时构建并上传 EXE artifact
- T1.3 文档补充「下载+执行指引 / SmartScreen 处理」
- **产出**：`.github/workflows/release.yml` + 文档；**完成标准**：tag 推送后 Action 成功产出 EXE，README 链接可用

#### T5 — 冲突解决策略
- T5.1 在 `MemoryMerger._resolve_conflict` 新增 `merge` 分支（非冲突部分自动合并）
- T5.2 `config.sync.conflict_strategy` 支持 `newer_wins`/`merge`；冲突段落入报告供用户确认
- T5.3 多机同时写场景的冲突检测/通知钩子
- **产出**：改 `sync_engine.py`+`agent_memory.py`+`config.json`；**完成标准**：新增 `test_conflict_merge`/`test_conflict_newer_wins`，全绿

#### T4 — 插件式 Agent 架构
- T4.1 定义 `WriterPlugin`/`DetectorPlugin` 接口（抽象基类 + 注册表 `register_plugin()`）
- T4.2 将现有 `WRITER_REGISTRY`/`detect_agents` 适配为可插拔
- T4.3 `CONTRIBUTING` 增加「如何编写 Agent 适配器」指南
- **产出**：接口 + 适配器重构 + 指南；**完成标准**：插件加载示例测试通过，旧适配器行为不变（回归测试全绿）

#### T6 — 语义搜索
- T6.1 强化 `EmbeddingService`（lazy 加载 `sentence-transformers`，缺失时降级关键词）
- T6.2 `search_memory` 支持 `mode="semantic"|"keyword"|"hybrid"` + 多维筛选（时间/Agent/标签）
- **产出**：改 `agent_memory.py`；**完成标准**：`vector` extras 未装时关键词搜索不受影响，装后语义搜索有测试

#### T2 — macOS/Linux GUI
- T2.1 抽象托盘后端（`WindowsTrayBackend`/`PystrayBackend`），运行时按平台选择
- T2.2 macOS `.app` 打包（复用 `build.py` 扩展 target）；Linux 托盘验证
- **产出**：`memory_sync_app.py` 后端抽象 + 打包扩展；**完成标准**：Windows 行为不变，macOS/Linux 至少托盘可启动（CI 仅做导入/语法校验）

#### T8 — UI/UX
- T8.1 暗色模式（基于现有 `COLORS` token 扩展明暗两套）
- T8.2 记忆可视化浏览器（树状/图状）+ 同步历史时间轴
- **产出**：GUI 增强；**完成标准**：新增 GUI 冒烟测试，手动验证清单

#### T7 — 效能优化
- T7.1 10万+ 条基准脚本（写入 `tools/benchmark.py`）
- T7.2 SQLite 索引优化 + 分页加载 + 增量同步阈值调优
- **产出**：基准工具 + 优化；**完成标准**：基准前后对比数据记录，回归测试全绿

#### T9 — 文档与测试（贯穿各阶段）
- 每完成一项功能，在 `test_full.py` 或新增 `test_<module>.py` 补覆盖
- 编写「Agent 适配器开发规范」「贡献者指南」
- 录制演示视频（可选，发布阶段）
- **完成标准**：核心模块单测覆盖率提升，PR 模板强制勾选「已补测试」

### 3.3 执行顺序（建议合并序列）

```
阶段0:  P0(工作流)  ──合并──► main
  │
阶段1:  T3 ──PR──► main
        T9(持续) ──随各 PR 带入──► main
        T1 ──PR──► main
  │
阶段2:  T5 ──PR──► main
        T4 ──PR──► main
        T6 ──PR──► main
  │
阶段3:  T2(长分支) ──PR──► main
        T8 ──PR──► main
        T7 ──PR──► main
```

> 每个 PR 强制：独立分支 → 本地 `python test_full.py` 全绿 → 推送 → 开 PR → CI 绿 + 至少 1 审批 → 方可合并。**绝不直接推 main。**

### 3.4 完成标准总表

| 维度 | 统一标准 |
|------|----------|
| 功能 | 实现 TODO 描述的行为，且有对应测试覆盖 |
| 测试 | `python test_full.py` 全绿（基线 146，新增后只增不减） |
| CI | GitHub Actions `ci.yml` 绿（PR 与 main 均通过） |
| 文档 | 行为变更须更新 README/CHANGELOG `[Unreleased]`；按需更新 CONTRIBUTING |
| 工作流 | 仅经功能分支 + PR + review 合并；无直推 main |
| 版本 | 合并时同步 `pyproject.toml` 与 CHANGELOG 版本（语义化） |

---

## 4. PR 工作流规范（强制）

> 本节为项目硬性规则，违反即视为不合格。本次交付（计划 + 工作流文件）本身也按此执行。

### 4.1 分支策略
- **`main` 为受保护分支，禁止任何直接提交/推送。**
- 开发一律在独立分支：`feature/<slug>`（新功能）、`fix/<slug>`（缺陷）、`chore/<slug>`（杂项/文档）。
- 分支从最新 `main` 拉取；长期分支定期 `rebase`/`merge` main 以避免偏离。
- 合并后删除分支（保留历史由 merge commit 承载）。

### 4.2 开发流程（标准 7 步）
1. `git switch -c feature/<slug> main`
2. 编码 + 补测试（每功能至少 1 单测）
3. 本地 `python test_full.py` 必须全绿
4. `git commit`（原子提交，信息含 TODO 引用，如 `feat: T3 体积保护智能保留 (#T3)`）
5. `git push -u origin feature/<slug>`
6. 开 PR（填 PR 模板：关联 TODO、测试证据、完成标准勾选）
7. **CI 绿 + 至少 1 审批** → 合并（squash 或 rebase）→ 删分支

### 4.3 合并门禁（任一不满足则禁止合并）
- ❌ 直接向 `main` 提交 → **严禁**
- ✅ 变更全部位于功能分支
- ✅ `python test_full.py` 本地全绿 + CI 绿
- ✅ PR 描述完整、关联对应 TODO 项
- ✅ 至少 1 位 reviewer 批准（solo 开发时：CI 必须绿 + 自审清单全勾，且不得自合并除非绿）
- ✅ 无未解决的 CONFLICT

### 4.4 配套落地文件（本次随 P0 提交）
- `.github/CONTRIBUTING.md`（更新版，含上述规则）
- `.github/workflows/ci.yml`（自动化测试门禁）
- `.github/PULL_REQUEST_TEMPLATE.md`（合并前自检清单）

### 4.5 本次交付如何走 PR
- 当前已在分支 `feature/impl-plan-and-pr-workflow` 上完成：
  - `docs/IMPLEMENTATION_PLAN.md`（本文件，主交付物）
  - `.github/CONTRIBUTING.md`（重写）
  - `.github/workflows/ci.yml`（新增）
  - `.github/PULL_REQUEST_TEMPLATE.md`（新增）
- 已本地运行 `python test_full.py` → **146/146 通过**（未改动被测代码，仅新增/更新工作流文件）。
- **下一步（需用户在具备 GitHub 网络/权限的环境执行，或本机若可推送则自动）**：
  ```bash
  git push -u origin feature/impl-plan-and-pr-workflow
  # 于 GitHub 打开 PR：feature/impl-plan-and-pr-workflow → main
  # 等待 CI 绿 + review 批准后合并；本代理不会自行合并 main
  ```
- 如本环境无法推送（沙箱为数据中心 IP，GitHub 推送可能被拦截），则保留本地分支，待用户在本地执行上述命令；**在 PR 审查通过前，任何变更都不会进入 main。**

---

## 5. 本次交付物清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `docs/IMPLEMENTATION_PLAN.md` | 新增（主交付物） | 架构审查 + TODO 梳理 + 实施计划 + PR 规范 |
| `.github/CONTRIBUTING.md` | 改写 | 修正失效命令，加入 PR 工作流硬性规则 |
| `.github/workflows/ci.yml` | 新增 | PR/push main 自动跑测试，失败阻断 |
| `.github/PULL_REQUEST_TEMPLATE.md` | 新增 | 合并前自检清单（分支验证/测试证据/完成标准） |

> 所有文件均在功能分支 `feature/impl-plan-and-pr-workflow` 上，未触碰 `main`。
