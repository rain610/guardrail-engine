# -*- coding: utf-8 -*-
"""跨平台批次 7：仓库登记表（repos 段的**键名**）与「本机没配路径」必须分开判。

缺陷原文（2026-09-18 实测，见 `reports/_batch6_partial_local_finding.txt`）
================================================================================
批次 2 把 33 条机器路径从共享 `config/harness.toml` 下沉到各人的
`config/harness.local.yaml` —— 方向是对的，但它**顺手把键名一起删了**。
于是「这个键在配置里根本没声明过」与「我这台机器没有这个仓」塌成了同一件事
（都是「键不在 repos 段里」）。而剩下的那根判据 `_machine`（本机有没有机器路径）
是按**维护机形态**校准的：那里 33 个键全在，所以「有未定义的键」一旦成立就真是拼错。

同事机上是另一个形态，而且是**最常见**的那一个 —— `docs/packaging/INSTALL.md` 明写
「编辑时只写你机器上**确实存在**的那几个仓即可」，`config/harness.local.example.toml`
的「场景 A」模板也只给 3 个键。实测（只填 1 条）：

    harness_self → 32 行 `❌ ⑤ gate <X> 引用未定义的 repo` → rc=1
    提交期钩子再把它转成 fail-closed rc=2 → **该同事每次提交都被拦，而他自己的
    代码一个字节都没被检查过**（拦在自检这一步，根本走不到他的 .vue）。

同一次输出里还自相矛盾：`⚠ gate empty_pc 的 repo 路径本机不存在 —— 不阻断` 就打在
`❌ ⑤ gate zhongdj_pc 引用未定义的 repo` 上面 —— 现实里这两句说的是同一件事。
维护机上看不到这一切（那里 33 个键全在），所以它在批次 2~6 之间一直没人发现；
是批次 6 的 WSL 复验第一次造出「配了 1 条」这个中间形态才当场红的
（此前几轮 WSL 跑的是「一条都没配」那个形态 → 走汇总 ⚠ → 全绿）。

修法：**键名表还给共享配置**（33 个键、值一律留空），两根判据各归各位 ——
    · 键**不在** repos 段 → 真配置错（拼错 / 新 gate 漏登记），任何机器上都阻断；
    · 键在、值空或路径不存在 → 「本机没这个仓」，常态，不阻断。

本文件钉住四件事
================================================================================
  ① **键名表在共享配置里，且不留死键**（每个 gate 的 repo 键都声明了 / 每个声明的键
     都有 gate 在用）—— 这是事故的根因，清空 repos 段这个动作必须当场红；
  ② **只配一半不阻断**：`harness_self` rc=0、`--validate` rc=0，且未配的那些 gate
     仍然**看得见**（⊘ 逐条列出，不是静默放行）；
  ③ **拼错的键照旧阻断**：新 gate 引用未声明的键 → `harness_self` rc=1、`--validate` rc=2。
     这一支是**保住检测力**的那一半 —— 本仓为它专门有 M43 那条变异（gate 键粘连 →
     34 个 gate 掉到 13 而**不报错**），修 ② 时不能把 ③ 一起修哑；
  ④ **口径只有一处**：`_config._machine_keys` 数的是「**值非空**的键」。批次 7 之前数的是
     「键在不在」，而键名回到共享配置之后那个口径恒为真 —— 它会把一份只填了 1 条的
     local.yaml 判成「本机有机器路径」，这正是事故的机制（M46 咬的就是这一行）。

为什么 ②③ 的锁必须是端到端的
================================================================================
⑤ 是 `checks/harness_self_check.py` 里的一段，跑在**子进程**里，而它就是那次事故的现场。
只测 `_config` 里的函数会漏掉「谁在读这份配置、读到的结论是什么」—— 那正是批次 3 的
M44 变异教过的同一件事（两半各自绿、中间是断的）。故 ②③ 各起两次真引擎，
用 `HARNESS_CONFIG` 喂一份夹具配置。
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
HARNESS = os.path.join(ROOT, "harness")

# 台账/统计/覆盖层一律隔离到临时目录：本文件会起真引擎，而真引擎默认往
# reports/gate_log.jsonl 追加（tests/test_coverage.py 对这件事有源码级锁）。
_ENV_STRIP = ("HARNESS_CONFIG", "HARNESS_LOG_FILE", "HARNESS_STATS_FILE",
              "HARNESS_ALLOW_POLICY_OVERRIDE", "HARNESS_POLICY_NOTICE_SHOWN")


def _run_gate(args, config_path=None):
    """起一次真引擎（子进程）。返回 `(rc, 输出)`；输出合并 stdout+stderr（结论两侧都有）。"""
    tmp = tempfile.mkdtemp(prefix="repo-key-reg-")
    try:
        env = dict(os.environ)
        for k in _ENV_STRIP:
            env.pop(k, None)
        env["HARNESS_LOG_FILE"] = os.path.join(tmp, "gate_log.jsonl")
        env["HARNESS_STATS_FILE"] = os.path.join(tmp, "stats.json")
        if config_path:
            env["HARNESS_CONFIG"] = config_path
        r = subprocess.run([sys.executable, os.path.join(HARNESS, "run_gate.py")] + list(args),
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=300, env=env, cwd=ROOT)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    finally:
        _rmtree_force(tmp)


class _FixtureCase(unittest.TestCase):
    """一个临时目录：放夹具 local 配置与被指过去的「端仓」目录。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="repo-key-fix-")
        self.repo = os.path.join(self.tmp, "endrepo")        # 假装是同事那个仓
        os.makedirs(self.repo)
        self.addCleanup(_rmtree_force, self.tmp)

    def _write(self, name, text):
        p = os.path.join(self.tmp, name)
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return p

    def _local(self):
        """同事机的常态：**只填一个仓**（INSTALL.md 的原话就是这个形态）。

        路径指向一个真存在的目录 —— 否则会在 `--validate` 的第三档
        （「键在、路径不存在」）上阻断，那就测不出本文件要测的那两支了。
        """
        return self._write("local.toml", "[repos]\nempty_pc = \"%s\"\n"
                           % self.repo.replace("\\", "/"))


class TestSharedConfigDeclaresEveryRepoKey(unittest.TestCase):
    """① 键名表必须在共享配置里 —— 事故的根因就是它被清空了。"""

    def _base_cfg(self):
        """只读 **base**（`config/harness.toml`），不经 local 覆盖层 —— 判据是团队策略。"""
        sys.path.insert(0, HARNESS)
        import _config
        return _config.load(os.path.join(ROOT, "config", "harness.toml"))

    def test_every_gate_repo_key_is_declared(self):
        cfg = self._base_cfg()
        repos = cfg.get("repos") or {}
        gates = cfg.get("gates") or {}
        self.assertTrue(repos, "共享 config/harness.toml 的 repos 段空了 —— 键名表是**团队策略**"
                               "（哪些仓受管），清空它会让「键没声明」与「本机没配」塌成一件事，"
                               "同事机上表现为提交链 fail-closed 全拦（见本文件 docstring）。")
        missing = sorted({(g or {}).get("repo") for g in gates.values()} - {None, "harness_self"}
                         - set(repos))
        self.assertEqual([], missing,
                         "这些 gate 的 repo 键在 repos 段里没有声明：%s —— 现在它们在任何机器上"
                         "都是阻断级 ❌（那是有意的：拼错 / 新 gate 漏登记必须出声）。"
                         "若这是刚加的 gate，请同时在 repos 段补一行 `键名: \"\"`。" % missing)

    def test_no_dead_repo_keys(self):
        """反向：声明了但没有任何 gate 引用的键 = 死键（本仓对「白写的配置项」一贯要报）。"""
        cfg = self._base_cfg()
        repos = cfg.get("repos") or {}
        used = {(g or {}).get("repo") for g in (cfg.get("gates") or {}).values()}
        dead = sorted(k for k in repos if k not in used)
        self.assertEqual([], dead, "repos 段里这些键没有任何 gate 引用（死键）：%s" % dead)


class TestPartialLocalDoesNotBlock(_FixtureCase):
    """② 只配一半 = 同事机常态 → **不阻断**（本次事故的正面锁）。"""

    def test_self_check_passes(self):
        rc, out = _run_gate(["harness_self", "--repo", ROOT], config_path=self._local())
        self.assertNotIn("引用未定义的 repo", out,
                         "只配了一个仓的 local.yaml 又让 ⑤ 报「未定义的 repo」了 —— 那是批次 2~6 "
                         "那个事故的复发：提交链会 fail-closed，同事的代码一个字节都过不了门禁。")
        self.assertEqual(0, rc, "同事机常态（只填自己有那几个仓）不该被 harness_self 拦：\n%s"
                         % out[-1500:])

    def test_validate_passes_and_still_shows_the_gap(self):
        rc, out = _run_gate(["--validate"], config_path=self._local())
        self.assertEqual(0, rc, "只配一半的机器上 --validate 必须通过（CI 干净容器同源）：\n%s"
                         % out[-2000:])
        # 不阻断 ≠ 静默：没配的那些 gate 必须**看得见**（⊘ 逐条 + 末尾汇总）。
        self.assertIn("⊘", out, "未配路径的 gate 连 ⊘ 都不打了 = 静默放行（本仓反复记过的形状）")
        self.assertIn("本机没填仓库路径", out, "末尾那句汇总改了措辞？它要说清「这不是配置错」")


class TestUndeclaredRepoKeyStillBlocks(_FixtureCase):
    """③ 拼错的键**照旧阻断** —— 修 ② 不能把这一支一起修哑。"""

    def _typo_cfg(self):
        """新 gate 引用一个 repos 段里没有的键。

        刻意用**新** gate（不在 base 的 gates 段里）：`_guard_policy` 只退回「已注册 gate
        的策略字段覆盖」，本地新增的 gate 是允许的（那是「场景 B：接新仓」的正当用法）——
        正好也顺带证明这条路仍然通。
        """
        return self._write("typo.toml", (
            "[gates.zzz_typo_pc]\n"
            "enabled = true\n"
            "repo = \"no_such_key\"\n"
            "script = \"checks/pc_code_check.py\"\n"
            "run_on = [\"pre_commit\"]\n"))

    def test_self_check_reports_it(self):
        rc, out = _run_gate(["harness_self", "--repo", ROOT], config_path=self._typo_cfg())
        self.assertIn("没有声明的键", out,
                      "拼错的键不再被 ⑤ 报出来了（检测力丢了）：\n%s" % out[-1200:])
        self.assertEqual(1, rc, "拼错的键必须阻断（M43 类事故唯一的信号）：\n%s" % out[-1500:])

    def test_validate_reports_it(self):
        rc, out = _run_gate(["--validate"], config_path=self._typo_cfg())
        self.assertIn("repo 键未声明", out, "体检不再报「键未声明」：\n%s" % out[-1500:])
        self.assertEqual(2, rc, "拼错的键必须让 --validate rc=2：\n%s" % out[-1500:])

    def test_structure_only_reports_it_too(self):
        """CI 曾用 `--validate --structure-only || true` —— 结构错被整条吞掉。

        删掉 `|| true` 之后，`--structure-only` 这支必须照样对坏键 rc=2；
        否则「CI 跑了体检」只是看起来跑了。
        """
        rc, out = _run_gate(["--validate", "--structure-only"], config_path=self._typo_cfg())
        self.assertIn("repo 键未声明", out, "structure-only 下不再报「键未声明」：\n%s" % out[-1500:])
        self.assertEqual(2, rc, "--structure-only 也必须对坏配置 rc=2：\n%s" % out[-1500:])


class TestBrokenOverrideIsNotGreen(_FixtureCase):
    """④ 覆盖层读不出来 ≠ 配置健康（复审报告 2026-10-22 §3.1）。

    现场：`HARNESS_CONFIG` 指向一份少写 `]` 的 TOML，load() 退回 base 并只往 stderr
    打一行，而 `--validate` 最后仍说「全部启用 gate 配置健康」+ rc=0。覆盖层里的
    路径/策略**一条都没生效**，体检却给绿色结论 —— 只看 rc 的 doctor / CI / 接入脚本
    会把"配置坏了"读成"配置好了"。这一支要 fail-closed，且不能把所有 HARNESS_CONFIG
    都判红（阳性对照在同一类里）。
    """

    def _broken(self):
        return self._write("broken.toml", "[repos\n")

    def test_validate_fails_closed(self):
        rc, out = _run_gate(["--validate"], config_path=self._broken())
        self.assertIn("本地覆盖层读不出来", out, "坏覆盖层不再被点名：\n%s" % out[-1500:])
        self.assertIn("一条都没生效", out, "没把后果说清（覆盖层里的路径/策略全没生效）：\n%s"
                      % out[-1500:])
        self.assertNotIn("体检通过", out, "坏覆盖层下仍打「体检通过」= 假绿复发：\n%s" % out[-1500:])
        self.assertEqual(2, rc, "坏覆盖层必须让 --validate rc=2：\n%s" % out[-1500:])

    def test_structure_only_fails_too(self):
        """CI 用 --structure-only；坏文件不是"端仓不在本机"，任何档位都得阻断。"""
        rc, out = _run_gate(["--validate", "--structure-only"], config_path=self._broken())
        self.assertEqual(2, rc, "--structure-only 下坏覆盖层必须照样 rc=2：\n%s" % out[-1500:])

    def test_a_readable_override_still_passes(self):
        """阳性对照：判据不是「只要设了 HARNESS_CONFIG 就红」。"""
        rc, out = _run_gate(["--validate"], config_path=self._local())
        self.assertEqual(0, rc, "可读的覆盖层被误判成坏文件：\n%s" % out[-1500:])


class TestCiRunsValidateHonestly(unittest.TestCase):
    """CI 不许再把体检失败吞掉，也不许退回只测 Linux。"""

    def _ci(self):
        p = os.path.join(ROOT, ".github", "workflows", "ci.yml")
        with io.open(p, encoding="utf-8") as f:
            return f.read()

    def test_validate_failure_is_not_swallowed(self):
        src = self._ci()
        self.assertNotRegex(src, r"run_gate\.py\s+--validate[^\n]*\|\|\s*true",
                            "CI 用 `|| true` 吞了 --validate 的失败：配置体检变成摆设，"
                            "坏键 / 坏值域永远不会让 CI 红")
        self.assertIn("run_gate.py --validate", src, "CI 不再跑配置体检了")

    def test_windows_runner_is_covered(self):
        src = self._ci()
        self.assertIn("windows-latest", src,
                      "CI 只有 Linux runner —— 路径分隔符、编码、行尾、解释器解析这些"
                      "Windows 特有坑不会被任何一次 push 覆盖")


class TestMachineKeysCountsOnlyFilledKeys(unittest.TestCase):
    """④ 口径：`_machine_keys` 数「值非空的键」—— 键名回到共享配置后，「键在不在」恒为真。"""

    def _m(self):
        sys.path.insert(0, HARNESS)
        import _config
        return _config._machine_keys

    def test_empty_value_is_not_a_machine_path(self):
        m = self._m()
        self.assertEqual(["a"], m({"a": "D:/x", "b": "", "c": None}),
                         "空值/None 的键被算成了「本机有路径」—— 那正是事故的机制："
                         "一份只填了 1 条的 local.yaml 会被判成「本机有路径」，"
                         "于是另外那些没填的键被当成拼错而阻断。")

    def test_injected_self_is_never_counted(self):
        m = self._m()
        self.assertEqual([], m({"harness_self": "D:/harness"}),
                         "harness_self 是引擎无条件注入的（恒有值），算进来会让"
                         "「本机一条路径都没配」永远判不出来。")

    def test_non_dict_is_empty(self):
        m = self._m()
        self.assertEqual([], m(None))
        self.assertEqual([], m("{}"))     # mini-yaml 会把 `repos: {}` 解析成这个字符串


class TestWarningsAreAggregated(unittest.TestCase):
    """未配路径的 gate 汇总成**一条**告警 —— 逐条报会把钩子的回灌窗口整段占满。"""

    def _capture(self, cfg):
        """跑一次 `_sanity_check`，返回它打到 stderr 的全部文本。"""
        sys.path.insert(0, HARNESS)
        import _config
        _config._warned.clear()           # 同进程去重是「说过就不再说了」，这里要它照说
        buf = io.StringIO()
        old = sys.stderr
        sys.stderr = buf
        try:
            _config._sanity_check(cfg)
        finally:
            sys.stderr = old
        return buf.getvalue()

    def _cfg(self):
        """两个已启用 gate：一个填了真路径、一个留空（+ 引擎注入的 harness_self）。"""
        return {"repos": {"harness_self": ROOT, "demo_pc": ROOT, "empty_pc": ""},
                "gates": {"demo_pc": {"enabled": True, "repo": "demo_pc"},
                          "empty_pc": {"enabled": True, "repo": "empty_pc"}}}

    def test_one_line_not_one_per_gate(self):
        out = self._capture(self._cfg())
        self.assertIn("1 个已启用 gate 没配仓库路径", out,
                      "未配路径的 gate 不再汇总，或数字不对（数字必须是**值非空**的键数）：\n%s" % out)
        self.assertIn("本机已填 1 条", out,
                      "汇总里要说清「本机填了几条」—— 数错就是把空值键也算进去了：\n%s" % out)
        self.assertNotIn("已启用但仓库路径不可用", out,
                         "又回到逐 gate 各报一条了：一台新机器上就是三十几行，"
                         "把钩子的回灌窗口（_compact，40 行）整段占满：\n%s" % out)

    def test_undeclared_and_unconfigured_say_different_things(self):
        """两种「跑不了」必须指向**不同**的修法：一个改共享配置，一个改自己的 local.yaml。"""
        cfg = self._cfg()
        cfg["gates"]["ghost_pc"] = {"enabled": True, "repo": "no_such_key"}
        out = self._capture(cfg)
        self.assertIn("没有声明", out, "「键没声明」那支不说了：\n%s" % out)
        self.assertIn("没配仓库路径", out, "「本机没配路径」那支不说了：\n%s" % out)
