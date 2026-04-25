"""Tasche compatibility layer over the generic gasket FFI library.

Gasket itself is binding-name agnostic.  Tasche historically expected
``SafeEnv(env).DB``/``CONTENT``/``SESSIONS`` attributes, so this shim keeps those
application-specific names local while the shared implementation lives in
``gasket.ffi``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    from gasket.ffi.safe_env import *  # noqa: F403
    from gasket.ffi.safe_env import SafeEnv as _GenericSafeEnv
except ImportError:  # local checkout migration path
    _gasket_root = Path(__file__).resolve().parents[2] / "gasket"
    if _gasket_root.exists():
        sys.path.insert(0, str(_gasket_root))
    sys.modules.pop("gasket", None)
    from gasket.ffi.safe_env import *  # noqa: F403,E402
    from gasket.ffi.safe_env import SafeEnv as _GenericSafeEnv  # noqa: E402


class SafeEnv(_GenericSafeEnv):
    """Tasche binding-name adapter for gasket's generic SafeEnv."""

    def __init__(self, env: Any) -> None:
        super().__init__(env)
        self.DB = self.d1("DB")
        self.CONTENT = self.r2("CONTENT")
        self.SESSIONS = self.kv("SESSIONS")
        self.ARTICLE_QUEUE = self.queue("ARTICLE_QUEUE")
        self.AI = self.ai("AI")
        self.READABILITY = self.service("READABILITY")
