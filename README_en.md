<div align="center">

<img src="docs/assets/banner-xicheng.jpg" alt="AgentMemorySystem" width="680"/>

# AgentMemorySystem

**Multi-Agent Memory Fusion & Cross-Device Sync**

**English** | [中文](README.md)

![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)
![Python](https://img.shields.io/badge/python-3.10+-yellow?style=flat-square)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey?style=flat-square)
![Stars](https://img.shields.io/github/stars/LEE20260315/AgentMemorySystem?style=flat-square&color=orange)

</div>

---

## Overview

**AgentMemorySystem** is a local-first memory synchronization system for multiple AI agents. It solves the problem of **fragmented memories across different AI clients** (Claude, Hermes, Trae, Cursor, CodePilot, etc.) with incompatible formats, scattered storage paths, and no way to share knowledge.

**Core pipeline:** Discover Agents → Extract Memories → Merge & Deduplicate → Write Back in Native Format

**Design Principles:**
- **Local-first** — All data stays on your machine, no cloud dependency
- **Cross-device sync** — Via OneDrive or any sync folder
- **Zero-config** — Double-click EXE to run, no Python required
- **Safe & reliable** — Auto-backup, conflict detection, sensitive info filtering, one-click rollback

## Features

| Feature | Description |
|---------|-------------|
| **Auto Agent Discovery** | Candidate paths + signature validation, no hardcoded paths |
| **Native Format Write-back** | Claude sub-files, Trae sections, Hermes §-delimited, generic Markdown |
| **Merge & Dedup** | SQLite fusion index with content-hash deduplication |
| **Tiered Storage** | Hot / Warm / Cold data tiers with auto-archiving |
| **Security** | Auto-backup, file locks, OneDrive conflict detection, sensitive info filtering |
| **GUI + CLI** | System tray resident app + command-line tools |
| **Out of the Box** | Single-file EXE with all dependencies bundled (~18MB) |

## Supported Agents

| Agent | Memory Format | Write-back Method |
|-------|--------------|-------------------|
| Claude | Sub-files + `MEMORY.md` index | Append to `shared/` sub-files |
| Hermes | `MEMORY.md` with `§` delimiters | Append § sections |
| Trae | `user_profile.md` sections | Append `## Shared Knowledge` |
| CodePilot | SQLite (`codepilot.db`) | Export as Markdown |
| Cursor / Windsurf / Cline / Continue / Aider / Roo-Code / Codex | Generic Markdown | Auto-adapted |

## Quick Start

### Option 1: Pre-built EXE (Recommended)

Download `AgentMemorySync.exe` from [Releases](https://github.com/LEE20260315/AgentMemorySystem/releases) and double-click to run.

No Python installation required. All dependencies bundled (~18MB).

### Option 2: Run from Source

**Requirements:**
- Python 3.10+
- Windows 10+ / Linux (GUI requires a graphical environment)

```bash
# Clone the repository
git clone https://github.com/LEE20260315/AgentMemorySystem.git
cd AgentMemorySystem

# Install dependencies
pip install -r requirements.txt

# Launch GUI
python memory_sync_app.py

# Or launch CLI
python memory_sync_app.py --cli
```

### Common Commands

```bash
# Full sync (discover → extract → merge → write back)
python memory_cli.py full-sync

# Re-detect agents
python memory_cli.py redetect

# Write a memory entry
python memory_cli.py --agent claude write "Remember this design decision" --tags dev

# Search memories
python memory_cli.py --agent claude search "keyword"

# Health check
python memory_cli.py --agent claude health

# Expire and archive old memories
python memory_cli.py --agent claude expire
```

## Architecture

```
Local Agent Memory Files (Claude / Hermes / Trae / ...)
    │
    ▼
┌─────────────────────────────────────┐
│  sync_engine.py — Orchestration     │
│  detect → extract → merge → write   │
└─────────────┬───────────────────────┘
              │
    ┌─────────┴─────────┐
    ▼                   ▼
┌──────────┐    ┌──────────────┐
│ SQLite   │    │ sync_writers │
│ Fusion   │    │ Write-back   │
│ Index    │    │ Adapters     │
└──────────┘    └──────────────┘
    │                   │
    ▼                   ▼
 Content-hash      Write back in
 deduplication     native format
```

**Layered Architecture:**
- **Core** (`agent_memory.py`) — SQLite storage, concurrency, backup, compression, health checks
- **Adapters** (`sync_writers.py`) — Per-agent write-back adapters
- **Orchestration** (`sync_engine.py`) — Discover → Extract → Merge → Write-back
- **UI** (`memory_sync_app.py`) — GUI + System tray + CLI

## Configuration

The `config.json` file supports the following main options:

```jsonc
{
  "paths": {
    "memory_root": "auto",      // Memory root dir, auto = auto-detect
    "shared_root": "auto"       // Shared dir
  },
  "limits": {
    "max_memories_per_agent": 10000,
    "max_memory_age_days": 365  // Memory expiry in days
  },
  "security": {
    "sensitive_patterns": ["password", "token", ...],
    "block_sensitive": false    // Block writes containing sensitive info
  },
  "sync": {
    "conflict_strategy": "newer_wins",
    "lock_timeout_seconds": 30
  }
}
```

See `config.json` in the repository for the full configuration reference.

## Version History

| Version | Date | Highlights |
|---------|------|-----------|
| **v1.3** | 2026-06 | GUI + system tray, EXE packaging, auto-sync scheduler, generic agent discovery, CodePilot support, lock file expiry fix |
| **v1.2** | 2026-05 | Sync engine, write-back adapters, SQLite fusion index, OneDrive conflict detection |
| **v1.1** | 2026-05 | Config management, logging system, sensitive info detection, health checks, memory expiry |
| **v1.0** | 2026-05 | Core library, file locks, device config, Markdown parsing |

See [CHANGELOG.md](CHANGELOG.md) for the detailed changelog.

## Project Structure

```
AgentMemorySystem/
├── agent_memory.py           # Core engine (SQLite, concurrency, backup, compression)
├── sync_engine.py            # Sync orchestration (discover → extract → merge → write-back)
├── sync_writers.py           # Agent write-back adapters
├── memory_sync_app.py        # GUI + system tray + CLI
├── memory_cli.py             # CLI entry point
├── build.py                  # Packaging script (python build.py → EXE)
├── config.json               # Configuration file
├── requirements.txt          # Python dependencies
├── pyproject.toml            # Package metadata
├── assets/                   # Icon assets
├── docs/                     # Documentation
├── CHANGELOG.md              # Changelog
├── DEVLOG.md                 # Development log
├── LICENSE                   # MIT License
└── test_memory.py            # Test suite
```

## FAQ

**Q: Do I need OneDrive?**
A: No. By default, data is stored in the project's `data/` directory. You can configure any path in `config.json`. OneDrive is only needed for cross-device sync.

**Q: Does it support macOS?**
A: The CLI works directly. The GUI uses tkinter, which requires Python to be installed with tcl/tk support on macOS.

**Q: What if a memory file is locked?**
A: Lock files have a 60-second auto-expiry mechanism. Stale lock files are automatically cleaned up.

**Q: How do I rollback a sync?**
A: Original files are auto-backed up to `.sync_backups/` before each sync. You can rollback via GUI or CLI.

**Q: How is privacy protected?**
A: Sensitive info (passwords, keys, tokens) is automatically detected on write. You can configure it to block or just warn. All data is stored locally, never uploaded.

## Contributing

Issues and Pull Requests are welcome!

1. Fork this repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

[MIT License](LICENSE) © 2026 LEE20260315

---

<div align="center">
<sub>紙承墨，墨載意，意馭器</sub><br>
<sub>西城閒人 · 識</sub>
</div>
