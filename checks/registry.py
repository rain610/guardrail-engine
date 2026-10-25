# -*- coding: utf-8 -*-
"""agentguard gate→脚本注册表（显式注册源）。

开源版注册四个 gate：`harness_self`（工程自检）、`example`（内置示例规则）与
`eslint` / `semgrep`（外部工具适配，规则集在工具自己那边）。
`ENDPOINT_SCRIPTS` 同时是 `harness/run_gate.py` 判断「要不要向脚本注入 `--gate`」
的唯一数据源，所以示例脚本也必须登记在这里。
本文件受 harness_guard 保护（checks/*.py），改动提交需带 [harness] 标记。"""
import os

# 「外部工具适配」端：规则集在工具自己那边，本仓只做统一门禁通道
# （见 checks/external_adapter.py 模块头）。它们没有 RULESETS 条目 —— 覆盖度自评
# 只对内置规则有定义，外部规则不参与静态清单。
# 这里的键同时是 run_gate 判断「--mode 对本次判定有没有语义」的依据：适配门禁不区分
# strict/loose（CONFIG_KEYS = frozenset()），收下旗标只是为了兼容通用 CLI 形态。
ADAPTER_SCRIPTS = {
    "eslint": "checks/eslint_check.py",
    "semgrep": "checks/semgrep_check.py",
}

# 端脚本（值 = 相对工程根的脚本路径）。新增规则集时在此登记。
ENDPOINT_SCRIPTS = {
    "example": "checks/example_code_check.py",
}
ENDPOINT_SCRIPTS.update(ADAPTER_SCRIPTS)

# 端脚本 basename → ruleset 模块名（规则实例由该模块的 *_RULES 列表承载）。
# 覆盖度自评（harness/coverage.py）与自检的能力边界清单
# （checks/harness_self_check.py）都从这里反推，不各写一份。
RULESETS = {
    "example_code_check.py": "example_ruleset",
}

# 不再按仓名前缀批量展开（开源版没有业务仓）。
CUSTOMER_PREFIXES = []

# 显式 gate 名 → 脚本
OVERRIDES = {
    "harness_self": "checks/harness_self_check.py",
    "example": "checks/example_code_check.py",
}
OVERRIDES.update(ADAPTER_SCRIPTS)

# gate 配置只写脚本路径；端标签由 ENDPOINT_SCRIPTS 反查（OVERRIDES 里的非端脚本不参与，
# 例如 harness_self 在覆盖度里不算某个业务端）。
ENDPOINT_BY_SCRIPT = {os.path.basename(script): ep
                      for ep, script in ENDPOINT_SCRIPTS.items()}


def _build():
    gates = dict(OVERRIDES)
    for prefix in CUSTOMER_PREFIXES:
        for ep, script in ENDPOINT_SCRIPTS.items():
            gates.setdefault(f"{prefix}_{ep}", script)
    return gates


GATE_SCRIPTS = _build()
