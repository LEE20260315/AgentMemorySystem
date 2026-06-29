# Sync Recursion v1.3.2 收官文档

> 本文档记录 v1.3.2 这一轮“同步后数据库递归增长 / 爆盘”的根因、修法、清理过程与最终验证结论。
> 读者：未来维护本项目的人，以及任何做“多端记忆提取 → 融合 → 写回”的人。

## 1. 最终结论

本轮问题不是 SQLite 自身损坏，也不是 OneDrive 单独造成的。
**真正根因是：同步写回产物被下次同步再次当成“原始记忆”提取，形成自我回灌。**

具体表现为三类同步产物被再次读入：

- Claude 写回子文件：`shared_from_agents.md`
- Trae 写回段：`## Shared Knowledge`
- Hermes / Generic Writer 写回标记：`[sync:mem_*]`

一旦这些内容再次进入 `extract_local_to_fused()`：

1. 被重新写进 `agent_*.db`
2. 再被 `shared.db` 融合
3. 再次写回各 Agent
4. 下一轮又被重新提取

于是数据库不断膨胀，最终出现“同步越来越慢 / DB 越来越大 / 看起来像爆盘”的结果。

## 2. 现场证据（修复前）

清理前抽样统计显示，污染占比已经非常高：

### OneDrive `AgentMemory/`

- `shared.db`: `total=1314`
  - `sync_marker=942`
  - `shared_knowledge=47`
  - `shared_from_agents=19`
- `agent_hermes/memories.db`: `total=3623`
  - `sync=3038`
- `agent_trae/memories.db`: `total=2598`
  - `sync=1296`
- `agent_codepilot/memories.db`: `total=2464`
  - `sync=1663`

### 项目内 `data/`

- `shared.db`: `total=1238`
  - `sync_marker=888`
  - `shared_knowledge=58`
  - `shared_from_agents=38`
- `agent_hermes/memories.db`: `total=3074`
  - `sync=2514`
- `agent_trae/memories.db`: `total=2617`
  - `sync=1325`
- `agent_codepilot/memories.db`: `total=2478`
  - `sync=1677`

说明数据库里的大头，已经不是“原始记忆”，而是“同步器自己写回去的话”。

## 3. 修复方案

本轮不是只加一层过滤，而是做了 **三层拦截 + 一层跨运行去重**。

### 3.1 扫描层：跳过同步文件

在 Agent 记忆文件扫描后，先过滤掉显然属于同步产物的文件：

- `shared_from_agents.md`

避免 Claude 的写回子文件在文件级直接重新进入提取链路。

### 3.2 解析层：跳过同步内容块

在 `LocalMemoryParser` 内增加同步产物识别与剥离：

- Hermes 分段时跳过带 `[sync:mem_*]` 的段
- Markdown 分块时跳过 `shared_from_agents.md` 自动索引块
- 通用文本预处理时移除 Trae 的 `## Shared Knowledge` 整段
- JSONL / USER / Markdown 在入条目前统一执行“同步内容判定”

也就是：**即使文件本身没被过滤，内容块仍会被第二层拦截。**

### 3.3 提取层：跳过同步内容

在 `extract_local_to_fused()` 中再次对内容做最终过滤，防止漏网之鱼进入融合层。

### 3.4 跨运行去重：同内容不再重复入库

即使是原始内容，只要之前已经提取过，也不应在下一轮再次灌入 `agent_*.db`。

因此在 `extract_local_to_fused()` 中新增：

- 对内容做标准化（统一换行、去首尾空白）
- 按标准化内容计算 hash
- 先查当前 `memories.db` 是否已有相同内容
- 当前轮内再用 `seen_hashes` 防重复

结果是：

- 同一轮不会重复写
- 下一轮再扫描同一批本地记忆，也只会 `skipped`
- 数据库不再随运行次数线性增长

## 4. 清理与重建过程

本轮没有尝试“在污染库上原地删脏数据”，而是直接重建，原因是更稳：

1. 先备份污染现场到：
   - `%TEMP%/AgentMemorySystem_cleanup_backup_<时间戳>/`
2. 删除两套数据根下的污染融合产物：
   - `shared.db*`
   - `agent_*/memories.db`
   - `agent_*/memory_private*.md`
   - `agent_*/memory_shared.md`
3. 重新执行同步，生成干净融合层
4. 再跑第二轮验证“无新增长”

涉及的两套数据根：

- `C:\Users\MR.Dong\OneDrive\AgentMemory`
- `C:\Users\MR.Dong\OneDrive\My Project\AgentMemorySystem\data`

## 5. 验证结果

### 5.1 OneDrive `AgentMemory/`

**第 1 轮：**

- 提取：32 条
- 融合：38 条
- 写回：51 条

**第 2 轮：**

- 提取：0 条
- 融合：0 条
- 写回：0 条
- 跳过：51 条
- 结果：`无操作（无新记忆需要同步）`

### 5.2 项目内 `data/`

**第 1 轮：**

- 提取：20 条
- 融合：50 条
- 写回：51 条

**第 2 轮：**

- 提取：0 条
- 融合：0 条
- 写回：0 条
- 跳过：51 条
- 结果：`无操作（无新记忆需要同步）`

### 5.3 清理后库内抽样

OneDrive `AgentMemory/` 清理后：

- `shared.db total=17`
  - `sync=0`
  - `shared_from_agents=0`
- `agent_codebuddy/memories.db total=17`
  - `sync=0`
- `agent_hermes/memories.db total=17`
  - `sync=0`
- `agent_trae/memories.db total=17`
  - `sync=0`

说明最危险的递归标记已经清空，且不会在第二轮重新长回来。

## 6. 为什么这次修法有效

这次有效，不是因为“加了更多 if”，而是因为把问题切在了**正确边界**：

- 写回产物不应再被当作输入
- 已提取过的原始内容不应随运行次数重复入库

前者解决“递归”，后者解决“线性重复”。

两个问题同时解决后，同步从“每跑一次更大”变成“第一次收敛、第二次静默”。

## 7. 未来避免

1. **所有 Writer 输出都应带可识别标记**，并在 Parser / Extractor 明确忽略
2. **输入边界和输出边界必须分开看**：能写回，不代表下次还能读回
3. **同步验证必须至少跑两轮**：
   - 第一轮看是否成功生成目标
   - 第二轮看是否保持 0 增量
4. **数据库体积异常时，先查内容构成**，不要先怀疑 SQLite 本身
5. **出现 “Shared Knowledge / shared_from_agents / [sync:*]” 大量进入共享库时，基本就是递归提取了**

## 8. 收官清单

- [x] 扫描层过滤 `shared_from_agents.md`
- [x] 解析层过滤 `Shared Knowledge` / `[sync:mem_*]` / 自动索引块
- [x] 提取层增加跨运行内容去重
- [x] 备份污染数据库
- [x] 重建 OneDrive `AgentMemory/` 融合层
- [x] 重建项目内 `data/` 融合层
- [x] 两套数据根都完成“双跑验证”
- [x] 第二轮确认 `0 提取 / 0 融合 / 0 写回`

—— 完 ——
