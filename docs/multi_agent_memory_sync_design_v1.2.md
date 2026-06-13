# 多 Agent 记忆同步系统设计文档
> **文档版本**：v1.2
> **最后更新**：2026-05-20
> **目标读者**：项目维护者（人类） + 实施方（Agent）
> **v1.2.1**: 修正机器特定路径处理,代码路径由系统提示词指定,非写死在文档/手册中;Agent 命名简化(去 _code / _lobster 后缀)。
> **v1.2 主要变更**：
> 1. `source_device` 从 `identity.json` 中移出,改用本地 `device_config.json` 配置(第 4.1、4.6 节)。
> 2. 新增 `_shared/agent_runtime_manual.md` 作为所有 Agent 共用的运行手册(第 4.7 节)。
> 3. `writing_policy.md` 骨架升级为正式版条款(第 4.5 节)。
> 4. 启动流程新增"读取 device_config.json"步骤(第 5.1 节)。

---
## 1. 项目目标
构建一个跨设备、跨 Agent 的记忆同步系统,满足以下核心需求:
1. **跨设备一致性**：同一个 Agent 安装在多台电脑时,使用同一份记忆。
2. **多 Agent 协作**：不同 Agent 各自维护私有记忆,定期融合实现知识共享。
3. **写入规范统一**：所有 Agent 遵守同一份约束文件,保证格式一致。
4. **存储渐进升级**：从 Markdown 起步,按数据量升级到 SQLite、向量库。
5. **底层依赖最小**：使用 OneDrive 做文件同步,不引入额外服务端。
6. **对 Agent 透明**：Agent 不感知"云同步"的存在,只看到本地文件路径。
7. **设备身份本地化**：机器特定的配置不进入 OneDrive 同步,避免跨设备覆盖。

---
## 2. 架构总览
```
OneDrive/                                  ← 跨设备同步层(数据)
└── AgentMemory/
    ├── _shared/
    │   ├── agent_runtime_manual.md       # 【v1.2 新增】所有 Agent 共用的运行手册
    │   ├── writing_policy.md             # 写入约束(只读)
    │   ├── merged_memory.md              # 融合后的共享记忆
    │   ├── merge_log.md                  # 融合历史
    │   ├── subscriptions.yaml            # 各 Agent 订阅配置
    │   └── .merge_lock                   # 融合互斥锁(运行时)
    ├── agent_hermes/
    │   ├── identity.json                 # 跨设备共享的身份信息(无 source_device)
    │   ├── memory_private.md
    │   ├── memory_private.md.pending
    │   ├── memory_shared.md
    │   └── last_sync.json
    ├── agent_<other>/
    │   └── ...
    └── archive/                          # 阶段二迁移后的 MD 备份

<系统提示词指定>\AgentMemorySystem\   ← 本地代码层(不同步)
├── agent_memory.py                       # 核心实现
├── test_memory.py                        # 测试脚本
└── device_config.json                    # 【v1.2 新增】本机设备配置
```

**三层职责划分**：
- **OneDrive 层**：纯文件搬运,Agent 不感知。
- **本地代码层**：代码 + 机器特定配置,不同步。
- **Agent 客户端层**：通过极简系统提示词引用 OneDrive 上的运行手册。

---
## 3. 核心决策记录
| 决策点 | 选择 | 理由 |
|---|---|---|
| Agent 身份识别 | 按 `agent_id` 逻辑身份 | 同 Agent 多设备视为同一人格,共用记忆 |
| 共享方式 | 分层记忆(私有 + 共享副本) | 共享知识同时保留个性视角 |
| 融合器部署 | 固定单机 + 定时任务 | 实现简单,避免并发冲突 |
| 记忆过期 | 不设过期 | 全量保留,通过分文件/分库管理大小 |
| 存储演进 | MD → SQLite → 向量库 | 按数据量阶梯升级 |
| 跨域共享 | 存储层平权 + 分发层可配置 | 通过 subscriptions.yaml 灵活控制 |
| 约束文件权限 | 仅人类可写,Agent 只读 | 防止 Agent 自改规则 |
| Agent 写入时机 | 事件驱动 + 节流缓冲 | 重要内容不丢、避免高频同步 |
| 云同步可见性 | 对 Agent 完全透明 | Agent 只读写本地路径 |
| **设备身份存储**【v1.2】 | 本地 device_config.json,不进 OneDrive | 避免跨设备覆盖 |
| **运行手册管理**【v1.2】 | 集中放 _shared/agent_runtime_manual.md | 改一处,所有 Agent 同步生效 |

---
## 4. 文件格式规范

### 4.1 `identity.json`(v1.2 调整:移除 source_device)
每个 Agent 目录下必须存在,**跨设备共享,所有机器看到的内容一致**。
```json
{
  "agent_id": "hermes",
  "display_name": "爱马仕龙虾",
  "primary_domain": "general",
  "memory_root": "C:\\Users\\<user>\\OneDrive\\AgentMemory\\agent_hermes\\",
  "shared_root": "C:\\Users\\<user>\\OneDrive\\AgentMemory\\_shared\\",
  "created_at": "2026-05-19T00:00:00"
}
```
**重要**: `source_device` 字段已从此文件移除,改由 `device_config.json` 提供(见 4.6)。

**字段说明**:
- `memory_root` / `shared_root`: 路径中可能含用户名,不同机器若用户名一致则无需修改;若不一致需在每台机器各自维护版本(可用占位符 + 启动时替换的方式,但 MVP 不实现)。

### 4.2 记忆条目格式(MD 阶段)
```markdown
---
id: mem_20260520_office_pc_001
agent_id: hermes
timestamp: 2026-05-20T10:43:00
source_device: office_pc
domain: general
tags: [架构设计, 记忆系统]
confidence: high
conflict_with: null
---
正文内容……
```
**字段强约束**:
- `id` 格式: `mem_<YYYYMMDD>_<source_device>_<3位序号>`,跨设备唯一。
- `source_device` 取自 `device_config.json`(不再来自 identity.json)。
- 其余字段定义同 v1.1。

### 4.3 `last_sync.json`
```json
{
  "agent_id": "hermes",
  "last_merge_timestamp": "2026-05-12T03:00:00",
  "last_merge_id": "merge_20260512_001",
  "shared_memory_version": "v23"
}
```

### 4.4 `subscriptions.yaml`
```yaml
agents:
  hermes:
    subscribe_domains: [general, coding, writing]
    exclude_tags: []
  claude:
    subscribe_domains: [general, coding]
    exclude_tags: []
  trae:
    subscribe_domains: [general, coding]
    exclude_tags: []
```

### 4.5 `writing_policy.md`(v1.2 升级:从骨架变正式条款)
```markdown
# 记忆写入约束 v1

## 1. 写入原则

### 1.1 什么值得记
- 用户明确表达的偏好、决策、重要结论。
- 对话中形成的新认知、新方法论、新经验教训。
- 用户给出的事实性信息(姓名、项目名、路径、配置等)。
- 用户纠正过你的地方——这类记忆 confidence 设为 high。

### 1.2 什么不要记
- 闲聊、寒暄、过渡性语句。
- 临时性想法(用户说"先这样试试"那种)。
- 你自己的推理过程或思考(只记结论,不记过程)。
- 当前会话内显而易见的上下文。

### 1.3 粒度要求
- 一条记忆对应一个独立的知识点或决策。
- 不要把多件事混在一条里,宁可拆成多条。
- 每条记忆应能脱离当前会话被独立理解。
- 记忆正文建议 1~5 句话,过长说明粒度不对。

## 2. 格式规范
- 严格遵守第 4.2 节的 front matter 字段。
- 正文用纯文本或 Markdown,不嵌入 HTML。
- 涉及路径、命令、代码片段时用 inline code 包裹。

## 3. 去重与冲突
- 写入前扫描 memory_private.md + memory_shared.md 近 30 天条目。
- 与已有条目高度相似时不重复记录;若信息更完整,标注 conflict_with 指向旧条目。
- 矛盾信息保留两条,互填 conflict_with,由人类后续仲裁。

## 4. 写入时机
- 会话结束前必须刷新一次,把本次值得记的内容全部落盘。
- 用户明确说"记一下"、"记住"——立即记录,confidence: high。
- 形成新决策、新偏好——立即记录。
- 单次会话内,合并相关条目再写,避免碎片化。

## 5. 禁止行为
- 禁止写入 _shared/ 目录。
- 禁止修改 writing_policy.md 自身。
- 禁止修改其他 Agent 目录下的文件。
- 禁止删除已有记忆条目(仅可标记 confidence: low)。
- 不要凭空"补"历史记忆——你只知道当前会话,不要编造过去。
```

### 4.6 `device_config.json`(v1.2 新增)
放在系统提示词指定的本机代码目录下,**不进 OneDrive**,每台机器各自维护:
```json
{
  "source_device": "office_pc"
}
```
家里电脑该文件内容为 `{"source_device": "home_pc"}`,以此类推。

**字段约束**:
- `source_device`: 字符串,本机唯一标识。建议命名约定: `office_pc` / `home_pc` / `laptop` 等,只用小写字母+下划线+数字,避免空格和中文。

**缺失处理**: Agent 启动时若读不到此文件,必须抛出明确错误:
```
device_config.json not found at <expected_path>.
Please create it with: { "source_device": "<your_device_name>" }
```

### 4.7 `agent_runtime_manual.md`(v1.2 新增)
放在 `_shared/` 下,所有 Agent 共用的运行手册,人类维护,Agent 只读。完整内容见附录 B。

---
## 5. Agent 运行时行为规范

### 5.1 启动流程(v1.2 调整:新增 device_config 读取)
```
1. 读取本地 identity.json,获取 agent_id、memory_root、shared_root。
2. 读取本地 device_config.json,获取 source_device。
   - 若文件不存在 → 抛出明确错误,终止启动。
3. 读取 <shared_root>/agent_runtime_manual.md,加载为系统准则。
4. 读取 <shared_root>/writing_policy.md,加载为写入规范。
5. 检测 memory_private.md.pending,若存在则并入 memory_private.md 后删除。
6. 读取 last_sync.json,对比共享记忆版本,落后则重新加载 memory_shared.md。
7. 加载 memory_private.md + memory_shared.md 到上下文。
8. 检查 OneDrive 冲突文件(如 memory_private-办公室电脑.md),存在则报警并暂停写入。
```

### 5.2 写入流程
(与 v1.1 一致,此处略;`source_device` 字段来自 device_config.json。)

### 5.3 禁止行为
(与 v1.1 一致。)

---
## 6. 融合器规范
(与 v1.1 一致。)

---
## 7. 存储演进路径
(与 v1.1 一致。)

---
## 8. 实施步骤

### Sprint 1: MVP(已完成)
- [x] 目录结构、初始文件创建
- [x] identity.json / writing_policy.md 等初始内容
- [x] agent_memory.py / test_memory.py 实现
- [x] 跨设备 OneDrive 同步验证

### Sprint 1.5: v1.2 架构修正(当前)
- [ ] 移除 identity.json 中的 source_device 字段
- [ ] 新增 device_config.json 机制
- [ ] 修改 agent_memory.py 的 load_identity 函数
- [ ] 更新 test_memory.py 适配新结构
- [ ] 新增 agent_runtime_manual.md
- [ ] 升级 writing_policy.md 为正式条款
- [ ] 各 Agent 客户端切换到极简系统提示词

### Sprint 2: 规范完善
(与 v1.1 一致。)

### Sprint 3: 容量扩展
(与 v1.1 一致。)

---
## 9. 验收标准
(与 v1.1 一致;Sprint 1.5 额外验收: 同一 Agent 在两台机器写入的记忆 id 中 source_device 不同。)

---
## 10. 待定事项
(与 v1.1 一致。)

---
## 11. 部署指南

### 11.1 首台电脑
```
1. 安装 OneDrive,登录,确认同步。
2. 创建目录结构(第 2 节)。
3. 在 _shared/ 放入 writing_policy.md、subscriptions.yaml、agent_runtime_manual.md。
4. 在 agent_<id>/ 创建 identity.json(不含 source_device)。
5. 在代码目录创建 device_config.json,填本机标识。
6. 右键 AgentMemory/ → "始终保留在此设备上"。
7. 给 Agent 客户端配置极简系统提示词(见附录 A)。
```

### 11.2 新增设备
```
1. 安装 OneDrive,登录同账号,等待 AgentMemory/ 同步下来。
2. "始终保留在此设备上"。
3. 复制代码目录到本机(通过 U 盘/Git/其他方式,不通过 OneDrive)。
4. 在本机代码目录创建 device_config.json,填本机标识(如 home_pc)。
5. 给本机 Agent 客户端配置同样的极简系统提示词。
```
**注意**: 不要直接复制办公室的 device_config.json,必须本地新建并填入本机标识。

### 11.3 融合器部署
(与 v1.1 一致。)

### 11.4 日常维护
- 修改运行规则: 编辑 `_shared/agent_runtime_manual.md` 或 `writing_policy.md`,几秒后所有设备所有 Agent 自动生效。
- 新增 Agent: 复制极简系统提示词模板,改 agent_id,新建 `agent_<新id>/` 目录与 identity.json。
- 新增设备: 按 11.2 操作。

---
## 附录 A: Agent 系统提示词模板
每个 Agent 客户端的"自定义指令"或"系统提示词"中粘贴(改 agent_id):
```
你的 agent_id: agent_<在此填入>
你的身份配置文件:
<OneDrive同步目录>/AgentMemory/agent_<在此填入>/identity.json

本机代码目录(包含 agent_memory.py 和 device_config.json):
<此处填写本机实际代码目录绝对路径>

启动时必须先读取并严格遵守:
<OneDrive同步目录>/AgentMemory/_shared/agent_runtime_manual.md
```

## 附录 B: agent_runtime_manual.md 完整内容
```markdown
# Agent 运行手册 v1

> 本文件是所有 Agent 的统一行为准则。
> 由人类维护,Agent 只读不写。
> 修改后所有 Agent 下次启动自动生效。
> 本文件不写死任何机器特定路径,所有本机路径由系统提示词提供。

## 1. 启动时必做

1. 读取 identity.json,获取 agent_id、memory_root、shared_root。
   identity.json 的路径由系统提示词中的"身份配置文件"提供。
2. 读取本机代码目录下的 device_config.json,获取 source_device。
   代码目录由系统提示词中的"本机代码目录"提供。
   若 device_config.json 不存在,终止并报告错误。
3. 读取本文件(agent_runtime_manual.md),作为本次会话的行动准则。
4. 读取 <shared_root>/writing_policy.md,作为写入记忆的格式与质量约束。
5. 读取 <memory_root>/memory_private.md 加载历史私有记忆。
6. 读取 <memory_root>/memory_shared.md 加载共享记忆。

## 2. 路径约定

- 私有记忆: <memory_root>/memory_private.md (你专属,只有你写)
- 共享记忆副本: <memory_root>/memory_shared.md (融合器分发,只读)
- 全局规则: <shared_root>/ 下所有文件 (人类维护,只读)
- 设备配置: 本机代码目录下 device_config.json (本机维护,只读)
- 代码工具: 本机代码目录下 agent_memory.py

## 3. 写入记忆的方式

通过调用本机代码目录下 agent_memory.py 中的 
write_memory(content, tags, confidence) 函数。
本机代码目录由系统提示词中的"本机代码目录"提供。

不要手动编辑 memory_private.md。

## 4. 行为约束

- 不要询问用户"是否需要初始化"、"是否写入记忆"——直接按规则执行。
- 每个 Agent 只写自己目录下的 memory_private.md,不存在多 Agent 写同一文件的冲突。
- 不要写入 _shared/ 目录下任何文件。
- 不要修改其他 Agent 目录下的任何文件。
- 不要删除任何已有记忆条目。
- 不要凭空"补"历史记忆——你只知道当前会话,不要编造过去发生的事。
- 历史条目的 agent_id 字段可能与你现在的 agent_id 不同(如曾经的 hermes_lobster),
  这是重命名遗留,属于正常情况,不要尝试"修正"它们。

## 5. 写入时机

- 会话结束前刷新一次,把本次值得记的内容落盘。
- 用户明确说"记一下"、"记住"——立即记录。
- 形成新决策、新偏好、新认知——立即记录。
- 写入内容的具体质量标准见 writing_policy.md。

## 6. 当你对规则有疑问时

不要询问用户。重新阅读本文件和 writing_policy.md。
若仍无法判断,默认行为是"不写入",而不是"问用户"。
```