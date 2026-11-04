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


if __name__ == "__main__":
    unittest.main(verbosity=2)
