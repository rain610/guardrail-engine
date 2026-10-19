# -*- coding: utf-8 -*-
"""Codex 批判性评审（2026-09-14）的回归锁 —— 第 1 批：P0/P1 五条。

对应关系：
  C1 pre-commit 的 fail-open（P0）
  C2 backend/ENC 的 legacy 整规则降级（P1）
  C3 node 探针"调用失败"静默（P1）
  C4 allowlist 加载失败静默空集（P1）
  C5 扩展名清单三套分叉（P1）
每一条都用**行为**锁（不是"源码里有这行字"），失败信息里写明为什么。
"""
import contextlib
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


def _read(rel):
    return pathlib.Path(os.path.join(ROOT, rel)).read_text(encoding="utf-8", errors="replace")


class TestC1PreCommitFailClosed(unittest.TestCase):
    """C1：pre-commit 必须区分「没有暂存文件」与「查不了」。"""

    def test_no_silent_except_left_in_collect(self):
        src = _read("harness/hooks/pre-commit.py")
        self.assertNotIn("except Exception:\n        files = []", src,
                         "收集暂存的 except 分支不得再把任何异常变成空文件列表（那等于静默放行）")
        self.assertIn("fail-closed", src, "查不了时必须明确说明按 fail-closed 处理")

    def test_non_git_dir_returns_2(self):
        """行为：拿一份工程副本（无 .git，绕开 guard）对非 git 目录跑 pre-commit → 必须 rc=2。"""
        work = tempfile.mkdtemp(prefix="c1-")
        try:
            # 只复制门禁运行必需的子树：整仓复制会撞上 dist/ 下被打包进程占用的产物（Permission denied），
            # 也会把评审快照/文档等无关大件拖进来。
            copy = os.path.join(work, "harness_copy")
            os.makedirs(copy)
            for name in ("harness", "checks", "sandbox", "config", "integrations", "agentguard"):
                src = os.path.join(ROOT, name)
                if os.path.isdir(src):
                    shutil.copytree(src, os.path.join(copy, name),
                                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            for name in ("harness.yaml",):
                f = os.path.join(ROOT, name)
                if os.path.isfile(f):
                    shutil.copy(f, copy)
            target = os.path.join(work, "not_a_repo")
            os.makedirs(target)
            with io.open(os.path.join(target, "a.vue"), "w", encoding="utf-8", newline="") as f:
                f.write("console.log(1);\n")
            r = subprocess.run([sys.executable, os.path.join(copy, "harness", "hooks", "pre-commit.py"),
                                "gxgk_pc", "--repo", target.replace("\\", "/")],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=180, cwd=copy)
            self.assertEqual(r.returncode, 2,
                             "非 git / git 不可用必须 rc=2（改前是 rc=0 静默放行）；输出=%s"
                             % ((r.stdout or "") + (r.stderr or ""))[:300])
        finally:
            _rmtree_force(work)

    def test_missing_repo_override_returns_2(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "harness", "hooks", "pre-commit.py"),
                            "gxgk_pc", "--repo", os.path.join(tempfile.gettempdir(), "definitely-not-here-xyz")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=120, cwd=ROOT)
        self.assertIn(r.returncode, (1, 2),
                      "显式给了不存在的 --repo 不允许静默放行（当前 rc=%s）" % r.returncode)
        self.assertNotEqual(r.returncode, 0, "不存在的 --repo 必须出声/阻断")


class TestC2LegacyLineLevel(unittest.TestCase):
    """C2：legacy 只豁免「存量且未改动」，不得整条规则降级。"""

    def _rules(self):
        import backend_ruleset as B
        import encoding_ruleset as E
        out = {}
        for mod in (B, E):
            for n in dir(mod):
                o = getattr(mod, n)
                if isinstance(o, type) and hasattr(o, "severity") and getattr(o, "id", ""):
                    try:
                        out[o.id] = o()
                    except Exception:
                        pass
        return out

    def _ctx(self, changed, is_new):
        import pathlib as _p
        import rule_base
        return rule_base.CheckContext(path=_p.Path("x.cs"), rel="RMC/x.cs", raw="", content="",
                                      script="", template="", mode="strict", repo="",
                                      changed_lines=frozenset(changed), legacy_exempt=True,
                                      is_new_file=is_new, targeted=True)

    def test_changed_lines_not_downgraded(self):
        rules = self._rules()
        for rid in ("R5", "R6", "R9"):
            if rid not in rules:
                continue
            self.assertEqual(rules[rid].severity(self._ctx([5], False)).name, "ERROR",
                             "%s：legacy 仓里**本次改动行**的命中必须照拦（改前整条降 INFO）" % rid)
            self.assertEqual(rules[rid].severity(self._ctx([], False)).name, rules[rid].severity_default.name,
                             "%s：真正存量的（无改动、非新文件）仍按豁免降级" % rid)

    def test_new_file_not_downgraded(self):
        rules = self._rules()
        for rid in ("R5", "R6", "R9", "ENC"):
            if rid not in rules:
                continue
            self.assertEqual(rules[rid].severity(self._ctx([], True)).name, "ERROR",
                             "%s：**新建文件**不得因 legacy 开关降级（编码规则尤其如此）" % rid)

    def test_finding_line_filter_helper(self):
        import rule_base
        class F:
            def __init__(self, line):
                self.line = line
        keep, dropped = rule_base.filter_findings_by_lines([F(5), F(9)], frozenset({5}))
        self.assertEqual([f.line for f in keep], [5])
        self.assertEqual(dropped, 1)
        # 中 C（评审 v13）改了三态：整文件口径现在用 **None** 表示。
        keep2, dropped2 = rule_base.filter_findings_by_lines([F(5)], None)
        self.assertEqual(len(keep2), 1, "changed_lines=None（整文件口径）时不过滤")
        # 空集不再是"不过滤"，而是「读到了 diff、本次没有新增任何行」（纯删除型改动）
        keep3, dropped3 = rule_base.filter_findings_by_lines([F(5)], frozenset())
        self.assertEqual([f.line for f in keep3], [],
                         "空集仍被当成「无 diff 信息」→ 纯删除型改动退回整文件口径，"
                         "存量违规升 ERROR 拦提交（中 C）")
        self.assertEqual(dropped3, 1)


class TestC3NodeProbeVisible(unittest.TestCase):
    """C3：node 存在但调用失败必须可见（可选 fail-closed）。"""

    def test_call_failure_warns(self):
        import pc_code_check
        import rule_base
        real = subprocess.run

        def boom(*a, **k):
            raise PermissionError("node denied")

        subprocess.run = boom
        buf = io.StringIO()
        try:
            rule_base._NODE_PROBE_WARNED[0] = False
            with contextlib.redirect_stderr(buf):
                r = pc_code_check.check_syntax("a.mjs", "console.log(1)")
            self.assertIsNone(r, "默认不阻塞（避免把基础设施问题变成误拦）")
            self.assertIn("node-warn", buf.getvalue(),
                          "调用失败必须出声——改前是 except Exception: return None（完全静默）")
        finally:
            subprocess.run = real

    def test_required_mode_turns_it_into_finding(self):
        import pc_code_check
        import rule_base
        real = subprocess.run

        def boom(*a, **k):
            raise PermissionError("node denied")

        subprocess.run = boom
        os.environ["HARNESS_NODE_REQUIRED"] = "1"
        try:
            rule_base._NODE_PROBE_WARNED[0] = False
            with contextlib.redirect_stderr(io.StringIO()):
                r = pc_code_check.check_syntax("a.mjs", "console.log(1)")
            self.assertIsNotNone(r, "HARNESS_NODE_REQUIRED=1 时语法未检查 = 失败")
            self.assertIn("未检查", r)
        finally:
            subprocess.run = real
            os.environ.pop("HARNESS_NODE_REQUIRED", None)


class TestC4AllowlistLoud(unittest.TestCase):
    """C4：allowlist 加载失败不得静默变成空集合。"""

    def _load_copy(self, rel, yaml_name):
        d = tempfile.mkdtemp(prefix="c4-")
        shutil.copy(os.path.join(ROOT, rel), d)
        buf = io.StringIO()
        import importlib.util
        with contextlib.redirect_stderr(buf):
            spec = importlib.util.spec_from_file_location("tmp_ruleset_%d" % abs(hash(d)),
                                                          os.path.join(d, os.path.basename(rel)))
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
        _rmtree_force(d)
        return m, buf.getvalue()

    def test_pc_missing_allowlist_is_loud(self):
        m, out = self._load_copy("checks/pc_ruleset.py", "components_allowlist.yaml")
        self.assertTrue(getattr(m, "ALLOWLIST_ERRORS", None),
                        "缺 allowlist 文件时必须记录错误（改前 except: pass 静默空集合）")
        self.assertIn("allowlist-warn", out, "并且要出声")

    def test_h5_missing_allowlist_is_loud(self):
        m, out = self._load_copy("checks/h5_ruleset.py", "components_allowlist_h5.yaml")
        self.assertTrue(getattr(m, "ALLOWLIST_ERRORS", None), "H5 同款必须记录错误")
        self.assertIn("allowlist-warn", out)

    def test_real_repo_allowlists_are_healthy(self):
        """反向锁：本仓自己的 allowlist 必须是健康的（否则上面的锁会被"永远为真"污染）。"""
        import pc_ruleset
        import h5_ruleset
        self.assertEqual(pc_ruleset.ALLOWLIST_ERRORS, [], "本仓 PC allowlist 应可正常加载")
        self.assertEqual(h5_ruleset.ALLOWLIST_ERRORS, [], "本仓 H5 allowlist 应可正常加载")


class TestC5ExtensionSingleSource(unittest.TestCase):
    """C5：扩展名清单必须单一来源（hook/gate 层不得各写一套）。"""

    def test_registry_covers_endpoints(self):
        import file_types as F
        self.assertIn(".nvue", F.exts_for("gxgk_h5"), "H5 支持 .nvue，提交期不得漏检")
        self.assertTrue(F.matches("a/b.tsx", "hy_backend"), "后端仓混入 .tsx 必须被看见")
        self.assertIn(".css", F.exts_for("zhongxin_pc"))
        self.assertNotIn(".nvue", F.exts_for("gxgk_pc"), "PC 端不该收 .nvue（避免无关噪声）")
        self.assertEqual(set(F.exts_for("unknown_gate")), set(F.ALL_EXTS),
                         "认不出端点时取并集（宁可多扫不可漏扫）")

    def test_hook_layer_uses_registry(self):
        for rel in ("harness/hooks/pre-commit.py", "harness/hooks/post-edit.py", "harness/agents/auto-gate.py"):
            src = _read(rel)
            self.assertIn("file_types", src, "%s 必须走单一来源 file_types（改前各自手抄）" % rel)

    def test_hook_layer_sets_superset_of_check_scripts(self):
        """hook 层的检查面 ⊇ 各 check 脚本自己的目标面（否则"编辑期看得见、提交期漏检"）。"""
        import file_types as F
        for rel, gate in (("checks/h5_code_check.py", "gxgk_h5"),
                          ("checks/backend_layer_check.py", "hy_backend")):
            src = _read(rel)
            exts = set(re.findall(r'"(\.[a-z]{2,4})"', src))
            hook = set(F.exts_for(gate))
            missing = {e for e in exts if e in (".vue", ".nvue", ".js", ".ts", ".tsx", ".jsx", ".css", ".scss", ".cs")} - hook
            self.assertEqual(missing, set(),
                             "%s 会检查 %s，但 hook 层收不到 → 提交期漏检" % (rel, sorted(missing)))


if __name__ == "__main__":
    unittest.main()
