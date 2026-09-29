# Recall — 召回优先的 Agent 记忆检索（可运行版）

> 基于「第一性原理」重建：记忆的唯一价值发生在**召回那一刻**。因此「只读摄取 → 本地索引 → 召回」，砍掉旧项目的写回/融合/墓碑/冲突处理等 70% 复杂度。
>
> 铁律：本程序**绝不修改任何 Agent 的记忆源文件**，只读；索引与日志只放本地 `%LOCALAPPDATA%`，绝不落 OneDrive。

---

## 1. 三种运行形态（按推荐顺序）

| 形态 | 文件 | 说明 |
|------|------|------|
| **免安装程序** | `recall.exe` + 同目录 `_internal\` | 免装 Python；**onedir 打包**（一个 exe + 一个 `_internal\` 依赖目录，整目录一起拷） |
| **源码运行** | `recall\` + Python 3.10+ | 开发/调试 |
| **后台守护** | `recall.exe watch` | 定时循环同步（观察期主推） |

> 部署位置：`%LOCALAPPDATA%\AgentMemorySystem\Run\recall\`（`recall.exe` + `_internal\` + `recall_config.json`）。

## 2. 命令速查

```
recall.exe status              # 查看索引概况
recall.exe config              # 显示本机实际生效的配置 + 各来源是否存在（跨设备排障）
recall.exe ingest              # 跑一次同步（摄取 + 建索引 + 写日志）
recall.exe recall "关键词"      # 召回 top-5（中文友好；同内容多来源合并，片段锚定命中词）
recall.exe watch --once        # 单次同步后退出（供计划任务调用）
recall.exe watch               # 守护循环：按配置间隔持续同步（Ctrl+C 停止）
recall.exe logs --tail 30      # 查看今日日志尾部
recall.exe stats --days 14     # 查询遥测统计（G-E4：每日查询数 / 零查询天数 / 判定行）
recall.exe autopilot             # 执行一轮受控自动任务
recall.exe autopilot --status    # 查看队列、状态和停止开关
recall.exe autopilot --stop      # 暂停自动循环
recall.exe autopilot --resume    # 恢复自动循环
```

> **自动循环说明**：计划任务 `RecallMemorySync` 每 3 小时执行 `autopilot --max-tasks 4`。它只运行 `sync`、`health`、`self_test`、`eval` 四类白名单任务；队列和状态写入本地 `%LOCALAPPDATA%\recall-memory\autopilot`，不写 Agent 记忆源。完整操作见 `AUTOPILOT.md`。

> **召回输出说明**：结果按「同内容多来源合并」呈现——同一段记忆被多个 Agent 各存了一份时，top-5 给的是 5 个**不同内容**（而不是 5 份拷贝）；同内容会被标 `（合并 N 源）` 并列出来源。片段**以命中词为中心**截取（而不是只取正文开头），并在行尾标注 `（命中: …）`。
> 示例：
> ```
> 1. [pi] Lessons Learned  （合并 3 源）
>       C:\Users\...\.pi\MEMORY.md
>       来源: pi, dsh, trae
>       …koa-connect wrapper caused subtle ctx.state data loss…   （命中: 托盘）
> ```
> 合并**只发生在查询/显示时**，索引始终保留每个来源的原始条目（provenance 不丢）。

## 3. 「跑几天观察」的两种方式

**A. 守护进程（简单、实测可行）**
- 双击 `recall_start.vbs` → 后台静默启动 `watch`，不占窗口。
- 停止：双击 `recall_stop.bat`。
- 缺点：机器重启后需重新启动。

**B. Windows 计划任务（扛重启、最靠谱）**
- 双击 `install_task.bat` → 注册任务 `RecallMemorySync`，**每 3 小时**自动跑一次 `watch --once` 并写日志；脚本已写入正确的电源设置（见下）。
- 卸载：双击 `remove_task.bat`。
- 改频率：编辑 `install_task.bat` 里的 `New-TimeSpan -Hours 3`。

> ⚠️ **计划任务默认只在「插电」时才会准时跑（重要实情）**
> 旧的 `RecallMemorySync` 任务 XML 带了 `DisallowStartIfOnBatteries=true` / `StopIfGoingOnBatteries=true`，
> 在电池供电或休眠时任务被压制 → 漏跑（09-19 出勤仅 3/8、09-20 仅 6/8），
> 而因为 `StartWhenAvailable=true` 把"未按时启动"不算 missed，`NumberOfMissedRuns` 仍报 0 —— **从任务状态完全看不出漏跑**。
> `install_task.bat` 现默认写入**电源安全**设置：`DisallowStartIfOnBatteries=false`、`StopIfGoingOnBatteries=false`、`StartWhenAvailable=true`、`WakeToRun=false`
> （即插电池也启动、拔电池不停止、错过会补跑、不唤醒睡眠机器）。若你确实想**只在插电时运行**，把前两项改回 `true` 即可。
> - **修复已注册任务**：运行 `tools\fix_task_power.ps1`（双击或用 `powershell -NoProfile -ExecutionPolicy Bypass -File tools\fix_task_power.ps1`）。
>   幂等、可反复执行；改前会把当前 XML 备份到 `%TEMP%\recall-task-backup\`，改后读回 XML 并打印"改动前 / 改动后"的关键设置做自证。

> 推荐组合：**B（长期保底）+ 需要时 A**。

## 4. 跨设备部署

程序本体放在 OneDrive（或任意同步目录），多机共享：

1. **整目录拷贝**：`recall.exe` + 同目录 `_internal\` + `recall_config.json`（**onedir 打包**：exe 依赖 `_internal\`，必须一起拷；缺了会启动即报错）。无需装 Python、无需装任何依赖。
2. **双击即用**：所有启动器（`recall_run.bat` / `recall_watch.bat` / `recall_start.vbs` / `install_task.bat`）都**优先用 `dist\recall.exe`**，没有 Python 也能跑；Python 只是回退。
3. **首次在另一台电脑上（重要）**：
   - 双击 exe 或 `.bat` 时 Windows 可能弹 **SmartScreen**「Windows 已保护你的电脑」——点 **更多信息 → 仍要运行**（程序未签名，属正常提示，只出现一次）。
   - 先跑一次 `recall.exe config`：看「记忆来源」列表里哪些 ✓ 存在、哪些 ✗。✗ 的来源会被自动跳过，不会报错。
   - 这台电脑上没有的 Agent（比如另一台机器不用 trae），要么在 `recall_config.json` 里删掉那行，要么留着（✗ 自动跳过，零成本）。
4. **索引/日志是本地的**（`%LOCALAPPDATA%\recall-memory\`），不跨机同步；换机后首次 `ingest` 自动重建，无需传数据库。
5. 可移植配置 `recall_config.json` 放在 **exe 旁边**（已随 OneDrive 同步），改它对所有设备生效。
6. 若本机 `%LOCALAPPDATA%` 不可写（极少见），程序自动降级到 `%TEMP%\recall-memory`，绝不崩。

## 5. 配置 `recall_config.json`（可选，缺省用内置默认）

```jsonc
{
  "sync_interval_minutes": 180,          // watch 循环间隔
  "sources": [                            // 只读记忆来源（覆盖默认）
    { "agent": "trae",      "root": "~/.trae-cn/memory", "glob": "**/*.md" },
    { "agent": "dsh",       "root": "~/.dsh",            "glob": "MEMORY.md" },
    { "agent": "pi",        "root": "~/.pi",             "glob": "MEMORY.md" },
    { "agent": "workbuddy", "root": "~/.workbuddy",      "glob": "MEMORY.md" },
    { "agent": "codepilot", "root": "~/.codepilot",      "glob": "MEMORY.md" },
    { "agent": "claude",    "root": "~/.claude",         "glob": "MEMORY.md" }
  ]
}
```

> **`glob` 现在真正生效**（T02）：含路径分隔符的模式按"相对 root 的路径"匹配，`**/` 匹配零个或多个目录层级
> （所以 `**/*.md` 也能匹配根目录文件）；不含分隔符的模式（如 `MEMORY.md`）只按**文件名**匹配，
> 因此能把 `~/.workbuddy`（raw ≈1.4 万个 md，经 `EXCLUDE_DIRS` 剪枝后 ≈7.4 千）约束到只取 `MEMORY.md`。`~` 会被展开；来源只读。
> 每条来源默认有 3000 条目的上限（`config.MAX_ENTRIES_PER_SOURCE`），超限会在同步汇总里标 `truncated=`。

## 6. 日志与健康分级

- 位置：`%LOCALAPPDATA%\recall-memory\logs\recall-YYYYMMDD.log`（每日一档，保留 14 天自动清理）
- 每行格式：`YYYY-MM-DD HH:MM:SS [LEVEL] message`。
- **健康分级（G-B10）** —— 三类状态不再都写成"OK"（以前 `errors>0` / `files=0` 也写 `[SYNC OK]`，看不出来）：

| 日志文本 | 级别 | 含义 | `watch --once` 退出码 |
|---|---|---|---|
| `[SYNC OK] files=.. entries=.. (agent=..) elapsed=..s` | INFO | 真正干净地跑完 | `0` |
| `[SYNC DEGRADED] files=0 ...` | WARNING | **一个文件都没走到**（来源全不存在 / 配置坏了）→ "什么都没干" | `3` |
| `[SYNC DEGRADED] errors=N ...` | WARNING | 跑到了文件，但有 N 个读取失败 → "带伤干活" | `2` |
| `[SYNC FAIL] ...` | ERROR | 同步本身抛异常（如索引库被锁） | `1` |

- 退出码经 `watch --once → cli.main → raise SystemExit` 传到 **Windows 计划任务的 `LastTaskResult`**，
  所以在"任务计划程序"里也能一眼看出红/绿：**只有 `0` 才是正常**。
- 这就是「跑几天观察」的度量依据：看每天新增条目数、是否稳定、有无 `DEGRADED` / `FAIL`。

### 6.1 召回遥测 `query-log/query-YYYYMMDD.jsonl`（G-E4）

- 位置：`%LOCALAPPDATA%\recall-memory\query-log\query-YYYYMMDD.jsonl`。每行一条 JSON：
  `{"ts": …, "query": …, "k": …, "n": …, "keys": "<12位内容键哈希>"}`（一次 `recall` 一行）。
- 查看：`recall.exe stats --days 14`（每日查询数 / 有查询天数 / 零查询天数 / **G-E4 判定行**）。
- 这是**本程序唯一允许的写副作用**（只读边界 R7）：(a) 不参与检索（`ingest`/`store`/`search` 绝不读它）；
  (b) 写失败被静默吞掉，绝不影响召回；(c) 只写 `%LOCALAPPDATA%`，且**从不写记忆正文**——只记 时间/查询词/k/结果数/短哈希。
- 用途：回答 G-E4「你什么时候真的会去取记忆」，是决定要不要做注入链（Phase 4）的证据；**默认开**，14 天零查询即触发止损冻结。
- 只读边界据此精确化：**摄取与查询都不会改动源文件，也不改动 `index.db`**（遥测日志是唯一例外，且不含正文）。

## 7. 观察期验收（Phase 0 价值门）

每次面对真实任务干一件事：先 `recall.exe recall "任务关键词"`，看 top-5 里有没有**「我本会忘、但确实有用」**的记忆。

- 通过线（开工前定死）：≥50% 次命中，且 ≥2 次**实质改变结果**。
- 达成 → 进 Phase 1（扩展 jsonl 摄取、语义检索、简报）。
- 未达成 → 停，零沉没成本。

---

## 打包（仅需开发/重打包时）

```
build_exe.bat    # 需先 python -m pip install pyinstaller；产物 dist\recall.exe
```

## 日志已确认的工作

- 中文召回正确（子串多词打分）；EXE 中文输出不乱码（强 UTF-8）。
- EXE 从任意工作目录（含 System32）可自举、可读 exe 旁配置。
- `watch --once` 写日志；守护循环与计划任务注册均验证可用。