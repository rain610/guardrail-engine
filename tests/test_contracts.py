# -*- coding: utf-8 -*-
import pathlib
"""钩子**契约指纹**的回归锁（Q3，2026-09-13 外部评审 v3 建议落地）。

两层：
  1. 纯函数层（快）：`compare()` 的三态语义证伪 —— ok / drift / version_changed / no_baseline；
  2. 实跑层（慢，@_slow）：用密闭夹具把 7 家客户端各跑一遍，与 baseline 比对 ——
     契约一旦漂移（客户端升级、或我们自己改了分支输出）立刻红。
     改了契约是**有意**的？跑 `python sandbox/check_contracts.py --all --accept` 更新基线再提交。
"""
import importlib.util
import json
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, f"快速模式（HARNESS_FAST=1）跳过{reason}")


def _load():
    p = os.path.join(ROOT, "sandbox", "check_contracts.py")
    spec = importlib.util.spec_from_file_location("check_contracts", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


CC = _load()
BASE = os.path.join(ROOT, "tests", "contracts", "baseline")


class TestContractCompare(unittest.TestCase):
    """三态比对逻辑（证伪：故意造漂移/版本变化，必须分别判 drift / version_changed）。"""

    FRESH = {"PostToolUse": {"rc": 2, "stdout": {"kind": "empty"}, "stderr": {"kind": "text"}},
             "Stop": {"rc": 2, "stdout": {"kind": "empty"}, "stderr": {"kind": "text"}},
             "PreToolUse": {"rc": 2, "stdout": {"kind": "empty"}, "stderr": {"kind": "text"}}}

    def _baseline(self, version=None):
        """默认用**当前真实探测到的版本** —— F4 修好后 _version_of 能取到真版本（如 2.1.269），
        基线里写 "unknown" 会被判成"版本变了"而走 ⚠ 软判定，测不到 drift。"""
        if version is None:
            version = CC._version_of("claude")
        return {"client": "claude", "client_version": version, **self.FRESH}

    def test_same_fingerprint_is_ok(self):
        lvl, _ = CC.compare("claude", self.FRESH, self._baseline())
        self.assertEqual(lvl, "ok")

    def test_drift_detected_when_version_unchanged(self):
        drifted = json.loads(json.dumps(self.FRESH))
        drifted["PostToolUse"]["stdout"] = {"kind": "json", "json_keys": ["hookSpecificOutput"]}
        lvl, notes = CC.compare("claude", drifted, self._baseline())
        self.assertEqual(lvl, "drift", notes)
        self.assertTrue(any("指纹变化" in n for n in notes))

    def test_version_changed_is_soft(self):
        drifted = json.loads(json.dumps(self.FRESH))
        drifted["Stop"]["rc"] = 0
        lvl, notes = CC.compare("qoder", drifted, self._baseline("1.0.0-old"))
        # qoder 本机版本可探测（1.1.51）→ 与基线写死的老版本不同 → 走"版本变了"的软判定
        self.assertIn(lvl, ("version_changed", "drift"))
        if lvl == "version_changed":
            self.assertTrue(any("版本" in n for n in notes))

    def test_no_baseline_is_prompt_not_failure(self):
        lvl, _ = CC.compare("claude", self.FRESH, None)
        self.assertEqual(lvl, "no_baseline")

    def test_event_key_added_is_drift(self):
        """新增/改名输出键也算漂移（客户端靠键名取值，少一个键就静默失效）。"""
        b = self._baseline()
        fresh = json.loads(json.dumps(self.FRESH))
        fresh["PreToolUse"]["stdout"] = {"kind": "json", "json_keys": ["hookSpecificOutput",
                                                                     "hookSpecificOutput.permissionDecision"]}
        lvl, _ = CC.compare("claude", fresh, b)
        self.assertEqual(lvl, "drift")



class TestAcceptBaselineGates(unittest.TestCase):
    """F3（评审 v4）：接受基线必须有闸 —— 现状曾是"看到红就更新基线"，连采集失败都能写进去。"""

    FP = {"rc": 2, "stdout": {"kind": "empty"}, "stderr": {"kind": "text"}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # 显式清理：不清理会在 GC 期抛 ResourceWarning（被测的 CI 严格句柄门槛会红）
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.join(self.tmp.name, "baseline")
        os.makedirs(self.base)
        self.fresh = {ev: dict(self.FP) for ev in CC.EVENTS}

    def tearDown(self):
        self.tmp.cleanup()

    def _acc(self, **kw):
        kw.setdefault("client", "claude")
        kw.setdefault("fresh", self.fresh)
        kw.setdefault("baseline", None)
        kw.setdefault("reason", "人工确认契约形态正确，接受本次基线")
        kw.setdefault("force", True)
        kw.setdefault("base_dir", self.base)
        return CC.accept_baseline(**kw)

    def _hist(self):
        p = os.path.join(os.path.dirname(self.base), "HISTORY.jsonl")
        return pathlib.Path(p).read_text(encoding="utf-8").splitlines() if os.path.isfile(p) else []

    def test_rejects_in_ci_or_noninteractive(self):
        ok, _, msg = self._acc(ci=True)
        self.assertFalse(ok, msg)
        ok, _, msg = self._acc(interactive=False)
        self.assertFalse(ok, msg)
        self.assertEqual(self._hist(), [], "被拒时不该留痕")

    def test_rejects_short_reason(self):
        ok, _, msg = self._acc(reason="太短")
        self.assertFalse(ok, msg)

    def test_rejects_capture_error(self):
        """采集失败（error/事件不全）绝不能被当成新契约写进基线 —— 这是最危险的一条。"""
        ok, _, msg = self._acc(fresh={"error": "钩子没写 trace"})
        self.assertFalse(ok, msg)
        ok, _, msg = self._acc(fresh={"PostToolUse": dict(self.FP)})   # 事件不全
        self.assertFalse(ok, msg)

    def test_rejects_drift_without_force(self):
        drifted = {"client": "claude", "client_version": CC._version_of("claude"),
                   **{ev: {"rc": 9, "stdout": {"kind": "x"}} for ev in CC.EVENTS}}
        ok, _, msg = self._acc(baseline=drifted, force=False)
        self.assertFalse(ok, msg)

    def test_accepts_and_leaves_audit_trail(self):
        ok, lvl, msg = self._acc()
        self.assertTrue(ok, msg)
        self.assertEqual(lvl, "accepted")
        with open(os.path.join(self.base, "claude.json"), encoding="utf-8") as fh:
            payload = json.load(fh)
        for field in ("accepted_at", "accepted_reason", "from_version", "to_version"):
            self.assertIn(field, payload, "基线缺审计字段 %s" % field)
        self.assertEqual(len(self._hist()), 1, "HISTORY.jsonl 应有留痕")

@_slow("契约指纹实跑（7 家 × 3 事件，约 30s）")
class TestContractFingerprintsLive(unittest.TestCase):
    """7 家客户的契约指纹必须与基线一致（漂移即红）。"""

    def test_all_baselines_exist(self):
        missing = [c for c in CC.CLIENTS if not os.path.isfile(os.path.join(BASE, "%s.json" % c))]
        self.assertEqual(missing, [], "缺契约基线：%s（跑 sandbox/check_contracts.py --all --accept 采一次）" % missing)

    def test_baseline_shape_is_structural_only(self):
        """基线里**不许**出现消息正文（只比结构）——否则指纹会随文案抖动而假红。"""
        for c in CC.CLIENTS:
            p = os.path.join(BASE, "%s.json" % c)
            if not os.path.isfile(p):
                continue
            raw = pathlib.Path(p).read_text(encoding="utf-8")
            self.assertNotIn("门禁未通过", raw, "%s 基线里混进了消息正文" % c)
            self.assertNotIn("harness-ok", raw, "%s 基线里混进了提示文案" % c)

    def test_live_fingerprints_match_baseline(self):
        bad = []
        for c in CC.CLIENTS:
            fresh = CC.capture(c)
            base = json.loads(pathlib.Path(os.path.join(BASE, "%s.json" % c)).read_text(encoding="utf-8"))
            lvl, notes = CC.compare(c, fresh, base)
            if lvl == "drift":
                bad.append("%s: %s" % (c, "; ".join(notes)[:200]))
            elif lvl == "no_baseline":
                bad.append("%s: 无基线" % c)
        self.assertEqual(bad, [], "契约漂移（若是有意改动，跑 check_contracts.py --all --accept 更新基线）：%s" % bad)
