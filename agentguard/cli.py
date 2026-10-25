#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容壳：`agentguard` 命令行 → `guardrail_engine.cli`（v0.3 移除）。

脚本形态（`python agentguard/cli.py ...`）与安装形态（console script `agentguard`）
都走这里；统一打印一次弃用提示，然后原样转发。
"""
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from guardrail_engine.cli import _require_python  # noqa: E402

_DEPRECATION = ("[deprecated] 命令 agentguard 已改为 guardrail-engine（v0.3 移除别名）。\n")


def main(argv=None):
    sys.stderr.write(_DEPRECATION)
    from guardrail_engine.cli import main as _main
    return _main(argv)


if __name__ == "__main__":
    _require_python()
    sys.exit(main())
