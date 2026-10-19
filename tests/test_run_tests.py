# -*- coding: utf-8 -*-
"""run_tests.py 回归锁（整改 C1）：解析逻辑 + 端到端调度行为。

全部用临时目录里的迷你测试文件，不触碰真实 tests/。
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "sandbox"))
import run_tests  # noqa: E402


class ParseOutputTest(unittest.TestCase):
    def test_ok_with_skip(self):
        out = "test_a (a.A) ... ok\n\n----------------------------------------------------------------------\nRan 45 tests in 12.300s\n\nOK (skipped=2)\n"
        r = run_tests.parse_output(out)
        self.assertEqual((r["ran"], r["skipped"], r["ok"]), (45, 2, True))

    def test_failed(self):
        out = "Ran 3 tests in 1.000s\n\nFAILED (failures=1, errors=1)\n"
        r = run_tests.parse_output(out)
        self.assertEqual(r["ran"], 3)
        self.assertFalse(r["ok"])
        self.assertIn("failures=1", r["failinfo"])


class RunnerEndToEndTest(unittest.TestCase):
    def _write(self, d, name, body):
        with io.open(os.path.join(d, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(body)

    def test_runner_parallel(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "test_alpha.py",
                        "import unittest\nclass A(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n")
            self._write(d, "test_beta.py",
                        "import unittest\nclass B(unittest.TestCase):\n    def test_bad(self):\n        self.assertEqual(1, 2)\n")
            r = subprocess.run([sys.executable, os.path.join(ROOT, "sandbox", "run_tests.py"),
                                "--tests-dir", d, "-j", "2"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            out = r.stdout + r.stderr
            self.assertIn("test_beta.py", out)
            self.assertIn("失败 1 个文件", out)

            self._write(d, "test_beta.py",
                        "import unittest\nclass B(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n")
            r2 = subprocess.run([sys.executable, os.path.join(ROOT, "sandbox", "run_tests.py"),
                                 "--tests-dir", d, "-j", "2"],
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
            self.assertIn("全部通过", (r2.stdout + r2.stderr))


if __name__ == "__main__":
    unittest.main()
