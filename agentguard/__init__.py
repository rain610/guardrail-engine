# -*- coding: utf-8 -*-
"""兼容壳（2026-10-25 改名 guardrail-engine）：`import agentguard` 仍可用。

本包只在 v0.2 迁移期存在，v0.3 移除。新代码请 `import guardrail_engine`；
命令行请用 `guardrail-engine`（`agentguard` 作为别名保留到 v0.3）。
"""
import warnings

warnings.warn(
    "包名 agentguard 已改为 guardrail_engine（v0.3 移除兼容层）；命令行推荐 guardrail-engine。",
    DeprecationWarning, stacklevel=2)

from guardrail_engine import __version__, _version  # noqa: E402,F401
from guardrail_engine import _shell as _shell  # noqa: E402,F401

__all__ = ["__version__", "_version", "_shell"]
