# -*- coding: utf-8 -*-
"""评审 v13 复查遗留项②：「被允许的削弱」在台账里与「真检查过且通过」不可区分。

缺陷原文（2026-09-16 实测，`reports/_localyaml_probe2.txt`；施工后复测 `reports/_probe_weakened.txt`）
================================================================================
两条**设计上允许**的削弱路径，在 `reports/gate_log.jsonl` 里读起来与一次正常通过没有区别：

  ① 本机把 gate 关掉（`config/harness.local.yaml` 的 `enabled: false`，`_guard_policy` 明确
     允许 —— "裁掉自己用不到的"）：台账只多一个 `skipped: true`，看不出是**本地关的**
     还是"共享配置里本来就关着"；
  ② 调试后门放行策略覆盖（`HARNESS_ALLOW_POLICY_OVERRIDE=1`）：那一行**一个字段都不多**，
     与真通过逐字相同 —— 实测那一行的全部键就是 `gate` / `rc`。

项目自称"出问题先看台账"，而实测整本台账里 `enabled`/`override`/`policy`/`local` 四个关键词
**一个都搜不到**。处置（用户拍板）：削弱事实记进台账（`weakened` 字段）+ 告警去重。

本文件钉住四件事
================================================================================
  ① **两条路径都留痕**：本地关 gate → `weakened: ["local_disabled"]`；放行后门 →
     `weakened: ["policy_override:gates.<gate>.<字段>"]`（真跑 run_gate，读回台账）；
  ② **阴性对照**：干净跑**不写**该字段（每行都挂空标记等于没标记）；
     且**被退回**的覆盖不算削弱（它没生效，写进去就是假话）；
  ③ **只标自己**：别的 gate 的覆盖不会标到这一行头上；
  ④ **信号是「最近一次 load()」**：显式 `load(path=…)` 与「HARNESS_CONFIG 指向的文件不存在」
     这两条早退路径必须清空信号 —— 旧实现留着上一次的答案，而它看起来与真话一模一样。
     外加告警去重（同一进程内、以及 run_gate 起的检查子进程里都只打一遍）。

销路说明：本文件同时是 ④ 的回归锁 —— `_config.load()` 的早退路径回归"残留上次答案"时，
`test_explicit_path_load_clears_stale_signal` 会红。
"""
from __future__ import unicode_literals

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _tmpclean import rmtree_force as _rmtree_force  # noqa: E402

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
HARNESS_LIB = os.path.join(ROOT, "harness")

sys.path.insert(0, TESTS)
sys.path.insert(0, HARNESS_LIB)
from _engine_copy import EngineCopy            # noqa: E402

BASE_YAML = os.path.join(ROOT, "config", "harness.yaml")
GATE = "example"                               # base 里 enabled: true 的示例 gate
# 快跑（HARNESS_FAST=1）：本文件的慢在**链路集成**（现建工程副本 + 真跑 run_gate 子进程），
# 纯逻辑那几类（信号复位 / 标记作用域 / mark 列）是文件读取与函数调用，快跑档照跑。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, f"快速模式（HARNESS_FAST=1）跳过{reason}")


_PROBE_ENV = "HARNESS_POLICY_NOTICE_SHOWN"


class _EnvGuard(unittest.TestCase):
    """按名字保存/还原这几个环境变量。

    去重记账就存在 `HARNESS_POLICY_NOTICE_SHOWN` 里（_config 不再另留一份进程内 set），
    所以复位"已说过的告警"= 把这个变量清掉，本类已经在做。
    """

    NAMES = ("HARNESS_CONFIG", "HARNESS_ALLOW_POLICY_OVERRIDE", _PROBE_ENV)

    def setUp(self):
        import _config
        self._config = _config
        self._old = {k: os.environ.get(k) for k in self.NAMES}
        for k in self.NAMES:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _Engine(unittest.TestCase):
    """一份工程副本 + 一个空端仓（run_gate 需要一个**存在的**仓目录才会走到记账）。"""

    def setUp(self):
        self.eng = EngineCopy()
        self.end = tempfile.mkdtemp(prefix="b8-end-")
        subprocess.run(["git", "-C", self.end, "init", "-q"], capture_output=True, timeout=180)
        self.ledger = os.path.join(self.eng.path, "reports", "gate_log.jsonl")
        self.local = self.eng.p("config/harness.local.yaml")

    def tearDown(self):
        self.eng.cleanup()
        _rmtree_force(self.end)

    def _write_local(self, body):
        with io.open(self.local, "w", encoding="utf-8") as f:
            f.write(body)

    def _run(self, env_extra=None):
        if os.path.exists(self.ledger):
            os.remove(self.ledger)
        env = dict(os.environ)
        for k in ("HARNESS_CONFIG", "HARNESS_ALLOW_POLICY_OVERRIDE", _PROBE_ENV):
            env.pop(k, None)
        # 台账隔离（test_coverage 的源码级锁）：探针记录绝不许写进真实 reports/gate_log.jsonl
        env["HARNESS_LOG_FILE"] = self.ledger
        if env_extra:
            env.update(env_extra)
        r = subprocess.run([sys.executable, os.path.join(self.eng.path, "harness", "run_gate.py"),
                            GATE, "--repo", self.end],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=300, env=env)
        out = (r.stdout or "") + (r.stderr or "")
        rows = []
        if os.path.exists(self.ledger):
            with io.open(self.ledger, encoding="utf-8") as f:
                rows = [json.loads(l) for l in f if l.strip()]
        return r.returncode, out, rows


@_slow("端到端：现建工程副本 + 真跑 run_gate 子进程")
class TestWeakenedRecordedInLedger(_Engine):
    def test_local_disabled_is_marked_as_weakened(self):
        """① 本机把 gate 关掉 → 台账必须说得出"是本地关的"（旧版只有一个 skipped）。"""
        self._write_local("gates:\n  %s:\n    enabled: false\n" % GATE)
        rc, out, rows = self._run()
        self.assertEqual(rc, 0, "本地关 gate 是允许的动作（rc=0），只是必须留痕：\n%s" % out)
        self.assertEqual(len(rows), 1, "跳过也要落一条：%s" % rows)
        row = rows[-1]
        self.assertTrue(row.get("skipped"), "跳过痕迹不能丢：%s" % row)
        self.assertEqual(row.get("weakened"), ["local_disabled"],
                         "本地关掉的 gate 必须能被台账认出来（这正是缺失的那个事实）：%s" % row)
        self.assertEqual(out.count("[config-local]"), 1,
                         "「本机已关闭」的提示每行命令只该出现一次：\n%s" % out)

    def test_policy_override_is_marked_as_weakened(self):
        """① 放行后门 → 台账那一行必须带上"哪条策略被放行了"（旧版与真通过逐字相同）。"""
        self._write_local("gates:\n  %s:\n    mode: loose\n" % GATE)
        rc, out, rows = self._run({"HARNESS_ALLOW_POLICY_OVERRIDE": "1"})
        self.assertEqual(len(rows), 1, "放行后门下的判定同样要落台账：%s\n%s" % (rows, out))
        self.assertEqual(rows[-1].get("weakened"),
                         ["policy_override:gates.%s.mode" % GATE],
                         "放行过策略覆盖的判定必须与真通过可区分：%s" % rows[-1])
        self.assertEqual(out.count("[config-policy] \u26a0 已按"), 1,
                         "同一条放行告警只该打一遍（含 run_gate 起的检查子进程）：\n%s" % out)

    def test_rejected_override_is_not_marked_as_weakened(self):
        """② 阴性对照：覆盖**被退回**（没生效）不算削弱 —— 记进去就是假话。"""
        self._write_local("gates:\n  %s:\n    mode: loose\n" % GATE)
        rc, out, rows = self._run()
        self.assertEqual(len(rows), 1, "%s\n%s" % (rows, out))
        self.assertNotIn("weakened", rows[-1],
                         "被忽略的覆盖没有生效，不得标成削弱：%s" % rows[-1])
        self.assertIn("已忽略", out, "被退回这件事仍要出声")

    def test_clean_run_has_no_weakened_key(self):
        """② 阴性对照：干净跑不写该字段（旧版真通过那行同样只有 gate/rc —— 区别在这里）。"""
        rc, out, rows = self._run()
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(rows), 1, rows)
        self.assertNotIn("weakened", rows[-1], "没被削弱就不许挂标记：%s" % rows[-1])
        self.assertNotIn("skipped", rows[-1], "真跑了就不该带跳过标记：%s" % rows[-1])

    def test_other_gates_override_is_not_marked(self):
        """③ 只标自己：别的 gate 的覆盖不能标到这一行头上（那等于说假话）。"""
        other = "harness_self"
        self._write_local("gates:\n  %s:\n    mode: loose\n" % other)
        rc, out, rows = self._run({"HARNESS_ALLOW_POLICY_OVERRIDE": "1"})
        self.assertEqual(len(rows), 1, "%s\n%s" % (rows, out))
        self.assertNotIn("weakened", rows[-1],
                         "被放行的是 %s，不是 %s，不得标到这一行：%s" % (other, GATE, rows[-1]))


class TestWeakenMarks(_EnvGuard):
    """`run_gate._weaken_marks` 的取值与容错（不走子进程，直接问它）。"""

    def setUp(self):
        super().setUp()
        import run_gate
        self.run_gate = run_gate
        self.tmp = tempfile.mkdtemp(prefix="b8-cfg-")

    def tearDown(self):
        super().tearDown()
        _rmtree_force(self.tmp)

    def _cfg_file(self, body):
        p = os.path.join(self.tmp, "harness.local.yaml")
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p

    def test_local_disabled_returns_mark(self):
        os.environ["HARNESS_CONFIG"] = self._cfg_file(
            "gates:\n  %s:\n    enabled: false\n" % GATE)
        self._config.load()
        self.assertEqual(self.run_gate._weaken_marks(GATE), ["local_disabled"])

    def test_override_scoped_to_its_own_gate(self):
        os.environ["HARNESS_CONFIG"] = self._cfg_file("gates:\n  %s:\n    mode: loose\n" % GATE)
        os.environ["HARNESS_ALLOW_POLICY_OVERRIDE"] = "1"
        self._config.load()
        self.assertEqual(self.run_gate._weaken_marks(GATE),
                         ["policy_override:gates.%s.mode" % GATE])
        self.assertEqual(self.run_gate._weaken_marks("harness_self"), [],
                         "别的 gate 的覆盖不得标到这一行")

    def test_clean_config_returns_empty(self):
        """没有覆盖层（HARNESS_CONFIG 指向不存在的文件）→ 一个标记都没有。

        刻意**不读本机真实的** `config/harness.local.yaml`：那会让结果取决于跑测试的人
        本机配置（v13 反复提的那一类"对着偶然状态断言"）。
        """
        os.environ["HARNESS_CONFIG"] = os.path.join(self.tmp, "nope.yaml")
        self._config.load()
        self.assertEqual(self.run_gate._weaken_marks(GATE), [])

    def test_marks_are_deduplicated(self):
        """同一条事实只出现一次（重复标记会让台账看起来像出了多件事）。"""
        os.environ["HARNESS_CONFIG"] = self._cfg_file(
            "gates:\n  %s:\n    enabled: false\n    mode: loose\n" % GATE)
        os.environ["HARNESS_ALLOW_POLICY_OVERRIDE"] = "1"
        self._config.load()
        marks = self.run_gate._weaken_marks(GATE)
        self.assertEqual(len(marks), len(set(marks)), "标记必须去重：%s" % marks)

    def test_failure_is_swallowed_but_not_silent(self):
        """读不出信号时：判定照走（台账绝不反过来挡门禁），但**不能不出声**。

        第四问（新机制最容易在边界上报平安）：若这里静默返回 []，一次"读不出削弱信号"
        与一次"确实没削弱"在台账与输出里就完全一样了。
        """
        boom = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        orig = self._config.last_local_disabled
        self._config.last_local_disabled = boom
        buf = io.StringIO()
        old = sys.stderr
        self.run_gate._WEAKEN_WARNED.clear()
        try:
            sys.stderr = buf
            first = self.run_gate._weaken_marks(GATE)
            second = self.run_gate._weaken_marks(GATE)      # 第二遍不再刷屏
        finally:
            sys.stderr = old
            self._config.last_local_disabled = orig
            self.run_gate._WEAKEN_WARNED.clear()
        self.assertEqual(first, [], "读不出就当没有（不得反过来说'有削弱'）")
        self.assertEqual(second, [])
        self.assertEqual(buf.getvalue().count("读不出策略削弱信号"), 1,
                         "必须出声，且只说一遍：\n%s" % buf.getvalue())


class TestSignalsAreFreshPerLoad(_EnvGuard):
    """④ 三个信号都是「**最近一次** load() 的结果」——早退路径不得留下上一次的答案。"""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="b8-cfg2-")

    def tearDown(self):
        super().tearDown()
        _rmtree_force(self.tmp)

    def _cfg_file(self, body):
        p = os.path.join(self.tmp, "harness.local.yaml")
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p

    def test_local_disable_visible_after_production_load(self):
        os.environ["HARNESS_CONFIG"] = self._cfg_file("gates:\n  %s:\n    enabled: false\n" % GATE)
        self._config.load()
        self.assertEqual(self._config.last_local_disabled(), [GATE])

    def test_explicit_path_load_clears_stale_signal(self):
        """显式 `load(path=…)` 是早退路径：旧实现**不清信号**，于是它报的是上一次的答案。"""
        os.environ["HARNESS_CONFIG"] = self._cfg_file("gates:\n  %s:\n    enabled: false\n" % GATE)
        self._config.load()
        self.assertEqual(self._config.last_local_disabled(), [GATE], "前提：先造出残留信号")
        self._config.load(BASE_YAML)            # 只读该文件，与本地覆盖层无关
        self.assertEqual(self._config.last_local_disabled(), [],
                         "显式文件加载后，信号必须是这次的结果（不得残留上一次）")

    def test_missing_config_file_clears_stale_signal(self):
        """`HARNESS_CONFIG` 指的文件不存在也是早退路径 —— 同样不许留着上一次的答案。"""
        os.environ["HARNESS_CONFIG"] = self._cfg_file("gates:\n  %s:\n    enabled: false\n" % GATE)
        self._config.load()
        self.assertEqual(self._config.last_local_disabled(), [GATE], "前提：先造出残留信号")
        os.environ["HARNESS_CONFIG"] = os.path.join(self.tmp, "不存在.yaml")
        self._config.load()
        self.assertEqual(self._config.last_local_disabled(), [],
                         "覆盖层文件不存在时信号必须清空（否则报的是上一次的答案）")

    def test_notice_printed_once_per_process(self):
        """去重（同一进程）。3 次 load 实测旧版打 3~4 行，读者于是不再读它。"""
        os.environ["HARNESS_CONFIG"] = self._cfg_file("gates:\n  %s:\n    mode: loose\n" % GATE)
        os.environ["HARNESS_ALLOW_POLICY_OVERRIDE"] = "1"
        buf = io.StringIO()
        old = sys.stderr
        try:
            sys.stderr = buf
            for _ in range(3):
                self._config.load()
        finally:
            sys.stderr = old
        self.assertEqual(buf.getvalue().count("[config-policy] \u26a0 已按"), 1,
                         "同一条告警在一个进程里只该出现一次：\n%s" % buf.getvalue())

    def test_different_overrides_still_each_report(self):
        """去重 ≠ 少报：换了字段就是另一条事实，照样要说。"""
        os.environ["HARNESS_ALLOW_POLICY_OVERRIDE"] = "1"
        buf = io.StringIO()
        old = sys.stderr
        try:
            sys.stderr = buf
            for body in ("gates:\n  %s:\n    mode: loose\n" % GATE,
                         "gates:\n  %s:\n    legacy_baseline: true\n" % GATE):
                os.environ["HARNESS_CONFIG"] = self._cfg_file(body)
                self._config.load()
        finally:
            sys.stderr = old
        self.assertEqual(buf.getvalue().count("[config-policy] \u26a0 已按"), 2,
                         "两条不同的覆盖各报一次：\n%s" % buf.getvalue())


class TestEngineCopyDropsMachineLocalYaml(unittest.TestCase):
    """工程副本不得继承**跑测试那台机器**的 `config/harness.local.yaml`。

    施工中发现（2026-09-17）：`tests/_engine_copy.py` 整棵复制 `config/`，而 `harness.local.yaml`
    恰恰是「本机把某个 gate 关了 / 把 mode 降了」的载体（`_guard_policy` 明确允许本地关 gate）
    —— 于是副本会把**跑测试者的机器状态**带进用例，正是本文件刻意避开的"对着偶然状态断言"。
    用临时工程根来验：源里有 local.yaml，副本里必须没有。
    """

    def test_copy_has_no_inherited_local_yaml(self):
        import _engine_copy
        src_root = tempfile.mkdtemp(prefix="b8-src-")
        try:
            os.makedirs(os.path.join(src_root, "config"))
            with io.open(os.path.join(src_root, "config", "harness.local.yaml"),
                         "w", encoding="utf-8") as f:
                f.write("gates:\n  %s:\n    enabled: false\n" % GATE)
            with io.open(os.path.join(src_root, "config", "harness.yaml"),
                         "w", encoding="utf-8") as f:
                f.write("gates:\n")
            old = _engine_copy.ROOT
            _engine_copy.ROOT = src_root
            try:
                eng = _engine_copy.EngineCopy()
            finally:
                _engine_copy.ROOT = old
            try:
                self.assertFalse(
                    os.path.exists(eng.p("config/harness.local.yaml")),
                    "副本继承了跑测试那台机器的本地覆盖层 → 用例结果会因人而异")
                self.assertTrue(os.path.exists(eng.p("config/harness.yaml")),
                                "只剔 local.yaml，别的配置照常复制")
            finally:
                eng.cleanup()
        finally:
            _rmtree_force(src_root)


class TestSummarySurfacesWeakening(unittest.TestCase):
    """`run_gate --summary` 也是"看台账"的入口：摘要原先只数 rc，跳过与被削弱混在"没拦住"里。

    顺带锁一条健壮性：台账里出现**结构不全**的行（换过 HARNESS_LOG_FILE / 被别的工具追加过）
    时摘要**不许崩** —— 崩了 Python 以 rc=1 退出，而 rc=1 的契约语义是「代码不合规」，
    一个只读报表没资格报出方向相反的结论。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="b8-sum-")
        self.ledger = os.path.join(self.tmp, "gate_log.jsonl")
        # 第 1 行刻意缺 scope/ms（旧实现在"最近一次"那行下标取值 → KeyError → rc=1）
        rows = [{"ts": "2026-09-17T00:00:00", "gate": "a", "rc": 0},
                {"ts": "2026-09-17T00:00:01", "gate": "b", "rc": 0, "skipped": True},
                {"ts": "2026-09-17T00:00:02", "gate": "c", "rc": 0,
                 "weakened": ["local_disabled"]},
                {"ts": "2026-09-17T00:00:03", "gate": "c", "rc": 1,
                 "weakened": ["policy_override:gates.c.mode"]}]
        with io.open(self.ledger, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def tearDown(self):
        _rmtree_force(self.tmp)

    def test_summary_counts_skipped_and_weakened(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "harness", "run_gate.py"),
                            "--summary", "50"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=120, env=dict(os.environ, HARNESS_LOG_FILE=self.ledger))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 0,
                         "只读报表不得以 rc=1（=代码不合规）退出：\n%s" % out)
        self.assertNotIn("Traceback", out)
        self.assertIn("跳过(未执行任何规则) 1 次", out, "跳过次数要单独看得见：\n%s" % out)
        self.assertIn("被削弱 2 次", out, "被削弱次数要单独看得见：\n%s" % out)
        self.assertIn("（c）", out, "要点出是哪个 gate 被削弱：\n%s" % out)


class TestCliMarkColumn(unittest.TestCase):
    """台账读的人只看 `cli.py log` 这张表 —— 字段进了 jsonl 而表里看不见等于没加。

    走**真子进程**（不 import cli）：断言的是人真正看到的那个界面，顺带避开壳层的导入形态耦合。
    三行夹具覆盖三种情形：干净 / 跳过 / 被削弱。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="b8-log-")
        self.ledger = os.path.join(self.tmp, "gate_log.jsonl")
        rows = [{"ts": "2026-09-17T00:00:00", "gate": "a", "rc": 0},
                {"ts": "2026-09-17T00:00:01", "gate": "b", "rc": 0, "skipped": True},
                {"ts": "2026-09-17T00:00:02", "gate": "c", "rc": 0,
                 "weakened": ["local_disabled", "policy_override:gates.c.mode"]}]
        with io.open(self.ledger, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def tearDown(self):
        _rmtree_force(self.tmp)

    def test_table_shows_the_mark_column(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "agentguard", "cli.py"),
                            "log", "--log", self.ledger, "--last", "10"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=120)
        out = (r.stdout or "") + (r.stderr or "")
        self.assertIn("mark", out, "表头要有这一列：\n%s" % out)
        # 批次 3（2026-10-15）：via（来源归因）进表；列序刻意在 mark **之前** ——
        # mark 行尾可见是这条锁自己的设计，别把新列排到它后面（会把下面三条行尾断言全打破）。
        self.assertIn("via", out, "表头要有 via 列（归因随表可见）：\n%s" % out)
        rows = [l for l in out.splitlines() if l.strip().startswith("2026-")]
        self.assertEqual(len(rows), 3, out)
        self.assertTrue(rows[0].rstrip().endswith("-"), "干净行不该有标记：%s" % rows[0])
        self.assertTrue(rows[1].rstrip().endswith("skip"), "跳过行要看得出来：%s" % rows[1])
        self.assertIn("weak:local_disabled", rows[2], "被削弱的行要看得出来：%s" % rows[2])
        self.assertIn("weak:policy_override:gates.c.mode", rows[2], rows[2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
