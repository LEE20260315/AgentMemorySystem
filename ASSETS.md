# 可复用资产清单

> 终止于 2026-10-06。此文档记录**哪些东西值得留给下一个项目**，
> 以及**怎么用**。完整评估见 [`PROJECT_EVALUATION_2026-10-06.md`](PROJECT_EVALUATION_2026-10-06.md)。

---

## ⭐ 顶级资产：只读记忆摄取器（`recall/`）

**为什么值钱**：这是全项目唯一**不依赖任何具体 agent 格式**的组件。
它解决的问题（"从一堆 Markdown 里检索出相关记忆"）在任何 agent 记忆方案里都存在。

| 特性 | 说明 |
|---|---|
| **零第三方依赖** | `dependencies = []`，只用标准库 sqlite3/hashlib/json/re |
| **强只读不变式** | 改源文件 1 字节 → 测试立即变红（**变异测试**级保障） |
| **跨源折叠** | 同一记忆在多个 agent 有副本时，检索结果自动合并去重 |
| **打分可解释** | 纯 Python BM25 形状（`_tf` 饱和词频 / `_idf` / 标题 3x 加权） |

**关键文件**：

```
recall/ingest.py     只读摄取（严格只读源树，只写本地 index.db）
recall/store.py      schema + BM25 形状打分 + 跨源折叠
recall/runner.py     编排 + 健康分级（EXIT_OK/EMPTY/ERRORS/FAIL 四档）
recall/config.py     三级可写性探测（LOCALAPPDATA → TEMP → TMP → home）
recall/telemetry.py  查询遥测（只记 query 哈希，不记正文）
```

**移植方式**：

1. 直接复制 `recall/` 整个目录（无依赖，3.8k 行）
2. 改 `recall_config.json` 的 `sources` 为你的记忆目录
3. **必读已知缺陷**（见下），改后再用

**⚠️ 移植前必改的 3 处**：

| # | 位置 | 问题 | 改法 |
|---|---|---|---|
| 1 | `ingest.py:216-218` | 静默吞掉全部文件读失败，模块内**零日志**（仅 1 行孤立 print） | 补 WARN + 失败计数（`sync_writers.py` 已有正确模式可照搬） |
| 2 | `autopilot.py:151` | `PRAGMA query_only is not None` **恒为 True**（RW/RO 连接都返回行） | 复用 `eval/_common.py:87-97` 的 `open_readonly()`（用 SQLite URI `mode=ro`） |
| 3 | `cli.py:113-114` | `stopped` 时 `return 0` → 被停止与成功执行**退出码相同** | 停止状态应返回非 0 |

**不适用的场景**：条目数 >1 万且要求 p99 < 500ms 时，
当前 `search()` 会成为瓶颈（它把候选行全量 `fetchall()` 到内存再纯 Python 打分）。
计划书 §17 已给出方案：为 CJK 建 2-gram 倒排替代 `LIKE` 全表扫（**未实施**）。

---

## ⭐ 方法论资产：预登记判据 + 变异测试

这两样**比代码更值钱**，可直接搬到任何"要防止自我验收放水"的项目。

### 1. 预登记判据（`RECALL_FIRST_PLAN.md` §7）

**做法**：改造效果判定前，先写下"哪几条判据同时成立才保留"，并写进文档。

**效果**：本次评估里，`REPORT.md:381-387` 记录了作者**三次自我否决测量结果**
（"我前两次测量因列顺序写错而自我否决"）—— **这在个人项目里极为罕见**。

**复用的判据范式**（来自本项目的实际案例）：

```
三条同时成立才保留排序改造：
  recall@5_new >= recall@5_base
  MRR_new      >= MRR_base - 0.005
  DOC_new      <= 0.8 * DOC_base
```

### 2. 变异测试（`recall/tests/test_readonly_invariant.py`）

**做法**：不只测"检查通过"，还要**证明这个检查会失败**——

```
① 全量快照源树 SHA256 → ingest + search → 再快照 → 必须逐字节相同
② 证明检查器不是橡皮章：向源树注入 1 字节 → 断言必须变红
③ 新建一个文件 → 断言必须变红
```

> **这是本项目质量最高的设计**。任何"审计类"检查都应配一个"注入违规后必须报警"的测试。

### 3. 主动作废被污染的基线

`REPORT.md:17-32` 记录 v1 黄金集因**停用词做查询、oracle 含旧系统注入标记**
而被作废、并归档为 `archive/golden_pairs.v1-confounded.json` 留痕。

> 坏基线比没基线更危险 ——它会让人对着错误的方向优化。

---

## 可复用的数据卫生规则

| 规则 | 出处 | 说明 |
|---|---|---|
| **运行期写入不落同步目录** | v2.2.0 | SQLite 存 `%LOCALAPPDATA%`，跨机只交换可 diff 的 Markdown |
| **原子性 ≠ 隔离性** | 跨进程锁 | `tmp+fsync+os.replace` 只保证不写坏；读-改-写互斥要另做（Windows 命名互斥量） |
| **Windows 批处理必须 CRLF** | `.gitattributes` | 裸 LF 会让 cmd.exe 从行中间切断命令（实测 6 个脚本同时损坏） |
| **删除操作走唯一入口** | v2.1.0 | 统一走 `delete_memory()`，否则 FTS 索引会产生孤儿 |
| **记住 `.bat` 里 %errorlevel% 展开时机** | `install_task.bat` | 括号块内需延迟展开，否则判断失效 |

---

## ⚠️ 已知缺陷清单（这些**没有**修好）

诚实记录，避免下一个项目重踩。

### 高危

| # | 位置 | 问题 |
|---|---|---|
| 1 | `autopilot.py:151` | `PRAGMA query_only is not None` 恒为 True → `db_readable` 是**恒真字段** |
| 2 | `ingest.py:216-218` | 静默吞掉全部文件读失败，无日志 → `errors>0` 时**不可诊断** |
| 3 | `cli.py:113-114` | `stopped` 返回 0，与"成功执行"在 Task Scheduler 里**无法区分** |
| 4 | `autopilot.py:82-85` | `queue.json` 损坏时**静默用默认值覆写**用户配置 |
| 5 | `telemetry.py:60` | query **明文落盘**且**无保留期**（`_prune_logs` 不清 `query-log/`） |

### 中危

| # | 位置 | 问题 |
|---|---|---|
| 6 | `telemetry.py` 架构 | 埋点只挂 `cli.py:36`，而真实使用走 `store.search()` → **零证据≠零使用** |
| 7 | `autopilot.py:184` | stale-lock 判定用**本地时间**，跨设备/时钟漂移会误删他人锁 |
| 8 | `autopilot.py:150` | self_test 探针词硬编码 `("OneDrive","Agent")`，结论**依赖索引内容**而非真实使用 |
| 9 | `store.py:33` 注释 | `connect()` 会 `makedirs` + `CREATE TABLE` → `--db` 指向源树内会**在源树建库** |
| 10 | `tombstones.py:268-269` | 清理失败与"无命中"**都返回 0**，调用方无法区分 |
| 11 | `store.py:225-229` | `search()` 全量 `fetchall()` 到内存 + 纯 Python 打分 → 10 万条时 O(候选数×文本长度) |
| 12 | `store.py` schema | `fingerprint` **无 UNIQUE 约束** → 并发 ingest 会重复 |
| 13 | `logs.py:79` | `_prune_logs` 只清 `recall-*.log`，**不清 `query-log/`** → 隐私数据无限累积 |

### 环境相关（不是代码缺陷）

- `recall/tests/` **缺 `__init__.py`** → `python -m unittest discover -s recall/tests` 报 `ImportError`
- CI（`.github/workflows/ci.yml`）**只跑 `test_full.py`，不含 recall 的 64 个测试**
  → 绿灯 CI ≠ recall 被验证
- `memory_sync_app.py:3150-3156` 有 **PowerShell 命令注入面**（单引号包裹后插值 + `-ExecutionPolicy Bypass`）；
  同文件 `:3109` 的 robocopy 正确用列表形式 —— 同一文件两种写法

---

## 代码规模参考

| 组件 | 行数 | 状态 |
|---|---:|---|
| `recall/`（可复用） | 3,841 | ✅ 质量过硬，64/64 测试 |
| `agent_memory.py` | 7,897 | ⚠️ 巨型模块，不建议参考 |
| `memory_sync_app.py` | 3,376 | ⚠️ GUI+托盘+CLI 三合一 |
| `sync_writers.py` | 2,068 | 多格式写回 |
| `sync_engine.py` | 1,846 | 同步引擎 |

**若要重新做类似工具**：**不要**从 `agent_memory.py` 学结构。
可参考的只有 `recall/` 的分层（store / ingest / runner / cli / eval / tests 六层，依赖单向）。

---

## 归档位置说明

| 内容 | 位置 | 说明 |
|---|---|---|
| 代码与历史 | `origin/main` = `cd6f5eb` | 完整保留，98 提交 |
| gitdir | **工作区内 `.git/`** | 2026-10-06 已从 `C:/git-store/` 迁回，随 OneDrive 备份 |
| 评估报告 | `PROJECT_EVALUATION_2026-10-06.md` | 522 行，含完整证据链 |
| 终止原因 | `TOMBSTONE.md` | 终止依据 + 三个可迁移教训 |
| 记忆数据副本 | `%LOCALAPPDATA%\AgentMemorySystem\terminated-archive\` | 129M / 689 文件（**唯一副本，已移出 OneDrive**） |
| 清理前备份 | `%LOCALAPPDATA%\AgentMemorySystem\termination-backup-20261006\` | 清理前的 3 个 `MEMORY.md`、7 个构建产物目录、快捷方式、90M 运行副本 |
| 审计证据 | `%LOCALAPPDATA%\AgentMemorySystem\` | `shared.db` / `heartbeat.log` / `logs/` / `locks/` |

> ⚠️ **`terminated-archive/_archive_AgentMemory_20261006/.sync_backups/` 内含未脱敏个人信息**
> （`RAW_JSON` 块里有手机号等），且这些内容在当前 agent 记忆文件中**已不存在**（`RAW_JSON` 计数为 0）
> → 它是**唯一副本**，故未删除；但也已移出 OneDrive，不再有云端暴露面。
> 若将来决定清理，优先删 `.sync_backups/`（87M），
> 保留 `memory_shared.md`(2.2M) 与 `knowledge_brief/`(168K) 作纪念。

---

## 终止时的执行动作（供复盘）

| 动作 | 结果 |
|---|---|
| 删构建产物 7 目录 | 1235 文件 / 115M → 移至备份区（**非 rm**） |
| 归档记忆数据 | 129M → 移出 OneDrive 至 `%LOCALAPPDATA%` |
| 清agent 源文件注入节 | workbuddy 1791→1760、dsh 1842→1786、codepilot 1696→1663 行 |
| 停计划任务 | `RecallMemorySync` 已注销（终止时仍在失败） |
| gitdir 归位| 从 `C:/git-store/` 迁回工作区，消除 09-04 丢历史风险 |
| GitHub | `archived=true` + 终止描述 + 4 话题标签 |
| **永久删除** | **0** —— 全部为移动，可恢复 |

> **终止过程中未执行任何 `rm`**，所有移除均可从 `%LOCALAPPDATA%\AgentMemorySystem\`
> 下的两个备份区恢复。

---

*资产清单生成于 2026-10-06。移植前请先读`PROJECT_EVALUATION_2026-10-06.md` 第 6 章的评估局限声明。*
