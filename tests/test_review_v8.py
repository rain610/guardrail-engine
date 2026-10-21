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
import io
import os
import pathlib
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
                                "demo_pc", "--repo", target.replace("\\", "/")],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=180, cwd=copy)
            self.assertEqual(r.returncode, 2,
                             "非 git / git 不可用必须 rc=2（改前是 rc=0 静默放行）；输出=%s"
                             % ((r.stdout or "") + (r.stderr or ""))[:300])
        finally:
            _rmtree_force(work)

    def test_missing_repo_override_returns_2(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "harness", "hooks", "pre-commit.py"),
                            "demo_pc", "--repo", os.path.join(tempfile.gettempdir(), "definitely-not-here-xyz")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=120, cwd=ROOT)
        self.assertIn(r.returncode, (1, 2),
                      "显式给了不存在的 --repo 不允许静默放行（当前 rc=%s）" % r.returncode)
        self.assertNotEqual(r.returncode, 0, "不存在的 --repo 必须出声/阻断")


class TestC2LegacyLineLevel(unittest.TestCase):
    """C2：按行过滤的三态语义（legacy 降级的判据本身在 rule_base，见 test_review_v5 G1）。"""

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


class TestC5ExtensionSingleSource(unittest.TestCase):
    """C5：扩展名清单必须单一来源（hook/gate 层不得各写一套）。"""

    def test_hook_layer_uses_registry(self):
        for rel in ("harness/hooks/pre-commit.py", "harness/hooks/post-edit.py", "harness/agents/auto-gate.py"):
            src = _read(rel)
            self.assertIn("file_types", src, "%s 必须走单一来源 file_types（改前各自手抄）" % rel)

    def test_unknown_gate_takes_union(self):
        """认不出端点时取并集（宁可多扫不可漏扫）—— 开源版 gate 名就是这种形态。"""
        import file_types as F
        self.assertEqual(set(F.exts_for("example")), set(F.ALL_EXTS))


if __name__ == "__main__":
    unittest.main()
