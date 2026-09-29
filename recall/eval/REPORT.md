# Recall 评测报告（T04 基座 / T05 切分对照）

> 本报告由 `recall/eval/recall_eval.py` 生成。**唯一裁判**：冻结黄金集 `golden_pairs.yaml`（seed 固定，构建后不再变动）。
> 指标定义见脚本 docstring：recall@k、MRR、DOC（top-k 重复占用）。
> 只读：索引以 SQLite `mode=ro` 打开，评测不改动 `index.db`；
> `answer_phrase` 仅存在于黄金集，绝不进入索引 / 日志 / 遥测。

**T05 保留判据（三条同时成立才保留切分）**：

- `recall@5_new >= recall@5_base`
- `MRR_new >= MRR_base - 0.005`
- `DOC_new <= 0.8 * DOC_base`

---
## 基线 (pre-T05)

- 生成时间：2026-09-22 13:11:43
- 索引库：`C:\Users\MR.Dong\AppData\Local\recall-memory\index.db`
- 冻结黄金集：`C:\Users\MR.Dong\OneDrive\My Project\AgentMemorySystem\recall\eval\golden_pairs.yaml`

### 指标

| 指标 | 数值 |
|---|---|
| 样本数 M | 200 |
| k | 5 |
| recall@1 | 21.5% |
| recall@3 | 37.5% |
| recall@5 | 52.0% |
| MRR | 0.319 |
| DOC (top-k 重复占用) | 0.120 |
| DOC 有效查询数 (n>=2) | 200 |
| 零结果查询数 | 0 |

各来源 recall@5：

| 来源 | 样本数 | recall@k |
|---|---|---|
| codepilot | 34 | 70.6% |
| dsh | 30 | 73.3% |
| pi | 35 | 60.0% |
| trae | 66 | 25.8% |
| workbuddy | 35 | 57.1% |

---

## T05 段级切分：**负结果，已回滚**（2026-09-22）

按预登记保留判据（recall@5_new ≥ 52.0% **且** MRR_new ≥ 0.314 **且** DOC_new ≤ 0.096，三条同时成立才保留），段级切分**未达标 → 回滚**。真实 `index.db` 全程未改动（未重建）。

在**索引副本**上新建切分索引（同一冻结黄金集评测）：

| 方案 | 行数 | recall@5 | MRR | DOC | 保留? |
|---|---|---|---|---|---|
| **基线（`##` 整段，cap 4000）** | 589 | **52.0%** | **0.319** | 0.120 | — |
| V1 切分 target200 cap1200 + 标题前缀（原设计） | 1588 | 30.0% | 0.200 | 0.061 | ✗ |
| V2 同上，去正文标题前缀 | 1571 | 33.0% | 0.198 | 0.063 | ✗ |
| V3 切分 target700 cap1200 无前缀 | 1370 | 35.0% | 0.214 | 0.083 | ✗ |
| V4 切分 target400 cap600 无前缀 | 2234 | 30.0% | 0.175 | 0.054 | ✗ |

**oracle 存活**：198/200（line_safe 子集 162/163）——数据没丢，是**排序**问题。

**根因**：切分把每段 `##` 打成 2~3 倍行数，标题型查询在 top-5 里被同段兄弟 chunk 挤满，含 oracle 的目标 chunk 掉出 top-5 → recall@5/MRR 大幅回归。DOC 确实降了（符合预期），但不足以抵消召回损失；所有参数变体都过不了判据。**处置：回滚到 `##` 整段。** 切分实现与参数扫描脚本已存档（`%TEMP%\t05_archive`）供后续与排序改造协同复用。

