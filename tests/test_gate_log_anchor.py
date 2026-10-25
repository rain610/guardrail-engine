# -*- coding: utf-8 -*-
"""台账篡改可发现三件套测试（2026-09-29）：月度分片 / 行内链哈希 / 锚定与校验。

对应实现（**三份读侧判据必须逐字同源**，本文件就是那个对拍锁）：
- harness/run_gate.py   _log_file / _ledger_files / _chain_h / _log_run（写侧 + --summary）
- guardrail_engine/cli.py  _ledger_paths / _read_ledger / _cmd_anchor / _cmd_verify（读侧 + 锚）
- harness/coverage.py   ledger_files / _anchor_probe / --quick（引擎侧只读锚点）

密闭口径（与既有 tests 同款）：HARNESS_LOG_FILE / HARNESS_LOG_DIR / HARNESS_ANCHOR_FILE
三个 env 全部指进 tempfile.mkdtemp 的夹具目录 —— 一行都不落进真实 reports/。
夹具生成后保留（本文件不调用任何删除类 API，与 test_agentguard.py 同一约定）。
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_GATE = os.path.join(ROOT, "harness", "run_gate.py")
CLI = os.path.join(ROOT, "guardrail_engine", "cli.py")
COV = os.path.join(ROOT, "harness", "coverage.py")
ENV_KEYS = ("HARNESS_LOG_FILE", "HARNESS_LOG_DIR", "HARNESS_ANCHOR_FILE")
PY = sys.executable


def _load(path, name):
    """按路径加载模块（tests/test_preflight.py 对 cli.py 的同款手法）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Sealed(unittest.TestCase):
    """env 密闭基类：进/出都把三个 env 还原 —— 测试绝不污染真实台账。"""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self._saved = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["HARNESS_LOG_DIR"] = self.td

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _sealed(self):
        """密闭模式：HARNESS_LOG_FILE 指进夹具（行为与旧版单文件逐字一致）。"""
        log = os.path.join(self.td, "gate_log.jsonl")
        os.environ["HARNESS_LOG_FILE"] = log
        os.environ["HARNESS_ANCHOR_FILE"] = os.path.join(self.td, "anchor.jsonl")
        return log

    def _cli(self, *args):
        env = {k: os.environ[k] for k in ENV_KEYS if os.environ.get(k)}
        e = dict(os.environ, PYTHONUTF8="1", **env)
        return subprocess.run([PY, CLI] + list(args), cwd=ROOT, env=e,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")

    @staticmethod
    def _recs(path):
        with open(path, encoding="utf-8") as f:
            return [json.loads(l) for l in f.read().splitlines() if l.strip()]

    def _rewrite(self, path, lines):
        """夹具重写（篡改可发现测试的「动手脚」步骤）；只写不删，与全文件约定一致。"""
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


class TestChainWrite(_Sealed):
    """写侧：行内链相接、片首 sv、篡改/删除/截断全部可点名。"""

    def test_chain_links_and_first_line_sv(self):
        log = self._sealed()
        m = _load(RUN_GATE, "rg_chain")
        m._log_run("t_pc", "test", 1, 0, 5)
        m._log_run("t_h5", "test", 2, 1, 7)
        recs = self._recs(log)
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0].get("sv"), 1)              # 片首行自描述 schema
        self.assertNotIn("sv", recs[1])                     # 只有片首行带
        self.assertTrue(recs[0].get("h"))
        self.assertEqual(recs[1]["h"], m._chain_h(recs[0]["h"], recs[1]))
        # 跨文件契约：壳侧同公式实现对同一批记录必须给出同一个 h（漂移即红）
        c = _load(CLI, "cli_chain")
        self.assertEqual(c._chain_h(recs[0]["h"], recs[1]), recs[1]["h"])

    def test_sealed_mode_verbatim(self):
        log = self._sealed()
        m = _load(RUN_GATE, "rg_sealed")
        self.assertEqual(m._log_file(), log)                # env 密闭 → 逐字用，不分片
        self.assertEqual(m._ledger_files(), [log])

    def test_tamper_breaks_chain_at_first_bad_line(self):
        log = self._sealed()
        m = _load(RUN_GATE, "rg_tamper")
        m._log_run("t_pc", "test", 1, 0, 5)
        m._log_run("t_h5", "test", 2, 0, 6)
        self.assertEqual(self._cli("log", "--anchor").returncode, 0)
        self.assertEqual(self._cli("log", "--verify").returncode, 0)
        with open(log, encoding="utf-8") as f:
            lines = f.read().splitlines()
        rec = json.loads(lines[0])
        rec["rc"] = 9                                       # 原地改第一行（不动 h）
        lines[0] = json.dumps(rec, ensure_ascii=False)
        self._rewrite(log, lines)
        r = self._cli("log", "--verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("链断裂", r.stdout)
        self.assertIn("第 1 行", r.stdout)

    def test_delete_middle_line_breaks_next(self):
        log = self._sealed()
        m = _load(RUN_GATE, "rg_delete")
        for i in range(3):
            m._log_run("t_pc", "test", i, 0, 5)
        with open(log, encoding="utf-8") as f:
            lines = f.read().splitlines()
        del lines[1]                                        # 删中间一行 → 后继 h 全对不上
        self._rewrite(log, lines)
        r = self._cli("log", "--verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("链断裂", r.stdout)
        self.assertIn("第 2 行", r.stdout)                   # 断点 = 被删行的后继（新序）

    def test_truncation_detected_by_anchor(self):
        log = self._sealed()
        m = _load(RUN_GATE, "rg_trunc")
        m._log_run("t_pc", "test", 1, 0, 5)
        m._log_run("t_h5", "test", 2, 0, 6)
        self.assertEqual(self._cli("log", "--anchor").returncode, 0)
        with open(log, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self._rewrite(log, lines[:1])                       # 截掉锚定段尾部
        r = self._cli("log", "--verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("截断", r.stdout)

    def test_unanchored_tail_reported(self):
        log = self._sealed()
        m = _load(RUN_GATE, "rg_tail")
        m._log_run("t_pc", "test", 1, 0, 5)
        m._log_run("t_h5", "test", 2, 0, 6)
        self.assertEqual(self._cli("log", "--anchor").returncode, 0)
        m._log_run("t_be", "test", 1, 0, 5)                 # 锚点之后新增
        r = self._cli("log", "--verify")
        self.assertEqual(r.returncode, 0)
        self.assertIn("未锚定新增", r.stdout)

    def test_verify_legacy_lines_says_build_chain(self):
        log = self._sealed()
        self._rewrite(log, ['{"ts": "2026-07-01T00:00:00", "gate": "t_pc", "rc": 0}'])
        r = self._cli("log", "--verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("旧格式", r.stdout)

    def test_bad_lines_change_reported(self):
        log = self._sealed()
        m = _load(RUN_GATE, "rg_bad")
        m._log_run("t_pc", "test", 1, 0, 5)
        self.assertEqual(self._cli("log", "--anchor").returncode, 0)
        with open(log, "a", encoding="utf-8") as f:
            f.write("这不是JSON\n")                          # 锚后有坏行：n 同、坏行数变
        r = self._cli("log", "--verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("坏行数变化", r.stdout)


class TestSharding(_Sealed):
    """分片模式（不设 HARNESS_LOG_FILE）：当月片写入 / 跨片不断链 / 三读者同判据。"""

    def test_monthly_shard_write_and_three_readers(self):
        m = _load(RUN_GATE, "rg_shard")
        m._log_run("t_pc", "test", 1, 0, 5)
        cur = os.path.join(self.td, "gate_log-%s.jsonl" % time.strftime("%Y-%m"))
        self.assertTrue(os.path.exists(cur))
        recs = self._recs(cur)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].get("sv"), 1)
        self.assertEqual(m._ledger_files(), [cur])
        self.assertEqual(_load(CLI, "cli_shard")._ledger_paths(), [cur])     # 壳侧同判据
        self.assertEqual(_load(COV, "cov_shard").ledger_files(), [cur])      # coverage 同判据

    def test_cross_shard_chain(self):
        m = _load(RUN_GATE, "rg_xshard")
        prev_path = os.path.join(self.td, "gate_log-2026-08.jsonl")
        r1 = {"ts": "2026-08-01T10:00:00", "gate": "t_pc", "scope": "test",
              "files": 1, "rc": 0, "ms": 5}
        r1["h"] = m._chain_h("", r1)
        with open(prev_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(r1, ensure_ascii=False) + "\n")
        m._log_run("t_pc", "test", 1, 0, 5)                 # 当月真实写入
        cur = os.path.join(self.td, "gate_log-%s.jsonl" % time.strftime("%Y-%m"))
        recs = self._recs(cur)
        self.assertEqual(recs[0]["h"], m._chain_h(r1["h"], recs[0]))   # 跨片接上一片链尾
        self.assertEqual(m._chain_head(), recs[0]["h"])
        self.assertEqual(m._ledger_files(), [prev_path, cur])

    def test_legacy_file_is_earliest_shard(self):
        m = _load(RUN_GATE, "rg_legacy")
        legacy = os.path.join(self.td, "gate_log.jsonl")
        with open(legacy, "w", encoding="utf-8") as f:
            f.write('{"ts": "2026-07-01T00:00:00", "gate": "t_pc", "rc": 0}\n')
        m._log_run("t_h5", "test", 1, 0, 5)
        cur = os.path.join(self.td, "gate_log-%s.jsonl" % time.strftime("%Y-%m"))
        self.assertEqual(m._ledger_files(), [legacy, cur])  # 旧单文件排最前
        recs = self._recs(cur)
        self.assertEqual(recs[0]["h"], m._chain_h("", recs[0]))        # 旧片无 h → 链从当月起头

    def test_summary_reads_all_shards(self):
        m = _load(RUN_GATE, "rg_sum")
        legacy = os.path.join(self.td, "gate_log.jsonl")
        with open(legacy, "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-07-01T00:00:00", "gate": "t_pc", "scope": "test",
                                "files": 1, "rc": 0, "ms": 5}, ensure_ascii=False) + "\n")
        m._log_run("t_h5", "test", 1, 0, 5)
        e = dict(os.environ, PYTHONUTF8="1", HARNESS_LOG_DIR=self.td)
        e.pop("HARNESS_LOG_FILE", None)
        p = subprocess.run([PY, RUN_GATE, "--summary"], cwd=ROOT, env=e,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("t_pc", p.stdout)                     # 旧片与当月片都被 --summary 看到
        self.assertIn("t_h5", p.stdout)


class TestAnchorContract(_Sealed):
    """锚定/校验往返 + 跨文件契约（last 语义 / rollup 形状 / coverage --quick 消费口）。"""

    def test_read_ledger_last_across_shards(self):
        m = _load(RUN_GATE, "rg_last")
        legacy = os.path.join(self.td, "gate_log.jsonl")
        with open(legacy, "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-07-01T00:00:00", "gate": "t_pc", "rc": 0},
                               ensure_ascii=False) + "\n")
        m._log_run("t_h5", "test", 1, 0, 5)
        c = _load(CLI, "cli_last")
        recs, bad = c._read_ledger(1)
        self.assertEqual(bad, 0)
        self.assertEqual([r["gate"] for r in recs], ["t_h5"])          # 跨片取尾 1 条
        recs2, _ = c._read_ledger(None)
        self.assertEqual([r["gate"] for r in recs2], ["t_pc", "t_h5"])  # 全集按时间序

    def test_read_ledger_last_zero_is_empty(self):
        c = _load(CLI, "cli_last0")
        self.assertEqual(c._read_ledger(0), ([], 0))        # L10 判据：「写 0」就是 0 条

    def test_rollup_shapes(self):
        c = _load(CLI, "cli_roll")
        recs = [
            {"ts": "t1", "gate": "a_pc", "rc": 0, "rules": {"R1": 2, "R2": {"error": 1}}},
            {"ts": "t2", "gate": "b_h5", "rc": 1, "weakened": ["local_disabled"]},
            {"ts": "t3", "gate": "a_pc", "rc": 0, "skipped": True, "rules": ["R3"]},
        ]
        roll = c._rollup(recs, 1)                            # 自第 1 条以来（rec2 无 rules 字段）
        self.assertEqual(roll["n"], 2)
        self.assertEqual(roll["rc"], {"1": 1, "0": 1})
        self.assertEqual(roll["weakened"], 1)
        self.assertEqual(roll["skipped"], 1)
        self.assertEqual(roll["rules_fired"], {"R3": 1})
        full = c._rollup(recs, 0)
        self.assertEqual(full["rules_fired"]["R1"], 2)       # dict 计数形状
        self.assertEqual(full["rules_fired"]["R2"], 1)       # dict-of-dict 形状
        self.assertEqual(full["rules_fired"]["R3"], 1)       # list 形状

    def test_anchor_verify_roundtrip_with_unanchored_tail(self):
        log = self._sealed()
        m = _load(RUN_GATE, "rg_round")
        m._log_run("t_pc", "test", 1, 0, 5)
        m._log_run("t_h5", "test", 2, 0, 6)
        r = self._cli("log", "--anchor")
        self.assertEqual(r.returncode, 0, r.stderr)
        a = self._recs(os.environ["HARNESS_ANCHOR_FILE"])[0]
        self.assertEqual(a["n"], 2)
        self.assertEqual(a["rollup"]["rc"], {"0": 2})
        self.assertEqual(a["rollup"]["gates"], {"t_pc": 1, "t_h5": 1})
        self.assertIsNone(a["prev"])
        r = self._cli("log", "--verify")
        self.assertEqual(r.returncode, 0)
        self.assertIn("吻合", r.stdout)
        m._log_run("t_be", "test", 1, 0, 5)
        r = self._cli("log", "--verify")
        self.assertEqual(r.returncode, 0)
        self.assertIn("未锚定新增", r.stdout)

    def test_second_anchor_chains_prev(self):
        log = self._sealed()
        m = _load(RUN_GATE, "rg_anchor2")
        m._log_run("t_pc", "test", 1, 0, 5)
        self.assertEqual(self._cli("log", "--anchor").returncode, 0)
        m._log_run("t_h5", "test", 1, 0, 5)
        self.assertEqual(self._cli("log", "--anchor").returncode, 0)
        a1, a2 = self._recs(os.environ["HARNESS_ANCHOR_FILE"])
        self.assertEqual(a2["prev"]["n"], a1["n"])           # 锚点成链：接上一锚
        self.assertEqual(a2["prev"]["tail_h"], a1["tail_h"])
        self.assertEqual(a2["rollup"]["from_n"], a1["n"])
        self.assertEqual(a2["rollup"]["gates"], {"t_h5": 1})  # 增量窗口只含新片
        self.assertEqual(self._cli("log", "--verify").returncode, 0)

    def test_coverage_quick_reads_anchor(self):
        log = self._sealed()
        m = _load(RUN_GATE, "rg_quick")
        m._log_run("t_pc", "test", 1, 0, 5)
        self.assertEqual(self._cli("log", "--anchor").returncode, 0)
        e = dict(os.environ, PYTHONUTF8="1",
                 HARNESS_ANCHOR_FILE=os.environ["HARNESS_ANCHOR_FILE"])
        p = subprocess.run([PY, COV, "--quick"], cwd=ROOT, env=e, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("锚点", p.stdout)
        self.assertIn("盲区候选", p.stdout)


class TestAnchorCorruptTail(_Sealed):
    """锚点尾行**解析不了** ≠ 没有锚点 —— 三态必须分开（2026-09-30 修）。

    原缺陷（本地实测复现过）：读侧把「文件不在 / 空文件」与「尾行不是 JSON」合并成同一个
    `None`，于是**往锚点文件尾部追加一个字节**，`gate log --verify` 就退回"尚无锚点"并
    rc=0。后果不是"少一条提示"，而是**锚点这半边防线的静默消失**：保留链合法的前缀、
    砍掉台账尾巴、再把锚点尾行弄坏 → 报绿。而"抓截断/重写"正是锚点唯一的存在理由。

    本类同时锁**两个方向**：`corrupt` 必须红（上面），`absent` 必须仍然绿（下面
    `test_absent_or_empty_anchor_still_green`）—— 只锁前者会诱发"一律报红"的过度矫正，
    那会让第一次锚定前的正常工作流变成红灯。
    """

    _GARBAGE = "{ 这不是 JSON\n"

    def _anchor_then(self):
        """两条记录 + 一次锚定（返回 (log, anchor) 路径）；调用方在此之上动手脚。"""
        log = self._sealed()
        m = _load(RUN_GATE, "rg_corrupt")
        m._log_run("t_pc", "test", 1, 0, 5)
        m._log_run("t_h5", "test", 2, 0, 6)
        self.assertEqual(self._cli("log", "--anchor").returncode, 0)
        self.assertEqual(self._cli("log", "--verify").returncode, 0)   # 动手脚前必须是绿的
        return log, os.environ["HARNESS_ANCHOR_FILE"]

    def _corrupt_tail(self, anchor, text=None):
        with open(anchor, "a", encoding="utf-8") as f:
            f.write(text or self._GARBAGE)

    def test_corrupt_anchor_tail_is_red_and_named(self):
        """只弄坏锚点尾行（台账一字不动）→ 必须 rc=1，且理由写成"锚点解析不了"。"""
        _log, anchor = self._anchor_then()
        self._corrupt_tail(anchor)
        r = self._cli("log", "--verify")
        self.assertEqual(r.returncode, 1,
                         "锚点尾行坏了还报绿 = 这半边防线静默消失\n%s" % r.stdout)
        self.assertIn("无法解析", r.stdout)
        self.assertNotIn("尚无锚点", r.stdout, "把「读不到」说成「还没锚」正是原缺陷的措辞")

    def test_truncation_hidden_behind_corrupt_anchor_is_still_red(self):
        """★ 原缺陷的完整利用链（本类的主锁）：截断 + 坏锚点，改前 **rc=0 且报"尚无锚点"**。

        前缀保留的截断单靠行内链看不出来（每一行自己都还是合法的），只有锚点的
        `n` / `set_sha256` 能发现 —— 所以弄坏锚点就等于把这道防线整条摘掉。
        """
        log, anchor = self._anchor_then()
        with open(log, encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        self._rewrite(log, lines[:1])          # 前缀保留：链本身依然自洽（见下断言）
        r = self._cli("log", "--verify")
        self.assertEqual(r.returncode, 1, "锚点完好时本就该红（对照）\n%s" % r.stdout)
        self.assertIn("截断", r.stdout)
        self._corrupt_tail(anchor)              # ← 唯一的差别：再多一个字节
        r = self._cli("log", "--verify")
        self.assertEqual(r.returncode, 1,
                         "一个垃圾字节就让截断重新变成绿 —— 锚点防线可绕过\n%s" % r.stdout)
        self.assertNotIn("尚无锚点", r.stdout)
        self.assertIn("无法解析", r.stdout)

    def test_verify_does_not_trust_chain_alone_when_anchor_corrupt(self):
        """坏锚点必须**明说"这半边没保住"**，不许拿"链完整"顶替整句结论。"""
        _log, anchor = self._anchor_then()
        self._corrupt_tail(anchor)
        r = self._cli("log", "--verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("失效", r.stdout)

    def test_anchor_refuses_to_append_on_corrupt_tail(self):
        """尾行坏了还往上接 → 新锚点的 `prev` 会被记成"无前锚"，断链无声 ⇒ 应当拒写。"""
        _log, anchor = self._anchor_then()
        self._corrupt_tail(anchor)
        # 尾行已是垃圾，`_recs`（逐行 json.loads）在这里用不了 —— 数非空行即可
        before = self._n_lines(anchor)
        r = self._cli("log", "--anchor")
        self.assertEqual(r.returncode, 1, "坏尾行之上仍追加了新锚点\n%s" % r.stdout)
        self.assertEqual(self._n_lines(anchor), before, "锚点文件被写进去了")
        self.assertIn("无法解析", r.stdout)

    @staticmethod
    def _n_lines(path):
        with open(path, encoding="utf-8") as f:
            return len([l for l in f.read().splitlines() if l.strip()])

    def test_absent_or_empty_anchor_still_green(self):
        """反锁：没有锚点 / 空锚点文件 = 「还没锚过」，必须仍然 rc=0 + 「尚无锚点」。

        （改了上面几条之后最容易顺手把这里也改成红的 —— 那会让"首次锚定前"的正常
        工作流报错，属过度矫正。）
        """
        self._sealed()
        m = _load(RUN_GATE, "rg_absent")
        m._log_run("t_pc", "test", 1, 0, 5)
        r = self._cli("log", "--verify")            # ① 锚点文件根本不存在
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("尚无锚点", r.stdout)
        self.assertIn("链完整", r.stdout)
        with open(os.environ["HARNESS_ANCHOR_FILE"], "w", encoding="utf-8"):     # ② 空文件
            pass
        r = self._cli("log", "--verify")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("尚无锚点", r.stdout)

    def test_coverage_quick_distinguishes_corrupt_from_absent(self):
        """`coverage --quick`（另一个读侧副本）同样不许把「损坏」说成「尚无」。

        它不拦人（rc 恒 0），但**报告的价值就在"没响"与"没跑"分得开** —— 把坏锚点
        显示成"尚无锚点"，等于让人以为只是还没锚定。
        """
        self._sealed()
        m = _load(RUN_GATE, "rg_cq")
        m._log_run("t_pc", "test", 1, 0, 5)
        env = dict(os.environ, PYTHONUTF8="1",
                   HARNESS_ANCHOR_FILE=os.environ["HARNESS_ANCHOR_FILE"])

        def quick():
            return subprocess.run([PY, COV, "--quick"], cwd=ROOT, env=env,
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace")

        p = quick()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("尚无锚点", p.stdout)
        self.assertEqual(self._cli("log", "--anchor").returncode, 0)
        self._corrupt_tail(os.environ["HARNESS_ANCHOR_FILE"])
        p = quick()
        self.assertEqual(p.returncode, 0, p.stderr)          # 信息性报告，不拦人
        self.assertNotIn("尚无锚点", p.stdout, "坏锚点被显示成「还没锚」")
        self.assertIn("解析不了", p.stdout)


class TestEngineSha(_Sealed):
    """5.7-A：台账必须记下「这次判定实际用的引擎内容」。

    `ver` 只说明哪一版；瞬时改弱规则再改回来，版本不变而 engine_sha 必须变。
    """

    def test_logged_as_16_hex(self):
        log = self._sealed()
        m = _load(RUN_GATE, "rg_eng_sha")
        m._log_run("t_pc", "test", 1, 0, 5)
        rec = self._recs(log)[0]
        self.assertRegex(rec.get("engine_sha", ""), r"^[0-9a-f]{16}$",
                         "台账没记 engine_sha（或格式不对）：%r" % rec.get("engine_sha"))

    def test_hash_tracks_content(self):
        m = _load(RUN_GATE, "rg_eng_sha2")
        td = tempfile.mkdtemp()
        f = os.path.join(td, "rule.py")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("RULE = 1\n")
        a = m._engine_sha([f])
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("RULE = 2\n")
        b = m._engine_sha([f])
        self.assertNotEqual(a, b, "内容变了哈希不变 —— 抓不住瞬时改弱")

    def test_stable_without_change(self):
        m = _load(RUN_GATE, "rg_eng_sha3")
        self.assertEqual(m._engine_sha(), m._engine_sha(),
                         "同一份引擎两次哈希不一致（台账无法对账）")

    def test_dispatch_answers_even_when_config_is_broken(self):
        """`--engine-sha` 是只读出口：配置读不出来时也要能回答"跑的是哪份引擎"。

        对照组用 `--validate`：同一份坏配置下它 rc=2（证明配置**确实**是坏的），
        而 `--engine-sha` 仍 rc=0 —— 否则 CI 校验尾注会被无关的本地配置问题卡死。
        """
        td = tempfile.mkdtemp()
        bad = os.path.join(td, "broken.toml")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("this is not toml [[[\n")

        def run(*argv):
            return subprocess.run([PY, RUN_GATE] + list(argv),
                                  cwd=ROOT, env=dict(os.environ, PYTHONUTF8="1",
                                                     HARNESS_CONFIG=bad),
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace")

        v = run("--validate")
        self.assertEqual(v.returncode, 2, "对照组失败：这份配置并没有被引擎判成坏配置")
        r = run("--engine-sha")
        self.assertEqual(r.returncode, 0, r.stderr)
        got = r.stdout.strip()
        self.assertRegex(got, r"^[0-9a-f]{16}$")
        self.assertEqual(got, _load(RUN_GATE, "rg_eng_sha4")._engine_sha())


class TestEngineProvenance(_Sealed):
    """`log --verify --engine`：台账 engine_sha 能否归因到**已提交**的引擎内容。

    语义定死在这里，因为最直觉的那种实现是错的：
    - ✗ 「记录 sha == 当前引擎」→ 正常升级、甚至改一行测试锁，全本立刻变红（假的失配）；
    - ✓ 「记录 sha ∈ 提交历史里声明过的 sha」→ 只有"用从未提交的引擎跑出来的判定"会红，
      而"瞬时改弱 → 跑通过 → 改回 → 提交"留下的正是这种记录。

    测试不依赖真实 git 历史：`HARNESS_ENGINE_SHAS` / `HARNESS_ENGINE_SINCE` 是读侧注入口
    （只在测试与离线审计里用，不参与任何拦截判定）。
    """

    def _recs_via_run_gate(self, n=2):
        log = self._sealed()
        m = _load(RUN_GATE, "rg_prov_write")
        for i in range(n):
            m._log_run("t_pc", "test", 1, 0, 5 + i)
        return log, m._engine_sha()

    def _inject(self, shas, since=None):
        # _Sealed.tearDown 只还原那三个台账 env；这两个必须自己收尾，否则会漏进别的用例
        old = {k: os.environ.get(k) for k in ("HARNESS_ENGINE_SHAS", "HARNESS_ENGINE_SINCE")}
        os.environ["HARNESS_ENGINE_SHAS"] = shas
        if since is None:
            os.environ.pop("HARNESS_ENGINE_SINCE", None)
        else:
            os.environ["HARNESS_ENGINE_SINCE"] = since
        for k, v in old.items():
            if v is None:
                self.addCleanup(os.environ.pop, k, None)
            else:
                self.addCleanup(os.environ.__setitem__, k, v)

    def test_known_sha_is_green(self):
        _log, sha = self._recs_via_run_gate()
        self._inject(sha)
        r = self._cli("log", "--verify", "--engine")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("引擎内容核对", r.stdout)
        self.assertIn("都对应已提交的引擎内容", r.stdout)
        self.assertIn(sha, r.stdout)

    def test_uncommitted_engine_is_red_and_named(self):
        """记录里的 sha 不属于任何已声明提交 → rc=1，且点名 gate（可据此重跑）。"""
        _log, sha = self._recs_via_run_gate()
        other = "0123456789abcdef"
        self.assertNotEqual(sha, other)
        self._inject(other)
        r = self._cli("log", "--verify", "--engine")
        self.assertEqual(r.returncode, 1, "未提交引擎的判定被放过了\n" + r.stdout)
        self.assertIn("不属于任何已提交的引擎内容", r.stdout)
        self.assertIn("gate=t_pc", r.stdout)
        self.assertIn(sha, r.stdout)                  # 清单里要能看到实际 sha

    def test_baseline_skips_older_records(self):
        """基线之前的历史不追溯：同一批记录 + 未知 sha，基线设到未来 → 绿且"无可对账"。"""
        _log, _sha = self._recs_via_run_gate()
        self._inject("0123456789abcdef", since="2999-01-01T00:00:00")
        r = self._cli("log", "--verify", "--engine")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("没有可对账的记录", r.stdout)

    def test_records_without_engine_sha_are_reported(self):
        """旧格式记录：不只字不提地略过，也不当作失配 —— 出声说"无法核对"。"""
        log = self._sealed()
        m = _load(RUN_GATE, "rg_prov_legacy")
        r1 = {"ts": "2026-07-01T00:00:00", "gate": "t_pc", "scope": "test",
              "files": 1, "rc": 0, "ms": 5}
        r1["h"] = m._chain_h("", r1)
        r2 = {"ts": "2026-07-01T00:00:01", "gate": "t_h5", "scope": "test",
              "files": 1, "rc": 0, "ms": 6}
        r2["h"] = m._chain_h(r1["h"], r2)
        self._rewrite(log, [json.dumps(r1, ensure_ascii=False),
                            json.dumps(r2, ensure_ascii=False)])
        self._inject("0123456789abcdef")
        r = self._cli("log", "--verify", "--engine")
        self.assertEqual(r.returncode, 0, r.stdout)      # 链是好的；缺字段不等于篡改
        self.assertIn("没有 engine_sha", r.stdout)
        self.assertIn("2 条", r.stdout)

    def test_no_trailer_yet_is_explained_not_failed(self):
        """功能启用前（历史里还没有尾注）：说清楚"不追溯"，rc=0。

        空串注入（而不是删 env）是为了让本用例与真实 git 历史无关：仓库一旦开始写尾注，
        "尚无尾注"就用真实历史测不出来了。
        """
        _log, _sha = self._recs_via_run_gate()
        self._inject("")
        r = self._cli("log", "--verify", "--engine")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("尚无 Engine-Sha 尾注", r.stdout)

    def test_git_history_counts_only_harness_commits(self):
        """尾注只从 `[harness]` 提交里取：普通提交写一行不算"已声明"。

        否则任何人都能把一个从未提交的 sha 塞进集合 —— 这条对账线自己先失效。
        基线 = 最早一条**有效**尾注的时间（中间那条不带标记的不参与）。
        """
        old = os.environ.pop("HARNESS_ENGINE_SHAS", None)
        if old is not None:
            self.addCleanup(os.environ.__setitem__, "HARNESS_ENGINE_SHAS", old)

        td = tempfile.mkdtemp()

        def git(*args):
            r = subprocess.run(["git", "-C", td] + list(args), capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout

        def commit(msg):
            git("-c", "user.name=t", "-c", "user.email=t@example.com",
                "commit", "--allow-empty", "--no-gpg-sign", "-q", "-m", msg)

        git("init", "-q")
        commit("feat: one [harness]\n\nEngine-Sha: 1111111111111111")
        ts1 = int(git("log", "-1", "--format=%ct").strip())
        commit("chore: two\n\nEngine-Sha: 2222222222222222")
        commit("feat: three [harness]\n\nEngine-Sha: 3333333333333333")

        c = _load(CLI, "cli_prov_hist")
        shas, since = c._engine_known_shas(repo=td)
        self.assertEqual(shas, {"1111111111111111", "3333333333333333"})
        self.assertEqual(since, time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts1)))

    def test_not_a_git_repo_returns_empty(self):
        old = os.environ.pop("HARNESS_ENGINE_SHAS", None)
        if old is not None:
            self.addCleanup(os.environ.__setitem__, "HARNESS_ENGINE_SHAS", old)
        c = _load(CLI, "cli_prov_nogit")
        self.assertEqual(c._engine_known_shas(repo=os.path.join(tempfile.mkdtemp(), "nope")),
                         (set(), ""))

    def test_flag_off_prints_no_engine_section(self):
        _log, _sha = self._recs_via_run_gate()
        r = self._cli("log", "--verify")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("引擎内容核对", r.stdout)

    def test_engine_without_verify_is_refused(self):
        """`--engine` 单独用没有意义：静默忽略会让人以为核对过了。"""
        self._recs_via_run_gate()
        r = self._cli("log", "--engine")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("只与 --verify 合用", r.stdout)

    def test_chain_failure_wins_over_engine_check(self):
        """链先红就报链：记录是否原样都还不确定时，谈归因是噪音。"""
        log, sha = self._recs_via_run_gate()
        self._inject(sha)
        with open(log, encoding="utf-8") as f:
            lines = f.read().splitlines()
        rec = json.loads(lines[0])
        rec["rc"] = 9
        lines[0] = json.dumps(rec, ensure_ascii=False)
        self._rewrite(log, lines)
        r = self._cli("log", "--verify", "--engine")
        self.assertEqual(r.returncode, 1)
        self.assertIn("链断裂", r.stdout)
        self.assertNotIn("引擎内容核对", r.stdout)


if __name__ == "__main__":
    unittest.main()
