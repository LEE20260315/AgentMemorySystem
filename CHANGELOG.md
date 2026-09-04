# Changelog

本项目的所有重要变更都记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased] - 2026-09-04

本轮主题：**全量审计后的仓库治理**。不改动任何同步逻辑，只让仓库结构、文档与元数据回到
"与代码事实一致"的状态。

### Changed（整理）

- 历史文档归档：`docs/IMPLEMENTATION_PLAN.md` → `docs/archive/2026-08-implementation-plan.md`、
  `docs/sync_audit_2026-08-31.md` → `docs/archive/2026-08-31-sync-audit.md`，均补归档说明头
  （逐项标注已修复/未关闭状态）；现行计划唯一权威为根目录 `TODO.md`
- `TODO.md` 重写为可落地路线图（P0/P1/P2，每项含现状/做法/验收标准/工作量），
  已完成记录压缩为版本索引；原 9/2 排查托盘问题的一次性探针脚本及其输出
  （`_*.py`、`co_mta` 等 18 个文件）移出仓库视野（本地归档 `archive/`，本就未被 git 跟踪，
  结论已沉淀于 v2.4.1 CHANGELOG）
- README（中/英）：新增 CI 徽章；目录树补齐 `tombstones.py`、`setup_agent.py`、`TODO.md`，
  更新 `docs/`、`tools/` 说明

### Fixed（元数据与门禁）

- **修复时间炸弹测试**：`test_detect_agents_cache_profiles_hash_invalidation` 场景 2 的缓存
  时间戳硬编码 `2026-08-17`，叠加 24h TTL，自 2026-08-18 起必然失败，长期被误记为
  「历史失败」并随多个版本带过；改为动态生成时间戳（场景 1 陈旧 / 场景 2 新鲜），
  全量测试恢复 **324/324 全绿**
- **修复 Windows 管道输出的中文编码崩溃**：stdout 为管道时 Python 回落 ANSI 代码页
  （cp1252），`test_full.py` 打印中文标题直接 `UnicodeEncodeError`（GitHub Actions
  Windows runner 实测如此）；测试脚本启动时对 stdout/stderr 强制 UTF-8 + 容错，
  CI 侧同时设 `PYTHONUTF8=1` 双保险
- **CI 门禁平台修正**：测试套件为 Windows 优先（原生托盘 / 命名互斥量 / Windows 路径语义），
  ubuntu 上有 5 条平台相关用例必然失败，导致 CI 自建立以来全红、门禁形同虚设；
  改为 windows-latest 门禁（Python 3.10/3.11/3.12）+ ubuntu 观察项（`continue-on-error` 不阻断）
- CI 依赖与 `requirements.txt` 对齐：移除 v2.0 起已不需要的 `pystray`，补上真正必需的 `pyyaml`
- 补交 `tools/__init__.py`：v2.1.2 起 tools 即为正式包且被 `agent_memory.py` /
  `sync_engine.py` / `test_full.py` 静态导入，但该文件从未提交进仓库
- `pyproject.toml` 入口修正：`agent-memory = "agent_memory:main"` 指向不存在的函数，
  改为 `memory_cli:main`
- `.github/CONTRIBUTING.md` 与 PR 模板中指向 `docs/IMPLEMENTATION_PLAN.md` 的失效引用，
  改指归档位置与 `TODO.md`

## [v2.4.1] - 2026-09-03

本轮主题：**代码审计收口 + dry-run 只读闭环**。不新增同步功能，清理"写了一半的开关"、
"永远走不到的分支"和"接不上的参数"，顺带把 9/2 的托盘自愈工作正式纳入版本。

### Fixed（`--dry-run` 仍会写盘）

v2.4.0 声称 dry-run 真正只读，实测仍有四处漏网（提取/融合阶段不受 dry_run 保护）：

- `SyncState.save()`：dry-run 下照写 `.sync_state.json`（含 LOCALAPPDATA 回退路径）
- `SyncState._record_tombstones()`：dry-run 下照记墓碑（跨设备可见，污染更久）
- `TraeMemoryWriter.extract_target_info()`：污染检测触发 `_repair_polluted_file()`
  重建 `user_profile.md` —— 提取阶段在写回守卫**之前**执行，写盘无法被拦住
- `SyncEngine.run()` 中的 `detect_agents()`：dry-run 下照写 `.detected_agents.json`

改动：`SyncState` 增加 `dry_run` 构造参数（保存/墓碑在触碰磁盘前返回）；
5 处 `extract_target_info` 增加 `dry_run` 形参（Trae 自愈在 dry-run 下只上报不修复，
返回 `repair_skipped_dry_run`）；`detect_agents(write_cache=not dry_run)`。

### Fixed（体积档位用错）

`_enforce_write_volume_limit` 硬编码读 `memory_private_md` 档位，Claude 的
`shared_from_agents.md` 属于共享池却按 private 的 256KB 放行，超过
`memory_shared_md` 128KB 上限也不截断。新增 `policy_key` 形参，Claude 写回显式传
`policy_key="memory_shared_md"`。

### Fixed（向量去重永远不生效）

`create_merger()` 工厂方法没有 `embedding_service` 形参，调用方想传也传不了，
`MemoryMerger` 的语义相似度去重档位（三处判定）永不生效，去重只剩
「id 全等 / 内容全等 / 归一化全等」。补齐形参并透传，默认仍为 `None`（保持现行为）。

### Fixed（`get_cache_stats()` 命中率恒为 0）

`SearchOptimizer` 从未累计 `_cache_hits` / `_cache_misses`，`hit_rate` 恒为 0。
补齐计数，`clear_cache()` 一并归零。

### Changed（死代码清理）

- 删除 `_resolve_conflict` 调用处的 `elif conflict_result == "merge":` 分支
  与 `_merge_memories()` 方法 —— `_resolve_conflict` 从不返回 `"merge"`。
  merge 冲突策略保留在 TODO #5，实现时以真实可达的调用路径补回。
- `config.json` 移除从未被任何代码读取的 `agents_md_standard` 整节，以及
  `sync_tool.auto_interval_days`（真实生效的是 `sync_settings.json` 的
  `auto_interval_hours`）。

### Added（托盘注册自愈，原 9/2 工作）

- `_retry_tray_add()`：托盘注册失败不再立即弹窗，改为每 1s 复用现有
  hwnd/hIcon 重新 `NIM_ADD`，最多 30 次；成功后自动隐藏主窗口
- `_show_tray_failed_ui()`：重试耗尽才提示，并按 EXE 是否在 OneDrive 目录
  给出不同原因与解决方案
- `_finish_minimize_to_tray()`：成功路径收口（首次成功与自愈成功共用）
- `_load_or_create_tray_guid()`：托盘 GUID 由硬编码改为每机持久化随机值
  （`%LOCALAPPDATA%\AgentMemorySystem\tray_guid`），多安装副本不再共用同一身份、
  互相顶掉 Win11「是否显示在任务栏」偏好；读取/写入失败回退内置固定 GUID

### Tests

新增 4 条回归测试（dry-run 状态与墓碑、embedding_service 透传、体积档位可选、
缓存命中率），全量 **322/324** 通过（2 条为既有历史失败：detect_agents 缓存用例，
与本版无关）。

---

## [v2.4.0] - 2026-08-31

本轮主题：**同步报告的保真度**。不新增功能，只修"系统说的话与它做的事不一致"。

### Fixed（融合报告虚报「新增共享」，实测新增为 0）

- **现象**：稳态下每轮同步恒定报告「融合: 55 条新增共享」，而实际上
  shared.db 行数、id 集合、timestamp **三轮零变化**——新增数为 0。
- **根因一（统计口径）**：`run()` 把 `full_sync()` 返回的**全部 14 个结果**
  （7 个 `*_to_shared` + 7 个 `shared_to_*`）的 `synced` 一并累加进
  `total_merged`，且不区分 insert 与 replace。改后只统计第一阶段
  （Agent → 共享库）的 `inserted`，第二阶段的回流不再计入"新增共享"。
- **根因二（replace churn）**：`_resolve_conflict` 对同一批行每轮都判定
  `replace`（delete + insert 同一条，净效果为零）。两个来源：
  - `timestamp newer`（20 次）：同一内容在两侧登记为**不同 id**
    （如 `mem_20260702_extra` vs `mem_20260629_extra`），id 精确匹配落空后
    退回 content 匹配，命中的却是"另一条"同内容记录，被当成更新。
    → 新增 **内容归一化兜底**（`_normalize_memory_content`，只消除 CRLF/
    行尾空白/连续空行，不做大小写折叠以免误合并），并在
    `_resolve_conflict` 前置判定：**归一化内容相同即视为无变化**，
    仅当新版本置信度更高才改写。
  - `access_count more`（35 次）：**`get_memory()` 有写副作用**——每次查询
    都 `access_count + 1`，而它正是 `_resolve_conflict` 的最后一条判定依据，
    形成"读操作驱动写决策"的循环。
    → `get_memory()` 新增 `track_access` 参数（默认 `True` 保持业务语义），
    融合比对路径统一传 `False`。

### Fixed（`get_memory` 副作用污染 access_count）

- `access_count` 本意是"记忆热度"，实际变成"被融合引擎查询的次数"，
  已膨胀到 **755** 且每轮再 +6。修复后该值冻结（实测三轮均为 755）。

### Fixed（Agent 重复登记）

- `C:\Users\Dong\.trae-cn\memory`（profile 命中 → `trae`）与
  `C:\Users\Dong\.trae-cn`（通用发现 → `generic-.trae-cn`）被当成两个 Agent，
  同一份记忆扫描两遍、写回两遍，且 `generic-.trae-cn` 没有独立 memories.db
  （不参与融合，属"半残"登记）。
- 新增 `_is_path_related()`：候选路径与已登记路径**存在父子目录关系**即判重复；
  已登记的更具体时跳过其祖先，反之跳过其后代。

### Fixed（`--dry-run` 不保护融合阶段）

- 旧实现只有写回 / 体积控制 / purge / FTS 判断 `dry_run`，**提取与融合照常
  执行并真实写库**（shared.db + `agent_*/memories.db`），所谓"试运行"一直在
  污染数据。现提取、融合、墓碑清理全部纳入 dry-run 保护。
- ⚠️ 需要验证融合效果时，请改用**复制 DB 到临时目录再回放**的办法。

### Changed（报告口径）

- 同步报告 `融合` 行改为 `融合: X 条新增共享, Y 条更新`。
- 新增一行提示：当 `新增=0 且 更新>0` 时明确说明"本次无新增，N 条为既有
  记忆的覆盖更新"，避免再被数字误导。
- `MemoryMerger.sync_agent_to_shared` 的返回值新增 `inserted` / `updated`
  两个细分项；`synced` 保留为两者之和，兼容既有调用方。

### 已知问题（v2.4.0 未处理，下轮评估）

1. **`memory_shared.md` 静默截断**：`_shared/volume_policy.json` 限制
   128KB / `truncate_oldest`，实测只装得下最新 51~55 条，而库中有 127~134 条。
   **旧记忆虽然在 shared.db 里，却永远不会出现在 Agent 能读到的 md 文件中**，
   且日志不提示丢弃数量。
2. **`_resolve_conflict` 从不返回 `"merge"`**，该分支为死代码。
3. **`create_merger()` 未传 `embedding_service`**，向量相似度去重档位永不生效，
   去重仍只依赖"id 全等 / content 全等 / 归一化全等"三档。
4. `access_count` 历史积累的 755 等虚高值未清理（已停止增长，不影响功能）。

### 升级注意事项

- **无需数据迁移**，schema 未变更。
- 升级后**首轮同步的「新增」可能比往常低**——这是修复后的真值，不是漏同步。
- 若升级后报告仍出现「更新」持续非 0，说明还有 replace churn 未被覆盖，
  可用「复制 DB 到临时目录回放三轮」的方式复现（三轮结果应完全一致）。

---

## [v2.3.0] - 2026-08-30

### Added（墓碑机制：防已删记忆跨设备复活，P1-3）

- **问题**：用户/Agent 直接编辑记忆文件删除一条已同步记忆后，`reconcile`
  （正常模式）把该内容的 hash 当孤儿从 SyncState 清除，下一轮写回时
  `known_hashes` 过滤失效，shared.db 中仍存在的同内容条目被重新写回——
  **删除被"复活"**。根因是删除从不传播（同步两个方向均为纯增量）。
- **方案**（新增 `tombstones.py`，墓碑库存数据根 `.tombstones.json`，
  OneDrive 同步、跨设备生效）：
  1. **产生**：reconcile 正常模式检测到 vanish → 按 content_hash 记墓碑。
     双安全阀：保守模式（文件不存在 / legacy-only）绝不墓碑化；24h 宽限期
     内的 vanish（刚写入即消失，疑似 pending/写失败）不墓碑化；单轮 vanish
     超过 50 条视为文件级重置事件，整体跳过。
  2. **过滤**：三处复活路径全堵——增量写回（`force_refresh` 也不豁免，
     墓碑语义是"删除"与去重 state 无关）、`memory_shared.md` 重建、DB 级
     融合 `sync_shared_to_agent`。
  3. **治本**：每轮同步在融合后、写回前，从 shared.db 删除命中行（含 FTS
     索引清理；`IN (...)` 分块 500，免疫 SQLite 变量上限）。
- **工程性质**：幂等 add；FileLock + 原子写；任何环节失败不阻断主同步且
  如实返回 0；`refresh()` 每轮丢弃缓存重读盘（GUI 常驻进程跨设备墓碑可见）；
  `prune(keep_days)` 预留保留策略（默认永久，每条约 100 字节）。

### Added（日志保留策略 v2.3.0，P1-2）

- 轮转文件名带时间戳（`<stem>.<YYYYMMDD-HHMMSS>.log`）**永不覆盖**；
  `(keep_count=3, keep_days=7)` 双维度裁剪，活跃文件绝不动。
- 写失败计数不再静默：下次成功时补记 `[WARN] 此前有 N 条日志未落盘`。
- 启动时自动维护（`_startup_log_maintenance`）；新增 `tools/log_retention.py`
  扫描 local/data/project 三处日志，**默认只出报告**，`--apply` 才删（走回收站）。

### Added（跨进程锁，P1-1）

- `safe_io.CrossProcessLock`：Windows 命名互斥量（`CreateMutexW`，
  `Local\` 会话命名空间）实现真正的跨进程读-改-写互斥——原子性 ≠ 隔离性，
  `tmp+fsync+os.replace` 只保证不写坏，互斥靠锁。
- 含遗留 `.lock` 文件清理（`cleanup_legacy_locks`，文件锁弃用后的收尾）。

### Changed

- 版本号单点 `__version__`（v2.2.3 引入）同步 2.3.0；`pyproject.toml` 对齐。
- 对抗性审查两轮：缓存刷新 / 批量 vanish 保护 / 失败日志 / purge_db 分块 /
  add 失败如实返回 0。测试 305 条，303 断言全绿（2 条历史 detect_agents
  缓存用例与本版无关）。

## [v2.2.3] - 2026-08-29

### Fixed（托盘图标反复沉入溢出区：GUID 标识接线）

- **根因**：注册托盘图标时未带 `_NIF_GUID`，Windows 11 退回按 **EXE 路径**
  识别图标并把「是否显示在任务栏」偏好记入注册表 IconStreams；而打包版
  每次自解压到不同目录（`%TEMP%\..._Run_%RANDOM%_%RANDOM%`），路径一变
  就被当作全新应用，默认塞进溢出区——用户手动拖出的设置随之失效。
  `_TRAY_GUID` / `_NIF_GUID` 常量早已定义却从未接线。
- **修复**：`nid.uFlags` 接入 `_NIF_GUID` 并填 `guidItem`，图标身份与显示
  偏好永久绑定 GUID，不再随路径漂移；`NIM_DELETE` / `NIM_MODIFY`（气泡
  通知）同步带 GUID，避免旧图标残影删不掉、通知发不出。
- `guidItem` 结构体字段 `c_byte` 改 `c_ubyte`（GUID 是原始字节；两者内存
  布局一致，仅为语义正确、调试不出现负值）。

### Fixed（运行目录固定化：告别 %TEMP%）

- EXE 自解压运行目录从 `%TEMP%\AgentMemorySync_Run[_随机]` 迁到
  **`%LOCALAPPDATA%\AgentMemorySystem\Run`**（bat 启动器与 build.py 同步修改）：
  1. `%TEMP%` 会被「存储感知 / 磁盘清理」清空，可能删掉正在运行的程序本体；
  2. 旧兜底逻辑用 `%RANDOM%` 拼目录名，是托盘图标身份漂移的另一半根因；
  3. `watchdog._find_exe` 兼容新旧两代路径，未重建的环境平滑过渡。

### Fixed（心跳不再写 OneDrive）

- 心跳此前每 5 分钟向数据根（OneDrive 同步目录）`tray_error.log` 追加一行，
  实测堆积 **86 个轮转文件 / 36MB** 且永不清理；两台设备并发 append 同一
  同步文件还会产生冲突副本。现在心跳只写 LOCALAPPDATA 主副本，跨设备诊断
  信息仍由 `_write_diag()` 在启动/托盘事件时写入数据根。

### Changed

- `requirements.txt` 移除已废弃的 `pystray`（托盘自 v2.0 起为 ctypes 直调
  原生 API），Pillow 描述修正为实际用途。

## [Unreleased] - v2.2.2

### Fixed（OneDrive 冲突副本根治：写入一律原子化）

- **`safe_io._safe_write_text` 重写为"永远原子"**：旧实现 `os.replace` 失败
  （OneDrive/杀软瞬时锁）时回退 **in-place 直写**——就地截断重写云端正在同步
  的文件，正是 OneDrive 报"无法操作/建立冲突副本"的元凶。新规则：
  1. 永远先写同目录**进程唯一** tmp（`原名.tmp{pid}`，旧版固定 `.tmp` 名，
     GUI/watchdog/CLI 并发写同一目标会互相覆盖 tmp 并撞 replace）+ `fsync`；
  2. `os.replace` 遇 `PermissionError` 指数退避重试；
  3. 持续锁定 → 完整快照落 `.pending`（读端优先采用更新快照，不丢数据）；
  4. 写成功后清理已被取代的过期 `.pending`。
- **`.pending` 生命周期语义修正**：`.pending` 一律是完整快照而非增量片段，
  `merge_pending_file` 恢复动作改为"新则替换、旧则丢弃"（旧版 append 会把
  快照再拼一遍 → 内容翻倍）；`_safe_read_text` 优先返回更新的 `.pending`；
  各入口（GUI/CLI/watchdog）启动时 `merge_pending_files` 全树收编 + 清理
  崩溃遗留超 1 小时的孤儿 `*.tmp*`。
- **settings 读写原子化**：`memory_sync_app.load_settings/save_settings` 统一走
  `safe_read_text`/`safe_write_text` 公共 API（新增别名），消灭模块内最后一处
  in-place 直写路径。

### Fixed（dsh agent 识别不出来：三层根因全修）

- **检测配置缺失**：`config.json` 新增 `dsh` profile（`~/.dsh` 等候选路径，
  `settings.yaml` 主签名 + `.anonymous-user-id` 备用签名——刚装完还没写
  settings.yaml 也能识别）；`_verify_agent_signature` 支持备用签名路径。
- **检测缓存不失效（隐蔽根因）**：`.detected_agents.json` 缓存 TTL 默认 24h，
  配置新增 profile 后旧缓存照样命中，新 agent 最长 24h 不可见。修复：缓存写入
  时记录 `agent_detection` 配置指纹（`profiles_hash`），读取时指纹不一致 →
  缓存立即失效重检测；指纹一致则照常生效（缓存机制本身保留）。
- **通用发现盲区**：`_discover_generic_agents` 增加家目录 `~` 点目录扫描
  （大量 CLI agent 以 `~/.xxx` 安装），关键词增加 `dsh`。
- **dsh 记忆扫描**：根目录 `*.md` + `sessions/**/*.jsonl` + `storages/*.json`；
  `.zstd` 压缩会话跳过。

### Security

- **凭据文件绝不进入记忆管道**：`_should_skip_agent_memory_file` 过滤
  `.credentials.yaml`（dsh）/`auth.json`（pi）等凭据类文件（`credential`/`auth`/
  `.env`/`secrets` 等），写回产物兜底再滤一次。

### Fixed（其他）

- **rollback 回归测试环境依赖**：旧测试直接 `SyncEngine()` 用真实数据根，机器上
  存在历史 `.sync_backups` 时断言恒失败；改为临时空数据根隔离。

### Added

- 新增 6 个回归测试（锁定目标原子写降级、pending 全树恢复 + 孤儿 tmp 清理、
  缓存指纹失效/生效双场景、dsh 记忆扫描与凭据排除、rollback 隔离），全量
  234 断言；对抗性审查（OneDrive 独占锁模拟、6 进程并发写、崩溃残留）通过。

## [Unreleased] - v2.2.1

### Fixed（OneDrive 运行时解耦，根治 v2.2.0 事故）

- **同步失败根治（日志本机化）**：引擎日志 `agent_memory.log` 从数据根 `.logs`
  （OneDrive 同步目录）迁至 `%LOCALAPPDATA%\AgentMemorySystem\logs`；`LogManager`
  候选目录依次降级（本机 logs → 数据根 .logs → 仅控制台），`get_logger()` 永不
  抛异常——日志故障不再中断同步（修复 `[Errno 13] Permission denied:
  ...agent_memory.log`）。
- **程序假死根治**：`_notify` 的 PowerShell 调用改为带超时（8s）且不捕获输出
  （DEVNULL），不再可能永久阻塞 tkinter 主线程；托盘失败路径改为先弹提示框、
  后发通知。
- **启动不再触碰 OneDrive 写入**：`get_data_root()` 移除 `.writable_test` 同步写
  测试（改为只读 `is_dir()` 校验），杜绝启动/CLI 在 OneDrive 锁下抛错或挂起。
- **托盘注册自愈**：`Shell_NotifyIconW(NIM_ADD)` 失败后延迟重试一次；诊断日志
  （`[RELOC]`/托盘 DEBUG/崩溃/退出）全部改为本机 LOCALAPPDATA 优先、数据根尽力
  而为，OneDrive 锁下不再丢失故障现场。
- **迁移复制加固**：`_ensure_local_install` 改用 robocopy（`/MIR` 镜像 + 自带
  重试 + 整体超时），替代在 OneDrive 并发同步下会卡死/半途失败的
  `shutil.copytree`；迁移失败仅告警、继续原地运行。

### Added（UI，v2.2.1）

- **"退出程序"按钮**：操作卡片底部新增（1px 分隔线上方），点击直接退出，无需
  再依赖托盘右键菜单。
- **窗口内容自适应**：`_build_ui` 后按内容实际所需尺寸（`winfo_req*`）校正窗口
  大小——默认/保存尺寸不足以容纳内容（右侧操作卡片被裁）时自动放大，且裁剪
  到屏幕工作区；不再依赖硬编码物理像素（天然适配任意 DPI/字号/屏幕）。

### Added

- 新增 10 个回归测试（日志降级/本机化、get_data_root 热路径不写盘、通知超时与
  DEVNULL、托盘重试、robocopy 迁移、诊断日志本地优先、窗口尺寸纯函数、退出
  按钮），全量 210 断言。

## [2.2.0] - 2026-08-13

### Changed（架构：shared.db 本机化，方案 A）

- **SQLite 彻底移出 OneDrive**：`shared.db` 从数据根（OneDrive 同步目录）迁至 `%LOCALAPPDATA%\AgentMemorySystem\shared.db`，作为**本机查询缓存**；跨机事实源改为 `memory_shared.md`（可 diff、无锁冲突），OneDrive 双向同步 SQLite 的反模式终结
- **缓存可重建**：本机缓存缺失/损坏时自动从 `agent_*/memory_shared.md` 重建（`rebuild_shared_cache_from_md`）；旧 OneDrive `shared.db` 首次同步自动迁移到本地并标记 `shared.db.migrated`（可恢复、不删除）
- **增量同步（P3-13）**：`memory_shared.md` 从"每次全量重建"改为"增量追加"——解析现有条目 id，只追加库中新增条目；文件缺失/格式损坏/超限时降级全量重建，消除 OneDrive 写放大与冲突风险；实测连续 3 次同步后第 2、3 次零写入
- **条目解析加固**：`_parse_md_entry_ids` 改用"头部定位法"（只认 `---` 后紧跟 `id:`），正文含 `---`（markdown 分隔线）不再导致解析错位；写入正文时将独立 `---` 行替换为 `- - -`

### Fixed（本阶段修复的既有问题）

- **体积控制打包失效（本轮核心 bug）**：`tools/shrink_memory_files.py` 未被 PyInstaller 收集（动态 `sys.path` + 裸 import），`_enforce_volume_control` 在 except 中整体 return，导致体积控制静默失效。修复：tools 变正式包（新增 `__init__.py`）、静态导入、`build.py` 加 `--paths tools` + `--hidden-import` + 打包后冒烟检查；新增 `_shrink_md_fallback` 内置兜底，即使再漏包也不会整体失效
- **FileLock UnboundLocalError**：`lock_acquired` 未提前初始化，抛 LockError 时 finally 引用未定义变量掩盖原始异常
- **回滚功能完全失效**：`SyncEngine.rollback()` 引用不存在的 `self.report`，回滚恒无效果；重写为 `backup_log.json` 驱动（备份名 → 目标路径映射）
- **备份名冲突**：`backup_file` 备份名仅用原文件名，跨 Agent 同名文件（MEMORY.md/user_profile.md）互相覆盖；加入 `agent_id` 前缀
- **跨机静默冒名**：`_resolve_source_device` / `_resolve_device_name` 匹配失败时回退 `default_device`，新机器记忆被错误标记为其他设备；改为无匹配即报错，调用方自动注册当前机器（hostname），注册失败回退真实 hostname，绝不冒名
- **陈旧 device_config.json 分裂**：删除根目录遗留 `device_config.json`（office_pc，非真实设备），`load_identity`/`SessionFlusher`/`memory_cli` 统一指向数据根
- **DB 过期清理时区/格式偏差**：时间戳为 T 分隔+小数秒格式，与 `datetime('now')` 空格格式字符串比较有边界偏差；改用 `substr(timestamp,1,10) < date('now',...)` 按天比较
- **VACUUM 高频执行**：每次同步无条件 VACUUM 整个 DB；改为仅实际删除条目时执行
- **OneDrive 冲突检测仅支持英文**：`(conflicted copy)` 之外的（冲突副本）/（衝突副本）/法语/德语命名漏检；改为单次遍历 + 多语言正则
- **跨机路径注入失效**：`_inject_brief_pointer` 注入绝对路径，OneDrive 同步的入口文件在另一台机器失效；改为相对路径 + 查找提示
- **`_safe_read_text` 死代码**：`isinstance(OSError, MemoryError)` 恒为 False；拆分为 MemoryError 直接返回默认值 + OSError 重试
- **日志无限增长**：`heartbeat.log` / `tray_error.log` 超 1MB 自动滚动为 .old

### Added

- 新增 22 个回归测试（体积控制兜底/回滚/备份命名/设备解析/缓存重建/增量同步/迁移/中文冲突/SQL/build.py 等），全量测试 174 断言全绿
- `.sync_state.json` 写入加文件锁 + 磁盘状态合并（同机多进程/跨机并发不互覆盖）

## [2.1.2] - 2026-08-07

### Fixed（图标与托盘）

- **图标变成“羽毛”根治**：`assets/app_icon.ico` 是早期遗留的白底灰色图形（视觉上像羽毛），与 `app_icon.png`（紫色记忆图标）不一致。用 PNG 重新生成 ICO（16~256 全尺寸、深紫背景），EXE/任务栏/托盘图标统一为紫色图标。旧 ICO 备份为 `assets/app_icon.ico.bak_fly`
- **托盘验证与加固**：新增 `--tray-test` 诊断参数，打包环境下自动触发最小化到托盘并记录结果；实测 `Shell_NotifyIconW add=1`、`OK: tray created`，托盘功能在 PyInstaller 打包环境正常
- **清理分裂残留**：删除 LOCALAPPDATA\AgentMemorySystem\App（昨晚 relocate 产生的副本，无心跳、疑似导致旧提示）；该副本已被删除，防止用户误双击启动旧版本

## [2.1.1] - 2026-08-06

### Fixed（数据分裂根治）

- **数据根注册点（Single Source of Truth）**：彻底根治多入口数据分裂
  - 新增持久化注册文件 `%LOCALAPPDATA%\AgentMemorySystem\data_root.txt`，数据根的唯一事实来源
  - 所有入口（GUI / CLI / watchdog / 直接双击任意 EXE 副本 / 开发模式）启动时都只读注册点，仅首次运行才做路径推导并落盘
  - 环境变量 `AGENT_MEMORY_DATA_DIR`（BAT 启动器注入）成为**最高权威**：BAT 每次启动都以其为准并同步注册点，自动纠正被错误入口带偏的注册
  - watchdog 重启时显式注入注册点数据根，保证崩溃重启后仍指向同一位置
  - 实测：直接双击 OneDrive 分发包 EXE（无 BAT）也能正确解析到项目根 `AgentMemory/`，不再分裂到 LOCALAPPDATA
- **清理历史遗留入口**：移除 LOCALAPPDATA 旧安装副本（App.legacy_20260806），防止误双击启动错误版本

## [2.1.0] - 2026-08-06

### Fixed（稳定性）

- **统一数据根目录**：修复历史分裂——`SyncEngine` 曾硬编码 `<repo>/data`，而 GUI/SyncState/BAT 启动器使用 `AgentMemory/`（AGENT_MEMORY_DATA_DIR），导致同步引擎与状态/日志各写各的目录
  - `SyncEngine.root`、`detect_agents` 缓存、`memory_cli --root` 默认值、日志目录全部改为 `get_data_root()` 统一解析
  - 新增 `tools/unify_data_root.py` 一次性迁移脚本，将 `data/` 真实数据合并到 `AgentMemory/`
- **心跳写本地磁盘**：心跳日志主写入 `%LOCALAPPDATA%\AgentMemorySystem\heartbeat.log`，避免 OneDrive 锁阻塞主线程；数据根副本仍保留供跨设备诊断
- **崩溃日志双写**：CRASH/APP EXIT 同时写本地磁盘与数据根，OneDrive 锁时崩溃信息不再丢失
- **重启计数持久化**：崩溃重启计数写入数据根 `.restart_count`，正常退出时清除
- **FTS 索引孤儿修复（核心体积问题）**：
  - `MemoryDatabase` 新增 `delete_memory()` / `delete_memories()`，所有删除操作同步清理 `memories_fts`（历史 90%+ 孤儿行的根因）
  - 替换全部 10+ 处裸 `DELETE FROM memories` 调用点（含 sync_engine 的批量过期删除）
  - 新增 `repair_fts_orphans()` + `_repair_fts_if_needed()`，同步前自动检测修复
  - 新增 `tools/repair_fts.py`：10 个数据库已从 88.65MB 压缩至 4.86MB（回收 83.79MB）
- **旧数据清理**：`~/.agent_memory/`（6.8GB 历史遗留）重命名为 `~/.agent_memory_legacy_20260806` 移出使用路径（可恢复）

### Added（智能感）

- **知识简报 `knowledge_brief.md`**：每次同步为每个 Agent 生成精简知识摘要（top 15/域、非模板噪音、去重、≤20KB），替代让 Agent 加载数千行原始记忆
- **Agent 入口知识注入**：在 Agent 本地入口文件（`MEMORY.md` / `user_profile.md` 等）自动追加 `## Shared Knowledge (auto-synced)` 引导节，幂等（marker 去重），让 Agent 启动时真正主动加载共享知识
- **写回修复**：数据根统一后 reconcile 与状态对齐，写回从“提取 0/写回 0”恢复为实际生效（实测 492 条写回）

### Changed（UI/UX）

- **屏幕自适应**：设置对话框尺寸按屏幕可用空间裁剪（修复小屏 500x600 溢出）
- **DPI 感知**：启动时启用 Per-Monitor DPI Aware，修复高分屏模糊/错位
- **窗口尺寸自动保存**：用户拖拽主窗口大小后自动保存 geometry（防抖 1 秒）
- **新增“打开数据目录”按钮**：一键定位数据根
- **日志轮转调优**：`max_log_files=2`，日志目录统一到数据根 `.logs/`，清理旧 9.8MB 轮转文件

### Fixed（其他）

- `detect_agents` 缓存路径统一到数据根（旧版硬编码 `~/.agent_memory`）
- `memory_cli` 默认 root 从硬编码 `<repo>/data` 改为 `get_data_root()`

---

## [2.0.4] - 2026-07-31

### Fixed

- **圖示統一**：修復歷史遺留的多圖示分歧問題
  - 托盤圖示路徑從獨立的 `tray_icon.png` 改為與視窗相同的 `app_icon.ico`（`_TRAY_ICON_PATH = _ICON_PATH`）
  - `_create_default_icon()` fallback 不再生成藍色圓形 M 圖標（與 app_icon 內容差異 56.9%~61.9%），改為從 `app_icon.png` 加載並轉為 ICO
  - 刪除冗餘圖示資源 `assets/tray_icon.png`、`assets/tray_icon_64.png`
  - 保證視窗、任務欄、托盤三個場景圖示完全一致

### Removed

- **過程性文件清理**：
  - 刪除 `DEVLOG.md`（開發日誌，內容已併入 CHANGELOG.md）
  - 刪除 `test_memory.py`（舊測試套件，已由 `test_full.py` 取代）
  - 刪除 `docs/multi_agent_memory_sync_design_v1.2.md`（過程性設計文檔）
  - 刪除 `docs/assets/banner-prompt.md`（過程性提示詞文檔）
  - 刪除 `tools/migrate_v13_to_v14.py`（一次性遷移腳本，已完成歷史使命）
  - 刪除 `assets/tray_icon.png`、`assets/tray_icon_64.png`（冗餘圖示資源）

## [2.0.3] - 2026-07-31

### Added

- **寫回前體積保護（方案 0.5.6）**：`sync_writers.py` 的 `BaseMemoryWriter` 新增 `_enforce_write_volume_limit()` 方法，在 Hermes/Trae/GenericMarkdown writer 的 `write()` 寫入前檢查 content 大小，超限時按 front matter 邊界從頭部截斷舊內容，保留最新條目
  - 新增 `_truncate_head_at_boundary()` 和 `_truncate_at_boundary()` 兩個邊界感知截斷方法
  - 體積策略從 `volume_policy.json` 動態讀取，預設 256KB / 3000 行

### Changed

- **`agent_runtime_manual.md` 移除不可執行規則**：原要求 Agent 調用 `write_memory()` Python 函數，但 Agent 只能讀寫文件。改為「直接編輯 memory_private.md，在文件末尾追加 front matter 格式條目」的可執行指南
  - `id` 字段從「由系統生成, 不可手編」改為「Agent 自行生成 `mem_YYYYMMDD_HHMMSS_XXXXXX`」
  - 路徑權限從「通過 write_memory API」改為「直接編輯文件，遵循 §3 格式」

- **`data/prompt.md` 消除硬編碼路徑**：原文件包含 `C:\Users\MR.Dong\...` 絕對路徑，改為占位符 `<AgentMemorySystem 數據根目錄>` 和 `<AgentMemorySystem 安裝目錄>`，並添加路徑替換說明

- **`shrink_memory_files.py` 排序邏輯簡化**：原 6 次 sort/reverse 調用簡化為單次 `sorted()` + `reverse=True`，利用 priority 和 conf_rank 取反技巧實現三維排序（永久優先 → confidence 降序 → 時間倒序）

## [2.0.2] - 2026-07-31

### Fixed

- **徹底解決回聲污染問題**：`memory_shared.md` 中殘留的 `[sync:...]` 標記和嵌套「— 來自 xxx (date)」導致 sync 引擎寫回的內容被再次提取為新記憶，形成正反饋循環
  - `agent_memory.py` `_is_sync_generated_content()` 新增 RAW_JSON_START/END 包裝檢測和 2+ 個 echo marker 嵌套檢測
  - `sync_engine.py` 新增 `_purge_polluted_entries()` 在每次同步前清理 DB 和 .md 中的污染條目
  - `sync_engine.py` 新增 `_clean_md_files()` 從 front matter 格式的 .md 文件中移除污染條目塊
- **`extract_local_to_fused()` 硬編碼 `source_device="extracted"`**：歷史遺留問題導致同步報告設備名顯示為「extracted」
  - 改為從根目錄 `device_config.json` 解析當前機器的 source_device
- **`extract_local_to_fused()` 使用絕對路徑寫 identity.json**：多機同步時路徑不可移植
  - 改為相對路徑（`memory_root: "."`, `shared_root: "../_shared"`）
- **`_count_legacy_markers()` 語義錯誤**：統計所有 `[sync:...]` marker（含新格式），導致 legacy 計數被高估，觸發保守模式死鎖
  - 改為只統計無 `|h:` 字段的真正 legacy marker
- **`extract_target_info()` legacy 計算重複減法**：`legacy = count - len(hashes)` 在新 `_count_legacy_markers` 語義下重複扣減
  - 改為直接使用 `_count_legacy_markers()` 結果

### Changed

- 所有 `data/agent_*/identity.json` 統一改為相對路徑，支援跨設備 OneDrive 同步
- `sync_engine.py` 同步流程新增「②.5 污染清理」階段，在提取前確保 DB 和 .md 乾淨
- 測試套件 `test_full.py` 修正 6 個因 v2.0 API 變更而失敗的用例

## [1.4.0] - 2026-07-23

### Fixed

- **根治寫回始終為 0 條的核心 BUG**（`hermes=0, trae=0, codepilot=0, codebuddy=0` 但記憶檔案持續膨脹）：
  - `reconcile_with_target_hashes()` 只定義無調用 → SyncState 孤兒 hash 永不清零，目標條目數 < state 條目數導致全部跳過寫回
  - `_load_shared_memories()` 硬編碼 `LIMIT 500 + ORDER BY timestamp DESC`，每次載入相同 500 條，增量來源枯竭
  - `_is_sync_generated_content()` 匹配所有 `[sync:*]` marker → 含 marker 的用戶本體內容被誤殺
  - `_scan_agent_memory_files()` 無檔案大小上限，5MB+ 檔案直接讀入
  - 提取階段缺乏污染自愈（原僅在寫回階段自愈）
- **SyncState 去重狀態系統性偏離目標檔案真實內容**（target 內容增長但 state 不變，邏輯鎖死）

### Changed

- `sync_engine.py` `run()`：寫回前新增 `reconcile_with_target_hashes()` 調用，對齊 SyncState 與目標檔案實際 hash
- `sync_engine.py` `_load_shared_memories()`：改為增量加載（LIMIT 2000 + content_hash 跳過已寫回），不再固定返回 500 條
- `agent_memory.py` `_is_sync_generated_content()`：預檢查 sync 標記/前綴，無 sync 痕跡的原生內容直接放行；有 sync 痕跡才剝離後檢查長度 < 30 字
- `agent_memory.py` `_scan_agent_memory_files()`：所有分支添加 10MB 上限 + `_size_ok()` 校驗
- `agent_memory.py` `parse_hermes_memory()` / `parse_markdown()`：超大檔案前置污染自愈（備份 → 重建 → 解析）
- `sync_writers.py` `detect_pollution()` / `repair_polluted_file()`：提升為模塊級函數（供 agent_memory.py 提取階段復用）
- `sync_writers.py` `_repair_polluted_file()`：新增 `sync_state` 參數，自愈後清零對應 agent 的 SyncState hash 條目
- 日誌輪轉默認值：10MB → 2MB, 5 備份 → 3 備份

### Added

- **pi / pi-web Agent 精確檢測與寫回支援**：
  - `config.json` 新增 `pi` 檢測配置：`candidate_paths=['~/.pi']`, `signature_file='agent/auth.json'`
  - `_scan_agent_memory_files()` 新增 pi 專有分支：掃描 `.md`、`.jsonl`、`memory/` 子目錄
  - `WRITER_REGISTRY` 新增 `pi-web`、`pi`、`clawdbot` 寫回器
- **OpenClaw Agent 寫回支援**：`WRITER_REGISTRY` 新增 `openclaw` 映射
- **通用 Agent 發現擴展**：`ai_keywords` 追加 `pi`、`openclaw`、`deepseek`、`gemini`、`chatgpt`、`coding`、`trae`、`hermes`
- **`~/.npm-global/node_modules/@agegr` + `~/OneDrive`** 加入通用發現掃描目錄

### 實測數據

| Agent | 修復前寫回 | 修復後寫回 |
|-------|----------|----------|
| hermes | 0 | 252 |
| trae | 0/16 | 376 |
| codepilot | 0 | 297 |
| codebuddy | 0 | 208 |
| openclaw | 未檢測 | 193 |
| **pi** | **新支援** | **821** |

reconcile 自愈：trae 清理 208 條孤兒 hash。測試：129/129 全部通過。

## [1.3.6] - 2026-07-08

### Fixed

- **修复托盘圖示靜默消失問題**（辦公室與家中兩台電腦均復現）：根因是 `_tray_wndproc` 回呼在每次滑鼠移過托盤圖示（`WM_MOUSEMOVE`，`lparam=512`）時都對 OneDrive 目錄下的 `tray_error.log` 做 `open/write/close`。OneDrive 同步鎖檔時回呼阻塞，Windows 因 wndproc 超時強制終止行程，Python 來不及記錄任何日誌。修復後 wndproc 不再做任何檔案 I/O，僅處理左鍵/右鍵點擊事件，其餘事件直接 `return 0`。

### Added

- **心跳日誌**：`_heartbeat()` 每 5 分鐘向 `tray_error.log` 寫入一次存活狀態（計數、時間戳、托盤是否啟用、是否同步中）。行程崩潰後心跳停止，下次啟動可據此判斷上次行程的死亡時刻
- **全域崩潰捕獲**：`_setup_crash_handlers()` 安裝 `sys.excepthook` + `threading.excepthook` + `atexit`，主/子線程未捕獲異常會寫入 `=== CRASH ===` 區塊（含完整 traceback），正常退出時寫入 `APP EXIT` 記錄
- **mainloop 自動重啟**：`main()` 用 `try/except` 包裹 `mainloop()`，崩潰後記錄日誌並自動重啟（最多 3 次，每次間隔 2 秒），超過上限彈窗提示使用者查看日誌後手動重啟。避免托盤靜默消失後使用者需手動重啟

## [1.3.5] - 2026-07-06

### Added

- **同步進度條**：`_start_sync` 啟動時顯示不確定型進度條，根據 `SyncEngine` 進度消息自動切換階段文字（檢測 Agent 中 / 提取記憶中 / 融合共享中 / 寫回 Agent 中 / 已完成），同步完成自動隱藏
- **日誌彩色 tag**：`_log()` 支援 `level` 參數與自動推斷，時間戳灰、錯誤紅、成功綠、警告橙、信息藍；在 `tk.Text` 上配置 `timestamp` / `error` / `success` / `warning` / `info` 五個 tag
- **錯誤卡片**：同步失敗時在主窗口頂部顯示紅色邊框錯誤卡片（標題 + 錯誤詳情 + 關閉按鈕），不再只靠日誌排查
- **統計數值狀態著色**：同步完成動態切換數值標籤樣式（`StatSuccess.TLabel` 綠 / `StatWarning.TLabel` 橙 / `StatError.TLabel` 紅）
- **COLORS token 擴展**：新增進度條（`progress_bg`/`progress_fill`/`progress_trough`）、日誌彩色（`log_timestamp`/`log_error`/`log_success`/`log_warning`/`log_info`）、錯誤卡片（`error_card_bg`/`error_card_border`）、統計狀色（`stat_success`/`stat_warning`/`stat_error`）、卡片陰影（`card_shadow`）
- **ttk 樣式新增**：`Horizontal.TProgressbar`（4px 細條）、`Stage.TLabel`、`ErrorTitle.TLabel`、`ErrorBody.TLabel`、`StatSuccess/Warning/Error.TLabel`；`Vertical/Horizontal.TScrollbar` 加 `active` hover map

### Changed

- **PIL 狀態點升級**：`_make_status_dot_image` 改為三層疊加繪製 —— 外圈半透明光暈（alpha=50）+ 中圈主光暈（alpha=90）+ 實心圓點 + 左上角中心高光（alpha=120），超採樣 4x 後 LANCZOS 縮小，視覺更精緻

### Fixed

- 修復托盤通知 `agents_found` 屬性不存在 bug：`SyncReport` 真實屬性名為 `agents_detected`，原寫法導致托盤通知 Agent 數永遠顯示 0

## [1.3.4] - 2026-07-02

### Changed

- **資料目錄回歸專案根**：`safe_io.get_data_root()` 解析邏輯簡化，專案根 `AgentMemory/` 成為預設位置（v1.3.2 曾改為 OneDrive 根下，但造成資料與專案割裂、雙 OneDrive 帳號定位錯誤等問題）
- 去除 OneDrive 環境變數獨立探測邏輯（`OneDrive`/`OneDriveConsumer`/`OneDriveCommercial` 掃描），跨裝置同步靠專案資料夾本身在 OneDrive 下即可
- 快捷方式圖示改用本地 TEMP 副本（`%TEMP%\AgentMemorySync_Run\_internal\assets\app_icon.ico`），修復 OneDrive 雲佔位符導致的白底圖示問題
- `build.py` 圖示選擇邏輯調整：本地副本優先 > 源構建目錄 > 專案根（原專案根優先會觸發雲佔位符問題）

### Added

- v1.3.4 升級自動遷移：`_migrate_old_data()` 自動從舊位置 `OneDrive\AgentMemory\` 複製資料到新位置 `專案根\AgentMemory\`（保留舊目錄作為備份，避免 OneDrive 同步衝突導致資料遺失）

### Fixed (2026-07-03 補丁，版本號不變)

- 修復小螢幕筆記本視窗顯示不全：保存的 `window_geometry` 直接套用未校驗是否超出當前螢幕。在解析保存的幾何後按當前螢幕工作區（`screen_w-40` / `screen_h-100`）裁剪寬高，並丟棄舊位置座標，由 `_center_window` 重新居中，避免換螢幕/多顯示器後視窗跑到屏外
- 修復 `_center_window` 只裁高度不裁寬度、且 `winfo_width()` 在某些時序下返回 1 的問題，新增 geometry 字串解析回退路徑
- 修復定時自動同步不生效：調度器 `_check()` 誤用 `auto_start`（啟動時立刻同步一次）作為門控，導致只設定間隔未勾「啟動時自動執行同步」時定時同步永不觸發、主界面同步日誌也無顯示。新增獨立開關 `auto_sync` 與 `auto_start` 解耦，設置面板新增「啟用定時自動同步」複選框
- 修復 `auto_interval_hours` 在調度器創建時閉包捕獲導致改設置後不重啟不生效的問題，改為每次檢查時動態讀取
- 修復 `_last_sync_time` 初始值為 0 導致首次檢查因 elapsed 巨大立刻觸發同步的問題，改為初始化為當前時間
- 啟用/保存定時同步時在主界面同步日誌輸出調度器狀態，觸發同步時打印間隔與距離上次同步的分鐘數，便於用戶確認調度器已就緒

## [1.3.3] - 2026-07

### Fixed

- 修复 EXE 打包缺少 tkinter.ttk 导致启动崩溃（ImportError: cannot import name 'ttk'）
- 修复托盘依赖安装：pythonw.exe 不支持 pip，改用同目录的 python.exe 安装
- 修复启动器 python/pythonw 路径不一致：从 python 路径推导 pythonw，确保同一安装
- 修复 Agent 检测缓存被测试污染问题（test_detect_agents 写入全局缓存导致真实检测失败）
- 修复 VBScript 启动器 UTF-8 编码问题（改用纯英文避免 Windows Script Host 解析失败）

### Changed

- 启动器重命名：`双击运行.bat` → `dev_run.bat`，`启动记忆同步.vbs` → `dev_run.vbs`（仅开发用）

### Added

- CodePilot Agent 支持：自动检测 `~/.codepilot/codepilot.db`，从 SQLite 导出对话历史为 Markdown
- 导出时自动过滤敏感信息（API 密钥、密码、token 等），8 种模式脱敏
- 通用 Agent 发现机制：`_discover_generic_agents()` 自动扫描常见 AI 工具目录
- PyInstaller 打包支持：`python build.py` 生成单文件 EXE（~18MB），内置所有依赖
- 应用窗口图标和托盘图标使用 `assets/icon.ico`
- 同步日志显示各阶段路径：融合层目录、Agent 源路径、共享数据库、写回目标
- 定时自动同步调度器：基于 `auto_interval_hours` 设置自动触发同步

### Changed

- EXE 输出位置从 `dist/` 改为项目根目录
- 打包后自动清理 `build/`、`dist/`、`*.spec` 临时文件
- 排除 15 个不需要的 PIL 子模块，EXE 体积从 20MB 降至 18MB
- `.gitignore` 新增 `AgentMemorySync.exe`、`device_config.json`
- 自动同步间隔从"天"改为"小时"，默认 2 小时，选项：1/2/4/8/16/24/48/72 小时
- 设置对话框点 X 关闭时自动保存（之前只有点"保存"按钮才生效）
- 新图标：app_icon.ico（主窗口）、tray_icon.png（托盘），由用户提供
- 同步完成后托盘气泡通知：显示设备名、Agent 数量、提取/写回条数、错误数
- Windows 原生通知兜底：pystray 不可用时自动降级为 Shell_NotifyIcon

### Fixed

- 修复 Hermes 锁文件残留问题：sync_writers.py 的手动锁没有过期检测，程序异常退出后锁文件永久残留。新增 60 秒过期自动清理
- 修复通用 Writer（GenericMarkdownWriter）同样的锁文件残留问题
- 修复 PyInstaller 打包 PIL 模块不全导致托盘崩溃：改用 `--collect-submodules PIL` 一次性收集所有 PIL 子模块
- 修复托盘图标创建后窗口隐藏过快：添加 0.3s 等待确保图标注册完成
- 修复 build.py 图标路径仍为旧 `icon.ico`，改为 `app_icon.ico`
- 托盘创建失败时自动写入日志文件 `~/.agent_memory/tray_error.log` 方便排查
- 修复最小化到托盘不生效：`_on_close()` 中 `and self.tray_icon` 条件导致 tray_icon 未创建时直接跳过
- 修复 build.py 图标路径仍为旧 `icon.ico`，改为 `app_icon.ico`
- 日志背景从黑色改为白色，文字改为黑灰色
- 窗口标题改为"多Agent记忆融合器"

### Added

- 重复启动检测：Windows 命名互斥锁（Global\AgentMemorySyncMutex），重复双击时弹窗提示"已在运行中，请检查系统托盘"

### Fixed (Earlier)

- 修复 pyproject.toml build-backend（setuptools.backends._legacy → setuptools.build_meta）
- 修复 pyproject.toml 版本号（0.1.0 → 1.3）并添加依赖声明
- 清理 docs/ 中 MR.Dong 硬编码路径（设计文档附录 A 改为占位符）
- 删除一次性部署脚本 docs/_apply_onedrive_changes.py（已被 detect_agents + extract 替代）
- 删除重复文档 docs/V1.3_AUTOMATION.md（内容已并入 README + DEVLOG）
- 清理 data/ 目录中真实运行时数据（个人用户名、真实记忆内容、SQLite 数据库）
  原数据完整保留在 data.local.bak/，data/ 重建为干净结构 + .gitkeep + README
- 修复 fsync_file() Windows 兼容性（添加 PermissionError 降级处理）
- 修复文件锁 TOCTOU 竞态条件（改用 os.O_CREAT | O_EXCL 原子创建）
- 修复 recover_pending_if_exists() 非原子性（改用临时文件 + rename 模式）
- 修复 now_iso8601() 缺少时区信息（改为 UTC 时间）
- 添加 ID 序号溢出保护（超过 999 条时抛出异常）
- 修复 load_private_memories() 只加载 memory_private.md 的问题，改为扫描所有 memory_private*.md 文件
- 修复 happy_path 和 write_and_reload 测试失败（适配设备专属文件命名）
- 修复 Hermes 路径 bug（AppData/Local/hermes/memories 不在 _AGENT_SUBDIRS 中）

### Added

- 创建 pyproject.toml 和 requirements.txt
- 创建 README.md
- 配置管理系统：config.json + ConfigManager 类，替代硬编码参数
- 日志系统：LogManager 类，自动记录操作日志到 .logs/ 目录，支持轮转
- 敏感信息检测：SensitiveInfoDetector 类，写入时自动检测密码/密钥/token
- 记忆过期机制：expire_old_memories() 函数，自动归档超过 max_memory_age_days 的记忆
- 数据库版本管理：metadata 表 + migrate_database() 函数，支持结构迁移
- 健康检查：health_check() 函数，检查配置/数据库/文件/磁盘空间等
- 新增 CLI 命令：health、expire、migrate
- 核心函数集成日志：startup、write_memory、search_memory、sync_markdown_to_db、full_sync、smart_compress、archive_cold_memories
- 核心函数集成敏感信息检测：write_memory 写入前自动检测
- ConfigManager 集成：DataProtection、TieredStorageManager、ConcurrentWriteManager、SmartCompressor 均从 config.json 读取参数
- 更新 README.md：新增系统运维章节（健康检查、过期清理、配置文件、日志系统、敏感信息检测）
- v1.3 自动化扩展：AgentRegistry（自动发现）、LocalMemoryParser（多格式解析）、TriggerEngine（关键词触发器）、SessionFlusher（会话落盘钩子）
- 新增 CLI 命令：discover、dashboard、extract、flush
- setup_agent.py：初始化时自动创建 triggers.yaml 并注册到 Registry
- requirements.txt：新增 pyyaml>=6.0
- 鲁棒性 Agent 路径探测：detect_agents() 函数，候选路径 + 特征验证 + 缓存 + 手动覆盖
- OneDrive 冲突文件检测：check_onedrive_conflicts() 函数
- 写回适配器：ClaudeMemoryWriter、TraeMemoryWriter、HermesMemoryWriter，按各 Agent 格式追加共享记忆
- 同步去重：基于 content hash 的 SyncState，避免重复写入
- 同步引擎：SyncEngine 类，完整流程（detect → extract → merge → write_back）
- GUI 同步工具：memory_sync_app.py，tkinter 主窗口 + pystray 系统托盘常驻
- 设置面板：自动同步间隔、OneDrive 冲突处理、Agent 路径覆盖
- 备份与回滚：每次同步备份原文件，支持一键回滚
- 新增 CLI 命令：full-sync（完整同步）、redetect（重新检测 Agent）
- requirements.txt：新增 pystray、Pillow
- 数据目录迁移：从 OneDrive/AgentMemory/ 迁移到项目内 data/ 目录
- GUI 美化：macOS 风格配色 + 卡片式布局 + 状态指示器
- 项目清理：删除 __pycache__、过程文件，设计文档归档到 docs/
- 一键启动器：双击运行.bat，自动检查依赖并启动 GUI
- README.md 重写：开源级完整文档，含依赖说明、工作原理、常见问题

### Fixed (v1.3 质量修复)

- 修复所有硬编码路径（AgentRegistry、extract_local_to_fused、TriggerEngine、CLI --root），改用 Path.home() 动态构建
- 统一版本号：v1.4 → v1.3（代码、测试、文档）
- TriggerEngine YAML 解析：优先用 pyyaml，fallback 到简单解析器
- SessionFlusher.flush()：新增内容去重，避免重复条目写入
- extract_local_to_fused：改用 write_memory() 完整流程（敏感信息检测、日志、SQLite 同步）
- parse_jsonl：删除 500 字硬截断，存完整内容

## [v0.1.0] - 2026-05-20

### Added

- 多 Agent 记忆同步系统核心库 (`agent_memory.py`)
- 启动流程（8 步完整实现）
- 写入流程（落盘 7 步，MVP 无缓冲/无节流/无去重）
- 基于文件的分布式锁机制（带死锁超时检测）
- .pending 文件恢复机制
- OneDrive 冲突文件检测
- 自定义异常体系（6 种异常类型）
- 记忆条目 Markdown 解析与格式化
- 设备配置文件 (`device_config.json`)
- 一次性部署脚本 (`_apply_onedrive_changes.py`)
- 5 个测试用例 (`test_memory.py`)
