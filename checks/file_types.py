# -*- coding: utf-8 -*-
"""文件类型清单的**单一来源**（评审 v8 / Codex P1）。

为什么必须单一：原先清单散落 6 处且三套不同 ——
  · harness/agents/auto-gate.py      8 种（含 .tsx/.jsx/.css/.scss）
  · harness/hooks/post-edit.py       4 种（.vue/.js/.ts/.cs）
  · harness/hooks/pre-commit.py      4 种（同上）
  · checks/backend_layer_check.py    7 种 / checks/backend_ruleset.py 7 种
  · checks/pc_code_check.py          .vue/.js/.ts
  · checks/h5_code_check.py          .vue/.nvue/.js/.ts
后果是**编辑期看得见、提交期漏检**（H5 的 .nvue、后端仓混入的 .tsx/.jsx/.css/.scss 都是这个洞）。

约定：hook/gate 层（pre-commit / post-edit / auto-gate）按 gate 名推端点，取该端点的**检查能力并集**；
端点自己的 check 脚本仍可用自己的最小集扫描目标，但**不得超出**本模块声明的能力面。
配套锁：tests/review/test_review_v8.py 断言 hook 层清单 ⊇ 各端点 check 脚本的清单。
"""

PC_EXTS = (".vue", ".js", ".ts", ".jsx", ".tsx", ".css", ".scss")
H5_EXTS = (".vue", ".nvue", ".js", ".ts", ".css", ".scss")
BACKEND_EXTS = (".cs", ".vue", ".js", ".ts", ".tsx", ".jsx", ".css", ".scss")

BY_KIND = {"pc": PC_EXTS, "h5": H5_EXTS, "backend": BACKEND_EXTS}
# 兜底：gate 名认不出端点时（自定义命名），用并集，宁可多扫不可漏扫
ALL_EXTS = tuple(sorted(set(PC_EXTS) | set(H5_EXTS) | set(BACKEND_EXTS)))


def kind_of(gate_name):
    """从 gate 名推端点：约定 <仓>_pc / <仓>_h5 / <仓>_backend。"""
    n = (gate_name or "").lower()
    for k in ("backend", "pc", "h5"):
        if n.endswith("_" + k):
            return k
    return ""


def exts_for(gate_name):
    """该 gate 在 hook/gate 层应当被收集的文件扩展名（按端点，认不出则取并集）。"""
    return BY_KIND.get(kind_of(gate_name), ALL_EXTS)


def is_repo_wide(gate_name):
    """**仓库级 gate**：不按扩展名筛文件的那种（如 harness_self 自检工程仓整体）。

    2026-09-15 Codex 复审 P1-1 实测：harness_self 走普通扩展名策略 → .py/.yaml 全被过滤掉，
    于是"最该保护的工程核心文件"反而绕过了自检层，而脚本本体直跑却是绿的（函数层对、接线层漏）。
    判据：认不出端点的 gate（kind_of 为空）即视为仓库级 —— 宁可多扫，不可漏扫。
    """
    return kind_of(gate_name) == ""


def matches(path, gate_name):
    """该文件是否属于本 gate 的检查面（仓库级 gate 一律为真）。"""
    if is_repo_wide(gate_name):
        return True
    return str(path).lower().endswith(exts_for(gate_name))
