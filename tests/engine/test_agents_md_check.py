# -*- coding: utf-8 -*-
"""`checks/agents_md_check.py` 的失效语义锁：查不了 ≠ 通过。

为什么需要（2026-10-21 外部评审 5.18）
--------------------------------------------------------------------------------
这个脚本在每次业务仓提交时由 run_gate 追加调用，判据是「受管仓 AGENTS.md 里有没有
指向 agentguard 的绝对路径」。旧实现里配置读取失败会 `return (True, "跳过")` ——
配置坏了反而输出「全部符合」，正中项目自己最想消灭的 fail-open。本文件钉住三件事：

  ① 配置读不到 / repos 段格式坏 → rc=2，且 stderr 说清「无法执行」；
  ② 本机没配任何仓 → rc=0（新 clone 常态，不能误报）；
  ③ 读到但 AGENTS.md 不可读 → rc=2（不能因单个仓读失败就宣称全部合格）。
"""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "checks"))
sys.path.insert(0, os.path.join(ROOT, "harness"))
import agents_md_check as M  # noqa: E402
import _config as C  # noqa: E402


class TestAgentsMdCheckFailClosed(unittest.TestCase):
    def _run_main(self, fake_load, argv=None):
        old = C.load
        C.load = fake_load
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = M.main([] if argv is None else argv)
        finally:
            C.load = old
        return rc, out.getvalue(), err.getvalue()

    def test_config_error_is_fail_closed(self):
        def boom():
            raise RuntimeError("bad config")

        rc, out, err = self._run_main(boom)
        self.assertEqual(2, rc, "配置读不到必须 rc=2，不能当成通过：\n%s\n%s" % (out, err))
        self.assertIn("无法执行", err, "必须在 stderr 说清检查没跑：\n%s" % err)
        self.assertNotIn("✅", out, "检查没跑就不能输出通过标记：\n%s" % out)

    def test_repos_not_mapping_is_config_error(self):
        rc, out, err = self._run_main(lambda: {"repos": ["not", "a", "mapping"]})
        self.assertEqual(2, rc, "repos 段格式坏必须 rc=2：\n%s\n%s" % (out, err))
        self.assertIn("repos 段", err)

    def test_no_repos_is_ok(self):
        rc, out, err = self._run_main(lambda: {"repos": {}})
        self.assertEqual(0, rc, "本机没配仓是常态，不能误报：\n%s\n%s" % (out, err))
        self.assertIn("跳过", out)

    def test_absolute_path_still_blocks(self):
        with tempfile.TemporaryDirectory(prefix="agents_md_") as d:
            with io.open(os.path.join(d, "AGENTS.md"), "w", encoding="utf-8") as f:
                f.write("python D:/work/agentguard/harness/agents/auto-gate.py\n")
            rc, out, err = self._run_main(lambda: {"repos": {"demo": d}})
        self.assertEqual(1, rc, "命中绝对路径必须照旧 rc=1：\n%s\n%s" % (out, err))
        self.assertIn("demo", out)

    def test_unreadable_agents_md_is_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="agents_md_") as d:
            target = os.path.join(d, "AGENTS.md")
            with io.open(target, "w", encoding="utf-8") as f:
                f.write("# ok\n")
            real_open = io.open

            def fake_open(path, *a, **k):
                if os.path.abspath(path) == os.path.abspath(target):
                    raise PermissionError("denied by test")
                return real_open(path, *a, **k)

            with mock.patch("builtins.open", fake_open):
                rc, out, err = self._run_main(lambda: {"repos": {"demo": d}})
        self.assertEqual(2, rc, "AGENTS.md 读不到必须 rc=2，不能宣称全部合格：\n%s\n%s" % (out, err))
        self.assertIn("无法读取", err)
        self.assertNotIn("全部业务仓", out)


class TestNewEngineNameIsMatched(unittest.TestCase):
    """P1-2②（第六轮评审 2026-10-28）：改名后新写法必须同样被拦。

    旧模式只认 `agentguard` 字样，于是 `/opt/guardrail-engine`、`C:/tools/guardrail-engine`
    这类**改名后**的绝对路径一个字符都匹配不上 —— 检查在新机器上形同虚设。
    """

    def _check(self, text):
        with tempfile.TemporaryDirectory(prefix="agents_md_") as d:
            with io.open(os.path.join(d, "AGENTS.md"), "w", encoding="utf-8") as f:
                f.write(text + "\n")
            old = C.load
            C.load = lambda: {"repos": {"demo": d}}
            out, err = io.StringIO(), io.StringIO()
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    rc = M.main([])
            finally:
                C.load = old
        return rc, out.getvalue(), err.getvalue()

    def test_posix_new_name_blocks(self):
        rc, out, err = self._check("python /opt/guardrail-engine/harness/run_gate.py --validate")
        self.assertEqual(1, rc, "POSIX 上的新名绝对路径必须拦：\n%s\n%s" % (out, err))

    def test_windows_new_name_blocks(self):
        rc, out, err = self._check("python C:/tools/guardrail-engine/harness/run_gate.py")
        self.assertEqual(1, rc, "Windows 上的新名绝对路径必须拦：\n%s\n%s" % (out, err))

    def test_home_pointer_is_not_a_violation(self):
        """反向锁：按本仓文档写的 `~/…` 指针是机器无关写法，拦它就是一装上就红。

        `integrations/agent-rule-snippet.md` 教业务仓写 `~/.guardrail-engine/harness.path`
        与 `~/.guardrail-engine/GLOBAL_AGENTS.md` —— 这两串必然出现在**合规**的 AGENTS.md 里。
        """
        rc, out, err = self._check(
            "路径约定：`~/.guardrail-engine/harness.path`（或旧名 `~/.agentguard/harness.path`），"
            "全局规则见 `~/.guardrail-engine/GLOBAL_AGENTS.md`。")
        self.assertEqual(0, rc, "家园相对指针被误判成绝对路径：\n%s\n%s" % (out, err))
        self.assertIn("符合机器无关原则", out)


class TestRepoScope(unittest.TestCase):
    """P1-2①（第六轮评审 2026-10-28）：`--repo` 只查目标仓，且不读 config。

    原先是**每次提交**都舰队级扫描：本机 33 个受管仓，与本次改动无关的仓 AGENTS.md
    坏了就拦这次提交；而且多一次配置读取 → 多一种"配置坏了所以提交不过"的失效面。
    """

    def _run(self, argv, fake_load=None):
        old = C.load
        C.load = fake_load if fake_load is not None else old
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = M.main(argv)
        finally:
            C.load = old
        return rc, out.getvalue(), err.getvalue()

    def test_only_target_repo_is_scanned(self):
        with tempfile.TemporaryDirectory(prefix="agents_md_") as d:
            good = os.path.join(d, "good")
            bad = os.path.join(d, "bad")
            os.makedirs(good)
            os.makedirs(bad)
            with io.open(os.path.join(bad, "AGENTS.md"), "w", encoding="utf-8") as f:
                f.write("python /opt/agentguard/harness/run_gate.py\n")
            # config 里的 bad 有违规，但本次提交的是 good → 不该拦
            rc, out, err = self._run(["--repo", good],
                                     lambda: {"repos": {"bad": bad, "good": good}})
        self.assertEqual(0, rc, "别的仓有违规不该拦本次提交：\n%s\n%s" % (out, err))

    def test_repo_mode_does_not_read_config(self):
        """直接证据：repo 模式下 `_config.load()` 一次都不许调。"""
        with tempfile.TemporaryDirectory(prefix="agents_md_") as d:
            calls = []

            def boom():
                calls.append(1)
                raise RuntimeError("repo 模式不许读 config")

            rc, out, err = self._run(["--repo", d], boom)
        self.assertEqual([], calls, "repo 模式读了 config —— 提交链又多出一种配置坏=提交不过")
        self.assertEqual(0, rc, "空仓（无 AGENTS.md）必须通过：\n%s\n%s" % (out, err))

    def test_missing_repo_dir_is_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="agents_md_") as d:
            rc, out, err = self._run(["--repo", os.path.join(d, "nope")])
        self.assertEqual(2, rc, "目标仓不存在必须 rc=2（判都没判成）：\n%s\n%s" % (out, err))
        self.assertIn("无法执行", err)

    def test_unknown_flag_is_fail_closed(self):
        """静默忽略未知旗标 = 调用方以为单仓模式生效、实际跑了舰队级扫描。"""
        with tempfile.TemporaryDirectory(prefix="agents_md_") as d:
            rc, out, err = self._run(["--repos", d])
        self.assertEqual(2, rc, "未知参数必须 rc=2：\n%s\n%s" % (out, err))
        self.assertIn("未知参数", err)

    def test_repo_without_value_is_fail_closed(self):
        rc, out, err = self._run(["--repo"])
        self.assertEqual(2, rc, "--repo 缺值必须 rc=2：\n%s\n%s" % (out, err))
        self.assertIn("无法执行", err)

    def test_repo_mode_still_blocks_absolute_path(self):
        with tempfile.TemporaryDirectory(prefix="agents_md_") as d:
            with io.open(os.path.join(d, "AGENTS.md"), "w", encoding="utf-8") as f:
                f.write("python D:/work/guardrail-engine/harness/agents/auto-gate.py\n")
            rc, out, err = self._run(["--repo", d])
        self.assertEqual(1, rc, "单仓模式必须照拦绝对路径：\n%s\n%s" % (out, err))
        self.assertIn("含绝对路径", out)


if __name__ == "__main__":
    unittest.main()
