# -*- coding: utf-8 -*-
r"""外部评审 2026-11-04 落地回归锁 —— P1-3：受保护面清单收敛到唯一事实源。

评审发现：受保护面有两份手写清单必然漂移——
  · `checks/harness_guard._in_protected()`（Python）保护了 config/harness.toml，但不保护 .yml/.sh/VERSION/pyproject；
  · `harness/hooks-dir/commit-msg` 的 `grep -E '\.(py|yaml)$|...'`（shell）只认旧扩展名。
后果：一个 agent 能在不带 [harness] 的提交里改策略文件或 CI，而 CI 的 Engine-Sha 校验只在
消息含 [harness] 时触发（ci.yml），于是这次修改连内容校验都跳过了。

修法：清单落到纯文本 `harness/protected-patterns.txt`（每行一条 POSIX ERE），Python 侧
`harness/_protected.py` 用 re 编译、shell 侧 commit-msg 用 `grep -Ef` 消费——同一份文件。

本模块的锁：
  S1  清单文件卫生：无空行（grep -f 空行=匹配一切）、每行合法 ERE（否则 grep -f 报错 → fail-open）。
  S2  commit-msg 真的读这份清单（grep -Ef），且不再残留旧的 `\.(py|yaml)$` 硬编码。
  S3  guard._in_protected 与 _protected.is_protected 判定一致（同一事实源的两个消费方）。
  S4  引擎内容面 ⊆ 受保护面：_engine_files() 里每个文件都必须 _in_protected（防危险方向漂移）。
  B1  行为锁（真 git）：暂存 config/harness.toml / .github/workflows/ci.yml / sandbox/install_hooks.sh /
      VERSION / pyproject.toml 且消息无 [harness] → commit-msg rc=1；带 [harness] → rc=0。

P1-1：引擎仓的 git worktree 提交绕过 [harness] 把关（复现 B：主检出 rc=1 而 worktree rc=0）。
根因是 commit-msg 与 guard 都用字面工作树路径判"是不是引擎仓"，worktree 根 ≠ 主检出根 → 漏判。
修法：按 git common-dir 判"同一仓库族"（worktree 与主检出共享 common-dir）；commit-msg 在 cd 前
把相对的 `$1` 按 TOP 绝对化（复现 F 的潜伏问题），暂存区查询改用 `git -C "$TOP"`。本模块的锁：
  W1  行为锁（真 git worktree）：worktree 改受保护文件无 [harness] → commit-msg rc=1；带 [harness] → rc=0；
      相对 `$1` 解析到 worktree 自己的消息（不读主检出旧消息）；主检出行为不回归。
  W2  guard `_is_engine_family` 认出 worktree（同族）、主检出（同族）、无关仓（非同族）；
      行为锁：guard `--repo=<worktree>` 出声"本仓提交"按 self 模式把关。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _tmpclean import rmtree_force as _rmtree_force      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HARNESS = os.path.join(ROOT, "harness")
PATTERNS = os.path.join(HARNESS, "protected-patterns.txt")
COMMIT_MSG = os.path.join(HARNESS, "hooks-dir", "commit-msg")
PY = sys.executable

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _import_engine_mods():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    if HARNESS not in sys.path:
        sys.path.insert(0, HARNESS)
    from checks.harness_guard import _in_protected
    import _protected
    import _engine_meta
    return _in_protected, _protected, _engine_meta


def _resolve_sh():
    if os.name != "nt":
        return shutil.which("sh") or shutil.which("bash")
    for cand in (r"C:/Program Files/Git/bin/bash.exe", r"C:/Program Files/Git/usr/bin/bash.exe",
                 shutil.which("sh"), shutil.which("bash")):
        if cand and os.path.isfile(cand):
            return cand
    return None


_SH = _resolve_sh()
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


class TestProtectedManifestHygiene(unittest.TestCase):
    """S1：清单文件本身的卫生 —— 两个 footgun 会让 fail-closed 变 fail-open，必须有锁。"""

    def setUp(self):
        self.assertTrue(os.path.isfile(PATTERNS), "受保护清单 harness/protected-patterns.txt 应入库")
        with open(PATTERNS, encoding="utf-8") as f:
            self.raw = f.read()

    def test_no_blank_lines(self):
        """grep -f 把空行当成「匹配一切」→ 所有暂存文件都会被误判受保护。清单不得有空行。"""
        # 末尾换行之后的空元素不算；中间任何空白行都算违规。
        lines = self.raw.split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]
        blanks = [i + 1 for i, ln in enumerate(lines) if ln.strip() == ""]
        self.assertEqual(blanks, [], "清单第 %s 行是空行（grep -f 空行=匹配一切，会误拦全部提交）" % blanks)

    def test_every_line_is_valid_ere(self):
        """每行（含 # 注释，grep 也会编译它）必须是合法正则，否则 grep -Ef 报错 → commit-msg 匹配失败。"""
        bad = []
        for i, ln in enumerate(self.raw.split("\n")):
            if ln.strip() == "":
                continue
            try:
                re.compile(ln.strip())
            except re.error as e:
                bad.append("第 %d 行 %r: %s" % (i + 1, ln, e))
        self.assertEqual(bad, [], "清单含非法正则（会让 grep -Ef 整体失败）：\n%s" % "\n".join(bad))

    def test_has_real_patterns(self):
        pats = [ln.strip() for ln in self.raw.split("\n")
                if ln.strip() and not ln.strip().startswith("#")]
        self.assertGreater(len(pats), 10, "清单里的真实模式太少，可能被误删")


class TestSingleSourceWiring(unittest.TestCase):
    """S2/S3/S4：三处消费方都读同一份清单，且不残留旧的手写清单。"""

    def test_commit_msg_reads_manifest(self):
        with open(COMMIT_MSG, encoding="utf-8", errors="replace") as f:
            src = f.read()
        self.assertIn("protected-patterns.txt", src,
                      "commit-msg 必须读唯一清单 harness/protected-patterns.txt（P1-3）")
        self.assertIn("grep -Ef", src, "commit-msg 必须用 grep -Ef 消费清单")
        # 只在**非注释行**里查旧硬编码——注释里引用它做说明是合法的（本文件上方就有）。
        code = "\n".join(ln for ln in src.split("\n") if not ln.lstrip().startswith("#"))
        self.assertNotIn(r"\.(py|yaml)$", code,
                         "commit-msg 不应再残留旧的手写扩展名 grep（会与 guard 漂移）")

    def test_guard_delegates_to_protected(self):
        _in_protected, _protected, _ = _import_engine_mods()
        # 同一事实源的两个消费方必须逐一一致
        samples = [
            "checks/example_code_check.py", "config/harness.toml", ".github/workflows/ci.yml",
            "sandbox/install_hooks.sh", "VERSION", "pyproject.toml", "setup.py", "MANIFEST.in",
            "harness/protected-patterns.txt", "harness/run_gate.py", "guardrail_engine/cli.py",
            "docs/x.md", "README.md", "tests/test_x.py", "config/harness.local.toml",
        ]
        for rel in samples:
            self.assertEqual(_in_protected(rel), _protected.is_protected(rel),
                             "guard._in_protected 与 _protected.is_protected 对 %r 判定不一致" % rel)

    def test_engine_files_subset_of_protected(self):
        """S4：引擎内容面（engine_sha）里的每个文件都必须受保护——否则「改了判定代码却不算碰门禁」。"""
        _in_protected, _, _engine_meta = _import_engine_mods()
        offenders = [rel for rel in _engine_meta._engine_files() if not _in_protected(rel)]
        self.assertEqual(offenders, [],
                         "以下引擎内容文件不在受保护面内（危险方向漂移）：%s" % offenders)


@_slow("真 git 提交链路")
@unittest.skipUnless(_SH, "无 sh/bash")
class TestCommitMsgRedTeam(unittest.TestCase):
    """B1：暂存策略/CI/安装器/打包元数据且消息无 [harness] → 必须被拦（这正是评审复现 D 的形状）。"""

    # (暂存路径, 内容)：全部是 P1-3 新增受保护、旧 grep 会漏掉的类别
    TARGETS = [
        ("config/harness.toml", 'version = 2\n[gates.example]\nenabled = false\n'),
        (".github/workflows/ci.yml", "name: ci\non: [push]\n"),
        ("sandbox/install_hooks.sh", "#!/bin/sh\necho pwn\n"),
        ("VERSION", "9.9.9\n"),
        ("pyproject.toml", "[project]\nname='x'\n"),
    ]

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="p13_")
        self.eng = os.path.join(self.dir, "eng")
        os.makedirs(os.path.join(self.eng, "harness", "hooks-dir"), exist_ok=True)
        shutil.copyfile(COMMIT_MSG, os.path.join(self.eng, "harness", "hooks-dir", "commit-msg"))
        shutil.copyfile(PATTERNS, os.path.join(self.eng, "harness", "protected-patterns.txt"))
        self.hook = os.path.join(self.eng, "harness", "hooks-dir", "commit-msg")
        self._git("init", "-q")
        self._git("config", "user.email", "t@t")
        self._git("config", "user.name", "t")
        self.msg_plain = self._write("msg_plain.txt", "普通提交消息\n")
        self.msg_harness = self._write("msg_harness.txt", "[harness] 维护提交\n")

    def tearDown(self):
        _rmtree_force(self.dir)

    def _git(self, *a):
        return subprocess.run(["git", "-C", self.eng] + list(a), capture_output=True,
                              text=True, encoding="utf-8", errors="replace")

    def _write(self, name, text):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return p

    def _run_hook(self, msg_file):
        e = dict(os.environ)
        e["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run([_SH, self.hook, msg_file], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=self.eng, env=e,
                              stdin=subprocess.DEVNULL, timeout=60)

    def test_targets_blocked_without_marker(self):
        for rel, content in self.TARGETS:
            with self.subTest(target=rel):
                # 每个 target 独立复位暂存区，避免相互污染（写过的文件留在盘上是未跟踪态，
                # 不进 --cached，不影响本次判定）
                self._git("reset", "-q")
                full = os.path.join(self.eng, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8", newline="") as f:
                    f.write(content)
                self._git("add", rel)
                r = self._run_hook(self.msg_plain)
                self.assertEqual(r.returncode, 1,
                                 "暂存 %s 且无 [harness] 必须被拦（P1-3）\n%s%s"
                                 % (rel, r.stdout, r.stderr))

    def test_targets_allowed_with_marker(self):
        for rel, content in self.TARGETS:
            with self.subTest(target=rel):
                self._git("reset", "-q")
                full = os.path.join(self.eng, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8", newline="") as f:
                    f.write(content)
                self._git("add", rel)
                r = self._run_hook(self.msg_harness)
                self.assertEqual(r.returncode, 0,
                                 "带 [harness] 应放行 %s\n%s%s" % (rel, r.stdout, r.stderr))

    def test_unprotected_file_passes_without_marker(self):
        """反向锁：非受保护文件（docs/x.md）无 [harness] 也应放行——清单没有过度扩张。"""
        self._git("reset", "-q")
        os.makedirs(os.path.join(self.eng, "docs"), exist_ok=True)
        with open(os.path.join(self.eng, "docs", "x.md"), "w", encoding="utf-8", newline="") as f:
            f.write("# doc\n")
        self._git("add", "docs/x.md")
        r = self._run_hook(self.msg_plain)
        self.assertEqual(r.returncode, 0, "非受保护文件不该要求 [harness]\n%s%s" % (r.stdout, r.stderr))

    def test_missing_manifest_fails_closed(self):
        """清单缺失 → fail-closed（报平安比报错危险）。"""
        os.remove(os.path.join(self.eng, "harness", "protected-patterns.txt"))
        os.makedirs(os.path.join(self.eng, "checks"), exist_ok=True)
        with open(os.path.join(self.eng, "checks", "c.py"), "w", encoding="utf-8", newline="") as f:
            f.write("x=1\n")
        self._git("add", "checks/c.py")
        r = self._run_hook(self.msg_plain)
        self.assertNotEqual(r.returncode, 0, "清单缺失必须阻断，不能静默放行")
        self.assertIn("清单缺失", r.stdout + r.stderr)


# ============================ P1-2：删除/改名对哨兵可见 ============================

class TestChangedPathsIncludesDeletions(unittest.TestCase):
    """P1-2 单元锁：`changed_paths_ex` 必须看见删除/改名——`changed_files_ex` 按设计看不见。

    评审复现 C：删掉 checks/agents_md_check.py 后 `changed_files_ex` 返回 ([], '')，
    因为 `--diff-filter=d` 排除了删除；guard 用它问「什么变了」→ 删除被吃掉 → 全程无声。
    """

    def setUp(self):
        if HARNESS not in sys.path:
            sys.path.insert(0, HARNESS)
        import _config
        self.C = _config
        self.dir = tempfile.mkdtemp(prefix="p12_")
        self._git("init", "-q")
        self._git("config", "user.email", "t@t")
        self._git("config", "user.name", "t")
        self._write("checks/x.py", "x = 1\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "baseline")

    def tearDown(self):
        _rmtree_force(self.dir)

    def _git(self, *a):
        return subprocess.run(["git", "-C", self.dir] + list(a), capture_output=True,
                              text=True, encoding="utf-8", errors="replace")

    def _write(self, rel, text):
        p = os.path.join(self.dir, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def test_unstaged_deletion_visible(self):
        os.remove(os.path.join(self.dir, "checks", "x.py"))
        paths, err = self.C.changed_paths_ex(self.dir)
        self.assertEqual(err, "")
        self.assertIn("checks/x.py", paths, "未暂存删除必须被看见（P1-2）")
        # 对照：旧口径 changed_files_ex 用 --diff-filter=d 排除删除 → 看不见（那是收集待检查文件的口径）
        files, _ = self.C.changed_files_ex(self.dir)
        self.assertFalse(any("x.py" in f for f in files),
                         "changed_files_ex 按设计排除删除，不该看见 x.py")

    def test_staged_deletion_visible(self):
        os.remove(os.path.join(self.dir, "checks", "x.py"))
        self._git("add", "-A")
        paths, err = self.C.changed_paths_ex(self.dir)
        self.assertEqual(err, "")
        self.assertIn("checks/x.py", paths, "已暂存删除必须被看见（P1-2）")

    def test_rename_both_ends_visible(self):
        """改名成 .bak：旧路径（受保护）与新路径都要出现，否则「改名绕过」无声。"""
        self._git("mv", "checks/x.py", "checks/x.bak")
        paths, err = self.C.changed_paths_ex(self.dir)
        self.assertEqual(err, "")
        self.assertIn("checks/x.py", paths, "rename 旧路径必须被看见")
        self.assertIn("checks/x.bak", paths, "rename 新路径必须被看见")

    def test_untracked_new_file_visible(self):
        self._write("checks/new.py", "y = 2\n")
        paths, err = self.C.changed_paths_ex(self.dir)
        self.assertEqual(err, "")
        self.assertIn("checks/new.py", paths, "未跟踪新文件必须被看见")


class TestGuardWiringP12(unittest.TestCase):
    """S：guard 必须走含删除的口径；self-check 必须把「缺失即静默降级」文件列为必需。"""

    def test_guard_uses_changed_paths_ex(self):
        with open(os.path.join(ROOT, "checks", "harness_guard.py"), encoding="utf-8", errors="replace") as f:
            src = f.read()
        # 只查 import 语句本身（裸词会命中注释里的历史说明）：guard 必须导入含删除/改名的口径
        self.assertIn("from _config import changed_paths_ex", src,
                      "guard 必须用含删除/改名的 changed_paths_ex（P1-2）")
        self.assertNotIn("from _config import changed_files_ex", src,
                         "guard 不该再导入 --diff-filter=d 的 changed_files_ex（会漏删除）")

    def test_self_check_requires_silent_degrade_files(self):
        with open(os.path.join(ROOT, "checks", "harness_self_check.py"), encoding="utf-8", errors="replace") as f:
            src = f.read()
        for rel in ("checks/agents_md_check.py", "checks/file_types.py"):
            self.assertIn(rel, src, "self-check 必需清单应含 %s（缺失即静默降级，P1-2）" % rel)


@_slow("真起子进程跑副本里的 guard / self-check")
class TestGuardSeesDeletionBehavior(unittest.TestCase):
    """P1-2 行为锁：在干净工程副本里删掉一个受保护文件 → guard 必须 rc=1（旧口径下会静默 rc=0）。"""

    def _run(self, script, *args):
        e = dict(os.environ)
        e["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run([PY, script] + list(args), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=os.path.dirname(script),
                              env=e, stdin=subprocess.DEVNULL, timeout=180)

    def test_guard_blocks_deletion_of_protected_file(self):
        from _engine_copy import EngineCopy
        copy = EngineCopy()
        dummy = tempfile.mkdtemp(prefix="p12biz_")
        try:
            # 干净基线 → 放行（--repo 指向 != 副本的路径，确保非 self 模式，走到删除检查）
            r0 = self._run(copy.guard, "example", "--repo", dummy)
            self.assertEqual(r0.returncode, 0, "干净副本应放行\n%s%s" % (r0.stdout, r0.stderr))
            # 删掉受保护文件（未暂存删除）→ 必须拦
            os.remove(copy.p("checks/agents_md_check.py"))
            r1 = self._run(copy.guard, "example", "--repo", dummy)
            self.assertEqual(r1.returncode, 1,
                             "删除受保护文件必须被 guard 拦（P1-2）\n%s%s" % (r1.stdout, r1.stderr))
            self.assertIn("agents_md_check.py", r1.stdout + r1.stderr)
        finally:
            _rmtree_force(dummy)
            copy.cleanup()

    def test_self_check_flags_missing_silent_degrade_file(self):
        from _engine_copy import EngineCopy
        copy = EngineCopy()
        try:
            os.remove(copy.p("checks/agents_md_check.py"))
            r = self._run(copy.p("checks/harness_self_check.py"))
            self.assertEqual(r.returncode, 1,
                             "self-check 必须因缺 agents_md_check.py 而 ❌\n%s%s" % (r.stdout, r.stderr))
            self.assertIn("agents_md_check.py", r.stdout + r.stderr)
        finally:
            copy.cleanup()


# ============================ P1-1：worktree 提交不再绕过把关 ============================

@_slow("真起 git 仓 + worktree + 真跑 commit-msg")
class TestCommitMsgWorktree(unittest.TestCase):
    """P1-1 行为锁（复现 B / F）：引擎仓的 **worktree** 提交不得绕过 [harness] 把关。

    旧判据 `[ "$TOP" = "$ENGROOT" ]` 比字面工作树路径——worktree 根 ≠ 主检出根 → `exit 0` 放行，
    [harness] 把关整体失效（复现 B：MAIN_RC=1 而 WORKTREE_RC=0）。改为按 git common-dir 判
    "同一仓库族"，worktree 与主检出一视同仁。

    另锁被第一个 bug 掩盖的潜伏问题（复现 F）：git 传给 commit-msg 的 `$1` 是**相对** cwd 的路径
    （实测 `.git/COMMIT_EDITMSG`），旧脚本先 `cd "$ENGROOT"` 再读 `$1`；一旦修好 worktree 判定，
    worktree 提交就会去读**主检出**的旧消息。修复要求在 cd 之前按 TOP 绝对化 `$1`。
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="p11_")
        self.main = os.path.join(self.dir, "main")
        os.makedirs(os.path.join(self.main, "harness", "hooks-dir"), exist_ok=True)
        os.makedirs(os.path.join(self.main, "checks"), exist_ok=True)
        shutil.copyfile(COMMIT_MSG, os.path.join(self.main, "harness", "hooks-dir", "commit-msg"))
        shutil.copyfile(PATTERNS, os.path.join(self.main, "harness", "protected-patterns.txt"))
        self.hook = os.path.join(self.main, "harness", "hooks-dir", "commit-msg")
        self._git(self.main, "init", "-q")
        self._git(self.main, "config", "user.email", "t@t")
        self._git(self.main, "config", "user.name", "t")
        # worktree add 需要至少一个 commit → 先建基线
        self._write(self.main, "README.md", "# base\n")
        self._git(self.main, "add", "-A")
        self._git(self.main, "commit", "-qm", "baseline")

    def tearDown(self):
        _rmtree_force(self.dir)

    def _git(self, repo, *a):
        return subprocess.run(["git", "-C", repo] + list(a), capture_output=True,
                              text=True, encoding="utf-8", errors="replace")

    def _write(self, base, rel, text):
        p = os.path.join(base, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return p

    def _worktree(self, name="wt"):
        wt = os.path.join(self.dir, name)
        r = self._git(self.main, "worktree", "add", "-q", wt, "-b", name)
        self.assertEqual(r.returncode, 0, "worktree 夹具创建失败：%s%s" % (r.stderr, r.stdout))
        return wt

    def _run_hook(self, cwd, msg_file):
        e = dict(os.environ)
        e["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run([_SH, self.hook, msg_file], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=cwd, env=e,
                              stdin=subprocess.DEVNULL, timeout=60)

    def test_main_protected_change_blocked(self):
        """回归：主检出改受保护文件无 [harness] → rc=1（本来就对，别被 worktree 修复带坏）。"""
        self._write(self.main, "checks/pwn.py", "x=1\n")
        self._git(self.main, "add", "checks/pwn.py")
        msg = self._write(self.main, "msg_plain.txt", "普通提交\n")
        r = self._run_hook(self.main, msg)
        self.assertEqual(r.returncode, 1,
                         "主检出受保护改动无 [harness] 应被拦\n%s%s" % (r.stdout, r.stderr))

    def test_worktree_protected_change_blocked(self):
        """复现 B：worktree 改受保护文件无 [harness] → rc=1（旧版 rc=0 绕过）。"""
        wt = self._worktree()
        self._write(wt, "checks/pwn.py", "x=1\n")
        self._git(wt, "add", "checks/pwn.py")
        msg = self._write(wt, "msg_plain.txt", "普通提交\n")
        r = self._run_hook(wt, msg)
        self.assertEqual(r.returncode, 1,
                         "worktree 受保护改动无 [harness] 必须被拦（P1-1 复现 B）\n%s%s" % (r.stdout, r.stderr))

    def test_worktree_with_marker_allowed(self):
        """反向：worktree 带 [harness] → rc=0（别把合法维护出口一起堵了）。"""
        wt = self._worktree()
        self._write(wt, "checks/pwn.py", "x=1\n")
        self._git(wt, "add", "checks/pwn.py")
        msg = self._write(wt, "msg_h.txt", "[harness] 维护\n")
        r = self._run_hook(wt, msg)
        self.assertEqual(r.returncode, 0,
                         "worktree 带 [harness] 应放行\n%s%s" % (r.stdout, r.stderr))

    def test_worktree_relative_msg_not_stale_main(self):
        """复现 F：相对 `$1` 必须按 worktree(TOP) 解析，不能读到主检出的旧消息。

        主检出放一条**带 [harness] 的旧 msg.txt**；若脚本仍 `cd "$ENGROOT"` 再读相对 `$1`，
        会读到它 → 误放行（rc=0）。修复后读 worktree 自己的 msg.txt（无标记）→ rc=1。
        """
        wt = self._worktree()
        self._write(self.main, "msg.txt", "STALE MAIN [harness]\n")   # 主检出的旧消息（陷阱）
        self._write(wt, "checks/pwn.py", "x=1\n")
        self._git(wt, "add", "checks/pwn.py")
        self._write(wt, "msg.txt", "普通提交\n")                       # worktree 自己的相对 msg.txt
        r = self._run_hook(wt, "msg.txt")                            # 传**相对**路径，cwd=worktree
        self.assertEqual(r.returncode, 1,
                         "相对 $1 必须解析到 worktree 的 msg.txt（无标记→拦），不能读主检出旧 [harness] 消息\n%s%s"
                         % (r.stdout, r.stderr))


@_slow("真起 git 仓 + worktree + 加载副本 guard")
class TestGuardWorktreeSelfMode(unittest.TestCase):
    """P1-1：guard 的 self 模式判据必须认出"引擎仓的 worktree"也是本仓提交。

    旧判据 `normcase(abspath(commit_repo)) == normcase(abspath(ROOT))` 比字面路径——worktree 根
    ≠ 主检出根 → 被判成"端仓提交" → 只检查主仓工作区（干净）→ 放行。改为补一条 git common-dir
    判据（`_is_engine_family`）：同仓库族即本仓。
    """

    def _load_guard(self, guard_path):
        """按文件路径加载副本里的 guard（其 ROOT 由 __file__ 推导 = 副本根），不污染 sys.modules。"""
        import importlib.util
        spec = importlib.util.spec_from_file_location("guard_wt_probe", guard_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _worktree_of(self, copy, wt):
        r = subprocess.run(["git", "-C", copy.path, "worktree", "add", "-q", wt, "-b", "wtbranch"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r

    def test_is_engine_family_recognizes_worktree(self):
        from _engine_copy import EngineCopy
        copy = EngineCopy()
        wt_dir = tempfile.mkdtemp(prefix="p11wt_")
        unrel_dir = tempfile.mkdtemp(prefix="p11unrel_")
        wt = os.path.join(wt_dir, "wt")
        try:
            r = self._worktree_of(copy, wt)
            self.assertEqual(r.returncode, 0, "worktree 创建失败：%s%s" % (r.stderr, r.stdout))
            # 无关仓：另起一个独立 git 仓（不同 common-dir），比"非 git 目录"更稳（不受父目录偶然是仓影响）
            subprocess.run(["git", "-C", unrel_dir, "init", "-q"], capture_output=True, text=True)
            mod = self._load_guard(copy.guard)
            self.assertEqual(os.path.normcase(mod.ROOT), os.path.normcase(copy.path),
                             "副本 guard 的 ROOT 应是副本根（否则这条锁没测到东西）")
            self.assertTrue(mod._is_engine_family(copy.path), "主检出本身当然属于引擎仓族")
            self.assertTrue(mod._is_engine_family(wt),
                            "引擎仓的 worktree 必须被判为同一仓库族（P1-1）——旧版按字面路径漏判")
            self.assertFalse(mod._is_engine_family(unrel_dir),
                             "无关仓库不该被判为引擎仓族（否则 self 模式会误放行端仓提交）")
        finally:
            subprocess.run(["git", "-C", copy.path, "worktree", "remove", "--force", wt],
                           capture_output=True, text=True)
            _rmtree_force(wt_dir)
            _rmtree_force(unrel_dir)
            copy.cleanup()

    def test_guard_self_mode_message_for_worktree_repo(self):
        """行为锁：guard `--repo=<worktree>`（pre-commit 阶段无消息）→ 出声"本仓提交"并按 self 放行。

        判别点在**输出**：旧版把 worktree 判成端仓时不会打印"本仓提交"，而是直接去查主仓工作区。
        """
        from _engine_copy import EngineCopy
        copy = EngineCopy()
        wt_dir = tempfile.mkdtemp(prefix="p11wt2_")
        wt = os.path.join(wt_dir, "wt")
        try:
            r = self._worktree_of(copy, wt)
            self.assertEqual(r.returncode, 0, "worktree 创建失败：%s%s" % (r.stderr, r.stdout))
            e = dict(os.environ)
            e["PYTHONIOENCODING"] = "utf-8"
            run = subprocess.run([PY, copy.guard, "example", "--repo", wt], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace",
                                 cwd=os.path.dirname(copy.guard), env=e,
                                 stdin=subprocess.DEVNULL, timeout=180)
            self.assertEqual(run.returncode, 0,
                             "干净副本 + worktree --repo 应放行\n%s%s" % (run.stdout, run.stderr))
            self.assertIn("本仓提交", run.stdout,
                          "worktree --repo 必须被 guard 认成本仓提交（self 模式），而非端仓（P1-1）")
        finally:
            subprocess.run(["git", "-C", copy.path, "worktree", "remove", "--force", wt],
                           capture_output=True, text=True)
            _rmtree_force(wt_dir)
            copy.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
