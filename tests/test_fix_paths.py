# -*- coding: utf-8 -*-
"""fix_paths.py 回归锁（v0.1.1-dev）：改写 .mcp.json 三处 + fail-fast。

全部在临时目录进行（不改仓库资产 / 不碰真实安装）。
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "sandbox", "fix_paths.py")


def norm(p):
    return os.path.abspath(p).replace(os.sep, "/")


TEMPLATE = """{
  "mcpServers": {
    "agentguard": {
      "command": "D:/old-machine/python.exe",
      "args": [
        "D:/old-machine/harness/agentguard/mcp.py"
      ],
      "env": {
        "AGENTGUARD_DIR": "D:/old-machine/harness"
      }
    }
  }
}
"""


class FixPathsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # 显式清理：不清理会在 GC 期抛 ResourceWarning（被测的 CI 严格句柄门槛会红）
        self.addCleanup(self.tmp.cleanup)
        self.base = self.tmp.name
        self.harness = os.path.join(self.base, "myharness")
        os.makedirs(os.path.join(self.harness, "agentguard"))
        with io.open(os.path.join(self.harness, "agentguard", "mcp.py"), "w",
                     encoding="utf-8") as f:
            f.write("# test\n")
        with io.open(os.path.join(self.harness, "VERSION"), "w", encoding="utf-8") as f:
            f.write("9.9.9-dev\n")
        self.mkt = os.path.join(self.base, "pkg", "marketplace")
        d = os.path.join(self.mkt, "plugins", "agentguard")
        os.makedirs(d)
        self.mj = os.path.join(d, ".mcp.json")
        with io.open(self.mj, "w", encoding="utf-8") as f:
            f.write(TEMPLATE)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args):
        return subprocess.run([sys.executable, SCRIPT] + list(args),
                              capture_output=True, text=True, encoding="utf-8", errors="replace")

    def test_rewrites_all_three_fields(self):
        r = self._run("--harness", self.harness, "--marketplace", self.mkt, "--python", sys.executable)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with io.open(self.mj, encoding="utf-8") as f:
            data = json.loads(f.read())
        srv = data["mcpServers"]["agentguard"]
        self.assertEqual(srv["command"], norm(sys.executable))
        self.assertEqual(srv["args"][0], norm(self.harness) + "/agentguard/mcp.py")
        self.assertEqual(srv["env"]["AGENTGUARD_DIR"], norm(self.harness))
        # 幂等：再跑一次结果不变
        r2 = self._run("--harness", self.harness, "--marketplace", self.mkt, "--python", sys.executable)
        self.assertEqual(r2.returncode, 0)
        with io.open(self.mj, encoding="utf-8") as f:
            data2 = json.loads(f.read())
        self.assertEqual(data2, data)

    def test_fails_when_harness_lacks_mcp(self):
        """目录在、只是没有 mcp.py = **指错了仓 / 路径拼错**（负对照见下一条）。

        这里必须**不出现** clone 那套：那种场景下让人去 clone 是错误引导 —— 他要改的是
        `--harness` 的取值。这条与下一条合起来才咬得住"两个分支被合成一句"的变异。
        """
        bad = os.path.join(self.base, "noharness")
        os.makedirs(bad)
        r = self._run("--harness", bad, "--marketplace", self.mkt)
        self.assertEqual(r.returncode, 1)
        self.assertIn("mcp.py", r.stdout)
        self.assertNotIn("git clone", r.stdout,
                         "目录存在、只是缺 mcp.py —— 这是指错了仓，不该让用户去 clone：\n%s" % r.stdout)

    def test_missing_harness_dir_points_at_the_clone_step(self):
        """目录**不存在**时，要给「先去 clone 引擎仓」这条路，而不是只说"没找到 mcp.py"。

        这是首次接入最常见的第一个坑：安装包内**不含**引擎（有意——提交钩子 / MCP / shim
        都指向仓库，引擎更新即生效），而《安装说明》原先把「已 clone 引擎仓」塞在括号里当
        "前置条件"、不是一步。修复后包内《安装说明》多了「零、先 clone 引擎仓」一节，
        且本脚本在**这种**误用下直接给出可复制的 clone 命令。
        """
        ghost = os.path.join(self.base, "never-cloned")     # 刻意不创建
        r = self._run("--harness", ghost, "--marketplace", self.mkt)
        self.assertEqual(r.returncode, 1)
        self.assertIn("agentguard.git", r.stdout,
                      "目录不存在时没给出可复制的 clone 命令（用户只能自己猜仓库地址）：\n%s" % r.stdout)
        self.assertIn("零", r.stdout,
                      "没把用户指到包内《安装说明》的对应一节：\n%s" % r.stdout)

    def test_fails_when_mcp_json_missing(self):
        empty = os.path.join(self.base, "emptymkt")
        os.makedirs(empty)
        r = self._run("--harness", self.harness, "--marketplace", empty)
        self.assertEqual(r.returncode, 1)
        self.assertIn(".mcp.json", r.stdout)


if __name__ == "__main__":
    unittest.main()
