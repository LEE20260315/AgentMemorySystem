"""
Agent 适配插件架构（v2.5.3 / TODO P1-7）
=========================================

背景
----
新增一个 Agent 的支持，此前要同时改三处核心代码：

1. ``config.json`` 的 ``agent_detection``（检测 profile）
2. ``sync_writers.WRITER_REGISTRY``（写回适配器映射）
3. ``test_full.py`` 的回归断言

本模块把检测与写回抽象为插件：注册一个插件即可完成「发现 + 写回」，
核心代码零改动。

两类插件
--------
- :class:`WriterPlugin`：为某 Agent 提供写回能力（必须实现 ``write()``）
- :class:`DetectorPlugin`：发现某 Agent 的安装位置（必须实现 ``detect()``）

查找顺序（``sync_writers.get_writer``）
--------------------------------------
插件注册表 → ``WRITER_REGISTRY`` → ``GenericMarkdownWriter`` 兜底。
即：外部插件优先级最高，可新增也可覆盖内置适配器。

安全约定
--------
**默认不扫描、不执行任何外部代码。** 目录加载需用户在配置里显式指定
（``agent_plugins.dir``）；未配置时插件系统只含内置适配器，行为与
v2.5.2 完全一致。

示例插件（放在配置指向的目录里即可加载）::

    from agent_plugins import WriterPlugin, register_writer_plugin

    class MyAgentWriter(WriterPlugin):
        agent_id = "myagent"
        aliases = ("myagent-appdata",)
        description = "MyAgent (~/.myagent/MEMORY.md)"

        def write(self, agent_id, target_path, memories, backup_dir=None, **kwargs):
            # ... 写回逻辑，返回 sync_writers.WriteBackResult
            ...

    register_writer_plugin(MyAgentWriter)

详见 .github/CONTRIBUTING.md「如何编写 Agent 适配器」。
"""

from __future__ import annotations

import importlib.util
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# agent_id -> WriterPlugin 类（内置 + 外部）
_WRITER_PLUGINS: Dict[str, type] = {}
# agent_id -> DetectorPlugin 类
_DETECTOR_PLUGINS: Dict[str, type] = {}


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------

class WriterPlugin(ABC):
    """写回插件基类。

    子类必须声明 ``agent_id`` 并实现 :meth:`write`。
    内置适配器（Claude / Trae / Hermes / Generic）以本类为第二个基类，
    统一登记进插件注册表。
    """

    agent_id: Optional[str] = None
    aliases: tuple = ()
    description: str = ""

    @abstractmethod
    def write(self, agent_id: str, target_path: Path, memories: list,
              backup_dir: Path = None, **kwargs):
        """写回记忆到目标文件。返回 ``sync_writers.WriteBackResult``。"""
        raise NotImplementedError

    def extract_target_info(self, agent_id: str, target_path: Path,
                            dry_run: bool = False):
        """可选：目标完整性信号（供 reconcile 判断孤儿）。

        默认返回 None —— 表示不提供该信号，同步引擎保持既有行为。
        """
        return None


class DetectorPlugin(ABC):
    """检测插件基类。

    子类必须声明 ``agent_id`` 并实现 :meth:`detect`。
    """

    agent_id: Optional[str] = None
    description: str = ""

    @abstractmethod
    def detect(self, config) -> Optional[dict]:
        """检测该 Agent 是否安装。

        Returns
        -------
        dict or None
            None 表示未检测到；命中时返回至少包含
            ``{"path": str, "memory_files": list}`` 的字典，
            ``detected_at`` / ``source`` 由框架补齐。
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 注册与查询
# ---------------------------------------------------------------------------

def _validate_agent_id(plugin_cls) -> str:
    agent_id = getattr(plugin_cls, "agent_id", None)
    if not agent_id:
        raise ValueError(
            "{} 未声明 agent_id 类属性".format(getattr(plugin_cls, "__name__", plugin_cls)))
    return agent_id


def register_writer_plugin(plugin_cls: type) -> str:
    """注册写回插件（含 aliases）。返回 agent_id。"""
    agent_id = _validate_agent_id(plugin_cls)
    _WRITER_PLUGINS[agent_id] = plugin_cls
    for alias in getattr(plugin_cls, "aliases", ()) or ():
        _WRITER_PLUGINS[alias] = plugin_cls
    logger.info("注册写回插件: {} ({})".format(agent_id, plugin_cls.__name__))
    return agent_id


def register_detector_plugin(plugin_cls: type) -> str:
    """注册检测插件。返回 agent_id。"""
    agent_id = _validate_agent_id(plugin_cls)
    _DETECTOR_PLUGINS[agent_id] = plugin_cls
    logger.info("注册检测插件: {} ({})".format(agent_id, plugin_cls.__name__))
    return agent_id


def unregister_plugin(agent_id: str) -> bool:
    """注销插件（测试用）。"""
    existed = _WRITER_PLUGINS.pop(agent_id, None) or _DETECTOR_PLUGINS.pop(agent_id, None)
    return existed is not None


def get_writer_plugin(agent_id: str):
    """按 agent_id 查找写回插件类；未注册返回 None。"""
    if not agent_id:
        return None
    return _WRITER_PLUGINS.get(agent_id)


def list_plugins() -> dict:
    """列举已注册插件（按 agent_id 去重后的概览）。"""
    writers = {}
    for aid, cls in _WRITER_PLUGINS.items():
        writers[aid] = {
            "class": cls.__name__,
            "module": cls.__module__,
            "description": getattr(cls, "description", ""),
            "alias_of": None if getattr(cls, "agent_id", None) == aid else
                        getattr(cls, "agent_id", None),
        }
    detectors = {}
    for aid, cls in _DETECTOR_PLUGINS.items():
        detectors[aid] = {
            "class": cls.__name__,
            "module": cls.__module__,
            "description": getattr(cls, "description", ""),
        }
    return {"writers": writers, "detectors": detectors}


# ---------------------------------------------------------------------------
# 检测插件执行
# ---------------------------------------------------------------------------

def run_detector_plugins(config, found: dict, log=None) -> dict:
    """执行所有检测插件，把命中结果追加进 found（不覆盖既有结果）。

    单个插件异常只记 Warning，不影响其他插件与内置检测结果。
    """
    log = log or logger
    if not _DETECTOR_PLUGINS:
        return found

    for agent_id, plugin_cls in list(_DETECTOR_PLUGINS.items()):
        # 已有检测结果（内置 profile / 手动覆盖）优先，插件不抢
        if agent_id in found:
            continue
        try:
            plugin = plugin_cls()
            info = plugin.detect(config)
        except Exception as e:
            log.warning("检测插件 {} 执行失败: {}".format(agent_id, e))
            continue
        if not info or not info.get("path"):
            continue
        record = dict(info)
        record.setdefault("memory_files", [])
        record.setdefault("detected_at", datetime.now(timezone.utc).isoformat())
        record.setdefault("source", "plugin")
        found[agent_id] = record
        log.info("检测插件命中: {} -> {}".format(agent_id, record["path"]))
    return found


# ---------------------------------------------------------------------------
# 目录加载（显式开启）
# ---------------------------------------------------------------------------

def load_plugins_from_dir(directory) -> int:
    """从目录加载插件文件（*.py），每个文件需自行调用 register_*_plugin。

    默认不扫描任何目录 —— 需用户在配置里显式指定 ``agent_plugins.dir``。
    单个文件加载失败只记 Warning，不影响其他插件与主流程。

    Returns
    -------
    int: 成功加载的文件数
    """
    d = Path(directory) if directory else None
    if not d or not d.is_dir():
        return 0

    loaded = 0
    for py in sorted(d.glob("*.py")):
        if py.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                "agent_plugin_{}".format(py.stem), py)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            loaded += 1
            logger.info("已加载 Agent 插件: {}".format(py))
        except Exception as e:
            logger.warning("加载 Agent 插件失败 {}: {}".format(py, e))
    return loaded


# ---------------------------------------------------------------------------
# 内置适配器登记为插件（行为等价，仅统一到插件注册表）
# ---------------------------------------------------------------------------

def register_builtin_plugins() -> int:
    """把内置写回适配器包装为插件登记进注册表。

    只声明 agent_id/aliases，写回逻辑仍来自原 writer 类 —— 行为零变化。
    返回注册的插件类数量。
    """
    try:
        from sync_writers import (
            ClaudeMemoryWriter, TraeMemoryWriter, HermesMemoryWriter,
            GenericMarkdownWriter,
        )
    except Exception as e:  # pragma: no cover - 正常安装下不会发生
        logger.warning("内置插件注册失败（跳过）: %s", e)
        return 0

    class BuiltinClaudeWriter(ClaudeMemoryWriter, WriterPlugin):
        agent_id = "claude"
        aliases = ("claude-appdata",)
        description = "Claude Code（内置，shared_from_agents.md + MEMORY.md 索引）"

    class BuiltinTraeWriter(TraeMemoryWriter, WriterPlugin):
        agent_id = "trae"
        aliases = ("trae-appdata",)
        description = "Trae（内置，user_profile.md 的 Shared Knowledge 段）"

    class BuiltinHermesWriter(HermesMemoryWriter, WriterPlugin):
        agent_id = "hermes"
        aliases = ("hermes-appdata",)
        description = "Hermes（内置，MEMORY.md 追加 § 分隔）"

    class BuiltinGenericWriter(GenericMarkdownWriter, WriterPlugin):
        agent_id = "generic"
        aliases = ()
        description = "通用 Markdown 写回（内置兜底）"

    for cls in (BuiltinClaudeWriter, BuiltinTraeWriter,
                BuiltinHermesWriter, BuiltinGenericWriter):
        register_writer_plugin(cls)
    return 4


# 模块导入即登记内置适配器（写回逻辑仍走原类，行为零变化）
register_builtin_plugins()
