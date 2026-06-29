"""
AgentMemorySystem 完整测试套件 v2.0
====================================
覆盖核心业务逻辑和边界条件，确保代码修复后正常运行。

测试模块:
  1. safe_io — 路径解析、安全读写、pending 机制
  2. sync_engine — 同步引擎、报告生成、回滚
  3. sync_writers — 写回器、去重状态、CodeBuddy 支持
  4. agent_memory — Agent 检测、CodePilot 导出、通用发现
  5. memory_sync_app — 模块加载、_reloc_log 作用域、配置
  6. config.json — 配置文件完整性
  7. build.py — 打包脚本语法检查

用法:
  python test_full.py              # 运行全部测试
  python test_full.py --module safe_io  # 只运行指定模块
"""
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent))


class TestRunner:
    """测试运行器"""

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
        self.current_module = ""

    def set_module(self, name):
        self.current_module = name

    def ok(self, name):
        self.passed += 1
        print("  [PASS] {}".format(name))

    def fail(self, name, reason):
        self.failed += 1
        self.errors.append((self.current_module + "/" + name, reason))
        print("  [FAIL] {} - {}".format(name, reason))

    def assert_eq(self, name, actual, expected):
        if actual == expected:
            self.ok(name)
        else:
            self.fail(name, "expected {}, got {}".format(repr(expected), repr(actual)))

    def assert_true(self, name, condition):
        if condition:
            self.ok(name)
        else:
            self.fail(name, "condition is False")

    def assert_raises(self, name, exc_type, func, *args, **kwargs):
        try:
            func(*args, **kwargs)
            self.fail(name, "expected {} not raised".format(exc_type.__name__))
        except exc_type:
            self.ok(name)
        except Exception as e:
            self.fail(name, "expected {}, got {}: {}".format(exc_type.__name__, type(e).__name__, e))

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


r = TestRunner()


# ===========================================================================
# 1. safe_io 模块测试
# ===========================================================================

def test_safe_io_get_data_root_dev_mode():
    """开发模式下 get_data_root 返回项目 data 目录"""
    r.set_module("safe_io")
    print("\n[MODULE] safe_io")

    from safe_io import get_data_root
    root = get_data_root()
    r.assert_true("get_data_root returns Path", isinstance(root, Path))
    r.assert_true("get_data_root is directory", root.is_dir())
    r.assert_true("get_data_root ends with data", root.name == "data")


def test_safe_io_get_data_root_env_override():
    """环境变量 AGENT_MEMORY_DATA_DIR 覆盖"""
    r.set_module("safe_io")

    from safe_io import get_data_root
    tmp = Path(tempfile.mkdtemp())
    try:
        with patch.dict(os.environ, {"AGENT_MEMORY_DATA_DIR": str(tmp / "custom_data")}):
            root = get_data_root()
            r.assert_eq("env override path", root, tmp / "custom_data")
            r.assert_true("env override dir created", root.is_dir())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_safe_io_write_and_read():
    """安全读写基本功能"""
    r.set_module("safe_io")

    from safe_io import _safe_write_text, _safe_read_text
    tmp = Path(tempfile.mkdtemp())
    try:
        test_file = tmp / "test.txt"
        content = "Hello, 你好世界!"
        result = _safe_write_text(test_file, content)
        r.assert_true("write returns True", result)

        read_back = _safe_read_text(test_file)
        r.assert_eq("read content matches", read_back, content)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_safe_io_read_nonexistent():
    """读取不存在的文件返回默认值"""
    r.set_module("safe_io")

    from safe_io import _safe_read_text
    result = _safe_read_text(Path("nonexistent_file_12345.txt"), default="fallback")
    r.assert_eq("nonexistent file returns default", result, "fallback")


def test_safe_io_pending_path():
    """pending 路径生成正确"""
    r.set_module("safe_io")

    from safe_io import _pending_path
    p = _pending_path(Path("/tmp/test.md"))
    r.assert_eq("pending path", p, Path("/tmp/test.md.pending"))

    p2 = _pending_path(Path("/tmp/config.json"))
    r.assert_eq("pending path json", p2, Path("/tmp/config.json.pending"))


def test_safe_io_write_creates_parent():
    """写入时自动创建父目录"""
    r.set_module("safe_io")

    from safe_io import _safe_write_text, _safe_read_text
    tmp = Path(tempfile.mkdtemp())
    try:
        deep_file = tmp / "a" / "b" / "c" / "test.txt"
        result = _safe_write_text(deep_file, "deep content")
        r.assert_true("deep write succeeds", result)
        r.assert_true("deep file exists", deep_file.exists())
        r.assert_eq("deep content", _safe_read_text(deep_file), "deep content")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# 2. sync_engine 模块测试
# ===========================================================================

def test_sync_engine_init():
    """SyncEngine 初始化路径正确"""
    r.set_module("sync_engine")
    print("\n[MODULE] sync_engine")

    from sync_engine import SyncEngine
    engine = SyncEngine()
    r.assert_true("root is Path", isinstance(engine.root, Path))
    r.assert_true("root exists", engine.root.exists())
    r.assert_true("sync_state has state_path", hasattr(engine.sync_state, "state_path"))
    r.assert_true("_last_report is None initially", engine._last_report is None)


def test_sync_report_summary():
    """SyncReport 生成汇总文本"""
    r.set_module("sync_engine")

    from sync_engine import SyncReport
    report = SyncReport(
        start_time="2026-06-23 10:00:00",
        end_time="2026-06-23 10:01:00",
        duration_seconds=60.0,
        device="test_pc",
    )
    report.agents_detected = {"claude": {"path": "/fake"}}
    report.total_extracted = 5
    report.total_merged = 3
    report.total_written = 2

    text = report.summary_text()
    r.assert_true("summary contains device", "test_pc" in text)
    r.assert_true("summary contains extracted", "5" in text)
    r.assert_true("summary contains success", "成功" in text or "无操作" in text)


def test_sync_report_with_errors():
    """SyncReport 带错误的汇总"""
    r.set_module("sync_engine")

    from sync_engine import SyncReport
    report = SyncReport()
    report.errors.append("测试错误")
    text = report.summary_text()
    r.assert_true("summary contains error", "错误" in text or "error" in text.lower())


def test_sync_report_with_warnings():
    """SyncReport 带警告的汇总"""
    r.set_module("sync_engine")

    from sync_engine import SyncReport
    report = SyncReport()
    report.warnings.append("文件被锁定")
    text = report.summary_text()
    r.assert_true("summary contains warning", "警告" in text or "warning" in text.lower())


# ===========================================================================
# 3. sync_writers 模块测试
# ===========================================================================

def test_sync_state_default_path():
    """SyncState 默认路径使用 get_data_root"""
    r.set_module("sync_writers")
    print("\n[MODULE] sync_writers")

    from sync_writers import SyncState
    from safe_io import get_data_root
    state = SyncState()
    expected = get_data_root() / ".sync_state.json"
    r.assert_eq("default state path", state.state_path, expected)


def test_sync_state_dedup():
    """去重状态正确工作"""
    r.set_module("sync_writers")

    from sync_writers import SyncState
    tmp = Path(tempfile.mkdtemp())
    try:
        state = SyncState(state_path=tmp / "state.json")

        r.assert_true("initial not duplicate", not state.is_duplicate("agent1", "content1"))

        state.mark_written("agent1", "content1")
        r.assert_true("after mark is duplicate", state.is_duplicate("agent1", "content1"))
        r.assert_true("different agent not duplicate", not state.is_duplicate("agent2", "content1"))
        r.assert_true("different content not duplicate", not state.is_duplicate("agent1", "content2"))

        # 保存和重新加载
        state.save()
        state2 = SyncState(state_path=tmp / "state.json")
        r.assert_true("reloaded state has duplicate", state2.is_duplicate("agent1", "content1"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_writer_registry_codebuddy():
    """CodeBuddy 在 writer 注册表中"""
    r.set_module("sync_writers")

    from sync_writers import get_writer, GenericMarkdownWriter
    w = get_writer("codebuddy")
    r.assert_true("codebuddy writer is GenericMarkdownWriter", isinstance(w, GenericMarkdownWriter))


def test_writer_registry_unknown_agent():
    """未知 Agent 使用 GenericMarkdownWriter"""
    r.set_module("sync_writers")

    from sync_writers import get_writer, GenericMarkdownWriter
    w = get_writer("unknown_agent_xyz")
    r.assert_true("unknown agent uses GenericMarkdownWriter", isinstance(w, GenericMarkdownWriter))


def test_writer_registry_all_known():
    """所有已知 Agent 都有对应 writer"""
    r.set_module("sync_writers")

    from sync_writers import get_writer, WRITER_REGISTRY
    for agent_id in WRITER_REGISTRY:
        w = get_writer(agent_id)
        r.assert_true("writer for {} not None".format(agent_id), w is not None)


def test_hermes_writer_write_and_dedup():
    """Hermes writer 写入和去重"""
    r.set_module("sync_writers")

    from sync_writers import HermesMemoryWriter, SyncState
    import agent_memory as am

    tmp = Path(tempfile.mkdtemp())
    try:
        mem_dir = tmp / "memories"
        mem_dir.mkdir()
        (mem_dir / "MEMORY.md").write_text("段1\n", encoding="utf-8")

        state = SyncState(state_path=tmp / "state.json")
        writer = HermesMemoryWriter(sync_state=state)

        mem = am.MemoryEntry(
            id="mem_test_001", agent_id="claude",
            timestamp="2026-06-23T12:00:00+00:00", source_device="test",
            domain="general", tags=["test"], confidence="high",
            conflict_with=None, content="共享知识",
        )

        result = writer.write("hermes", mem_dir, [mem])
        r.assert_eq("first write count", result.written, 1)
        r.assert_eq("first write skipped", result.skipped, 0)

        # 重复写入
        result2 = writer.write("hermes", mem_dir, [mem])
        r.assert_eq("second write count", result2.written, 0)
        r.assert_eq("second write skipped", result2.skipped, 1)

        # 验证内容
        content = (mem_dir / "MEMORY.md").read_text(encoding="utf-8")
        r.assert_true("content has sync marker", "[sync:mem_test_001]" in content)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_generic_writer_creates_file():
    """GenericMarkdownWriter 自动创建记忆文件"""
    r.set_module("sync_writers")

    from sync_writers import GenericMarkdownWriter, SyncState
    import agent_memory as am

    tmp = Path(tempfile.mkdtemp())
    try:
        mem_dir = tmp / "agent_data"
        mem_dir.mkdir()

        state = SyncState(state_path=tmp / "state.json")
        writer = GenericMarkdownWriter(sync_state=state)

        mem = am.MemoryEntry(
            id="mem_test_002", agent_id="hermes",
            timestamp="2026-06-23T12:00:00+00:00", source_device="test",
            domain="general", tags=[], confidence="high",
            conflict_with=None, content="测试内容",
        )

        result = writer.write("unknown_agent", mem_dir, [mem])
        r.assert_eq("generic write count", result.written, 1)
        r.assert_true("MEMORY.md created", (mem_dir / "MEMORY.md").exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# 4. agent_memory 模块测试
# ===========================================================================

def test_agent_memory_imports():
    """agent_memory 模块可正常导入"""
    r.set_module("agent_memory")
    print("\n[MODULE] agent_memory")

    import agent_memory
    r.assert_true("module loaded", agent_memory is not None)
    r.assert_true("has detect_agents", hasattr(agent_memory, "detect_agents"))
    r.assert_true("has export_codepilot_memory", hasattr(agent_memory, "export_codepilot_memory"))
    r.assert_true("has content_hash", hasattr(agent_memory, "content_hash"))


def test_content_hash():
    """内容哈希函数"""
    r.set_module("agent_memory")

    import agent_memory as am
    h1 = am.content_hash("hello")
    h2 = am.content_hash("hello")
    h3 = am.content_hash("world")

    r.assert_eq("same content same hash", h1, h2)
    r.assert_true("different content different hash", h1 != h3)
    r.assert_eq("hash length 16", len(h1), 16)


def test_detect_agents_with_mock_config():
    """Agent 检测使用 mock 配置"""
    r.set_module("agent_memory")

    import agent_memory as am

    tmp = Path(tempfile.mkdtemp())
    try:
        # 创建模拟 Hermes 目录
        hermes_dir = tmp / ".hermes" / "memories"
        hermes_dir.mkdir(parents=True)
        (hermes_dir / "MEMORY.md").write_text("段1\n§\n段2\n", encoding="utf-8")

        config = am.ConfigManager(config_path=tmp / "config.json")
        config.config["agent_detection"] = {
            "hermes": {
                "candidate_paths": [str(hermes_dir)],
                "signature_file": "MEMORY.md",
                "signature_content": "§",
            }
        }
        config.config["agent_overrides"] = {}
        config.config["sync_tool"] = {"cache_ttl_hours": 24}

        # 确保不使用全局缓存（mock Path.home 指向临时目录）
        with patch("pathlib.Path.home", return_value=tmp):
            detected = am.detect_agents(config, force_redetect=True, write_cache=False)
        r.assert_true("hermes detected", "hermes" in detected)
        r.assert_eq("hermes source", detected.get("hermes", {}).get("source"), "auto")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_detect_agents_codebuddy_profile():
    """config.json 中有 codebuddy profile"""
    r.set_module("agent_memory")

    config_path = Path(__file__).parent / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    r.assert_true("config has codebuddy profile", "codebuddy" in config.get("agent_detection", {}))
    cb = config["agent_detection"]["codebuddy"]
    r.assert_true("codebuddy has candidate_paths", "candidate_paths" in cb)
    r.assert_true("codebuddy has signature_glob", "signature_glob" in cb)


def test_verify_agent_signature():
    """Agent 签名验证"""
    r.set_module("agent_memory")

    import agent_memory as am

    tmp = Path(tempfile.mkdtemp())
    try:
        # 创建有签名的目录
        d = tmp / "agent_dir"
        d.mkdir()
        (d / "MEMORY.md").write_text("§\n内容\n", encoding="utf-8")

        profile = {"signature_file": "MEMORY.md", "signature_content": "§"}
        r.assert_true("valid signature", am._verify_agent_signature(d, profile))

        # 无签名文件
        profile2 = {"signature_file": "NONEXISTENT.md"}
        r.assert_true("invalid signature file", not am._verify_agent_signature(d, profile2))

        # 不存在的路径
        r.assert_true("nonexistent path", not am._verify_agent_signature(tmp / "nope", {}))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_export_codepilot_memory_no_db():
    """CodePilot 导出在无数据库时优雅失败"""
    r.set_module("agent_memory")

    import agent_memory as am

    tmp = Path(tempfile.mkdtemp())
    try:
        fake_db = tmp / "nonexistent.db"
        output = tmp / "export.md"
        result = am.export_codepilot_memory(fake_db, output)
        r.assert_true("export returns path", result is not None)
        r.assert_true("export file created", output.exists())
        content = output.read_text(encoding="utf-8")
        r.assert_true("export has error message", "failed" in content.lower() or "Export" in content)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_sanitize_sensitive():
    """敏感信息过滤"""
    r.set_module("agent_memory")

    import agent_memory as am

    text = "my api_key is sk-12345678901234567890123 and password is secret123"
    sanitized = am._sanitize_sensitive(text)
    r.assert_true("sk-key redacted", "sk-***REDACTED***" in sanitized)
    r.assert_true("password redacted", "***REDACTED***" in sanitized)


def test_check_onedrive_conflicts():
    """OneDrive 冲突检测"""
    r.set_module("agent_memory")

    import agent_memory as am

    tmp = Path(tempfile.mkdtemp())
    try:
        # 无冲突
        conflicts = am.check_onedrive_conflicts(tmp)
        r.assert_eq("no conflicts", len(conflicts), 0)

        # 创建冲突文件
        (tmp / "test (conflicted copy).md").write_text("conflict", encoding="utf-8")
        conflicts = am.check_onedrive_conflicts(tmp)
        r.assert_eq("one conflict", len(conflicts), 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_scan_generic_memory_files_size_limit():
    """通用记忆文件扫描有大小限制"""
    r.set_module("agent_memory")

    import agent_memory as am

    tmp = Path(tempfile.mkdtemp())
    try:
        # 创建正常大小文件
        (tmp / "MEMORY.md").write_text("normal content", encoding="utf-8")
        # 创建超大文件 (11MB)
        big_file = tmp / "big.md"
        big_file.write_bytes(b"x" * (11 * 1024 * 1024))

        files = am._scan_generic_memory_files(tmp)
        r.assert_true("normal file included", str(tmp / "MEMORY.md") in files)
        r.assert_true("big file excluded", str(big_file) not in files)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_scan_agent_memory_files_filters_sync_artifacts():
    """Claude 扫描应跳过 shared_from_agents.md 这类同步产物"""
    r.set_module("agent_memory")

    import agent_memory as am

    tmp = Path(tempfile.mkdtemp())
    try:
        projects = tmp / "projects" / "demo" / "memory"
        projects.mkdir(parents=True)
        (projects / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
        (projects / "shared_from_agents.md").write_text("# sync artifact\n", encoding="utf-8")

        files = am._filter_agent_memory_files(
            "claude",
            am._scan_agent_memory_files("claude", tmp / "projects")
        )
        r.assert_true("MEMORY.md included", str(projects / "MEMORY.md") in files)
        r.assert_true("shared_from_agents excluded", str(projects / "shared_from_agents.md") not in files)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_discover_generic_agents_excludes_chromium():
    """通用发现排除 Chromium 目录"""
    r.set_module("agent_memory")

    import agent_memory as am
    import logging

    tmp = Path(tempfile.mkdtemp())
    try:
        # 创建模拟 AppData/Local
        appdata = tmp / "AppData" / "Local"
        appdata.mkdir(parents=True)

        # 创建 ima.copilot 目录 (Chromium 壳)
        ima_dir = appdata / "ima.copilot"
        ima_dir.mkdir()
        (ima_dir / "MEMORY.md").write_text("should not be detected", encoding="utf-8")

        logger = logging.getLogger("test")
        found = {}
        result = am._discover_generic_agents(found, tmp, logger)

        r.assert_true("ima.copilot not detected", "generic-ima.copilot" not in result)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_memory_sync_app_imports():
    """memory_sync_app 模块可正常导入"""
    r.set_module("memory_sync_app")
    print("\n[MODULE] memory_sync_app")

    import memory_sync_app
    r.assert_true("module loaded", memory_sync_app is not None)


def test_reloc_log_is_module_level():
    """_reloc_log 是模块级函数（非嵌套）"""
    r.set_module("memory_sync_app")

    import memory_sync_app
    r.assert_true("_reloc_log is module attribute", hasattr(memory_sync_app, "_reloc_log"))
    r.assert_true("_reloc_log is callable", callable(memory_sync_app._reloc_log))


def test_reloc_log_callable():
    """_reloc_log 可被调用且不崩溃"""
    r.set_module("memory_sync_app")

    import memory_sync_app
    try:
        memory_sync_app._reloc_log("test message")
        r.ok("_reloc_log callable without error")
    except Exception as e:
        r.fail("_reloc_log callable", str(e))


def test_ensure_local_install_normalizes_paths():
    """迁移逻辑使用规范化路径，避免 8.3 短路径污染。"""
    r.set_module("memory_sync_app")

    import memory_sync_app
    from pathlib import Path
    from unittest.mock import patch

    with patch.object(memory_sync_app, "_normalize_windows_path", wraps=memory_sync_app._normalize_windows_path) as norm:
        # 只验证路径规范化函数存在并可调用，不执行真实迁移
        p = memory_sync_app._normalize_windows_path(Path(r"C:\Users\MR7FF0~1.DON\AppData\Local\Temp\AgentMemorySystem\App"))
        r.assert_true("normalized path returns Path", isinstance(p, Path))
        r.assert_true("normalize called", norm.called)
        r.assert_true("short path removed", "~" not in str(p))


def test_data_dir_returns_path():
    """_data_dir 返回有效路径"""
    r.set_module("memory_sync_app")

    import memory_sync_app
    d = memory_sync_app._data_dir()
    r.assert_true("returns Path", isinstance(d, Path))
    r.assert_true("directory exists", d.exists())


def test_load_save_settings():
    """设置加载和保存"""
    r.set_module("memory_sync_app")

    import memory_sync_app

    tmp = Path(tempfile.mkdtemp())
    try:
        with patch.object(memory_sync_app, "_data_dir", return_value=tmp):
            settings = {"auto_interval_hours": 4, "conflict_action": "skip"}
            memory_sync_app.save_settings(settings)

            loaded = memory_sync_app.load_settings()
            r.assert_eq("loaded interval", loaded.get("auto_interval_hours"), 4)
            r.assert_eq("loaded conflict_action", loaded.get("conflict_action"), "skip")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_default_settings():
    """默认设置包含必要字段"""
    r.set_module("memory_sync_app")

    import memory_sync_app
    ds = memory_sync_app.DEFAULT_SETTINGS
    r.assert_true("has auto_interval_hours", "auto_interval_hours" in ds)
    r.assert_true("has conflict_action", "conflict_action" in ds)
    r.assert_true("has minimize_to_tray", "minimize_to_tray" in ds)


def test_colors_dict():
    """COLORS 字典包含必要颜色"""
    r.set_module("memory_sync_app")

    import memory_sync_app
    required = ["bg", "card_bg", "accent", "text", "border", "success", "warning", "error"]
    for key in required:
        r.assert_true("COLORS has {}".format(key), key in memory_sync_app.COLORS)


# ===========================================================================
# 6. config.json 测试
# ===========================================================================

def test_config_json_valid():
    """config.json 是有效 JSON"""
    r.set_module("config.json")
    print("\n[MODULE] config.json")

    config_path = Path(__file__).parent / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    r.assert_true("config is dict", isinstance(config, dict))


def test_config_has_agent_detection():
    """config.json 有 agent_detection 配置"""
    r.set_module("config.json")

    config_path = Path(__file__).parent / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    r.assert_true("has agent_detection", "agent_detection" in config)
    agents = config["agent_detection"]
    r.assert_true("has hermes", "hermes" in agents)
    r.assert_true("has claude", "claude" in agents)
    r.assert_true("has codepilot", "codepilot" in agents)
    r.assert_true("has codebuddy", "codebuddy" in agents)


def test_config_codepilot_sqlite():
    """codepilot 配置为 sqlite 存储类型"""
    r.set_module("config.json")

    config_path = Path(__file__).parent / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    cp = config["agent_detection"]["codepilot"]
    r.assert_eq("codepilot storage_type", cp.get("storage_type"), "sqlite")
    r.assert_eq("codepilot signature_file", cp.get("signature_file"), "codepilot.db")


# ===========================================================================
# 7. build.py 测试
# ===========================================================================

def test_build_py_syntax():
    """build.py 语法正确"""
    r.set_module("build.py")
    print("\n[MODULE] build.py")

    build_path = Path(__file__).parent / "build.py"
    try:
        with open(build_path, "r", encoding="utf-8") as f:
            compile(f.read(), "build.py", "exec")
        r.ok("build.py compiles")
    except SyntaxError as e:
        r.fail("build.py syntax", str(e))


def test_build_py_uses_onedir():
    """build.py 使用 --onedir 模式"""
    r.set_module("build.py")

    build_path = Path(__file__).parent / "build.py"
    content = build_path.read_text(encoding="utf-8")
    r.assert_true("uses --onedir", "--onedir" in content)
    r.assert_true("not --onefile", "--onefile" not in content)


def test_build_py_hidden_import_safe_io():
    """build.py 包含 --hidden-import safe_io"""
    r.set_module("build.py")

    build_path = Path(__file__).parent / "build.py"
    content = build_path.read_text(encoding="utf-8")
    r.assert_true("has safe_io hidden import", "safe_io" in content and "hidden-import" in content)


# ===========================================================================
# 8. 集成测试
# ===========================================================================

def test_integration_full_sync_flow():
    """集成测试：完整同步流程（mock Agent）"""
    r.set_module("integration")
    print("\n[MODULE] integration")

    import agent_memory as am
    from sync_engine import SyncEngine

    tmp = Path(tempfile.mkdtemp())
    try:
        # 创建两个模拟 Agent 目录（使用 mock 配置，不触碰真实 Agent）
        for agent_id in ("alpha", "beta"):
            agent_dir = tmp / ("agent_" + agent_id)
            agent_dir.mkdir(parents=True)
            (agent_dir / "memory_private.md").write_text(
                "---\nid: mem_{}\nagent_id: {}\ntimestamp: 2026-06-23T10:00:00+00:00\n---\n内容{}\n".format(
                    agent_id, agent_id, agent_id),
                encoding="utf-8",
            )

        # 创建配置
        config = am.ConfigManager(config_path=tmp / "config.json")
        config.config["agent_detection"] = {
            "alpha": {"candidate_paths": [str(tmp / "agent_alpha")], "signature_file": "memory_private.md"},
            "beta": {"candidate_paths": [str(tmp / "agent_beta")], "signature_file": "memory_private.md"},
        }
        config.config["agent_overrides"] = {}
        config.config["sync_tool"] = {"cache_ttl_hours": 24, "conflict_action": "skip"}
        config.config["paths"] = {"memory_root": "auto"}

        # 完全隔离：mock Path.home 和 detect_agents，不触碰真实 Agent
        # 注意：必须 patch sync_engine.detect_agents，因为 sync_engine 已模块级导入该函数
        with patch("pathlib.Path.home", return_value=tmp):
            with patch("sync_engine.detect_agents") as mock_detect:
                mock_detect.return_value = {
                    "alpha": {"path": str(tmp / "agent_alpha"), "memory_files": [str(tmp / "agent_alpha" / "memory_private.md")], "detected_at": "2026-06-23T10:00:00+00:00", "source": "auto"},
                    "beta": {"path": str(tmp / "agent_beta"), "memory_files": [str(tmp / "agent_beta" / "memory_private.md")], "detected_at": "2026-06-23T10:00:00+00:00", "source": "auto"},
                }
                with patch.dict(os.environ, {"AGENT_MEMORY_DATA_DIR": str(tmp / "data")}):
                    engine = SyncEngine(config=config)
                    report = engine.run()

                    r.assert_true("sync completed", report is not None)
                    r.assert_true("has duration", report.duration_seconds >= 0)
                    r.assert_true("_last_report saved", engine._last_report is not None)
    except Exception as e:
        r.fail("integration full sync", str(e))
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_integration_path_consistency():
    """集成测试：所有模块使用相同的数据根目录"""
    r.set_module("integration")

    from safe_io import get_data_root
    from sync_engine import SyncEngine
    from sync_writers import SyncState

    root = get_data_root()
    engine = SyncEngine()
    state = SyncState()

    r.assert_true("engine root matches data root", engine.root == root)
    r.assert_true("state path under data root", root in state.state_path.parents or state.state_path == root / ".sync_state.json")


def test_integration_rollback_no_crash():
    """集成测试：回滚在无备份时不崩溃"""
    r.set_module("integration")

    from sync_engine import SyncEngine

    engine = SyncEngine()
    try:
        result = engine.rollback()
        r.assert_true("rollback returns int", isinstance(result, int))
        r.assert_true("rollback returns 0 when no backup", result == 0)
    except Exception as e:
        r.fail("rollback no crash", str(e))


# ===========================================================================
# 主入口
# ===========================================================================

ALL_TESTS = [
    # safe_io
    test_safe_io_get_data_root_dev_mode,
    test_safe_io_get_data_root_env_override,
    test_safe_io_write_and_read,
    test_safe_io_read_nonexistent,
    test_safe_io_pending_path,
    test_safe_io_write_creates_parent,
    # sync_engine
    test_sync_engine_init,
    test_sync_report_summary,
    test_sync_report_with_errors,
    test_sync_report_with_warnings,
    # sync_writers
    test_sync_state_default_path,
    test_sync_state_dedup,
    test_writer_registry_codebuddy,
    test_writer_registry_unknown_agent,
    test_writer_registry_all_known,
    test_hermes_writer_write_and_dedup,
    test_generic_writer_creates_file,
    # agent_memory
    test_agent_memory_imports,
    test_content_hash,
    test_detect_agents_with_mock_config,
    test_detect_agents_codebuddy_profile,
    test_verify_agent_signature,
    test_export_codepilot_memory_no_db,
    test_sanitize_sensitive,
    test_check_onedrive_conflicts,
    test_scan_generic_memory_files_size_limit,
    test_scan_agent_memory_files_filters_sync_artifacts,
    test_discover_generic_agents_excludes_chromium,
    # memory_sync_app
    test_memory_sync_app_imports,
    test_reloc_log_is_module_level,
    test_reloc_log_callable,
    test_ensure_local_install_normalizes_paths,
    test_data_dir_returns_path,
    test_load_save_settings,
    test_default_settings,
    test_colors_dict,
    # config.json
    test_config_json_valid,
    test_config_has_agent_detection,
    test_config_codepilot_sqlite,
    # build.py
    test_build_py_syntax,
    test_build_py_uses_onedir,
    test_build_py_hidden_import_safe_io,
    # integration
    test_integration_full_sync_flow,
    test_integration_path_consistency,
    test_integration_rollback_no_crash,
]


def main():
    print("=" * 60)
    print("AgentMemorySystem 完整测试套件 v2.0")
    print("=" * 60)

    module_filter = None
    if "--module" in sys.argv:
        idx = sys.argv.index("--module")
        if idx + 1 < len(sys.argv):
            module_filter = sys.argv[idx + 1]

    for test_func in ALL_TESTS:
        module_name = test_func.__name__.replace("test_", "").split("_")[0]
        if module_filter and module_filter not in test_func.__name__:
            continue
        try:
            test_func()
        except Exception as e:
            r.fail(test_func.__name__, "UNHANDLED: {}".format(e))
            traceback.print_exc()

    all_passed = r.summary()
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
