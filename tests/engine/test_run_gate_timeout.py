# -*- coding: utf-8 -*-
"""`run_gate` 子进程时限的锁（第六轮评审 P1-1，2026-10-28）。

被锁的东西：**门禁子进程跑不完时，必须按「没跑成」处理，而不是按「通过」或「永远等下去」**。

  ① 超时 → 杀整棵进程树、rc=2（fail-closed）、台账写 `timed_out`/`timeout_s`；
  ② 时限可配（`GUARDRAIL_GATE_TIMEOUT`，旧名 `AGENTGUARD_GATE_TIMEOUT` 也认）；
     配错（非整数 / ≤0）→ rc=2，不执行 gate；
  ③ 顺带锁住 P1-2①：追加的 AGENTS.md 检查只查**本次目标仓**（`--repo`），
     舰队级扫描归 `run_gate --validate`（`--structure-only` 时明确跳过）。

为什么用工程副本（`_engine_copy`）：`script` 只允许解析到引擎根之内（执行边界），
而"慢脚本"必须是真进程 —— 只能写进副本的 `checks/`，不能往真仓里塞测试文件。
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _tmpclean import rmtree_force as _rmtree_force      # noqa: E402
from _engine_copy import EngineCopy                       # noqa: E402

FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    """**类级**用：本文件的慢在建副本 + 起真引擎 + 等超时，跳过时副本也不该建。

    （若打到用例上，setUpClass 照样会跑 —— 快跑档白花两次整引擎拷贝。）
    """
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


_STRIP_ENV = ("HARNESS_CONFIG", "HARNESS_LOG_FILE", "HARNESS_STATS_FILE",
              "GUARDRAIL_GATE_TIMEOUT", "AGENTGUARD_GATE_TIMEOUT",
              "SLOW_SLEEP", "SLOW_CHILD_MARKER", "SLOW_START_MARKER")

# 慢 gate 夹具：SLEEP 秒后正常退出；给了 SLOW_CHILD_MARKER 就再生一个孙进程，
# 孙进程 2 秒后写标记文件 —— 用来区分「杀整棵树」与「只杀直接子进程」。
_SLOW_GATE = '''# -*- coding: utf-8 -*-
import os, subprocess, sys, time
started = os.environ.get("SLOW_START_MARKER")
if started:
    open(started, "w").write("started")
marker = os.environ.get("SLOW_CHILD_MARKER")
if marker:
    subprocess.Popen([sys.executable, "-c",
        "import sys, time; time.sleep(2); open(sys.argv[1], 'w').write('alive')", marker])
time.sleep(float(os.environ.get("SLOW_SLEEP", "30")))
sys.exit(0)
'''

_FAST_GATE = '''# -*- coding: utf-8 -*-
import sys
sys.exit(0)
'''


class _Base(unittest.TestCase):
    """一份工程副本 + 一个临时业务仓 + 一份临时配置。"""

    @classmethod
    def setUpClass(cls):
        cls.eng = EngineCopy()
        with io.open(cls.eng.p("checks/slow_gate.py"), "w", encoding="utf-8", newline="\n") as f:
            f.write(_SLOW_GATE)
        with io.open(cls.eng.p("checks/fast_gate.py"), "w", encoding="utf-8", newline="\n") as f:
            f.write(_FAST_GATE)

    @classmethod
    def tearDownClass(cls):
        cls.eng.cleanup()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gate-timeout-")
        self.addCleanup(_rmtree_force, self.tmp)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.log = os.path.join(self.tmp, "gate_log.jsonl")

    def _cfg(self, script="checks/fast_gate.py", extra_repos=""):
        p = os.path.join(self.tmp, "cfg.toml")
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("[repos]\n"
                    "t_gate = \"%s\"\n%s"
                    "[gates.t_gate]\n"
                    "enabled = true\n"
                    "repo = \"t_gate\"\n"
                    "script = \"%s\"\n"
                    % (self.repo.replace("\\", "/"), extra_repos, script))
        return p

    def _run(self, *argv, **env_extra):
        cfg = env_extra.pop("cfg", None) or self._cfg()
        env = dict(os.environ)
        for k in _STRIP_ENV:
            env.pop(k, None)
        env["HARNESS_CONFIG"] = cfg
        env["HARNESS_LOG_FILE"] = self.log
        env.update({k: str(v) for k, v in env_extra.items()})
        r = subprocess.run([sys.executable, os.path.join(self.eng.path, "harness", "run_gate.py")]
                           + list(argv),
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, cwd=self.eng.path, timeout=90)
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def _records(self):
        if not os.path.isfile(self.log):
            return []
        with io.open(self.log, encoding="utf-8") as f:
            return [json.loads(l) for l in f.read().splitlines() if l.strip()]


@_slow("建工程副本 + 起真引擎 + 等超时（~20s）")
class TestTimeoutIsFailClosed(_Base):
    """① 超时 → rc=2 + 台账留痕。"""

    def test_timeout_blocks_and_returns_fast(self):
        t0 = time.time()
        rc, out = self._run("t_gate", "--all", GUARDRAIL_GATE_TIMEOUT=1, SLOW_SLEEP=6,
                            cfg=self._cfg(script="checks/slow_gate.py"))
        elapsed = time.time() - t0
        self.assertEqual(2, rc, "超时必须 rc=2（不能读成通过、也不能一直等）：\n%s" % out[-1500:])
        self.assertLess(elapsed, 5, "超时后没有立刻返回（杀进程树没生效）：%.1fs" % elapsed)
        self.assertIn("超过 1s", out, "输出没说清是超时（rc=2 会被误读成代码不合规）")
        self.assertIn("GUARDRAIL_GATE_TIMEOUT", out, "没告诉用户怎么调大时限")

    def test_timeout_is_recorded_in_ledger(self):
        rc, out = self._run("t_gate", "--all", GUARDRAIL_GATE_TIMEOUT=1, SLOW_SLEEP=6,
                            cfg=self._cfg(script="checks/slow_gate.py"))
        self.assertEqual(2, rc, out[-1500:])
        recs = self._records()
        self.assertEqual(1, len(recs), "一次调用必须只落一行台账：%r" % recs)
        self.assertTrue(recs[0].get("timed_out"),
                        "台账没记 timed_out —— 事后分不清这次 rc=2 是超时还是配置错：%r" % recs[0])
        self.assertEqual(1, recs[0].get("timeout_s"), "台账没记当时的阈值秒数：%r" % recs[0])
        self.assertEqual(2, recs[0].get("rc"))

    def test_timeout_kills_the_whole_tree(self):
        """只杀直接子进程（不杀树）时，孙子进程会在超时后继续跑并落盘 —— 红。"""
        marker = os.path.join(self.tmp, "grandchild.txt")
        rc, out = self._run("t_gate", "--all", GUARDRAIL_GATE_TIMEOUT=1, SLOW_SLEEP=30,
                            SLOW_CHILD_MARKER=marker,
                            cfg=self._cfg(script="checks/slow_gate.py"))
        self.assertEqual(2, rc, out[-1500:])
        time.sleep(4)   # 孙进程若活着，2s 后就写了标记
        self.assertFalse(os.path.exists(marker),
                         "超时只杀了直接子进程：孙子进程仍在跑并落盘（%s）" % marker)

    def test_passing_gate_leaves_no_timeout_fields(self):
        """正向对照：判据不是「台账里只要出现 timed_out 就红」。"""
        rc, out = self._run("t_gate", "--all", GUARDRAIL_GATE_TIMEOUT=60)
        self.assertEqual(0, rc, out[-1500:])
        rec = self._records()[-1]
        self.assertNotIn("timed_out", rec, "没超时的判定被记成超时：%r" % rec)
        self.assertNotIn("timeout_s", rec)

    def test_legacy_env_name_still_works(self):
        rc, out = self._run("t_gate", "--all", AGENTGUARD_GATE_TIMEOUT=1, SLOW_SLEEP=6,
                            cfg=self._cfg(script="checks/slow_gate.py"))
        self.assertEqual(2, rc, "旧名 AGENTGUARD_GATE_TIMEOUT 必须照样生效（迁移期双认）：\n%s"
                         % out[-1500:])


@_slow("建工程副本（~2s）")
class TestTimeoutConfigErrors(_Base):
    """② 配错的时限是配置错（rc=2），且**不执行** gate。"""

    def test_non_integer_is_config_error(self):
        rc, out = self._run("t_gate", "--all", GUARDRAIL_GATE_TIMEOUT="abc")
        self.assertEqual(2, rc, "非整数时限必须 rc=2：\n%s" % out[-1500:])
        self.assertIn("不是整数", out)

    def test_non_positive_is_config_error(self):
        for bad in ("0", "-5"):
            rc, out = self._run("t_gate", "--all", GUARDRAIL_GATE_TIMEOUT=bad)
            self.assertEqual(2, rc, "时限 %s 必须 rc=2：\n%s" % (bad, out[-1500:]))
            self.assertIn("正整数", out)
        self.assertEqual([], self._records(), "配置错不落台账（判都没判）")

    def test_bad_env_does_not_run_the_gate(self):
        """判据不是"反正 rc=2"：gate 脚本一次都不许被启动。"""
        started = os.path.join(self.tmp, "started.txt")
        rc, out = self._run("t_gate", "--all", GUARDRAIL_GATE_TIMEOUT="oops",
                            SLOW_SLEEP=1, SLOW_START_MARKER=started,
                            cfg=self._cfg(script="checks/slow_gate.py"))
        self.assertEqual(2, rc, out[-1500:])
        self.assertFalse(os.path.exists(started), "时限配错时仍启动了 gate 子进程")


@_slow("建工程副本 + 起真引擎（~8s）")
class TestAgentsCheckIsScopedToTargetRepo(_Base):
    """③ P1-2①：提交期只查本次目标仓；舰队级扫描归 `--validate`。"""

    def setUp(self):
        super().setUp()
        self.other = os.path.join(self.tmp, "other")
        os.makedirs(self.other)
        with io.open(os.path.join(self.other, "AGENTS.md"), "w", encoding="utf-8") as f:
            f.write("python D:/work/agentguard/harness/agents/auto-gate.py\n")

    def _cfg_with_other(self):
        return self._cfg(extra_repos="other = \"%s\"\n" % self.other.replace("\\", "/"))

    def test_other_repo_violation_does_not_block_this_commit(self):
        rc, out = self._run("t_gate", "--all", cfg=self._cfg_with_other())
        self.assertEqual(0, rc, "别的仓 AGENTS.md 有绝对路径，却拦了本次提交：\n%s" % out[-1500:])

    def test_validate_still_scans_the_fleet(self):
        """对照：同一份配置，舰队级体检必须把那个仓抓出来（检查没被挪丢）。"""
        rc, out = self._run("--validate", cfg=self._cfg_with_other())
        self.assertEqual(1, rc, "舰队级体检没抓到违规 AGENTS.md：\n%s" % out[-2000:])
        self.assertIn("含绝对路径", out)

    def test_structure_only_skips_fleet_check(self):
        """CI 的端仓可能根本不在机器上 —— `--structure-only` 明确跳过舰队级扫描。"""
        rc, out = self._run("--validate", "--structure-only", cfg=self._cfg_with_other())
        self.assertEqual(0, rc, "--structure-only 下不该跑舰队级 AGENTS.md 检查：\n%s" % out[-2000:])


if __name__ == "__main__":
    unittest.main()
