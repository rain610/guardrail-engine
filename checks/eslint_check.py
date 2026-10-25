#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ESLint 适配门禁 —— 把 ESLint 的 `--format json` 接进统一门禁通道。

用法：
    python checks/eslint_check.py --repo <仓库路径> [文件...]
    python checks/eslint_check.py --repo <仓库路径> --all

配置：ESLint 自己的规则集由项目里的 eslint 配置决定（本门禁不改、不覆盖）；
命令默认取 PATH 里的 `eslint`，可用 `GUARDRAIL_ESLINT`（旧名 `AGENTGUARD_ESLINT` 仍可用）覆盖（也接受 JSON 字符串数组）。
超时默认 300s，可用 `GUARDRAIL_ADAPTER_TIMEOUT` 调整。

退出码：0=通过（可能带警告），1=有阻断级问题，2=工具/环境/配置问题（**不是**不通过）。
规则细节与 fail-closed 判据见 checks/external_adapter.py 的模块头。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from external_adapter import run  # noqa: E402

# 本脚本不读任何 gate 级配置：规则选择属于 ESLint 自己的配置。
# 显式声明空集合，让 run_gate --validate 把「写了但没人读」的键当场拦下。
CONFIG_KEYS = frozenset()

if __name__ == "__main__":
    sys.exit(run("eslint", "ESLint", "GUARDRAIL_ESLINT", "eslint",
                 ["--format", "json", "--no-color", "--no-error-on-unmatched-pattern"]))
