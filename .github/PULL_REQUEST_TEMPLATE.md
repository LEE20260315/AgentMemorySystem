<!--
合并前必填。任何一项未勾选或 CI 未绿、未获批准，严禁合并至 main。
详见 docs/IMPLEMENTATION_PLAN.md 第 4 节与 .github/CONTRIBUTING.md。
-->

## 关联 TODO / 计划
- 计划文档：`docs/IMPLEMENTATION_PLAN.md`
- 对应 TODO 项：`#____` （可选：T1~T9 / P0；多个用逗号分隔）

## 变更摘要
<!-- 简要说明本次 PR 做了什么、为什么 -->

## 分支验证（合并前必填）
- [ ] 分支从最新 `main` 拉取，未直接提交到 `main`
- [ ] 本地运行 `python test_full.py` 全绿（通过数：____/____）
- [ ] 已补充/更新单元测试覆盖本次变更
- [ ] 行为变更已更新 `README.md` / `CHANGELOG.md` 的 `[Unreleased]`

## 完成标准对照（计划 §3.4）
- [ ] 功能实现且有测试覆盖
- [ ] `python test_full.py` 全绿
- [ ] CI 绿（GitHub Actions）
- [ ] 文档同步更新
- [ ] 版本号已对齐（`pyproject.toml` 与 `CHANGELOG.md`）

## 审查要求
- [ ] CI 通过（本 PR 的 Actions 全绿）
- [ ] 至少 1 位 reviewer 批准
- [ ] 无未解决冲突

> ⚠️ **在 CI 变绿且获得批准之前，严禁合并至 `main`。**
