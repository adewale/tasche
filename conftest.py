"""Root conftest — ensures ``src/`` is importable during tests.

The Cloudflare Python Workers runtime treats ``src/`` as the module root, so
application code uses bare imports like ``from src.boundary import ...`` and
``from auth.session import ...``.  This conftest adds ``src/`` to
``sys.path`` so that those same imports resolve correctly under pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

_src_dir = str(Path(__file__).resolve().parent / "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
