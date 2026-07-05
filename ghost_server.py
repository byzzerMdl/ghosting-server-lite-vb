#!/usr/bin/env python3
"""Compatibility launcher — the code now lives in the ghostserver/ package.

Run either `python ghost_server.py` or `python -m ghostserver`.
"""

from ghostserver import *  # noqa: F401,F403 (re-export for old imports)
from ghostserver.__main__ import main

if __name__ == "__main__":
    main()
