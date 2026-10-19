# -*- coding: utf-8 -*-
"""外部评审 v6 的回归锁（K1–K8 + Q1/Q3）。

K1 是 P1（按行过滤坐标系错位），核心是**双向**行为锁：
  · 漏报侧：已跟踪文件里"改动行恰好是 script 段内违规" → 必须 rc=1
  · 误报侧：只改模板行、存量 script 违规不动 → legacy 门必须 rc=0（不得"复活"）
夹具都带 git 真仓 + 真 run_gate（端到端），不用纯函数模拟——K1 恰恰是
"函数各自正确、坐标不一致"的缺陷，纯函数锁抓不住。
"""
import io
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _tmpclean import rmtree_force as _rmtree_force  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "checks"))
sys.path.insert(0, os.path.join(ROOT, "sandbox"))


def _read(rel):
    return pathlib.Path(os.path.join(ROOT, rel)).read_text(encoding="utf-8", errors="replace")


def _msys_bash():
    """优先 Git-for-Windows 的 MSYS bash（System32 的 WSL bash 看不懂 C:/ 路径）。"""
    if os.name == "nt":
        for cand in (r"C:/Program Files/Git/bin/bash.exe", r"C:/Program Files/Git/usr/bin/bash.exe"):
            if os.path.isfile(cand):
                return cand
        return None
    return shutil.which("bash")


def _posix(p):
    """Windows 盘符路径 → MSYS 路径（C:/x → /c/x）。"""
    p = p.replace("\\", "/")
    if os.name == "nt" and len(p) > 1 and p[1] == ":":
        p = "/" + p[0].lower() + p[2:]
    return p


# 快跑（HARNESS_FAST=1，2026-09-17 批 3 接线）：接线前本文件是"半接"的 —— 只有
# TestK6DoctorWarnCount 里那一条用行内 `if os.environ.get("HARNESS_FAST"): self.skipTest(...)`
# 写了开关（那一条独占 46.5s），其余全没接，快跑档单跑仍要 14.1s。
# 落点按实测（≥1s 的 7 例占 13.4s）：
#   · TestK1LineCoordinateAlignment → **方法级**：同类里
#     test_script_line_offset_is_absolute_not_segment_relative 是纯源码级坐标算术锁（0.008s），
#     它就是"改坏了立刻知道"的那一类，类级会把它一起清掉。
#   · TestQ1IgnoredNewFileContract / TestL3LogRulesInvariant → **类级**（类里没有 <1s 的例）。
#   · TestK6DoctorWarnCount 那条行内开关换成标准 `@_slow`（语义不变，只是并到同一套写法上），
#     同类的 test_counting_patch_present 是快速前哨（<0.1s，源码级），照跑。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


class GateFixture:
    """密闭 git 夹具：临时仓 + 临时 gate 配置 + 真跑 run_gate。"""

    def __init__(self, gate, legacy=False, eol="crlf", ignored=False):
        self.d = tempfile.mkdtemp(prefix="v6-")
        self.repo = os.path.join(self.d, "repo")
        os.makedirs(os.path.join(self.repo, "src"))
        subprocess.run(["git", "init", "-q"], cwd=self.repo, capture_output=True)
        self.cfg = os.path.join(self.d, "cfg.yaml")
        with io.open(self.cfg, "w", encoding="utf-8", newline="") as f:
            f.write("repos:\n  t: %s\ngates:\n  t:\n    enabled: true\n    repo: t\n"
                    "    script: checks/%s_code_check.py\n    run_on: [pre_commit, post_edit]\n%s"
                    % (self.repo.replace("\\", "/"), gate, "    legacy_baseline: true\n" if legacy else ""))
        # 旧锁 test_tests_set_harness_log_file（N-2 血案）：凡调 run_gate 的测试都必须隔离日志，
        # 否则探针记录会写进真实 reports/gate_log.jsonl。
        self.log = os.path.join(self.d, "gate_log.jsonl")
        self.env = dict(os.environ, HARNESS_CONFIG=self.cfg, PYTHONUTF8="1", HARNESS_LOG_FILE=self.log)
        self.eol = eol
        self.f = os.path.join(self.repo, "src", "a.vue")
        if ignored:
            with io.open(os.path.join(self.repo, ".gitignore"), "w", encoding="utf-8", newline="") as f:
                f.write("dist/\n")
            os.makedirs(os.path.join(self.repo, "dist"))
            self.f = os.path.join(self.repo, "dist", "b.vue")

    def write(self, script_body, tpl="  <div>h</div>"):
        txt = "<template>\n%s\n</template>\n<script setup>\n%s\n</script>\n" % (tpl, script_body)
        if self.eol == "crlf":
            txt = txt.replace("\n", "\r\n")
        with io.open(self.f, "w", encoding="utf-8", newline="") as f:
            f.write(txt)

    def commit(self, msg="base"):
        subprocess.run(["git", "add", "-A"], cwd=self.repo, capture_output=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", msg],
                       cwd=self.repo, capture_output=True)

    def stage(self):
        subprocess.run(["git", "add", "-A"], cwd=self.repo, capture_output=True)

    def run(self):
        r = subprocess.run([sys.executable, "harness/run_gate.py", "t", self.f], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=self.env, timeout=180)
        self.last_out = (r.stdout or "") + (r.stderr or "")
        return r.returncode

    def cleanup(self):
        _rmtree_force(self.d)


class TestK1LineCoordinateAlignment(unittest.TestCase):
    """K1（P1）：命中行号必须是**文件绝对行号**，才能与 changed_lines 比较。"""

    def test_script_line_offset_is_absolute_not_segment_relative(self):
        import pc_code_check as PC
        from pc_ruleset import ConsoleLogRule
        import rule_base
        c = "<template>\n  <div>h</div>\n</template>\n<script setup>\nconsole.log(1)\n</script>\n"
        off = PC.script_line_offset(c)
        self.assertEqual(off, 3, "偏移量 = <script> 段内容起点之前的换行数")
        hits = ConsoleLogRule()._find_hits(PC.extract_script(c), off)
        self.assertEqual([h[0] for h in hits], [5], "命中行号应为文件绝对行号 5（不是段内 2）")
        kept, dropped = rule_base.filter_hits_by_lines(hits, frozenset({5}))
        self.assertEqual(len(kept), 1, "改的是第 5 行 → 命中必须保留（漏报侧）")
        kept2, dropped2 = rule_base.filter_hits_by_lines(hits, frozenset({2}))
        self.assertEqual(len(kept2), 0, "只改第 2 行（模板行）→ 命中必须丢弃（误报侧）")

    @_slow("真起密闭 git 仓 + 真跑 run_gate（2.7s）")
    def test_pc_tracked_file_changed_line_violation_blocks(self):
        """端到端漏报侧：普通门 + 已跟踪文件改出 console.log → rc=1（修复前 rc=0）。"""
        fx = GateFixture("pc", eol="crlf")
        try:
            fx.write("const a = 1;\nconsole.log(1);")
            fx.commit()
            fx.write("const a = 2;\nconsole.log(2);")     # 第 5/6 行都改，含新违规
            self.assertEqual(fx.run(), 1, "改动行里的违规必须拦（rc=1），输出=%s" % fx.last_out[:200])
            fx.stage()
            self.assertEqual(fx.run(), 1, "提交期口径同样必须拦")
        finally:
            fx.cleanup()

    @_slow("真起密闭 git 仓 + 真跑 run_gate（1.8s）")
    def test_pc_legacy_untouched_existing_violation_not_blocked(self):
        """端到端误报侧：legacy 门只改模板行、存量 console.log 不动 → rc=0。"""
        fx = GateFixture("pc", legacy=True, eol="crlf")
        try:
            fx.write("const a = 1;\nconsole.log(1);")
            fx.commit()
            fx.write("const a = 1;\nconsole.log(1);", tpl="  <div>changed</div>")
            self.assertEqual(fx.run(), 0, "存量违规未改动时不得被拦（否则编辑期处处误报），输出=%s" % fx.last_out[:200])
        finally:
            fx.cleanup()

    @_slow("真起密闭 git 仓 + 真跑 run_gate（1.7s）")
    def test_h5_tracked_file_changed_line_violation_blocks(self):
        fx = GateFixture("h5", eol="lf")
        try:
            fx.write("const a = 1;\nconsole.log(1);")
            fx.commit()
            fx.write("const a = 2;\nconsole.log(2);")
            self.assertEqual(fx.run(), 1, "H5 同款：改动行违规必须拦，输出=%s" % fx.last_out[:200])
        finally:
            fx.cleanup()

    @_slow("真起密闭 git 仓 + 真跑 run_gate（1.6s）")
    def test_script_at_file_top_still_blocks(self):
        """坐标系偏移=0 的形态（script 在第 1 行）也不能回归。"""
        fx = GateFixture("pc", eol="crlf")
        try:
            top = '<script setup>\nconst a = 1; console.log(1);\n</script>\n<template>\n  <div>h</div>\n</template>\n'
            with io.open(fx.f, "w", encoding="utf-8", newline="") as f:
                f.write(top.replace("\n", "\r\n"))
            fx.commit()
            with io.open(fx.f, "w", encoding="utf-8", newline="") as f:
                f.write(top.replace("a = 1; console.log(1)", "a = 2; console.log(2)").replace("\n", "\r\n"))
            self.assertEqual(fx.run(), 1, "script 在文件首行时改行违规仍须拦")
        finally:
            fx.cleanup()


@_slow("真起密闭 git 仓 + 真跑 run_gate（两侧各 1.2~1.3s，类里没有更便宜的例）")
class TestQ1IgnoredNewFileContract(unittest.TestCase):
    """Q1：`.gitignore` 命中的新文件"放行"是**契约**（不是漏洞），钉住它。"""

    def test_ignored_new_file_is_skipped(self):
        fx = GateFixture("pc", legacy=True, eol="crlf", ignored=True)
        try:
            fx.write("console.log(1);")
            self.assertEqual(fx.run(), 0, ".gitignore 命中的新文件按既有语义放行（构建产物不制造噪声）")
        finally:
            fx.cleanup()

    def test_force_added_ignored_file_is_checked(self):
        fx = GateFixture("pc", legacy=True, eol="crlf", ignored=True)
        try:
            fx.write("console.log(1);")
            subprocess.run(["git", "add", "-f", "dist/b.vue"], cwd=fx.repo, capture_output=True)
            self.assertEqual(fx.run(), 1, "`git add -f` 之后就是会进提交的代码 → 必须拦")
        finally:
            fx.cleanup()


class TestK2NoDeprecationWarning(unittest.TestCase):
    """K2：list_entries 不得把 kwargs 传给 Path()（3.13 警告、3.14 TypeError）。"""

    def test_source_has_no_kwargs_to_path(self):
        src = _read("sandbox/list_entries.py")
        self.assertNotIn('Path(path, encoding=', src)
        for m in re.finditer(r"Path\(([^)]*)\)", src):
            self.assertNotIn("encoding", m.group(1), "Path() 参数里不该有 encoding：%s" % m.group(0))

    def test_runs_clean_under_werror(self):
        r = subprocess.run([sys.executable, "-W", "error", "sandbox/list_entries.py", "--check"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           cwd=ROOT, timeout=120)
        self.assertEqual(r.returncode, 0, "-W error 下必须 rc=0（警告即失败），stderr=%s" % (r.stderr or "")[:300])


class TestK3ReadmeNoDeadCounts(unittest.TestCase):
    """K3：README 不得写死用例数（写死必旧）。"""

    def test_no_hardcoded_case_count(self):
        src = _read("README.md")
        bad = re.findall(r"\d+\s*例", src)
        self.assertEqual(bad, [], "README 里不允许出现写死的用例数（会静默过期）：%s" % bad)


class TestK4CoverageEmptyRulesDict(unittest.TestCase):
    """K4：`rules={}` 也算"没有规则级数据"。"""

    def test_empty_dict_counted(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("cov", os.path.join(ROOT, "harness", "coverage.py"))
        C = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(C)
        recs = [{"ts": "1", "gate": "g", "rc": 0, "scope": "targeted", "ms": 1, "rules": {"X": {"error": 1}}},
                {"ts": "2", "gate": "g", "rc": 0, "scope": "targeted", "ms": 1, "rules": {}},
                {"ts": "3", "gate": "g", "rc": 0, "scope": "targeted", "ms": 1}]
        rep = C.build_report(recs, {}, {})
        self.assertEqual(rep.get("n_no_rule_data"), 2, "空 dict 与缺字段都必须计入缺数据")

    def test_only_empty_dict_window(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("cov", os.path.join(ROOT, "harness", "coverage.py"))
        C = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(C)
        rep = C.build_report([{"ts": "1", "gate": "g", "rc": 0, "scope": "targeted", "ms": 1, "rules": {}}], {}, {})
        self.assertEqual(rep.get("n_no_rule_data"), 1)
        self.assertFalse(rep.get("has_rule_data"), "只有空 dict 时不能宣称「有规则级数据」")


class TestK6DoctorWarnCount(unittest.TestCase):
    """K6：doctor 汇总不得把 ⚠ 丢进子壳（33 条提示 → "0 项 / 全绿"）。"""

    def test_counting_patch_present(self):
        """⚠ 本锁只是**快速前哨**（评审 v7 的 M6a 变异实验证明：把重算打断后本锁照样绿），
        真正的判据在同类的 test_summary_matches_displayed_warnings（行为锁）。"""
        src = _read("sandbox/doctor.sh")
        self.assertIn("N_HOOK_WARN", src, "WARN 必须像 FAIL 那样用 grep -c 在子壳外重算")
        self.assertIn("N_HOOK_NONE", src, "NONE 走的也是 warn()，同样要计入")

    @_slow("真跑 doctor.sh（本文件最贵的一条；2026-09-18 实测独占 65~85s）")
    def test_summary_matches_displayed_warnings(self):
        """行为：跑真 doctor，汇总行的 ⚠ 数必须等于实际显示的 ⚠ 行数。

        预算是**防挂死**的，不是性能断言 —— 2026-09-18 从 300s 提到 600s，理由是实测：
        doctor.sh 单跑 65~85s（分段计时：2/4 门禁探针 6s、3/4 hook 活性约 36s（一个 python
        进程里约 100 次 git 子进程）、3b/4 约 21~30s（`$(sleep 30 | timeout 2 …)` 按构造就要
        等整条管道）、4/4 三个软链约 14s）—— 成本几乎全在 **Windows 进程创建**上。
        全量套件里它跑到 **370s**（同一份脚本，5× 膨胀），于是红了。

        红的样子很费解、值得记一笔：`TimeoutExpired: … timed out after -70.65 seconds`
        —— **负的**。`Popen.communicate` 只在管道空闲超时时才查预算，而 doctor.sh 一直在吐
        输出，所以它一路跑过 300s 都没触发，最后那句 `self.wait(timeout=剩余)` 才拿到负数。
        300s 对 65~85s 只有 4 倍余量，在套件负载下不够；600s 给到 7 倍以上，同时仍能拦住真挂死。
        """
        bash = _msys_bash()
        if not bash:
            self.skipTest("无可用 bash")
        r = subprocess.run([bash, "sandbox/doctor.sh"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=ROOT, timeout=600)
        out = (r.stdout or "") + (r.stderr or "")
        m = re.search(r"❌ (\d+) 项必须处理 / ⚠ (\d+) 项提示", out)
        self.assertIsNotNone(m, "找不到 doctor 汇总行")
        shown = len([l for l in out.splitlines() if l.strip().startswith("⚠")])
        self.assertEqual(int(m.group(2)), shown,
                         "汇总 ⚠ %s 与实际显示 %d 条不一致（子壳吞计数的老毛病）" % (m.group(2), shown))


class TestK7WriteContextNarrowed(unittest.TestCase):
    """K7：G9 语境必须收成钩子词（点号/缩写/文件名形态曾让语境≈恒真）。"""

    def _judge(self, out):
        import importlib.util
        spec = importlib.util.spec_from_file_location("vj", os.path.join(ROOT, "sandbox", "verify_judge.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m.judge("claude", True, False, "", out)

    def test_version_and_abbrev_do_not_trigger(self):
        for txt in ("上次的 permission denied 是 chmod 问题，v2.0 才有的",
                    "permission denied，e.g. chmod 755 解决的",
                    "permission denied（文件 a.b 相关）"):
            r = self._judge(txt)
            self.assertEqual(r["verdict"], "probe_not_written",
                             "闲聊/版本号/缩写不该被当成钩子故障：%s" % txt)

    def test_real_probe_path_still_triggers(self):
        for txt in ("Permission denied: src/__verify_probe_claude.vue",
                    "harness hook: permission denied writing gate_log.jsonl"):
            r = self._judge(txt)
            self.assertEqual(r["verdict"], "probe_write_suspect", "真落点仍须判怀疑：%s" % txt)


class TestK8HarnessRootWithoutGate(unittest.TestCase):
    """K8：工程根命中但实现缺失（clone 半残）→ 出声一次，不阻塞。"""

    def test_speaks_when_gate_missing(self):
        d = tempfile.mkdtemp(prefix="k8-")
        try:
            root = os.path.join(d, "agentguard")
            os.makedirs(os.path.join(root, "harness", "agents"))
            os.makedirs(os.path.join(root, "integrations", "hooks"))
            shutil.copy(os.path.join(ROOT, "integrations", "hooks", "agentguard-gate.py"),
                        os.path.join(root, "integrations", "hooks", "agentguard-gate.py"))
            # 故意不建 harness/agents/auto-gate.py
            # 隔离 HOME：否则真机上的 ~/.agentguard/harness.path 指针会把解析救活（那不是本锁要测的）
            env = dict(os.environ, AGENTGUARD_DIR=root, HARNESS_DIR=root, PYTHONUTF8="1",
                       HOME=tempfile.mkdtemp(prefix="k8home-"), USERPROFILE=tempfile.mkdtemp(prefix="k8up-"))
            env.pop("HARNESS_CONFIG", None)
            hook = os.path.join(root, "integrations", "hooks", "agentguard-gate.py")
            r = subprocess.run([sys.executable, hook, "claude"], input='{"tool_input":{"file_path":"/tmp/x.py"}}',
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env=env, cwd=d, timeout=120)
            self.assertEqual(r.returncode, 0, "仍不许阻塞")
            self.assertIn("auto-gate.py", (r.stderr or ""), "根在但实现缺失必须出声")
        finally:
            _rmtree_force(d)


@_slow("真起密闭 git 仓 + 真跑 run_gate（单例 2.9s）")
class TestL3LogRulesInvariant(unittest.TestCase):
    """L3：空数据 → 记录**无** rules 键（反向锁；正向锁在 test_coverage）。"""

    def test_clean_file_has_no_rules_key_and_violation_has_one(self):
        fx = GateFixture("pc", eol="crlf")
        try:
            fx.write("const a = 1;")                     # 干净
            fx.commit()
            fx.run()
            fx.write("const a = 2;\nconsole.log(1);")   # 违规（改动行）
            fx.run()
            import json
            recs = [json.loads(l) for l in pathlib.Path(fx.log).read_text(encoding="utf-8").splitlines() if l.strip()]
            self.assertGreaterEqual(len(recs), 2, "夹具应产生两条记录")
            self.assertNotIn("rules", recs[0], "干净运行不得写 rules 键（空 dict 视为无数据）")
            self.assertIn("rules", recs[1], "有触发必须写 rules")
        finally:
            fx.cleanup()


class TestQ5AcceptedLossFrozen(unittest.TestCase):
    """Q5（评审 v7 判决）：无钩子/探针落点的 `permission denied` 明确是**接受的损失**，冻结意图。"""

    def test_bare_permission_denied_on_other_file_is_not_suspect(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("vj", os.path.join(ROOT, "sandbox", "verify_judge.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        r = m.judge("claude", True, False, "", "Permission denied: src/utils.vue")
        self.assertEqual(r["verdict"], "probe_not_written",
                         "无钩子/探针落点的 permission denied 不再判 suspect（接受的损失，勿改回）")


if __name__ == "__main__":
    unittest.main()
