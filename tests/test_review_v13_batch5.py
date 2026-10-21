# -*- coding: utf-8 -*-
"""评审 v13 严重 4 的回归锁 —— 哨兵在真实提交链路上恒返回 0。

**缺陷形状**（不是"偶尔漏拦"，是"从未生效过一次"）：
    `harness/hooks/pre-commit.py::_run_guard` 写死 `cwd=ROOT` 调哨兵；
    而 `checks/harness_guard.py` 判 self 模式**只靠 cwd 推断**（hook 场景 cwd 才是被提交仓根）。
    写死之后**每一次端仓提交**都被判成"本仓提交"，再撞上紧随其后的
    `if self_mode and not commit_msg: return 0`（pre-commit 阶段 git 还没写 COMMIT_EDITMSG）
    → 哨兵恒返回 0。保护循环照常打印"保护循环：检查 harness 工程仓是否有未提交的门禁文件改动"，
    看起来一切正常 —— 而「改弱门禁脚本 → 不提交 → 回端仓提交业务代码 → 弱化版门禁放行」
    这条它本该唯一堵住的路径，一直是通的。

**修法两条，缺一不可**（本文件分别锁住）：
    ① 调用方不再覆盖 cwd；
    ② 调用方把**自己已经知道的事实**（本次提交的是哪个仓 = `target_repo`）用 `--repo` 显式告知，
       给了就以此为准、不再看 cwd —— 模式判定不能再依赖一个随手可覆盖的环境信号。

**夹具**：`tests/_engine_copy.py`（工程副本 + 干净 git 基线）。用副本而不是弄脏真工程仓，
是为了让本文件既不投毒别的用例、也不依赖开发者工作区的偶然状态 —— 副本里的脏由本文件
自己制造，因此**任何工作区状态下都能跑**，不需要 skip。

本文件带一条**阴性对照**（`test_clean_engine_copy_does_not_block`）：证明"拦住了"确实来自
那处未提交改动，而不是夹具本身恒拦 —— 缺了它的拦截断言无法区分"哨兵正常工作"与"哨兵恒拦"。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _tmpclean import rmtree_force as _rmtree_force  # noqa: E402
from _engine_copy import EngineCopy            # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
REAL_HOOK = os.path.join(ROOT, "harness", "hooks", "pre-commit.py")

_HAS_GIT = os.path.isdir(os.path.join(ROOT, ".git"))

# 快跑（HARNESS_FAST=1）：本文件的慢在**链路集成**（每例现建一份工程副本 + 一次真提交链路），
# 逐个 ~1.5s、整文件 ~10s；下面的源码级契约锁是纯文件读取，快跑档**照跑**
# （维护规则：>5s 的文件接 @_slow，见 tests/test_coverage.py::test_slow_test_files_wire_the_fast_switch）。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, f"快速模式（HARNESS_FAST=1）跳过{reason}")


def _run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)


class _EndRepo:
    """被提交的端仓替身：一个与工程仓无关的最小 git 仓（带一次提交，暂存区为空）。"""

    def __init__(self):
        self.path = tempfile.mkdtemp(prefix="v13b5-end-")
        self._git("init", "-q")
        with open(os.path.join(self.path, "keep.txt"), "w", encoding="utf-8") as f:
            f.write("keep\n")
        self._git("add", "-A")
        self._git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")

    def _git(self, *a):
        return subprocess.run(["git", "-C", self.path] + list(a), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=180)

    def cleanup(self):
        _rmtree_force(self.path)


@_slow("端到端提交链路（每例现建工程副本 + 真起 git 仓）")
@unittest.skipUnless(_HAS_GIT, "工程副本没有 .git：哨兵判据依赖 git 仓，跳过而非假红")
class TestCritical4SentinelOnRealCommitPath(unittest.TestCase):
    """严重 4：从"正在提交另一个仓"的调用链上调哨兵，必须真的拦得住。"""

    def setUp(self):
        self.end = _EndRepo()
        self.eng = EngineCopy()

    def tearDown(self):
        self.eng.cleanup()
        self.end.cleanup()

    # ---- 阴性对照：干净副本 + 同一调用形状 → 不拦 --------------------------------
    def test_clean_engine_copy_does_not_block(self):
        """副本干净时，同一条提交链路必须放行（rc=0，端仓无暂存文件）。

        没有这条，下面那条"拦住了"就无法区分是哨兵按判据工作，还是夹具恒拦。
        """
        r = _run([PY, self.eng.hook, "demo_pc", "--repo", self.end.path], cwd=self.eng.path)
        self.assertEqual(r.returncode, 0,
                         "干净工程仓被哨兵误拦（误报型灾难）：rc=%d\n%s"
                         % (r.returncode, r.stdout + r.stderr))

    # ---- ① 真实入口：走 harness/hooks/pre-commit.py 全链路 ------------------------
    def test_real_precommit_entry_blocks_and_names_the_file(self):
        """`python pre-commit.py demo_pc --repo <端仓>` + 工程仓脏 → rc=1，且点名文件。

        这是端仓提交的真实形状：cwd 是**被提交仓**，调用方通过 --repo 把该事实传给门禁。
        修前 rc=0（哨兵放行 → 端仓无暂存文件 → 直接 return 0），且**一个字都不说**。
        """
        probe = self.eng.make_dirty()
        r = _run([PY, self.eng.hook, "demo_pc", "--repo", self.end.path], cwd=self.eng.path)
        self.assertEqual(r.returncode, 1,
                         "端仓提交未被哨兵拦截：rc=%d\n%s" % (r.returncode, r.stdout + r.stderr))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertIn("未提交的门禁文件改动", out, "拦截输出没有说明原因")
        self.assertIn(os.path.basename(probe), out, "拦截输出没有指出是哪个受保护文件")

    # ---- ② 反向：--repo=工程根 就是本仓提交，不能反过来把本仓提交永久堵死 ----------
    def test_repo_arg_equals_root_is_self_mode_even_from_end_repo_cwd(self):
        """cwd=端仓 + `--repo <工程根>` → self 模式（自指死结仍被解开）。

        与 ③ 互为反向：判据只能来自 --repo，两个方向都要成立。
        """
        self.eng.make_dirty()
        r = _run([PY, self.eng.guard, "harness_self", "--repo", self.eng.path], cwd=self.end.path)
        self.assertEqual(r.returncode, 0,
                         "本仓提交被自己的哨兵挡住（自指死结）：%s" % (r.stdout + r.stderr))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertIn("self 模式", out)
        self.assertIn("读不到提交消息", out, "self 模式必须延后把关给 commit-msg，而不是就地放弃")

    def test_repo_arg_beats_cwd_harness_root(self):
        """cwd=工程根（=旧写死值）+ `--repo <端仓>` → **不是** self 模式 → rc=1。

        精确复现旧调用形状（cwd 恒为工程根），锁住"`--repo` 是权威"这一契约：
        若哪天有人把 cwd 推断重新叠加上去，端仓提交会再次被判成本仓提交。
        """
        self.eng.make_dirty()
        r = _run([PY, self.eng.guard, "demo_pc", "--repo", self.end.path], cwd=self.eng.path)
        self.assertEqual(r.returncode, 1,
                         "cwd=工程根 把端仓提交劫持成了 self 模式：%s" % (r.stdout + r.stderr))
        self.assertNotIn("按 self 模式把关", (r.stdout or "") + (r.stderr or ""))

    # ---- ③ 向后兼容：不带 --repo 的老路径（dispatcher / 手工调用）行为不变 ----------
    def test_cwd_inference_still_works_without_repo_arg(self):
        """不带 `--repo` 时仍按 cwd 推断：端仓 cwd → 拦（rc=1），工程根 cwd → 放行（rc=0）。"""
        self.eng.make_dirty()
        r_end = _run([PY, self.eng.guard, "demo_pc"], cwd=self.end.path)
        self.assertEqual(r_end.returncode, 1,
                         "无 --repo 时 cwd 推断失效（老装法的钩子会漏拦）：%s"
                         % (r_end.stdout + r_end.stderr))
        r_root = _run([PY, self.eng.guard, "harness_self"], cwd=self.eng.path)
        self.assertEqual(r_root.returncode, 0,
                         "无 --repo 时 cwd=工程根 不再判 self（本仓提交会被永久堵死）：%s"
                         % (r_root.stdout + r_root.stderr))

class TestRunGuardSourceContract(unittest.TestCase):
    """两条修法缺一不可，把 ①② 的决定钉在源码上。

    纯文件读取（不建副本、不起 git）—— 所以它**不在** `@_slow` 里：快跑档也要跑，
    否则"有人把写死 cwd 的写法加回来"恰恰会在最快的档位下无人发现。
    """

    def test_run_guard_no_longer_forces_harness_cwd(self):
        """`_run_guard` 不得再写死 cwd（工程根），且必须把目标仓用 `--repo` 传下去。

        今天两者都在也不出错（`--repo` 是权威），但**只留写死 cwd** 就等于把缺陷还给
        任何一个忘了传 `--repo` 的未来调用方 —— 而那正是本缺陷的成因，不是风格问题。
        """
        with open(REAL_HOOK, encoding="utf-8") as f:
            src = f.read()
        i = src.find("def _run_guard")
        self.assertGreater(i, 0, "找不到 _run_guard")
        body = src[i:i + 3000]
        self.assertNotIn("cwd=" + "ROOT", body,
                         "_run_guard 又写死了工作目录 —— 哨兵的 self 模式判定会被劫持")
        self.assertIn('"--repo"', body, "_run_guard 没有再声明 `--repo`，目标仓传递断链")


if __name__ == "__main__":
    unittest.main(verbosity=2)
