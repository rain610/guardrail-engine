# -*- coding: utf-8 -*-
"""
agentguard/harness/agents/auto-gate.py —— 跨 Agent 通用门禁入口
================================================================================
设计目标：任何 AI agent（Claude Code / Codex / OpenCode / CodeBuddy / Hermes）
在任意端仓库工作目录下跑一行命令即可触发门禁，无需知道 gate 名。
它自动完成 3 件事：
  1. 用当前目录（或 --repo）匹配 config/harness.yaml，识别属于哪个 gate（客户_端）；
     （--only <file> 可指定"只检查这个文件"，编辑期钩子用它避免把整仓未提交改动算进来）
  2. 收集该仓库 git diff 的改动文件（.vue/.js/.ts/.cs）；
  3. 把文件交给 run_gate.py 跑对应门禁脚本。

用法（在端仓库根目录下执行；`<harness工程>` 用 `sh <harness工程>/sandbox/where.sh` 求值）：
  python <harness工程>/harness/agents/auto-gate.py                  # 用当前目录识别 gate
  python <harness工程>/harness/agents/auto-gate.py --repo D:/path/to/worktree
  python <harness工程>/harness/agents/auto-gate.py --all            # 全量扫描

退出码：0=通过，1=门禁拦截，2=配置/定位错误，3=非受管仓库(跳过)
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

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # = agentguard/
ROOT = os.path.dirname(HARNESS)                                        # = agentguard 工程根

# 台账「来源入口」：cli/mcp/hook 都设了自己的值，auto-gate 缺失 → 台账 entry 恒空。
# 中 16（评审 v13）：原先 setdefault —— 由 MCP/CLI 拉起时（MCP 的 repo 分支正是这么做的）
# 外层值胜出，台账把**真实执行者** auto-gate 记成了上游入口。现在硬赋值 + 另记上游。
_prev_entry = (os.environ.get("HARNESS_ENTRY") or "").strip()
os.environ["HARNESS_ENTRY"] = "auto-gate"
if _prev_entry and _prev_entry != "auto-gate":
    os.environ["HARNESS_ENTRY_VIA"] = _prev_entry

# 评审 v8：扩展名走**单一来源** checks/file_types.py（原先此处 8 种、pre-commit/post-edit 4 种，
# 清单分叉导致"编辑期看得见、提交期漏检"）。此处保留模块级别名供旧调用点兼容。
sys.path.insert(0, os.path.join(ROOT, "checks"))
try:
    import file_types as _ft
    _EXT = _ft.ALL_EXTS
except Exception:                     # checks/ 缺失时退回并集（fail-open 仅限此处，门禁主体另有守卫）
    _EXT = (".vue", ".js", ".ts", ".cs", ".tsx", ".jsx", ".css", ".scss")


def _norm(p):
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def _find_gate(cwd):
    """返回 (gate_name, repo_path)；cwd 不在任何受管仓库内则返回 (None, None)。
    含 worktree 识别：cwd 是某受管仓库的 git worktree（共享 .git）时，按主仓库归位。"""
    sys.path.insert(0, HARNESS)
    from _config import all_gates
    best, best_gate = None, None
    n = _norm(cwd)
    for name, g in all_gates().items():
        rp = g.get("_repo_path")
        if not rp or not os.path.isdir(rp):
            continue
        nr = _norm(rp)
        # 匹配：仓库路径是当前目录的前缀（或相等），取最长匹配
        if nr == n or n.startswith(nr + os.sep):
            if best is None or len(nr) > len(best):
                best, best_gate = nr, name
    if best_gate:
        return (best_gate, best)
    # worktree 兜底：cwd 不在登记清单，但若是某登记仓库的 git worktree
    # （同仓库族共享同一 common-dir：所有客户 worktree 都挂在主仓库 .git 下），
    # 按 common-dir 相等归位到对应 gate（临时分支改码场景必需）。
    try:
        clean_env = {k: v for k, v in os.environ.items()
                     if k not in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY")}
        def _common_dir(path):
            r = subprocess.run(["git", "-C", path, "rev-parse", "--git-common-dir"],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=15, env=clean_env)
            if r.returncode != 0:
                return None
            out = r.stdout.strip()
            # git 对仓内路径常返回相对形态（".git"）——必须基于【该仓自身】转绝对，
            # 不能用 abspath（基于进程 cwd 解析）：在临时 git 仓里跑 auto-gate 时
            # 会把所有登记仓的 common-dir 都算到临时仓下，恒等导致误匹配第一个 gate
            # （2026-09-09 实测：unmanaged 临时仓被误判成 hy_pc，rc=3 契约被破坏）。
            if not os.path.isabs(out):
                out = os.path.join(path, out)
            return _norm(os.path.abspath(out))
        cwd_common = _common_dir(cwd)
        if cwd_common is None:
            return (None, None)
        for name, g in all_gates().items():
            rp = g.get("_repo_path")
            if not rp or not os.path.isdir(rp):
                continue
            if _common_dir(rp) == cwd_common:
                # 用 cwd(实际 worktree)而非登记路径：改动收集必须在此 working tree 跑，
                # worktree 之间 index/working tree 相互独立。
                return (name, cwd)
    except Exception:
        pass
    return (None, None)


def _changed_files(repo, gate_name):
    """改动文件收集（**fail-closed**：返回 (files, err)，2026-09-15 评审 v3 P1）。

    收敛到 `_config.changed_files_ex`（与 post-edit 同一收集器）——旧版在这里自带一份实现、
    并把所有异常吞成 []，于是 main 打印「无改动文件，门禁通过」rc=0：**查不了被当成没改动**。
    gate_name 用于仓库级 gate 豁免扩展名过滤（file_types.is_repo_wide）。
    """
    if HARNESS not in sys.path:
        sys.path.insert(0, HARNESS)
    from _config import changed_files_ex
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(HARNESS), "checks"))
        import file_types
    except Exception as e:
        return [], "无法加载检查面清单 checks/file_types.py：%r" % (e,)
    return changed_files_ex(repo, file_types.exts_for(gate_name), gate_name=gate_name)



def main():
    args = sys.argv[1:]
    # 注意：不能用 $PWD —— git-bash/msys 会把它设成 /d/... 而非原生 Windows 路径，
    # Windows 的 python 拿 abspath 会错误扩展成 D:\\d\\MyProject... 匹配不上 config。
    # os.getcwd() 始终返回原生路径（D:\\MyProject\\...），可靠。
    cwd = os.getcwd()
    repo_arg = None
    full = "--all" in args
    only = []                     # --only <file>：只检查指定文件（编辑期用，见下）
    for i, a in enumerate(args):
        if a == "--repo" and i + 1 < len(args):
            repo_arg = args[i + 1]
        elif a == "--only" and i + 1 < len(args):
            only.append(args[i + 1])
    base = repo_arg or cwd

    gate_name, repo = _find_gate(base)
    if not gate_name:
        print(f"[auto-gate] {base} 不在 harness 受管仓库内，跳过门禁。")
        return 3

    # 位置参数契约：`--only` 的值只许是文件路径（2026-09-23 评审报告 H1）。
    # 为什么这里需要（而 pre-commit 不需要）：`--only` 的值来自**编辑器宿主的 payload**
    # （integrations/hooks/agentguard-gate.py 的 target_file()），是外部输入；
    # 而 pre-commit 的 files 一律是 `os.path.join(仓根, 暂存路径)` 的绝对路径，
    # 结构上不可能以 `-` 开头 —— 给它加一条永不触发的判据，只是又一条死判据。
    # 危害：run_gate 会把 `--mode loose` 这类项当自己的旗标消费（覆盖判定口径），
    # 实测能把一次拦截翻成放行、且台账不留痕。fail-closed 拒绝，不"悄悄丢掉再照跑"。
    _bad = [str(f) for f in only if str(f).startswith("-")]
    if _bad:
        print(f"[auto-gate] ✗ {gate_name}: --only 的值里有旗标形态的项 → 不放行（fail-closed）："
              f"{', '.join(repr(b) for b in _bad)}\n"
              f"            （以 '-' 开头的项会被 run_gate 当命令行参数消费，等于让被检查方改判定口径；"
              f"--only 只收文件路径。）", file=sys.stderr)
        return 2

    sys.path.insert(0, HARNESS)
    from _config import gate
    g = gate(gate_name)
    if not g.get("enabled", False):
        print(f"[auto-gate] gate {gate_name} 未启用，跳过。")
        return 0

    # run_on 触发点消费（2026-09-12 P3-3 接线，外部评审：配置项此前无消费方=死配置）。
    # 本 hook 是「编辑后」触发点：run_on 不含 post_edit 的 gate 在此跳过（提交时 pre-commit
    # 仍会拦，不存在放行风险）；要恢复编辑期反馈就在 config 里把它加回去。
    run_on = g.get("run_on") or ["pre_commit", "post_edit"]
    if "post_edit" not in run_on:
        # 第三轮 N-α：提示走 stderr，stdout 保持干净——调用方把 stdout 当"检查结论"读时，
        # 这句"跳过"会和"通过"混在一起（rc=0 只表示本次未检查，不代表通过）。
        print(f"[auto-gate] gate {gate_name} 的 run_on={run_on} 不含 post_edit，跳过编辑期检查"
              f"（提交时 pre-commit 仍会检查）。", file=sys.stderr)
        return 0

    # 高 8（评审 v13）：把**实际目标仓**转发给调度器。
    # pre-commit.py(:207-208) 早就这么做了，本文件（与 post-edit）没跟上——worktree 场景下
    # `_find_gate` 返回的是 cwd（真 worktree），而 run_gate 若拿 config 的 _repo_path（主仓）
    # 当检查基准，rel/changed_lines 全按主仓算 → worktree 里新写的违规被判「存量」→ 假放行。
    # 两者相同时不转发：run_gate 本来就用 config 值，等价且不改既有输出。
    _repo_flag = ["--repo", os.path.abspath(repo)] if (repo and repo != g.get("_repo_path")) else []

    if full:
        cmd = [sys.executable, os.path.join(HARNESS, "run_gate.py"), gate_name] + _repo_flag + ["--all"]
    elif only:
        # 编辑期精确口径（2026-09-13 用户拍板）：只检查**被编辑的那个文件**。
        # 原因：默认走 _changed_files(repo) 会把**整个仓的未提交改动**都算进来——脏工作区里
        # 别人的/上一位工具留下的问题会拦到当前这次编辑头上（实测撞过两次），又吵又难归因。
        # 提交期不受影响：pre-commit 与 Stop 钩子仍走全量收集（兜底不放松）。
        files = [f for f in only if os.path.isfile(f)]
        if not files:
            # 中 9（评审 v13）：--only 一个都没匹配上时原先打印「门禁通过」并 rc=0 ——
            # 与同文件 `_changed_files` 的 fail-closed（rc=2）自相矛盾：同样是"查不了/没查到"，
            # 一个说 rc=2 一个说通过。且这句是 stdout 上的**正式结论**，调用方会当真。
            # 对齐为 rc=2 + stderr（stdout 只留结论通道）。
            print(f"[auto-gate] ✗ {gate_name}: --only 指定的文件都不存在 → 不放行（fail-closed）："
                  f"{', '.join(only[:3])}", file=sys.stderr)
            return 2
        cmd = [sys.executable, os.path.join(HARNESS, "run_gate.py"), gate_name] + _repo_flag + files
    else:
        files, err = _changed_files(repo, gate_name)
        if err:
            # 评审 v3 P1：git 失败/仓不存在 = **查不了**，不是「没有改动」
            print(f"[auto-gate] ✗ {gate_name}: 无法收集改动文件 → 不放行（fail-closed）：{err}",
                  file=sys.stderr)
            return 2
        if not files:
            print(f"[auto-gate] {gate_name}: 无改动文件，门禁通过。")
            return 0
        cmd = [sys.executable, os.path.join(HARNESS, "run_gate.py"), gate_name] + _repo_flag + files

    print(f"[auto-gate] {gate_name} @ {repo}：{' '.join(cmd)}")
    rc = subprocess.run(cmd, cwd=HARNESS).returncode
    # 退出码归一：143（SIGTERM/128+15）和 120（超时）不在契约(0/1/2/3)内 → 归一为 2（基础设施错误）。
    if rc not in (0, 1, 2, 3):
        print(f"[auto-gate] 子进程返回非契约退出码 rc={rc}（可能是超时），归一为 rc=2",
              file=sys.stderr)
        rc = 2
    return rc


if __name__ == "__main__":
    sys.exit(main())