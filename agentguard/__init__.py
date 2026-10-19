# -*- coding: utf-8 -*-
"""agentguard：agentguard 的薄壳集合（ADR-0001：壳只转发，逻辑只一份）。

- cli.py   : 统一命令行入口（运行 / 台账 / 安装 / 旁路 / 回滚）
- mcp.py   : 单文件零依赖 MCP 薄服务（stdio JSON-RPC）
- _shell.py: 三个入口共用的环境定位件（工程根 / 版本 / 解释器 / 前置检查 / gate 归属匹配）

版本单一来源（ADR-0001 铁律 3）：本文件的 `__version__`、`cli._version()`、`mcp.VERSION`
都走 `_shell.read_version()` 的**同一份**实现。此前三处各写一遍且回退链不同 ——
轮子安装形态（包内不带 VERSION 文件）下同一个包会报出两个版本号（剖析报告 N1）。
"""
from . import _shell


def _version():
    """本包版本 —— 转发公用件（保留本名以维持既有调用面）。"""
    return _shell.read_version()


__version__ = _version()
