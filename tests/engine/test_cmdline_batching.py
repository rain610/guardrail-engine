# -*- coding: utf-8 -*-
"""命令行长度回归锁（2026-11-03）

缺陷现场：老代码解冲突后一次提交数百个文件（大 merge / 批量格式化），pre-commit.py 把
全部路径拼进 run_gate 的 argv，超过 Windows CreateProcess 的 32767 字符上限 →
`OSError [WinError 206] 文件名或扩展名太长`。异常没被捕获，进程以 rc=1 退出，分发器把它
翻译成「pre-commit 门禁未通过（BLOCKED rc=1）」—— 环境问题被报成业务代码问题。

本文件锁三件事：
  ① `harness/_batch.py` 切批：不丢文件、不重排、每批命令行低于上限；
  ② 三条自动入口（pre-commit / auto-gate / post-edit）确实用了它，且 pre-commit 把
     OSError 报成 rc=2（配置/环境）而不是 rc=1（业务拦截）；
  ③ 真起子进程验证：400 个真实长度的路径不再触发 WinError 206。
"""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TESTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "harness")
sys.path.insert(0, HARNESS)
sys.path.insert(0, TESTS)
from _tmpclean import mkdtemp_cleanup as _mkdtemp  # noqa: E402
import _batch  # noqa: E402

# 台账隔离（仓库惯例，见 engine/test_coverage.py）：本文件只**构造** run_gate 的命令行、
# 不真的跑它，但源码锁按"调用参数里出现 run_gate.py 即视为会调用"从严判定，这里照样钉住。
os.environ["HARNESS_LOG_FILE"] = os.path.join(
    _mkdtemp("harness-testlog-"), "gate_log.jsonl")

PY = sys.executable
WINDOWS_CMDLINE_LIMIT = 32767


def _realistic(n):
    """400 个约 95 字符的端仓绝对路径（实测 400 个 ≈ 38k 字符，必超 Windows 上限）。"""
    base = ("D:/Rain/project/gxgk_pc/src/views/business/management/some/deep/"
            "module/component/file_%04d.vue")
    return [base % i for i in range(n)]


class SplitTest(unittest.TestCase):
    def test_keeps_every_file_once_and_in_order(self):
        files = _realistic(400)
        self.assertEqual([f for b in _batch.split(files) for f in b], files)

    def test_each_batch_within_budget(self):
        files = _realistic(400)
        for batch in _batch.split(files):
            self.assertLessEqual(sum(_batch._arg_len(a) for a in batch),
                                 _batch._CMD_BUDGET)

    def test_400_realistic_paths_exceed_windows_limit_unsplit(self):
        """夹具自证：不切批时命令行确实超限，否则上面的断言测不到东西。"""
        files = _realistic(400)
        self.assertGreater(len(" ".join(files)), WINDOWS_CMDLINE_LIMIT)

    def test_batched_command_lines_fit_windows_limit(self):
        files = _realistic(400)
        prefix = [PY, os.path.join(HARNESS, "run_gate.py"), "example",
                  "--repo", "D:/Rain/project/gxgk_pc"]
        batches = list(_batch.split(files, prefix))
        self.assertGreater(len(batches), 1, "400 个路径必须被切成多批")
        for batch in batches:
            self.assertLess(len(" ".join(prefix + batch)), WINDOWS_CMDLINE_LIMIT)

    def test_single_overlong_path_is_not_dropped(self):
        huge = "D:/x/" + "a" * 50000 + ".vue"
        self.assertEqual(list(_batch.split([huge], budget=24000)), [[huge]])

    def test_empty_input_yields_no_batches(self):
        self.assertEqual(list(_batch.split([])), [])


class CallersUseBatchingTest(unittest.TestCase):
    """源码锁：同一个缺陷还有两个同形入口，任何一个绕过 _batch 都会在那边复发。"""

    def _src(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            return f.read()

    def test_pre_commit_batches_and_maps_oserror_to_rc2(self):
        src = self._src("harness/hooks/pre-commit.py")
        self.assertIn("from _batch import split", src)
        self.assertIn("except OSError", src)
        self.assertNotIn("os.path.abspath(target_repo)] + files", src)

    def test_pre_commit_expands_staged_paths_via_stdin(self):
        src = self._src("harness/hooks/pre-commit.py")
        self.assertIn('"--stdin"', src)
        self.assertNotIn('"--"] + rels', src)

    def test_auto_gate_batches(self):
        self.assertIn("from _batch import split",
                      self._src("harness/agents/auto-gate.py"))

    def test_post_edit_batches(self):
        self.assertIn("from _batch import split",
                      self._src("harness/hooks/post-edit.py"))


class RealSubprocessTest(unittest.TestCase):
    """行为锁：按批次真的起子进程，400 个文件不再 WinError 206。"""

    def test_batched_launch_succeeds(self):
        files = _realistic(400)
        prefix = [PY, "-c", "import sys; sys.exit(0 if len(sys.argv) > 1 else 3)"]
        batches = list(_batch.split(files, prefix))
        self.assertGreater(len(batches), 1)
        rcs = [subprocess.run(prefix + b, stdin=subprocess.DEVNULL).returncode
               for b in batches]
        self.assertEqual(rcs, [0] * len(batches))


if __name__ == "__main__":
    unittest.main()
