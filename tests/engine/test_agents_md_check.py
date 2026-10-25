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
    def _run_main(self, fake_load):
        old = C.load
        C.load = fake_load
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = M.main()
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


if __name__ == "__main__":
    unittest.main()
