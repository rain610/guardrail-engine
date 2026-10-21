# -*- coding: utf-8 -*-
"""
agentguard/hooks/post-edit.py —— 改码后门禁（Codex hooks 的 post-edit 点）
=================================================================================
职责：调 run_gate 跑对应端门禁（自动取 git 改动文件，或显式传文件）。
改动文件口径（2026-09-03 统一）：未暂存 diff + 已暂存 diff + 未跟踪新文件，
与 auto-gate 共用 _config.changed_files_ex 收集器（**带失败信号**的那一支：返回 (files, err)，
err 非空 = 查不了 → 调用方 fail-closed。宽容包装 changed_files() 现在只剩测试在用）。
——旧版只看未暂存 diff：文件一旦 git add 就漏检、新建未跟踪文件漏检，与 auto-gate 口径不一致。
用法：python harness/hooks/post-edit.py <gate名> [文件...]
退出码：0=通过，1=拦截
"""
import sys, os, subprocess
# GBK 兼容（2026-09-12 补，第二轮评审 N9）：GUI/原生 cp936 控制台触发时，
# print Unicode 符号会 UnicodeEncodeError 崩 → 门禁误拦。与 run_gate/pre-commit 同款兜底。
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 中 16（评审 v13）：台账「来源入口」。本文件此前**根本不设** HARNESS_ENTRY ——
# 编辑期跑出来的判定在 reports/gate_log.jsonl 里没有来源，cli.py log 的「入口分布」把它算丢。
# 硬赋值自己的名字（不是 setdefault：外层残留的值不许冒充本次入口），上游是谁另记一份。
_prev_entry = (os.environ.get("HARNESS_ENTRY") or "").strip()
os.environ["HARNESS_ENTRY"] = "post-edit"
if _prev_entry and _prev_entry != "post-edit":
    os.environ["HARNESS_ENTRY_VIA"] = _prev_entry


def main():
    # --repo <路径>（2026-09-11）：显式指定目标仓，优先于 config（对齐 pre-commit.py，
    # 也让链路测试可用 HARNESS_CONFIG + 临时仓密闭运行，不绑真实端仓路径）。
    args = sys.argv[1:]
    repo_override = None
    if "--repo" in args:
        i = args.index("--repo")
        if i + 1 < len(args):
            repo_override = args[i + 1]
            args = args[:i] + args[i + 2:]
    gate_name = args[0] if args else "example"
    files = args[1:]
    sys.path.insert(0, HARNESS)
    from _config import gate, changed_files_ex
    g = gate(gate_name)
    repo = repo_override or g.get("_repo_path")
    if not files and not repo:
        # 评审 v3 P1：没有可查的仓 = 查不了 → fail-closed（旧版落到下面「无改动通过」rc=0）
        print("[post-edit] ✗ %s：gate 未登记仓库（无法收集改动文件）→ 不放行（fail-closed）" % gate_name,
              file=sys.stderr)
        return 2
    if not files:
        # 评审 v8：扩展名走单一来源（原先这里 4 种 < auto-gate 8 种 → 编辑期看得见、提交期漏检）
        # 评审 v3 P1：git 失败不再当「无改动」（查不了 → rc=2），仓库级 gate 不吃扩展名。
        sys.path.insert(0, os.path.join(os.path.dirname(HARNESS), "checks"))
        import file_types
        files, err = changed_files_ex(repo, file_types.exts_for(gate_name), gate_name=gate_name)
        if err:
            print("[post-edit] ✗ %s：无法收集改动文件 → 不放行（fail-closed）：%s" % (gate_name, err),
                  file=sys.stderr)
            return 2
    if not files:
        # 空改动护栏（对齐 pre-commit / auto-gate，2026-09-04）：无改动时必须直接放行。
        # 缺了它空文件列表会把 checks 脚本带进"无位置参数→全量扫描"分支，
        # 让与本次改动无关的存量问题（CRLF/console.log 等）误拦 post-edit。
        print("[post-edit] 无改动文件，门禁通过。")
        return 0
    cmd = [sys.executable, os.path.join(HARNESS, "run_gate.py"), gate_name]
    # 高 8（评审 v13）：把**实际目标仓**转发给调度器。
    # pre-commit.py(:207-208) 早就这么做了，post-edit 与 auto-gate 没跟上——worktree 场景下
    # run_gate 用 config 的 _repo_path（策略归属＝主仓）当检查基准 → rel/changed_lines 全按主仓算
    # → worktree 里**新写的违规被判「存量」** → 编辑期 rc=0 放行（提交期才拦，反馈自相矛盾）。
    # 只在两者不同时才转发：相同时 run_gate 本来就用 config 值，等价且不改输出。
    if repo and repo != g.get("_repo_path"):
        cmd += ["--repo", os.path.abspath(repo)]
    cmd += files
    print(f"[post-edit] 跑门禁 {gate_name}")
    return subprocess.run(cmd, cwd=HARNESS, stdin=subprocess.DEVNULL).returncode


if __name__ == "__main__":
    sys.exit(main())
