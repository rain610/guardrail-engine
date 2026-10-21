# -*- coding: utf-8 -*-
"""
agentguard/hooks/pre-commit.py —— 提交前门禁（Codex hooks 的 pre-commit 点）
===================================================================================
职责：① 先跑保护循环 guard（防 AI 为过门禁而改门禁）
      ② 再调 run_gate 跑对应端门禁（取 git diff --cached 文件）。被各端 .git/hooks/pre-commit 调。
用法：python harness/hooks/pre-commit.py <gate名> [文件...]
退出码：0=放行，1=拦截

改动文件口径（2026-09-03 统一约定）：
  本钩子只看 staged（git diff --cached）——语义是「只拦真正进历史的内容」；
  工作区中尚未暂存的改动由 post-edit / auto-gate（口径=diff+cached+untracked）负责。
"""
import sys, os, shutil, subprocess, tempfile

# 台账「来源入口」（打包路线 2026-09-14）：提交钩子链的判定记为 hook。
# 中 16（评审 v13）：原先用 setdefault —— 外层环境里已有的 HARNESS_ENTRY **永远胜出**，
# 于是「在 MCP/CLI 会话里 git commit」会把这次提交记成 mcp/cli，来源入口是错的。
# 现在硬赋值本次执行者，上游是谁另记一份（两个事实都在台账里）。
_prev_entry = (os.environ.get("HARNESS_ENTRY") or "").strip()
os.environ["HARNESS_ENTRY"] = "hook"
if _prev_entry and _prev_entry != "hook":
    os.environ["HARNESS_ENTRY_VIA"] = _prev_entry

# 语法传感器策略（2026-09-15 用户拍板）：**提交期 required**（node 缺失/调用失败 = 未检查 = 拦），
# 编辑期不注入（避免编辑器里频繁打扰）。setdefault：显式设 0 仍可退出（逃生门，别写进共享配置）。
os.environ.setdefault("HARNESS_NODE_REQUIRED", "1")

# GBK 兼容（2026-09-10 doctor 首跑实测：本入口是 6 个门禁脚本里唯一漏 UTF-8 兜底的——
# GUI git 客户端触发 hook 时控制台是 GBK，print ❌/✅ 直接 UnicodeEncodeError 崩溃，
# 门禁异常退出误拦所有提交且报错乱码。对齐 7bf52a0 其余入口的兜底写法）。
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # = harness/
ROOT = os.path.dirname(HARNESS)  # 工程根 agentguard/
LIB = HARNESS  # _config.py 就在 harness/


def main():
    # gate 名来源（2026-09-12 hooksPath 升级）：
    #   ① 显式 argv[1]（旧 install_hooks.sh 拷贝模式 / 手动调用 / 单测）
    #   ② 无参（core.hooksPath 共享分发器模式）→ 必须靠 cwd 纠偏识别 gate；
    #      纠偏失败 = 该仓未纳管 → 打印指引后 rc=2（fail-closed），绝不 fallback 到
    #      某个默认 gate 误跑别人的规则（旧默认 gate 是某次误跑事故的根源之一）。
    gate_name = sys.argv[1] if len(sys.argv) > 1 else None
    rest = sys.argv[2:] if gate_name else sys.argv[1:]
    repo_override = None
    if "--repo" in rest:
        i = rest.index("--repo")
        if i + 1 < len(rest):
            repo_override = rest[i + 1]
            rest = rest[:i] + rest[i+2:]
    files = rest
    sys.path.insert(0, LIB)

    # worktree 纠偏（2026-09-07）：hook 安装在共享 .git/hooks，gate 是安装时写死的
    # （如 hy_backend 指向主仓）。但 worktree（如 hzy）里提交时，cwd 才是真仓库。
    # 若 cwd 所在仓在 config 里登记了另一个 gate，则以 cwd 识别为准（覆盖写死值）。
    try:
        # auto-gate.py 文件名带连字符，不能 import；用 importlib 从路径加载
        import importlib.util
        _spec = importlib.util.spec_from_file_location(
            "auto_gate", os.path.join(ROOT, "harness", "agents", "auto-gate.py"))
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        cwd_gate, cwd_repo = _mod._find_gate(os.getcwd())
        # 2026-09-15（CI 回归修复）：**显式 --repo 是权威，cwd 纠偏不得劫持它**。
        # 起因：工程仓自己注册成 harness_self 后（2026-09-14 自检 gate），任何 cwd=工程根的
        # 调用——测试夹具、工具、单测——都被纠偏成 harness_self：传进来的端 gate 及其
        # staged 违规文件被拿去跑自检 → rc=0 **假绿**（本机复现；CI 上因 config repo 不存在
        # 表现为 rc=2 假红）。cwd 纠偏只服务于「hooksPath 分发器 / worktree」场景，
        # 那种调用**不带 --repo**；带 --repo 说明调用方已明确指定目标仓。
        if cwd_gate and not repo_override:
            if gate_name and cwd_gate != gate_name:
                print(f"[pre-commit] worktree 纠偏: {gate_name} -> {cwd_gate} (cwd 仓)")
            gate_name = cwd_gate
        elif cwd_gate and repo_override and cwd_gate != gate_name:
            print(f"[pre-commit] 忽略 cwd 纠偏（{gate_name} -> {cwd_gate}）：本次调用显式指定了 --repo")
    except Exception:
        pass  # 识别失败沿用安装时 gate（显式传参模式旧行为）

    if not gate_name:
        # hooksPath 共享分发器模式 + cwd 纠偏失败 = 该仓未纳管 → fail-closed 明确指引
        print("[pre-commit] 当前目录不在 harness 受管仓库内（未注册 gate）。")
        print("  - 若该仓应纳管：在 config/harness.yaml 注册 repo+gate 后，")
        print(f"    在仓根跑 sh {ROOT}/sandbox/install_hooks.sh <gate名>")
        print("  - 若不应纳管：此钩子经 core.hooksPath 全局生效，如需绕过请说明原因（git commit --no-verify）。")
        return 2

    # ① 保护循环（防 AI 为过门禁而改门禁）在下方 target_repo 确定之后执行 ——
    # 顺序理由见 _run_guard 的注释（评审 v13 严重 4）：哨兵需要知道"正在提交哪个仓"，
    # 而这个事实要等 target_repo 解析完才有。**它仍然跑在任何检查之前**，只是不再跑在
    # 仓库解析之前 —— 解析仓不是"跑门禁"。
    from _config import gate
    g = gate(gate_name)
    # run_on 消费（2026-09-12 P3-3）：本路径是「提交」触发点。若某 gate 声明里没有
    # pre_commit，按项目 fail-closed 约定**仍然执行**检查并告警配置不一致——
    # 绝不允许因配置疏漏出现"提交不被检查"。
    run_on = g.get("run_on") or ["pre_commit"]
    if "pre_commit" not in run_on:
        print(f"[pre-commit] ⚠ gate {gate_name} 的 run_on={run_on} 不含 pre_commit——"
              f"按 fail-closed 仍执行检查；若确实不想在提交时拦，请禁用该 gate 而非改 run_on。")
    repo = g.get("_repo_path")
    # staged 文件一律取「当前提交仓」的 diff（cwd），而不是 config repo ——
    # worktree 场景二者不同，config repo 只是策略归属。
    # 评审 v8（Codex P0）：**区分「没有暂存文件」与「查不了」** ——
    # 旧写法把任何异常都变成 files=[] → return 0：git 不可用 / 路径不对 / 配置坏掉时
    # 提交静默通过（钩子看着装上了、其实没检查）。本项目其它位置（分发器 G2、未纳管仓）
    # 都是 fail-closed，这里对齐：查不了 = rc=2 + 说清原因。
    # 提交仓判定顺序: ①cwd 识别出的受管仓(worktree 纠偏同源,链测传 --repo 也可)
    # ②git toplevel(hook 场景 cwd=仓库根) ③config repo
    target_repo = None
    if repo_override:
        if not os.path.isdir(repo_override):
            print("[pre-commit] ✗ --repo 不存在或不是目录：%s → 不放行（fail-closed）" % repo_override)
            return 2
        target_repo = repo_override
    if not target_repo and cwd_repo and os.path.isdir(cwd_repo):
        target_repo = cwd_repo
    try:
        if not target_repo:
            tr = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=30)
            top = (tr.stdout or "").strip()
            if tr.returncode != 0 or not top:
                print("[pre-commit] ✗ 无法确定提交仓（git rev-parse 失败：%s）"
                      % ((tr.stderr or "").strip()[:160] or "无输出"))
                print("[pre-commit] 无法完成检查 = 不放行（fail-closed）；这是环境/配置问题，不是业务代码问题。")
                return 2
            target_repo = top
        # --diff-filter=d：排除**已暂存删除**的文件。删除的文件在 index 里已不存在，
        # 若不排除，检查器只能读到工作区残留副本 → 又变成读工作区（P0 的另一面）。
        # 2026-09-16（剖析报告续）：本调用原先 `text=True` 但**没写 encoding** —— Python 会按
        # **本机 locale**（中文 Windows = GBK）解码 git 输出的 UTF-8 路径，于是含中文的文件名
        # 变成 mojibake（`README_推广接入.md` → `README_鎺ㄥ箍鎺ュ叆.md`），下游 checkout-index
        # 就报 "is not in the cache" → fail-closed 阻断提交，而提示只说"配置/环境问题"。
        # 实测：本仓 31 个非 ASCII 路径全部中招，端仓（中文文件名常见）同样。
        # 同函数下面 checkout-index 那处**写了** encoding="utf-8" —— 同一个函数里两种写法，
        # 这才是 bug 的形状。用 `-z`：NUL 分隔，顺带不再依赖 `core.quotepath`（默认 true 时
        # 非 ASCII 路径会被 C 引号+八进制转义，同样对不上 index）。
        # 这是**加强**而非放松：此前这些文件根本检查不到（直接 fail-closed），现在真检查。
        r = subprocess.run(["git", "-C", target_repo, "diff", "--cached", "--name-only",
                            "--diff-filter=d", "-z"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        if r.returncode != 0:
            print("[pre-commit] ✗ git 无法读取暂存区（%s）：%s"
                  % (target_repo, (r.stderr or "").strip()[:160]))
            print("[pre-commit] 无法完成检查 = 不放行（fail-closed）。")
            return 2
    except Exception as e:
        print("[pre-commit] ✗ 调用 git 失败：%r → 不放行（fail-closed）" % (e,))
        return 2

    # ① 保护循环：target_repo 已确定，现在把"谁在提交"一并交给哨兵
    guard_rc = _run_guard(gate_name, target_repo)
    if guard_rc != 0:
        return guard_rc

    try:
        sys.path.insert(0, os.path.join(ROOT, "checks"))
        import file_types                      # 单一来源（评审 v8）：扩展名不再各处手抄
        exts = file_types.exts_for(gate_name)
    except Exception as e:
        print("[pre-commit] ✗ 无法加载检查面清单 checks/file_types.py：%r → 不放行（fail-closed）" % (e,))
        return 2
    # -z 输出：NUL 分隔（末项后也有一个 NUL → 会有空串，滤掉）。不再 strip —— 文件名
    # 首尾空格是合法的，strip 会把它改成另一个路径。
    staged = [l for l in (r.stdout or "").split("\x00") if l]
    files = [os.path.join(target_repo, f) for f in staged if file_types.matches(f, gate_name)]
    if staged and not files:
        # 可见性（评审 v8）：原来"暂存了但一个都不在检查面内"是静默的
        # （仓库级 gate 如 harness_self 现在 matches() 恒真 → 不会走到这里）
        print("[pre-commit] 提示：暂存的 %d 个文件都不在本 gate 的检查面内（%s）→ 本次不检查"
              % (len(staged), ", ".join(exts)))
    if not files:
        return 0
    # ── 评审 v3 P0（2026-09-15）：把**暂存内容**展开到 overlay，检查器只读 index 版本 ──
    # 旧行为：只从 index 取文件名、把工作区路径交给检查器 → 检查器 open(工作区)
    # → 「暂存坏版本 + 工作区改回好版本」rc=0 放过（实测复现）。这里是唯一的修法入口。
    overlay = tempfile.mkdtemp(prefix="harness-staged-")
    try:
        rels = []
        for f in files:
            try:
                rl = os.path.relpath(f, target_repo).replace(os.sep, "/")
            except ValueError:
                rl = ""
            if not rl or rl.startswith(".."):
                print("[pre-commit] ✗ 暂存文件不在提交仓内：%s → 不放行（fail-closed）" % f)
                return 2
            rels.append(rl)
        prefix = overlay.replace("\\", "/").rstrip("/") + "/"
        ci = subprocess.run(["git", "-C", target_repo, "checkout-index", "-f",
                             "--prefix=%s" % prefix, "--"] + rels,
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=60)
        if ci.returncode != 0:
            print("[pre-commit] ✗ 无法展开暂存内容（git checkout-index：%s）→ 不放行（fail-closed）"
                  % ((ci.stderr or "").strip()[:160] or "无输出"))
            return 2
        missing = [rl for rl in rels if not os.path.isfile(os.path.join(overlay, rl))]
        if missing:
            print("[pre-commit] ✗ 暂存副本不完整（%s…）→ 不放行（fail-closed）：宁可报错，"
                  "也绝不回退去读工作区版本（评审 v3 P0）" % ", ".join(missing[:3]))
            return 2
        os.environ["HARNESS_STAGED_OVERLAY"] = overlay
        os.environ["HARNESS_STAGED_REPO"] = os.path.abspath(target_repo)
        # 评审 v9（Codex P1-1，实测复现）：必须把**实际提交仓**交给调度器。
        # 旧行为只传文件路径 → run_gate 用 config 的 _repo_path（策略归属，worktree 场景是主仓）
        # 当检查基准 → rel/changed_lines 全按主仓算 → worktree 里新增的违规被判"存量" → rc=0 放过。
        cmd = [sys.executable, os.path.join(HARNESS, "run_gate.py"), gate_name,
               "--repo", os.path.abspath(target_repo)] + files
        print(f"[pre-commit] 跑门禁 {gate_name}（检查**暂存内容** {len(files)} 个文件）")
        r = subprocess.run(cmd, cwd=HARNESS, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            print("[pre-commit] 门禁未通过，commit 已阻断。修复后重试。")
        return r.returncode
    finally:
        shutil.rmtree(overlay, ignore_errors=True)


def _run_guard(gate_name, repo):
    """调用 checks/harness_guard.py（保护哨兵）。
    stdin=subprocess.DEVNULL（2026-09-11）：防子进程继承 GUI/CI 打开的管道后，
    任何一处 stdin 读取永久阻塞 → commit 静默挂死（外部评审 P0 的链路层兜底）。

    评审 v13 严重 4：本函数原先把子进程的**工作目录写死成工程根**再调哨兵，而哨兵判 self
    模式**全靠 cwd 推断**（hook 场景 cwd 才是被提交仓根）。写死之后每一次端仓提交都被判成
    "本仓提交"，再撞上"pre-commit 阶段读不到提交消息 → 本阶段放行"，哨兵在真实提交链路上
    **恒返回 0** —— 不是偶尔漏拦，是保护从未生效过一次，而输出里始终印着
    "保护循环：检查…"，看着一切正常。（回归锁把这条钉在源码上：
    `tests/test_review_v13_batch5.py` 禁止本函数出现写死 cwd 的写法。）

    修法两条，缺一不可：
      ① 不再改 cwd（继承调用方的 cwd，让 dispatcher / 手工调用那条"cwd 推断"老路径仍然成立）；
      ② 把调用方**已经知道的事实**（本次提交的是哪个仓）用 `--repo` 显式告诉哨兵，
         让模式判定由这个权威参数决定，而不是由一个随手可覆盖的环境信号决定。
    """
    guard = os.path.join(ROOT, "checks", "harness_guard.py")
    if not os.path.exists(guard):
        return 0  # 无 guard 则跳过保护（门禁层兜底）
    print("[pre-commit] 保护循环：检查 harness 工程仓是否有未提交的门禁文件改动")
    cmd = [sys.executable, guard, gate_name]
    if repo:
        cmd += ["--repo", os.path.abspath(repo)]
    r = subprocess.run(cmd, stdin=subprocess.DEVNULL)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
