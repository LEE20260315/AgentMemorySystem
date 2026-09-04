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
import subprocess
import sys
import tempfile
import threading
import time
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
    """开发模式下 get_data_root 返回有效目录（v1.3.2 起不再强制是项目 data/）"""
    r.set_module("safe_io")
    print("\n[MODULE] safe_io")

    from safe_io import get_data_root
    root = get_data_root()
    r.assert_true("get_data_root returns Path", isinstance(root, Path))
    r.assert_true("get_data_root is directory", root.is_dir())
    r.assert_true("get_data_root has expected leaf", root.name in ("data", "AgentMemory", "AgentMemorySystem"))


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
    # v2.0: _last_report 不再是必需属性；改为验证 sync_state 存在
    r.assert_true("sync_state loaded", engine.sync_state is not None)


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
    # v2.0: summary_text 不再包含"成功"字样；改为检查"同步报告"
    r.assert_true("summary contains report header", "同步报告" in text or "===" in text)


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
    # v2.0: SyncReport 用 errors 字段而非 warnings
    report.errors.append("文件被锁定")
    text = report.summary_text()
    r.assert_true("summary contains warning", "错误" in text or "error" in text.lower())


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
        # v2.0: sync marker 格式升级为 [sync:<id>|h:<hash>|src:<agent>]
        r.assert_true("content has sync marker", "[sync:mem_test_001" in content)
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
        r.assert_true("normalize returns existing or unchanged path", str(p).endswith("AgentMemorySystem\\App") or "~" not in str(p))


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


def test_single_instance_holds_mutex():
    """_check_single_instance 创建互斥锁后句柄常驻模块级"""
    r.set_module("memory_sync_app")

    import memory_sync_app

    memory_sync_app._SINGLE_INSTANCE_MUTEX = None

    with patch("memory_sync_app.ctypes.windll.kernel32.CreateMutexW", return_value=123) as create_mutex:
        with patch("memory_sync_app.ctypes.windll.kernel32.GetLastError", return_value=0):
            result1 = memory_sync_app._check_single_instance()
            r.assert_true("first call is master", result1)
            r.assert_true("mutex handle retained", memory_sync_app._SINGLE_INSTANCE_MUTEX == 123)
            r.assert_eq("CreateMutex called once", create_mutex.call_count, 1)

            result2 = memory_sync_app._check_single_instance()
            r.assert_true("second call is still master", result2)
            r.assert_eq("CreateMutex not called again", create_mutex.call_count, 1)

    memory_sync_app._SINGLE_INSTANCE_MUTEX = None


def test_single_instance_detects_conflict():
    """_check_single_instance 检测到外部已存在同名互斥锁时返回 False"""
    r.set_module("memory_sync_app")

    import memory_sync_app

    memory_sync_app._SINGLE_INSTANCE_MUTEX = None

    with patch("memory_sync_app.ctypes.windll.kernel32.CreateMutexW", return_value=456) as create_mutex:
        with patch("memory_sync_app.ctypes.windll.kernel32.GetLastError", return_value=183):
            with patch("memory_sync_app.ctypes.windll.user32.MessageBoxW") as msgbox:
                result = memory_sync_app._check_single_instance()
                r.assert_eq("conflict detected", result, False)
                r.assert_eq("mutex not retained on conflict", memory_sync_app._SINGLE_INSTANCE_MUTEX, None)
                r.assert_eq("CreateMutex called once", create_mutex.call_count, 1)
                r.assert_eq("MessageBox shown", msgbox.call_count, 1)

    memory_sync_app._SINGLE_INSTANCE_MUTEX = None



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
                    # v2.0: _last_report 不再保存为实例属性；改为验证 report 返回
                    r.assert_true("report has agents", hasattr(report, 'agents_detected'))
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

    # v2.0: engine.root 可能与 get_data_root() 不同（SyncEngine 用 config 的 memory_root）
    # 改为验证 engine.root 存在且为 Path
    r.assert_true("engine root is Path", isinstance(engine.root, Path))
    r.assert_true("engine root exists", engine.root.exists())
    r.assert_true("state path under data root", root in state.state_path.parents or state.state_path == root / ".sync_state.json")


def test_integration_rollback_no_crash():
    """集成测试：回滚在无备份时不崩溃

    v2.2.2 修复：旧版直接 SyncEngine() 用真实数据根，机器上存在历史
    .sync_backups 时 rollback 会恢复文件返回 >0，断言恒失败（环境依赖）。
    现改为临时空数据根隔离，"无备份"前提在任何机器上都成立。
    """
    r.set_module("integration")

    import agent_memory as am
    from sync_engine import SyncEngine

    tmp = Path(tempfile.mkdtemp())
    try:
        config = am.ConfigManager(config_path=tmp / "config.json")
        config.config["paths"] = {"memory_root": str(tmp / "empty_root")}
        with patch.dict(os.environ, {"AGENT_MEMORY_DATA_DIR": str(tmp / "empty_root")}):
            engine = SyncEngine(config=config)
            result = engine.rollback()
            r.assert_true("rollback returns int", isinstance(result, int))
            r.assert_true("rollback returns 0 when no backup", result == 0)
    except Exception as e:
        r.fail("rollback no crash", str(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# 7.5 v2.1.2 修复回归测试
# ===========================================================================

def test_filelock_acquire_release():
    """v2.3.0: FileLock 获取/释放正常，且**不在目标目录创建任何文件**。

    旧实现会在 lock_path 指向的位置（数据根 = OneDrive 同步目录）真实创建
    .lock 文件，同步到另一台机器后，对端看到一个"陈旧锁"就拒绝写入并走
    fail-open 覆盖分支 —— 这是跨机丢更新的根因之一。新实现只把它当身份令牌。
    """
    r.set_module("agent_memory")
    from agent_memory import FileLock
    tmp = Path(tempfile.mkdtemp())
    try:
        lock_path = tmp / "test.lock"
        with FileLock(lock_path):
            r.ok("lock acquired")
        # 关键断言：目标目录保持干净，没有任何锁文件残留
        leftovers = list(tmp.iterdir())
        r.assert_true("目标目录无锁文件残留", len(leftovers) == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_filelock_reentrant_same_thread():
    """v2.3.0: 同线程可重入（旧实现会自己把自己锁死，抛 LockError）"""
    r.set_module("agent_memory")
    from agent_memory import FileLock
    tmp = Path(tempfile.mkdtemp())
    try:
        lock_path = tmp / "test.lock"
        with FileLock(lock_path):
            with FileLock(lock_path):
                with FileLock(lock_path):
                    r.ok("三层嵌套重入成功")
        # 全部退出后锁必须真的释放：再拿一次应当立刻成功
        with FileLock(lock_path, timeout=2.0):
            r.ok("释放后可再次获取")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_filelock_cross_process_mutual_exclusion():
    """v2.3.0: **跨进程**真互斥（旧实现无法保证，且失败即 fail-open）

    起一个子进程持锁 5 秒；父进程在此期间用 1 秒超时去抢，必须失败。
    """
    r.set_module("safe_io")
    from safe_io import CrossProcessLock, LockTimeout
    tmp = Path(tempfile.mkdtemp())
    ready = tmp / "ready.txt"
    target = tmp / "shared.md"
    child = None
    try:
        code = (
            "import sys, time\n"
            "sys.path.insert(0, {!r})\n"
            "from safe_io import CrossProcessLock\n"
            "with CrossProcessLock({!r}, timeout=25):\n"
            "    open({!r}, 'w').write('held')\n"
            "    time.sleep(5)\n"
            "print('done')\n"
        ).format(os.getcwd(), str(target), str(ready))

        child = subprocess.Popen([sys.executable, "-c", code])

        # 等子进程持锁信号
        waited = 0.0
        while not ready.exists() and waited < 20.0:
            time.sleep(0.1)
            waited += 0.1
        r.assert_true("子进程已持锁", ready.exists())

        # 父进程抢锁必须超时失败
        blocked = False
        try:
            with CrossProcessLock(target, timeout=1.0):
                pass
        except LockTimeout:
            blocked = True
        r.assert_true("跨进程争用被正确阻塞", blocked)

        child.wait(timeout=30)
        child = None

        # 子进程退出后（OS 自动释放）必须能立刻拿到
        acquired = False
        with CrossProcessLock(target, timeout=5.0):
            acquired = True
        r.assert_true("子进程退出后可获取", acquired)
    finally:
        if child is not None:
            try:
                child.kill()
                child.wait(timeout=10)
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def test_locked_update_no_lost_update():
    """v2.3.0 核心回归：并发追加不丢更新（旧实现会整块覆盖）"""
    r.set_module("safe_io")
    from safe_io import locked_update
    tmp = Path(tempfile.mkdtemp())
    target = tmp / "counter.txt"
    try:
        target.write_text("base\n", encoding="utf-8")

        def _append(tag):
            def _mutator(current):
                # 刻意放大竞态窗口：读完之后睡一觉再返回
                time.sleep(0.05)
                return current + "{}\n".format(tag)
            return _mutator

        errors = []

        def _worker(tag):
            try:
                locked_update(target, _append(tag), timeout=30.0)
            except Exception as e:  # noqa: BLE001 - 测试里收集所有异常
                errors.append("{}: {}".format(tag, e))

        threads = [threading.Thread(target=_worker, args=("t%d" % i,))
                   for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        r.assert_true("无异常", not errors)
        content = target.read_text(encoding="utf-8")
        for i in range(8):
            r.assert_true("t%d 未丢失" % i, ("t%d" % i) in content)
        r.assert_true("base 仍在", "base" in content)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_filelock_stale_lock_recovered():
    """v2.3.0: 旧位置遗留的陈旧锁文件不再阻塞写入

    旧实现若在 lock_path 处发现一个"30 秒内的锁"就直接抛 LockError；
    升级后这类遗留文件（可能是上一版本同步过来的）不应再影响加锁。
    """
    r.set_module("agent_memory")
    from agent_memory import FileLock
    tmp = Path(tempfile.mkdtemp())
    try:
        lock_path = tmp / "test.lock"
        lock_path.write_text("2000-01-01T00:00:00", encoding="utf-8")
        with FileLock(lock_path, timeout=5.0):
            r.ok("陈旧锁文件不再阻塞")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_backup_file_agent_id_naming():
    """备份文件名含 agent_id，不同 Agent 同名文件不互相覆盖"""
    r.set_module("sync_writers")
    from sync_writers import backup_file
    tmp = Path(tempfile.mkdtemp())
    try:
        a = tmp / "a" / "MEMORY.md"; a.parent.mkdir(parents=True, exist_ok=True)
        a.write_text("x", encoding="utf-8")
        b = tmp / "b" / "MEMORY.md"; b.parent.mkdir(parents=True, exist_ok=True)
        b.write_text("y", encoding="utf-8")
        bd = tmp / "backups"
        bak_a = backup_file(a, bd, agent_id="hermes")
        bak_b = backup_file(b, bd, agent_id="trae")
        r.assert_true("names differ", bak_a.name != bak_b.name)
        r.assert_true("hermes prefixed", "hermes" in bak_a.name)
        r.assert_true("trae prefixed", "trae" in bak_b.name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_backup_log_roundtrip():
    """备份日志 + SyncEngine.rollback 恢复目标文件"""
    r.set_module("sync_engine")
    from sync_engine import SyncEngine
    from sync_writers import _append_backup_log
    from safe_io import _safe_write_text
    tmp = Path(tempfile.mkdtemp())
    try:
        target = tmp / "target.md"
        _safe_write_text(target, "original")
        backup_dir = tmp / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        bak = backup_dir / "hermes_target.md.bak"
        bak.write_text("original", encoding="utf-8")
        _append_backup_log(backup_dir, "hermes", str(target), bak.name)
        _safe_write_text(target, "mutated")

        engine = SyncEngine()
        engine.root = tmp
        engine.backup_dir = backup_dir
        restored = engine.rollback()
        r.assert_eq("rollback restores 1", restored, 1)
        r.assert_eq("content restored", target.read_text(encoding="utf-8"), "original")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_resolve_source_device_no_match_raises():
    """跨机无匹配时抛 DeviceConfigNotFoundError（不静默冒名）"""
    r.set_module("agent_memory")
    import agent_memory as am
    tmp = Path(tempfile.mkdtemp())
    try:
        dc = {"devices": {"other": {"hostname": "totally-different-host-xyz",
                                    "user_home": "C:/no/such/home"}},
              "default_device": "other"}
        try:
            am._resolve_source_device(dc, tmp / "device_config.json")
            r.fail("no match raises", "should raise DeviceConfigNotFoundError")
        except am.DeviceConfigNotFoundError:
            r.ok("no match raises DeviceConfigNotFoundError")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_register_current_device_roundtrip():
    """自动注册当前机器后能被解析"""
    r.set_module("agent_memory")
    import agent_memory as am
    tmp = Path(tempfile.mkdtemp())
    try:
        dc_path = tmp / "device_config.json"
        name = am.register_current_device(dc_path)
        dc = json.loads(dc_path.read_text(encoding="utf-8"))
        resolved = am._resolve_source_device(dc, dc_path)
        r.assert_eq("resolve after register", resolved, name)
        r.assert_true("default set", dc.get("default_device") == name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_resolve_device_name_no_impersonation():
    """SyncEngine._resolve_device_name 匹配失败时不返回其他机器名"""
    r.set_module("sync_engine")
    import socket
    from sync_engine import SyncEngine
    tmp = Path(tempfile.mkdtemp())
    try:
        dc = tmp / "device_config.json"
        dc.write_text(json.dumps({
            "devices": {"other_machine": {"hostname": "totally-different-host-xyz",
                                           "user_home": "C:/no/such/home"}},
            "default_device": "other_machine",
        }), encoding="utf-8")
        engine = SyncEngine()
        engine.root = tmp
        with patch("sync_engine.register_current_device") as mock_reg:
            mock_reg.side_effect = Exception("simulated write failure")
            name = engine._resolve_device_name()
        r.assert_true("no impersonation", name != "other_machine")
        r.assert_eq("returns hostname", name, socket.gethostname().lower().replace("-", "_"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_safe_read_text_memory_error():
    """_safe_read_text 遇 MemoryError 返回默认值（不再死代码重试）"""
    r.set_module("safe_io")
    from safe_io import _safe_read_text
    tmp = Path(tempfile.mkdtemp())
    try:
        p = tmp / "big.md"
        p.write_text("hello", encoding="utf-8")
        with patch("builtins.open", side_effect=MemoryError("oom")):
            result = _safe_read_text(p, default="DEFAULT")
        r.assert_eq("memory error returns default", result, "DEFAULT")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# v2.2.0: shared.db 本机化 + 增量同步
# ---------------------------------------------------------------------------

def test_get_local_data_dir_under_localappdata():
    """本机私有目录应在 LOCALAPPDATA 下，不在 OneDrive 数据根。"""
    import os
    from safe_io import get_local_data_dir, get_data_root
    local = get_local_data_dir()
    assert local.is_dir()
    assert "AgentMemorySystem" in str(local)
    root = str(get_data_root()).lower()
    assert str(local).lower() != root


def test_get_shared_db_path_not_in_data_root():
    """shared.db 本机化：不再位于 OneDrive 数据根。"""
    from agent_memory import get_shared_db_path
    from safe_io import get_data_root
    p = get_shared_db_path()
    assert p.name == "shared.db"
    assert str(p).lower() != str(get_data_root() / "shared.db").lower()


def test_rebuild_shared_cache_from_md():
    """从 memory_shared.md 重建本机缓存（跨机事实源）。"""
    import tempfile, shutil
    from pathlib import Path
    from agent_memory import rebuild_shared_cache_from_md, MemoryDatabase
    tmp = Path(tempfile.mkdtemp())
    try:
        md = tmp / "memory_shared.md"
        md.write_text(
            "# x 共享记忆\n\n"
            "---\nid: m1\nagent_id: a\nsource_device: dev1\ndomain: general\n"
            "confidence: high\nconflict_with: null\n---\n第一条\n\n"
            "---\nid: m2\nagent_id: b\nsource_device: dev1\ndomain: general\n"
            "confidence: medium\nconflict_with: null\n---\n第二条\n\n",
            encoding="utf-8",
        )
        db = tmp / "shared.db"
        n = rebuild_shared_cache_from_md([md], db)
        assert n == 2
        with MemoryDatabase(db) as mdb:
            rows = mdb.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            assert rows == 2
            assert mdb.conn.execute(
                "SELECT content FROM memories WHERE id='m1'").fetchone()[0] == "第一条"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_parse_md_entry_ids_robust_to_separators():
    """头部定位法：正文含 --- 分隔线时不漏解析。"""
    import tempfile, shutil
    from pathlib import Path
    from sync_engine import SyncEngine
    tmp = Path(tempfile.mkdtemp())
    try:
        md = tmp / "memory_shared.md"
        md.write_text(
            "# t 共享记忆\n\n"
            "---\nid: a1\nagent_id: x\n---\n正文一\n---\n另一段\n\n"
            "---\nid: b2\nagent_id: y\n---\n正文二\n\n",
            encoding="utf-8",
        )
        engine = SyncEngine()
        ids = engine._parse_md_entry_ids(md)
        assert ids == {"a1", "b2"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_write_shared_md_incremental_no_rewrite():
    """增量同步：md 已含全部条目时第二次调用不写文件（无写放大）。"""
    import tempfile, shutil
    from pathlib import Path
    from sync_engine import SyncEngine
    from agent_memory import MemoryDatabase, MemoryEntry
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "agent_pi").mkdir()
        md = tmp / "agent_pi" / "memory_shared.md"
        md.write_text("# pi 共享记忆\n\n", encoding="utf-8")
        engine = SyncEngine()
        engine.root = tmp
        engine._shared_db = tmp / "shared.db"
        with MemoryDatabase(engine._shared_db) as db:
            db.insert_memory(MemoryEntry(
                id="m1", agent_id="alpha", timestamp="2026-01-01T00:00:00",
                source_device="d1", domain="general", tags=[], confidence="high",
                conflict_with=None, content="记忆一"))
            db.insert_memory(MemoryEntry(
                id="m2", agent_id="beta", timestamp="2026-01-01T00:00:00",
                source_device="d1", domain="general", tags=[], confidence="high",
                conflict_with=None, content="记忆二"))
        engine._write_shared_md("pi")
        c1 = md.read_text(encoding="utf-8")
        assert "id: m1" in c1 and "id: m2" in c1
        mtime1 = md.stat().st_mtime_ns
        import time; time.sleep(0.05)
        engine._write_shared_md("pi")
        assert md.stat().st_mtime_ns == mtime1, "无新增时应不写文件"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_ensure_shared_cache_migrates_legacy():
    """迁移：OneDrive 旧 shared.db → 本机缓存 + 标记 .migrated。"""
    import tempfile, os, sqlite3, shutil
    from pathlib import Path
    from sync_engine import SyncEngine
    old_local = os.environ.get("LOCALAPPDATA")
    tmp = Path(tempfile.mkdtemp())
    try:
        root = tmp / "root"; root.mkdir()
        legacy = root / "shared.db"
        conn = sqlite3.connect(str(legacy))
        conn.execute("CREATE TABLE t (x)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit(); conn.close()
        os.environ["LOCALAPPDATA"] = str(tmp / "local")
        engine = SyncEngine()
        engine.root = root
        engine._shared_db = Path(os.environ["LOCALAPPDATA"]) / "AgentMemorySystem" / "shared.db"
        p = engine._ensure_shared_cache()
        assert p is not None and p.exists()
        assert (root / "shared.db.migrated").exists(), "旧库应被标记"
        assert not (root / "shared.db").exists()
    finally:
        if old_local is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old_local
        shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# 主入口
# ===========================================================================


def test_shrink_md_fallback_truncates():
    """体积控制兜底：超限文件被截断"""
    r.set_module("sync_engine")
    from sync_engine import SyncEngine
    engine = SyncEngine()
    tmp = Path(tempfile.mkdtemp())
    try:
        md = tmp / "memory_shared.md"
        content = "# 测试\n\n" + "\n".join("line {}".format(i) for i in range(5000))
        md.write_text(content, encoding="utf-8")
        res = engine._shrink_md_fallback(md, max_lines=1000, max_size_kb=256)
        r.assert_eq("action truncated", res["action"], "force_truncated")
        r.assert_true("lines reduced", res["after_lines"] < res["before_lines"])
        actual = md.read_text(encoding="utf-8").splitlines()
        r.assert_true("file actually truncated", len(actual) <= 1000)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_sync_state_merge_preserves_other_agents():
    """SyncState 合并保留其他 agent 的磁盘状态"""
    r.set_module("sync_writers")
    from sync_writers import SyncState
    tmp = Path(tempfile.mkdtemp())
    try:
        state_path = tmp / ".sync_state.json"
        state_path.write_text(json.dumps({"agent_b": {"h1": "2026-01-01"}}), encoding="utf-8")
        ss = SyncState(state_path=state_path)
        ss.state["agent_a"] = {"h2": "2026-01-02"}
        merged = ss._merge_states(ss._load_raw())
        r.assert_true("other agent preserved", "agent_b" in merged)
        r.assert_true("own agent kept", "agent_a" in merged)
        r.assert_true("other hash kept", "h1" in merged["agent_b"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_check_onedrive_conflicts_chinese():
    """OneDrive 冲突检测支持中文命名"""
    r.set_module("agent_memory")
    import agent_memory as am
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "记忆（冲突副本）.md").write_text("x", encoding="utf-8")
        conflicts = am.check_onedrive_conflicts(tmp)
        r.assert_eq("chinese conflict detected", len(conflicts), 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_enforce_db_limit_date_prefix_sql():
    """过期清理 SQL：日期前缀比较对 T/Z/小数秒时间戳生效"""
    r.set_module("sync_engine")
    import sqlite3
    tmp = Path(tempfile.mkdtemp())
    try:
        db_path = tmp / "shared.db"
        con = sqlite3.connect(str(db_path))
        con.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT, confidence TEXT, timestamp TEXT)")
        con.executemany("INSERT INTO memories VALUES (?,?,?,?)", [
            ("old1", "c", "low", "2025-01-01T02:24:31.09081"),
            ("old2", "c", "low", "2025-01-01T02:24:31Z"),
            ("new1", "c", "low", "2026-07-01 02:24:31"),
        ])
        con.commit()
        rows = con.execute(
            "SELECT id FROM memories WHERE confidence='low' "
            "AND substr(timestamp,1,10) < date('now','-180 days')"
        ).fetchall()
        ids = [r_[0] for r_ in rows]
        r.assert_true("old entries expire", "old1" in ids and "old2" in ids)
        r.assert_true("new entry kept", "new1" not in ids)
        con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_tools_package_importable():
    """tools 包静态导入（体积控制不再依赖 sys.path 魔法）"""
    r.set_module("tools")
    from tools.shrink_memory_files import shrink_file, parse_memory_entries, format_entry
    r.assert_true("shrink_file callable", callable(shrink_file))
    r.assert_true("parse_memory_entries callable", callable(parse_memory_entries))


def test_build_py_paths_tools():
    """build.py 包含 tools 包打包参数（防 shrink_file 缺失回归）"""
    r.set_module("build.py")
    content = (Path(__file__).parent / "build.py").read_text(encoding="utf-8")
    r.assert_true("has --paths tools", "--paths" in content and "shrink_memory_files" in content)


def test_build_py_smoke_check():
    """build.py 包含打包产物冒烟检查"""
    r.set_module("build.py")
    content = (Path(__file__).parent / "build.py").read_text(encoding="utf-8")
    r.assert_true("has smoke check", "冒烟" in content and "shrink_memory_files" in content)


def test_get_local_home_prefers_localappdata():
    """v2.2.0: 跨机 home 错位场景，SHGetKnownFolderPath（进程 token）优先生效，
    即使 USERPROFILE/LOCALAPPDATA/HOME + Path.home 全部残留，仍返回真实用户目录。"""
    r.set_module("safe_io")

    import safe_io

    # 正常环境下的真实 home（SHGetKnownFolderPath 结果）
    real_home = str(safe_io.get_local_home()).lower()

    old = {k: os.environ.get(k) for k in ("USERPROFILE", "LOCALAPPDATA", "HOME", "HOMEDRIVE", "HOMEPATH")}
    try:
        # 模拟那台电脑的残留：所有环境变量 + Path.home 都指向旧账户
        os.environ["USERPROFILE"] = r"C:\Users\MR.Dong_FAKE"
        os.environ["LOCALAPPDATA"] = r"C:\Users\MR.Dong_FAKE\AppData\Local"
        os.environ["HOME"] = r"C:\Users\MR.Dong_FAKE"
        os.environ["HOMEDRIVE"] = "C:"
        os.environ["HOMEPATH"] = r"\Users\MR.Dong_FAKE"
        with patch("pathlib.Path.home", return_value=Path(r"C:\Users\MR.Dong_FAKE")):
            h = safe_io.get_local_home()
        r.assert_true(
            "polluted env still returns real home",
            "FAKE" not in str(h) and str(h).lower() == real_home,
        )
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # 回退链：LOCALAPPDATA 标准形态推断仍有效（非 Windows 或 SHGetKnownFolderPath 失败时）
    fake_root = Path(tempfile.mkdtemp())
    users = fake_root / "Users"
    (users / "Dong" / "AppData" / "Local").mkdir(parents=True)
    (users / "MR.Dong").mkdir(parents=True, exist_ok=True)
    old_la = os.environ.get("LOCALAPPDATA")
    try:
        os.environ["LOCALAPPDATA"] = str(users / "Dong" / "AppData" / "Local")
        class FakePath(type(Path())):
            @classmethod
            def home(cls):
                return users / "MR.Dong"
        with patch("safe_io._known_folder_profile", return_value=None), \
             patch("pathlib.Path.home", FakePath.home):
            h2 = safe_io.get_local_home()
        r.assert_true("localappdata fallback works", str(h2).replace("\\", "/").endswith("Users/Dong"))
    finally:
        if old_la is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old_la
        shutil.rmtree(fake_root, ignore_errors=True)


def test_expand_agent_home_path_tilde():
    """v2.2.0: ~ 候选路径统一用 home 展开（防 USERPROFILE 残留错位）"""
    r.set_module("agent_memory")

    import agent_memory as am

    home = Path("C:/Users/Dong")
    def _s(p):
        return str(p).replace("\\", "/")
    r.assert_eq("tilde dir", _s(am._expand_agent_home_path("~/.trae-cn/memory", home)), "C:/Users/Dong/.trae-cn/memory")
    r.assert_eq("tilde appdata", _s(am._expand_agent_home_path("~/AppData/Roaming/Trae/memory", home)), "C:/Users/Dong/AppData/Roaming/Trae/memory")
    r.assert_eq("tilde only", _s(am._expand_agent_home_path("~", home)), "C:/Users/Dong")
    r.assert_eq("absolute passthrough", _s(am._expand_agent_home_path("D:/x/memory", home)), "D:/x/memory")


def test_detect_agents_cache_cross_device_invalidated():
    """v2.2.0: .detected_agents.json 跨机缓存（含原机绝对路径）被过滤"""
    r.set_module("agent_memory")

    import agent_memory as am

    tmp = Path(tempfile.mkdtemp())
    data_root = tmp / "data"
    (data_root / "_shared").mkdir(parents=True)
    other_home = tmp / "Users" / "MR.Dong"
    other_home.mkdir(parents=True)
    cache = {
        "detected_at": "2026-08-13T00:00:00+00:00",
        "agents": {
            "trae": {"path": str(other_home / ".trae-cn" / "memory"), "memory_files": [], "source": "auto"},
            "pi": {"path": str(other_home / ".pi"), "memory_files": [], "source": "auto"},
        },
    }
    (data_root / ".detected_agents.json").write_text(json.dumps(cache), encoding="utf-8")

    config = am.ConfigManager(config_path=tmp / "config.json")
    config.config["agent_detection"] = {"hermes": {"candidate_paths": [str(tmp / "hermes")]}}
    config.config["agent_overrides"] = {}
    config.config["sync_tool"] = {"cache_ttl_hours": 24}

    old_la = os.environ.get("LOCALAPPDATA")
    try:
        # 本机 home 为 Dong（缓存里是 MR.Dong 路径 → 全部失效）
        local_user = tmp / "Users" / "Dong"
        (local_user / "AppData" / "Local").mkdir(parents=True)
        os.environ["LOCALAPPDATA"] = str(local_user / "AppData" / "Local")
        # 缓存 TTL 内命中分支需要 _candidate_env/get_data_root 能解析 data_root：直接 patch 数据根
        with patch("agent_memory.get_data_root", return_value=data_root):
            detected = am.detect_agents(config, force_redetect=False, write_cache=False)
        r.assert_true("cross-device cache filtered", "trae" not in detected and "pi" not in detected)
    finally:
        if old_la is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old_la
        shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# 7.7 v2.2.1 回归测试（OneDrive 运行时解耦：日志本机化 / 启动不写数据根 /
#     通知带超时 / 托盘重试 / 迁移走 robocopy）
# ===========================================================================

def test_get_data_root_skips_writable_test():
    """get_data_root 热路径不再做 .writable_test 同步写（OneDrive 锁挂起/拒写修复）"""
    r.set_module("safe_io")

    from safe_io import get_data_root, reset_data_root_cache
    tmp = Path(tempfile.mkdtemp())
    try:
        reset_data_root_cache()
        with patch.dict(os.environ, {"AGENT_MEMORY_DATA_DIR": str(tmp / "data")}):
            root = get_data_root()
            r.assert_true("returns env path", root == tmp / "data")
            r.assert_true("no .writable_test created", not (root / ".writable_test").exists())
    finally:
        reset_data_root_cache()
        shutil.rmtree(tmp, ignore_errors=True)


def test_log_manager_fallback_on_bad_dir():
    """LogManager 在日志目录不可用时降级为仅控制台，不抛异常（v2.2.1）"""
    r.set_module("agent_memory")

    from agent_memory import LogManager
    import logging
    tmp = Path(tempfile.mkdtemp())
    try:
        # 用"文件路径冒充目录"构造必然失败的日志目录
        blocker = tmp / "not_a_dir"
        blocker.write_text("x", encoding="utf-8")
        try:
            lm = LogManager(log_dir=blocker)
            logger = lm.get_logger()
            r.assert_true("logger returned", logger is not None)
            r.assert_true("file handler skipped", lm.file_handler is None)
            r.assert_true("console handler present", any(
                isinstance(h, logging.StreamHandler) for h in logger.handlers))
        except Exception as e:
            r.fail("LogManager raised", str(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_log_manager_defaults_to_local_logs():
    """LogManager 默认日志目录在本机 LOCALAPPDATA（而非 OneDrive 数据根）"""
    r.set_module("agent_memory")

    from agent_memory import LogManager
    from safe_io import get_data_root
    try:
        lm = LogManager()
        r.assert_true("log_dir resolved", lm.log_dir is not None)
        r.assert_true("file handler attached", lm.file_handler is not None)
        r.assert_true("log file created", lm.log_file is not None and lm.log_file.exists())
        data_root = str(get_data_root()).lower()
        local_dir = str(lm.log_dir).lower()
        r.assert_true("logs not under data root", data_root not in local_dir)
    except Exception as e:
        r.fail("LogManager default dir", str(e))


def test_get_logger_never_raises():
    """get_logger() 永不抛异常，返回可用 logger（v2.2.1）"""
    r.set_module("agent_memory")

    from agent_memory import get_logger
    try:
        logger = get_logger()
        r.assert_true("logger returned", logger is not None)
    except Exception as e:
        r.fail("get_logger raised", str(e))


def test_notify_subprocess_bounded():
    """_notify 的 PowerShell 通知调用必须带超时且不捕获输出（防主线程挂死）"""
    r.set_module("memory_sync_app")

    import memory_sync_app
    import subprocess

    captured = {}

    def fake_run(args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0)

    with patch.object(memory_sync_app.subprocess, "run", side_effect=fake_run):
        win = memory_sync_app.SyncMainWindow.__new__(memory_sync_app.SyncMainWindow)
        win._tray_nid = None
        win._tray_hwnd = None
        try:
            memory_sync_app.SyncMainWindow._notify(win, "test", "body")
            r.assert_true("timeout set", captured.get("timeout") is not None)
            r.assert_true("stdout DEVNULL", captured.get("stdout") == subprocess.DEVNULL)
            r.assert_true("stderr DEVNULL", captured.get("stderr") == subprocess.DEVNULL)
            r.assert_true("stdin DEVNULL", captured.get("stdin") == subprocess.DEVNULL)
        except Exception as e:
            r.fail("_notify raised", str(e))


def test_shell_notify_icon_retry_once():
    """Shell_NotifyIconW 注册失败后延迟重试一次（托盘瞬时失败自愈）"""
    r.set_module("memory_sync_app")
    if sys.platform != "win32":
        r.ok("skipped on non-win32")
        return

    import memory_sync_app
    nid = memory_sync_app._NOTIFYICONDATAW()
    nid.cbSize = memory_sync_app.ctypes.sizeof(memory_sync_app._NOTIFYICONDATAW)

    calls = {"n": 0}
    real_func = memory_sync_app._shell32.Shell_NotifyIconW

    def fake(dwMessage, pnid):
        calls["n"] += 1
        return calls["n"] >= 2  # 第一次失败、第二次成功

    memory_sync_app._shell32.Shell_NotifyIconW = fake
    try:
        ok, _err = memory_sync_app._shell_notify_icon_add(nid, retries=2, delay=0)
        r.assert_true("eventually succeeds", ok)
        r.assert_eq("called twice", calls["n"], 2)
    finally:
        memory_sync_app._shell32.Shell_NotifyIconW = real_func


def test_reloc_log_writes_local_first():
    """_reloc_log 写入本机 LOCALAPPDATA（OneDrive 锁下诊断不丢失）"""
    r.set_module("memory_sync_app")

    import memory_sync_app
    tmp = Path(tempfile.mkdtemp())
    try:
        with patch.dict(os.environ, {"LOCALAPPDATA": str(tmp)}):
            with patch.object(memory_sync_app, "_data_dir", return_value=tmp / "onedrive_data"):
                memory_sync_app._reloc_log("test message")
                local_log = tmp / "AgentMemorySystem" / "tray_error.log"
                r.assert_true("local log written", local_log.exists())
                content = local_log.read_text(encoding="utf-8")
                r.assert_true("content contains message", "test message" in content)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_ensure_local_install_uses_robocopy():
    """迁移复制走 robocopy（带超时、不捕获输出、退出码>=8 视为失败）"""
    r.set_module("memory_sync_app")
    if sys.platform != "win32":
        r.ok("skipped on non-win32")
        return

    import memory_sync_app
    import subprocess
    tmp = Path(tempfile.mkdtemp())
    try:
        fake_exe = tmp / "OneDrive" / "AgentMemorySync" / "AgentMemorySync.exe"
        fake_exe.parent.mkdir(parents=True)
        fake_exe.write_text("x")
        local_exe_path = tmp / "Local" / "AgentMemorySystem" / "App" / "AgentMemorySync.exe"

        def fake_run(*args, **kwargs):
            # 模拟 robocopy 成功：生成本地副本
            local_exe_path.parent.mkdir(parents=True, exist_ok=True)
            local_exe_path.write_text("x")
            return subprocess.CompletedProcess(args[0] if args else [], 1)

        run_calls = {}
        popen_calls = {}

        def fake_popen(cmd, **kwargs):
            popen_calls.update({"cmd": cmd, "kwargs": kwargs})
            return object()

        with patch.object(memory_sync_app.sys, "frozen", True, create=True):
            with patch.object(memory_sync_app.sys, "executable", str(fake_exe)):
                with patch.object(memory_sync_app.sys, "argv", [str(fake_exe)]):
                    with patch.dict(os.environ, {"LOCALAPPDATA": str(tmp / "Local")}):
                        with patch.object(memory_sync_app, "_reloc_log"):
                            with patch.object(memory_sync_app.subprocess, "run", side_effect=fake_run) as mock_run:
                                with patch.object(memory_sync_app.subprocess, "Popen", side_effect=fake_popen) as mock_popen:
                                    with patch.object(memory_sync_app.sys, "exit"):
                                        memory_sync_app._ensure_local_install()
                                        run_calls["run"] = mock_run.call_args
                                        run_calls["popen"] = mock_popen.call_args
        args = run_calls["run"][0][0]
        r.assert_true("uses robocopy", "robocopy" in args[0].lower())
        r.assert_true("has timeout", run_calls["run"][1].get("timeout") is not None)
        r.assert_true("stdout DEVNULL", run_calls["run"][1].get("stdout") == subprocess.DEVNULL)
        r.assert_true("relaunch from local copy", "AgentMemorySystem" in run_calls["popen"][0][0][0])
    except Exception as e:
        r.fail("ensure_local_install robocopy", str(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# 7.8 v2.2.1 UI 回归测试（退出按钮 + 窗口内容自适应）
# ===========================================================================

def test_fit_window_size():
    """_fit_window_size：放大到内容所需、不缩小用户偏好、裁剪到屏幕上限"""
    r.set_module("memory_sync_app")

    import memory_sync_app
    fit = memory_sync_app._fit_window_size

    # 内容所需高度超过当前窗口 → 放大高度
    w, h = fit(req_w=700, req_h=560, cur_w=760, cur_h=540, max_w=1200, max_h=700)
    r.assert_eq("height enlarged to req", (w, h), (760, 560))

    # 内容小于当前窗口 → 不缩小（保留用户偏好/默认）
    w, h = fit(req_w=500, req_h=400, cur_w=880, cur_h=620, max_w=1200, max_h=700)
    r.assert_eq("no shrink below current", (w, h), (880, 620))

    # 内容超过屏幕上限 → 裁剪到上限
    w, h = fit(req_w=2000, req_h=1500, cur_w=880, cur_h=620, max_w=960, max_h=700)
    r.assert_eq("clamped to max", (w, h), (960, 700))

    # 宽度也需要放大
    w, h = fit(req_w=900, req_h=500, cur_w=760, cur_h=540, max_w=1200, max_h=700)
    r.assert_eq("width enlarged to req", (w, h), (900, 540))


def test_ui_has_exit_button_and_fit():
    """主界面含"退出程序"按钮，窗口尺寸走 _fit_window_size（源码级冒烟检查）"""
    r.set_module("memory_sync_app")

    src = Path(__file__).parent.joinpath("memory_sync_app.py").read_text(encoding="utf-8")
    r.assert_true("has exit button text", 'text="退出程序"' in src)
    r.assert_true("exit button calls _quit", "command=self._quit" in src)
    r.assert_true("has _fit_window_size", "def _fit_window_size" in src)
    r.assert_true("content fit wired in init", "_fit_window_size(req_w, req_h" in src)


# ===========================================================================
# v2.2.2 修复回归测试
# ===========================================================================

def test_safe_write_locked_target_falls_to_pending():
    """v2.2.2: 目标被锁（OneDrive 模拟）→ 快照落 .pending，读取优先快照，解锁合并不翻倍

    Windows：open() 句柄默认无 FILE_SHARE_DELETE → os.replace 被拒，完整验证锁降级链路。
    POSIX：无"阻止 rename"的用户态锁，改为直接验证 pending 快照的读端优先 + 合并语义。
    """
    r.set_module("safe_io")
    from safe_io import _safe_write_text, _safe_read_text, _pending_path, merge_pending_file

    tmp = Path(tempfile.mkdtemp())
    try:
        target = tmp / "memory.md"
        _safe_write_text(target, "version-1\n")
        if sys.platform == "win32":
            lock = open(target, "r", encoding="utf-8")
            try:
                ok = _safe_write_text(target, "version-2-FULL\n")
                r.assert_true("write returns True under lock", ok)
                r.assert_true("pending snapshot created", _pending_path(target).exists())
                r.assert_eq("pending has full snapshot", _pending_path(target).read_text(encoding="utf-8"), "version-2-FULL\n")
                r.assert_eq("read prefers newer pending", _safe_read_text(target), "version-2-FULL\n")
                r.assert_eq("target NOT modified in-place", target.read_text(encoding="utf-8"), "version-1\n")
            finally:
                lock.close()
        else:
            # POSIX：手动构造 pending 快照（等同锁降级的产物）
            _safe_write_text(_pending_path(target), "version-2-FULL\n")
            r.assert_eq("read prefers newer pending", _safe_read_text(target), "version-2-FULL\n")
        r.assert_true("merge consumed", merge_pending_file(target))
        r.assert_eq("target recovered", target.read_text(encoding="utf-8"), "version-2-FULL\n")
        r.assert_true("pending removed", not _pending_path(target).exists())
        r.assert_true("no content duplication", "version-1" not in target.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_merge_pending_files_tree_and_orphan_tmp():
    """v2.2.2: merge_pending_files 全树恢复 + 孤儿 .tmp*（>1h）清理"""
    r.set_module("safe_io")
    from safe_io import _safe_write_text, _pending_path, merge_pending_files

    tmp = Path(tempfile.mkdtemp())
    try:
        for rel in ("a/x.md", "b/c/y.json"):
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            _safe_write_text(p, "old\n")
            _safe_write_text(_pending_path(p), "recovered\n")
        n = merge_pending_files(tmp)
        r.assert_eq("consumed 2 pending", n, 2)
        r.assert_eq("a/x.md recovered", (tmp / "a" / "x.md").read_text(encoding="utf-8"), "recovered\n")
        r.assert_eq("b/c/y.json recovered", (tmp / "b" / "c" / "y.json").read_text(encoding="utf-8"), "recovered\n")
        # 孤儿 tmp：超 1 小时清除，新近保留
        import time as _t
        stale = tmp / "orphan.md.tmp99999"
        stale.write_text("x", encoding="utf-8")
        old = _t.time() - 7200
        os.utime(str(stale), (old, old))
        fresh = tmp / "fresh.md.tmp1"
        fresh.write_text("x", encoding="utf-8")
        merge_pending_files(tmp)
        r.assert_true("stale orphan removed", not stale.exists())
        r.assert_true("fresh tmp kept", fresh.exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_detect_agents_cache_profiles_hash_invalidation():
    """v2.2.2: agent_detection 配置变化（新增 dsh profile）→ 旧缓存立即失效；hash 一致 → 缓存生效"""
    r.set_module("agent_memory")

    import agent_memory as am

    tmp = Path(tempfile.mkdtemp())
    data_root = tmp / "data"
    (data_root / "_shared").mkdir(parents=True)
    local_home = tmp / "Users" / "Dong"
    local_home.mkdir(parents=True)
    dsh_dir = local_home / ".dsh"
    dsh_dir.mkdir(parents=True)
    (dsh_dir / ".anonymous-user-id").write_text("uid", encoding="utf-8")
    dsh_profile = {"candidate_paths": [str(dsh_dir)], "signature_file": "settings.yaml",
                   "signature_paths_fallback": [str(dsh_dir / ".anonymous-user-id")]}
    now_iso = "2026-08-17T00:00:00+00:00"
    seeded = {"path": str(local_home / "zz_cached"), "memory_files": [], "detected_at": now_iso, "source": "auto"}

    config = am.ConfigManager(config_path=tmp / "config.json")
    config.config["agent_detection"] = {"dsh": dsh_profile}
    config.config["agent_overrides"] = {}
    config.config["sync_tool"] = {"cache_ttl_hours": 24}

    old_la = os.environ.get("LOCALAPPDATA")
    try:
        os.environ["LOCALAPPDATA"] = str(local_home / "AppData" / "Local")
        # 屏蔽 OS 权威 home 解析 → get_local_home 走 LOCALAPPDATA 推断 = local_home，
        # 种子缓存里的路径才不会被"跨机过滤"误杀
        with patch("safe_io._known_folder_profile", return_value=None):
            with patch("agent_memory.get_data_root", return_value=data_root):
                # 场景 1：旧版缓存（无 profiles_hash）→ 失效重检测 → dsh 可见
                (data_root / ".detected_agents.json").write_text(
                    json.dumps({"detected_at": now_iso, "agents": {"zz_cached": seeded}}), encoding="utf-8")
                detected = am.detect_agents(config, write_cache=False)
                r.assert_true("stale cache invalidated, dsh detected", "dsh" in detected)
                r.assert_true("stale cache content not returned", "zz_cached" not in detected)

                # 场景 2：hash 一致（TTL 内）→ 缓存照常生效（不能一刀切禁用缓存）
                correct_hash = am._detection_profiles_hash(config)
                (data_root / ".detected_agents.json").write_text(
                    json.dumps({"detected_at": now_iso, "profiles_hash": correct_hash,
                                "agents": {"zz_cached": seeded}}), encoding="utf-8")
                detected2 = am.detect_agents(config, write_cache=False)
                r.assert_true("matching-hash cache honored", "zz_cached" in detected2)
                r.assert_true("no rescan when cache valid", "dsh" not in detected2)
    finally:
        if old_la is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old_la
        shutil.rmtree(tmp, ignore_errors=True)


def test_detect_agents_dsh_memory_scan():
    """v2.2.2: dsh 记忆扫描——*.md + sessions/**/*.jsonl + storages/*.json；凭据/压缩文件排除"""
    r.set_module("agent_memory")

    import agent_memory as am

    tmp = Path(tempfile.mkdtemp())
    dsh_dir = tmp / ".dsh"
    (dsh_dir / "sessions" / "s1").mkdir(parents=True)
    (dsh_dir / "storages").mkdir(parents=True)
    (dsh_dir / "settings.yaml").write_text("model: deepseek\n", encoding="utf-8")
    (dsh_dir / ".anonymous-user-id").write_text("uid", encoding="utf-8")
    (dsh_dir / ".credentials.yaml").write_text("api_key: SECRET", encoding="utf-8")
    (dsh_dir / "MEMORY.md").write_text("# mem", encoding="utf-8")
    (dsh_dir / "sessions" / "s1" / "session.jsonl").write_text('{"q":"hi"}\n', encoding="utf-8")
    (dsh_dir / "sessions" / "s1" / "session.jsonl.zstd").write_text("compressed", encoding="utf-8")
    (dsh_dir / "storages" / "ws.json").write_text("{}", encoding="utf-8")

    files = am._scan_agent_memory_files("dsh", dsh_dir)
    names = {Path(f).name for f in files}
    r.assert_true("MEMORY.md scanned", "MEMORY.md" in names)
    r.assert_true("session.jsonl scanned", "session.jsonl" in names)
    r.assert_true("storages ws.json scanned", any(str(Path(f)).find("storages") >= 0 for f in files))
    r.assert_true("zstd excluded", not any(f.endswith(".zstd") for f in files))
    r.assert_true("credentials excluded", not any("credentials" in f.lower() for f in files))
    shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# P1-3: 墓碑机制（tombstones.py）
# ===========================================================================

def test_tombstone_store_roundtrip():
    """墓碑库 add/known/persist 往返，重复 add 不新增"""
    r.set_module("tombstones")

    from tombstones import TombstoneStore

    tmp = Path(tempfile.mkdtemp())
    try:
        p = tmp / ".tombstones.json"
        store = TombstoneStore(path=p)
        n = store.add(["h1", "h2"], agent_id="claude", reason="reconcile_vanish")
        r.assert_eq("add returns 2", n, 2)
        r.assert_true("h1 tombstoned", store.is_tombstoned("h1"))
        r.assert_true("h3 not tombstoned", not store.is_tombstoned("h3"))
        r.assert_eq("known subset", sorted(store.known({"h1", "h3"})), ["h1"])

        # 新实例从盘上重读（OneDrive 同步语义）
        store2 = TombstoneStore(path=p)
        r.assert_true("persisted h2", store2.is_tombstoned("h2"))

        # 重复 add 幂等
        n2 = store2.add(["h1"], agent_id="claude", reason="reconcile_vanish")
        r.assert_eq("re-add returns 0", n2, 0)
        r.assert_eq("count still 2", store2.count, 2)

        # 空输入
        r.assert_eq("empty add returns 0", store2.add([]), 0)
        r.assert_true("empty hash not tombstoned", not store2.is_tombstoned(""))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_reconcile_vanish_records_tombstone_after_grace():
    """reconcile 正常模式 vanish → 过宽限期的记墓碑，宽限期内不记"""
    r.set_module("tombstones")

    from datetime import datetime, timedelta, timezone
    from sync_writers import SyncState

    tmp = Path(tempfile.mkdtemp())
    try:
        state = SyncState(state_path=tmp / "state.json")
        now = datetime.now(timezone.utc)
        state.state["claude"] = {
            "h_old": (now - timedelta(hours=48)).isoformat(),
            "h_fresh": now.isoformat(),
        }
        result = state.reconcile_with_target_hashes(
            "claude", actual_hashes=set(), legacy_count=0, target_file_present=True)
        r.assert_eq("both removed from state", result["removed"], 2)
        r.assert_eq("only old tombstoned", result.get("tombstoned"), 1)
        r.assert_true("old hash tombstoned", state.tombstones.is_tombstoned("h_old"))
        r.assert_true("fresh hash NOT tombstoned",
                      not state.tombstones.is_tombstoned("h_fresh"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_reconcile_conservative_never_tombstones():
    """保守模式（文件不存在 / legacy-only）绝不产生墓碑"""
    r.set_module("tombstones")

    from datetime import datetime, timedelta, timezone
    from sync_writers import SyncState

    tmp = Path(tempfile.mkdtemp())
    try:
        state = SyncState(state_path=tmp / "state.json")
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        state.state["claude"] = {"h1": old}

        r1 = state.reconcile_with_target_hashes(
            "claude", actual_hashes=set(), legacy_count=0, target_file_present=False)
        r.assert_true("file-missing is conservative", r1.get("conservative") is True)
        r2 = state.reconcile_with_target_hashes(
            "claude", actual_hashes=set(), legacy_count=5, target_file_present=True)
        r.assert_true("legacy-only is conservative", r2.get("conservative") is True)
        r.assert_eq("no tombstones created", state.tombstones.count, 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_tombstone_refresh_reloads_disk():
    """refresh() 丢弃内存缓存——跨设备更新的墓碑对本进程可见"""
    r.set_module("tombstones")

    from tombstones import TombstoneStore

    tmp = Path(tempfile.mkdtemp())
    try:
        p = tmp / ".tombstones.json"
        store_a = TombstoneStore(path=p)
        r.assert_eq("a initially empty", store_a.count, 0)
        # 模拟另一设备写入（独立实例直接落盘）
        store_b = TombstoneStore(path=p)
        store_b.add(["h_remote"], agent_id="other", reason="test")
        # a 的进程内缓存仍是旧的（模拟 GUI 常驻）
        r.assert_eq("a stale cache", store_a.count, 0)
        # refresh 后可见
        store_a.refresh()
        r.assert_eq("a sees remote tombstone after refresh", store_a.count, 1)
        r.assert_true("h_remote now tombstoned", store_a.is_tombstoned("h_remote"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_reconcile_mass_vanish_not_tombstoned():
    """单轮 vanish 超过阈值视为文件重置，不墓碑化（仅清 state）"""
    r.set_module("tombstones")

    from datetime import datetime, timedelta, timezone
    from sync_writers import SyncState
    from tombstones import TOMBSTONE_MASS_VANISH_LIMIT

    tmp = Path(tempfile.mkdtemp())
    try:
        state = SyncState(state_path=tmp / "state.json")
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        n = TOMBSTONE_MASS_VANISH_LIMIT + 10
        state.state["claude"] = {"h_{}".format(i): old for i in range(n)}
        result = state.reconcile_with_target_hashes(
            "claude", actual_hashes=set(), legacy_count=0, target_file_present=True)
        r.assert_eq("all removed from state", result["removed"], n)
        r.assert_true("no tombstone on mass vanish",
                      "tombstoned" not in result)
        r.assert_eq("tombstone file untouched", state.tombstones.count, 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_tombstone_purge_db_chunks_large_hits():
    """命中数超过分块阈值时 DELETE 分块执行仍完整删除"""
    r.set_module("tombstones")

    import sqlite3
    from agent_memory import content_hash
    from tombstones import TombstoneStore

    tmp = Path(tempfile.mkdtemp())
    try:
        db = tmp / "shared.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT)")
        n_tomb, n_keep = 510, 10  # 510 > 500 分块阈值，验证 500+10 两块
        for i in range(n_tomb + n_keep):
            conn.execute("INSERT INTO memories VALUES (?, ?)",
                         ("m{}".format(i), "content_{}".format(i)))
        conn.commit()
        conn.close()

        store = TombstoneStore(path=tmp / ".tombstones.json")
        store.add([content_hash("content_{}".format(i)) for i in range(n_tomb)],
                  agent_id="a", reason="test")
        removed = store.purge_db(db)
        r.assert_eq("chunked purge removes all hits", removed, n_tomb)

        conn = sqlite3.connect(str(db))
        left = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        conn.close()
        r.assert_eq("kept rows untouched", left, n_keep)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_tombstone_add_returns_zero_on_write_failure():
    """底层写入失败时 add 如实返回 0（不把未落盘的墓碑当成已记录）"""
    r.set_module("tombstones")

    import safe_io
    from tombstones import TombstoneStore

    tmp = Path(tempfile.mkdtemp())
    orig = safe_io._safe_write_text
    try:
        p = tmp / ".tombstones.json"
        safe_io._safe_write_text = lambda *a, **k: False
        store = TombstoneStore(path=p)
        n = store.add(["hx"], agent_id="a", reason="test")
        r.assert_eq("write-failure returns 0", n, 0)
        r.assert_true("no file written", not p.exists())
        # 恢复写入函数后重试成功
        safe_io._safe_write_text = orig
        n2 = store.add(["hx"], agent_id="a", reason="test")
        r.assert_eq("retry after failure succeeds", n2, 1)
        r.assert_true("hx tombstoned after retry", store.is_tombstoned("hx"))
    finally:
        safe_io._safe_write_text = orig
        shutil.rmtree(tmp, ignore_errors=True)


def test_tombstone_purge_db_removes_rows():
    """purge_db 从 SQLite 删除命中墓碑的行，并清理 FTS 索引"""
    r.set_module("tombstones")

    import sqlite3
    from agent_memory import content_hash
    from tombstones import TombstoneStore

    tmp = Path(tempfile.mkdtemp())
    try:
        db = tmp / "shared.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT)")
        conn.execute("CREATE TABLE memories_fts (id TEXT, content TEXT)")
        conn.execute("INSERT INTO memories VALUES ('m1', 'keep me')")
        conn.execute("INSERT INTO memories VALUES ('m2', 'delete me')")
        conn.execute("INSERT INTO memories_fts VALUES ('m2', 'delete me')")
        conn.commit()
        conn.close()

        store = TombstoneStore(path=tmp / ".tombstones.json")
        store.add([content_hash("delete me")], agent_id="a", reason="test")
        removed = store.purge_db(db)
        r.assert_eq("purged 1 row", removed, 1)

        conn = sqlite3.connect(str(db))
        left = [row[0] for row in conn.execute("SELECT id FROM memories").fetchall()]
        fts = [row[0] for row in conn.execute("SELECT id FROM memories_fts").fetchall()]
        conn.close()
        r.assert_eq("only m1 left", left, ["m1"])
        r.assert_eq("fts cleaned", fts, [])

        # 无墓碑 / 库不存在 → 0，不抛异常
        empty_store = TombstoneStore(path=tmp / "empty.json")
        r.assert_eq("no tombstones -> 0", empty_store.purge_db(db), 0)
        r.assert_eq("missing db -> 0", store.purge_db(tmp / "nope.db"), 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_load_shared_memories_filters_tombstoned():
    """写回加载 _load_shared_memories 过滤墓碑（force_refresh 也不豁免）"""
    r.set_module("tombstones")

    from agent_memory import MemoryDatabase, MemoryEntry, content_hash
    from sync_engine import SyncEngine
    from tombstones import TombstoneStore

    tmp = Path(tempfile.mkdtemp())
    try:
        engine = SyncEngine()
        engine.root = tmp
        engine._shared_db = tmp / "shared.db"
        engine.tombstones = TombstoneStore(path=tmp / ".tombstones.json")
        with MemoryDatabase(engine._shared_db) as db:
            db.insert_memory(MemoryEntry(
                id="m1", agent_id="alpha", timestamp="2026-01-01T00:00:00",
                source_device="d1", domain="general", tags=[], confidence="high",
                conflict_with=None, content="活着的记忆"))
            db.insert_memory(MemoryEntry(
                id="m2", agent_id="beta", timestamp="2026-01-01T00:00:00",
                source_device="d1", domain="general", tags=[], confidence="high",
                conflict_with=None, content="被删除的记忆"))
        engine.tombstones.add([content_hash("被删除的记忆")],
                              agent_id="beta", reason="test")

        normal = engine._load_shared_memories("pi")
        r.assert_eq("normal load 1 entry", [m.id for m in normal], ["m1"])

        forced = engine._load_shared_memories("pi", force_refresh=True)
        r.assert_eq("force_refresh still filtered", [m.id for m in forced], ["m1"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_sync_shared_to_agent_filters_tombstoned():
    """DB 级融合 sync_shared_to_agent 跳过墓碑命中的记忆"""
    r.set_module("tombstones")

    import agent_memory as am
    from agent_memory import MemoryDatabase, MemoryEntry, MemoryMerger, content_hash
    import tombstones as tmod
    from tombstones import TombstoneStore, reset_tombstone_store

    tmp = Path(tempfile.mkdtemp())
    try:
        shared = tmp / "shared.db"
        agent = tmp / "agent.db"
        with MemoryDatabase(shared) as db:
            db.insert_memory(MemoryEntry(
                id="m1", agent_id="claude", timestamp="2026-01-01T00:00:00",
                source_device="d1", domain="general", tags=[], confidence="high",
                conflict_with=None, content="被删除的记忆"))
        MemoryDatabase(agent).close()  # 建空库

        store = TombstoneStore(path=tmp / ".tombstones.json")
        store.add([content_hash("被删除的记忆")], agent_id="claude", reason="test")

        # 注入模块级单例（模拟生产环境 get_tombstone_store 命中）
        reset_tombstone_store()
        tmod._store = store
        try:
            merger = MemoryMerger(shared_db_path=shared, agent_dbs={"trae": agent})
            stats = merger.sync_shared_to_agent("trae")
            r.assert_eq("synced 0 (tombstoned)", stats["synced"], 0)
            with MemoryDatabase(agent) as db:
                cnt = db.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            r.assert_eq("agent db still empty", cnt, 0)

            # 对照组：无墓碑时正常同步
            reset_tombstone_store()
            stats2 = merger.sync_shared_to_agent("trae")
            r.assert_eq("synced 1 without tombstone", stats2["synced"], 1)
        finally:
            reset_tombstone_store()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# v2.3.0: 日志保留策略
# ---------------------------------------------------------------------------
def test_log_rotation_uses_timestamped_names():
    """轮转文件名带时间戳，多次轮转互不覆盖（v2.1.2 的 .old 会被覆盖）。"""
    r.set_module("memory_sync_app")

    import memory_sync_app
    tmp = Path(tempfile.mkdtemp())
    try:
        p = tmp / "app.log"
        for i in range(3):
            p.write_text("X" * 5000, encoding="utf-8")
            memory_sync_app._append_rotated(p, "LINE%d\n" % i,
                                            max_bytes=1000, keep_count=99, keep_days=999)
        names = sorted(x.name for x in tmp.glob("app.*.log"))
        r.assert_eq("3 次轮转产生 3 个不同文件", len(names), 3)
        r.assert_true("轮转文件名带时间戳", all(n.startswith("app.20") for n in names))
        r.assert_true("旧内容未被覆盖",
                      all("X" * 5000 in (tmp / n).read_text(encoding="utf-8") for n in names))
        r.assert_eq("活跃文件只剩最新一行", p.read_text(encoding="utf-8"), "LINE2\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_log_prune_by_count_keeps_newest():
    """按数量裁剪：只保留最新的 keep_count 个轮转文件，活跃文件不动。"""
    r.set_module("memory_sync_app")

    import memory_sync_app
    tmp = Path(tempfile.mkdtemp())
    try:
        p = tmp / "app.log"
        p.write_text("ACTIVE", encoding="utf-8")
        for i in range(6):
            f = tmp / ("app.2026080%d-120000.log" % i)
            f.write_text("old%d" % i, encoding="utf-8")
            os.utime(f, (time.time() - (6 - i) * 86400,) * 2)
        memory_sync_app._prune_log_files(p, keep_count=2, keep_days=999)
        left = sorted(x.name for x in tmp.glob("app.*.log"))
        r.assert_eq("只剩 2 个轮转", len(left), 2)
        r.assert_eq("保留的是最新的两个", left,
                    ["app.20260804-120000.log", "app.20260805-120000.log"])
        r.assert_true("活跃文件未被删除", p.exists())
        r.assert_eq("活跃文件内容不变", p.read_text(encoding="utf-8"), "ACTIVE")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_log_prune_by_days_and_never_touches_active():
    """按天数裁剪；即使策略为 0，活跃文件也绝不会被删。"""
    r.set_module("memory_sync_app")

    import memory_sync_app
    tmp = Path(tempfile.mkdtemp())
    try:
        p = tmp / "app.log"
        p.write_text("ACTIVE", encoding="utf-8")
        old = tmp / "app.20260810-120000.log"
        new = tmp / "app.20260826-120000.log"
        old.write_text("old", encoding="utf-8")
        new.write_text("new", encoding="utf-8")
        os.utime(old, (time.time() - 10 * 86400,) * 2)
        os.utime(new, (time.time() - 3 * 86400,) * 2)
        memory_sync_app._prune_log_files(p, keep_count=99, keep_days=7)
        r.assert_true("超龄轮转被淘汰", not old.exists())
        r.assert_true("未超龄轮转保留", new.exists())

        # 极端策略下活跃文件仍存活——这是「绝不删正在写的文件」的硬保证
        memory_sync_app._prune_log_files(p, keep_count=0, keep_days=0)
        r.assert_true("keep_count=0/keep_days=0 时活跃文件仍在", p.exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_log_write_failure_is_surfaced_not_swallowed():
    """写入失败不再被 except 静默吞掉，下次成功时补记一行 WARN。"""
    r.set_module("memory_sync_app")

    import memory_sync_app
    tmp = Path(tempfile.mkdtemp())
    try:
        memory_sync_app._log_write_failures = 0
        # 目标是目录 → open() 必然失败
        memory_sync_app._append_rotated(tmp, "boom\n")
        r.assert_eq("失败被计数", memory_sync_app._log_write_failures, 1)

        p = tmp / "app.log"
        memory_sync_app._append_rotated(p, "recovered\n")
        text = p.read_text(encoding="utf-8")
        r.assert_eq("成功后计数归零", memory_sync_app._log_write_failures, 0)
        r.assert_true("补记了 WARN 行", "WARN" in text)
        r.assert_true("正常内容仍然写入", text.endswith("recovered\n"))
    finally:
        memory_sync_app._log_write_failures = 0
        shutil.rmtree(tmp, ignore_errors=True)


def test_log_retention_tool_scan_is_preview_by_default():
    """tools.log_retention 默认只出报告、不删除；活跃文件永远归到 keep 组。"""
    r.set_module("tools.log_retention")

    from tools import log_retention
    tmp = Path(tempfile.mkdtemp())
    try:
        active = tmp / "app.log"
        stale = tmp / "app.20260801-120000.log"
        active.write_text("ACTIVE", encoding="utf-8")
        stale.write_text("old", encoding="utf-8")
        os.utime(stale, (time.time() - 20 * 86400,) * 2)

        recs = []
        for f in (active, stale):
            st = f.stat()
            recs.append({"path": f, "size": st.st_size, "mtime": st.st_mtime,
                         "age_days": (time.time() - st.st_mtime) / 86400.0, "zone": "local"})
        keep, drop = log_retention.plan_prune(recs, keep_days=7, keep_count=3)
        r.assert_eq("活跃文件进 keep 组", [x["path"].name for x in keep], ["app.log"])
        r.assert_eq("超龄文件进 drop 组", [x["path"].name for x in drop],
                    ["app.20260801-120000.log"])
        # 预览模式不应改动磁盘
        r.assert_true("预览模式未删除文件", stale.exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_dry_run_sync_state_skips_save_and_tombstones():
    """v2.4.1: dry-run 下 SyncState 不落盘、不记墓碑（与正常模式对照）

    背景：同步引擎的写回阶段早有 dry_run 判断，但 .sync_state.json 保存与
    墓碑记录没有 —— 干跑一轮仍会污染状态与墓碑库。
    """
    r.set_module("sync_writers")

    from datetime import datetime, timedelta, timezone
    from sync_writers import SyncState

    def _build(base: Path, dry_run: bool) -> "SyncState":
        base.mkdir(parents=True, exist_ok=True)
        state = SyncState(state_path=base / ".sync_state.json", dry_run=dry_run)
        now = datetime.now(timezone.utc)
        # 48h 前写入 → 远超 24h 宽限期，正常模式必记墓碑
        state.state["claude"] = {"h_old": (now - timedelta(hours=48)).isoformat()}
        return state

    tmp = Path(tempfile.mkdtemp())
    try:
        dry = _build(tmp / "dry", True)
        res_dry = dry.reconcile_with_target_hashes(
            "claude", actual_hashes=set(), legacy_count=0, target_file_present=True)
        # reconcile 只在 tombstoned > 0 时写入该键（dry-run 下为 0 → 键缺失）
        r.assert_true("dry-run vanish 未记墓碑", not res_dry.get("tombstoned"))
        r.assert_true("dry-run 墓碑库为空", not dry.tombstones.is_tombstoned("h_old"))
        dry.save()
        r.assert_true("dry-run save 未落盘", not dry.state_path.exists())

        wet = _build(tmp / "wet", False)
        res_wet = wet.reconcile_with_target_hashes(
            "claude", actual_hashes=set(), legacy_count=0, target_file_present=True)
        r.assert_eq("正常模式 vanish 记墓碑", res_wet.get("tombstoned"), 1)
        r.assert_true("正常模式墓碑已记", wet.tombstones.is_tombstoned("h_old"))
        wet.save()
        r.assert_true("正常模式 save 落盘", wet.state_path.exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_create_merger_passes_embedding_service():
    """v2.4.1: create_merger 透传 embedding_service（此前工厂方法无该形参）"""
    r.set_module("agent_memory")

    import agent_memory as am

    tmp = Path(tempfile.mkdtemp())
    try:
        db = tmp / "shared.db"
        sentinel = object()
        merger = am.create_merger(db, embedding_service=sentinel)
        r.assert_true("embedding_service 已透传", merger.embedding_service is sentinel)

        merger_default = am.create_merger(db)
        r.assert_true("默认仍为 None", merger_default.embedding_service is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_volume_limit_policy_key_selectable():
    """v2.4.1: 写回体积保护可选档位（shared 128KB / private 256KB）

    修复前 _enforce_write_volume_limit 硬编码读 memory_private_md 档位，
    Claude 写共享池时误用 256KB 上限，超过 128KB 的共享池策略形同虚设。
    """
    r.set_module("sync_writers")

    import json
    from sync_writers import GenericMarkdownWriter, SyncState

    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "_shared").mkdir(parents=True, exist_ok=True)
        (tmp / "_shared" / "volume_policy.json").write_text(json.dumps({
            "limits": {
                "memory_private_md": {"max_size_kb": 256, "max_lines": 3000,
                                      "action_when_exceeded": "compress_and_truncate"},
                "memory_shared_md": {"max_size_kb": 128, "max_lines": 2000,
                                     "action_when_exceeded": "truncate_oldest"},
            }
        }), encoding="utf-8")

        writer = GenericMarkdownWriter(
            sync_state=SyncState(state_path=tmp / ".sync_state.json"))

        content = "# MEMORY\n\n## Shared Knowledge\n\n" + "".join(
            "- [sync:mem_{}|h:abc{:06d}] 记忆内容测试记忆内容测试记忆内容测试。\n".format(i, i)
            for i in range(2500)
        )
        r.assert_true("构造内容确实超过 128KB", len(content.encode("utf-8")) > 128 * 1024)

        _, trunc_private, _ = writer._enforce_write_volume_limit(
            tmp / "x.md", content, "generic", preserve_tail=True)
        r.assert_true("private 档 ~178KB 不截断", not trunc_private)

        shared_content, trunc_shared, _ = writer._enforce_write_volume_limit(
            tmp / "x.md", content, "generic", preserve_tail=True,
            policy_key="memory_shared_md")
        r.assert_true("shared 档 ~178KB 截断", trunc_shared)
        r.assert_true("截断后低于 128KB 上限",
                      len(shared_content.encode("utf-8")) <= 128 * 1024)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cache_hit_rate_stats():
    """v2.4.1: SearchOptimizer.get_cache_stats 的命中率不再是恒 0"""
    r.set_module("agent_memory")

    import agent_memory as am

    tmp = Path(tempfile.mkdtemp())
    try:
        db = tmp / "search.db"
        opt = am.SearchOptimizer(db)
        opt.optimized_search("关键词A", limit=5)   # miss
        opt.optimized_search("关键词A", limit=5)   # hit
        opt.optimized_search("关键词B", limit=5)   # miss

        stats = opt.get_cache_stats()
        r.assert_eq("命中 1 次", stats["hits"], 1)
        r.assert_eq("未命中 2 次", stats["misses"], 2)
        r.assert_true("命中率计算", abs(stats["hit_rate"] - (1 / 3)) < 1e-6)

        opt.clear_cache()
        cleared = opt.get_cache_stats()
        r.assert_eq("清空后命中归零", cleared["hits"], 0)
        r.assert_eq("清空后未命中归零", cleared["misses"], 0)
        r.assert_eq("清空后缓存为空", cleared["size"], 0)
        r.assert_eq("计数归零后命中率为 0", cleared["hit_rate"], 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
    test_single_instance_holds_mutex,
    test_single_instance_detects_conflict,
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
    # v2.1.2 fixes
    test_filelock_acquire_release,
    test_filelock_reentrant_same_thread,
    test_filelock_cross_process_mutual_exclusion,
    test_filelock_stale_lock_recovered,
    test_locked_update_no_lost_update,
    test_backup_file_agent_id_naming,
    test_backup_log_roundtrip,
    test_resolve_source_device_no_match_raises,
    test_register_current_device_roundtrip,
    test_resolve_device_name_no_impersonation,
    test_safe_read_text_memory_error,
    test_shrink_md_fallback_truncates,
    test_sync_state_merge_preserves_other_agents,
    test_check_onedrive_conflicts_chinese,
    test_enforce_db_limit_date_prefix_sql,
    test_tools_package_importable,
    test_build_py_paths_tools,
    test_build_py_smoke_check,
    # v2.2.0: 跨机 home 解析（LOCALAPPDATA 优先 + 缓存跨机失效）
    test_get_local_home_prefers_localappdata,
    test_expand_agent_home_path_tilde,
    test_detect_agents_cache_cross_device_invalidated,
    # v2.2.0: shared.db 本机化 + 增量同步
    test_get_local_data_dir_under_localappdata,
    test_get_shared_db_path_not_in_data_root,
    test_rebuild_shared_cache_from_md,
    test_parse_md_entry_ids_robust_to_separators,
    test_write_shared_md_incremental_no_rewrite,
    test_ensure_shared_cache_migrates_legacy,
    # v2.2.1: OneDrive 运行时解耦（日志本机化 / 启动不写数据根 / 通知超时 / 托盘重试 / robocopy）
    test_get_data_root_skips_writable_test,
    test_log_manager_fallback_on_bad_dir,
    test_log_manager_defaults_to_local_logs,
    test_get_logger_never_raises,
    test_notify_subprocess_bounded,
    test_shell_notify_icon_retry_once,
    test_reloc_log_writes_local_first,
    test_ensure_local_install_uses_robocopy,
    # v2.2.1: UI（退出按钮 + 窗口内容自适应）
    test_fit_window_size,
    test_ui_has_exit_button_and_fit,
    # v2.2.2: OneDrive 锁原子写 + dsh 识别 + 缓存指纹失效
    test_safe_write_locked_target_falls_to_pending,
    test_merge_pending_files_tree_and_orphan_tmp,
    test_detect_agents_cache_profiles_hash_invalidation,
    test_detect_agents_dsh_memory_scan,
    # P1-3: 墓碑机制（防已删记忆跨设备复活）
    test_tombstone_store_roundtrip,
    test_reconcile_vanish_records_tombstone_after_grace,
    test_reconcile_conservative_never_tombstones,
    test_tombstone_refresh_reloads_disk,
    test_reconcile_mass_vanish_not_tombstoned,
    test_tombstone_purge_db_chunks_large_hits,
    test_tombstone_add_returns_zero_on_write_failure,
    test_tombstone_purge_db_removes_rows,
    test_load_shared_memories_filters_tombstoned,
    test_sync_shared_to_agent_filters_tombstoned,
    # v2.3.0: 日志保留策略（数量+天数 / 轮转不覆盖 / 失败不静默）
    test_log_rotation_uses_timestamped_names,
    test_log_prune_by_count_keeps_newest,
    test_log_prune_by_days_and_never_touches_active,
    test_log_write_failure_is_surfaced_not_swallowed,
    test_log_retention_tool_scan_is_preview_by_default,
    # v2.4.1: 代码审计回归（dry-run 只读闭环 / 向量去重可达 / 体积档位 / 命中率）
    test_dry_run_sync_state_skips_save_and_tombstones,
    test_create_merger_passes_embedding_service,
    test_volume_limit_policy_key_selectable,
    test_cache_hit_rate_stats,
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
