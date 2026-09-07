# Contributing to AgentMemorySystem

Thanks for your interest in contributing! 🎉

> **本文件为项目硬性协作规范。**
> 违反「禁止直推 main」「必须分支 + review + CI」规则的贡献将被拒绝合并。
> 现行开发计划见根目录 `TODO.md`；历史计划存档于 `docs/archive/2026-08-implementation-plan.md`。

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

## 编写 Agent 适配器（插件式，v2.5.3+）

新增 Agent 支持**不需要改核心代码**。注册一个插件即可完成「发现 + 写回」，
`config.json`、`sync_writers.WRITER_REGISTRY`、`test_full.py` 三处都不用动。

### 两类插件

| 基类 | 作用 | 必须实现 |
|------|------|----------|
| `WriterPlugin` | 把共享记忆写回该 Agent 的记忆文件 | `write()` |
| `DetectorPlugin` | 发现该 Agent 是否安装、装在哪 | `detect()` |

两者都在 `agent_plugins.py`；子类必须声明 `agent_id` 类属性。

### 写回插件（`WriterPlugin`）

```python
# my_agent_plugin.py
from pathlib import Path
from agent_plugins import WriterPlugin, register_writer_plugin


class MyAgentWriter(WriterPlugin):
    agent_id = "myagent"                       # 必填，与 detect_agents 的 id 对齐
    aliases = ("myagent-appdata",)             # 可选，别名也指向本插件
    description = "MyAgent (~/.myagent/MEMORY.md)"

    def write(self, agent_id, target_path: Path, memories, backup_dir=None, **kwargs):
        """写回记忆，返回 sync_writers.WriteBackResult。"""
        ...

    def extract_target_info(self, agent_id, target_path, dry_run=False):
        """可选：目标完整性信号（供 reconcile 判孤儿）；默认 None = 不提供。"""
        return None


register_writer_plugin(MyAgentWriter)          # 文件末尾自行注册
```

### 检测插件（`DetectorPlugin`）

```python
from agent_plugins import DetectorPlugin, register_detector_plugin


class MyAgentDetector(DetectorPlugin):
    agent_id = "myagent"

    def detect(self, config) -> dict | None:
        """返回 None = 未安装；命中时至少给 path 与 memory_files：

        {"path": "C:/.../.myagent", "memory_files": ["MEMORY.md"]}

        detected_at / source 由框架补齐。
        """
        ...


register_detector_plugin(MyAgentDetector)
```

### 查找顺序与优先级

写回器解析（`sync_writers.get_writer`）依次为：
**插件注册表 → `WRITER_REGISTRY` → `GenericMarkdownWriter` 兜底**。
即外部插件优先级最高，可新增 Agent，也可以覆盖内置适配器（改行为/修 bug 用）。

检测结果则相反：**已有的检测结果优先**（内置 profile 或用户手动配置），
插件只在没有结果时追加，不会抢。单个插件抛异常只记 Warning，不影响其他插件。

### 启用外部插件目录（默认关闭）

安全起见，插件系统**默认不扫描、不执行任何外部代码**。要加载自己的插件：

```jsonc
// config.json
{
  "agent_plugins": { "dir": "C:/path/to/my/plugins" }
}
```

目录下每个非 `_` 开头的 `*.py` 会被 import，由文件自己调用
`register_writer_plugin()` / `register_detector_plugin()`。加载失败只记 Warning。

### 验证

```bash
python -c "import agent_plugins, json; print(json.dumps(agent_plugins.list_plugins(), indent=2, ensure_ascii=False))"
python test_full.py            # 全量回归必须全绿
```

提交时请在测试里至少覆盖：插件注册后可被 `get_writer()` 命中、检测结果被追加、
未配置 `agent_plugins.dir` 时行为与内置一致。

## 许可证

贡献即表示你同意以 MIT License 授权你的贡献。
