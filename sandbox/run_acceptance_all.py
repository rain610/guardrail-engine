# -*- coding: utf-8 -*-
"""P2-2 下半段：对「从未运行」的 gate 逐个受管仓跑 acceptance 并留档。

判据与留档口径（与评审 v9 一致）：四项证据（hook / probe / clean / syntax）+ 台账留档才算"上线"。
本脚本只跑 acceptance_check 并落一份本机报告（`reports/` 已 gitignore），不修改任何受管仓。
"""
import datetime
import io
import json
import os
import pathlib
import subprocess
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 工程根由**本文件位置**推导（2026-09-18 批次 5）：原先写死 `D:/Rain/project/agentguard`，
# 同事机器上必然指错。范式同 sandbox/install_shim.py、integrations/plugins/build_plugin.py。
# 三处锚点（config/ reports/ sandbox/）全在本工程同树内，推导完全等价。
R = pathlib.Path(__file__).resolve().parent.parent

# 仓→路径改从**生效配置**读（2026-09-18 批次 5，修批次 2 的连带遗漏）：
# 原先是用正则从 `config/harness.toml` 里刮 `repos:` 段。批次 2 把 repos 段整体下沉到
# gitignored 的 `config/harness.local.toml` 之后，共享文件里那一段只剩注释 —— 正则刮出 0 条，
# 紧接着的 `assert >= 10` 当场炸。而且"刮 YAML"这件事本身就与本仓「键与读者相邻」的纪律相悖：
# `_config.all_gates()` 已经把 local 覆盖层合并好了，每个 gate 都带 `_repo_path`。
sys.path.insert(0, str(R / "harness"))
from _config import all_gates            # noqa: E402

gates = {}
for _name, _g in all_gates().items():
    if not _g.get("enabled"):
        continue
    _rp = (_g.get("_repo_path") or "").replace("\\", "/")
    if _rp:
        gates[_name] = _rp
# 数量打出来防静默（沿用原判据）：解析异常时**拒绝在错误输入上继续**，
# 而不是生成一份"看起来跑完了、其实对象是空的"报告。
print("[acceptance] 生效配置里解析到 %d 个 gate→仓路径" % len(gates))
assert len(gates) >= 10, "配置解析异常（只拿到 %d 个 gate），拒绝在错误输入上继续" % len(gates)

rows = []
with io.open(R / "reports/gate_log.jsonl", encoding="utf-8", errors="replace") as f:
    for ln in f:
        if ln.strip():
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass
ran = {r.get("gate") for r in rows[-1000:]}
never = sorted(g for g in gates if g not in ran)

out = ["# 逐仓 acceptance 留档（2026-09-15 · 评审 v9 P2-2 下半段）\n",
       "\n> 对象：覆盖率报告里**从未运行**的 %d 个 gate\n" % len(never),
       "> 方式：`sandbox/acceptance_check.py <gate>`（cwd=受管仓），捕获五项证据\n",
       "> 说明：本文件由脚本生成；「仓不在本机」= 该受管仓未 checkout 到本机，需在有仓的机器上补跑\n"]
summary = []
for g in never:
    repo = gates[g].replace("\\", "/")
    if not os.path.isdir(repo):
        summary.append((g, "仓不在本机", repo))
        continue
    try:
        r = subprocess.run([sys.executable, str(R / "sandbox/acceptance_check.py"), g],
                           cwd=repo, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=420, stdin=subprocess.DEVNULL)
        txt = (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        summary.append((g, "运行异常: %s" % type(e).__name__, repo))
        continue
    tail = [l.rstrip() for l in txt.splitlines() if l.strip()][-16:]
    summary.append((g, "rc=%d %s" % (r.returncode, "✅通过" if r.returncode == 0 else "⚠未过"),
                    repo))
    out.append("\n## %s\n\n- 仓库：`%s`\n- 结果：rc=%d\n\n```text\n%s\n```\n"
               % (g, repo, r.returncode, "\n".join(tail)))

out.append("\n## 汇总\n\n| gate | 结果 | 仓库 |\n|---|---|---|\n")
for g, s, p in summary:
    out.append("| %s | %s | `%s` |\n" % (g, s, p))
out_dir = R / "reports"
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / ("acceptance-%s.md" % datetime.date.today().isoformat())).write_text(
    "".join(out), encoding="utf-8", newline="")

print("[acceptance] 对象 %d 个（从未运行）；本机有仓并已跑 %d 个"
      % (len(never), sum(1 for _g, s, _p in summary if s.startswith("rc="))))
for g, s, p in summary:
    print("  %-22s %s" % (g, s))
