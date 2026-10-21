# -*- coding: utf-8 -*-
"""
agentguard 规则注册表 loader（阶段2 发现式注册，2026-09-06）
========================================================
职责：把「gate → ruleset」的映射从 run_gate 硬编码改为**目录发现 + 显式注册**双轨：
  - 唯一数据源 = checks/registry.py 的 GATE_SCRIPTS（2026-09-12 收敛：
    本文件直接 import，不再维护第二份 DEFAULT 副本）。
  - registry 缺失/异常时回退 config 的 script 字段（run_gate 侧兜底，门禁不瘫痪）。
发现式（glob）不自动注册任何 .py——规则集必须显式登记，避免把测试/工具脚本误当规则集。

新增一个规则集的步骤：
  1. checks/ 下写 xxx_ruleset.py，导出 XXX_RULES: list[Rule]；
  2. checks/registry.py 的 ENDPOINT_SCRIPTS/OVERRIDES 登记；
  3. config/harness.yaml（或 local）加 gate 条目。完事。
"""
import os
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKS = os.path.join(ROOT, "checks")
REGISTRY_FILE = os.path.join(CHECKS, "registry.py")

_cache = None  # 进程内缓存（registry 是静态数据，无需重复加载）


def load_gate_scripts():
    """返回 gate名 -> 脚本相对路径 的 dict。直接 import registry.py（唯一数据源）。"""
    global _cache
    if _cache is not None:
        return dict(_cache)
    spec = importlib.util.spec_from_file_location("agentguard_registry", REGISTRY_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    reg = getattr(mod, "GATE_SCRIPTS", None)
    if not isinstance(reg, dict) or not reg:
        raise ValueError(f"registry.py 未导出有效 GATE_SCRIPTS: {REGISTRY_FILE}")
    _cache = dict(reg)
    return dict(_cache)


def script_for(gate_name):
    """gate 名 -> 脚本绝对路径；未注册返回 None。"""
    rel = load_gate_scripts().get(gate_name)
    if not rel:
        return None
    p = os.path.join(ROOT, rel)
    return p if os.path.exists(p) else None
