# Contributing to AgentMemorySystem

Thanks for your interest in contributing! 🎉

> **本文件为项目硬性协作规范，与 `docs/IMPLEMENTATION_PLAN.md` 第 4 节一致。**
> 违反「禁止直推 main」「必须分支 + review + CI」规则的贡献将被拒绝合并。

## 核心红线（必须遵守）

1. **`main` 分支受保护，禁止任何直接提交或推送。** 所有改动必须在独立功能分支完成。
2. **合并前必须满足**：本地测试全绿 + CI 绿 + 至少 1 位 reviewer 批准。
3. **禁止**在 PR 审查/CI 通过前自行合并到 `main`。

## 分支策略

| 类型 | 命名 | 示例 |
|------|------|------|
| 新功能 | `feature/<slug>` | `feature/volume-limit-smart` |
| 缺陷修复 | `fix/<slug>` | `fix/conflict-merge` |
| 杂项/文档 | `chore/<slug>` | `chore/ci-workflow` |

- 分支从最新 `main` 拉取：`git switch -c feature/<slug> main`
- 长期分支定期同步 `main`（`rebase` 或 `merge`）以避免偏离
- 合并后删除分支（历史由 merge commit 保留）

## 标准开发流程（7 步）

1. 从 `main` 创建分支：`git switch -c feature/<slug> main`
2. 编码，并**为每次变更补充/更新单元测试**（核心模块见 `test_full.py`）
3. 本地运行测试并**确保全绿**：`python test_full.py`
4. 原子提交，信息关联 TODO 项，如 `feat: T3 体积保护智能保留 (#T3)`
5. 推送：`git push -u origin feature/<slug>`
6. 开 Pull Request，填写 PR 模板（关联 TODO、测试证据、完成标准勾选）
7. **CI 绿 + 至少 1 审批** → 合并（squash 或 rebase）→ 删除分支

## 开发环境

```bash
# 克隆（自己的 fork 或本仓库）
git clone https://github.com/LEE20260315/AgentMemorySystem.git
cd AgentMemorySystem

# 安装依赖（GUI 为可选）
pip install -r requirements.txt        # 核心 + GUI 依赖
# 仅核心：pip install pyyaml
# 语义搜索（可选，约 500MB）：pip install ".[vector]"

# 运行测试（唯一权威测试入口）
python test_full.py                    # 全量
python test_full.py --module safe_io   # 单模块
```

> ⚠️ 历史文档曾引用 `python test_memory.py`，该文件已在 v2.0.4 删除，请勿使用。

## 代码风格

- 遵循 [PEP 8](https://pep8.org/)
- 公共函数使用类型注解（type hints）
- 公共函数/类写 docstring
- 函数保持小而聚焦
- 提交信息语义化（feat / fix / chore / docs / test）

## 报告缺陷

请使用 [bug report 模板](.github/ISSUE_TEMPLATE/bug_report.md)。

## 建议功能

请使用 [feature request 模板](.github/ISSUE_TEMPLATE/feature_request.md)。

## 编写 Agent 适配器（插件式，进行中）

T4 计划将 `WRITER_REGISTRY` / `detect_agents` 重构为可插拔架构。完成前，新增 Agent 支持请：
1. 在 `config.json` 的 `agent_detection` 增加候选路径与特征文件
2. 在 `sync_writers.py` 的 `WRITER_REGISTRY` 注册写回器
3. 在 `test_full.py` 补充检测/写回测试

## 许可证

贡献即表示你同意以 MIT License 授权你的贡献。
