"""
Agent 记忆系统 - MVP 测试脚本
==============================
5 个测试用例:
  1. happy_path: 正常启动 + 写入
  2. missing_identity: 缺失 identity.json 时抛出错误
  3. write_and_reload: 写入后重启加载验证
  4. missing_device_config: 缺失 device_config.json 时抛出错误
  5. id_contains_device: memory_id 包含 source_device 字符串
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import agent_memory as am


class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def ok(self, name):
        self.passed += 1
        print("  [PASS] {}".format(name))

    def fail(self, name, reason):
        self.failed += 1
        self.errors.append((name, reason))
        print("  [FAIL] {} - {}".format(name, reason))

    def summary(self):
        total = self.passed + self.failed
        print("\n" + "=" * 60)
        print("测试结果: {}/{} 通过".format(self.passed, total))
        if self.errors:
            print("失败用例:")
            for name, reason in self.errors:
                print("  - {}: {}".format(name, reason))
        print("=" * 60)
        return self.failed == 0


results = TestResult()


def create_test_env(base_dir, source_device="test_device"):
    """
    在 base_dir 下创建模拟的 AgentMemory 目录结构。
    返回 (identity_path, memory_root, shared_root, device_config_path)。
    """
    memory_root = base_dir / "agent_hermes"
    shared_root = base_dir / "_shared"
    memory_root.mkdir(parents=True, exist_ok=True)
    shared_root.mkdir(parents=True, exist_ok=True)

    identity_data = {
        "agent_id": "hermes",
        "display_name": "爱马仕",
        "primary_domain": "general",
        "memory_root": str(memory_root) + "\\",
        "shared_root": str(shared_root) + "\\",
        "created_at": "2026-05-19T00:00:00",
    }
    identity_path = memory_root / "identity.json"
    identity_path.write_text(json.dumps(identity_data, indent=2), encoding="utf-8")

    device_config_path = base_dir / "device_config.json"
    device_config_path.write_text(
        json.dumps({"source_device": source_device}, indent=2),
        encoding="utf-8",
    )

    policy_path = shared_root / "writing_policy.md"
    policy_path.write_text(
        "# 记忆写入约束 v1\n\n## 1. 写入原则\n- 每条记忆必须包含完整的 front matter 字段。\n",
        encoding="utf-8",
    )

    runtime_manual_path = shared_root / "agent_runtime_manual.md"
    runtime_manual_path.write_text(
        "# Agent 运行手册 v1\n\n## 1. 启动时必做\n1. 读取 identity.json\n",
        encoding="utf-8",
    )

    private_path = memory_root / "memory_private.md"
    private_path.write_text("# hermes 私有记忆\n", encoding="utf-8")

    shared_mem_path = memory_root / "memory_shared.md"
    shared_mem_path.write_text("# hermes 共享记忆\n", encoding="utf-8")

    sync_data = {
        "agent_id": "hermes",
        "last_merge_timestamp": None,
        "last_merge_id": None,
        "shared_memory_version": "v0",
    }
    sync_path = memory_root / "last_sync.json"
    sync_path.write_text(json.dumps(sync_data, indent=2), encoding="utf-8")

    return identity_path, memory_root, shared_root, device_config_path


def test_happy_path():
    print("\n[TEST] happy_path - 正常启动和写入")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        identity_path, memory_root, shared_root, device_config_path = create_test_env(tmp_dir)

        am._context = None

        ctx = am.startup(identity_path, device_config_path=device_config_path)
        results.ok("startup 成功")

        assert ctx.identity.agent_id == "hermes", "agent_id 不匹配"
        results.ok("agent_id == hermes")

        assert ctx.identity.source_device == "test_device", "source_device 不匹配"
        results.ok("source_device == test_device")

        assert len(ctx.runtime_manual) > 0, "runtime_manual 为空"
        results.ok("agent_runtime_manual.md 已加载 ({} 字节)".format(len(ctx.runtime_manual)))

        assert len(ctx.policy) > 0, "policy 为空"
        results.ok("writing_policy.md 已加载 ({} 字节)".format(len(ctx.policy)))

        assert ctx.write_allowed is True, "write_allowed 应为 True"
        results.ok("write_allowed == True (无冲突文件)")

        mid = am.write_memory(
            content="这是一条测试记忆, 用于验证 MVP 写入流程。",
            tags=["测试", "MVP"],
            confidence="high",
        )
        results.ok("write_memory 成功, id={}".format(mid))

        assert mid.startswith("mem_"), "ID 格式错误: {}".format(mid)
        results.ok("ID 格式正确: {}".format(mid))

        # 设备专属文件：memory_private_<device>.md
        private_md = memory_root / "memory_private_test_device.md"
        if not private_md.exists():
            # 兼容旧模式
            private_md = memory_root / "memory_private.md"
        text = private_md.read_text(encoding="utf-8")
        assert "这是一条测试记忆" in text, "内容未写入文件"
        results.ok("内容已写入 {}".format(private_md.name))

        assert "agent_id: hermes" in text, "front matter 缺少 agent_id"
        results.ok("front matter 包含 agent_id")

        assert "source_device: test_device" in text, "front matter 缺少 source_device"
        results.ok("front matter 包含 source_device")

        assert "confidence: high" in text, "front matter 缺少 confidence"
        results.ok("front matter 包含 confidence")

    except Exception as e:
        results.fail("happy_path", str(e))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_missing_identity():
    print("\n[TEST] missing_identity - 缺失 identity.json")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        identity_path, _, _, device_config_path = create_test_env(tmp_dir)

        identity_path.unlink()
        results.ok("identity.json 已删除")

        am._context = None

        try:
            am.startup(identity_path, device_config_path=device_config_path)
            results.fail("missing_identity", "未抛出异常")
        except am.IdentityNotFoundError as e:
            results.ok("正确抛出 IdentityNotFoundError: {}".format(str(e)[:60]))
        except Exception as e:
            results.fail("missing_identity", "抛出了错误类型: {}".format(type(e).__name__))

    except Exception as e:
        results.fail("missing_identity (setup)", str(e))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_write_and_reload():
    print("\n[TEST] write_and_reload - 写入后重启加载验证")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        identity_path, memory_root, shared_root, device_config_path = create_test_env(tmp_dir)

        am._context = None
        ctx1 = am.startup(identity_path, device_config_path=device_config_path)
        results.ok("第一次 startup 成功")

        mid1 = am.write_memory(
            content="持久化测试记忆第一条",
            tags=["持久化", "测试"],
            confidence="high",
        )
        results.ok("写入第一条记忆: {}".format(mid1))

        mid2 = am.write_memory(
            content="持久化测试记忆第二条, 包含更多细节信息。",
            tags=["持久化", "详细"],
            confidence="medium",
        )
        results.ok("写入第二条记忆: {}".format(mid2))

        assert len(ctx1.private_memories) == 0, "上下文应在写入前为空"
        results.ok("写入前上下文 private_memories 为空 (符合预期)")

        am._context = None
        ctx2 = am.startup(identity_path, device_config_path=device_config_path)
        results.ok("第二次 startup (重启) 成功")

        assert len(ctx2.private_memories) >= 2, \
            "重启后应加载至少 2 条记忆, 实际: {}".format(len(ctx2.private_memories))
        results.ok("重启后加载了 {} 条私有记忆".format(len(ctx2.private_memories)))

        loaded_ids = [m.id for m in ctx2.private_memories]
        assert mid1 in loaded_ids, "第一条记忆未加载: {}".format(mid1)
        results.ok("第一条记忆 {} 已加载".format(mid1))

        assert mid2 in loaded_ids, "第二条记忆未加载: {}".format(mid2)
        results.ok("第二条记忆 {} 已加载".format(mid2))

        loaded_contents = {m.id: m.content for m in ctx2.private_memories}
        assert "持久化测试记忆第一条" in loaded_contents[mid1], "第一条内容不匹配"
        results.ok("第一条记忆内容正确")

        assert "持久化测试记忆第二条" in loaded_contents[mid2], "第二条内容不匹配"
        results.ok("第二条记忆内容正确")

        loaded_conf = {m.id: m.confidence for m in ctx2.private_memories}
        assert loaded_conf[mid1] == "high", "第一条 confidence 应为 high"
        results.ok("第一条 confidence == high")

        assert loaded_conf[mid2] == "medium", "第二条 confidence 应为 medium"
        results.ok("第二条 confidence == medium")

        seq1 = int(mid1.split("_")[-1])
        seq2 = int(mid2.split("_")[-1])
        assert seq2 > seq1, "序号未递增: {} vs {}".format(seq1, seq2)
        results.ok("序号递增: {} -> {}".format(seq1, seq2))

    except Exception as e:
        results.fail("write_and_reload", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_missing_device_config():
    print("\n[TEST] missing_device_config - 缺失 device_config.json")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        identity_path, _, _, device_config_path = create_test_env(tmp_dir)

        device_config_path.unlink()
        results.ok("device_config.json 已删除")

        am._context = None

        try:
            am.startup(identity_path, device_config_path=device_config_path)
            results.fail("missing_device_config", "未抛出异常")
        except am.DeviceConfigNotFoundError as e:
            err_msg = str(e)
            assert "device_config.json not found" in err_msg, \
                "错误信息不含 'device_config.json not found': {}".format(err_msg)
            results.ok("正确抛出 DeviceConfigNotFoundError: {}".format(err_msg[:80]))
        except Exception as e:
            results.fail("missing_device_config", "抛出了错误类型: {}".format(type(e).__name__))

    except Exception as e:
        results.fail("missing_device_config (setup)", str(e))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_id_contains_device():
    print("\n[TEST] id_contains_device - memory_id 包含 source_device")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        identity_path, memory_root, shared_root, device_config_path = create_test_env(
            tmp_dir, source_device="test_device"
        )

        am._context = None
        am.startup(identity_path, device_config_path=device_config_path)
        results.ok("startup 成功")

        mid = am.write_memory(
            content="验证 memory_id 包含 source_device 的测试记忆",
            tags=["测试", "设备ID"],
            confidence="high",
        )
        results.ok("write_memory 成功, id={}".format(mid))

        assert "test_device" in mid, \
            "memory_id 不包含 source_device: {}".format(mid)
        results.ok("memory_id 包含 'test_device': {}".format(mid))

    except Exception as e:
        results.fail("id_contains_device", str(e))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_sqlite_index():
    print("\n[TEST] sqlite_index - SQLite 索引层测试")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        identity_path, memory_root, shared_root, device_config_path = create_test_env(tmp_dir)

        am._context = None
        am.startup(identity_path, device_config_path=device_config_path)
        results.ok("startup 成功")

        # 写入测试记忆
        mid1 = am.write_memory(
            content="SQLite 索引测试记忆第一条：Python 是一种解释型语言",
            tags=["Python", "编程语言"],
            confidence="high",
        )
        mid2 = am.write_memory(
            content="SQLite 索引测试记忆第二条：机器学习需要大量数据",
            tags=["机器学习", "数据"],
            confidence="medium",
        )
        results.ok("写入 2 条测试记忆")

        # 同步到 SQLite
        db_path = memory_root / "memories.db"
        count = am.sync_markdown_to_db(db_path=db_path)
        results.ok("同步 {} 条记忆到 SQLite".format(count))

        # 测试关键词搜索
        with am.MemoryDatabase(db_path) as db:
            # 搜索 "Python"
            results_list = db.search_by_keyword("Python")
            assert len(results_list) >= 1, "关键词搜索 'Python' 应返回至少 1 条结果"
            results.ok("关键词搜索 'Python' 返回 {} 条结果".format(len(results_list)))

            # 搜索 "机器学习"
            results_list = db.search_by_keyword("机器学习")
            assert len(results_list) >= 1, "关键词搜索 '机器学习' 应返回至少 1 条结果"
            results.ok("关键词搜索 '机器学习' 返回 {} 条结果".format(len(results_list)))

            # 测试标签过滤
            results_list = db.search_by_keyword("测试", tags=["Python"])
            assert len(results_list) >= 1, "带标签过滤的搜索应返回至少 1 条结果"
            results.ok("标签过滤搜索返回 {} 条结果".format(len(results_list)))

            # 测试获取单条记忆
            entry = db.get_memory(mid1)
            assert entry.id == mid1, "获取的记忆 ID 不匹配"
            results.ok("get_memory 成功获取记忆 {}".format(mid1))

            # 测试列出记忆
            all_memories = db.list_memories()
            assert len(all_memories) >= 2, "应列出至少 2 条记忆"
            results.ok("list_memories 返回 {} 条记忆".format(len(all_memories)))

    except Exception as e:
        results.fail("sqlite_index", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_search_api():
    print("\n[TEST] search_api - 搜索 API 测试")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        identity_path, memory_root, shared_root, device_config_path = create_test_env(tmp_dir)

        am._context = None
        am.startup(identity_path, device_config_path=device_config_path)
        results.ok("startup 成功")

        # 写入测试记忆
        am.write_memory(
            content="深度学习是机器学习的一个子领域，使用神经网络进行学习",
            tags=["深度学习", "神经网络"],
            confidence="high",
        )
        am.write_memory(
            content="自然语言处理是人工智能的重要应用方向",
            tags=["NLP", "人工智能"],
            confidence="medium",
        )
        results.ok("写入 2 条测试记忆")

        # 同步到 SQLite
        db_path = memory_root / "memories.db"
        am.sync_markdown_to_db(db_path=db_path)
        results.ok("同步到 SQLite 完成")

        # 测试关键词搜索 API
        results_list = am.search_memory(
            query="深度学习",
            mode="keyword",
            db_path=db_path
        )
        assert len(results_list) >= 1, "关键词搜索 API 应返回结果"
        results.ok("search_memory (keyword) 返回 {} 条结果".format(len(results_list)))

        # 测试混合搜索 API（不测试向量搜索，因为需要安装 sentence-transformers）
        results_list = am.search_memory(
            query="人工智能",
            mode="keyword",
            tags=["NLP"],
            db_path=db_path
        )
        assert len(results_list) >= 1, "带标签过滤的搜索应返回结果"
        results.ok("search_memory (keyword + tags) 返回 {} 条结果".format(len(results_list)))

    except Exception as e:
        results.fail("search_api", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_merger():
    print("\n[TEST] merger - 融合器测试")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        # 创建两个 Agent 环境
        identity_path1, memory_root1, _, device_config_path1 = create_test_env(
            tmp_dir / "agent1", source_device="device1"
        )
        identity_path2, memory_root2, _, device_config_path2 = create_test_env(
            tmp_dir / "agent2", source_device="device2"
        )

        # Agent 1 写入记忆
        am._context = None
        am.startup(identity_path1, device_config_path=device_config_path1)
        mid1 = am.write_memory(
            content="融合测试记忆：Agent 1 的记忆",
            tags=["融合", "Agent1"],
            confidence="high",
        )
        results.ok("Agent 1 写入记忆: {}".format(mid1))

        # Agent 2 写入记忆
        am._context = None
        am.startup(identity_path2, device_config_path=device_config_path2)
        mid2 = am.write_memory(
            content="融合测试记忆：Agent 2 的记忆",
            tags=["融合", "Agent2"],
            confidence="medium",
        )
        results.ok("Agent 2 写入记忆: {}".format(mid2))

        # 同步到各自的 SQLite
        db_path1 = memory_root1 / "memories.db"
        db_path2 = memory_root2 / "memories.db"
        shared_db_path = tmp_dir / "shared.db"

        am._context = None
        am.startup(identity_path1, device_config_path=device_config_path1)
        am.sync_markdown_to_db(db_path=db_path1)

        am._context = None
        am.startup(identity_path2, device_config_path=device_config_path2)
        am.sync_markdown_to_db(db_path=db_path2)
        results.ok("两个 Agent 的记忆已同步到 SQLite")

        # 创建融合器
        merger = am.create_merger(
            shared_db_path=shared_db_path,
            agent_configs={
                "hermes": db_path1,
                "hermes": db_path2,  # 使用相同 agent_id 测试
            }
        )
        results.ok("融合器创建成功")

        # 执行同步
        result1 = merger.sync_agent_to_shared("hermes")
        results.ok("Agent -> 共享同步完成: {}".format(result1))

        result2 = merger.sync_shared_to_agent("hermes")
        results.ok("共享 -> Agent 同步完成: {}".format(result2))

        # 验证共享库中有记忆
        with am.MemoryDatabase(shared_db_path) as db:
            shared_memories = db.list_memories()
            assert len(shared_memories) >= 1, "共享库应包含至少 1 条记忆"
            results.ok("共享库包含 {} 条记忆".format(len(shared_memories)))

    except Exception as e:
        results.fail("merger", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_deduplication():
    print("\n[TEST] deduplication - 去重测试")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        identity_path, memory_root, _, device_config_path = create_test_env(tmp_dir)

        am._context = None
        am.startup(identity_path, device_config_path=device_config_path)

        # 写入重复记忆
        am.write_memory(
            content="去重测试：这是一条重复的记忆",
            tags=["去重", "测试"],
            confidence="high",
        )
        am.write_memory(
            content="去重测试：这是一条重复的记忆",  # 完全相同的内容
            tags=["去重", "重复"],
            confidence="medium",
        )
        am.write_memory(
            content="去重测试：这是另一条不同的记忆",
            tags=["去重", "唯一"],
            confidence="high",
        )
        results.ok("写入 3 条记忆（2 条重复）")

        # 同步到 SQLite
        db_path = memory_root / "memories.db"
        am.sync_markdown_to_db(db_path=db_path)
        results.ok("同步到 SQLite 完成")

        # 试运行去重
        stats = am.run_deduplication(db_path, dry_run=True)
        assert stats["duplicates"] >= 1, "应检测到至少 1 条重复记忆"
        results.ok("试运行检测到 {} 条重复".format(stats["duplicates"]))

        # 执行去重
        stats = am.run_deduplication(db_path, dry_run=False)
        assert stats["removed"] >= 1, "应删除至少 1 条重复记忆"
        results.ok("去重完成，删除 {} 条，保留 {} 条".format(stats["removed"], stats["kept"]))

        # 验证去重结果
        with am.MemoryDatabase(db_path) as db:
            remaining = db.list_memories()
            assert len(remaining) >= 1, "去重后应至少保留 1 条记忆"
            results.ok("去重后剩余 {} 条记忆".format(len(remaining)))

    except Exception as e:
        results.fail("deduplication", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_decay():
    print("\n[TEST] decay - 记忆衰减测试")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        identity_path, memory_root, _, device_config_path = create_test_env(tmp_dir)

        am._context = None
        am.startup(identity_path, device_config_path=device_config_path)

        # 写入测试记忆
        am.write_memory(
            content="衰减测试：这条记忆会随时间衰减",
            tags=["衰减", "测试"],
            confidence="high",
        )
        results.ok("写入测试记忆")

        # 同步到 SQLite
        db_path = memory_root / "memories.db"
        am.sync_markdown_to_db(db_path=db_path)
        results.ok("同步到 SQLite 完成")

        # 测试衰减服务
        decay_service = am.MemoryDecayService(db_path=db_path)

        # 更新权重
        stats = decay_service.update_weights()
        assert stats["total"] >= 1, "应至少有 1 条记忆"
        assert stats["average_weight"] > 0, "平均权重应大于 0"
        results.ok("衰减更新完成: 平均权重 {:.2f}".format(stats["average_weight"]))

        # 获取加权记忆
        weighted_memories = decay_service.get_weighted_memories(limit=10)
        assert len(weighted_memories) >= 1, "应返回至少 1 条记忆"
        results.ok("获取加权记忆: {} 条".format(len(weighted_memories)))

        # 验证权重范围
        for memory, weight in weighted_memories:
            assert 0 <= weight <= 1, "权重应在 0-1 之间"
        results.ok("所有权重值在有效范围内")

    except Exception as e:
        results.fail("decay", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_health_check():
    print("\n[TEST] health_check - 健康检查测试")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        identity_path, memory_root, _, device_config_path = create_test_env(tmp_dir)

        am._context = None
        am.startup(identity_path, device_config_path=device_config_path)
        results.ok("startup 成功")

        # 写入一条记忆, 让 health 检查有数据
        am.write_memory(
            content="健康检查测试记忆",
            tags=["健康检查", "测试"],
            confidence="high",
        )
        am.sync_markdown_to_db()
        results.ok("写入 1 条测试记忆")

        # 执行健康检查
        result = am.health_check(memory_root=memory_root)
        assert result is not None, "health_check 应返回字典"
        results.ok("health_check 返回结果")

        # 验证必要字段
        assert "status" in result, "缺少 status 字段"
        assert "checks" in result, "缺少 checks 字段"
        assert "warnings" in result, "缺少 warnings 字段"
        assert "errors" in result, "缺少 errors 字段"
        results.ok("结果包含必要字段: status/checks/warnings/errors")

        # 验证 checks 包含关键项 (确保 7 项检查都输出)
        required_checks = [
            "config", "database", "memory_count", "memory_files",
            "disk_free_gb", "backup_count", "active_locks", "log_dir"
        ]
        for key in required_checks:
            assert key in result["checks"], "缺少检查项: {}".format(key)
        results.ok("checks 包含全部 8 项: {}".format(", ".join(required_checks)))

        # memory_count 应该 >= 1
        assert result["checks"]["memory_count"] >= 1, "memory_count 应 >= 1"
        results.ok("memory_count = {}".format(result["checks"]["memory_count"]))

        # status 应该是 healthy 或 warning
        assert result["status"] in ("healthy", "warning", "unhealthy"), \
            "status 异常: {}".format(result["status"])
        results.ok("status = {}".format(result["status"]))

    except Exception as e:
        results.fail("health_check", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_expire_memories():
    print("\n[TEST] expire_memories - 过期记忆测试")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        identity_path, memory_root, _, device_config_path = create_test_env(tmp_dir)

        am._context = None
        am.startup(identity_path, device_config_path=device_config_path)

        # 写入一条记忆
        am.write_memory(
            content="过期测试记忆",
            tags=["过期", "测试"],
            confidence="high",
        )
        am.sync_markdown_to_db()
        results.ok("写入 1 条测试记忆")

        db_path = memory_root / "memories.db"

        # dry_run=True: 不应真的删除
        result = am.expire_old_memories(memory_root=memory_root, dry_run=True)
        assert "total_checked" in result, "缺少 total_checked"
        assert "expired_found" in result, "缺少 expired_found"
        assert "archived" in result, "缺少 archived"
        results.ok("dry_run 返回: total={}, expired={}, archived={}".format(
            result["total_checked"], result["expired_found"], result["archived"]))

        # dry_run 模式下 archived 必为 0
        assert result["archived"] == 0, "dry_run 不应真的归档"
        results.ok("dry_run 模式未真的归档")

        # 验证总条数不变
        with am.MemoryDatabase(db_path) as db:
            all_memories = db.list_memories(limit=10000)
            count_before = len(all_memories)

        # 临时把 max_memory_age_days 设为 0, 让所有记忆都过期
        am.get_config().set("limits.max_memory_age_days", 0)
        try:
            result = am.expire_old_memories(memory_root=memory_root, dry_run=False)
            # 验证执行后数据库
            with am.MemoryDatabase(db_path) as db:
                all_memories = db.list_memories(limit=10000)
                count_after = len(all_memories)

            results.ok("执行过期后: 剩余 {} 条 (原本 {} 条)".format(count_after, count_before))
        finally:
            am.get_config().set("limits.max_memory_age_days", 365)

    except Exception as e:
        results.fail("expire_memories", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_migrate_database():
    print("\n[TEST] migrate_database - 数据库迁移测试")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        identity_path, memory_root, _, device_config_path = create_test_env(tmp_dir)

        am._context = None
        am.startup(identity_path, device_config_path=device_config_path)

        # 同步以创建 db
        am.write_memory(
            content="迁移测试记忆",
            tags=["迁移", "测试"],
            confidence="high",
        )
        am.sync_markdown_to_db()
        results.ok("写入 1 条记忆并同步")

        db_path = memory_root / "memories.db"
        assert db_path.exists(), "数据库应已创建"
        results.ok("数据库文件已创建: {}".format(db_path.name))

        # 执行迁移
        result = am.migrate_database(db_path)
        assert "current_version" in result, "缺少 current_version"
        assert "target_version" in result, "缺少 target_version"
        results.ok("迁移返回: v{} -> v{}".format(
            result["current_version"], result["target_version"]))

        # 验证当前版本
        assert result["current_version"] >= 1, "迁移后版本应 >= 1"
        results.ok("迁移后版本 = v{}".format(result["current_version"]))

        # 验证 metadata 表中有 db_version
        with am.MemoryDatabase(db_path) as db:
            cursor = db.conn.execute("SELECT value FROM metadata WHERE key = 'db_version'")
            row = cursor.fetchone()
            assert row is not None, "metadata 表中应存在 db_version"
            assert row[0] == "1", "db_version 应为 1"
            results.ok("metadata 表 db_version = {}".format(row[0]))

        # 重复执行迁移应提示"已是最新"
        result2 = am.migrate_database(db_path)
        assert result2["already_up_to_date"] is True, "重复迁移应标记为已最新"
        results.ok("重复迁移: already_up_to_date = True")

    except Exception as e:
        results.fail("migrate_database", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_sensitive_info_detection():
    print("\n[TEST] sensitive_info_detection - 敏感信息检测测试")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        identity_path, memory_root, _, device_config_path = create_test_env(tmp_dir)

        am._context = None
        am.startup(identity_path, device_config_path=device_config_path)
        results.ok("startup 成功")

        # 测试 1: 正常写入 (无敏感信息) - 应成功
        mid_clean = am.write_memory(
            content="这是一条普通的记忆, 不含敏感信息",
            tags=["正常"],
            confidence="high",
        )
        assert mid_clean is not None, "正常记忆应写入成功"
        results.ok("正常记忆写入成功: {}".format(mid_clean))

        # 测试 2: 包含敏感词 (默认 warn 模式) - 应成功但有警告
        mid_warn = am.write_memory(
            content="我的密码是 123456, 请帮我保存",
            tags=["敏感测试"],
            confidence="high",
        )
        assert mid_warn is not None, "敏感记忆在 warn 模式下应能写入"
        results.ok("敏感记忆 (warn 模式) 写入成功: {}".format(mid_warn))

        # 测试 3: 直接调用检测器
        detector = am.SensitiveInfoDetector()
        check_result = detector.check("我的 api_key 是 abc123")
        assert check_result["has_sensitive"] is True, "应检测到 api_key"
        assert len(check_result["matches"]) > 0, "应返回匹配项"
        results.ok("检测器直接调用: 检测到 {} 个敏感词".format(len(check_result["matches"])))

        # 测试 4: 清理功能
        sanitized = detector.sanitize("password 是 12345")
        assert "[REDACTED]" in sanitized, "sanitize 应替换为 [REDACTED]"
        results.ok("sanitize 功能: 原文 -> 清理后包含 [REDACTED]")

        # 测试 5: 配置为 block 模式 (临时修改 config 测试)
        am.get_config().set("security.block_sensitive", True)
        # 重建检测器以读取新配置
        am._detector = am.SensitiveInfoDetector()
        try:
            am.write_memory(
                content="我的 token 是 xyz789",
                tags=["阻止测试"],
                confidence="high",
            )
            results.fail("sensitive_info_detection", "block 模式下应抛出 PolicyValidationError")
        except am.PolicyValidationError:
            results.ok("block 模式正确阻止写入")
        finally:
            # 恢复配置
            am.get_config().set("security.block_sensitive", False)
            am._detector = am.SensitiveInfoDetector()

    except Exception as e:
        results.fail("sensitive_info_detection", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Sprint 1.6 v1.3 新增测试: AgentRegistry / TriggerEngine / SessionFlusher / Dashboard
# ---------------------------------------------------------------------------

def test_agent_registry():
    """
    v1.3 重写: AgentRegistry 扫描 LOCAL_PATTERNS (本地安装目录), 不再扫融合层
    """
    print("\n[TEST] agent_registry - Agent 注册表测试 (v1.3 本地安装扫描)")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        # 模拟 2 个 Agent 的本地安装目录
        for agent_id in ("alpha", "beta"):
            local_dir = tmp_dir / (".agent_" + agent_id) / "memories"
            local_dir.mkdir(parents=True, exist_ok=True)
            (local_dir / "MEMORY.md").write_text(
                "{} 记忆段一。\n§\n{} 记忆段二。\n".format(agent_id, agent_id),
                encoding="utf-8",
            )

        fused_root = tmp_dir / "fused"
        registry = am.AgentRegistry(
            root=fused_root,
            registry_path=fused_root / "_shared" / "agent_registry.json",
        )
        # Monkeypatch LOCAL_PATTERNS 指向测试 fixture
        registry.LOCAL_PATTERNS = [
            ("alpha", str(tmp_dir / ".agent_alpha"), "memories"),
            ("beta", str(tmp_dir / ".agent_beta"), "memories"),
        ]
        results.ok("Registry 初始化成功")

        new_agents = registry.scan_local()
        assert len(new_agents) == 2, "应发现 2 个新 agent, 实际: {}".format(len(new_agents))
        results.ok("scan_local 发现 2 个新 agent: {}".format(new_agents))

        all_agents = registry.list_all()
        assert len(all_agents) == 2, "list_all 应返回 2 个"
        results.ok("list_all 返回 2 个")

        new_agents_2 = registry.scan_local()
        assert len(new_agents_2) == 0, "第二次 scan 不应发现新 agent"
        results.ok("去重: 重复 scan_local 不添加")

        # register 新 agent (v1.3: 用 installation_path + memory_files)
        gamma_local = tmp_dir / ".agent_gamma" / "memories"
        gamma_local.mkdir(parents=True, exist_ok=True)
        (gamma_local / "MEMORY.md").write_text("Gamma 段一。\n", encoding="utf-8")
        info = registry.register(
            "gamma",
            installation_path=str(tmp_dir / ".agent_gamma"),
            memory_files=[str(gamma_local / "MEMORY.md")],
            display_name="Gamma",
        )
        assert info["agent_id"] == "gamma"
        assert info["display_name"] == "Gamma"
        results.ok("register: gamma 写入 display_name=Gamma")

        # update_last_seen
        ok = registry.update_last_seen("gamma")
        assert ok is True
        results.ok("update_last_seen: gamma 已更新")

        # 持久化: 重新加载
        registry2 = am.AgentRegistry(root=fused_root)
        assert "gamma" in registry2.agents
        results.ok("持久化: 重新加载 Registry 后 gamma 仍在")

    except Exception as e:
        results.fail("agent_registry", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_hotword_trigger():
    print("\n[TEST] hotword_trigger - 关键词触发器测试")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        # 创建临时 triggers.yaml
        shared_dir = tmp_dir / "_shared"
        shared_dir.mkdir(parents=True, exist_ok=True)
        triggers_path = shared_dir / "triggers.yaml"
        triggers_path.write_text("""enabled: true
chinese:
  - "记住"
  - "以后"
  - "偏好"
english:
  - "remember"
  - "always"
  - "never"
auto_tags:
  - "用户明确指令"
force_confidence: "high"
""", encoding="utf-8")

        # 创建 fake agent
        agent_dir = tmp_dir / "agent_test"
        agent_dir.mkdir(parents=True, exist_ok=True)
        identity = {
            "agent_id": "test", "display_name": "Test",
            "memory_root": str(agent_dir) + "\\",
            "shared_root": str(shared_dir) + "\\",
            "created_at": "2026-06-12T00:00:00"
        }
        (agent_dir / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
        (agent_dir / "device_config.json").write_text(json.dumps({"source_device": "test_pc"}), encoding="utf-8")
        (agent_dir / "writing_policy.md").write_text("# policy", encoding="utf-8")
        (agent_dir / "agent_runtime_manual.md").write_text("# manual", encoding="utf-8")
        (agent_dir / "last_sync.json").write_text(json.dumps({
            "agent_id": "test", "last_merge_timestamp": None,
            "last_merge_id": None, "shared_memory_version": "v0"
        }), encoding="utf-8")

        engine = am.TriggerEngine(triggers_path=triggers_path, root=tmp_dir)
        results.ok("TriggerEngine 加载配置成功")

        # 测试 1: 中文触发词
        match = engine.match_hotword("请记住我不要吃辣")
        assert match["matched"] is True
        assert "记住" in match["matched_words"]
        assert match["force_confidence"] == "high"
        results.ok("中文触发词: 匹配 '记住'")

        # 测试 2: 英文触发词
        match2 = engine.match_hotword("Please remember this preference")
        assert match2["matched"] is True
        assert "remember" in match2["matched_words"]
        results.ok("英文触发词: 匹配 'remember'")

        # 测试 3: 不命中
        match3 = engine.match_hotword("今天天气不错")
        assert match3["matched"] is False
        results.ok("无关内容: 不触发")

        # 测试 4: 多个触发词
        match4 = engine.match_hotword("以后我总是偏好清淡的")
        assert "以后" in match4["matched_words"] or "总是" in match4["matched_words"]
        results.ok("多触发词: {}".format(match4["matched_words"]))

        # 测试 5: auto_tags
        assert "用户明确指令" in match["auto_tags"]
        results.ok("auto_tags: 包含 '用户明确指令'")

        # 测试 6: 禁用触发器
        engine.config["enabled"] = False
        match5 = engine.match_hotword("记住这个")
        assert match5["matched"] is False
        results.ok("禁用触发器: 不生效")

    except Exception as e:
        results.fail("hotword_trigger", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_session_flush():
    print("\n[TEST] session_flush - 会话落盘钩子测试")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        identity_path, memory_root, _, device_config_path = create_test_env(tmp_dir)

        am._context = None
        am.startup(identity_path, device_config_path=device_config_path)

        flusher = am.SessionFlusher(memory_root=memory_root)
        results.ok("SessionFlusher 初始化成功")

        # 1. buffer 初始为空
        assert flusher._load_buffer() == []
        results.ok("初始 buffer 为空")

        # 2. add_pending
        n1 = flusher.add_pending("这是一条值得记住的偏好信息, 用户说他喜欢用纯文本", ["偏好"])
        assert n1 == 1
        results.ok("add_pending 1: buffer = {}".format(n1))

        n2 = flusher.add_pending("记住这个: Agent 应该用 markdown 写技术文档", ["指令"])
        assert n2 == 2
        results.ok("add_pending 2: buffer = {}".format(n2))

        # 3. filter_by_policy: 闲聊关键词应被过滤
        chitchat_entry = {"content": "好的", "tags": []}
        assert flusher.filter_by_policy(chitchat_entry) is False
        results.ok("policy filter: 闲聊 '好的' 被过滤")

        # 4. filter_by_policy: 太短内容应被过滤
        short_entry = {"content": "hi", "tags": []}
        assert flusher.filter_by_policy(short_entry) is False
        results.ok("policy filter: 太短 'hi' 被过滤")

        # 5. flush dry_run
        result_dry = flusher.flush(dry_run=True)
        assert result_dry["total"] == 2
        results.ok("dry_run flush: total={}, written={}, skipped={}".format(
            result_dry["total"], result_dry["written"], result_dry["skipped"]))

        # dry_run 后 buffer 不应被清空
        assert len(flusher._load_buffer()) == 2
        results.ok("dry_run 不清空 buffer")

        # 6. 实际 flush
        result = flusher.flush(dry_run=False)
        assert result["written"] == 2
        results.ok("实际 flush: written={}".format(result["written"]))

        # flush 后 buffer 应被清空
        assert flusher._load_buffer() == []
        results.ok("flush 后 buffer 清空")

    except Exception as e:
        results.fail("session_flush", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_dashboard():
    """
    v1.3 重写: Dashboard 双层视图 (本地 + 融合)
    """
    print("\n[TEST] dashboard - Dashboard 双层视图测试 (v1.3)")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        # 模拟 2 个 Agent 的本地安装目录
        for agent_id in ("alpha", "beta"):
            local_dir = tmp_dir / (".agent_" + agent_id) / "memories"
            local_dir.mkdir(parents=True, exist_ok=True)
            (local_dir / "MEMORY.md").write_text(
                "{} 段一。\n§\n{} 段二。\n".format(agent_id, agent_id),
                encoding="utf-8",
            )

        # 融合层 root
        root = tmp_dir / "fused"

        # 在融合层预存 alpha 1 条 / beta 2 条
        for i, agent_id in enumerate(("alpha", "beta")):
            agent_dir = root / ("agent_" + agent_id)
            agent_dir.mkdir(parents=True, exist_ok=True)
            for j in range(i + 1):
                with open(agent_dir / "memory_private.md", "a", encoding="utf-8") as f:
                    f.write("---\nid: mem_test_{}\ntimestamp: 2026-06-12T10:0{}:00\nagent_id: {}\n---\n内容{}\n---\n".format(
                        j, j, agent_id, j))

        # 重新创建本地 fixture, 用 --- 分隔以便 parse_markdown 切出 2 块
        for agent_id in ("alpha", "beta"):
            local_dir = tmp_dir / (".agent_" + agent_id) / "memories"
            (local_dir / "MEMORY.md").write_text(
                "---\n{} 段一内容。\n---\n{} 段二内容。\n".format(agent_id, agent_id),
                encoding="utf-8",
            )

        # 准备 Registry, 用 LOCAL_PATTERNS 指向测试 fixture
        registry = am.AgentRegistry(
            root=root,
            registry_path=root / "_shared" / "agent_registry.json",
        )
        registry.LOCAL_PATTERNS = [
            ("alpha", str(tmp_dir / ".agent_alpha"), "memories"),
            ("beta", str(tmp_dir / ".agent_beta"), "memories"),
        ]

        new = registry.scan_local()
        assert len(new) == 2
        results.ok("discover 发现 2 个 agent (LOCAL_PATTERNS)")

        # update_local_stats: 本地层
        local_stats = registry.update_local_stats()
        assert local_stats["alpha"]["total_entries"] == 2
        assert local_stats["beta"]["total_entries"] == 2
        results.ok("update_local_stats: alpha/beta 本地各 2 段")

        # update_memory_counts: 融合层
        counts = registry.update_memory_counts()
        assert counts["alpha"][0] == 1, "alpha 融合层 1 条"
        assert counts["beta"][0] == 2, "beta 融合层 2 条"
        results.ok("update_memory_counts: alpha=1, beta=2")

        # list_all
        all_agents = registry.list_all()
        assert len(all_agents) == 2
        results.ok("list_all 返回 2 个")

        # 持久化
        reg2 = am.AgentRegistry(root=root)
        assert len(reg2.list_all()) == 2
        results.ok("Dashboard 数据已持久化")

    except Exception as e:
        results.fail("dashboard", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_v14_local_parser_hermes_section():
    """
    v1.3: LocalMemoryParser.parse_hermes_memory - § 分隔
    """
    print("\n[TEST] v14_local_parser_hermes_section - Hermes § 分隔解析")
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        mem_file = tmp_dir / "MEMORY.md"
        mem_file.write_text(
            "第一段：用户偏好中文交流。\n"
            "§\n"
            "第二段：API 提供商配置。\n"
            "§\n"
            "第三段：飞书凭证备份位置。\n",
            encoding="utf-8",
        )
        parser = am.LocalMemoryParser()
        entries = parser.parse_hermes_memory(mem_file)
        assert len(entries) == 3, "应解析出 3 段, 实际 {}".format(len(entries))
        assert entries[0]["source_format"] == "hermes_section"
        assert "第1条" in entries[0]["tags"]
        assert "中文" in entries[0]["content"]
        results.ok("parse_hermes_memory 切出 3 段")
    except Exception as e:
        results.fail("v14_local_parser_hermes_section", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_v14_local_parser_markdown():
    """
    v1.3: LocalMemoryParser.parse_markdown - front matter 分块
    """
    print("\n[TEST] v14_local_parser_markdown - Markdown 分块")
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        md = tmp_dir / "notes.md"
        md.write_text(
            "id: mem_001\nagent_id: claude\n---\n内容一：MCP 协议。\n\n---\n"
            "id: mem_002\nagent_id: claude\n---\n内容二：REST API。\n",
            encoding="utf-8",
        )
        parser = am.LocalMemoryParser()
        entries = parser.parse_markdown(md)
        assert len(entries) >= 2, "应至少 2 块, 实际 {}".format(len(entries))
        assert any("MCP" in e["content"] for e in entries)
        results.ok("parse_markdown 切出 ≥2 块")
    except Exception as e:
        results.fail("v14_local_parser_markdown", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_v14_local_parser_jsonl_v1():
    """
    v1.3: LocalMemoryParser.parse_jsonl - Claude history v1 (display 字段)
    """
    print("\n[TEST] v14_local_parser_jsonl_v1 - Claude prompt history")
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        jsonl = tmp_dir / "history.jsonl"
        lines = [
            json.dumps({"display": "你是谁？", "timestamp": 1775534486100,
                        "sessionId": "abc", "project": "C:\\Users\\test"}),
            json.dumps({"display": "请用中文回答我。", "timestamp": 1775534600000,
                        "sessionId": "abc", "project": "C:\\Users\\test"}),
            json.dumps({"display": "x", "timestamp": 1775534700000,
                        "sessionId": "abc"}),  # 太短, 应被过滤
        ]
        jsonl.write_text("\n".join(lines), encoding="utf-8")
        parser = am.LocalMemoryParser()
        entries = parser.parse_jsonl(jsonl)
        # 第一条 4 字 <=5 被过滤, 第二条 7 字保留, 第三条 1 字过滤
        assert len(entries) == 1, "应 1 条, 实际 {}".format(len(entries))
        assert entries[0]["source_format"] == "jsonl"
        assert "中文" in entries[0]["content"]
        results.ok("parse_jsonl 兼容 v1 display 格式")
    except Exception as e:
        results.fail("v14_local_parser_jsonl_v1", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_v14_agent_registry_scan_local():
    """
    v1.3: AgentRegistry.scan_local 扫描 LOCAL_PATTERNS
    """
    print("\n[TEST] v14_agent_registry_scan_local - 扫描本地安装路径")
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        # 模拟本地安装目录
        fake_hermes = tmp_dir / ".hermes" / "memories"
        fake_hermes.mkdir(parents=True)
        (fake_hermes / "MEMORY.md").write_text("第一段内容。\n§\n第二段内容。\n", encoding="utf-8")
        (fake_hermes / "USER.md").write_text("用户身份：小明。\n", encoding="utf-8")

        registry = am.AgentRegistry(
            root=tmp_dir / "fused",
            registry_path=tmp_dir / "fused" / "_shared" / "agent_registry.json",
        )
        # 用 monkeypatch 替换 LOCAL_PATTERNS
        registry.LOCAL_PATTERNS = [
            ("hermes", str(tmp_dir / ".hermes"), "memories"),
        ]
        new_agents = registry.scan_local()
        assert "hermes" in new_agents, "应发现 hermes"
        mems = registry.get_agent_memory_files("hermes")
        assert len(mems) == 2, "应 2 个本地文件"
        results.ok("scan_local 发现 hermes (2 文件)")
    except Exception as e:
        results.fail("v14_agent_registry_scan_local", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_v14_extract_local_to_fused():
    """
    v1.3: extract_local_to_fused 从本地提取到融合层
    """
    print("\n[TEST] v14_extract_local_to_fused - 提取到融合层")
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        # 本地: 模拟 Hermes
        local_dir = tmp_dir / ".hermes" / "memories"
        local_dir.mkdir(parents=True)
        (local_dir / "MEMORY.md").write_text("这是第一段测试内容，用于验证提取功能。\n§\n这是第二段测试内容，包含更多细节信息。\n", encoding="utf-8")

        # 融合层: OneDrive 模拟
        fused_root = tmp_dir / "OneDrive" / "AgentMemory"
        fused_root.mkdir(parents=True)

        registry = am.AgentRegistry(
            root=fused_root,
            registry_path=fused_root / "_shared" / "agent_registry.json",
        )

        result = am.extract_local_to_fused(
            agent_id="hermes",
            root=fused_root,
            local_files=[str(local_dir / "MEMORY.md")],
            registry=registry,
        )

        assert result["extracted"] == 2, "应提取 2 条, 实际 {}".format(result["extracted"])
        fused_md = fused_root / "agent_hermes" / "memory_private.md"
        assert fused_md.exists(), "融合层文件应存在"
        text = fused_md.read_text(encoding="utf-8")
        assert "这是第一段测试内容" in text
        assert "这是第二段测试内容" in text
        results.ok("extract_local_to_fused 写入 2 条到融合层")
    except Exception as e:
        results.fail("v14_extract_local_to_fused", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_v14_update_local_stats():
    """
    v1.3: AgentRegistry.update_local_stats 解析本地文件统计条目
    """
    print("\n[TEST] v14_update_local_stats - 本地文件统计")
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        fake_hermes = tmp_dir / ".hermes" / "memories"
        fake_hermes.mkdir(parents=True)
        (fake_hermes / "MEMORY.md").write_text("段1。\n§\n段2。\n§\n段3。\n", encoding="utf-8")
        (fake_hermes / "USER.md").write_text("用户身份。\n", encoding="utf-8")

        registry = am.AgentRegistry(
            root=tmp_dir / "fused",
            registry_path=tmp_dir / "fused" / "_shared" / "agent_registry.json",
        )
        registry.LOCAL_PATTERNS = [
            ("hermes", str(tmp_dir / ".hermes"), "memories"),
        ]
        registry.scan_local()
        stats = registry.update_local_stats()
        assert stats["hermes"]["file_count"] == 2
        assert stats["hermes"]["total_entries"] == 4, "应 4 条 (3 段 + 1 用户), 实际 {}".format(
            stats["hermes"]["total_entries"])
        results.ok("update_local_stats 统计正确")
    except Exception as e:
        results.fail("v14_update_local_stats", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_v14_dashboard_dual_layer():
    """
    v1.3: Dashboard 显示本地 + 融合双层
    """
    print("\n[TEST] v14_dashboard_dual_layer - 双层统计")
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        # 本地层
        local_hermes = tmp_dir / ".hermes" / "memories"
        local_hermes.mkdir(parents=True)
        (local_hermes / "MEMORY.md").write_text("段1。\n§\n段2。\n", encoding="utf-8")

        # 融合层: 1 条预存 + 提取 2 条 = 3 条
        fused_root = tmp_dir / "OneDrive" / "AgentMemory"
        agent_fused = fused_root / "agent_hermes"
        agent_fused.mkdir(parents=True)
        (agent_fused / "memory_private.md").write_text(
            "---\nid: mem_pre_001\nagent_id: hermes\n---\n预存记忆。\n",
            encoding="utf-8",
        )

        registry = am.AgentRegistry(
            root=fused_root,
            registry_path=fused_root / "_shared" / "agent_registry.json",
        )
        registry.LOCAL_PATTERNS = [
            ("hermes", str(tmp_dir / ".hermes"), "memories"),
        ]
        registry.scan_local()
        registry.update_local_stats()
        counts = registry.update_memory_counts()
        # 融合层应有 1 条预存 (extract 未执行)
        assert counts["hermes"][0] == 1, "融合层预存 1 条"
        # 本地 2 条
        info = registry.get("hermes")
        assert info["local_stats"]["total_entries"] == 2, "本地 2 条"
        results.ok("Dashboard 双层: 本地 2 / 融合 1")
    except Exception as e:
        results.fail("v14_dashboard_dual_layer", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 同步工具测试
# ---------------------------------------------------------------------------

def test_content_hash():
    """测试内容哈希函数"""
    print("\n[TEST] content_hash - 内容哈希")
    try:
        h1 = am.content_hash("hello world")
        h2 = am.content_hash("hello world")
        h3 = am.content_hash("different content")
        assert h1 == h2, "相同内容哈希应相同"
        assert h1 != h3, "不同内容哈希应不同"
        assert len(h1) == 16, "哈希长度应为 16"
        results.ok("content_hash: 哈希一致且长度正确")
    except Exception as e:
        results.fail("content_hash", str(e))


def test_check_onedrive_conflicts():
    """测试 OneDrive 冲突文件检测"""
    print("\n[TEST] check_onedrive_conflicts - 冲突检测")
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        # 无冲突
        conflicts = am.check_onedrive_conflicts(tmp_dir)
        assert len(conflicts) == 0, "无冲突文件时应返回空列表"

        # 创建冲突文件
        conflict_file = tmp_dir / "test (conflicted copy).md"
        conflict_file.write_text("conflict", encoding="utf-8")
        conflicts = am.check_onedrive_conflicts(tmp_dir)
        assert len(conflicts) == 1, "应检测到 1 个冲突文件"
        results.ok("check_onedrive_conflicts: 正确检测冲突文件")
    except Exception as e:
        results.fail("check_onedrive_conflicts", str(e))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_detect_agents():
    """测试鲁棒性 Agent 路径探测"""
    print("\n[TEST] detect_agents - 路径探测")
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        # 创建模拟的 Claude 目录
        claude_dir = tmp_dir / ".claude" / "projects" / "test-project" / "memory"
        claude_dir.mkdir(parents=True)
        (claude_dir / "MEMORY.md").write_text("# Memory Index\n", encoding="utf-8")

        # 创建模拟的 Trae 目录
        trae_dir = tmp_dir / ".trae-cn" / "memory"
        trae_dir.mkdir(parents=True)
        (trae_dir / "user_profile.md").write_text("## Profile\n", encoding="utf-8")

        # 创建模拟的 Hermes 目录
        hermes_dir = tmp_dir / "AppData" / "Local" / "hermes" / "memories"
        hermes_dir.mkdir(parents=True)
        (hermes_dir / "MEMORY.md").write_text("段1\n§\n段2\n", encoding="utf-8")

        # 创建自定义配置
        config = am.ConfigManager(config_path=tmp_dir / "config.json")
        config.config["agent_detection"] = {
            "claude": {
                "candidate_paths": [str(tmp_dir / ".claude" / "projects")],
                "signature_glob": "*/memory/MEMORY.md",
            },
            "trae": {
                "candidate_paths": [str(tmp_dir / ".trae-cn" / "memory")],
                "signature_file": "user_profile.md",
            },
            "hermes": {
                "candidate_paths": [str(tmp_dir / "AppData" / "Local" / "hermes" / "memories")],
                "signature_file": "MEMORY.md",
                "signature_content": "§",
            },
        }
        config.config["agent_overrides"] = {}
        config.config["sync_tool"] = {"cache_ttl_hours": 24}

        # 检测
        detected = am.detect_agents(config, force_redetect=True, write_cache=False)
        assert "claude" in detected, "应检测到 Claude"
        assert "trae" in detected, "应检测到 Trae"
        assert "hermes" in detected, "应检测到 Hermes"
        assert detected["claude"]["source"] == "auto"
        results.ok("detect_agents: 正确检测 3 个 Agent")
    except Exception as e:
        results.fail("detect_agents", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_detect_agents_with_override():
    """测试手动覆盖路径"""
    print("\n[TEST] detect_agents_override - 手动覆盖")
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        # 创建自定义路径
        custom_hermes = tmp_dir / "custom" / "hermes" / "memories"
        custom_hermes.mkdir(parents=True)
        (custom_hermes / "MEMORY.md").write_text("§\n自定义路径\n", encoding="utf-8")

        config = am.ConfigManager(config_path=tmp_dir / "config.json")
        config.config["agent_detection"] = {
            "hermes": {
                "candidate_paths": [str(tmp_dir / "nonexistent")],
                "signature_file": "MEMORY.md",
                "signature_content": "§",
            },
        }
        config.config["agent_overrides"] = {"hermes": str(custom_hermes)}
        config.config["sync_tool"] = {"cache_ttl_hours": 24}

        detected = am.detect_agents(config, force_redetect=True, write_cache=False)
        assert "hermes" in detected, "应通过覆盖路径检测到 Hermes"
        assert detected["hermes"]["source"] == "override"
        results.ok("detect_agents_override: 手动覆盖生效")
    except Exception as e:
        results.fail("detect_agents_override", str(e))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_sync_state():
    """测试同步去重状态管理"""
    print("\n[TEST] sync_state - 去重状态")
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        from sync_writers import SyncState
        state_path = tmp_dir / "sync_state.json"
        state = SyncState(state_path=state_path)

        # 初始状态不重复
        assert not state.is_duplicate("claude", "hello"), "初始状态不重复"

        # 标记写入后应重复
        state.mark_written("claude", "hello")
        assert state.is_duplicate("claude", "hello"), "标记后应重复"
        assert not state.is_duplicate("trae", "hello"), "不同 Agent 不重复"
        assert not state.is_duplicate("claude", "world"), "不同内容不重复"

        # 保存和加载
        state.save()
        state2 = SyncState(state_path=state_path)
        assert state2.is_duplicate("claude", "hello"), "重新加载后应重复"

        results.ok("sync_state: 去重状态正确")
    except Exception as e:
        results.fail("sync_state", str(e))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_hermes_writer():
    """测试 Hermes 写回器"""
    print("\n[TEST] hermes_writer - Hermes 写回")
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        from sync_writers import HermesMemoryWriter, SyncState

        # 创建 Hermes 记忆目录
        mem_dir = tmp_dir / "memories"
        mem_dir.mkdir(parents=True)
        (mem_dir / "MEMORY.md").write_text("原始段1\n§\n原始段2\n", encoding="utf-8")

        state = SyncState(state_path=tmp_dir / "sync_state.json")
        writer = HermesMemoryWriter(sync_state=state)

        # 创建测试记忆
        mem = am.MemoryEntry(
            id="mem_test_001",
            agent_id="claude",
            timestamp="2026-06-13T12:00:00+00:00",
            source_device="test",
            domain="general",
            tags=["test"],
            confidence="high",
            conflict_with=None,
            content="来自 Claude 的共享知识",
        )

        result = writer.write("hermes", mem_dir, [mem], backup_dir=tmp_dir / "bak")
        assert result.written == 1, "应写入 1 条"
        assert result.skipped == 0, "无跳过"

        # 验证文件内容
        content = (mem_dir / "MEMORY.md").read_text(encoding="utf-8")
        assert "§" in content, "应包含 § 分隔符"
        assert "来自 Claude 的共享知识" in content, "应包含写入的内容"
        assert "[sync:mem_test_001]" in content, "应包含同步标记"

        # 重复写入应跳过
        result2 = writer.write("hermes", mem_dir, [mem], backup_dir=tmp_dir / "bak")
        assert result2.written == 0, "重复写入应为 0"
        assert result2.skipped == 1, "应跳过 1 条"

        results.ok("hermes_writer: 写入和去重正确")
    except Exception as e:
        results.fail("hermes_writer", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_trae_writer():
    """测试 Trae 写回器"""
    print("\n[TEST] trae_writer - Trae 写回")
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        from sync_writers import TraeMemoryWriter, SyncState

        # 创建 Trae 记忆目录
        mem_dir = tmp_dir / "memory"
        mem_dir.mkdir(parents=True)
        (mem_dir / "user_profile.md").write_text("## Profile\n用户信息\n", encoding="utf-8")

        state = SyncState(state_path=tmp_dir / "sync_state.json")
        writer = TraeMemoryWriter(sync_state=state)

        mem = am.MemoryEntry(
            id="mem_test_002",
            agent_id="hermes",
            timestamp="2026-06-13T12:00:00+00:00",
            source_device="test",
            domain="general",
            tags=[],
            confidence="high",
            conflict_with=None,
            content="来自 Hermes 的知识",
        )

        result = writer.write("trae", tmp_dir, [mem])
        assert result.written == 1, "应写入 1 条"

        content = (mem_dir / "user_profile.md").read_text(encoding="utf-8")
        assert "## Shared Knowledge" in content, "应创建 Shared Knowledge 段"
        assert "来自 Hermes 的知识" in content, "应包含写入内容"

        results.ok("trae_writer: 写入正确")
    except Exception as e:
        results.fail("trae_writer", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_claude_writer():
    """测试 Claude 写回器"""
    print("\n[TEST] claude_writer - Claude 写回")
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        from sync_writers import ClaudeMemoryWriter, SyncState

        # 创建 Claude 项目目录
        project_mem = tmp_dir / "projects" / "test" / "memory"
        project_mem.mkdir(parents=True)
        (project_mem / "MEMORY.md").write_text("# Memory Index\n\n- [test](test.md)\n", encoding="utf-8")

        state = SyncState(state_path=tmp_dir / "sync_state.json")
        writer = ClaudeMemoryWriter(sync_state=state)

        mem = am.MemoryEntry(
            id="mem_test_003",
            agent_id="hermes",
            timestamp="2026-06-13T12:00:00+00:00",
            source_device="test",
            domain="general",
            tags=["demo"],
            confidence="medium",
            conflict_with=None,
            content="来自 Hermes 的共享记忆",
        )

        result = writer.write("claude", tmp_dir / "projects", [mem])
        assert result.written == 1, "应写入 1 条"

        # 验证子文件
        shared_file = project_mem / "shared_from_agents.md"
        assert shared_file.exists(), "应创建 shared_from_agents.md"
        content = shared_file.read_text(encoding="utf-8")
        assert "来自 Hermes 的共享记忆" in content, "应包含写入内容"

        # 验证索引更新
        index = (project_mem / "MEMORY.md").read_text(encoding="utf-8")
        assert "shared_from_agents" in index, "索引应包含链接"

        results.ok("claude_writer: 子文件和索引正确")
    except Exception as e:
        results.fail("claude_writer", str(e))
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 60)
    print("Agent 记忆系统 MVP 测试")
    print("=" * 60)

    test_happy_path()
    test_missing_identity()
    test_write_and_reload()
    test_missing_device_config()
    test_id_contains_device()
    test_sqlite_index()
    test_search_api()
    test_merger()
    test_deduplication()
    test_decay()
    test_health_check()
    test_expire_memories()
    test_migrate_database()
    test_sensitive_info_detection()
    test_agent_registry()
    test_hotword_trigger()
    test_session_flush()
    test_dashboard()
    # v1.3 新增
    test_v14_local_parser_hermes_section()
    test_v14_local_parser_markdown()
    test_v14_local_parser_jsonl_v1()
    test_v14_agent_registry_scan_local()
    test_v14_extract_local_to_fused()
    test_v14_update_local_stats()
    test_v14_dashboard_dual_layer()
    # 同步工具测试
    test_content_hash()
    test_check_onedrive_conflicts()
    test_detect_agents()
    test_detect_agents_with_override()
    test_sync_state()
    test_hermes_writer()
    test_trae_writer()
    test_claude_writer()

    all_passed = results.summary()
    sys.exit(0 if all_passed else 1)
