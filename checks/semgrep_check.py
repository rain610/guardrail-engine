#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Semgrep 适配门禁 —— 把 `semgrep scan --json` 接进统一门禁通道。

用法：
    python checks/semgrep_check.py --repo <仓库路径> [文件...]
    python checks/semgrep_check.py --repo <仓库路径> --all

配置：扫描规则由 Semgrep 自己的配置/规则包决定（本门禁不改、不覆盖）；
命令默认取 PATH 里的 `semgrep`，可用 `AGENTGUARD_SEMGREP` 覆盖（也接受 JSON 字符串数组）。
超时默认 300s，可用 `AGENTGUARD_ADAPTER_TIMEOUT` 调整。

退出码：0=通过（可能带警告），1=有阻断级问题，2=工具/环境/配置问题或**分析不完整**
（Semgrep 的 errors 非空）—— 后者不能读成「没问题」。
规则细节与 fail-closed 判据见 checks/external_adapter.py 的模块头。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from external_adapter import run  # noqa: E402

# 本脚本不读任何 gate 级配置：规则选择属于 Semgrep 自己的配置。
# 显式声明空集合，让 run_gate --validate 把「写了但没人读」的键当场拦下。
CONFIG_KEYS = frozenset()

if __name__ == "__main__":
    sys.exit(run("semgrep", "Semgrep", "AGENTGUARD_SEMGREP", "semgrep",
                 ["scan", "--json", "--quiet", "--metrics=off", "--disable-version-check"]))
