# -*- coding: utf-8 -*-
"""评审 v13 复查遗留项①：篡改可发现哨兵 fail-open —— 查不了就静默放行。

缺陷原文（2026-09-16 实测，`reports/_localyaml_probe2.txt` 同批取证）
================================================================================
`checks/harness_guard.py` 用宽容包装 `changed_files(ROOT)` 取"工程仓未提交的受保护文件"，
而那个函数自己在 `harness/_config.py` 里写着「查不了时返回 []，需要 fail-closed 语义的调用方
请用 `changed_files_ex()`」。于是：**工程仓 git 一坏（.git 被清理 / git 不可用 / 查询超时 /
权限不足），空清单 → touched 为空 → `return 0`，哨兵静默失效，输出里一个字都不说。**
同一时刻链路上其它各处全是 fail-closed（分发器缺实现 exit 2、pre-commit 读不到暂存区 rc=2、
run_gate 脚本越界 rc=2），只有这一处相反。

处置（用户拍板）：改用 `changed_files_ex`，「读不了」= rc=2 fail-closed + 说清原因。
代价已知并接受：工程仓不健康时 33 个端仓提交会被一并拦下（先例：分发器先跑 harness_self_check）。

本文件钉住四件事
================================================================================
  ① **读不了 → rc=2**（不是 0 放行、也不是 1 业务拦截：语义必须落在"配置/环境"这一档）；
  ② **阳性对照**：读得了且干净 → 仍 rc=0；读得了且脏 → 仍 rc=1。没有这两条，
     「恒返回 2」也能让①变绿；
  ③ **真跑一遍**：把引擎的 checks/harness/ 复制到**没有 .git** 的临时目录，跑真哨兵 ——
     不靠打桩，直接验"工程仓不是 git 工作区"这条真实路径（这条才是 clone 半残的实际形态）；
  ④ **桩点搬家算锁的一部分**：`tests/test_review_v13_batch2.py` 打的是 `changed_files`，
     哨兵换名后那条锁会**悄悄失效**（真读工程仓 → 结果取决于跑测试时工作区脏不脏）。
     这里用结构化断言把"哨兵只许用带失败信号的版本"钉在源码上。
"""
from __future__ import unicode_literals

import ast
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _tmpclean import rmtree_force as _rmtree_force  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HARNESS_LIB = os.path.join(ROOT, "harness")
CHECKS = os.path.join(ROOT, "checks")
GUARD_SRC = os.path.join(CHECKS, "harness_guard.py")

if CHECKS not in sys.path:
    sys.path.insert(0, CHECKS)
if HARNESS_LIB not in sys.path:
    sys.path.insert(0, HARNESS_LIB)

PROTECTED = "checks/pc_code_check.py"
NOT_A_REPO_ERR = "git diff --name-only --diff-filter=d -z 失败（rc=128）：fatal: not a git repository"


class _StubbedGuard(unittest.TestCase):
    """把哨兵的"工程仓改动查询"钉成桩（真读的话结果取决于跑测试时工作区状态）。"""

    def _run(self, result):
        import _config
        import harness_guard
        orig = _config.changed_files_ex
        _config.changed_files_ex = lambda repo, *a, **k: result
        buf = io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        old_argv, old_cwd = sys.argv, os.getcwd()
        tmp = tempfile.mkdtemp(prefix="b7-")
        try:
            sys.stdout = sys.stderr = buf
            sys.argv = ["harness_guard.py", "demo_pc"]
            os.chdir(tmp)                  # 非工程根 → 不触发 self 模式识别
            rc = harness_guard.main()
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            sys.argv = old_argv
            os.chdir(old_cwd)
            _config.changed_files_ex = orig
            _rmtree_force(tmp)
        return rc, buf.getvalue()


class TestGuardFailsClosedWhenUnreadable(_StubbedGuard):
    def test_read_failure_blocks_with_rc2(self):
        """① 读不了 → rc=2（配置/环境档），且把"不是业务代码问题"与跳过方式说清。"""
        rc, out = self._run(([], NOT_A_REPO_ERR))
        self.assertEqual(rc, 2, "查不了工程仓必须 fail-closed（rc=2），不得静默放行：\n%s" % out)
        self.assertIn("fail-closed", out)
        self.assertIn("无法验证门禁文件是否被弱化", out, "要说清拦的是什么")
        self.assertIn("原因", out, "要带出底层错误，否则无从排查")
        self.assertIn("不是业务代码问题", out, "rc=2 必须点明这不是要人去改业务代码")
        self.assertIn("--no-verify", out, "要说清紧急时的出口（跳过=本次没有门禁）")

    def test_clean_query_still_passes(self):
        """② 阳性对照：读得了且干净 → rc=0（防「恒返回 2」把上面那条测绿）。"""
        rc, out = self._run(([], ""))
        self.assertEqual(rc, 0, "工程仓干净时不得拦提交：\n%s" % out)

    def test_dirty_query_still_blocks_with_rc1(self):
        """② 阳性对照：读得了且脏 → rc=1（业务拦截档，不能被新逻辑改成 2）。"""
        rc, out = self._run(([os.path.join(ROOT, PROTECTED.replace("/", os.sep))], ""))
        self.assertEqual(rc, 1, "受保护文件有未提交改动时必须拦截（且是 rc=1）：\n%s" % out)
        self.assertIn("已拦截", out)


class TestGuardUsesFailingVariant(unittest.TestCase):
    """④ 结构化锁：哨兵只许用带失败信号的收集器。

    判据走 **ast**（只看真实的调用节点），不看源码文本 —— 本文件上级那句
    「原先用宽容包装 changed_files(ROOT)」的**注释**也含同样的字样，
    泛匹配会自伤（`test_review_v8_batch3.py` 的 import 锁记过同一个坑：
    文件里的解释性注释把结构化断言绊倒）。
    """

    @staticmethod
    def _called_names(path):
        with io.open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        return {n.func.id for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}

    def test_guard_calls_changed_files_ex(self):
        called = self._called_names(GUARD_SRC)
        self.assertIn("changed_files_ex", called, "哨兵必须用带失败信号的收集器")
        self.assertNotIn("changed_files", called,
                         "哨兵不得再用宽容包装 changed_files()（查不了=空清单=fail-open）")

    def test_exit_code_documented(self):
        """退出码是跨入口契约（skill/分发器都按它分流），docstring 必须跟上。"""
        with io.open(GUARD_SRC, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("2=**读不出**工程仓的改动", src, "新增的 rc=2 语义要写进模块契约")
        self.assertIn("_not_a_worktree_hint", src)


class TestRealNonGitEngineBlocks(unittest.TestCase):
    """③ 真跑一遍：工程仓不是 git 工作区（clone 半残的真实形态）→ rc=2。"""

    def test_engine_copy_without_git_blocks(self):
        d = tempfile.mkdtemp(prefix="b7-real-")
        try:
            eng = os.path.join(d, "engine")
            for sub in ("checks", "harness", "config"):
                src = os.path.join(ROOT, sub)
                if os.path.isdir(src):
                    shutil.copytree(src, os.path.join(eng, sub),
                                    ignore=shutil.ignore_patterns("__pycache__"))
            self.assertFalse(os.path.exists(os.path.join(eng, ".git")),
                             "本用例的前提就是副本**没有** .git")
            end = os.path.join(d, "endrepo")
            os.makedirs(end)
            # HARNESS_LOG_FILE 指向临时目录（test_coverage 的隔离锁：测试绝不许往真实的
            # reports/gate_log.jsonl 里写探针记录 —— N-2 实测污染过 124 条）。本用例只跑哨兵、
            # 哨兵不写台账，但隔离是这一层的约定，不该靠"我知道它不写"来省。
            r = subprocess.run([sys.executable, os.path.join(eng, "checks", "harness_guard.py"),
                                "demo_pc", "--repo", end],
                               cwd=d, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=120,
                               env=dict(os.environ,
                                        HARNESS_LOG_FILE=os.path.join(d, "gate_log.jsonl")))
            out = (r.stdout or "") + (r.stderr or "")
            self.assertEqual(r.returncode, 2,
                             "没有 .git 的工程仓必须 fail-closed（旧版这里 rc=0 静默放行）：\n%s" % out)
            self.assertIn("不是 git 工作区", out, "要给出可执行的修复指引（重跑 git clone）")
        finally:
            _rmtree_force(d)


class TestWorktreeHintDetection(unittest.TestCase):
    """`_not_a_worktree_hint` 两向探针：非工作区说有、真工作区说没有。

    判定刻意用 git 的**退出码/输出**而不是匹配报错文本 —— 那些文本随 locale 变
    （中文 git 输出「不是一个 git 仓库」），匹配字符串等于给自己埋一个只在英文环境下有效的提示。
    """

    def _hint_with_root(self, root):
        import harness_guard
        old = harness_guard.ROOT
        harness_guard.ROOT = root
        try:
            return harness_guard._not_a_worktree_hint()
        finally:
            harness_guard.ROOT = old

    def test_non_worktree_gets_clone_hint(self):
        d = tempfile.mkdtemp(prefix="b7-hint-")
        try:
            hint = self._hint_with_root(d)
            self.assertIn("git clone", hint, "非工作区必须给 clone 指引，而不是一句空话")
        finally:
            _rmtree_force(d)

    def test_real_worktree_gets_no_hint(self):
        self.assertEqual("", self._hint_with_root(ROOT),
                         "工程仓是真工作区时不得乱报 clone 指引（假指引比没指引更坏）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
