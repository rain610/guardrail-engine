# -*- coding: utf-8 -*-
r"""外部评审 v13（2026-09-16）落地回归锁。

严重 1（run_gate 没给 PC/H5 注入 `--gate`）的锁在 `tests/test_entry_level.py`
的 `TestGateNameReachesEndpointScript`；本模块锁的是严重 3。

严重 3：`rule_base.compute_changed_lines` 原先用**前缀**判断"文件头 vs 内容行"，
而这个判断在 **hunk 内部**同样生效 —— 正文以 `--` / `++` 开头的行会被误判成文件头：

  · 删除行正文以 `--` 开头（`--x;`、SQL 注释、Markdown 分隔线…）→ diff 行 `---x;`
    被当成 `--- a/...` → 落到"上下文行"分支把行号推进 1 → 其后**所有新增行号整体 +1**
    → 真实新增行上的违规被 `filter_hits_by_lines` 滤掉 = **假放行**（实测 rc=1→0）。
  · 新增行正文以 `++` 开头 → `+++i;` 被当成 `+++ b/...` → 该新增行根本不入集合 →
    集合可能退化成空 → 空集=整文件口径 → 存量违规被当新增 **误拦**。
  · `\ No newline at end of file` 也占掉一个行号 → 同一 hunk 内后续行号整体偏移。

三条都写成**行为锁**：真 git 仓、真 `git diff -U0`、真退出码，不 mock 解析器。
期望值取自实测 diff 的新侧真实行号（不是照着实现反推）。
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _tmpclean import rmtree_force as _rmtree_force      # noqa: E402

# 快跑（HARNESS_FAST=1，2026-09-17 批 4 接线）：本文件慢在**真起 git 仓 + 真跑
# `git diff -U0` + 真起子进程跑 pc_code_check.py**（每条一个临时仓、几次子进程），
# 接线前完全没接（全文 0 处 HARNESS_FAST），快跑档单跑 6.5~8.2s。
# 落点 = **两个类都类级**：5 例里 4 例 ≥1s，唯一"没到线"的那条是 0.979s —— 与 1.006s 的
# 那条是同一种形状、同一笔开销，**按 1s 线切开等于按噪声切**（见 test_coverage.py 名单锁
# docstring 的第三条量法坑：这条线在用例挤在 1s 附近的文件上不重复）。类里没有"便宜且
# 值得在快跑档留着"的锁，故类级。
# ★ 本文件还是变异 M38 的**载体**（把 test_no_newline_marker_does_not_shift_lines 改名成
#   `_disabled_*`）—— 但 M38 的判据在 `test_lock_inventory.py`，数的是源码里 `def test_`
#   的**条数**（静态正则），不看谁被装饰、谁被跳过；且冒烟的 `run()` 钉死全严格档。
#   故本批加装饰器**不会**让 M38 空转（实测：批 4 冒烟 M38 RED）。
#   落点选类级也与 M38 有关：它的锚点 `    def test_no_newline_marker_...(self):` 走的是
#   `apply_mutation` 的 exact 分支，要求该串在文件里**恰好命中 1 次**；类级装饰器不碰那一行，
#   而方法级会在它紧邻上一行插一条 —— 命中次数同样是 1，但没必要把锚点的邻域搅动。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKS = os.path.join(ROOT, "checks")
PY = sys.executable

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _sh(cmd, cwd=None, env=None, timeout=180):
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    if env:
        e.update(env)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=cwd, env=e,
                          stdin=subprocess.DEVNULL, timeout=timeout)


def _git(repo, *a):
    return _sh(["git", "-C", repo] + list(a))


def _write(path, text, newline=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)


# PC 样本：行尾显式 CRLF，免得行尾规则抢戏（PC 默认 CRLF，LF 会引入无关告警）
HDR = ('<template>\r\n  <div>hi</div>\r\n</template>\r\n<script setup>\r\nlet x = 2;\r\n')
FOOT = '\r\n</script>\r\n'


class _Repo:
    """临时 git 仓：commit(基线) / stage(本次改动)，供真实 diff 使用。"""

    def __init__(self, tmp):
        self.path = os.path.join(tmp, "repo")
        os.makedirs(self.path)
        _git(self.path, "init", "-q")
        _git(self.path, "config", "user.email", "v13@t")
        _git(self.path, "config", "user.name", "v13")
        self._n = 0

    def commit(self, rel, text):
        self._n += 1
        _write(os.path.join(self.path, rel), text, newline="")
        _git(self.path, "add", "-A")
        _git(self.path, "commit", "-qm", "base%d" % self._n)

    def stage(self, rel, text):
        _write(os.path.join(self.path, rel), text, newline="")
        _git(self.path, "add", "-A")

    def file(self, rel):
        return os.path.join(self.path, rel)


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v13fix_")
        self.addCleanup(_rmtree_force, self.tmp)
        self.repo = _Repo(self.tmp)
        if CHECKS not in sys.path:
            sys.path.insert(0, CHECKS)
        import rule_base
        self.rb = rule_base

    def _changed(self, rel):
        return sorted(self.rb.compute_changed_lines(self.repo.path, rel))


@_slow("真起 git 仓 + 真跑 git diff -U0（5 例里 4 例 ≥1s，最便宜的一条也 0.98s）")
class TestChangedLinesHunkShapes(_Fixture):
    """严重 3 单元锁：三种被"前缀判定"坑到的 hunk 形状。"""

    REL = "src/a.vue"

    def test_deleted_line_starting_with_dashdash(self):
        """删除行正文 `--x;` → 新增行号不得整体 +1。

        实测 diff：`---x;` 是 hunk 内的删除行；旧实现当文件头 → 新增行 6 记成 7。
        """
        self.repo.commit(self.REL, HDR + '--x;' + FOOT)
        self.repo.stage(self.REL, HDR + 'console.log("leak");' + FOOT)
        self.assertEqual(self._changed(self.REL), [6],
                         "真实新增行是第 6 行；行号偏移会让该行上的违规被判成存量（假放行）")

    def test_added_line_starting_with_plusplus(self):
        """新增行正文 `++i;` → 该行必须真的进集合（否则退化成整文件口径 → 误拦）。"""
        self.repo.commit(self.REL, HDR + 'let y = 1;' + FOOT)
        self.repo.stage(self.REL, HDR + '++i;' + FOOT)
        self.assertEqual(self._changed(self.REL), [6],
                         "新增行正文以 ++ 开头时旧实现把它当 `+++ b/...` 文件头丢掉 → 集合为空")

    def test_no_newline_marker_does_not_shift_lines(self):
        r"""`\ No newline at end of file` 是标记行，不占新侧行号。

        实测 diff：该标记夹在 `-l2` 与 `+l2`/`+l3` 之间；旧实现走"上下文行"分支 → {3,4}。
        """
        self.repo.commit("src/plain.js", "l1\nl2")
        self.repo.stage("src/plain.js", "l1\nl2\nl3\n")
        self.assertEqual(self._changed("src/plain.js"), [2, 3],
                         "无行尾换行标记被当内容行 → 后续行号整体偏移")


@_slow("真起子进程跑 pc_code_check.py 的端到端假放行锁（2 例 1.65 / 1.77s）")
class TestFalsePassEndToEnd(_Fixture):
    """严重 3 的行为锁：真实入口必须拦住"紧跟在 `--` 删除行之后的新增违规"。"""

    def _cfg(self):
        cfg = os.path.join(self.tmp, "cfg.yaml")
        _write(cfg, "repos:\n  chain: %s\ngates:\n  v13:\n    enabled: true\n"
                    "    repo: chain\n    script: checks/pc_code_check.py\n"
                    % self.repo.path.replace("\\", "/"))
        return {"HARNESS_CONFIG": cfg,
                "HARNESS_LOG_FILE": os.path.join(self.tmp, "gate_log.jsonl"),
                "HARNESS_STATS_FILE": os.path.join(self.tmp, "stats.json")}

    def _run_pc(self, path):
        return _sh([PY, os.path.join(CHECKS, "pc_code_check.py"),
                    "--gate", "v13", "--repo", self.repo.path, path], env=self._cfg())

    def test_new_violation_after_dashdash_deletion_blocks(self):
        """`--x;` 被替换成 console.log → 新增行在改动范围内 → 必须拦下。

        旧实现：changed_lines=[7]，命中行 6 被滤掉 → rc=0（**假放行**）。
        """
        self.repo.commit("src/a.vue", HDR + '--x;' + FOOT)
        self.repo.stage("src/a.vue", HDR + 'console.log("leak");' + FOOT)
        f = self.repo.file("src/a.vue")
        r = self._run_pc(f)
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 1,
                         "新增的 console.log 被行号偏移滤掉了（假放行）\n" + out[-800:])
        self.assertIn("console.log", out, "拦下的理由应当是 console.log 规则本身")

    def test_preexisting_violation_outside_change_does_not_block(self):
        """反向对照：存量违规在改动范围之外 → 必须放行。

        没有这条，上面那条的 rc=1 可能只是"整文件口径"的假象（changed_lines 为空时
        会退回整文件扫描）——存量放行成立，才证明按行过滤真的在工作。
        """
        self.repo.commit("src/a.vue", HDR + 'console.log("old");' + FOOT)
        self.repo.stage("src/a.vue", HDR.replace("let x = 2;", "let x = 3;")
                        + 'console.log("old");' + FOOT)
        f = self.repo.file("src/a.vue")
        r = self._run_pc(f)
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 0,
                         "存量（未改动行）上的 console.log 不该拦：说明按行过滤没生效\n" + out[-800:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
