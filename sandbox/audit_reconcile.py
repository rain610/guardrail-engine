# -*- coding: utf-8 -*-
"""门禁对账（本机审计）：提交 × 判定台账，找出「有改动但无门禁记录」的提交。

一句话：拿本机 reports/gate_log.jsonl 对照每个端仓的真实提交历史，
回答评审最大缺口的第一问——哪些改动的提交没有伴随门禁通过记录。

背景（2026-09-14，整改项 A1）：
    服务端对账的落地形态（2026-09-14 起：不依赖内网 CI / runner）——本工具以「本机审计版」承接：
    对每个端仓、每个触碰代码文件的提交，在台账里找「同 gate、提交时刻前窗口内、
    targeted、rc=0」的记录；找不到就分类标注，产出报告供人工复核。

口径与边界（重要）：
    * 台账只记录本机运行的判定（ts 为本地时间）；因此默认只审计「本机作者的提交」
      （--author 可换，--all-authors 可全量），避免把同事机器的提交误判为漏跑。
    * 台账写入失败是静默的（观测不阻断原则）→ 本工具产物定性为「审计线索」，
      不自动阻断、不自动判罪；发现可疑项请人工核实。
    * 匹配用时间窗（默认：提交前 15 分钟内 ～ 提交后 120 秒内），非精确到提交哈希。
    * **证据强度（评审 v8 后显式声明）**：本工具产物是**线索级**证据 —— 时间窗匹配只能说明
      "提交前后本机跑过同 gate 且通过"，**不能**证明"这次提交的内容被检查过"。
      要升级到内容级证据，需要在台账里补 diff 指纹/文件集合（尚未做，见 Codex 报告 P1-8）。
      因此：报告可用于人工复核与追责线索，**不得**对外表述为"提交可自证"。

用法：
    python sandbox/audit_reconcile.py                  # 全部端仓，近 14 天，本机作者
    python sandbox/audit_reconcile.py --since 7d
    python sandbox/audit_reconcile.py --gate gxgk_pc   # 只审一个 gate
    python sandbox/audit_reconcile.py --repo <PATH> --gate gxgk_pc   # 显式单仓（调试）
    python sandbox/audit_reconcile.py --json           # 额外输出 JSON 到 stdout
    python sandbox/audit_reconcile.py --fail-on-suspicious   # 有可疑项时退出码 1

退出码：0=完成；1=完成且发现可疑项（仅 --fail-on-suspicious）；2=参数/环境错误。
"""
import argparse
import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "harness")
DEFAULT_LOG = os.environ.get("HARNESS_LOG_FILE") or os.path.join(ROOT, "reports", "gate_log.jsonl")

WINDOW_AFTER_S = 120  # 台账记录允许落在提交之后的最长间隔（秒）

CODE_EXTS = {
    "pc": {".vue", ".js", ".ts", ".jsx", ".tsx", ".css", ".scss", ".less", ".html"},
    "h5": {".vue", ".js", ".ts", ".css", ".scss", ".less", ".html"},
    "backend": {".cs", ".vue", ".js", ".ts", ".html", ".css"},
}


def run_git(repo, *args):
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def parse_since(text):
    """'14d' / '7d' / '48h' / ISO日期 → epoch 秒。"""
    text = (text or "").strip().lower()
    now = time.time()
    try:
        if text.endswith("d"):
            return now - int(text[:-1]) * 86400
        if text.endswith("h"):
            return now - int(text[:-1]) * 3600
        dt = datetime.strptime(text, "%Y-%m-%d")
        return dt.timestamp()
    except Exception:
        print("[reconcile] ✗ --since 格式应为 14d / 48h / 2026-09-01，收到：%r" % text)
        sys.exit(2)


def gate_endpoint(gate_name):
    for ep in ("pc", "h5", "backend"):
        if gate_name.endswith("_" + ep) or gate_name == ep:
            return ep
    return "any"


def load_records(log_path):
    """读台账 → {gate: [(epoch, rc, scope)]} 与统计。"""
    by_gate = {}
    n = 0
    n_targeted = 0
    if os.path.isfile(log_path):
        with io.open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts = datetime.strptime(rec.get("ts", ""), "%Y-%m-%dT%H:%M:%S").timestamp()
                except Exception:
                    continue
                gate = rec.get("gate") or ""
                scope = rec.get("scope") or ""
                rc = rec.get("rc")
                by_gate.setdefault(gate, []).append((ts, rc, scope))
                n += 1
                if scope == "targeted":
                    n_targeted += 1
    return by_gate, n, n_targeted


def collect_commits(repo, since_epoch):
    """git log → [{sha, epoch, email, name, subject, files[]}]（跳过无文件的合并提交）。"""
    since_iso = datetime.fromtimestamp(since_epoch).strftime("%Y-%m-%dT%H:%M:%S")
    rc, out, _ = run_git(repo, "log", "--since=" + since_iso, "--no-renames",
                         "--pretty=format:@@%H|%cI|%ce|%cn|%s", "--name-only")
    if rc != 0:
        return None
    commits = []
    cur = None
    for line in out.split("\n"):
        if line.startswith("@@"):
            if cur is not None:
                commits.append(cur)
            parts = line[2:].split("|", 4)
            if len(parts) < 4:
                cur = None
                continue
            try:
                ctime = datetime.fromisoformat(parts[1]).timestamp()
            except Exception:
                ctime = 0.0
            cur = {"sha": parts[0], "epoch": ctime, "email": (parts[2] or "").lower(),
                   "name": parts[3] or "", "subject": (parts[4] if len(parts) > 4 else ""), "files": []}
        elif line.strip() and cur is not None:
            cur["files"].append(line.strip())
    if cur is not None:
        commits.append(cur)
    return commits


def is_code_file(path, endpoint):
    exts = CODE_EXTS.get(endpoint) or set().union(*CODE_EXTS.values())
    dot = os.path.splitext(path.lower())[1]
    return dot in exts


def hooks_status(repo):
    rc, out, _ = run_git(repo, "config", "--get", "core.hooksPath")
    val = (out or "").strip()
    if not val:
        return "未设置", False
    norm = val.replace("\\", "/").rstrip("/").lower()
    ok = norm.endswith("harness/hooks-dir")
    return val, ok


def local_author(repo):
    for key in ("user.email", "user.name"):
        rc, out, _ = run_git(repo, "config", "--get", key)
        val = (out or "").strip()
        if val:
            return val.lower()
    return ""


def config_mtime(repo):
    """该仓 .git/config 的最后修改时间（接线时间佐证；兼容 worktree：取 common dir）。"""
    gd = ""
    for flag in ("--git-common-dir", "--git-dir"):
        rc, out, _ = run_git(repo, "rev-parse", flag)
        gd = (out or "").strip()
        if gd:
            break
    if not gd:
        return None
    if not os.path.isabs(gd):
        gd = os.path.join(repo, gd)
    try:
        return os.path.getmtime(os.path.join(gd, "config"))
    except OSError:
        return None


def classify(commit, records, window_before_s, gate_first_epoch):
    """窗口内找记录：rc=0 → OK；有非通过记录 → BLOCKED；早于台账期 → PRE_RECORD；否则 NO_RECORD。"""
    lo = commit["epoch"] - window_before_s
    hi = commit["epoch"] + WINDOW_AFTER_S
    hits = [(ts, rc, scope) for (ts, rc, scope) in records if lo <= ts <= hi]
    if hits:
        if any(rc == 0 for _, rc, _ in hits):
            best = min((t for t, rc, _ in hits if rc == 0), key=lambda t: abs(t - commit["epoch"]))
            return "OK", "", best
        rcs = sorted(set(str(rc) for _, rc, _ in hits))
        return "BLOCKED", "窗口内存在非通过记录 rc=" + ",".join(rcs), None
    if gate_first_epoch is not None and commit["epoch"] < gate_first_epoch:
        return "PRE_RECORD", "早于该 gate 首条台账记录（%s）" % fmt_ts(gate_first_epoch), None
    return "NO_RECORD", "", None


def audit_one(repo, gate, records, since_epoch, window_before_s, author_filter, all_authors, fallback_first):
    """返回 (commits_considered, code_commits, items[], hook_desc, hook_ok, author_desc)。"""
    hook_desc, hook_ok = hooks_status(repo)
    local = local_author(repo)
    cfg_mt = config_mtime(repo)
    commits = collect_commits(repo, since_epoch)
    if commits is None:
        return None
    gate_records = records.get(gate, [])
    gate_first = min((ts for ts, _, _ in gate_records), default=None)
    ref_first = gate_first if gate_first is not None else fallback_first
    items = []
    considered = 0
    code_commits = 0
    for c in commits:
        if not c["files"]:
            continue
        if not all_authors:
            if author_filter:
                probe = author_filter.lower()
                if probe not in c["email"] and probe not in c["name"].lower():
                    continue
            elif local:
                if c["email"] != local and c["name"].lower() != local:
                    continue
        considered += 1
        ep = gate_endpoint(gate)
        if not any(is_code_file(f, ep) for f in c["files"]):
            continue
        code_commits += 1
        status, note, rec_ts = classify(c, gate_records, window_before_s, ref_first)
        if status == "NO_RECORD":
            if gate_first is None:
                note = "该 gate 本机无台账记录"
            elif cfg_mt:
                cmt = time.strftime("%Y-%m-%d %H:%M", time.localtime(cfg_mt))
                if c["epoch"] < cfg_mt:
                    note = "该仓配置修改于提交之后（%s）——可能属接线前" % cmt
                else:
                    note = "该仓配置最后修改 %s" % cmt
        items.append({"repo": repo, "gate": gate, "sha": c["sha"], "email": c["email"],
                      "subject": c["subject"], "files": len(c["files"]), "status": status,
                      "note": note, "hooks": hook_desc, "hooks_ok": hook_ok,
                      "ts": c["epoch"], "rec_ts": rec_ts})
    return considered, code_commits, items, hook_desc, hook_ok, (author_filter or local or "(不过滤)")


def fmt_ts(epoch):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))


def main(argv=None):
    ap = argparse.ArgumentParser(description="门禁对账（本机审计）：提交 × 台账")
    ap.add_argument("--since", default="14d", help="回溯窗口：14d / 48h / 2026-09-01")
    ap.add_argument("--gate", default=None, help="只审指定 gate（默认全部）")
    ap.add_argument("--repo", default=None, help="显式单仓路径（调试/测试用，需配 --gate）")
    ap.add_argument("--log", default=DEFAULT_LOG, help="台账路径（默认 reports/gate_log.jsonl 或 HARNESS_LOG_FILE）")
    ap.add_argument("--window", type=int, default=15, help="提交前匹配窗口（分钟，默认 15）")
    ap.add_argument("--author", default=None, help="作者过滤子串（默认自动取本机 user.email）")
    ap.add_argument("--all-authors", action="store_true", help="不过滤作者")
    ap.add_argument("--json", action="store_true", help="额外输出 JSON 到 stdout")
    ap.add_argument("--out", default=None, help="报告输出路径（默认 reports/reconcile_<时间戳>.md）")
    ap.add_argument("--max-rows", type=int, default=200, help="报告明细行上限（默认 200）")
    ap.add_argument("--fail-on-suspicious", action="store_true", help="有可疑项时退出码 1")
    args = ap.parse_args(argv)

    since_epoch = parse_since(args.since)
    records, n_rec, n_tgt = load_records(args.log)
    window_before_s = max(1, args.window) * 60

    targets = []  # [(gate, repo_path)]
    skipped = []
    if args.repo:
        if not args.gate:
            print("[reconcile] ✗ 单仓模式需同时给 --gate")
            sys.exit(2)
        targets.append((args.gate, args.repo))
    else:
        sys.path.insert(0, HARNESS)
        import _config
        for gate, cfg in sorted(_config.all_gates().items()):
            if args.gate and gate != args.gate:
                continue
            path = cfg.get("_repo_path") or ""
            if path and os.path.isdir(path):
                targets.append((gate, path))
            else:
                skipped.append(gate)

    items = []
    repo_rows = []
    commits_considered = 0
    code_commits = 0
    author_descs = []
    fallback_first = min((ts for rs in records.values() for ts, _, _ in rs), default=None)
    for gate, repo in targets:
        res = audit_one(repo, gate, records, since_epoch, window_before_s, args.author, args.all_authors, fallback_first)
        if res is None:
            skipped.append(gate)
            continue
        cc, ccode, rows, hook_desc, hook_ok, adesc = res
        commits_considered += cc
        code_commits += ccode
        items.extend(rows)
        if not author_descs:
            author_descs.append(adesc)
        repo_rows.append({"gate": gate, "repo": repo, "hooks": hook_desc, "hooks_ok": hook_ok,
                          "commits": cc, "code_commits": ccode})

    ok = sum(1 for i in items if i["status"] == "OK")
    pre = sum(1 for i in items if i["status"] == "PRE_RECORD")
    no_record = sum(1 for i in items if i["status"] == "NO_RECORD")
    blocked = sum(1 for i in items if i["status"] == "BLOCKED")
    suspicious = no_record + blocked

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_path = args.out or os.path.join(ROOT, "reports", "reconcile_%s.md" % stamp)

    # ---- 报告 ----
    L = []
    L.append("# 门禁对账报告（本机审计）· %s" % time.strftime("%Y-%m-%d %H:%M"))
    L.append("")
    L.append("- 范围：since=%s ｜ 仓库 %d 个（跳过 %d）｜ 作者过滤：%s ｜ 匹配窗口：提交前 %d 分钟 ～ 提交后 %d 秒"
             % (args.since, len(targets), len(skipped), author_descs[0] if author_descs else "(无)", args.window, WINDOW_AFTER_S))
    L.append("- 台账：%s（%d 条记录 / %d 条 targeted）" % (args.log, n_rec, n_tgt))
    L.append("- 说明：审计线索性质，命中「⚠/⛔」不等于定罪；台账缺失也可能来自日志写失败。")
    L.append("")
    L.append("## 摘要")
    L.append("")
    L.append("| 分类 | 数量 |")
    L.append("|---|---|")
    L.append("| ✅ 通过记录 | %d |" % ok)
    L.append("| ◻ 早于台账期（未接入期，参考） | %d |" % pre)
    L.append("| ⚠ 无门禁记录 | %d |" % no_record)
    L.append("| ⛔ 存在非通过记录 | %d |" % blocked)
    L.append("")
    L.append("（触碰代码文件的提交 %d 条；按作者过滤后参与判断的提交 %d 条）" % (code_commits, commits_considered))
    L.append("")

    if repo_rows:
        L.append("## 钩子体检")
        L.append("")
        L.append("| 仓库(gate) | hooksPath | 状态 | 提交(作者过滤后) | 代码提交 |")
        L.append("|---|---|---|---|---|")
        for r in repo_rows:
            L.append("| %s | %s | %s | %d | %d |" % (r["gate"], r["hooks"], "✅ 已装" if r["hooks_ok"] else "⚠ 未装/非共享分发器", r["commits"], r["code_commits"]))
        L.append("")

    for status, title in (("OK", "✅ 通过记录"), ("PRE_RECORD", "◻ 早于台账期（未接入期，参考）"),
                          ("NO_RECORD", "⚠ 无门禁记录"), ("BLOCKED", "⛔ 存在非通过记录")):
        subset = [i for i in items if i["status"] == status]
        if not subset:
            continue
        subset.sort(key=lambda x: x["ts"], reverse=True)
        L.append("## %s（%d）" % (title, len(subset)))
        L.append("")
        if status == "OK":
            L.append("| 仓库 | 提交 | 时间 | 匹配记录差 |")
            L.append("|---|---|---|---|")
            for i in subset[:args.max_rows]:
                delta = int(i["ts"] - (i["rec_ts"] or i["ts"]))
                when = ("%d 秒前有通过记录" % delta) if delta >= 0 else ("提交后 %d 秒才有记录" % (-delta))
                L.append("| %s | `%s` | %s | %s |" % (
                    os.path.basename(i["repo"]), i["sha"][:10], fmt_ts(i["ts"]), when))
        elif status == "PRE_RECORD":
            L.append("| 仓库 | 提交 | 时间 | 文件数 | 备注 |")
            L.append("|---|---|---|---|---|")
            for i in subset[:args.max_rows]:
                L.append("| %s | `%s` | %s | %d | %s |" % (
                    os.path.basename(i["repo"]), i["sha"][:10], fmt_ts(i["ts"]), i["files"], i["note"]))
        else:
            L.append("| 仓库 | 提交 | 时间 | 文件数 | 钩子状态 | 备注 |")
            L.append("|---|---|---|---|---|---|")
            for i in subset[:args.max_rows]:
                cell = (i["note"] + " ｜ " + i["subject"][:32]) if i["note"] else i["subject"][:40]
                L.append("| %s | `%s` | %s | %d | %s | %s |" % (
                    os.path.basename(i["repo"]), i["sha"][:10], fmt_ts(i["ts"]), i["files"],
                    "已装" if i["hooks_ok"] else "未装", cell))
        if len(subset) > args.max_rows:
            L.append("")
            L.append("（其余 %d 条见 JSON 输出）" % (len(subset) - args.max_rows))
        L.append("")

    if skipped:
        L.append("## 跳过")
        L.append("")
        for s in skipped:
            L.append("- %s" % s)
        L.append("")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")

    summary = {"repos": len(targets), "skipped": len(skipped), "commits_considered": commits_considered,
               "code_commits": code_commits, "ok": ok, "pre_record": pre, "no_record": no_record,
               "blocked": blocked, "suspicious": suspicious, "window_before_min": args.window,
               "log": args.log, "out": out_path}
    if args.json:
        print(json.dumps({"summary": summary, "items": items}, ensure_ascii=False, indent=1))

    def _msg(*a):
        print(*a, file=(sys.stderr if args.json else sys.stdout))

    _msg("[reconcile] 仓库 %d 个 ｜ 提交 %d ｜ 代码提交 %d ｜ ✅ %d ｜ ◻ %d ｜ ⚠ %d ｜ ⛔ %d"
         % (len(targets), commits_considered, code_commits, ok, pre, no_record, blocked))
    _msg("[reconcile] 报告：%s" % out_path)
    for i in [x for x in items if x["status"] != "OK"][:8]:
        _msg("  - [%s] %s %s %s" % (i["status"], os.path.basename(i["repo"]), i["sha"][:8], fmt_ts(i["ts"])))

    if args.fail_on_suspicious and suspicious:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
