# Recall Autopilot

Recall 现在由受控的本地 autopilot 负责定时运行，不执行任意命令，也不写入任何 Agent 记忆源文件。

## 自动执行内容

| 任务 | 默认间隔 | 行为 |
|---|---:|---|
| `sync` | 3 小时 | 只读扫描来源并更新 `%LOCALAPPDATA%\recall-memory\index.db` |
| `health` | 1 小时 | 检查索引条目数和来源路径覆盖 |
| `self_test` | 6 小时 | 本地只读检索探针；不写查询遥测 |
| `eval` | 24 小时 | 只读运行冻结黄金集评测 |

每次计划任务最多执行 4 个白名单任务。队列、运行状态、锁和停止开关均位于：

```text
%LOCALAPPDATA%\recall-memory\autopilot\
├─ queue.json
├─ state.json
├─ run.lock
└─ stop.flag       # 存在即暂停，不删除任何数据
```

## 控制命令

在项目根目录执行：

```powershell
python -m recall.cli autopilot --status
python -m recall.cli autopilot --max-tasks 4
python -m recall.cli autopilot --stop
python -m recall.cli autopilot --resume
```

已打包运行位使用：

```powershell
%LOCALAPPDATA%\AgentMemorySystem\Run\recall\recall.exe autopilot --status
```

## Windows 计划任务

任务名：`RecallMemorySync`

动作：

```text
recall.exe autopilot --max-tasks 4
```

频率：每 3 小时；电池供电可运行；错过的触发点会补跑；不唤醒睡眠机器。

## 安全边界

- 队列只接受 `sync`、`health`、`self_test`、`eval` 四类任务。
- 不支持 shell、PowerShell、任意 Python 或网络发布任务。
- 单轮有任务预算；单个任务失败不会无限重试，也不会阻断其它任务。
- 召回查询的真实任务价值不能由 autopilot 伪造；`Phase0_命中日志.md` 只记录实际任务中人工使用并产生影响的结果。
- 使用 `recall_stop.bat` 或 `autopilot --stop` 后，计划任务仍可触发，但只会记录暂停状态，不执行任务。
