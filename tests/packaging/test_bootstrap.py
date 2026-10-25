# -*- coding: utf-8 -*-
"""bootstrap.py 回归锁（2026-09-18）：引擎引导 —— clone 一次，且不越界。

背景（这个坑的形状）
================================================================================
安装包内**不含引擎**，这是有意的：提交钩子 / MCP 注册 / 编辑期 shim 全都**指向仓库**，
引擎一更新你这边就是新的。代价是"先 clone 引擎仓"成了拿到包的人要做的**第一件事** ——
而它此前只是《安装说明》「先决条件」里括号中的一句、不是一个可执行的步骤。于是首次接入
最常见的第一个坑是：跑到「二」的第 0 步（路径适配）被 fail-fast 拦下，报错说
"没找到 mcp.py"，而真正缺的是"还没 clone"。

本文件钉住四类行为（**全部跑真 git / 真 clone**，不用替身 —— 替身只能证明"我以为
git 会这么干"；本文件的每一条都是一次真的 `git clone`）：

  ① **正路**：clone 真仓 → 产物就位，**且把后面两步的命令打出来**（不要求读者回去翻文档）。
  ② **幂等**：目标已经是引擎仓 → 什么都不做（不 pull、不覆盖），并明说"什么都没做"。
  ③ **不越界**：目标目录占着但不是引擎 → 拒绝、原样保留、指向 `--dir`（不删不并不覆盖）。
  ④ **失败要人话**：没有 git / clone 报错 / 克隆到的不是引擎仓 —— 三条都要给下一步。

另附一条**跨工具的判据锁**（`test_fix_paths_accepts_what_bootstrap_just_cloned`）：
bootstrap 说"就位"了，`fix_paths.py` 就必须不再拦 —— 两处判据一旦分叉，就会长出
"上一步说好了、下一步照样 ✗"的鬼故事。这条不是字符串对拍，是**真跑一遍 fix_paths**。
"""
from __future__ import unicode_literals

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _tmpclean import rmtree_force as _rmtree_force      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "sandbox", "bootstrap.py")
FIXER = os.path.join(ROOT, "sandbox", "fix_paths.py")

_GIT = shutil.which("git")

#: 引擎仓的判据（与 bootstrap.ENGINE_MARKER / fix_paths.py 的 fail-fast 同一条）
MARKER = os.path.join("agentguard", "mcp.py")

MCP_TEMPLATE = """{
  "mcpServers": {
    "agentguard": {
      "command": "__PY__",
      "args": ["__MCP__"],
      "env": {"AGENTGUARD_DIR": "__HARNESS__"}
    }
  }
}
"""


def _run(*args, **kw):
    return subprocess.run([sys.executable, SCRIPT] + list(args),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180, **kw)


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)


def _write(p, text):
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _make_repo(path, engine):
    """造一个能被真 clone 的仓。

    engine=True → 带 `agentguard/mcp.py`（= 引擎仓）；False → 一个别的仓
    （用来看"克隆到的不是引擎仓"那条分支）。**必须有一次提交**：没有提交的空仓
    clone 出来是空的，测不到"文件到位"。
    """
    os.makedirs(path)
    if engine:
        os.makedirs(os.path.join(path, "agentguard"))
        _write(os.path.join(path, *MARKER.split("/")), "# 只为让判据成立\n")
    _write(os.path.join(path, "seed.txt"), "seed\n")
    _git(path, "init", "-q")
    _git(path, "add", "-A")
    # `-c commit.gpgsign=false`：有的机器全局开了提交签名，凭据不在场时 commit 会直接失败，
    # 于是本文件**每一**条用例都会以"造不出测试仓"的名义红掉 —— 那时报错指向的是签名，
    # 与本文件要钉的东西毫无关系。测试要的是"有个能被 clone 的仓"，不是"复现你的 git 配置"。
    _git(path, "-c", "user.email=t@example.com", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed")


@unittest.skipUnless(_GIT, "需要 git 才能跑真 clone")
class BootstrapTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bootstrap-")
        # 2026-09-21：`shutil.rmtree(ignore_errors=True)` 撞上只读的 .git/objects 会静默失败
        # → 每跑一次留一份完整克隆（实测存量 599 份）。走强删助手。
        self.addCleanup(_rmtree_force, self.tmp)
        self.base = self.tmp
        self.src = os.path.join(self.base, "engine-src")      # 假装是内网那个引擎仓
        _make_repo(self.src, engine=True)
        # 目标刻意放在**还不存在的父目录**下：顺带覆盖"父目录要不要建"这条
        self.dst = os.path.join(self.base, "deep", "agentguard")

    def run_bs(self, *args, **kw):
        return _run("--dir", self.dst, "--url", self.src, *args, **kw)

    # ---------------------------------------------------------------- ① 正路
    def test_clone_lands_and_prints_the_next_steps(self):
        r = self.run_bs()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.dst, *MARKER.split("/"))),
                        "clone 完引擎判据文件不在：%s" % r.stdout)
        self.assertIn("引擎已就位", r.stdout)
        # 这一段才是本脚本的重点：clone 完还得知道接下来干嘛。原先的坑不只是"不知道要
        # clone"，还有"clone 完了也不知道后面两步"—— 这里就该把命令直接给出来。
        self.assertIn("fix_paths.py", r.stdout, "没把「第 0 步·路径适配」的命令打出来：\n%s" % r.stdout)
        self.assertIn("setup_machine.sh", r.stdout, "没把「生成覆盖层」的命令打出来：\n%s" % r.stdout)

    # ---------------------------------------------------------------- ② 幂等
    def test_second_run_leaves_the_existing_repo_alone(self):
        """已经是引擎仓 → 一个字都不改（不 pull、不覆盖、不 merge）。"""
        self.assertEqual(self.run_bs().returncode, 0)
        touched = os.path.join(self.dst, "别再动我.txt")
        _write(touched, "本文件必须活下来\n")

        r = self.run_bs()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.isfile(touched), "二次运行动了已有的引擎仓（我的文件没了）")
        self.assertIn("什么都没做", r.stdout,
                      "二次运行该明说「什么都没做」，而不是让人以为又 clone 了一次：\n%s" % r.stdout)
        self.assertNotIn("正在 clone", r.stdout, "已经在了还去 clone：\n%s" % r.stdout)

    # ------------------------------------------------------------ ③ 不越界
    def test_refuses_a_directory_that_is_not_an_engine_repo(self):
        """目录占着但里面不是引擎仓 → 拒绝，且**原样保留**（不删不并不覆盖）。"""
        os.makedirs(self.dst)
        keep = os.path.join(self.dst, "别人的东西.txt")
        _write(keep, "不是你的，别动\n")

        r = self.run_bs()
        self.assertEqual(r.returncode, 1, "占着别人的目录却报成功：%s" % r.stdout)
        self.assertIn("拒绝在", r.stdout, "没说清为什么不动手：\n%s" % r.stdout)
        self.assertIn("--dir", r.stdout, "拒绝之后没给出路（换个位置那条）：\n%s" % r.stdout)
        self.assertTrue(os.path.isfile(keep), "把别人目录里的文件动了")
        self.assertIn("别人的东西.txt", os.listdir(self.dst))
        self.assertFalse(os.path.isdir(os.path.join(self.dst, ".git")),
                         "拒绝是拒绝了，却还是往里 clone 了一个仓")
        self.assertNotIn("正在 clone", r.stdout, "拒绝分支不该走到 clone：\n%s" % r.stdout)

    # ------------------------------------------------------------ ④ 失败要人话
    def test_missing_git_points_at_how_to_install_it(self):
        """没有 git：退出码 2（与 `_require_python` 的"前置不足"同码），并给出装法。

        PATH 指向一个**空目录**而不是空串：Windows 上 `shutil.which` 会把 PATH 里的
        空段当成"当前目录"，空串反而可能捞到别处的 git。
        """
        empty = os.path.join(self.base, "emptybin")
        os.makedirs(empty)
        r = self.run_bs(env=dict(os.environ, PATH=empty))
        self.assertEqual(r.returncode, 2, "前置不足该退 2（不是 1）：\n%s" % r.stdout)
        self.assertIn("winget", r.stdout, "没说清 git 怎么装：\n%s" % r.stdout)
        self.assertFalse(os.path.exists(self.dst), "没 git 却把目录建出来了")

    def test_clone_error_is_reported_with_the_usual_causes(self):
        r = _run("--dir", self.dst, "--url", os.path.join(self.base, "根本没有这个仓"))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("clone 失败", r.stdout, "clone 报错没转成人话：\n%s" % r.stdout)
        self.assertIn("权限", r.stdout, "没提最常见的原因（没权限）：\n%s" % r.stdout)

    def test_cloning_a_repo_that_is_not_the_engine_is_rejected(self):
        """地址敲错、克隆到**别的**仓：git 是成功的，但拿到的不是引擎。

        这条必须与「③ 目录占着」区分开报 —— 两者的下一步完全不同（一个改 --url，
        一个改 --dir）。所以断言取的是本分支独有的措辞。
        """
        other = os.path.join(self.base, "other-repo")
        _make_repo(other, engine=False)
        r = _run("--dir", self.dst, "--url", other)
        self.assertEqual(r.returncode, 1, "克隆到非引擎仓却报成功：\n%s" % r.stdout)
        self.assertIn("clone 完了，但", r.stdout, "没识别出「拿到的不是引擎仓」：\n%s" % r.stdout)
        self.assertNotIn("引擎已就位", r.stdout, "拿到的不是引擎却报就位：\n%s" % r.stdout)

    # ------------------------------------------------------------ --check
    def test_check_mode_touches_nothing(self):
        r = self.run_bs("--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("体检通过", r.stdout, r.stdout)
        self.assertFalse(os.path.exists(self.dst), "--check 竟然动了手")
        self.assertNotIn("正在 clone", r.stdout, "--check 竟然去 clone 了：\n%s" % r.stdout)


@unittest.skipUnless(_GIT, "需要 git 才能跑真 clone")
class CriterionMatchesFixPaths(unittest.TestCase):
    """跨工具判据锁：bootstrap 说"就位"了，`fix_paths.py` 就必须不再拦。

    为什么值得单列：两支脚本各自硬写了一份判据（`agentguard/mcp.py` 在不在）。
    bootstrap 判据一旦比 fix_paths 松，就会出现"引导说好了、下一步照样 ✗"的鬼故事 ——
    而这正是本次要消灭的那个坑的**同款形状**（报错与真实原因错位）。
    判据不是对拍字符串（那种锁会随措辞变动假红），而是**真跑一遍 fix_paths**。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bootstrap-x-")
        # 2026-09-21：`shutil.rmtree(ignore_errors=True)` 撞上只读的 .git/objects 会静默失败
        # → 每跑一次留一份完整克隆（实测存量 599 份）。走强删助手。
        self.addCleanup(_rmtree_force, self.tmp)
        self.base = self.tmp
        self.src = os.path.join(self.base, "engine-src")
        _make_repo(self.src, engine=True)
        self.dst = os.path.join(self.base, "cloned")
        self.mkt = os.path.join(self.base, "pkg", "marketplace")
        d = os.path.join(self.mkt, "plugins", "agentguard")
        os.makedirs(d)
        _write(os.path.join(d, ".mcp.json"), MCP_TEMPLATE)

    def test_fix_paths_accepts_what_bootstrap_just_cloned(self):
        r = _run("--dir", self.dst, "--url", self.src)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        f = subprocess.run([sys.executable, FIXER, "--harness", self.dst,
                            "--marketplace", self.mkt, "--python", sys.executable],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        self.assertEqual(f.returncode, 0,
                         "bootstrap 说就位了，fix_paths 却拦下了 —— 两处判据分叉了：\n%s"
                         % (f.stdout + f.stderr))
        self.assertIn("适配完成", f.stdout, f.stdout)
        self.assertNotIn("没找到", f.stdout, "bootstrap 刚 clone 的目录被 fix_paths 判成不合格：\n%s" % f.stdout)


if __name__ == "__main__":
    unittest.main()
