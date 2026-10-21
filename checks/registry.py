# -*- coding: utf-8 -*-
"""agentguard gate→脚本注册表（显式注册源）。

开源版只保留两个 gate：`harness_self`（工程自检）与 `example`（示例门禁）。
`ENDPOINT_SCRIPTS` 同时是 `harness/run_gate.py` 判断「要不要向脚本注入 `--gate`」
的唯一数据源，所以示例脚本也必须登记在这里。
本文件受 harness_guard 保护（checks/*.py），改动提交需带 [harness] 标记。"""

# 端脚本（值 = 相对工程根的脚本路径）。新增规则集时在此登记。
ENDPOINT_SCRIPTS = {
    "example": "checks/example_code_check.py",
}

# 不再按客户前缀批量展开（开源版没有客户仓）。
CUSTOMER_PREFIXES = []

# 显式 gate 名 → 脚本
OVERRIDES = {
    "harness_self": "checks/harness_self_check.py",
    "example": "checks/example_code_check.py",
}


def _build():
    gates = dict(OVERRIDES)
    for prefix in CUSTOMER_PREFIXES:
        for ep, script in ENDPOINT_SCRIPTS.items():
            gates.setdefault(f"{prefix}_{ep}", script)
    return gates


GATE_SCRIPTS = _build()
