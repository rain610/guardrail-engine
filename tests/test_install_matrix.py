# -*- coding: utf-8 -*-
"""`cli.py installed`（本机安装矩阵）回归锁 —— 剖析报告 G4。

G4 说的是：装出来的副本各自读各自的地方，谁都不报自己那一版，"装了什么、装的哪一版"
在四个地方（引擎仓 / pip 包 / 插件缓存 / 技能副本 / 编辑期 shim）之间没有任何一个统一视图。
`installed` 补的就是这个视图。

这里锁两件事，**缺一不可**：
  ① 真的不一致时要报出来（否则这个视图只是个打印函数）；
  ② 没装的通道**不许**算作"不一致"（否则一台什么都没装的机器一进门就被 ⚠ 糊脸，
     而那段警告的文本讲的还全是"重装不换内容"—— 跟现状毫无关系）。

②为什么值得单独锁：本仓纪律是「假警报比漏报更磨损可信度」。2026-09-17 实测空 HOME，
   上一行刚印"技能副本：未安装"，下一行就报"⚠ 有副本与引擎仓不同版/不同内容"；
   成因是 `if desc != "已装": drift = True` 把**未装**和**不一致**当成了同一件事，
   而且判据是拿**提示文案**去比 —— 改一句话就静默失效。

安全约定：全部在**临时 HOME** 里跑（重定向 HOME/USERPROFILE/CLAUDE_CONFIG_DIR），
绝不读改本机真实的 ~/.claude 与 ~/.agentguard。
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "agentguard", "cli.py")
SKILL_SRC = os.path.join(ROOT, "integrations", "agentguard-skill")
DRIFT_MARK = "有副本与引擎仓不同版/不同内容"


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class InstallMatrixTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home)

    def _run(self):
        env = dict(os.environ)
        env["HOME"] = self.home
        env["USERPROFILE"] = self.home
        env["CLAUDE_CONFIG_DIR"] = os.path.join(self.home, ".claude")
        r = subprocess.run([sys.executable, CLI, "installed"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=env, cwd=ROOT)
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    # ---- ① 真空机器：一个字都不许吓人 ----

    def test_fresh_machine_reports_no_drift(self):
        """一台什么都没装的机器：矩阵要说"未安装"，**不许**报"与引擎仓不一致"。

        这条是 2026-09-17 那个假警报的固化：当时 shim 未装被算成 drift，
        于是空机器上 `quickstart` 的收尾是一段 ⚠，读的人会以为自己装坏了。
        """
        rc, out = self._run()
        self.assertEqual(rc, 0, out)
        self.assertNotIn(DRIFT_MARK, out,
                         "一台什么都没装的机器被判成了「有副本与引擎仓不一致」：\n%s" % out)
        for line in ("技能副本", "编辑期 shim"):
            self.assertIn(line, out, "矩阵少了 %s 这一格：\n%s" % (line, out))
        self.assertIn("未装", out, "空机器上应当出现「未安装 / 未装」：\n%s" % out)

    # ---- ② 真不一致：必须报 ----

    def test_stale_skill_copy_is_reported(self):
        """阴性对照（防"把警告删了了事"）：技能副本落后于引擎仓时，必须报出来。"""
        t = os.path.join(self.home, ".claude", "skills", "agentguard")
        _write(os.path.join(t, "SKILL.md"), "# 上一版的内容（与引擎仓不同）\n")
        rc, out = self._run()
        self.assertEqual(rc, 0, out)
        self.assertIn("落后", out, "技能副本与引擎仓不一致却没报：\n%s" % out)
        self.assertIn(DRIFT_MARK, out, "报了差异却没说清该怎么办：\n%s" % out)

    def test_shim_pointing_at_a_missing_repo_is_reported(self):
        """编辑期 shim 装了、但指针指向一个**不存在**的仓库 —— 这是最该报的一种。

        它的现场是"7 家客户端的编辑期门禁整体无声失效"：shim 在、路径在、什么也不报，
        只是每次检查都读不到引擎。所以它必须算偏差（与"没装"刚好相反）。
        """
        agents = os.path.join(self.home, ".agentguard")
        _write(os.path.join(agents, "hooks", "agentguard-gate.py"), "# 探针\n")
        _write(os.path.join(agents, "harness.path"),
               os.path.join(self.tmp.name, "gone-repo").replace("\\", "/") + "\n")
        rc, out = self._run()
        self.assertEqual(rc, 0, out)
        self.assertIn("指针指向的仓库不存在", out, "shim 指到了不存在的仓库却没报：\n%s" % out)
        self.assertIn(DRIFT_MARK, out, "这种情形必须算偏差：\n%s" % out)

    def test_plugin_registered_but_directory_gone_is_reported(self):
        """宿主登记里写着插件，但那个目录已经不在了 —— 登记与实物不符，要报。"""
        cfg = os.path.join(self.home, ".claude")
        _write(os.path.join(cfg, "plugins", "installed_plugins.json"),
               json.dumps({"version": 2, "plugins": {"agentguard@agentguard-tools": [
                   {"scope": "user", "version": "0.1.2-dev",
                    "installPath": os.path.join(self.tmp.name, "gone-plugin")}]}}, indent=2) + "\n")
        rc, out = self._run()
        self.assertEqual(rc, 0, out)
        self.assertIn("登记与实物不符", out, "登记有、目录没了却没报：\n%s" % out)
        self.assertIn(DRIFT_MARK, out, "这种情形必须算偏差：\n%s" % out)


if __name__ == "__main__":
    unittest.main()
