#!/usr/bin/env python3
"""
Agent 记忆系统 - 初始化脚本
用法: python setup_agent.py --agent claude --device laptop --root "C:/Users/你/OneDrive/AgentMemory"
"""

import argparse
import json
import sys
from pathlib import Path


def setup(agent_id: str, device_name: str, root_dir: str):
    """初始化 Agent 记忆目录"""
    root = Path(root_dir)
    agent_dir = root / agent_id
    shared_dir = root / "_shared"

    # 创建目录
    agent_dir.mkdir(parents=True, exist_ok=True)
    shared_dir.mkdir(parents=True, exist_ok=True)

    # identity.json
    identity = {
        "agent_id": agent_id,
        "display_name": agent_id,
        "primary_domain": "general",
        "memory_root": str(agent_dir),
        "shared_root": str(shared_dir),
        "created_at": "2026-06-12T00:00:00"
    }
    (agent_dir / "identity.json").write_text(json.dumps(identity, indent=2, ensure_ascii=False), encoding="utf-8")

    # device_config.json
    (agent_dir / "device_config.json").write_text(
        json.dumps({"source_device": device_name}, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # memory_private.md
    (agent_dir / "memory_private.md").write_text("# {} 私有记忆\n".format(agent_id), encoding="utf-8")

    # memory_shared.md
    (agent_dir / "memory_shared.md").write_text("# {} 共享记忆\n".format(agent_id), encoding="utf-8")

    # last_sync.json
    sync = {
        "agent_id": agent_id,
        "last_merge_timestamp": None,
        "last_merge_id": None,
        "shared_memory_version": "v0"
    }
    (agent_dir / "last_sync.json").write_text(json.dumps(sync, indent=2), encoding="utf-8")

    # shared files (only create if not exist)
    policy_path = shared_dir / "writing_policy.md"
    if not policy_path.exists():
        policy_path.write_text("# 记忆写入约束\n\n## 规则\n- 每条记忆必须有标签\n- confidence 必须是 high/medium/low\n", encoding="utf-8")

    manual_path = shared_dir / "agent_runtime_manual.md"
    if not manual_path.exists():
        manual_path.write_text("# Agent 运行手册\n\n## 启动流程\n1. 读取 identity.json\n2. 加载记忆\n", encoding="utf-8")

    # 默认 triggers.yaml (新增 v1.3)
    triggers_path = shared_dir / "triggers.yaml"
    if not triggers_path.exists():
        triggers_path.write_text("""# Triggers Configuration
# 用户偏好: 直接编辑此文件即可调整触发词
# enabled: 是否启用 hotword 触发器

enabled: true

# 中文触发词
chinese:
  - "记住"
  - "以后"
  - "偏好"
  - "不要"
  - "习惯"
  - "总是"
  - "千万别"
  - "以后都"
  - "注意"
  - "重要"

# 英文触发词
english:
  - "remember"
  - "note that"
  - "from now on"
  - "preference"
  - "always"
  - "never"
  - "important"
  - "keep in mind"

# 命中触发词时自动追加的标签
auto_tags:
  - "用户明确指令"

# 命中触发词时强制 confidence
force_confidence: "high"
""", encoding="utf-8")

    # 注册到 Registry (新增 v1.3)
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import agent_memory as am
        registry = am.AgentRegistry(root=root)
        info = registry.register(agent_id, str(agent_dir / "identity.json"))
        print("[Registry] Agent '{}' 已注册".format(agent_id))
    except Exception as e:
        print("[Registry] 注册失败 (可手动执行 discover): {}".format(e))

    print("初始化完成!")
    print("  Agent: {}".format(agent_id))
    print("  设备: {}".format(device_name))
    print("  目录: {}".format(agent_dir))
    print()
    print("配置文件: {}".format(agent_dir / "identity.json"))
    print("设备配置: {}".format(agent_dir / "device_config.json"))
    print("Registry: {}/_shared/agent_registry.json".format(root))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="初始化 Agent 记忆目录")
    parser.add_argument("--agent", required=True, help="Agent ID (如 claude, hermes, trae)")
    parser.add_argument("--device", required=True, help="设备名称 (如 laptop, desktop, work-pc)")
    parser.add_argument("--root", required=True, help="OneDrive 同步目录 (如 C:\\Users\\你\\OneDrive\\AgentMemory)")

    args = parser.parse_args()
    setup(args.agent, args.device, args.root)
