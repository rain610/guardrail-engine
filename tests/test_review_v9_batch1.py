# -*- coding: utf-8 -*-
r"""外部评审 v9（2026-09-15）落地回归锁 —— 闭合 Codex v4 与 AutoClaw v9 的缺口。

本模块的每一条都对应"外部评审发现的问题"，且都写成**行为锁**（不靠源码文本匹配），
判据是退出码 / 输出关键字 / 真实的 rel+changed_lines 计算结果：

L1  Codex v4 P1-1：worktree 提交时检查基准必须是**实际提交仓**，不能是 config 主仓。
    机制证据（实测）：基准=主仓 → rel_path='..\wt\src\a.vue'（仓外）、changed_lines=[]（全判存量）；
    基准=worktree → rel='src\a.vue'、changed_lines=[2]（新增行可见）。
    接线证据：run_gate 必须消费 `--repo`（旧版把它当"未知参数 + 文件"，把目录塞给检查器）。
L2  Codex v4 P1-3：后端检查器读取失败必须是**阻断级 error**，不能用空 violations 表示通过。
L3  Codex v4 P2-1：分发器必须**透传退出码**（2=配置错 / 1=业务拦截），不得压平成 1。
L4  Codex v4 P1-2：commit-msg 的链式查找必须与 pre-commit 一致（common-dir → absolute-git-dir）。
L5  AutoClaw v9 V9-1：提交期 repo-wide 分支（file_types.matches 短路）必须有锁 —— 变异后原为全绿。
L6  AutoClaw v9 V9-2：分发器**链首自检**（工程仓体检失败 → rc=2）必须有锁。
L7  AutoClaw v9 V9-3：acceptance ⑤ syntax 探针在**缺 node** 时必须为 False（防"恒真盲区"）。
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _tmpclean import rmtree_force as _rmtree_force      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKS = os.path.join(ROOT, "checks")
PY = sys.executable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _engine_copy import shared as _engine     # noqa: E402

# UTF-8 兜底（本仓硬规则：输出型入口必须自保，否则 Windows GBK 控制台会整体崩）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _sh(cmd, cwd=None, env=None, timeout=180):
    e = dict(os.environ)
    e.pop("PYTHONUTF8", None)
    e["PYTHONIOENCODING"] = "utf-8"
    if env:
        e.update(env)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=cwd, env=e, stdin=subprocess.DEVNULL, timeout=timeout)


def _git(repo, *a):
    return _sh(["git", "-C", repo] + list(a))


def _resolve_bash():
    """解析可用于跑 shell 行为锁的 bash。

    评审 v10 §2-B1：原先的探测命令是 `test -d /c/Users && echo ok` —— **Linux 上永假** →
    整个 TestL3Dispatchers（rc 透传 / commit-msg 链式 / 链首自检，CI 上的唯一看守）**永久 skip**。
    现在按平台分支：非 Windows 直接用 POSIX bash；Windows 必须是 Git for Windows 的 bash
    （System32 的 WSL bash 看不见 /c/... 路径 → rc=127）。
    """
    import shutil as _shutil
    if os.name != "nt":
        return _shutil.which("bash")            # Linux/macOS：POSIX bash 直接可用（CI 上真跑）
    for cand in (r"C:/Program Files/Git/bin/bash.exe", r"C:/Program Files/Git/usr/bin/bash.exe",
                 _shutil.which("bash")):
        if not cand or not os.path.isfile(cand):
            continue
        try:
            r = subprocess.run([cand, "-c", "test -d /c/Users && echo ok"],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=30)
        except Exception:
            continue
        if "ok" in (r.stdout or ""):
            return cand
    return None

_BASH = _resolve_bash()

# 快跑（HARNESS_FAST=1，2026-09-17 接线）：本文件的慢在**真起 git 仓/worktree + 真起子进程
# 跑 run_gate / pre-commit.py / bash 分发器**（L1 每例 1~3s、L3 每例 2.6~6.1s、L5 6.4s、L7 3.6s）。
# **跳过的代价要说清**：L1（worktree 基准）、L3（分发器 rc 透传 / commit-msg 链式 / 链首自检）、
# L5（仓库级 gate 不被扩展名过滤）、L7（acceptance 语法探针不恒真）此后只在全量档守着 ——
# 而这四条正是变异冒烟 M1 / M3 / M5 / M7 的判据所在（见 sandbox/mutation_smoke.py）。
# **不清空**：TestL2BackendReadFailure（0.05s，纯内存 + 临时文件）照跑。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


def _bsh(path):
    """交给 bash 的路径必须是 **MSYS 形式**：反斜杠会被 bash 当转义符（rc=127），
    而 `C:/...` 在本机实测同样解析不到 → 统一转成 `/c/...`。"""
    p = os.path.abspath(path).replace("\\", "/")
    if len(p) > 1 and p[1] == ":":
        return "/" + p[0].lower() + p[2:]
    return p


def _write(path, text, newline=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)


def _is_node_family(name):
    """`node` / `node.exe` / `npm.cmd` … 都算 node 一族（去扩展名后比名字，不按子串）。"""
    base = name.lower()
    for ext in (".exe", ".cmd", ".bat", ".ps1"):
        if base.endswith(ext):
            base = base[: -len(ext)]
            break
    return base in ("node", "nodejs", "npm", "npx", "corepack", "yarn", "pnpm")


def _node_free_path(tmp):
    """造一条**确定没有 node** 的 PATH（L7 的夹具前提）。

    旧写法是把 `shutil.which("git"/"bash"/…)` 的**所在目录整条**塞进 PATH。它在本机碰巧成立
    （git-bash 的 node 与 git 不同目录），在 Linux/WSL 上必假 —— 那台机 git / node / python3
    全在 `/usr/bin`，「保留 git 所在目录」就等于把 node 一起保留了，于是夹具自检
    `assertIsNone(which("node"))` 直接红（2026-09-19 WSL 全量唯一红；带 @_slow，
    所以此前只走快跑档时从没执行过）。

    现改为按**文件**建镜像：需要的目录逐个镜像进临时目录，只把 node 一族挡在外面。
    前提从「这台机器的 node 恰好不在这些目录里」变成「node 这个名字不进我的 PATH」——
    与机器布局无关，这才是真自足。

    退路（不静默）：`os.symlink` 建不了时退回按目录（Windows 无开发者模式会抛 WinError 1314），
    此时由调用方的 `assertIsNone(which("node"))` 出声，而不是悄悄放过。
    """
    srcs = []
    for exe in ("git", "bash", "sh", "sed", "grep"):
        wx = shutil.which(exe)
        if wx:
            srcs.append(os.path.dirname(wx))
    srcs.append(os.path.dirname(sys.executable))
    srcs = list(dict.fromkeys(srcs))                    # 去重且保序
    try:
        mirror = os.path.join(tmp, "nodefree-bin")
        os.makedirs(mirror)
        for d in srcs:
            for name in os.listdir(d):
                if _is_node_family(name):
                    continue
                dst = os.path.join(mirror, name)
                if os.path.exists(dst) or os.path.islink(dst):
                    continue
                os.symlink(os.path.join(d, name), dst)
        return mirror
    except OSError:
        return os.pathsep.join(srcs)


class _Fixture(unittest.TestCase):
    """通用夹具：临时 main 仓 + 它的 worktree + 指向 main 的 gate 配置。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v9fix_")
        self.addCleanup(_rmtree_force, self.tmp)      # 显式清理：GC 期隐式清理会触发严格句柄门槛
        self.main = os.path.join(self.tmp, "main")
        os.makedirs(self.main)
        _git(self.main, "init", "-q")
        _git(self.main, "config", "user.email", "v9@t")
        _git(self.main, "config", "user.name", "v9")

    def cfg(self, gate, script, repo=None, extra=""):
        d = os.path.join(self.tmp, "cfg")
        cfg = os.path.join(d, "c.yaml")
        _write(cfg, "repos:\n  chain: %s\ngates:\n  %s:\n    enabled: true\n    repo: chain\n"
                    "    script: %s\n%s" % ((repo or self.main).replace("\\", "/"), gate, script, extra))
        return {"HARNESS_CONFIG": cfg, "HARNESS_ALLOW_POLICY_OVERRIDE": "1",
                "HARNESS_LOG_FILE": os.path.join(d, "gate_log.jsonl")}

    def worktree(self, name="wt"):
        wt = os.path.join(self.tmp, name)
        r = _git(self.main, "worktree", "add", "-q", wt)
        self.assertEqual(r.returncode, 0, "worktree 夹具创建失败：%s" % (r.stderr or ""))
        return wt


@_slow("真起 git 仓 + worktree + 真跑 run_gate / pre-commit.py（每例 1~3s）")
class TestL1WorktreeBasis(_Fixture):
    """Codex v4 P1-1：检查基准必须是实际提交仓。"""

    def test_rel_and_changed_lines_use_worktree_basis(self):
        _write(os.path.join(self.main, "src/a.vue"), "x\r\n")
        _git(self.main, "add", "-A"); _git(self.main, "commit", "-qm", "init")
        wt = self.worktree()
        f = os.path.join(wt, "src", "a.vue")
        _write(f, "x\r\ny\r\n")
        _git(wt, "add", "src/a.vue")

        sys.path.insert(0, CHECKS)
        import rule_base as rb
        # 跨平台：rel_path 返回本机分隔符（Windows 反斜杠 / Linux 正斜杠）→ 归一后再比。
        # 这条曾在 CI（Linux）上因写死反斜杠而红 —— 教训：断言里不写死平台分隔符。
        self.assertEqual(rb.rel_path(f, wt).replace("\\", "/"), "src/a.vue",
                         "以 worktree 为基准时 rel 必须正确")
        self.assertEqual(sorted(rb.compute_changed_lines(wt, "src/a.vue")), [2],
                         "以 worktree 为基准时必须看到新增行")
        # 反证：以 config 主仓为基准时（旧行为）新增行完全不可见 —— 这就是 P1-1 的机制
        self.assertTrue(rb.rel_path(f, self.main).startswith(".."),
                        "以主仓为基准时文件被视为仓外（旧行为），本断言防止夹具失真")
        # 中 C（评审 v13）后三态：以主仓为基准时 src/a.vue 压根没动 → None（整文件口径）。
        # 断言仍是同一条命题：**看不到本次新增行** → 新增违规被当成存量判（P1-1 的机制）。
        self.assertIsNone(rb.compute_changed_lines(self.main, "src/a.vue"),
                          "以主仓为基准时应拿不到本次改动信息（None）→ 新增违规会被判存量（P1-1）")

    def test_run_gate_consumes_repo_flag(self):
        """接线：run_gate 必须认 --repo（旧版 → '忽略未知参数: --repo' + 把目录当文件）。"""
        _write(os.path.join(self.main, "src/a.vue"), "x\r\n")
        _git(self.main, "add", "-A"); _git(self.main, "commit", "-qm", "init")
        wt = self.worktree()
        _write(os.path.join(wt, "src/a.vue"), "x\r\ny\r\n")
        _git(wt, "add", "src/a.vue")
        env = self.cfg("example", "checks/example_code_check.py")
        o = _sh([PY, os.path.join(ROOT, "harness", "run_gate.py"), "example",
                 "--repo", wt, os.path.join(wt, "src", "a.vue")], env=env)
        out = o.stdout + o.stderr
        self.assertNotIn("忽略未知参数: --repo", out, "run_gate 仍把 --repo 当未知参数（P1-1 未接线）")
        self.assertIn("检查基准=调用方指定仓", out, "run_gate 没有以调用方指定仓为基准")
        self.assertNotIn("Permission denied", out, "仍把 --repo 的路径当文件塞给检查器")

    def test_basis_invalid_path_is_fail_closed(self):
        env = self.cfg("example", "checks/example_code_check.py")
        o = _sh([PY, os.path.join(ROOT, "harness", "run_gate.py"), "example",
                 "--repo", os.path.join(self.tmp, "nope")], env=env)
        self.assertEqual(o.returncode, 2, "--repo 指向非法路径必须 fail-closed(2)")

@unittest.skipUnless(_BASH, "本机找不到可用的 git-bash（裸 bash 指向 WSL）→ shell 类锁跳过")
@_slow("真跑 bash 分发器 + 假工程布局（每例 2.6~6.1s）")
class TestL3Dispatchers(unittest.TestCase):
    """C4（rc 透传）+ C2（commit-msg common-dir）+ V9-2（链首自检）—— 用假布局真跑分发器。"""

    def _layout(self, hook_rc=0, selfcheck_ok=True):
        """搭一个最小工程布局：<root>/harness/hooks-dir/<分发器> + harness/hooks/pre-commit.py 假实现。"""
        d = tempfile.mkdtemp(prefix="v9disp_")
        self.addCleanup(_rmtree_force, d)
        for name in ("pre-commit", "commit-msg"):
            shutil.copy(os.path.join(ROOT, "harness", "hooks-dir", name),
                        os.path.join(d, "harness", "hooks-dir", name)) if os.path.isdir(
                os.path.join(d, "harness", "hooks-dir")) else None
        os.makedirs(os.path.join(d, "harness", "hooks-dir"), exist_ok=True)
        os.makedirs(os.path.join(d, "harness", "hooks"), exist_ok=True)
        os.makedirs(os.path.join(d, "checks"), exist_ok=True)
        for name in ("pre-commit", "commit-msg"):
            shutil.copy(os.path.join(ROOT, "harness", "hooks-dir", name),
                        os.path.join(d, "harness", "hooks-dir", name))
        # 假实现：按需要返回 hook_rc
        with io.open(os.path.join(d, "harness", "hooks", "pre-commit.py"), "w",
                     encoding="utf-8", newline="") as f:
            f.write("import sys\nprint('[fake] hook rc=%d')\nsys.exit(%d)\n" % (hook_rc, hook_rc))
        # 假自检：分发器链首调用的是 checks/harness_self_check.py（不是 run_gate.py）
        with io.open(os.path.join(d, "checks", "harness_self_check.py"), "w",
                     encoding="utf-8", newline="") as f:
            f.write("import sys\nsys.exit(0)\n" if selfcheck_ok else
                    "import sys\nsys.stderr.write('fake self-check failed: allowlist 加载失败')\nsys.exit(2)\n")
        with io.open(os.path.join(d, "harness", "run_gate.py"), "w", encoding="utf-8", newline="") as f:
            f.write("import sys\nsys.exit(0)\n")
        return d

    def _repo(self, d):
        repo = os.path.join(d, "endrepo")
        os.makedirs(repo)
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "v9@t"); _git(repo, "config", "user.name", "v9")
        _write(os.path.join(repo, "a.txt"), "hi\n")
        _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "init")
        return repo

    def _backup(self, d, repo, name, content):
        """把"端仓原有 hook"放进分发器约定的备份位置（common-dir 下）。"""
        bak = os.path.join(repo, ".git", "agentguard-original-hooks")
        os.makedirs(bak, exist_ok=True)
        p = os.path.join(bak, name)
        with io.open(p, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.chmod(p, 0o755)
        return p

    def test_chain_not_skipped_for_lookalike_backup(self):
        """评审 v11 §4-V11-4（负向行为锁，防**误跳过**）：
        备份文件只是"正文里含分发器字样"（此处放在**第 3 行**，不在头部标记行）——
        绝不能被指纹当成"本分发器自己"而跳过链式，否则端仓原有质量流程被**无声降级**。
        判据是**行为**：断言原 hook 真的被执行了（它打印的 MARKER-NEG 出现在输出里），
        而不是断言"没打印某句话"。"""
        d = self._layout()
        repo = self._repo(d)
        self._backup(d, repo, "pre-commit",
                     "#!/bin/sh\n# 普通注释：本仓沿用 harness 说明\n"
                     "# 参考 agentguard dispatcher 的写法（正文提及，不是标记行）\n"
                     "echo MARKER-NEG\n")
        # cwd 必须是**端仓**：分发器的链式块用 `git rev-parse --git-common-dir` 判当前目录的仓，
        # 在假工程根（非 git 仓）下永远找不到备份 → 链式不会触发（我第一版就是这么写的，两条锁都假红）。
        r = _sh([_BASH, _bsh(os.path.join(d, "harness", "hooks-dir", "pre-commit"))],
                cwd=repo, env={"HARNESS": _bsh(d)})
        out = (r.stdout or "") + (r.stderr or "")
        self.assertIn("MARKER-NEG", out,
                      "备份正文含分发器字样就被误判自递归 → 链式被静默跳过（原 hook 没跑）:\n%s" % out[-500:])

    def test_chain_skipped_for_real_dispatcher_backup(self):
        """评审 v11 §4-V11-4（正向行为锁，防**自递归**）：
        备份确实是本分发器自己的拷贝（标记在**第 2 行**）→ 必须跳过链式；
        判据仍是行为：给拷贝追加的 MARKER-POS **不得**出现在输出里（即它没被执行）。"""
        d = self._layout()
        repo = self._repo(d)
        with io.open(os.path.join(ROOT, "harness", "hooks-dir", "pre-commit"),
                     encoding="utf-8") as f:
            body = f.read()
        self._backup(d, repo, "pre-commit", body + "\necho MARKER-POS\n")
        r = _sh([_BASH, _bsh(os.path.join(d, "harness", "hooks-dir", "pre-commit"))],
                cwd=repo, env={"HARNESS": _bsh(d)})
        out = (r.stdout or "") + (r.stderr or "")
        self.assertIn("跳过链式", out, "备份就是本分发器，应跳过链式（否则自递归）:\n%s" % out[-400:])
        self.assertNotIn("MARKER-POS", out, "被跳过的备份仍然被执行了（自递归没防住）")

    def test_dispatcher_passes_hook_rc_through(self):
        """C4：假 hook 退出码 1/2 必须**原样透传**（旧实现一律压成 1）。"""
        for want in (1, 2):
            d = self._layout(hook_rc=want)
            repo = self._repo(d)
            script = os.path.join(d, "harness", "hooks-dir", "pre-commit")
            self.assertTrue(os.path.isfile(script), "夹具没铺好分发器：%s" % script)
            r = _sh([_BASH, "harness/hooks-dir/pre-commit"], cwd=d, env={"HARNESS": _bsh(d)})
            self.assertEqual(r.returncode, want,
                             "分发器把 rc=%d 压成了 %d（应透传）" % (want, r.returncode))

    def test_commitmsg_chain_finds_backup_in_common_dir(self):
        """C2：worktree 里备份落在 common-dir → commit-msg 必须找到并链式调用。"""
        d = self._layout()
        repo = self._repo(d)
        _write(os.path.join(repo, "b.txt"), "x\n")
        _git(repo, "add", "b.txt")
        wt = os.path.join(d, "wt")
        r = _git(repo, "worktree", "add", "-q", wt)
        self.assertEqual(r.returncode, 0, (r.stderr or "")[:120])
        common = _git(repo, "rev-parse", "--git-common-dir").stdout.strip()
        common = common if os.path.isabs(common) else os.path.join(repo, common)
        bak = os.path.join(common, "agentguard-original-hooks")
        os.makedirs(bak, exist_ok=True)
        msg = os.path.join(d, "msg.txt")
        _write(msg, "normal message\n")
        with io.open(os.path.join(bak, "commit-msg"), "w", encoding="utf-8", newline="") as f:
            f.write("#!/bin/sh\necho '[orig-commit-msg] 被链式调用了'\nexit 0\n")
        env = {"HARNESS": d, "PATH": os.environ.get("PATH", "")}
        script = os.path.join(d, "harness", "hooks-dir", "commit-msg")
        self.assertTrue(os.path.isfile(script), "夹具没铺好分发器：%s" % script)
        o = _sh([_BASH, _bsh(script), msg], cwd=wt, env={"HARNESS": _bsh(d), "PATH": os.environ.get("PATH", "")})
        out = o.stdout + o.stderr
        self.assertIn("调用了", out, "worktree 里 common-dir 的原有 commit-msg 未被链式调用（C2）")

    def test_dispatcher_head_selfcheck_blocks(self):
        """V9-2：链首工程仓自检失败必须阻断（rc=2）——这条此前无锁。"""
        d = self._layout(hook_rc=0, selfcheck_ok=False)
        repo = self._repo(d)
        script = os.path.join(d, "harness", "hooks-dir", "pre-commit")
        self.assertTrue(os.path.isfile(script), "夹具没铺好分发器：%s" % script)
        r = _sh([_BASH, "harness/hooks-dir/pre-commit"], cwd=d, env={"HARNESS": _bsh(d)})
        self.assertNotEqual(r.returncode, 0, "链首自检失败却放行了（V9-2）")


class TestL5RepoWideAtCommit(_Fixture):
    """V9-1：提交期 repo-wide 分支（.py 不被扩展名过滤）必须有锁。"""

    @_slow("真跑提交期入口（真起工程仓 + 子进程，6.4s）")
    def test_harness_self_runs_on_staged_py(self):
        repo = os.path.join(self.tmp, "engrepo")
        os.makedirs(repo)
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "v9@t"); _git(repo, "config", "user.name", "v9")
        _write(os.path.join(repo, "checks/sample.py"), "x = 1\n")
        _git(repo, "add", "-A")
        env = self.cfg("harness_self", "checks/harness_self_check.py", repo=repo)
        # 跑**工程副本**里的入口，不是真工程仓那份：哨兵修好（评审 v13 严重 4）后，
        # "工程仓有未提交的受保护文件改动"会真的拦下模拟提交 —— 那与 V9-1 被测行为无关，
        # 却会在维护者改门禁文件时把本条记成红。副本见 tests/_engine_copy.py。
        o = _sh([PY, _engine().hook, "harness_self", "--repo", repo], env=env)
        out = o.stdout + o.stderr
        self.assertIn("跑门禁 harness_self", out,
                      "提交 .py 时仓库级 gate 被扩展名过滤掉了（V9-1：file_types.matches 短路失效）")
        self.assertNotIn("都不在本 gate 的检查面内", out, "仓库级 gate 又按扩展名筛文件了")


class TestL7AcceptanceSyntaxProbe(_Fixture):
    """V9-3：acceptance ⑤ syntax 探针在缺 node 时必须为 False（防恒真盲区）。"""

    @_slow("真跑 acceptance 探针（子进程 + 全仓扫描，3.6s）")
    def test_syntax_probe_is_false_without_node(self):
        env = self.cfg("demo_pc", "checks/pc_code_check.py")
        # 造一个"没有 node"的 PATH（按文件建镜像，见 _node_free_path）
        env["PATH"] = _node_free_path(self.tmp)   # 按文件建镜像，node 一族一个不进（见该函数）
        self.assertIsNone(shutil.which("node", path=env["PATH"]),
                          "夹具没剥掉 node，锁会失真（PATH=%s）" % env["PATH"])
        o = _sh([PY, os.path.join(ROOT, "sandbox", "acceptance_check.py"), "demo_pc", "--json"], env=env)
        out = o.stdout + o.stderr
        self.assertIn('"syntax"', out, "acceptance 没有输出 syntax 项（探针没接线）")
        # 评审 v10 §7-3（V10-3）：原判据 `assertIn("false", out.lower())` 近似**恒真** ——
        # acceptance JSON 里 `"pass": false` 恒在（ledger 基本为 false），量不到 syntax 真假。
        # 现改为精确断言 `"syntax": false`（并保留字段存在性断言）。
        self.assertIn('"syntax"', out, "acceptance JSON 里应有 syntax 字段")
        self.assertIn('"syntax": false', out, "缺 node 时 syntax 必须为 false（V9-3 探针不能是恒真）")


if __name__ == "__main__":
    unittest.main()
