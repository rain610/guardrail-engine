# -*- coding: utf-8 -*-
"""引擎元信息：版本与内容哈希（run_gate 台账字段的来源）。

从 `run_gate.py` 拆出：只读 VERSION 与引擎文件树，不碰台账 / 通知等模块级状态。
`_engine_sha` 是 `Engine-Sha` 提交尾注与 CI provenance 的同一个口径 —— 只此一份实现。
"""
import hashlib
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # = 引擎根

_VER_CACHE = None        # 规则/引擎版本缓存（VERSION 单一来源；打包路线 2026-09-14 台账字段）


def _engine_version():
    """台账「规则版本」：读 VERSION（单一来源）；读不到返回空串（不落键）。"""
    global _VER_CACHE
    if _VER_CACHE is None:
        try:
            with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
                _VER_CACHE = f.read().strip()
        except OSError:
            _VER_CACHE = ""
    return _VER_CACHE


_ENGINE_SHA_CACHE = []


def _engine_files():
    """判定所用引擎的内容面：规则、调度、审计器、壳包与锁清单。

    2026-10-26 第四轮评审 §4.2：`guardrail_engine/`（CLI/MCP/run/done/log --verify）
    与 `agentguard/` 兼容壳不是纯转发层 —— 改弱它们同样能骗过编辑期自检与 MCP 返回值，
    所以一并算进 engine_sha，让「判定代码从未提交」的形状也覆盖壳包。
    """
    files = []
    for sub in ("checks", "harness", "guardrail_engine", "agentguard"):
        base = os.path.join(ROOT, sub)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for fn in sorted(filenames):
                if fn.endswith(".py") or (sub == "checks" and fn in
                                          ("components_allowlist.yaml", "components_allowlist_h5.yaml")):
                    files.append(os.path.relpath(os.path.join(dirpath, fn), ROOT))
    for rel in ("config/harness.toml", "config/harness.yaml", "sandbox/mutation_smoke.py",
                "tests/tooling/test_lock_inventory.py", "tests/lock-inventory.json"):
        if os.path.isfile(os.path.join(ROOT, rel)):
            files.append(rel)
    return sorted(set(f.replace("\\", "/") for f in files))


def _engine_sha(files=None):
    """引擎内容哈希（16 hex）：同一份判定代码必须得到同一个值。

    与 ver 的区别：VERSION 只说明「哪一版」，本值说明「这次实际跑的是哪份内容」——
    瞬时改弱规则再改回来，两条台账的 engine_sha 不同（2026-10-21 外部评审 5.7-A）。
    读不到的文件按 `<missing>` 计入，让缺失本身也改变哈希，不静默跳过。
    """
    if files is None and _ENGINE_SHA_CACHE:
        return _ENGINE_SHA_CACHE[0]
    h = hashlib.sha256()
    for rel in (files if files is not None else _engine_files()):
        p = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
        h.update(rel.replace("\\", "/").encode("utf-8") + b"\0")
        try:
            with open(p, "rb") as f:
                h.update(hashlib.sha256(f.read()).digest())
        except OSError:
            h.update(b"<missing>")
    out = h.hexdigest()[:16]
    if files is None:
        _ENGINE_SHA_CACHE.append(out)
    return out
