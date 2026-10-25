# -*- coding: utf-8 -*-
"""兼容壳：`agentguard/mcp.py` → `guardrail_engine/mcp.py`（v0.3 移除）。

已有 MCP 配置里的 `<engine>/agentguard/mcp.py` 在迁移期继续可用。
"""
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from guardrail_engine.mcp import *  # noqa: E402,F401,F403
from guardrail_engine.mcp import _require_python, main  # noqa: E402

sys.stderr.write(
    "[deprecated] MCP 入口 agentguard/mcp.py 已改为 guardrail_engine/mcp.py"
    "（v0.3 移除兼容路径）。\n")

if __name__ == "__main__":
    sys.exit(main())
