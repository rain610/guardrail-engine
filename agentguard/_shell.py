# -*- coding: utf-8 -*-
"""兼容壳：`agentguard._shell` → `guardrail_engine._shell`（v0.3 移除）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from guardrail_engine._shell import *  # noqa: E402,F401,F403
from guardrail_engine._shell import __all__  # noqa: E402,F401
