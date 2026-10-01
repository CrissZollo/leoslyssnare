"""Entry point for running from source (`python run.py`) and for PyInstaller."""

import sys

from leoslyssnare.__main__ import run

if __name__ == "__main__":
    sys.exit(run())
