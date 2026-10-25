# -*- coding: utf-8 -*-
"""技能卸载归档的回归锁 —— 剖析报告 G8。

G8 说的是两件事，都发生在"卸载"这一侧、而且都**看不见**：
  ① 反复「卸载 + 重装」会在宿主 skills 目录里堆 `.disabled-*` 残骸（每份都是完整技能副本），
     而 `skill status` 只 stat 了 `<宿主>/guardrail-engine` 一个路径 —— 不枚举父目录，
     对残骸**完全盲**；文档里也只写着归档"可逆"，没人写它的代价。
  ② 归档名是秒级戳（`%Y%m%d-%H%M%S`），同一秒内两次卸载会撞上同名目录，Windows 上
     `os.rename` 直接抛 OSError，而打印出来的是「改名归档失败（可能被占用）」——
     把**重名**报成了**文件占用**，运维会去查一个根本不存在的锁。

这里锁：残骸要报（且刷屏要有上限）、归档名要挑空的（重名根本不发生）、
失败原因要按真实错误码分级、失败时必须说清"技能没被移动"。

安全约定：subprocess 那几个用例全部重定向 HOME/USERPROFILE 到临时目录；
进程内那两个用例只调纯函数 + 临时目录，不动本机任何真实目录。
"""
import io
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "guardrail_engine", "cli.py")
SKILL_NAME = "guardrail-engine"


def _load_cli():
    """按路径加载 cli.py（不起 subprocess），用于直接喂它的纯函数。"""
    spec = importlib.util.spec_from_file_location("_cli_under_test_skillarch", CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FrozenTime(object):
    """冻住时间戳：归档名可预测，测"挑空名"不必跟秒边界赛跑。"""

    def __init__(self, s):
        self.s = s

    def strftime(self, _fmt):
        return self.s


class SkillStatusResidueTest(unittest.TestCase):
    """① `skill status` 必须看得见残骸。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        self.skills = os.path.join(self.home, ".claude", "skills")
        os.makedirs(self.skills)

    def _run_status(self):
        env = dict(os.environ)
        env["HOME"] = self.home
        env["USERPROFILE"] = self.home
        r = subprocess.run([sys.executable, CLI, "skill", "status"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=env, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout or ""

    def _fake_archive(self, name):
        d = os.path.join(self.skills, "%s.disabled-%s" % (SKILL_NAME, name))
        os.makedirs(d)
        with io.open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("# 残骸\n")
        return d

    def test_fresh_machine_says_nothing_about_residue(self):
        """没有残骸时不许提"残骸" —— 假警报比漏报更磨损可信度。"""
        out = self._run_status()
        self.assertIn("未安装", out)
        self.assertNotIn("残骸", out)

    def test_residue_is_listed_with_its_path(self):
        d = self._fake_archive("20260101-000000")
        out = self._run_status()
        self.assertIn("残骸", out)
        self.assertIn(d.replace("\\", "/"), out.replace("\\", "/"),
                      "报了份数却没报路径 —— 人还是不知道该删哪个")
        self.assertIn("1 份", out)

    def test_the_host_line_still_says_installed_state(self):
        """残骸是**附加信息**：不能把"装了没有"这件事盖掉。"""
        os.makedirs(os.path.join(self.skills, SKILL_NAME))
        self._fake_archive("20260101-000001")
        out = self._run_status()
        self.assertIn("已安装", out)
        self.assertIn("残骸", out)

    def test_residue_listing_is_capped(self):
        """卸载过很多次时不刷屏：列前 3 份 + 总数（真要看全，目录就在上一行）。"""
        for i in range(5):
            self._fake_archive("20260101-00000%d" % i)
        out = self._run_status()
        self.assertIn("5 份", out)
        self.assertIn("还有 2 份", out)
        self.assertEqual(out.count("残骸: "), 3, "只该列出前 3 份：\n%s" % out)

    def test_archives_of_other_skills_are_not_counted(self):
        os.makedirs(os.path.join(self.skills, "other-skill.disabled-20260101-000000"))
        out = self._run_status()
        self.assertNotIn("残骸", out, "别人家的残骸不算我们的账")


class ArchiveNameTest(unittest.TestCase):
    """② 归档名要挑空的：重名这件事从此不发生。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cli = _load_cli()
        self.real_time = self.cli.time
        self.cli.time = _FrozenTime("20260101-000000")
        self.addCleanup(lambda: setattr(self.cli, "time", self.real_time))
        self.target = os.path.join(self.tmp.name, "skills", SKILL_NAME)
        os.makedirs(self.target)

    def test_free_name_is_the_plain_stamp(self):
        want = self.target + ".disabled-20260101-000000"
        self.assertEqual(self.cli.archive_name(self.target), want)

    def test_collisions_are_answered_with_a_sequence_suffix(self):
        """同一秒里的第二次、第三次卸载：加序号，**不许**失败、也不许覆盖已有的归档。"""
        first = self.cli.archive_name(self.target)
        os.makedirs(first)
        second = self.cli.archive_name(self.target)
        self.assertEqual(second, first + "-2")
        os.makedirs(second)
        third = self.cli.archive_name(self.target)
        self.assertEqual(third, first + "-3")

    def test_existing_archives_are_left_intact(self):
        first = self.cli.archive_name(self.target)
        os.makedirs(first)
        with io.open(os.path.join(first, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("# 旧归档\n")
        self.cli.archive_name(self.target)
        with io.open(os.path.join(first, "SKILL.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "# 旧归档\n", "挑名字不得动已有归档")


class RenameFailureHintsTest(unittest.TestCase):
    """③ 失败原因按真实错误码分级，并且说清"什么没发生"。"""

    def setUp(self):
        self.cli = _load_cli()

    def test_name_collision_is_not_reported_as_a_lock(self):
        import errno
        hint = self.cli._rename_hint(OSError(errno.EEXIST, "File exists"))
        self.assertIn("归档名已被占用", hint)
        self.assertNotIn("占用或没有权限", hint)

    def test_busy_or_permission_is_reported_as_such(self):
        import errno
        for en in (errno.EBUSY, errno.EACCES, errno.EPERM):
            self.assertIn("占用或没有权限", self.cli._rename_hint(OSError(en, "x")))

    def test_unknown_errors_do_not_get_a_made_up_cause(self):
        import errno
        hint = self.cli._rename_hint(OSError(errno.ENOSPC, "No space left"))
        self.assertIn("系统错误", hint)
        self.assertNotIn("占用", hint, "不知道原因就别编一个原因")

    def test_failed_uninstall_says_the_skill_was_not_moved(self):
        """真实路径上跑一次：os.rename 抛错 → 必须说清技能还在原处（卸载没生效）。"""
        import argparse
        import contextlib
        import errno
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        target = os.path.join(tmp.name, "skills", SKILL_NAME)
        os.makedirs(target)
        ops = os.path.join(tmp.name, "ops.jsonl")

        real = os.rename
        old_ops = os.environ.get("AGENTGUARD_OPS_LOG")
        os.environ["AGENTGUARD_OPS_LOG"] = ops
        os.rename = lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EBUSY, "busy"))
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = self.cli.cmd_skill(argparse.Namespace(action="uninstall", dir=target,
                                                           target=None))
        finally:
            os.rename = real
            if old_ops is None:
                os.environ.pop("AGENTGUARD_OPS_LOG", None)
            else:
                os.environ["AGENTGUARD_OPS_LOG"] = old_ops

        out = buf.getvalue()
        self.assertEqual(rc, 2, out)
        self.assertIn("占用或没有权限", out)
        self.assertIn("技能未被移动", out.replace("*", ""),
                      "只印失败原因看不出技能到底动没动（本次'卸载'其实没生效）")
        self.assertTrue(os.path.isdir(target), "失败时技能必须还在原处")
        self.assertFalse(os.path.exists(ops), "失败不该往台账里写一条卸载成功")


if __name__ == "__main__":
    unittest.main()
