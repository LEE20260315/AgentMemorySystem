# AgentMemorySystem 使用示例

## Phase 1: 读取 API

### 1. 启动并同步记忆到 SQLite

```python
import agent_memory as am
from pathlib import Path

# 启动
ctx = am.startup(
    identity_path=Path("path/to/identity.json"),
    device_config_path=Path("path/to/device_config.json")
)

# 同步 Markdown 记忆到 SQLite 索引
count = am.sync_markdown_to_db()
print(f"同步了 {count} 条记忆到 SQLite")
```

### 2. 写入新记忆

```python
# 写入记忆
memory_id = am.write_memory(
    content="Python 是一种解释型、面向对象的高级编程语言",
    tags=["Python", "编程语言", "技术"],
    confidence="high",
    domain="技术"
)
print(f"记忆已写入: {memory_id}")
```

### 3. 搜索记忆

```python
# 关键词搜索
results = am.search_memory(
    query="Python",
    mode="keyword",
    tags=["编程语言"],
    domain="技术",
    limit=10
)

for memory in results:
    print(f"[{memory.id}] {memory.content[:50]}...")
    print(f"  标签: {memory.tags}")
    print(f"  置信度: {memory.confidence}")
```

### 4. 按 ID 获取记忆

```python
# 获取单条记忆
memory = am.get_memory("mem_20260612_test_device_001")
print(f"内容: {memory.content}")
print(f"访问次数: {memory.access_count}")
```

### 5. 列出记忆

```python
# 列出所有记忆
all_memories = am.list_memories(limit=20)

# 按领域过滤
tech_memories = am.list_memories(domain="技术", limit=10)

# 按标签过滤
python_memories = am.list_memories(tags=["Python"], limit=5)
```

### 6. 向量语义搜索 (需要安装 sentence-transformers)

```python
# 初始化 Embedding 服务
embedding_service = am.EmbeddingService(model_name="all-MiniLM-L6-v2")

# 向量搜索
results = am.search_memory(
    query="编程语言的特点",
    mode="vector",
    embedding_service=embedding_service,
    limit=5
)

# 混合搜索 (关键词 + 向量)
results = am.search_memory(
    query="Python 编程",
    mode="hybrid",
    embedding_service=embedding_service,
    limit=10
)
```

## Phase 2: 融合器 (跨 Agent 记忆共享)

### 1. 创建融合器

```python
from pathlib import Path
import agent_memory as am

# 创建融合器
merger = am.create_merger(
    shared_db_path=Path("shared.db"),
    agent_configs={
        "claude": Path("claude/memories.db"),
        "hermes": Path("hermes/memories.db"),
        "trae": Path("trae/memories.db"),
    },
    similarity_threshold=0.85  # 相似度阈值
)
```

### 2. 执行同步

```python
# 单向同步：Agent -> 共享库
result = merger.sync_agent_to_shared("claude")
print(f"同步结果: {result}")
# {'synced': 10, 'skipped': 2, 'conflicts': 1}

# 单向同步：共享库 -> Agent
result = merger.sync_shared_to_agent("hermes")
print(f"同步结果: {result}")
# {'synced': 5, 'skipped': 3}

# 完整双向同步
results = merger.full_sync()
print(f"完整同步结果: {results}")
```

### 3. 冲突解决策略

融合器自动处理冲突，策略如下：

1. **置信度优先**: 高置信度记忆覆盖低置信度
2. **时间优先**: 相同置信度时，保留最新记忆
3. **访问频率**: 相同置信度和时间时，保留访问更多的
4. **内容合并**: 标签合并，内容保留更详细版本

## Phase 3: 去重和质量控制

### 1. 去重

```python
import agent_memory as am
from pathlib import Path

db_path = Path("memories.db")

# 试运行（只检测不删除）
stats = am.run_deduplication(db_path, dry_run=True)
print(f"检测到 {stats['duplicates']} 条重复")

# 执行去重
stats = am.run_deduplication(db_path, dry_run=False)
print(f"删除 {stats['removed']} 条，保留 {stats['kept']} 条")
```

### 2. 记忆衰减

```python
# 创建衰减服务
decay_service = am.MemoryDecayService(
    db_path=Path("memories.db"),
    decay_rate=0.1,      # 衰减率
    min_weight=0.1,      # 最小权重
    max_age_days=365     # 最大天数
)

# 更新权重
stats = decay_service.update_weights()
print(f"平均权重: {stats['average_weight']:.2f}")

# 获取按权重排序的记忆
weighted_memories = decay_service.get_weighted_memories(limit=10)
for memory, weight in weighted_memories:
    print(f"[{memory.id}] 权重: {weight:.2f} - {memory.content[:30]}...")
```

### 3. 写入前去重检查

```python
# 创建去重服务
dedup_service = am.DeduplicationService(
    db_path=Path("memories.db"),
    similarity_threshold=0.95
)

# 检查新记忆是否重复
new_memory = am.MemoryEntry(
    id="new_id",
    agent_id="claude",
    timestamp="2026-06-12T00:00:00Z",
    source_device="laptop",
    domain="技术",
    tags=["Python"],
    confidence="high",
    conflict_with=None,
    content="Python 是一种编程语言"
)

is_duplicate, similar, similarity = dedup_service.check_duplicate(new_memory)
if is_duplicate:
    print(f"发现重复记忆: {similar.id} (相似度: {similarity:.2f})")
else:
    print("无重复，可以写入")
```

## 数据库结构

SQLite 数据库包含以下表:

- **memories**: 记忆主表
  - id, agent_id, timestamp, source_device, domain, confidence
  - content, embedding (向量), access_count, last_accessed

- **tags**: 标签表
  - id, name

- **memory_tags**: 记忆-标签关联表
  - memory_id, tag_id

- **memories_fts**: 全文搜索索引
  - 用于关键词搜索

## 搜索模式

| 模式 | 说明 | 依赖 |
|------|------|------|
| `keyword` | 关键词搜索 (LIKE 匹配) | 无 |
| `vector` | 向量语义搜索 | sentence-transformers |
| `hybrid` | 混合搜索 (关键词 + 向量) | sentence-transformers |
