# -*- coding: utf-8 -*-
"""评审 v13 批次 9（跨平台批次 3）：期望行尾三态（显式 / auto / 端默认）的回归锁。

缺陷原文（2026-09-18 实测）
================================================================================
`config/harness.yaml` 里 22 处 `line_ending:`（11 LF + 11 CRLF）是**维护机当年检出时的快照**，
而它的优先级高于事实：

    git -C <某个 H5 端仓> ls-files --eol | <数 w/ 列>
    #   → crlf=1745 / lf=29        ← 而该 gate 声明的是 line_ending: LF
    # 台账里那 1489 条 H5-CRLF 错误就是它的产物：**纯行尾误报，今天就在 Windows 上发生**

同事换盘符 / 换用户名 / 在 WSL 上 clone 只会更糟（Linux 检出是 LF，而 PC gate 声明 CRLF
→ 每个文件都被判"行尾异常"）。修法不是换个新值，是把"期望什么行尾"从**共享配置里的常量**
改成**按本机本仓的检出事实判定**（`harness/_eol_policy.py`，本批新增）。

本文件钉住七件事
================================================================================
  ① **显式值优先级最高、且不探不打**：`CRLF` / `LF` 的语义与接线前逐字节相同，
     并且**一个 git 子进程都不起**（判据：把 `_git` 换成会抛的桩，显式路径照样返回值）；
  ② **`auto` 的结论 == 该仓 `w/` 列实测多数**，含那个**逼出③的反例仓**
     （`core.autocrlf=false` 而工作树 100% CRLF —— 计划里"false → LF"的捷径在它身上必错）；
  ③ **`.gitattributes` 最优先**：`core.autocrlf=true` 而 `attr` 写 `eol=lf` 的仓，
     先看 autocrlf 会**当场判错**（本仓自己就是这个形态）；
  ④ **空仓不作断言**：`git init` 但从不 `git add` 的仓（= 本仓测试夹具的形态）必须落端默认，
     否则同一份夹具会在 Windows 解析成 CRLF、在 CI 上解析成 LF —— 这正是本批要消灭的差异；
  ⑤ **门槛与出声**：`w/` 多数 < 90% → `no-majority` → 端默认（防批量转换让门禁静默翻转）；
     出声判据是**结论 != 端默认**（外加 `no-majority` / `probe-failed` 这两条"探不出来"），
     其余静默 —— 每提交一行永不改变的话换来的不是"知道"而是"不再读"；
  ⑥ **与 `sandbox/eol_text.py` 不漂移**：同一份 `git ls-files --eol` 输出，它取 **`i/`（存成什么）**、
     本模块取 **`w/`（躺在盘上是什么）**，两边必须各自与 git 的对应列**逐字相符**；
  ⑦ **`config/harness.toml` 里的 gate 键必须真的还在**（见下）。

⑦ 是施工中真出过的事故，值得单说
================================================================================
删那 22 行 `line_ending:` 时，替换把该行**连同它与下一行之间的换行**一起吞了，
于是某个 gate 键粘到了上一行末尾 —— 在 YAML 里整行成了注释/值的一部分，
**该 gate 从配置里整个消失**。症状不报错而是"看起来配好了"：`--validate --structure-only`
实测从 34 个 gate 掉到 13 个，且 `script:` 全部串位（某个 gate 的脚本显示成
`checks/backend_layer_check.py`），`run_gate.py <某个 H5 gate> <文件>` 直接报「gate 未登记」。

所以 `TestConfigGateKeysAreIntact` 三条一起上：**粘连正则零命中** + **严格行扫描的键集 ==
加载后的 gate 集** + **`script` 与 gate 名后缀一致**。第三条是"后果锁"——它不问原因，
只问"这个 gate 的脚本是不是被换成了别人的"，而这正是那次事故最刺眼的症状。

为什么每个临时仓都显式 `git config core.autocrlf`
================================================================================
`_eol_policy` 读的正是这个键，而 `git config --get` 在仓里没设时会**回落到本机全局配置**。
不显式写死的话，这个文件会在"全局 autocrlf=true"的机器上绿、在别的机器上红 ——
那与它要锁的东西是同一类差异，用差异去锁差异等于没锁。
"""
from __future__ import unicode_literals

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _tmpclean import rmtree_force as _rmtree_force      # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
HARNESS_LIB = os.path.join(ROOT, "harness")
SANDBOX = os.path.join(ROOT, "sandbox")
CONFIG_TOML = os.path.join(ROOT, "config", "harness.toml")

sys.path.insert(0, HARNESS_LIB)
import _eol_policy as eol                                    # noqa: E402

#: 去重记账就存在这个环境变量里（`_config._policy_notice_once`），复位"已说过的提示"= 清掉它
_NOTICE_ENV = "HARNESS_POLICY_NOTICE_SHOWN"


def _load_sandbox(name, filename):
    """按路径加载 `sandbox/` 下的模块（它不是包）。取**真身**而不是抄一遍逻辑。"""
    spec = importlib.util.spec_from_file_location(name, os.path.join(SANDBOX, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


et = _load_sandbox("_eol_text_under_test", "eol_text.py")


@contextlib.contextmanager
def _stderr():
    """捕获 stderr —— `_policy_notice_once` 是**调用时**才 `sys.stderr.write`，所以拦得住。"""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        yield buf


def _no_notice_env():
    os.environ.pop(_NOTICE_ENV, None)


class _RepoCase(unittest.TestCase):
    """真临时仓基类 —— `git ls-files --eol` 的结论只认**真仓**，桩不出这个东西。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="eol-lock-")
        self.addCleanup(_rmtree_force, self.tmp)
        eol._CACHE.clear()
        self._env_old = os.environ.get(_NOTICE_ENV)
        _no_notice_env()
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._env_old is None:
            os.environ.pop(_NOTICE_ENV, None)
        else:
            os.environ[_NOTICE_ENV] = self._env_old

    def git(self, repo, *args):
        return subprocess.run(["git"] + [str(a) for a in args], cwd=repo,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=60).stdout.decode("utf-8", "replace")

    def repo(self, name="r", autocrlf="false", attr=None, crlf=3, lf=0,
             body=b"line1\nline2\n"):
        """造一个受管仓：`crlf` 个文件的工作树写成 CRLF、`lf` 个写成 LF（**索引一律 LF**）。

        `autocrlf` 必须显式给（见模块 docstring 末节）；`attr` 非 None 时写 `.gitattributes`
        —— 注意它本身也会进统计（它自己永远是 `w/lf`）。
        """
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        self.git(d, "init", "-q")
        self.git(d, "config", "user.email", "lock@example.invalid")
        self.git(d, "config", "user.name", "lock")
        self.git(d, "config", "core.autocrlf", autocrlf)
        if attr:
            with open(os.path.join(d, ".gitattributes"), "wb") as f:
                f.write(attr.encode("utf-8"))
        names = ["f%02d.txt" % i for i in range(crlf + lf)]
        for n in names:
            with open(os.path.join(d, n), "wb") as f:
                f.write(body)
        self.git(d, "add", "-A")
        self.git(d, "commit", "-qm", "init")
        # 提交**之后**改写工作树：索引侧停在 LF，于是得到"索引 LF / 工作树 CRLF"的真形态
        for n in names[:crlf]:
            with open(os.path.join(d, n), "wb") as f:
                f.write(body.replace(b"\n", b"\r\n"))
        return d

    def empty_repo(self, name="empty"):
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        self.git(d, "init", "-q")
        return d


# ===================================================== ① 显式值：优先级最高且不探不打
class TestDeclaredWins(_RepoCase):
    """显式 `CRLF` / `LF` 的语义与接线前**逐字节相同** —— 这是一条不许动的兼容承诺。"""

    def test_declared_beats_the_repo_s_actual_majority(self):
        """仓里实测 100% CRLF，声明 LF → 答案是 LF。**优先级高于事实**是设计如此。"""
        d = self.repo(crlf=3)
        self.assertEqual(eol.eol_majority(d)[0], "CRLF", "前提：这个仓实测就是 CRLF 多数")
        self.assertEqual(eol.resolve(d, {"line_ending": "LF"}, "CRLF", "t"),
                         ("LF", "declared"))

    def test_declared_does_not_probe_at_all(self):
        """把 `_git` 换成会炸的桩：显式路径**必须照样返回**（证明它一个子进程都没起）。

        这是"不探不打"的唯一硬证据 —— 光看耗时会被缓存和机器快慢盖过去。
        """
        d = self.repo(crlf=3)
        boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该探测"))  # noqa: E731
        old = eol._git
        eol._git = boom
        try:
            self.assertEqual(eol.resolve(d, {"line_ending": "CRLF"}, "LF", "t"),
                             ("CRLF", "declared"))
            # 阴性对照：同一个桩下 auto **必须**炸进 probe-failed 分支 —— 证明桩是有效的，
            # 上面那条不是因为桩没被调用上而侥幸通过。
            self.assertEqual(eol.resolve(d, {}, "LF", "t"), ("LF", "endpoint-default"))
        finally:
            eol._git = old

    def test_declared_is_case_and_space_insensitive(self):
        d = self.repo(crlf=1)
        for raw in (" crlf ", "crlf", "CRLF", "CrLf"):
            self.assertEqual(eol.resolve(d, {"line_ending": raw}, "LF", "t"),
                             ("CRLF", "declared"), "取值=%r" % raw)

    def test_absent_or_auto_or_bogus_means_probe(self):
        """"没写" = `auto`；**写错的**（`AUTO2`）在运行期也不许崩，照样走探测。

        值域拦截是 `run_gate.py --validate` 的活（rc=2，见 `TestConfigGateKeysAreIntact`
        隔壁那条）—— 运行期拿不准就按 auto 处理，绝不因为一个错值把门禁打挂。
        """
        d = self.repo(crlf=3)
        for cfg in ({}, None, {"line_ending": "auto"}, {"line_ending": "AUTO2"}):
            value, source = eol.resolve(d, cfg, "CRLF", "t")
            self.assertEqual((value, source), ("CRLF", "checkout-majority"), "配置=%r" % (cfg,))


# ===================================================== ② auto == 该仓 w/ 列实测多数
class TestAutoMatchesWorktreeReality(_RepoCase):
    """`auto` 的判据是 **git 的检出配置**，不是 `os.name`。"""

    def test_counterexample_autocrlf_false_but_worktree_is_crlf(self):
        """计划里"`autocrlf=false` → LF"的捷径，被一个真实的跨端仓证伪。

        实测该仓 `core.autocrlf=false` 而工作树 1072 `w/crlf` / 0 `w/lf`。原因：`false` 的含义
        是"git 不做任何转换"，于是工作树 = 索引内容 —— 只有 `ls-files` 答得上来。落端默认
        就正好是那个仓的假红。
        """
        d = self.repo(autocrlf="false", crlf=3)
        self.assertEqual(eol.resolve(d, {}, "LF", "t"), ("CRLF", "checkout-majority"))

    def test_autocrlf_input_is_a_guarantee_of_lf(self):
        d = self.repo(autocrlf="input", crlf=0, lf=3)
        self.assertEqual(eol.resolve(d, {}, "CRLF", "t"), ("LF", "git-config"))

    def test_autocrlf_true_is_a_guarantee_about_checkout_not_about_existing_bytes(self):
        """**已知边界，钉住而不是假装没有**：`true` 时结论恒为 CRLF。

        它是 git 对"**被检出的文本文件**"的保证；手工在仓里写过 LF 的临时仓并不满足它
        （字节是手写的，没经过 checkout）。真 clone 的工作树必是 CRLF，所以这不是本仓形态；
        而代价换来的是 0.21s 对 1.74s（实测某个 H5 仓：无 pathspec 的 `ls-files --eol` 要 1.5s+）。
        """
        d = self.repo(autocrlf="true", crlf=0, lf=3)
        self.assertEqual(eol.worktree_eol_map(d)["f00.txt"], "lf", "前提：工作树确实是 LF")
        self.assertEqual(eol.resolve(d, {}, "LF", "t"), ("CRLF", "git-config"))


# ===================================================== ③ .gitattributes 最优先
class TestGitattributesFirst(_RepoCase):
    """先看 autocrlf 会**当场判错** —— 本仓自己就是那个形态（`autocrlf=true` + `eol=lf`）。"""

    def test_gitattributes_wins_over_autocrlf(self):
        d = self.repo(autocrlf="true", attr="* text eol=lf\n", crlf=0, lf=3)
        self.assertEqual(eol.resolve(d, {}, "CRLF", "t"), ("LF", "checkout-majority"))

    def test_worktree_drifted_from_gitattributes_is_flagged_not_silently_resolved(self):
        """`.gitattributes` 说 `eol=lf`，工作树却被人手改写成 CRLF → **不许**静默给出结论。

        3 个 CRLF + `.gitattributes` 自己 `w/lf` = 3/4 = 0.75 < 0.90 → `no-majority` →
        端默认，**并且出声**（这一条属于"探到了东西却判不出来"，与"空仓"不同）。
        """
        d = self.repo(autocrlf="true", attr="* text eol=lf\n", crlf=3)
        value, counts, total = eol.eol_majority(d)
        self.assertIsNone(value, "3/4 不该判成多数：%r" % (counts,))
        _no_notice_env()
        with _stderr() as err:
            self.assertEqual(eol.resolve(d, {}, "LF", "t"), ("LF", "endpoint-default"))
        self.assertIn("no-majority", err.getvalue())


# ===================================================== ④ 空仓不作断言
class TestEmptyAndMissingRepos(_RepoCase):
    """`git init` 但从不 `git add` —— 本仓测试夹具（`test_entry_level.py::_Fixture`）正是这形态。"""

    def test_empty_repo_falls_back_to_endpoint_default_for_both_ends(self):
        d = self.empty_repo()
        self.assertFalse(eol.has_tracked(d))
        self.assertEqual(eol.resolve(d, {}, "CRLF", "t"), ("CRLF", "endpoint-default"))
        self.assertEqual(eol.resolve(d, {}, "LF", "t"), ("LF", "endpoint-default"))

    def test_empty_repo_and_missing_path_are_silent(self):
        """`no-tracked` / `no-repo` **不出声**：它们不是"行尾这件事有异常"。

        天天报只会把人训成不看，而这一行存在的全部意义就是"看见它时该反应过来"。
        两者另有各自的信号（`[config-warn]` / 夹具本身）。
        """
        for repo in (self.empty_repo(), os.path.join(self.tmp, "nope")):
            _no_notice_env()
            with _stderr() as err:
                self.assertEqual(eol.resolve(repo, {}, "LF", "t")[1], "endpoint-default")
            self.assertEqual(err.getvalue(), "", "不该出声：%r" % err.getvalue())


# ===================================================== ⑤ 门槛与出声
class TestMajorityThresholdAndNotice(_RepoCase):
    """90% 门槛防的是**批量转换让门禁静默跟着翻转**（`git add --renormalize .`）。"""

    def test_boundary_is_inclusive(self):
        """恰好 90%（9/10）算多数 —— `>=` 是本仓明写在 `MAJORITY_THRESHOLD` 的口径。"""
        d = self.repo(autocrlf="false", crlf=9, lf=1)
        value, counts, total = eol.eol_majority(d)
        self.assertEqual((value, total), ("CRLF", 10), counts)
        self.assertAlmostEqual(counts["crlf"] / float(total), 0.9, places=6)

    def test_below_threshold_falls_back_and_speaks_up(self):
        d = self.repo(autocrlf="false", crlf=8, lf=2)
        self.assertEqual(eol.eol_majority(d)[0], None, "8/10 = 0.8 不该判成多数")
        _no_notice_env()
        with _stderr() as err:
            self.assertEqual(eol.resolve(d, {}, "CRLF", "t"), ("CRLF", "endpoint-default"))
        self.assertIn("no-majority", err.getvalue())

    def test_silent_when_equal_to_endpoint_default_and_loud_when_it_differs(self):
        """出声判据 = **结论 != 端默认**（= "这个 gate 的期望值因你这台机器而变了"）。

        同一个仓、同一份字节，只换端默认：PC 侧（CRLF）静默、H5 侧（LF）出声 ——
        某个 H5 仓那 1489 条误报的成因就是后者，而它是同事最需要看见的那一句。
        """
        d = self.repo(autocrlf="true", crlf=3)
        _no_notice_env()
        with _stderr() as err:
            self.assertEqual(eol.resolve(d, {}, "CRLF", "t")[0], "CRLF")
        self.assertEqual(err.getvalue(), "", "结论 == 端默认时不该出声")

        _no_notice_env()
        with _stderr() as err:
            self.assertEqual(eol.resolve(d, {}, "LF", "t")[0], "CRLF")
        self.assertIn("[config-eol]", err.getvalue())
        self.assertIn("期望行尾 CRLF", err.getvalue())

    def test_notice_is_deduplicated_then_returns_when_the_value_changes(self):
        """去重靠环境变量（不是永久闩锁），且 key 里**带值** —— 翻转会重新报一次。"""
        d = self.repo(autocrlf="true", crlf=3)
        _no_notice_env()
        with _stderr() as err:
            eol.resolve(d, {}, "LF", "t")
            first = os.environ.get(_NOTICE_ENV, "")
            eol.resolve(d, {}, "LF", "t")
            second = os.environ.get(_NOTICE_ENV, "")
        self.assertEqual(err.getvalue().count("[config-eol]"), 1, "同一结论只该报一次")
        self.assertTrue(first, "去重记账应写进环境变量（跨进程去重的载体）")
        self.assertEqual(first, second, "第二次调用不该再往记账里加东西")

        _no_notice_env()                       # 复位 = 清掉环境变量，等于换了个进程
        with _stderr() as err:
            eol.resolve(d, {}, "LF", "t")
        self.assertEqual(err.getvalue().count("[config-eol]"), 1)


# ===================================================== 探测失败
class TestProbeFailure(_RepoCase):
    def test_probe_failure_falls_back_to_endpoint_default_and_says_so(self):
        """探不出来是**正常结局**（没有 git / 不是仓库），不该把门禁打挂。"""
        d = self.repo(crlf=3)
        old = eol._git
        eol._git = lambda *a, **k: (_ for _ in ()).throw(OSError("no git"))
        try:
            _no_notice_env()
            with _stderr() as err:
                self.assertEqual(eol.resolve(d, {}, "LF", "t"), ("LF", "endpoint-default"))
        finally:
            eol._git = old
        self.assertIn("probe-failed", err.getvalue())


# ===================================================== ⑥ 与 sandbox/eol_text.py 不漂移
class TestNoDriftWithSandboxEolText(_RepoCase):
    """两个**不同**的问题，共用同一份 `git ls-files --eol` 输出，解析必须不漂移。

    `eol_text.index_eol_map()` 取 `i/`（git 会把它**存**成什么 —— 打包/发布语义）；
    `_eol_policy.worktree_eol_map()` 取 `w/`（它现在**躺**在盘上是什么 —— 行尾门禁语义）。
    行尾门禁读的是磁盘字节，所以必须比工作树侧：拿索引侧比，某个 H5 仓那 1732 个 CRLF 照样全红。
    """

    def test_both_sides_match_git_own_columns_verbatim(self):
        d = self.repo(autocrlf="false", crlf=3)
        eol._CACHE.clear()

        raw = self.git(d, "ls-files", "--eol")
        gitmap = {}
        for line in raw.splitlines():
            head, tab, path = line.partition("\t")
            if tab:
                toks = head.split()
                gitmap[path] = (toks[0][2:], toks[1][2:])   # (i/ 侧, w/ 侧)
        self.assertTrue(gitmap, "前提：git 得给出 --eol 输出；拿到=%r" % raw)

        idx = et.index_eol_map(d)              # 签名：(root, quiet=False)
        wt = eol.worktree_eol_map(d)
        self.assertEqual(len(gitmap), 3)
        for path, (i_side, w_side) in gitmap.items():
            self.assertEqual(i_side, "lf", "前提：索引侧确实是 LF（%s）" % path)
            self.assertEqual(w_side, "crlf", "前提：工作树侧确实是 CRLF（%s）" % path)
            self.assertEqual(idx.get(path), i_side, "eol_text 与 git 的 i/ 列不符：%s" % path)
            self.assertEqual(wt.get(path), w_side, "_eol_policy 与 git 的 w/ 列不符：%s" % path)

        # 两个实现给出**不同**的答案 —— 这不是重复实现，是回答了不同的问题。
        self.assertNotEqual(idx, wt)

    def test_module_does_not_import_eol_text(self):
        """`import eol_text` 会踩两条硬约束：`tests/test_stdlib_only.py` 判"疑似第三方"、
        `checks/harness_self_check.py` 明文写着"引擎不应依赖 sandbox"。也放不进 sandbox/
        —— `tests/_engine_copy.py` 只拷 `checks/ harness/ config/` 三棵子树。

        判据**必须另起一个干净解释器**：`sys.modules` 是本进程全局的，全套 discover 一起跑时
        别的模块会**正当地**加载 `eol_text`（`tests/test_pack_eol.py` 就在干这个）。就地断言
        「它不在 sys.modules 里」等于把别人的合法行为算到自己头上 —— 首轮全量实测就是这么红的
        （单跑本文件绿、全套红：典型的进程共享污染，锁错了对象）。
        这里要问的是「**引擎模块自己**会不会把它拖进来」，那只能在没被污染的进程里问。
        """
        probe = (
            "import sys, os\n"
            "sys.path.insert(0, os.path.join(%r, 'harness'))\n"
            "import _eol_policy\n"
            "print('EOL_TEXT_LOADED' if 'eol_text' in sys.modules else 'CLEAN')\n" % ROOT
        )
        r = subprocess.run([sys.executable, "-c", probe],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out = r.stdout.decode("utf-8", "replace")
        self.assertIn("CLEAN", out,
                      "引擎层 `_eol_policy` 把 sandbox/eol_text 拖进来了：out=%r err=%r"
                      % (out, r.stderr.decode("utf-8", "replace")))

        # 阴性对照：探针本身得**认得出**"真 import 了"的形态，否则上面那句是空断言。
        r2 = subprocess.run([sys.executable, "-c", probe + (
            "sys.path.insert(0, os.path.join(%r, 'sandbox'))\n"
            "import eol_text\n"
            "print('EOL_TEXT_LOADED' if 'eol_text' in sys.modules else 'CLEAN')\n" % ROOT)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertIn("EOL_TEXT_LOADED", r2.stdout.decode("utf-8", "replace"),
                      "对照失败：探针连『真的加载了』都认不出来，那它证明不了任何事")


# ===================================================== ⑦ 配置与文档
class TestConfigGateKeysAreIntact(unittest.TestCase):
    """⑦ 那次事故的锁。**症状不报错，而是"看起来配好了"** —— 所以三条一起上。

    2026-10-21 配置迁到 TOML 后，判据跟着格式走：gate 键 = `[gates.<name>]` 表头，
    粘连 = 表头前面还挂着别的内容（那样的文件不是合法的 TOML，或整段被吃进了上一行）。
    """

    def _lines(self):
        with open(CONFIG_TOML, encoding="utf-8") as f:
            return f.read().split("\n")

    def test_no_gate_key_is_glued_onto_the_previous_line(self):
        glued = []
        for i, l in enumerate(self._lines()):
            if l.lstrip().startswith("#"):
                continue
            m = re.search(r"\[gates\.[A-Za-z0-9_]+\]", l)
            if m and l[:m.start()].strip():
                glued.append((i + 1, l))
        self.assertEqual(glued, [], "gate 表头被粘到别的内容后面：%r" % glued)

    def test_strict_line_scan_agrees_with_the_loaded_gate_set(self):
        """严格行扫描（只认 `^[gates.key]`）与配置加载器必须数出**同一批** gate。

        两者不一致 = 文件里有键是加载器看不见的，而那正是粘连的后果。
        """
        keys = []
        for l in self._lines():
            m = re.match(r"^\[gates\.([A-Za-z0-9_]+)\]\s*$", l)
            if m:
                keys.append(m.group(1))
        self.assertGreaterEqual(len(keys), 2, "gate 数掉到这个量级，八成是整段被吃掉了")

        sys.path.insert(0, HARNESS_LIB)
        import _config
        loaded = set((_config.load(CONFIG_TOML).get("gates") or {}))
        self.assertEqual(set(keys), loaded,
                         "文件里的键与加载后的 gate 集不一致：只在文件里=%s；只在配置里=%s"
                         % (sorted(set(keys) - loaded), sorted(loaded - set(keys))))

    def test_each_gate_script_matches_its_name_suffix(self):
        """**后果锁**：不问原因，只问"这个 gate 的脚本是不是被换成了别人的"。

        事故现场就是某个 gate 的 `script` 显示成 `checks/backend_layer_check.py` ——
        因为它的 `repo:`/`script:` 两行在解析里并到了前一个 gate 身上。
        """
        import tomllib
        with open(CONFIG_TOML, "rb") as f:
            gates = tomllib.load(f).get("gates") or {}
        want = {"example": "example_code_check.py"}
        seen = 0
        for name, g in gates.items():
            if name == "harness_self":
                continue
            self.assertIn(name, want, "gate 名 %r 没有可判定的脚本，本锁需要跟着更新" % name)
            self.assertEqual(os.path.basename(g.get("script") or ""), want[name],
                             "%s 的 script 不该是 %r" % (name, g.get("script")))
            seen += 1
        self.assertGreaterEqual(seen, 1, "只判到 %d 个 gate，锁可能已空转" % seen)

    def test_no_gate_declares_line_ending(self):
        """那 22 行是**维护机当年检出的快照**，删掉是目的本身 —— 别加回来。

        真要锁死某个值，显式写 `CRLF` / `LF` 仍然有效（见 `TestDeclaredWins`），
        但前提是**仓里确实如此**；本机五个业务仓实测：`autocrlf=true` 的四个检出 CRLF、
        `autocrlf=false` 的 `hy_h5` 也是 CRLF（1094/1096），而它们的端默认并不都是 CRLF。
        """
        hits = [(i + 1, l) for i, l in enumerate(self._lines())
                if re.match(r"^\s*line_ending\s*=", l)]
        self.assertEqual(hits, [], "共享配置里不该再出现 line_ending（机器事实不进共享文件）：%r" % hits)

    def test_validate_rejects_a_bogus_line_ending_value(self):
        """值域 `{auto, CRLF, LF}`；写别的 → `--validate` rc=2。

        在这一批之前 `line_ending` **什么校验都没有**：写 `auto` 被静默接受（那时它落到
        下面的 else 分支等于 CRLF，没人发现），拼错同样静默 —— 而三个取值的语义差别很大
        （auto=按本机检出判定；显式值=锁定），拼错不是"用了个奇怪的值"，
        而是**写的人以为锁住了、实际没有**。

        gate 名**刻意不注册**（`probe_eol_gate`，同 `test_review_v13_batch4` 的 `probe_gate`）：
        真名会撞上 `_config` 的策略覆盖保护 —— 本地覆盖层不许改已注册 gate 的策略字段，
        覆盖会被静默丢掉，夹具就构造不出被测场景。**阴阳两向**：同一条 gate 写 `auto` 必须不报。
        """
        tmp = tempfile.mkdtemp(prefix="eol-cfg-")
        self.addCleanup(_rmtree_force, tmp)

        def run(value, enabled=True):
            cfg = os.path.join(tmp, "local-%s.yaml" % value)
            with open(cfg, "w", encoding="utf-8") as f:
                f.write("gates:\n  probe_eol_gate:\n    enabled: %s\n"
                        "    script: checks/example_code_check.py\n    line_ending: %s\n"
                        % ("true" if enabled else "false", value))
            # `HARNESS_LOG_FILE` / `HARNESS_STATS_FILE` 必须**指到临时目录**：
            # 不指的话 `run_gate` 会把本次探针写进真实的 `reports/gate_log.jsonl`
            # （第四轮 N-2 实测污染过 124 条，因此 `tests/test_coverage.py:400` 有一条源码级锁
            # 盯着"调 run_gate 的测试文件必须出现 HARNESS_LOG_FILE"这个串 —— 首轮全量就是
            # 这么红的：本文件当时只设了 HARNESS_CONFIG）。按契约设，别只为了过锁。
            r = subprocess.run([sys.executable, os.path.join(HARNESS_LIB, "run_gate.py"),
                                "--validate", "--structure-only"],
                               cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               env=dict(os.environ, HARNESS_CONFIG=cfg,
                                        HARNESS_LOG_FILE=os.path.join(tmp, "gate_log.jsonl"),
                                        HARNESS_STATS_FILE=os.path.join(tmp, "stats.json")),
                               timeout=180)
            return r.returncode, r.stdout.decode("utf-8", "replace")

        rc_bad, out_bad = run("AUTO2")
        self.assertEqual(rc_bad, 2, "非法值必须阻断（rc=2）；输出=%s" % out_bad[-700:])
        self.assertIn("line_ending 非法", out_bad)

        # 合法值这一向用 disabled gate：示例脚本不消费 line_ending（它只由端脚本的
        # _resolve_eol 读取），enabled 时会额外命中「空承诺」检查 —— 那是另一条判据，
        # 会让「值域合法」这个结论混进无关的红。值域检查本身与 enabled 无关。
        rc_ok, out_ok = run("auto", enabled=False)
        self.assertNotIn("line_ending 非法", out_ok, "合法值不该被报：%s" % out_ok[-400:])
        self.assertEqual(rc_ok, 0, "`auto` 是默认值，必须放行；输出=%s" % out_ok[-700:])

    def test_readme_documents_the_three_states(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        self.assertIn("line_ending", readme,
                      "README 必须保留 line_ending 这个键（键仍在，只是默认变了）")
        # 2026-10-21 配置迁到 TOML 后，示例行的形态从 `line_ending:` 变成 `line_ending =`。
        row = [l for l in readme.split("\n") if re.match(r"^\s*line_ending\s*=", l)]
        self.assertEqual(len(row), 1, "配置示例里该键应恰好一行")
        self.assertIn("auto", row[0])
        self.assertIn("CRLF", row[0])
        self.assertIn("LF", row[0])


# ===================================================== ⑧ 防护 3：行尾来源落进台账
#: 台账探针要压住的环境变量。`HARNESS_LOG_FILE` 必须**指到临时目录**：本文件这几条会真起
#: run_gate 子进程，不指的话判定记录会写进真实的 `reports/gate_log.jsonl`
#: （第四轮 N-2 实测污染过 124 条，`tests/test_coverage.py:400` 有源码级锁盯着这个串）。
_LEDGER_ENV = ("HARNESS_CONFIG", "HARNESS_LOG_FILE", "HARNESS_ALLOW_POLICY_OVERRIDE", _NOTICE_ENV)


class TestEolSourceInLedger(_RepoCase):
    """plan §3.5(c) 防护 3：`resolve()` 一直把来源算出来了又扔掉，这里把它钉进台账。

    为什么这几条必须**端到端**（真起 run_gate 子进程 + 读回台账文件），而不单测 `emit_stats()`：
    这条通道有**两半** —— 子进程把结论写进 `HARNESS_STATS_FILE`，父进程 `_read_stats()` 读回、
    `_log_run` 落键。单测 `emit_stats` 只证明得了前一半：父进程读没读、落没落、键叫什么名字，
    全不在它的视野里。"两半各自绿、中间是断的"正是本仓反复记过的形态，而它**只在端到端
    跑得出来**。取舍同 `test_hooks_chain.py` 那两条 `--list` 锁：只在端到端显现的性质，
    就不该挂 `_slow` 把它从唯一需要它的场景里拿掉。

    实测每例 0.34~0.70s（一条 run_gate 子进程 + 一次 `/tmp` 工程副本），**没有任何一例 ≥1s**，
    所以按 `@_slow` 的判据（整文件 >5s **且** 存在 ≥1s 的用例）不挂 —— 后者不成立。

    刻意**不是**「每行都挂一个来源」：没有行尾这个概念的地方（backend / harness_self）、
    没跑过的 gate，一律不落键，由下面两条阴性对照看住。
    """

    GATE = "example"                    # 共享配置里 enabled: true 的示例 gate

    def _ledger_path(self):
        return os.path.join(self.tmp, "gate_log.jsonl")

    def _row(self, repo, gate=None, engine=None, extra_env=None):
        """真起一次 run_gate，读回台账最后一行。→ `(rc, 输出, 行)`；没落行则为 `{}`。"""
        ledger = self._ledger_path()
        if os.path.exists(ledger):
            os.remove(ledger)
        env = dict(os.environ)
        for k in _LEDGER_ENV:
            env.pop(k, None)
        env["HARNESS_LOG_FILE"] = ledger
        if extra_env:
            env.update(extra_env)
        eng = engine or ROOT
        r = subprocess.run([sys.executable, os.path.join(eng, "harness", "run_gate.py"),
                            gate or self.GATE, "--repo", repo],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300, env=env)
        out = (r.stdout or "") + (r.stderr or "")
        rows = []
        if os.path.exists(ledger):
            with io.open(ledger, encoding="utf-8") as f:
                rows = [json.loads(l) for l in f if l.strip()]
        return r.returncode, out, (rows[-1] if rows else {})

    def _override(self, body, name="local.yaml"):
        p = os.path.join(self.tmp, name)
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p

    # 阳性用例（checkout-majority / declared / resolve-failed）依赖 PC/H5 检查脚本里
    # `_resolve_eol()` 的接线；开源裁剪后已无这类脚本。_eol_policy.resolve() 本身
    # 仍由 TestDeclaredWins / TestAutoMatchesWorktreeReality 等类直接覆盖。
    # ---------- 阴性：没有这个概念 / 没跑过，就不许留痕 ----------
    def test_gate_without_eol_concept_has_no_eol_keys(self):
        """`harness_self` 压根不走行尾 → 台账里**不该有这一项**。

        防的是"给每条都编一个来源"：那一栏一旦每行都有，它就退化成噪音，
        真正的异常反而看不见了（与 `weakened` 同一条纪律）。
        """
        repo = self.empty_repo("self")
        rc, out, row = self._row(repo, gate="harness_self")
        self.assertEqual(rc, 0, out[-600:])
        self.assertTrue(row, "没落行的话本锁空转：%s" % out[-600:])
        self.assertEqual([k for k in row if k.startswith("eol")], [],
                         "不涉及行尾的 gate 不该有 eol_* 键：%s" % row)

    def test_skipped_gate_has_no_eol_keys(self):
        """本机关掉的 gate 根本没跑检查 → 子进程没说过话，台账就不许替它说。

        这一条同时是"不填的规则是硬的"的阴性对照：`skipped` 那一行**只有**跳过痕迹，
        没有行尾来源 —— 因为解析根本没发生过。
        """
        repo = self.repo("skip", autocrlf="false", crlf=6, lf=0)
        cfg = self._override("gates:\n  %s:\n    enabled: false\n" % self.GATE)
        rc, out, row = self._row(repo, extra_env={"HARNESS_CONFIG": cfg})
        self.assertEqual(rc, 0, "本机关 gate 是允许的动作：%s" % out[-600:])
        self.assertTrue(row, out[-600:])
        self.assertTrue(row.get("skipped"), "没跑成的话连跳过痕迹都得在：%s" % row)
        self.assertEqual([k for k in row if k.startswith("eol")], [],
                         "没跑过的 gate 不许有行尾来源：%s" % row)

    # resolve-failed → 台账 这一用例同样依赖端脚本的 `_resolve_eol()` 接线；
    # 半残部署的失败分支由 `_eol_policy.resolve()` 自身的行为锁覆盖（见 TestProbeFailure）。


if __name__ == "__main__":
    unittest.main()
