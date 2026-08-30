# 同步运行核查报告

- **核查对象**：2026-08-30 19:44:00 那轮同步（v2.3.0，EXE 打包版）
- **核查方式**：源码走查 + 数据库实证 + **生产库副本回放**（不动生产数据）
- **结论**：**流程跑通、无报错、无数据损坏**；核心数字大多数符合预期，但
  **「融合: 55 条新增共享」是虚假数字**，另有 2 个中等问题、3 个次要问题。

---

## 一、逐项核查表

| 日志项 | 判定 | 说明 |
|---|---|---|
| 发现 7 个 Agent | ✅ 正常 | trae / codex / codepilot / workbuddy / dsh / generic-.pi / generic-.trae-cn |
| 提取 0 条，跳过 N 条 | ✅ **正常** | 稳态下去重跳过；本地 md 未变动时不该有新提取 |
| 融合: 55 条新增共享 | ❌ **虚假** | 实际新增 **0 条**，见问题 1 |
| 写回: 0 条 | ✅ 正常 | SyncState 已记录全部 hash，无新增 |
| 跳过(去重): 0 条 | ✅ 正常 | 与写回 0 条自洽 |
| 库中共 52 / 132 / 127 / 133 / 134 条 | ✅ **正确** | = 134 − 该 Agent 自身贡献数（trae 82→52，workbuddy 7→127，codex 0→134） |
| 重建 51 条（库中共 134 条） | ⚠️ 符合策略但有害 | 128KB 上限装不下 134 条，`truncate_oldest` 静默丢弃旧记忆，见问题 3 |
| trae 走「增量追加」、其余走「重建」 | ✅ 符合逻辑 | trae 可见仅 52 条，文件未超限；其余 6 个超限降级全量重建 |
| 体积控制无输出 | ✅ 正常 | 文件均在限额内，无需动作 |
| 耗时 7.3 秒 / OneDrive 冲突 0 | ✅ 正常 | |
| 托盘最小化成功 | ✅ 正常 | 与 08-30 验收一致 |

---

## 二、问题 1（中等）：「55 条新增共享」是假的，实际新增 0 条

### 证据

把 `shared.db` 与 6 个 `agent_*/memories.db` **复制到临时目录**后回放 `full_sync()`，
连续三轮结果完全一致：

```
round 1 / 2 / 3:
  phase1 (agent->shared) synced = 55
  phase2 (shared->agent) synced = 0
  shared 行数: 134 → 134 → 134      （恒定）
  id 集合:     added=0  removed=0  timestamp_changed=0   （零变化）
```

**行数不变、id 不变、timestamp 不变，但每轮都报 55。** 这 55 次全是
`replace`（delete + insert 同一条），净效果为零。

### 根因

`_resolve_conflict` 的分支探针统计（两轮完全一致）：

| 判定 | 次数 | 成因 |
|---|---|---|
| `replace` ← timestamp newer | 20 | 同一内容在两侧挂了**不同 id** |
| `replace` ← access_count more | 35 | `get_memory()` 的副作用 |
| `keep_existing` | 775 | 正常跳过 |

**成因 A：同内容不同 id**
```
agent 侧: mem_20260702_extra   timestamp=2026-07-02
shared 侧: mem_20260629_extra   timestamp=2026-06-29   ← 内容完全相同
```
ID 匹配落空 → 退回 content 匹配 → 命中另一条同内容记录 → 判定"更新"→ replace。
replace 后又插成同一个 id，下一轮再来一遍。

**成因 B：`get_memory()` 自带写副作用**（`agent_memory.py:1789-1796`）
```python
row = cursor.fetchone()
...
UPDATE memories SET access_count = access_count + 1, last_accessed = datetime('now')
return self._row_to_entry(row)     # ← 返回的是 UPDATE 之前的 row
```
`_find_similar_in_shared` 每次调用它都给 `access_count` +1，而 `_resolve_conflict`
的最后一条判定正是 `if new.access_count > existing.access_count: return "replace"`。

### 危害

1. **报告失真**：稳定状态下永远显示 55，用户无法判断真有新记忆。
2. **写放大**：每轮对 55 行执行 delete+insert（含 FTS 索引维护）。
3. **access_count 语义污染**：已膨胀到 **761**，且每轮仍 +6（761→767 实测）。
   该字段本意是"记忆热度"，实际变成"被融合引擎查询的次数"。

---

## 三、问题 2（中等）：trae 与 generic-.trae-cn 是同一个 Agent

```
trae             -> C:\Users\Dong\.trae-cn\memory
generic-.trae-cn -> C:\Users\Dong\.trae-cn
```

同一个家目录被识别成两个 Agent，导致：

- 提取阶段扫两遍（42 文件 + 1 文件）
- 写回阶段向同一目录写两份 `memory_shared.md`（`.trae-cn\memory\` 与 `.trae-cn\`）
- `generic-.trae-cn` 没有 `memories.db`，不参与融合，属于"半残"登记

> 注：两者在融合层写的是不同目录（`agent_trae/` 与 `agent_generic-.trae-cn/`），
> **不会互相覆盖**。这点我起初判断有误，已修正。

---

## 四、问题 3（次要）：memory_shared.md 静默丢弃旧记忆

`_shared/volume_policy.json` 规定 `memory_shared_md` 上限 **128KB / 2000 行**，
超限动作为 `truncate_oldest`。

实测各文件均已顶格（122~132KB），**只装得下最新 51~55 条**，而库中有 127~134 条。

后果：**旧记忆虽然在 shared.db 里，却永远不会出现在 Agent 能读到的 md 文件中。**
日志只说"重建完成，51 条"，不提示丢了多少条，属于静默降级。

---

## 五、其余次要问题

| # | 问题 | 位置 |
|---|---|---|
| 4 | **dry-run 不保护融合阶段**：`run()` 只在 purge / FTS / 写回 / 体积控制处判断 `dry_run`，**提取与融合照常执行并真实写库** | `sync_engine.py:311,321,457,506` |
| 5 | `_resolve_conflict` 从不返回 `"merge"`，该分支（2358 行）为死代码 | `agent_memory.py:2520-2539` |
| 6 | `create_merger()` 不传 `embedding_service`，向量相似度去重档位**永不生效**，去重只剩"ID 全等 + content 全等" | `agent_memory.py:3003` |

---

## 六、修复建议（按性价比排序）

1. **拆分报告口径**：把 `total_merged` 拆成 `新增 / 更新 / 无变化` 三项；
   `replace` 不再计入"新增"。改动最小、收益最直接。
2. **给 `_find_similar_in_shared` 加内容哈希兜底**：以规范化后的 content 做键匹配，
   避免因 id 不同反复判定 replace。
3. **去掉 `get_memory()` 的 access_count 副作用**，或改用不带副作用的内部查询供融合使用。
4. **Agent 去重**：`generic-.trae-cn` 与 `trae` 同目录时只保留更具体的那个。
5. **dry-run 真正只读**：融合阶段也纳入 `dry_run` 判断。

---

## 七、复核方法（可复现）

```bash
# 在副本上回放融合，验证幂等性（不动生产数据）
# 1) 复制 shared.db + agent_*/memories.db 到临时目录
# 2) create_merger(shared_db_path=副本, agent_configs=副本).full_sync()
# 3) 连续跑 3 轮，比对行数 / id 集合 / synced 计数
# 环境：C:/Users/Dong/.workbuddy/binaries/python/envs/ams312/Scripts/python.exe
```
