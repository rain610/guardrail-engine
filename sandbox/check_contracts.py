#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""钩子**契约指纹**采集与比对（Q3，2026-09-13 外部评审 v3 建议落地）。

问题：7 家 × 三端 的钩子契约先前靠"人工读过 + 合成 payload 锁"，客户端（或我们自己）
一升级就可能漂移而无人知 —— 判据散在文档与记忆里，不机械。

做法（三层）：
  1. 采集：钩子正本支持 `HARNESS_CONTRACT_TRACE=1`，把【payload 键集合 / 命中输出形态 / 退出码】
     记一行（**只记结构不记消息文本**，所以指纹跨机器、跨文案稳定）。
  2. 比对：本脚本用**密闭夹具**（临时受管仓 + 临时 gate 配置）为每家客户端跑一遍，
     与 `tests/contracts/baseline/<client>.json` 比指纹。
  3. 三态语义（外部评审给的）：
       版本未变 + 指纹变 → ❌（契约漂移或被我们改坏）
       版本变了 + 指纹变 → ⚠（先跑一次真验证，再 `--accept` 更新基线）
       版本变了 + 指纹同 → ✅
       无基线            → ⚠（提示采集）

用法：
    python sandbox/check_contracts.py --all             # 比对全部（缺基线只提示）
    python sandbox/check_contracts.py --client trae
    python sandbox/check_contracts.py --all --accept    # 把当前指纹写回基线（人工确认后）
    python sandbox/check_contracts.py --all --json
退出码：有 ❌ → 1；否则 0（⚠ 不算失败，但会打印）
"""
from __future__ import annotations

import pathlib

import argparse
import io
import json
import os
import shutil
import subprocess
import time
import sys
import tempfile

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "integrations", "hooks", "eaglermc-harness-gate.py")
BASE_DIR = os.path.join(ROOT, "tests", "contracts", "baseline")
CLIENTS = ("claude", "codex", "opencode", "codebuddy", "zcode", "qoder", "trae")
EVENTS = ("PreToolUse", "PostToolUse", "Stop")   # PreToolUse 也纳入：qoder/codebuddy 的 deny 契约在那里

BAD_VUE = ('<template>\r\n  <div v-auth="p">x</div>\r\n</template>\r\n'
           '<script setup>\r\nconsole.log("probe");\r\n</script>\r\n')


def _version_of(client):
    """尽量取客户端版本（拿不到就 unknown）——用于"版本变了+指纹变"的语义区分。"""
    cmds = {
        "claude": ["claude", "--version"],
        "codex": ["codex", "--version"],
        "opencode": ["opencode", "--version"],
        "qoder": [os.path.join(os.path.expanduser("~"), ".qoder-cn", "bin", "qoderclicn", "qoderclicn.exe"), "--version"],
    }
    cmd = cmds.get(client)
    if not cmd:
        return "unknown"
    exe = cmd[0]
    # F4（评审 v4）：Windows 上多数客户端是 .cmd/.bat —— 先 shutil.which 解析到真实路径。
    #
    # 2026-09-23（评审 L14）：F4 当时还顺手对 .cmd/.bat 套了一层 `cmd /c`，理由写的是
    # "subprocess 不经 shell 执行不到"。**这句实测是假的**：直接调 kernel32 的 CreateProcessW
    # （绕开 subprocess）也能跑 .cmd ⇒ 是**操作系统本人**认 .cmd，不需要谁替它转交
    # （Python 文档同此：「batch files may be launched by the operating system in a system shell」）。
    # 而那层 cmd 会在参数含空格时**再解析一遍**引号（`cmd /c "<含空格路径>" "<含空格参数>"`
    # 会按 cmd 自己的规则剥掉首尾引号 ⇒ 引号错位、rc=1 且输出为空）。
    # ⚠ 本函数当前只带 `--version`（无空格）⇒ **踩不到那个格子**，属消隐患 + 订正错注释，
    #   不是修已复现的缺陷；锁见 tests/test_review_2026_09_23.py::TestL14BatchFileIsNotHandedToAShell。
    # ⚠ 同形的另两处（sandbox/upgrade.py / uninstall.py 的 run_cmd）**随包分发**，当初改它们会让
    #   已发布的 v0.1.6-dev 包与仓库脱钩（verify_release ⑧ 逐字节对拍）⇒ 等到了下一次版本号变更：
    #   **2026-09-23 已随 v0.1.7-dev 同批改成直跑**，三份实现现在口径一致（mcp.py 本来就没有那一支）。
    resolved = shutil.which(exe) if not os.path.isabs(exe) else exe
    if resolved:
        cmd = [resolved] + cmd[1:]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        line = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        return line[0][:60] if line else "unknown"
    except Exception:
        return "unknown"


class Fixture:
    """密闭夹具：临时受管仓 + 临时 gate 配置（不碰真实端仓，指纹才可复现）。"""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="contract-fixture-")
        self.repo = os.path.join(self.dir, "repo_pc")
        os.makedirs(os.path.join(self.repo, "src"))
        subprocess.run(["git", "init", "-q"], cwd=self.repo, capture_output=True)
        cfg = os.path.join(self.dir, "cfg.yaml")
        with io.open(cfg, "w", encoding="utf-8", newline="") as fh:
            fh.write("repos:\n  t_pc: %s\ngates:\n  t_pc:\n    enabled: true\n    repo: t_pc\n"
                     "    script: checks/pc_code_check.py\n    run_on: [pre_commit, post_edit]\n"
                     % self.repo.replace("\\", "/"))
        self.env = dict(os.environ, HARNESS_CONFIG=cfg,
                        HARNESS_CONTRACT_TRACE=os.path.join(self.dir, "trace.jsonl"),
                        HARNESS_LOG_FILE=os.path.join(self.dir, "gate_log.jsonl"))
        with io.open(os.path.join(self.repo, "src", "bad.vue"), "w", encoding="utf-8", newline="") as fh:
            fh.write(BAD_VUE)

    def payload(self, event):
        return json.dumps({"cwd": self.repo, "hook_event_name": event, "tool_name": "Write",
                           "tool_input": {"file_path": os.path.join(self.repo, "src", "bad.vue")}})

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def capture(client):
    """跑一遍夹具，返回该客户端的契约指纹。"""
    fx = Fixture()
    try:
        out = {}
        for event in EVENTS:
            r = subprocess.run([sys.executable, HOOK, client, event], input=fx.payload(event),
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env=fx.env, timeout=300)
            trace_path = fx.env["HARNESS_CONTRACT_TRACE"]
            rec = None
            if os.path.isfile(trace_path):
                lines = [l for l in pathlib.Path(trace_path).read_text(encoding="utf-8").splitlines() if l.strip()]
                if lines:
                    rec = json.loads(lines[-1])
                os.remove(trace_path)
            if rec is None:
                return {"error": "钩子没有写出 trace（HARNESS_CONTRACT_TRACE 未生效？）"}
            out[event] = {"rc": rec.get("rc"), "payload_keys": rec.get("payload_keys"),
                          "stdout": rec.get("stdout"), "stderr": rec.get("stderr")}
        return out
    finally:
        fx.cleanup()


def compare(client, fresh, baseline):
    """返回 (level, [说明...])，level ∈ ok / drift / version_changed / no_baseline"""
    if not baseline:
        return "no_baseline", ["尚无基线：确认这轮行为正确后跑 --accept 采一次"]
    notes = []
    same = True
    for ev in EVENTS:
        b, f = baseline.get(ev), fresh.get(ev)
        if b != f:
            same = False
            notes.append("%s 指纹变化：基线 %s → 现在 %s" % (ev, json.dumps(b, ensure_ascii=False),
                                                          json.dumps(f, ensure_ascii=False)))
    if same:
        return "ok", []
    if baseline.get("client_version") != _version_of(client):
        return "version_changed", notes + ["客户端版本从 %s 变为 %s（升级导致的漂移？先跑一次真验证再 --accept）"
                                          % (baseline.get("client_version"), _version_of(client))]
    return "drift", notes + ["版本未变而指纹变了 —— 契约漂移或被我们改坏（❌ 必须处理）"]



def validate_accept(reason, ci=False, interactive=True):
    """accept 的输入校验（Q③ 评审 v5）：CLI 与 accept_baseline 共用**同一份实现**。

    为什么抽出来：两层各写一遍同样的判据，风险不在"多"而在"改一处忘一处"——
    于是 CLI 能过、函数层不认（或反之），两层的"闸"就名存实亡。
    返回 None 表示通过，否则返回人话错误串。
    """
    if ci or not interactive:
        return "CI / 非交互环境不允许 accept（接受基线必须在人看着的终端里做，请用真实终端）"
    if len((reason or "").strip()) < 10:
        return "必须带 --reason（≥10 字，写清为什么接受这次变化）"
    return None


def accept_baseline(client, fresh, baseline, reason, force, base_dir=None, interactive=True, ci=False):
    """F3（评审 v4）：接受基线必须留痕、且不许把错误态/意外漂移顺手写进去。

    返回 (ok, level, note)。五道闸：
      ① CI / 非交互 → 拒绝（接受是人的动作）
      ② reason < 10 字 → 拒绝（留痕防手滑）
      ③ 采集失败 / 事件不全 → 拒绝（不能把错误态当新契约）
      ④ drift（版本未变而指纹变）→ 需 force（确认是有意改动）
      ⑤ 通过 → 打印差异、写基线（带审计字段）并 append HISTORY.jsonl
    """
    import time as _time
    base_dir = base_dir or BASE_DIR
    err = validate_accept(reason, ci=ci, interactive=interactive)
    if err:
        return False, "drift", err
    if fresh.get("error") or not all(ev in fresh for ev in EVENTS):
        return False, "drift", "采集失败/事件不全 —— 不能把错误态写进基线"
    lvl, notes = compare(client, fresh, baseline)
    if lvl == "drift" and not force:
        return False, "drift", "版本未变而指纹变了，必须加 --force（确认这是我们有意改的）"
    old_v = (baseline or {}).get("client_version", "(无基线)")
    new_v = _version_of(client)
    payload = {"client": client, "client_version": new_v, **fresh,
               "accepted_at": _time.strftime("%Y-%m-%dT%H:%M:%S"), "accepted_reason": (reason or "").strip(),
               "from_version": old_v, "to_version": new_v}
    os.makedirs(base_dir, exist_ok=True)
    with io.open(os.path.join(base_dir, "%s.json" % client), "w", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    hist = os.path.join(os.path.dirname(base_dir), "HISTORY.jsonl")
    with io.open(hist, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": payload["accepted_at"], "client": client, "reason": payload["accepted_reason"],
                             "from_version": old_v, "to_version": new_v}, ensure_ascii=False) + "\n")
    return True, "accepted", "已写回基线 %s.json（留痕 HISTORY.jsonl）" % client


def main():
    ap = argparse.ArgumentParser(description="钩子契约指纹采集 / 比对")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--client")
    ap.add_argument("--accept", action="store_true")
    ap.add_argument("--reason", default="", help="接受基线必须给理由（≥10 字）——留痕防手滑")
    ap.add_argument("--force", action="store_true", help="drift（版本未变而指纹变）时接受所必需")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    targets = CLIENTS if (a.all or not a.client) else (a.client,)
    # F3（评审 v4）：接受基线是**人的动作**，要留痕、要防"看到红就更新基线"的橡皮图章。
    if a.accept:
        _err = validate_accept(a.reason, ci=bool(os.environ.get("CI")),
                               interactive=sys.stdin.isatty())
        if _err:
            print("✗ 拒绝：" + _err)
            return 2
    rows, bad = [], False
    for c in targets:
        fresh = capture(c)
        path = os.path.join(BASE_DIR, "%s.json" % c)
        baseline = json.loads(pathlib.Path(path).read_text(encoding="utf-8")) if os.path.isfile(path) else None
        lvl, notes = compare(c, fresh, baseline)
        if a.accept and lvl != "ok":
            old_v = (baseline or {}).get("client_version", "(无基线)")
            print("  📝 尝试接受 %s 新基线：版本 %s → %s" % (c, old_v, _version_of(c)))
            for ev in EVENTS:
                o, n = (baseline or {}).get(ev), fresh.get(ev)
                if o != n:
                    print("       %s: %s → %s" % (ev, json.dumps(o, ensure_ascii=False)[:110],
                                                  json.dumps(n, ensure_ascii=False)[:110]))
            ok, lvl, msg = accept_baseline(c, fresh, baseline, a.reason, a.force)
            notes = [msg]
            if not ok:
                bad = True
                print("  ✗ %s 拒绝接受：%s" % (c, msg))
        rows.append({"client": c, "level": lvl, "notes": notes})
        bad = bad or lvl == "drift"
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        icon = {"ok": "✅", "accepted": "📝", "drift": "❌", "version_changed": "⚠️", "no_baseline": "⚠️"}
        print("钩子契约指纹比对（夹具复跑，只比结构不比消息文本）")
        for r in rows:
            print("  %s %-10s %s" % (icon.get(r["level"], "?"), r["client"], r["level"]))
            for n in r["notes"]:
                print("       - %s" % n[:200])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
