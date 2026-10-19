# -*- coding: utf-8 -*-
"""评审 v13 中 17：安装/卸载不对称 —— 存档被覆盖 + 卸载不读存档。

缺陷原文（实测）
================================================================================
`install_hooks.sh` 每次安装都整体重写 `PREVIOUS-hooksPath.txt`，二次安装时
「安装前 core.hooksPath」已经是我们自己上次写进去的共享目录 —— **真正的原值被永久抹掉**；
卸载侧（`cli.py`）只做 `--unset core.hooksPath`，从不读那份存档，却打印「回滚完成」。
实测：接入前指向 `.myhooks` 的仓，卸载后没有复原。

本文件钉住三件事
================================================================================
  ① **写入侧**：装两次，存档里记的仍是**首装时**的原值（不是第二次的快照）。
  ② **读取侧**：卸载真的把原值还原回去（review 的实测场景：`.myhooks`）；
     没有存档时要说清"只能清空、无法还原"，而不是继续打印「回滚完成」。
     这里**跑真 install_hooks.sh + 真 cli.py**，不用手写的假存档 ——
     手写存档只能证明"我以为格式是这样"，跑真脚本才锁得住读写两侧的契约。
  ③ **镜像实现不漂移**：`sandbox/uninstall.py`（CLI 转发不了时的兜底，随包分发、
     不能 import 包内模块）与 `agentguard/cli.py` 解析同一份存档必须同结论。

另附：`install_engine_hooks.sh` 的一条**自毁**路径（同一次排查中发现，见该文件头）
—— 它原先用 `git rev-parse --git-path hooks` 定位目录，而该命令尊重 `core.hooksPath`，
于是会指向共享分发器本身：`cat > $HOOKS_DIR/pre-commit` 覆盖 33 个端仓共用的分发器，
`cat "$CM_SOURCE" > "$TARGET_MSG"`（同一个文件）把共享 commit-msg 截断成 0 字节。
这里锁住"它不再这么干"，并**实测跑一遍**确认共享分发器指纹没变。
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
from _tmpclean import rmtree_force as _rmtree_force  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "agentguard", "cli.py")
INSTALL_HOOKS = os.path.join(ROOT, "sandbox", "install_hooks.sh")
ENGINE_HOOKS = os.path.join(ROOT, "sandbox", "install_engine_hooks.sh")
UNINSTALL_PY = os.path.join(ROOT, "sandbox", "uninstall.py")
SHARED = os.path.join(ROOT, "harness", "hooks-dir")

_HAS_GIT = shutil.which("git") is not None
_HAS_SH = shutil.which("sh") is not None

# 存档行格式（与 agentguard/cli.py / sandbox/uninstall.py 的常量一致，有 test_same_constants 锁）
PREV_HP_PREFIX = "安装前 core.hooksPath："

# 快跑（HARNESS_FAST=1）：本文件的慢在**真起 git 仓 + 真跑两个脚本**（每例 1~2s）。
# 源码级契约锁是纯文件读取，快跑档照跑 —— 否则"有人把 --git-path hooks 写回来"
# 恰恰会在最快的档位下无人发现。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


def _read(p):
    with io.open(p, encoding="utf-8", errors="replace") as f:
        return f.read()


def _run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)


def _git(repo, *args):
    r = _run(["git", "-C", repo] + list(args))
    return r.returncode, (r.stdout or "").strip()


def _code_only(src):
    """去掉整行注释。

    注释里会**引用**被禁的写法（说明为什么不能用），整文件断言会把说明文字判成违规
    —— 那是锁写错了，不是代码错了。判定要落在真代码上。
    """
    return "\n".join(ln for ln in src.splitlines()
                     if ln.strip() and not ln.strip().startswith("#"))


def _archived_original(text):
    """取存档里**首装那行**记录的原值（必须以该前缀开头）。

    不能整文件 `in` 判断：重装时追加的那行写的是「本次安装前 core.hooksPath：X」，
    它**天然**含子串「安装前 core.hooksPath：X」—— 用子串判定会把这条合法历史记成违规。
    （首版就是这么写的，实测被自己追加的那行咬了一口。）
    """
    for line in text.splitlines():
        if line.startswith(PREV_HP_PREFIX):
            return line[len(PREV_HP_PREFIX):].strip()
    return None


class _RepoFixture(unittest.TestCase):
    """一个临时 git 仓 + 一个"接入前就存在的自定义 hooks 目录"（review 的 .myhooks 场景）。"""

    ORIGINAL = ".myhooks"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v13b6-")
        self.addCleanup(_rmtree_force, self.tmp)
        self.repo = os.path.join(self.tmp, "endrepo")
        os.makedirs(self.repo)
        _run(["git", "init", "-q"], cwd=self.repo)
        os.makedirs(os.path.join(self.repo, self.ORIGINAL), exist_ok=True)
        # 接入前：本仓指向自己的自定义 hooks 目录（这正是"卸载必须还原"的原值）
        _git(self.repo, "config", "--local", "core.hooksPath", self.ORIGINAL)
        self.archive = os.path.join(self.repo, ".git", "agentguard-original-hooks",
                                    "PREVIOUS-hooksPath.txt")

    def install(self):
        return _run(["sh", INSTALL_HOOKS], cwd=self.repo)

    def uninstall_cli(self):
        return _run([sys.executable, CLI, "hooks", "uninstall", "--repo", self.repo])

    def hookspath(self):
        rc, out = _git(self.repo, "config", "--local", "--get", "core.hooksPath")
        return out if rc == 0 else ""


@unittest.skipUnless(_HAS_GIT and _HAS_SH, "需要 git 与 sh（git-bash）才能跑真脚本")
@_slow("真起 git 仓 + 真跑 install_hooks.sh / cli.py（每例 1~2s）")
class TestInstallUninstallSymmetry(_RepoFixture):

    def test_archive_is_written_once_and_survives_reinstall(self):
        """① 装两次，存档记的仍是首装时那个原值。

        修前：第二次安装把「安装前 = .myhooks」改写成「安装前 = <共享目录>」，
        原值从此在任何地方都查不到（不是"记错了"，是**没了**）。
        """
        r1 = self.install()
        self.assertEqual(r1.returncode, 0, "首次安装失败：%s" % (r1.stdout + r1.stderr))
        self.assertTrue(os.path.isfile(self.archive), "安装没留下原值存档：%s" % self.archive)
        first = _read(self.archive)
        self.assertEqual(_archived_original(first), self.ORIGINAL,
                         "首装存档没记下原值：\n%s" % first)

        r2 = self.install()          # 幂等重装 —— 缺陷就在这一步
        self.assertEqual(r2.returncode, 0, "二次安装失败：%s" % (r2.stdout + r2.stderr))
        second = _read(self.archive)
        self.assertEqual(_archived_original(second), self.ORIGINAL,
                         "二次安装把首装记录的原值覆盖掉了 —— 卸载将无法还原：\n%s" % second)
        self.assertNotEqual(_archived_original(second), SHARED.replace("\\", "/"),
                            "存档把『安装前』记成了门禁自己的共享目录（= 原地打转）")
        self.assertIn("再次安装", second, "重装事实没有留下痕迹（存档该记得它被装过两次）")

    def test_uninstall_restores_original_hookspath(self):
        """② 卸载把原值还原回去 —— review 实测的那条（修前 .myhooks 没复原）。"""
        self.install()
        r = self.uninstall_cli()
        self.assertEqual(r.returncode, 0, "卸载失败：%s" % (r.stdout + r.stderr))
        self.assertEqual(self.hookspath(), self.ORIGINAL,
                         "卸载后 core.hooksPath 不是接入前的原值（修前就是这里错）：\n%s"
                         % r.stdout)
        self.assertIn("还原", r.stdout, "输出没说清做了什么：\n%s" % r.stdout)

    def test_uninstall_restores_after_double_install(self):
        """①② 合起来：装两次之后再卸载，原值仍要还原（修前这条必红）。"""
        self.install()
        self.install()
        r = self.uninstall_cli()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.hookspath(), self.ORIGINAL,
                         "装两次后卸载没还原原值 —— 存档被覆盖的后果：\n%s" % r.stdout)

    def test_uninstall_without_archive_does_not_claim_rollback(self):
        """没有存档时：清空可以，但**不许**说「回滚完成」（那是谎报）。

        用真安装脚本无法制造"无存档"场景（它总会写），所以这里手工清掉存档，
        模拟"存档被人删了 / 包是从旧版本升上来的"。
        """
        self.install()
        os.remove(self.archive)
        r = self.uninstall_cli()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.hookspath(), "", "无存档时应清空")
        self.assertIn("无法", r.stdout, "没告诉用户「还原不了」：\n%s" % r.stdout)
        self.assertNotIn("回滚完成", r.stdout,
                         "无存档还打印「回滚完成」= 谎报（修前正是如此）：\n%s" % r.stdout)

    def test_uninstall_restores_when_install_before_was_unset(self):
        """接入前本来就没设置 → 卸载后必须回到"未设置"，而不是留着我们的共享目录。"""
        _git(self.repo, "config", "--local", "--unset", "core.hooksPath")
        self.install()
        r = self.uninstall_cli()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.hookspath(), "", "应回到未设置状态：\n%s" % r.stdout)
        self.assertNotIn("无法", r.stdout, "这是「原值就是未设置」，不是「没存档」：\n%s" % r.stdout)

    def test_status_shows_the_archived_original(self):
        """status 要能在**卸载之前**就把原值亮出来（否则用户无从判断卸完会变成什么）。"""
        self.install()
        r = _run([sys.executable, CLI, "hooks", "status", "--repo", self.repo])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(self.ORIGINAL, r.stdout,
                      "status 没显示安装前原值 —— 用户看不到卸载会还原成什么：\n%s" % r.stdout)


class TestUninstallPyMirrorDoesNotDrift(unittest.TestCase):
    """③ 兜底实现（uninstall.py）与主实现（cli.py）解析同一份存档必须同结论。

    uninstall.py 随安装包分发、跑在"包可能已经被卸掉"的时刻，所以不能 import 包内模块
    —— 只能镜像。镜像就会漂移，故这里既锁常量、也**拿同一份存档喂两个实现**对拍。
    """

    def test_same_constants(self):
        cli_src, un_src = _read(CLI), _read(UNINSTALL_PY)
        for line in ("BACKUP_DIRNAME = \"agentguard-original-hooks\"",
                     "PREV_HP_NAME = \"PREVIOUS-hooksPath.txt\"",
                     "PREV_HP_PREFIX = \"安装前 core.hooksPath：\"",
                     "PREV_HP_UNSET = \"<未设置>\""):
            self.assertIn(line, cli_src, "cli.py 缺常量：%s" % line)
            self.assertIn(line, un_src,
                          "uninstall.py 与 cli.py 的存档常量不一致（镜像漂移）：%s" % line)

    def test_both_readers_agree_on_the_same_archive(self):
        """同一份存档 → 两个实现必须给出同样的 (状态, 值)。"""
        sys.path.insert(0, os.path.join(ROOT, "agentguard"))
        try:
            from agentguard import cli as cli_mod     # noqa: F401
        except Exception:
            import cli as cli_mod                        # 无包上下文时裸导入（别的测试也这么干）
        import importlib.util
        spec = importlib.util.spec_from_file_location("_uninstall_under_test", UNINSTALL_PY)
        un_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(un_mod)

        cases = {
            "安装前 core.hooksPath：.myhooks\n": ("ok", ".myhooks"),
            "安装前 core.hooksPath：<未设置>\n": ("unset", None),
            "记录时间：x\n（没有任何「安装前」行）\n": ("none", None),
        }
        for text, want in cases.items():
            tmp = tempfile.mkdtemp(prefix="v13b6-mirror-")
            try:
                repo = os.path.join(tmp, "r")
                os.makedirs(repo)
                _run(["git", "init", "-q"], cwd=repo)
                d = os.path.join(repo, ".git", "agentguard-original-hooks")
                os.makedirs(d, exist_ok=True)
                with io.open(os.path.join(d, "PREVIOUS-hooksPath.txt"), "w",
                             encoding="utf-8") as f:
                    f.write(text)
                got_cli = cli_mod.read_prev_hookspath(repo)
                got_un = un_mod.prev_hookspath(repo)
                self.assertEqual(got_cli, want, "cli.py 解析结果不对：%r" % (got_cli,))
                self.assertEqual(got_un, want, "uninstall.py 解析结果不对：%r" % (got_un,))
                self.assertEqual(got_cli, got_un, "两个实现对同一份存档结论不同")
            finally:
                _rmtree_force(tmp)

    @unittest.skipUnless(_HAS_GIT, "需要 git 才能跑真卸载分支")
    def test_fallback_path_also_restores(self):
        """**行为锁**：强制走兜底分支（`harness_cli=None`，即 CLI 转发不了时），
        它必须同样把原值还原回去。

        为什么不用源码锁：首版写的是 `assertIn("prev_hookspath(repo)", src)` —— 而那句
        在**函数定义行** `def prev_hookspath(repo):` 里就有，于是把兜底分支整个退回
        「一律 --unset」它照样绿（变异验证实测：没咬住）。断言撞上同名定义 = 自证。
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location("_uninstall_under_test2", UNINSTALL_PY)
        un_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(un_mod)

        tmp = tempfile.mkdtemp(prefix="v13b6-fb-")
        try:
            repo = os.path.join(tmp, "endrepo")
            os.makedirs(repo)
            _run(["git", "init", "-q"], cwd=repo)
            _run(["git", "-C", repo, "config", "--local", "core.hooksPath", ".myhooks"])
            d = os.path.join(repo, ".git", "agentguard-original-hooks")
            os.makedirs(d, exist_ok=True)
            with io.open(os.path.join(d, "PREVIOUS-hooksPath.txt"), "w", encoding="utf-8") as f:
                f.write("安装前 core.hooksPath：.myhooks\n")

            class _A(object):
                pass

            a = _A()
            a.unhook = [repo]
            a.yes = True
            a.harness_cli = None          # ← 强制走兜底分支
            a.python = sys.executable
            un_mod.record = lambda *args, **kw: None   # 别把测试写进真运维台账
            fails = un_mod.step_hooks(a)
            self.assertEqual(fails, [], "兜底分支报错：%s" % fails)
            rc, out = _git(repo, "config", "--local", "--get", "core.hooksPath")
            self.assertEqual(out if rc == 0 else "", ".myhooks",
                             "兜底分支没还原原值（有 CLI 时还原、没 CLI 时清空 = 同一件事两种结果）")
        finally:
            _rmtree_force(tmp)


class TestEngineHooksCannotSelfDestruct(unittest.TestCase):
    """install_engine_hooks.sh 的自毁路径（中 17 附带查出）。

    两条实测事实（2026-09-16）：
      · `git rev-parse --git-path hooks` 尊重 core.hooksPath → 本仓返回共享分发器目录；
      · `cat x > x` 先把 x 截断 → 文件变 0 字节。
    旧版脚本两者都踩：会覆盖共享 pre-commit 分发器、并把共享 commit-msg 清空。
    """

    def test_does_not_use_git_path_hooks(self):
        code = _code_only(_read(ENGINE_HOOKS))
        self.assertNotIn("--git-path hooks", code,
                         "又用 git rev-parse --git-path hooks 定位目录了：该命令尊重 "
                         "core.hooksPath，会指向共享分发器 —— 写入即自毁")
        self.assertIn("--absolute-git-dir", code, "应改用 --absolute-git-dir + /hooks")

    def test_has_uninstall_and_fingerprint_guard(self):
        src = _read(ENGINE_HOOKS)
        self.assertIn("--uninstall", src,
                      "拷贝模式仍没有卸载路径（评审 v13 中 17 原文指出的那条）")
        self.assertIn("FP_PRE", src, "卸载不认指纹 = 可能删掉别人的 hook")

    @unittest.skipUnless(_HAS_GIT and _HAS_SH, "需要 git 与 sh")
    @_slow("真跑 install_engine_hooks.sh")
    def test_running_it_does_not_touch_the_shared_dispatcher(self):
        """实测跑一遍：共享分发器的指纹必须**逐字节不变**。

        这是本类唯一有真牙齿的断言 —— 上面两条是源码措辞锁，这条证明"跑了也没事"。
        不动 .git/hooks 里的旧副本：那是历史遗留，本脚本的 --uninstall 才负责它。

        结尾那条措辞断言**分状态**（2026-09-18 修）：脚本第 49 行读
        `git config --get core.hooksPath`，非空才打印「已由 hooksPath 接管」并早退。
        那是**本机 .git/config 的状态**（不进版本控制）—— 原写法无条件要求那行，
        于是本机（设了）绿、CI 干净 clone（没设）红：断言写死了开发机的配置。
        """
        before = {p: _sha1(os.path.join(SHARED, p)) for p in ("pre-commit", "commit-msg")}
        # 走哪条分支由**装之前**的 core.hooksPath 决定，所以要在跑脚本前读。
        _, hp_before = _git(ROOT, "config", "--get", "core.hooksPath")
        r = _run(["sh", ENGINE_HOOKS], cwd=ROOT)
        self.assertEqual(r.returncode, 0, "脚本跑挂了：%s" % (r.stdout + r.stderr))
        after = {p: _sha1(os.path.join(SHARED, p)) for p in ("pre-commit", "commit-msg")}
        self.assertEqual(before, after,
                         "共享分发器被改动了！输出：\n%s" % r.stdout)
        if hp_before:
            self.assertIn("core.hooksPath", r.stdout,
                          "本仓已设 hooksPath，该在输出里说明「已被 hooksPath 接管」，而不是闷声安装")
        else:
            self.assertIn("已安装", r.stdout,
                          "本仓没设 core.hooksPath（干净 clone 的常态）→ 该走拷贝安装并把装了什么说清；"
                          "输出=%s" % r.stdout[-300:])


def _sha1(p):
    import hashlib
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    unittest.main()
