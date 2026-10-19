# -*- coding: utf-8 -*-
"""agentguard gate→脚本注册表（阶段2 显式注册源）。
2026-09-12 收敛：唯一数据源 = 按端基础映射 + 客户前缀展开。
registry_loader 直接 import 本文件，不再维护第二份 DEFAULT_GATE_SCRIPTS。
新增客户：在 CUSTOMER_PREFIXES 加一行即可（三端 gate 自动展开）；
新增规则集：改 ENDPOINT_SCRIPTS 的值。本文件受 harness_guard 保护（checks/*.py），
改动提交需带 [harness] 标记。"""

# 按端的基础脚本（值 = 相对工程根的脚本路径）
ENDPOINT_SCRIPTS = {
    "pc":      "checks/pc_code_check.py",
    "h5":      "checks/h5_code_check.py",
    "backend": "checks/backend_layer_check.py",
}

# 客户 gate 前缀（每个客户自动展开三端 gate：{前缀}_pc / {前缀}_h5 / {前缀}_backend）
# hy 主仓命名不规则（hy_pc/hy_h5/hy_backend），用下方 OVERRIDES 显式登记。
CUSTOMER_PREFIXES = [
    "gxgk",
    "hy_hzy",
    "hy_base",
    "ntrx",
    "zhongdj",
    "keji",
    "c712",         # 712（YAML key 不能以数字开头）
    "c712v3",       # 712_v3
    "hanbogaoxin",
]

# 不规则命名的显式登记（优先于展开结果）
OVERRIDES = {
    "hy_pc": "checks/pc_code_check.py",
    "hy_h5": "checks/h5_code_check.py",
    "hy_backend": "checks/backend_layer_check.py",
    # 中新（zhongxin）：PC/H5 目录名大写 ZhongXin
    "zhongxin_backend": "checks/backend_layer_check.py",
    "zhongxin_pc": "checks/pc_code_check.py",
    "zhongxin_h5": "checks/h5_code_check.py",
    # 科技 keji_v3 线（2026-09-23）：**只有后端一端**（本机 `agentguard_back_net-keji_v3`
    # 是 keji_v3 分支的 worktree，没有 pc/h5 对应仓）。故**不能**加进 CUSTOMER_PREFIXES ——
    # 那里是按 `{前缀}_{端}` 三端展开的，塞进去会凭空长出 `keji_v3_pc`/`keji_v3_h5` 两条
    # 没有对应 gate 的登记（就是本仓一贯要报的「白写的配置项」）。这里显式登记这一条。
    "keji_v3_backend": "checks/backend_layer_check.py",
    # 工程自检（2026-09-16）：此前是**唯一**未登记的 gate，于是每次 `run_gate --validate`
    # 都固定打一行「⚠ registry 未登记 1/34」——一条永远为真的告警。常驻噪音的害处是
    # "下次真出现兜底时没人会看"，属告警疲劳。补登记后该行只在**新** gate 漏登记时出现。
    "harness_self": "checks/harness_self_check.py",
}

def _build():
    gates = dict(OVERRIDES)
    for prefix in CUSTOMER_PREFIXES:
        for ep, script in ENDPOINT_SCRIPTS.items():
            gates.setdefault(f"{prefix}_{ep}", script)
    return gates

GATE_SCRIPTS = _build()
