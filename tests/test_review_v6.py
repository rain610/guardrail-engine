# -*- coding: utf-8 -*-
"""外部评审 v6 的回归锁（K1–K8 + Q1/Q3）。

K1 是 P1（按行过滤坐标系错位），核心是**双向**行为锁：
  · 漏报侧：已跟踪文件里"改动行恰好是 script 段内违规" → 必须 rc=1
  · 误报侧：只改模板行、存量 script 违规不动 → legacy 门必须 rc=0（不得"复活"）
夹具都带 git 真仓 + 真 run_gate（端到端），不用纯函数模拟——K1 恰恰是
"函数各自正确、坐标不一致"的缺陷，纯函数锁抓不住。
"""
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
