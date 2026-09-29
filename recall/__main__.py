"""Allow `python -m recall ...` (used by the .bat/.vbs launchers)."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())