"""PyInstaller entry point (`--onefile` target).

Kept separate from recall/cli.py so the packaged exe resolves the portable
recall_config.json next to itself (see config._config_file_path).
"""

import sys

from recall.cli import main

if __name__ == "__main__":
    sys.exit(main())