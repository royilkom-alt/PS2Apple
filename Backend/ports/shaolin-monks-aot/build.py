#!/usr/bin/env python3
"""Build the audited Shaolin Monks USA revision with PS2Apple's shared runtime."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ports.generic.build import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main(desktop_metal=True))
