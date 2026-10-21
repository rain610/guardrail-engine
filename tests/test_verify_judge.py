# -*- coding: utf-8 -*-
import pathlib
"""验证判定器（sandbox/verify_judge.py）的**证伪套件**（Q4，外部评审 v3 建议）。

为什么值得单独测试：这一层的判据改过多次（关键字过窄→放宽；Qoder 只输出最后一句→加行为证据；
上游报错误判→加识别），每次都是人肉回归。这里把"该判 yes / 该判 no / 边界"用人造 fixture 钉死，
判据再改就是受控修改。全部纯函数，无 I/O。
"""
import importlib.util
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    p = os.path.join(ROOT, "sandbox", "verify_judge.py")
    spec = importlib.util.spec_from_file_location("verify_judge", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


J = _load()


def rows(*rcs, gate="demo_pc"):
    return "\n".join(json.dumps({"ts": "2026-09-13T00:00:0%d" % i, "gate": gate, "rc": rc,
                                  "rules": {"PC-CONSOLE": {"error": 1}}} if rc else
                                 {"ts": "2026-09-13T00:00:0%d" % i, "gate": gate, "rc": rc, "rules": {}})
                     for i, rc in enumerate(rcs))


class TestVerifyJudgeFalsification(unittest.TestCase):
    """≥12 条人造 fixture：yes 家族 / no 家族 / 边界。"""

    def _j(self, log_slice, out, hard=True, wrote=True, client="claude"):
        return J.judge(client, hard, wrote, log_slice, out)

    # ── yes 家族 ────────────────────────────────────────────────────────────
    def test_keyword_direct(self):
        r = self._j(rows(1), "…harness 门禁未通过（自动跑于你本次改动之后）…")
        self.assertEqual(r["verdict"], J.PASS_REPLAY)
        self.assertFalse(r["fail"])

    def test_keyword_paraphrased(self):
        """模型转述也算（历史坑：codex 转述成 ❌ 发现 3 个阻断级问题 被判 no）。"""
        r = self._j(rows(1), "我改完了。顺便说，❌ 发现 3 个阻断级问题，已修复。")
        self.assertEqual(r["verdict"], J.PASS_REPLAY)

    def test_keyword_gate_title_pasted(self):
        """贴门禁标题也算（Qoder 实测就是贴了 H5 门禁 [strict] 针对性检查）。"""
        r = self._j(rows(1), "输出如下：\nH5 门禁 [strict] 针对性检查：1 个文件")
        self.assertEqual(r["verdict"], J.PASS_REPLAY)

    def test_behavior_evidence_rc1_then_rc0(self):
        r = self._j(rows(1, 1, 0), "文件已改好。")
        self.assertEqual(r["verdict"], J.PASS_BEHAVIOR)
        self.assertFalse(r["fail"])

    def test_keyword_beats_behavior(self):
        r = self._j(rows(1, 0), "门禁未通过，我已修好")
        self.assertEqual(r["verdict"], J.PASS_REPLAY)

    def test_keyword_beats_upstream_error(self):
        r = self._j(rows(1), "harness 门禁未通过… 另外 Upstream request failed")
        self.assertEqual(r["verdict"], J.PASS_REPLAY)

    # ── no / ⏸ 家族 ────────────────────────────────────────────────────────
    def test_not_logged_in(self):
        r = self._j("", "Not logged in · Please run /login", hard=True)
        self.assertEqual(r["verdict"], J.NOT_LOGGED_IN)
        self.assertFalse(r["fail"], "未登录是账号状态，不该算 harness 失败")

    def test_external_blocked_without_trigger(self):
        r = self._j("", "429 rate limit, Reconnecting…", hard=True)
        self.assertEqual(r["verdict"], J.EXTERNAL_BLOCKED)
        self.assertFalse(r["fail"])

    def test_upstream_error_with_trigger(self):
        r = self._j(rows(1), "invalid_request_error: model not found", hard=True)
        self.assertEqual(r["verdict"], J.UPSTREAM_ERROR)
        self.assertFalse(r["fail"])

    def test_hard_client_not_triggered_fails(self):
        r = self._j("", "文件已创建，没有遇到任何检查", hard=True, wrote=True)
        self.assertEqual(r["verdict"], J.NOT_TRIGGERED)
        self.assertTrue(r["fail"], "强制型没触发必须 FAIL")

    def test_soft_client_not_triggered_is_warning(self):
        r = self._j("", "文件已创建", hard=False, wrote=True)
        self.assertEqual(r["verdict"], J.SOFT_NOT_RUN)
        self.assertFalse(r["fail"])

    # ── 边界（最容易写错的地方）────────────────────────────────────────────
    def test_blocked_but_last_rc_still_1_is_not_behavior(self):
        """rc=1 出现但**最后一条仍是 rc=1** → 只能算"拦下但无回灌证据"，不能算行为证据。"""
        r = self._j(rows(1, 1, 1), "嗯我看看")
        self.assertEqual(r["verdict"], J.BLOCKED_NO_EVIDENCE)
        self.assertFalse(r["fail"])

    def test_triggered_but_never_blocked(self):
        """门禁跑了但没报问题（rc 全 0）→ ⚠ 探针/匹配器可疑，而不是 ✅。"""
        r = self._j(rows(0), "文件已创建")
        self.assertEqual(r["verdict"], J.PROBE_NOT_BLOCKED)

    def test_probe_not_written_is_not_harness_fault(self):
        r = self._j("", "", hard=True, wrote=False)
        self.assertEqual(r["verdict"], J.PROBE_NOT_WRITTEN)
        self.assertFalse(r["fail"])

    def test_empty_everything_hard_client(self):
        r = self._j("", "", hard=True, wrote=True)
        self.assertEqual(r["verdict"], J.NOT_TRIGGERED)
        self.assertTrue(r["fail"])

    def test_bad_json_lines_are_ignored(self):
        r = self._j("not json\n" + rows(1), "门禁未通过")
        self.assertEqual(r["verdict"], J.PASS_REPLAY, "坏行应被忽略而不是让判定崩掉")

    def test_verdict_is_closed_enum(self):
        """任何输入都必须落到封闭枚举里（不要出现"临时字符串"）。"""
        cases = [("", "", True, True), (rows(1), "", True, True), (rows(0), "x", True, True),
                 (rows(1, 0), "y", True, True), ("", "429", False, False)]
        for log_slice, out, hard, wrote in cases:
            r = self._j(log_slice, out, hard=hard, wrote=wrote)
            self.assertIn(r["verdict"], J.VERDICTS, r)


    # ── F1（评审 v4）：blocked 只能认 rc==1；rc=2/3 是"门禁自己坏了"，绝不能算软通过 ──
    def test_rc2_is_gate_side_error_not_blocked(self):
        r = self._j(rows(2), "没有检查信息")
        self.assertEqual(r["verdict"], J.GATE_SIDE_ERROR)
        self.assertTrue(r["fail"], "门禁自身 rc=2 是 harness 侧问题，必须 FAIL")

    def test_rc3_is_gate_side_error(self):
        r = self._j(rows(3), "x")
        self.assertEqual(r["verdict"], J.GATE_SIDE_ERROR)

    def test_gate_error_beats_keyword(self):
        """关键字正则里含"门禁调用出错" —— 若 rpm 不先判 gate_err，rc=2 会被误判成"回灌成功"。"""
        r = self._j(rows(2), "harness 门禁调用出错 rc=2（多半是配置问题）")
        self.assertEqual(r["verdict"], J.GATE_SIDE_ERROR)
        self.assertTrue(r["fail"])

    def test_mixed_rc1_and_rc2_still_gate_error(self):
        r = self._j(rows(1, 2), "门禁未通过")
        self.assertEqual(r["verdict"], J.GATE_SIDE_ERROR, "混有 rc=2 时不能报平安")

    def test_rc1_blocked_no_evidence_unchanged(self):
        """反向锁：rc=1 的既有语义不许被这次收紧改坏。"""
        r = self._j(rows(1), "嗯我看看")
        self.assertEqual(r["verdict"], J.BLOCKED_NO_EVIDENCE)
        self.assertFalse(r["fail"])

    # ── F2（评审 v4）：探针没写出来 ≠ 模型偷懒；有拦截/权限痕迹就是 harness 侧问题 ──
    def test_probe_write_blocked_by_hook_is_suspect(self):
        r = self._j("", "error: Blocking hook failure — can't open file ...gate.py", hard=True, wrote=False)
        self.assertEqual(r["verdict"], J.PROBE_WRITE_SUSPECT)
        self.assertTrue(r["fail"], "hard 客户端下钩子拦写必须 FAIL")

    def test_permission_denied_is_suspect(self):
        r = self._j("", "Permission denied: src/__verify_probe.vue", hard=True, wrote=False)
        self.assertEqual(r["verdict"], J.PROBE_WRITE_SUSPECT)

    def test_lazy_model_is_not_harness_fault(self):
        """纯偷懒（无任何痕迹）仍然不算 harness 失败 —— 反向锁。"""
        r = self._j("", "文件已创建", hard=True, wrote=False)
        self.assertEqual(r["verdict"], J.PROBE_NOT_WRITTEN)
        self.assertFalse(r["fail"])

    def test_soft_client_write_suspect_is_warning_only(self):
        r = self._j("", "Traceback (most recent call last)", hard=False, wrote=False)
        self.assertEqual(r["verdict"], J.PROBE_WRITE_SUSPECT)
        self.assertFalse(r["fail"], "自觉型下算 ⚠ 不算 FAIL")

class TestJudgeIsActuallyUsed(unittest.TestCase):
    """反向锁：判定器必须真的被验证脚本调用（别抽出个没人用的摆设），且旧的内联 if/elif 链已撤。"""

    def setUp(self):
        self.sh = pathlib.Path(os.path.join(ROOT, "sandbox", "verify_integrations.sh")).read_text(encoding="utf-8")

    def test_script_calls_judge(self):
        self.assertIn("verify_judge.py", self.sh, "验证脚本没有调用抽出来的判定器")

    def test_no_inline_verdict_chain_left(self):
        self.assertNotIn('elif [ "$hit_log" = yes ] && [ "$self_fixed" = yes ]', self.sh,
                         "旧的内联判定链还在：抽出纯函数后应只剩一处（judge 内）")


if __name__ == "__main__":
    unittest.main()
