#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证判定的**纯函数**实现（Q4，2026-09-13 外部评审 v3 建议落地）。

为什么抽出来：回灌判据改过好几次（关键字口径过窄 → 放宽；Qoder 只输出最后一句 → 加行为证据；
上游报错误判 → 加识别），每次都散落在 `verify_integrations.sh` 的 if/elif 链里，改一次要重新
人肉回归一遍。抽成纯函数后，`tests/tooling/test_verify_judge.py` 用 ≥12 条**人造 fixture** 把"该判 yes /
该判 no"钉死 —— 判据再改就是受控修改，而不是碰运气。

输入（全部是数据，不做任何 I/O）：
  client            客户端名
  hard              是否强制型（True/False）
  wrote_probe       探针文件是否真的被写出来
  log_slice         本次运行**新增**的门禁日志行（list[str] 或 "
" 拼接的文本）
  client_output     客户端留档全文（stdout+stderr 合并）
输出：{"verdict": <封闭枚举>, "fail": bool, "summary": "…", "hint": "…"}
"""
from __future__ import annotations

import pathlib

import json
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── 判据常量（历史沿革见上面的 docstring；改这里必须同步 tests/tooling/test_verify_judge.py）
KEYWORD_RE = re.compile(
    r"门禁(未通过|调用出错)|harness 门禁|发现 [0-9]+ 个阻断级问题|门禁 \[strict\]|门禁 \[loose\]"
    r"|run_gate\] |门禁[^。\n]{0,10}(拦截|通过|阻断|已修)")
UPSTREAM_RE = re.compile(r"Upstream request failed|invalid_request_error|Internal server error|service_unavailable")
EXTERNAL_BLOCK_RE = re.compile(r"high demand|Reconnecting|429|temporarily unavailable|rate limit")
NOT_LOGGED_IN_RE = re.compile(r"Not logged in|Please run /login")

# 封闭枚举
PASS_REPLAY = "pass_replay"                    # 结论出现在模型输出里
PASS_BEHAVIOR = "pass_behavior"                # rc=1 → 最后 rc=0（模型据结论自修）
BLOCKED_NO_EVIDENCE = "blocked_no_evidence"    # 拦下了但没抓到回灌证据（软通过）
PROBE_NOT_BLOCKED = "probe_not_blocked"        # 触发了但**没拦下**（探针/匹配器可疑）
NOT_TRIGGERED = "not_triggered"                # 强制型却完全没触发 → ❌
SOFT_NOT_RUN = "soft_not_run"                  # 自觉型没跑（靠 pre-commit 兜底）→ ⚠
UPSTREAM_ERROR = "upstream_error"              # 上游模型报错，回灌未验证 → ⏸
EXTERNAL_BLOCKED = "external_blocked"          # 提供方限流/不可用 → ⏸
NOT_LOGGED_IN = "not_logged_in"                # CLI 未登录 → ⏸
PROBE_NOT_WRITTEN = "probe_not_written"        # 客户端**确实没写**且无拦截痕迹 → ⚠
GATE_SIDE_ERROR = "gate_side_error"            # 门禁自身 rc=2/3（配置/定位错、非受管）→ ❌
PROBE_WRITE_SUSPECT = "probe_write_suspect"    # 没写出来但输出里有拦截/权限痕迹 → hard ❌

VERDICTS = (PASS_REPLAY, PASS_BEHAVIOR, BLOCKED_NO_EVIDENCE, PROBE_NOT_BLOCKED, NOT_TRIGGERED,
            SOFT_NOT_RUN, UPSTREAM_ERROR, EXTERNAL_BLOCKED, NOT_LOGGED_IN, PROBE_NOT_WRITTEN,
            GATE_SIDE_ERROR, PROBE_WRITE_SUSPECT)

# 哪些算"harness 侧失败"（用于退出码）
FAILING = (NOT_TRIGGERED, GATE_SIDE_ERROR, PROBE_WRITE_SUSPECT)

# F2（2026-09-13 评审 v4）：没写探针时，出现这些痕迹多半是 **Pre* 钩子拦写**或权限问题，
# 不是"模型偷懒" → 不能按 probe_not_written 放过（那会掩盖 harness/配置问题）。
# G9（评审 v5 收紧）：**硬短语**直判 + **弱词需共现**。
# 动机：wrote=False 且模型闲聊里恰好出现 Traceback / permission denied 时，
# 原先会误判成 PROBE_WRITE_SUSPECT（fail=True）。硬短语按完整句式收紧；
# 弱词（permission denied / errno 13）要求同一条输出里出现 hook/harness/gate/.py 语境才算钩子故障。
WRITE_BLOCKED_RE = re.compile(
    r"Blocking hook|hook failed|can't open file|Traceback \(most recent call last\)",
    re.IGNORECASE)
WRITE_WEAK_RE = re.compile(r"permission denied|access is denied|errno 13", re.IGNORECASE)
# 语境 = 钩子/门禁相关词（K7 评审 v6：曾把"路径/文件名形态"也算语境，结果
# `v2.0`/`e.g.`/`a.b`/`os.rename` 这类任意点号+字母数字都满足 → 语境≈恒真，
# 共现收紧形同虚设。现收成纯钩子词；探针名含 `__verify_probe`，F2 真阳性不丢）。
WRITE_CONTEXT_RE = re.compile(r"hook|harness|gate|pre-?commit|probe|__verify", re.IGNORECASE)


def _write_blocked_evidence(text):
    """输出里是否存在"钩子/权限把写文件拦了"的证据（G9：噪音词需与钩子语境共现）。"""
    t = text or ""
    if WRITE_BLOCKED_RE.search(t):
        return True
    return bool(WRITE_WEAK_RE.search(t) and WRITE_CONTEXT_RE.search(t))


def _rows(log_slice):
    if isinstance(log_slice, (list, tuple)):
        lines = list(log_slice)
    else:
        lines = [l for l in (log_slice or "").splitlines() if l.strip()]
    out = []
    for l in lines:
        l = l.strip()
        if not l:
            continue
        try:
            out.append(json.loads(l))
        except Exception:
            continue
    return out


def judge(client, hard, wrote_probe, log_slice, client_output):
    rows = _rows(log_slice)
    triggered = bool(rows)
    # F1（评审 v4）：**只认 rc==1 为"拦下了"**。原实现写成"非 0 即拦"，于是 rc=2（配置/定位错）
    # 与 rc=3（非受管仓库）也被算成"拦下"，把"门禁自己坏了"渲染成软通过 —— 比报错更危险。
    blocked = any(r.get("rc") == 1 for r in rows)
    gate_err = [r.get("rc") for r in rows if r.get("rc") in (2, 3)]
    last_rc = rows[-1].get("rc") if rows else None
    self_fixed = blocked and last_rc == 0
    text = client_output or ""

    if NOT_LOGGED_IN_RE.search(text):
        return {"verdict": NOT_LOGGED_IN, "fail": False, "summary": "%s: ⏸ 未登录（先跑 CLI login，非 harness 问题）" % client,
                "hint": "⏸ CLI 未登录：先登录再复验"}

    # F1：门禁自身出错（rc=2 配置错 / rc=3 非受管）优先于一切 —— 关键字里本来就含"门禁调用出错"，
    # 不先拦会把"门禁坏了"判成"回灌成功"。
    if triggered and gate_err:
        return {"verdict": GATE_SIDE_ERROR, "fail": True,
                "summary": "%s: ❌ 门禁自身返回 rc=%s（配置/定位错误或非受管仓库）—— 这是 harness 侧问题，别当业务错"
                           % (client, ",".join(str(x) for x in gate_err)),
                "hint": "❌ 门禁调用出错：查 config/harness.toml 的 gate 登记与该仓路径；"
                        "用 `python sandbox/check_client_configs.py` / `install_shim.py --check` 自查"}
    if triggered and KEYWORD_RE.search(text):
        return {"verdict": PASS_REPLAY, "fail": False,
                "summary": "%s: ✅ 通过（门禁触发 + 结论回灌）" % client, "hint": ""}
    if triggered and self_fixed:
        return {"verdict": PASS_BEHAVIOR, "fail": False,
                "summary": "%s: ✅ 通过（门禁触发 + 模型据结论自修：rc=1 → rc=0，行为证据）" % client,
                "hint": "✅ 行为证据：本次 rc=1（真拦下）→ 最后 rc=0（模型照结论改好）"}
    if triggered and UPSTREAM_RE.search(text):
        return {"verdict": UPSTREAM_ERROR, "fail": False,
                "summary": "%s: ⏸ 门禁已触发、但上游模型报错（回灌未验证，非 harness 问题）—— 留档 verify_out_%s.txt" % (client, client),
                "hint": "⏸ 上游模型报错（模型没产出最终回答），回灌未能验证；门禁侧已确认触发"}
    if triggered and not blocked:
        return {"verdict": PROBE_NOT_BLOCKED, "fail": False,
                "summary": "%s: ⚠ 门禁触发但未拦下（探针内容/匹配器可能不对）——留档 verify_out_%s.txt" % (client, client),
                "hint": "⚠ 门禁触发了但没报问题：检查探针内容或该客户端的 matcher"}
    if triggered:
        return {"verdict": BLOCKED_NO_EVIDENCE, "fail": False,
                "summary": "%s: ✅ 门禁触发（拦下了；未见回灌证据，留档 verify_out_%s.txt）" % (client, client),
                "hint": ""}

    # ---- 完全没触发 ----
    if EXTERNAL_BLOCK_RE.search(text):
        return {"verdict": EXTERNAL_BLOCKED, "fail": False,
                "summary": "%s: ⏸ 外部阻塞（模型/提供方不可用，非 harness 问题）—— 留档 verify_out_%s.txt" % (client, client),
                "hint": "⏸ 上游模型不可用（不算 harness 失败）；钩子契约另由「钩子契约验证」一节保证"}
    if not wrote_probe:
        # F2（评审 v4）：没写出来**且输出里有拦截/权限痕迹** → 多半是 Pre* 钩子拦写或权限问题，
        # 那是 harness/配置问题，不能按"模型偷懒"放过。
        if _write_blocked_evidence(text):
            return {"verdict": PROBE_WRITE_SUSPECT, "fail": bool(hard),
                    "summary": "%s: %s 探针没写出来，但输出里有拦截/权限痕迹（Pre* 钩子拦写或权限问题）—— 这是 harness 侧问题"
                               % (client, "❌" if hard else "⚠"),
                    "hint": "❌ 钩子可能拦住了写操作：查该客户端 Pre* 配置（deny 分支）与 `install_shim.py --check`，"
                            "并确认客户端沙箱/权限"}
        return {"verdict": PROBE_NOT_WRITTEN, "fail": False,
                "summary": "%s: ⚠ 探针文件没写出来（客户端没执行写操作？）—— 不计 harness 失败，但这次链路没验到" % client,
                "hint": "⚠ 探针未写入：确认该客户端真的执行了写文件动作再复验"}
    if hard:
        return {"verdict": NOT_TRIGGERED, "fail": True,
                "summary": "%s: ❌ 门禁未触发（强制型，必须修）" % client,
                "hint": "❌ 强制型却完全没触发：查钩子配置/客户端是否读到了配置"}
    return {"verdict": SOFT_NOT_RUN, "fail": False,
            "summary": "%s: ⚠ 无原生钩子，模型本次没自觉跑（靠 git pre-commit 兜底）" % client, "hint": ""}


def main(argv=None):
    """CLI：给 bash 用。输出两行 —— ① 机器可读（verdict|fail|summary）② 给人看的提示（可为空）。"""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True)
    ap.add_argument("--hard", default="yes")
    ap.add_argument("--wrote", default="yes")
    ap.add_argument("--log-slice", default="")      # 文件路径或空
    ap.add_argument("--output", default="")         # 留档文件路径或空
    a = ap.parse_args(argv)
    slice_text, out_text = "", ""
    if a.log_slice:
        try:
            slice_text = pathlib.Path(a.log_slice).read_text(encoding="utf-8", errors="replace")
        except Exception:
            slice_text = ""
    if a.output:
        try:
            out_text = pathlib.Path(a.output).read_text(encoding="utf-8", errors="replace")
        except Exception:
            out_text = ""
    r = judge(a.client, a.hard == "yes", a.wrote == "yes", slice_text, out_text)
    print("%s|%d|%s" % (r["verdict"], 1 if r["fail"] else 0, r["summary"]))
    print(r.get("hint") or "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
