# -*- coding: utf-8 -*-
"""受保护路径判定的唯一实现（P1-3 / P2-8）。

背景：受保护面原先有**两份手写清单**——`checks/harness_guard._in_protected()`（Python）
与 `harness/hooks-dir/commit-msg` 里的 `grep -E '\\.(py|yaml)$|...'`（shell）。两份必然漂移：
commit-msg 停留在旧扩展名上，漏掉 `config/harness.toml`、`.github/workflows/*.yml`、
`sandbox/*.sh`、`VERSION`、`pyproject.toml`、`setup.py`、`MANIFEST.in`——一个 agent 可以在
不带 `[harness]` 的提交里改策略文件或 CI，而 CI 的 Engine-Sha 校验只在消息含 `[harness]`
时才触发，于是这次修改连内容校验都跳过了。

修法：清单落到纯文本 `harness/protected-patterns.txt`（每行一条 POSIX ERE），
Python 侧本模块用 `re` 编译，shell 侧 commit-msg 用 `grep -Ef` 消费——**同一份文件**，
不再各写一套。`tests/review/test_review_2026_11_04.py` 钉住「两处都读这份文件」且逐行合法。

为什么是纯文本而不是 Python 常量：commit-msg 是 `#!/bin/sh`，不能 import Python 模块；
用文本清单让两种语言零成本共享同一事实源（grep -Ef 直接吃这个文件）。
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))          # harness/
ROOT = os.path.dirname(HERE)                                # 工程根
PATTERNS_FILE = os.path.join(HERE, "protected-patterns.txt")

_CACHE = []                                                 # 编译后的 (regex, 原始行) 缓存


def _load():
    """读取并编译清单。跳过空行与 # 注释（与 grep 侧的惰性注释行为一致）。

    非法 ERE 直接抛出——清单损坏时必须**出声**，不能静默降级成「什么都不受保护」（fail-open）。
    """
    if _CACHE:
        return _CACHE[0]
    pats = []
    with open(PATTERNS_FILE, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            pats.append((re.compile(line), line))
    _CACHE.append(tuple(pats))
    return _CACHE[0]


def is_protected(rel):
    """相对工程根的路径是否属于受保护面。接受反斜杠，内部统一成正斜杠。"""
    r = (rel or "").replace("\\", "/")
    if not r:
        return False
    return any(p.search(r) for p, _ in _load())


def patterns_file():
    return PATTERNS_FILE
