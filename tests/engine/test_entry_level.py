# -*- coding: utf-8 -*-
"""
入口级行为断言测试（2026-09-12，第二轮外部评审元建议）
========================================================
背景：连续两轮外审都有同一形状的缺陷——
  · 第一轮 P0：PC/H5 的语法规则**函数**本身没问题，但 check_file 没执行它；
  · 第二轮 N1：接线补上了，可 main() 又把 skip_node 开关无条件打开 → 语法校验在**所有模式**失效；
  · 第二轮 N3：run_gate 的 --mode 分支把参数值丢了 → 定向检查退化成全量扫描。
单测（tests/test_checks.py）测的是"函数级"行为，这些 bug 全都从它眼皮下走过。
本文件按评审建议，专门测**真实入口的行为**：命令行 → run_gate → check 脚本 → 退出码。

运行：python -m unittest discover -s tests   （与其它用例一起；全部用临时目录）
"""
import json
import os
import re
import shutil
import subprocess


import sys
import tempfile
import pathlib
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _tmpclean import mkdtemp_cleanup as _mkdtemp    # noqa: E402
from _tmpclean import rmtree_force as _rmtree_force  # noqa: E402



# 快跑（HARNESS_FAST=1）：只跳过**慢的链路集成**（真起 CLI/git/全量扫描/hook 子进程），
# 纯文件读取与纯进程内逻辑锁照跑。
# 第四轮外审 ①：本文件 25 例里 6 个类占了 ~22s，快跑开关原先没接它 → 开关价值被稀释。
# 2026-09-17 批 3 补：那时只接了"类"，于是本文件一直是**半接**的 —— 快跑档单跑仍要 32.9s，
# 其中 TestAgentHookContracts 一个类就占 31s（19 例里 17 例 ≥1s，合计 30.4s）。
# 该类**刻意用方法级而不是类级**：同类里 test_compact_keeps_findings_not_only_tail（0.124s）
# 是纯进程内逻辑锁（直接调 hk._compact()，不起子进程），**而且是变异 M11 唯一的判据**；
# test_unrecognized_command_defers_to_stop（0.372s）同理。类级会把这两条一起清掉 ——
# 省下的只有约 0.5s，代价却是"改坏了 _compact 在快跑档里没人吭声"。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, f"快速模式（HARNESS_FAST=1）跳过{reason}")




# 测试隔离（第四轮 N-2）：把 run_gate 的运行日志钉到临时文件。
# 原先只有 test_coverage.py 设了 HARNESS_LOG_FILE，其余用例调 run_gate 时会把探针记录
# （gate=t_gate 等）写进真实的 reports/gate_log.jsonl —— 实测污染过 124 条，覆盖度自评的
# 窗口数据（运行次数、gate 概览）被混入测试噪声。模块导入时就设好，子进程
# env=dict(os.environ, ...) 会继承；tests/test_coverage.py 里有源码级锁防止将来漏设。
# 2026-09-21：退出时那次 `shutil.rmtree` **删不掉**这个目录（里面有 run_gate 真写出来的
# 台账，`ignore_errors=True` 把 PermissionError 静默吞掉 —— 症状与"清理成功"一模一样）。
# 实测 `harness-testlog-` 存量 1193 份 / 441 MB，只能靠数目录才发现。走强删助手。
_LOG_TMPDIR = _mkdtemp("harness-testlog-")
os.environ["HARNESS_LOG_FILE"] = os.path.join(_LOG_TMPDIR, "gate_log.jsonl")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = sys.executable

# 端到端"命中"样本：示例 gate 的 NO-CONSOLE 是 block 级，退出码能反映判定
BAD_FILE = ('<template><div /></template>\r\n'
            '<script setup>\r\n'
            'console.log("debug");\r\n'
            '</script>\r\n')
OK_FILE = ('<template><div /></template>\r\n'
           '<script setup>\r\n'
           'const x = 1;\r\n'
           '</script>\r\n')


def _find_sh():
    p = shutil.which("sh")
    if p:
        return p
    for cand in (r"C:\Program Files\Git\bin\sh.exe",
                 r"C:\Program Files\Git\usr\bin\sh.exe"):
        if os.path.exists(cand):
            return cand
    return None


_SH = _find_sh()


def _run(cmd, cwd=None, env=None):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=cwd or ROOT, stdin=subprocess.DEVNULL,
                          env=env, timeout=180)


def _base_repo_keys():
    """base(config/harness.toml) 里各 gate 引用的 repo 键。

    纯读文件（`_load_file`），**不经** `load()` —— 免得受本进程环境里的 HARNESS_CONFIG
    影响。用途见 test_findings_survive_diagnostic_flood：要把"每个 gate 的仓都不存在"
    这个 CI 前提**在本机复刻**出来。
    """
    sys.path.insert(0, os.path.join(ROOT, "harness"))
    import _config
    cfg = _config._load_file(os.path.join(ROOT, "config", "harness.toml"))
    keys = {(g or {}).get("repo") for g in (cfg.get("gates") or {}).values()}
    return sorted(k for k in keys if k)


def _load_hook_module():
    """按路径加载钩子模块（它不在包内，只能 importlib 直取）——供"纯函数级"锁使用。"""
    import importlib.util
    p = os.path.join(ROOT, "integrations", "hooks", "agentguard-gate.py")
    spec = importlib.util.spec_from_file_location("_agentguard_hook_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read(*parts):
    """读工程内文件。必须走 with 块：套件在 `-W error::ResourceWarning` 下跑，
    裸 open().read() 会因未关闭句柄被判失败（本文件曾自己踩这条）。"""
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class _Fixture:
    """临时端仓 + 指向它的最小配置（gate t_gate），走 HARNESS_CONFIG 覆盖层。"""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="entrylvl_")
        self.repo = os.path.join(self.dir, "repo_gate")
        os.makedirs(os.path.join(self.repo, "src"), exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, capture_output=True)
        self.cfg = os.path.join(self.dir, "cfg.toml")
        with open(self.cfg, "w", encoding="utf-8", newline="") as f:
            f.write("[repos]\n"
                    "t_gate = \"%s\"\n"
                    "[gates.t_gate]\n"
                    "enabled = true\n"
                    "repo = \"t_gate\"\n"
                    "script = \"checks/example_code_check.py\"\n"
                    % self.repo.replace(chr(92), "/"))
        self.env = dict(os.environ, HARNESS_CONFIG=self.cfg)

    def write(self, rel, text):
        p = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return p

    def cleanup(self):
        _rmtree_force(self.dir)


@_slow("示例 gate 入口链路（起子进程）")
class TestExampleRuleReachesRealEntry(unittest.TestCase):
    """入口级回归锁：规则命中必须在**真实入口**被拦（函数级测过不算数）。"""

    def setUp(self):
        self.fx = _Fixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_blocks_via_check_script_cli(self):
        bad = self.fx.write("src/bad.vue", BAD_FILE)
        r = _run([PY, os.path.join(ROOT, "checks", "example_code_check.py"),
                  "--gate", "t_gate", "--repo", self.fx.repo, bad], env=self.fx.env)
        self.assertEqual(r.returncode, 1,
                         f"命中经 check 脚本 CLI 应 rc=1，实际 {r.returncode}\n{r.stdout[-600:]}")
        self.assertIn("NO-CONSOLE", r.stdout + r.stderr,
                      "拦截理由必须是规则命中（不是崩溃/路径错）")

    def test_blocks_via_run_gate(self):
        """run_gate 是提交/编辑期真正走的入口——这里才是回归锁的现场。"""
        bad = self.fx.write("src/bad.vue", BAD_FILE)
        r = _run([PY, os.path.join(ROOT, "harness", "run_gate.py"), "t_gate", bad],
                 env=self.fx.env)
        self.assertEqual(r.returncode, 1,
                         f"命中经 run_gate 应 rc=1，实际 {r.returncode}\n{r.stdout[-600:]}")
        self.assertIn("NO-CONSOLE", r.stdout + r.stderr)

    def test_clean_file_passes(self):
        """反向锁：合规文件不得被误拦（避免"改狠了"）。"""
        ok = self.fx.write("src/ok.vue", OK_FILE)
        r = _run([PY, os.path.join(ROOT, "harness", "run_gate.py"), "t_gate", ok],
                 env=self.fx.env)
        self.assertEqual(r.returncode, 0, f"合规文件应放行\n{r.stdout[-600:]}")

    def test_full_scan_reports_violation(self):
        """全量入口（`--all`）必须扫到仓内违规，而不是只认位置参数。"""
        self.fx.write("src/bad.vue", BAD_FILE)
        r = _run([PY, os.path.join(ROOT, "checks", "example_code_check.py"),
                  "--gate", "t_gate", "--repo", self.fx.repo, "--all"], env=self.fx.env)
        self.assertEqual(r.returncode, 1,
                         f"全量扫描应报出违规\n{r.stdout[-600:]}")
        self.assertIn("NO-CONSOLE", r.stdout)

    def test_relative_repo_is_resolved_against_callers_cwd(self):
        """`--repo` 相对路径必须按调用方 cwd 解析并绝对化后再传给子进程。

        子进程以 cwd=<HARNESS> 启动，相对路径原样传下去会指到引擎仓：在受管仓里
        `run_gate <gate> --repo . <file>` 会变成"查引擎仓里的同名路径" —— 文件找不到
        （修复后 rc=2）或查错对象。"""
        self.fx.write("src/bad.vue", BAD_FILE)
        r = _run([PY, os.path.join(ROOT, "harness", "run_gate.py"), "t_gate",
                  "--repo", os.path.basename(self.fx.repo), "src/bad.vue"],
                 cwd=self.fx.dir, env=self.fx.env)
        self.assertEqual(r.returncode, 1,
                         f"相对 --repo 没有按调用方 cwd 解析\n{r.stdout[-800:]}{r.stderr[-400:]}")
        self.assertIn("NO-CONSOLE", r.stdout + r.stderr)


@_slow("真跑 demo_gate.sh（起两次 run_gate）")
class TestDemoGateScript(unittest.TestCase):
    """首屏演示锁：demo 必须真的先拦后放，而不是打印一段看似成功的文案。"""

    def test_demo_intercepts_then_passes(self):
        if not _SH:
            self.skipTest("无可用 sh")
        r = _run([_SH, os.path.join(ROOT, "sandbox", "demo_gate.sh")])
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 0, "demo 自身失败了：\n" + out[-1200:])
        self.assertIn("拦截成功", out, "demo 没有证明违规被拦：\n" + out[-1200:])
        self.assertIn("放行成功", out, "demo 没有证明修复后放行：\n" + out[-1200:])


class TestDemoGatePythonUsage(unittest.TestCase):
    """Python demo 的用法守卫（不跑门禁，快跑档也留着）：只对应 example gate。"""

    def test_unknown_gate_is_refused(self):
        r = _run([PY, os.path.join(ROOT, "sandbox", "demo_gate.py"), "pc"])
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(2, r.returncode, "非 example gate 必须以 rc=2 拒绝：\n" + out[-600:])
        self.assertIn("只对应 example", out, "拒绝时必须说清原因：\n" + out[-600:])


@_slow("真跑 demo_gate.py（起两次 run_gate）")
class TestDemoGatePython(unittest.TestCase):
    """PowerShell / 无 sh 环境的入口锁：Python demo 必须同样先拦后放。"""

    def test_python_demo_intercepts_then_passes(self):
        r = _run([PY, os.path.join(ROOT, "sandbox", "demo_gate.py")])
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 0, "demo 自身失败了：\n" + out[-1200:])
        self.assertIn("拦截成功", out, "demo 没有证明违规被拦：\n" + out[-1200:])
        self.assertIn("放行成功", out, "demo 没有证明修复后放行：\n" + out[-1200:])
        self.assertIn("[NO-CONSOLE]", out, "拦的不是 NO-CONSOLE：\n" + out[-1200:])


@_slow("run_gate 参数链路（起子进程）")
class TestRunGateModeValue(unittest.TestCase):
    """N3 回归锁：--mode 的值不能被吞掉（旧实现 → 命令变 `--mode <文件>`、文件丢失）。"""

    def setUp(self):
        self.fx = _Fixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_mode_value_and_file_both_reach_command(self):
        f = self.fx.write("src/ok.vue", OK_FILE)
        r = _run([PY, os.path.join(ROOT, "harness", "run_gate.py"),
                  "t_gate", "--mode", "loose", f], env=self.fx.env)
        out = r.stdout + r.stderr
        self.assertIn("--mode loose", out,
                      "mode 的值必须出现在真正执行的命令里（旧实现被丢弃）")
        self.assertIn(os.path.basename(f), out, "文件参数不能丢失")


# 示例 gate 的命中样本（行尾统一 CRLF，避免 EOL 规则抢先）
CONSOLE_VUE = ('<template><div /></template>\r\n'
               '<script setup>\r\n'
               'console.log("x");\r\n'
               '</script>\r\n')


class _TwoGateFixture(_Fixture):
    """同一临时仓上注册两个 gate（同一脚本），用来验证 gate 名按位置参数转发。

    刻意**不定义默认 gate**：夹具里一旦存在同名默认 gate，"没转发 --gate"与"转发了"
    的行为就完全一致，缺陷不可观测。
    """

    def __init__(self, script, pairs):
        super().__init__()
        repo = self.repo.replace(chr(92), "/")
        lines = ["[repos]"]
        for name, _extra in pairs:
            lines.append('%s = "%s"' % (name, repo))
        for name, extra in pairs:
            lines.append("")
            lines.append("[gates.%s]" % name)
            lines.append("enabled = true")
            lines.append('repo = "%s"' % name)
            lines.append('script = "%s"' % script)
            lines.extend(extra)
        with open(self.cfg, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(lines) + "\n")
        self.env = dict(os.environ, HARNESS_CONFIG=self.cfg)


@_slow("run_gate gate 名转发链路（起子进程）")
class TestGateNameReachesEndpointScript(unittest.TestCase):
    """run_gate 必须把**命令行上的 gate 名**转发给端脚本。

    缺陷形状（2026-09-16 修复前）：`--gate` 的注入条件里写死了
    `backend_layer_check.py`，其余端于是停在脚本里硬编码的 gate 名上，
    `load_policy` 读的是别家 gate 的策略 —— 配置整体错位，且台账里看不出来。
    现在注入集合取自 `registry.ENDPOINT_SCRIPTS`（唯一数据源），示例 gate 也在册。
    """

    def tearDown(self):
        self.fx.cleanup()

    def _run_gate(self, gate, path):
        return _run([PY, os.path.join(ROOT, "harness", "run_gate.py"), gate, path],
                    env=self.fx.env)

    def test_gate_name_is_injected_for_each_gate(self):
        self.fx = _TwoGateFixture("checks/example_code_check.py",
                                  [("t_on", []), ("t_off", [])])
        f = self.fx.write("src/log.vue", CONSOLE_VUE)
        for gate in ("t_on", "t_off"):
            r = self._run_gate(gate, f)
            self.assertIn(f"--gate {gate}", r.stdout,
                          f"命令行上的 gate 名 {gate} 没被注入子进程命令\n" + r.stdout[-600:])
            self.assertEqual(r.returncode, 1,
                             f"示例规则应拦下 console.log，实际 {r.returncode}\n"
                             + r.stdout[-600:])

    def test_caller_supplied_gate_flag_is_ignored(self):
        """调用方**不能**用 `--gate` 改写策略归属（只认位置参数）。"""
        self.fx = _TwoGateFixture("checks/example_code_check.py",
                                  [("t_on", []), ("t_off", [])])
        f = self.fx.write("src/log.vue", CONSOLE_VUE)
        rg = os.path.join(ROOT, "harness", "run_gate.py")
        for label, argv in (("旗标在文件后", ["t_on", f, "--gate", "t_off"]),
                            ("旗标在文件前", ["t_on", "--gate", "t_off", f])):
            r = _run([PY, rg] + argv, env=self.fx.env)
            self.assertIn("忽略调用方 --gate", r.stdout,
                          f"{label}：调用方 --gate 没被忽略\n" + r.stdout[-600:])
            self.assertIn("--gate t_on", r.stdout,
                          f"{label}：策略归属应仍是 t_on\n" + r.stdout[-600:])
            self.assertNotIn("--gate t_off", r.stdout,
                             f"{label}：t_off 混进了真正执行的命令\n" + r.stdout[-600:])


@_slow("commit-msg 链路（真 git 提交）")
class TestCommitMsgIsScoped(unittest.TestCase):
    """N2 回归锁，第三轮 N-η 加固：`hooks-dir/commit-msg` 是**全部端仓**共享的 core.hooksPath
    分发器，只能在「被提交的是 harness 工程仓本身」时生效。

    为什么加固：旧版只断言端仓方向「rc=0 且无输出」，而**旧代码（无自识守卫）会不会失败，
    取决于跑测试那一刻真实工程仓里有没有暂存的 .py/.yaml** —— 工程仓干净时空过。
    现在把 hook 复制进一个临时"工程仓"并**固定暂存一个 .py**（正是旧版的触发条件），
    于是「端仓必须放行」「工程仓无标记必须拦」「有标记必须放行」三条都成为确定性断言。
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="endrepo_")
        src = os.path.join(ROOT, "harness", "hooks-dir", "commit-msg")
        self.assertTrue(os.path.exists(src), "hooks-dir/commit-msg 应入库（单一来源）")
        # 临时"工程仓"：hook 放到 <eng>/harness/hooks-dir/ → ENGROOT 由脚本自身路径推导为 <eng>
        self.eng = os.path.join(self.dir, "eng")
        os.makedirs(os.path.join(self.eng, "harness", "hooks-dir"), exist_ok=True)
        self.hook = os.path.join(self.eng, "harness", "hooks-dir", "commit-msg")
        shutil.copyfile(src, self.hook)
        self._git_init(self.eng)
        with open(os.path.join(self.eng, "dummy_check.py"), "w", encoding="utf-8", newline="") as f:
            f.write("x = 1\n")
        subprocess.run(["git", "add", "dummy_check.py"], cwd=self.eng, capture_output=True)
        # 端仓（工作区里也有一个 py，用来证明"拦的判据来自被提交仓而不是工程仓"）
        self.repo = os.path.join(self.dir, "endrepo")
        os.makedirs(self.repo, exist_ok=True)
        self._git_init(self.repo)
        with open(os.path.join(self.repo, "a.py"), "w", encoding="utf-8", newline="") as f:
            f.write("x = 1\n")
        subprocess.run(["git", "add", "a.py"], cwd=self.repo, capture_output=True)
        self.msg_plain = os.path.join(self.dir, "msg_plain.txt")
        with open(self.msg_plain, "w", encoding="utf-8", newline="") as f:
            f.write("普通端仓提交\n")
        self.msg_harness = os.path.join(self.dir, "msg_harness.txt")
        with open(self.msg_harness, "w", encoding="utf-8", newline="") as f:
            f.write("[harness] 维护提交\n")

    @staticmethod
    def _git_init(d):
        subprocess.run(["git", "init", "-q"], cwd=d, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=d, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=d, capture_output=True)

    def tearDown(self):
        _rmtree_force(self.dir)

    @unittest.skipUnless(_SH, "无 sh")
    def test_end_repo_passes_silently(self):
        """端仓提交：即使工程仓里**确实**暂存着受保护文件，也必须 rc=0 且零输出。"""
        r = _run([_SH, self.hook, self.msg_plain], cwd=self.repo)
        self.assertEqual(r.returncode, 0, f"端仓提交不得被工程仓 hook 拦截\n{r.stdout}{r.stderr}")
        self.assertEqual((r.stdout + r.stderr).strip(), "",
                         "端仓提交不应打出工程仓的复核提示（跨仓噪声）")

    @unittest.skipUnless(_SH, "无 sh")
    def test_harness_repo_blocks_without_marker(self):
        """工程仓自身提交、消息无 [harness] → 必须拦（这是守护的核心，不能被端仓方向掩盖）。"""
        r = _run([_SH, self.hook, self.msg_plain], cwd=self.eng)
        self.assertEqual(r.returncode, 1, f"工程仓无标记提交必须被拦\n{r.stdout}{r.stderr}")
        self.assertIn("受保护", r.stdout + r.stderr)

    @unittest.skipUnless(_SH, "无 sh")
    def test_harness_repo_allows_with_marker(self):
        """工程仓自身提交、消息带 [harness] → 放行 + 复核提示。"""
        r = _run([_SH, self.hook, self.msg_harness], cwd=self.eng)
        self.assertEqual(r.returncode, 0, f"带标记应放行\n{r.stdout}{r.stderr}")
        self.assertIn("复核", r.stdout + r.stderr)


class TestEntryUtf8Fallback(unittest.TestCase):
    """N9 回归锁：所有门禁入口都要带 UTF-8 输出兜底（GBK 控制台崩=误拦），
    清单与 sandbox/doctor.sh 的检查项保持一致。"""

    # 2026-09-13 外部评审 v3 H1：入口清单**机器推导**（原先是手工列举 → 新入口天然漏网，
    # 同一个"GBK 控制台打印中文崩/乱码"的坑出现三次：auto-gate → 钩子正本 → 钩子 shim）。
    # 清单的唯一来源是 sandbox/list_entries.py，doctor.sh 与这里共用它。
    LIST_ENTRIES = os.path.join(ROOT, "sandbox", "list_entries.py")

    def _entries(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("list_entries", self.LIST_ENTRIES)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.entries()

    def test_all_entries_have_reconfigure(self):
        """凡"会输出且含非 ASCII"的入口脚本都必须带 UTF-8 兜底（清单机器推导）。"""
        entries = self._entries()
        self.assertGreater(len(entries), 8, f"推导出的入口太少（{entries}），判据可能坏了")
        missing = []
        for rel in entries:
            p = os.path.join(ROOT, rel)
            if not os.path.exists(p):
                missing.append(f"{rel}(不存在)")
                continue
            with open(p, encoding="utf-8") as f:
                if "reconfigure" not in f.read():
                    missing.append(rel)
        self.assertEqual(missing, [], f"以下入口缺 UTF-8 兜底：{missing}")

    def test_doctor_uses_generated_list(self):
        """doctor.sh 必须**调用生成器**（清单同源），不许退回手工列举 —— 否则两处清单会漂移，
        且新入口又会漏网（这正是本轮 H1 的形状）。"""
        with open(os.path.join(ROOT, "sandbox", "doctor.sh"), encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("list_entries.py", txt, "doctor.sh 没调用机器推导的清单生成器")
        self.assertNotIn('for f in checks/pc_code_check.py', txt,
                         "doctor.sh 又退回手工列举入口清单了")


@_slow("行内豁免入口链路")
class TestExampleInlineWaiver(unittest.TestCase):
    """N4 回归锁：示例 gate 必须认 `harness-ok: <规则ID> <理由>` 行内豁免。"""

    def setUp(self):
        self.fx = _Fixture()

    def tearDown(self):
        self.fx.cleanup()

    MARKED = ('<template><div /></template>\r\n'
              '<script setup>\r\n'
              'console.log("debug")  // harness-ok: NO-CONSOLE 演示必须临时打印\r\n'
              '</script>\r\n')
    PLAIN = ('<template><div /></template>\r\n'
             '<script setup>\r\n'
             'console.log("debug")\r\n'
             '</script>\r\n')

    def test_marked_line_is_waived(self):
        f = self.fx.write("src/marked.vue", self.MARKED)
        r = _run([PY, os.path.join(ROOT, "harness", "run_gate.py"), "t_gate", f], env=self.fx.env)
        self.assertEqual(r.returncode, 0, f"带 harness-ok 的行应放行\n{r.stdout[-400:]}")

    def test_unmarked_line_still_blocks(self):
        """反向锁：没写豁免标记就必须照拦（否则豁免等于默认全开）。"""
        f = self.fx.write("src/plain.vue", self.PLAIN)
        r = _run([PY, os.path.join(ROOT, "harness", "run_gate.py"), "t_gate", f], env=self.fx.env)
        self.assertEqual(r.returncode, 1, f"未标记的 console.log 必须拦\n{r.stdout[-400:]}")

    def test_reasonless_waiver_still_blocks(self):
        """P0-2 item 1: harness-ok: <ID> without reason must NOT waive.

        Review found: old regex did not require a reason, so `harness-ok: NO-CONSOLE`
        without explanation was accepted -- an unaudited backdoor.
        New: `harness-ok: <ID> <reason>` required.
        """
        no_reason = ('<template><div /></template>\r\n'
                     '<script setup>\r\n'
                     'console.log("debug")  // harness-ok: NO-CONSOLE\r\n'
                     '</script>\r\n')
        f = self.fx.write("src/no_reason.vue", no_reason)
        r = _run([PY, os.path.join(ROOT, "harness", "run_gate.py"), "t_gate", f], env=self.fx.env)
        self.assertEqual(r.returncode, 1,
                         f"reasonless harness-ok must block\n{r.stdout[-400:]}")

    def test_waivers_used_are_reported(self):
        """N7 回归锁：用掉豁免时必须**打印**审计行。

        为什么较真：豁免是唯一合法的绕过出口，越看不见越危险 —— 提交输出里不列出来，
        复核的人就不知道这次是「真合规」还是「打了标记放行」。

        与上面 test_marked_line_is_waived 分开命名，是为了失败信息能直接指出
        「是没打印」而不是「没放行」—— 两者是不同的事故。

        ★ 2026-09-30 追加（用户的安全关切）：本条**同时**守"别回显理由"。原先那第三句
        `assertIn("演示必须临时打印", r.stdout)` 断言的是**理由原文**必须出现 ——
        理由是从源文件注释里逐字抄来的自由文本，而 gate stdout 就是 hook 回灌给模型的
        注入通道 ⇒ 那句断言等于**要求**通道搬运不可信文本（与 ③ 里 `test_checks.py`
        那处同款：把缺陷锁成了要求）。现改为 `assertNotIn` **并**补一句正面断言
        「`NO-CONSOLE L3` 必须还在」（L3 是 `MARKED` 样本里那个 `console.log` 的行号）——
        免得反转过头，把本条退化成"什么都别打印"。
        判到**注入正文**那一层的锁在下面
        `TestHookPathExtraction::test_injected_context_carries_no_waiver_reason_text`；
        护盾 `M116`。
        """
        f = self.fx.write("src/marked.vue", self.MARKED)
        r = _run([PY, os.path.join(ROOT, "harness", "run_gate.py"), "t_gate", f], env=self.fx.env)
        self.assertEqual(r.returncode, 0, f"前置：带豁免标记应放行\n{r.stdout[-400:]}")
        self.assertIn("本次用掉", r.stdout,
                      "没有打印本次用掉的豁免（审计行缺失）\n" + r.stdout[-600:])
        self.assertIn("NO-CONSOLE", r.stdout, "豁免汇总里必须点名规则 ID")
        # ★ 2026-09-30：这一句原先写的是 assertIn("演示必须临时打印", r.stdout)，理由是
        # 「豁免汇总必须带上理由 —— 理由才是复核的依据」。**它把缺陷锁成了要求。**
        # 理由是从源文件注释里逐字抄来的**自由文本**，而 gate stdout 正是 hook 回灌给
        # 模型的**注入通道**：谁能让一行注释进仓，谁就能把任意文字送进模型上下文，
        # 且穿着门禁自己的审计口吻。理由的家在 **diff** —— 原句括注里那句「标记写它
        # 就是为了留在 diff 里」恰恰说明它不必在这行注入里**再**出现一次。
        # 判据因此**反转**，与 ③ 里 `test_checks.py` 那处同款（老锁写的
        # assertIn("harness-ok: H5-UPLOAD", message) 也是把缺陷锁成了要求）。
        # ⚠ N7 的**实质**没被削弱：它要的是"豁免看得见"，上面两句（审计行 + 规则 ID）
        #   就是那条判据 —— 去掉的只是**第三个字段的回显**。
        self.assertNotIn("演示必须临时打印", r.stdout,
                         "豁免的理由原文被回显进了 gate stdout —— 那正是 hook 回灌给模型的"
                         "注入通道：注释里的任意文字都会原样进模型上下文。理由留在 diff 里，"
                         "不在这里（见 waiver_summary() 的说明与 M116）\n" + r.stdout[-400:])
        self.assertIn("NO-CONSOLE L3", r.stdout,
                      "反转之后必须**仍然**点名「规则 ID + 行号」—— 否则这条锁就从"
                      "「别回显理由」退化成了「什么都别打印」，反向的假绿\n" + r.stdout[-400:])


class TestThirdRoundLocks(unittest.TestCase):
    """第三轮外审 N-α…N-θ 的回归锁（每条锁住"这类问题再出现时套件会红"）。"""

    def test_all_gates_have_post_edit(self):            # N-α
        """全部 gate 必须都含 post_edit：少一个就意味着那个仓的 AI 编辑期**没有反馈**，
        而 auto-gate 又是 SKILL 指定的主入口（第三轮 4 个 hy gate 正是这么漏的）。"""
        sys.path.insert(0, os.path.join(ROOT, "harness"))
        from _config import all_gates
        missing = [n for n, g in all_gates().items()
                   if g.get("enabled") and "post_edit" not in (g.get("run_on") or [])]
        self.assertEqual(missing, [], f"这些启用的 gate 缺 post_edit：{missing}")

    def test_docs_gate_count_matches_config(self):      # N-β
        """README/AGENTS 里的启用数必须与 config 实际一致（第三轮：文档写'29 启用'，
        而实际已 33/33——机械可查的东西不该靠人记）。"""
        sys.path.insert(0, os.path.join(ROOT, "harness"))
        from _config import all_gates
        g = all_gates()
        en, total = sum(1 for v in g.values() if v.get("enabled")), len(g)
        for rel in ("README.md", "AGENTS.md"):
            txt = _read(rel)
            m = re.search(r"(\d+)\s*个\s*gate[^\n]*?（([^）]+)）", txt)
            m2 = re.search(r"完整清单（(\d+) 个，([^）]+)）", txt)
            if m:
                self.assertEqual(int(m.group(1)), total, f"{rel} gate 总数与 config 不符")
            if m2:
                self.assertEqual(int(m2.group(1)), total, f"{rel} gate 总数与 config 不符")
            if "启用" in txt:
                self.assertNotIn("（29 启用）", txt, f"{rel} 仍是过时的'29 启用'")
            if en == total:
                self.assertNotIn("（29 启用）", txt, f"{rel} 过时启用数")

    def test_agent_usages_not_mangled(self):            # N-γ
        """脚本用法注释里，`--flag` 不得被行内注释吃掉（第三轮：批量替换占位符时把
        `--repo/--all` 挤到注释后面，等于文档教人敲一条错命令）。"""
        for rel in ("harness/agents/auto-gate.py", "integrations/hooks/agentguard-gate.py"):
            for i, line in enumerate(_read(rel).split("\n"), 1):
                if "#" in line and line.lstrip().startswith(("python", "  python")):
                    tail = line.split("#", 1)[1]
                    self.assertNotIn("--", tail,
                                     f"{rel}:{i} 参数被行内注释吃掉：{line.strip()}")

    def test_guard_protects_hook_scripts(self):         # N-δ
        sys.path.insert(0, os.path.join(ROOT, "checks"))
        import harness_guard as guard
        self.assertTrue(guard._in_protected("integrations/hooks/agentguard-gate.py"))
        self.assertFalse(guard._in_protected("integrations/codex/README.md"))

    def test_bootstrap_writes_pointer(self):            # N-ε
        """文档承诺 `~/.agentguard/harness.path` 是钩子定位工程的一种方式，
        bootstrap 必须真的生成它（否则是空转选项）。"""
        txt = _read("sandbox", "bootstrap_agent_env.sh")
        self.assertIn("harness.path", txt)
        self.assertIn(".agentguard", txt)
        # 内容必须是 Windows 风格：Python 钩子读不了 git-bash 的 `/d/...`（实测踩到）
        self.assertIn('where.sh" --win', txt, "指针必须用 where.sh --win 求值")
        self.assertIn("cygpath", _read("sandbox", "where.sh"))

    @unittest.skipUnless(_SH, "无 sh")
    def test_hook_tolerates_closed_stdin(self):         # N-ζ
        """钩子读不到 payload 也必须正常退出（DEVNULL 模拟 stdin 立即关闭）；
        且源码里要有 isatty 保护——交互终端下裸 read 会挂死（harness_guard 的历史 P0）。"""
        hook = os.path.join(ROOT, "integrations", "hooks", "agentguard-gate.py")
        src = _read("integrations", "hooks", "agentguard-gate.py")
        self.assertIn("isatty", src, "钩子缺 isatty 保护")
        r = _run([PY, hook, "codex", "PostToolUse"], cwd=ROOT)   # _run 用 DEVNULL
        self.assertEqual(r.returncode, 0, f"无 payload 时应静默放行\n{r.stdout}{r.stderr}")

    def test_auto_gate_skip_hint_goes_to_stderr(self):  # N-α 配套
        """跳过编辑期检查的提示必须走 stderr：stdout 是"检查结论"通道，
        rc=0 只代表没检查，不代表通过。"""
        src = _read("harness", "agents", "auto-gate.py")
        i = src.index("的 run_on={run_on} 不含 post_edit")   # 唯一锚点：跳过分支的 print
        self.assertIn("file=sys.stderr", src[i:i + 200])


class TestAgentHookContracts(unittest.TestCase):
    """三款客户端的**钩子输出契约**（不依赖模型：合成 payload 直喂钩子）。

    这些契约一旦被改坏，客户端侧会静默失效（Claude 不拦 / opencode 分不清"未通过"与"调用出错"），
    而函数级单测看不出来——所以按契约逐条锁。对应 sandbox/verify_integrations.sh 的「钩子契约验证」一节。
    """

    def setUp(self):
        self.fx = _Fixture()
        self.hook = os.path.join(ROOT, "integrations", "hooks", "agentguard-gate.py")
        self.bad = self.fx.write("src/bad.vue", BAD_FILE)

    def tearDown(self):
        self.fx.cleanup()

    def _payload(self, path, command=None):
        d = {"cwd": self.fx.repo, "tool_name": "Write", "tool_input": {"file_path": path}}
        if command is not None:
            d["tool_input"] = {"command": command}
        return json.dumps(d)

    def _hook(self, client, payload, event="PostToolUse"):
        """注意：钩子的契约就是"调用方把 JSON 写到 stdin 并关闭"，所以这里必须真喂 stdin——
        `_run()` 固定了 DEVNULL（那是给 git hook 链路用的），直接用会让钩子读到空 payload、
        再以测试进程 cwd 当成被编辑仓 → 判成"非受管仓库"放行（本测试第一版就这么假绿过）。"""
        return subprocess.run([PY, self.hook, client, event], cwd=ROOT,
                              input=payload, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              # 批次 4：心跳/跳过写盘必须隔离（否则测试数据会写进真实的 ~/.agentguard/）
                              env=dict(self.fx.env,
                                       HARNESS_HOOK_ACTIVITY_DIR=os.path.join(self.fx.dir, "activity")),
                              timeout=180)

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_claude_contract_exit2_with_message(self):
        r = self._hook("claude", self._payload(self.bad))
        self.assertEqual(r.returncode, 2, f"Claude 侧必须 exit 2 才拦得住（exit 1 不拦）\n{r.stdout}{r.stderr}")
        self.assertIn("门禁未通过", r.stdout + r.stderr)

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_opencode_contract_json_gate_rc(self):
        """opencode 插件靠 JSON 里的 gate_rc 区分"未通过(1)"与"调用出错(2)"——hook 退出码恒 2 分不出来。"""
        r = self._hook("opencode", self._payload(self.bad))
        data = json.loads(r.stdout)
        self.assertEqual(data["agentguard"]["gate_rc"], 1)
        self.assertIn("门禁未通过", data["agentguard"]["message"])

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_codex_contract_additional_context(self):
        """Codex：PostToolUse 回 hookSpecificOutput.additionalContext；且只有"像写文件"的命令才触发。"""
        r = self._hook("codex", self._payload(None, command="Set-Content -Path src/bad.vue -Value x"))
        data = json.loads(r.stdout)
        self.assertIn("门禁未通过", data["hookSpecificOutput"]["additionalContext"])
        # 只读命令必须放行（否则一个会话白等几十秒）
        r2 = self._hook("codex", self._payload(None, command="Get-ChildItem src"))
        self.assertEqual(r2.returncode, 0)
        self.assertEqual((r2.stdout or "").strip(), "")

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_edit_time_checks_only_the_edited_file(self):
        """用户拍板（2026-09-13）：编辑期**只扫被编辑的文件**。

        反锁：仓里另放一个违规文件，钩子只被告知 bad.vue —— bad.vue 的问题必须报，
        另一个文件**不能**出现在结论里（旧行为走 auto-gate 收集全仓未提交改动，会把别人的问题一起报）。
        """
        self.fx.write("src/other_bad.vue", BAD_FILE)
        r = self._hook("claude", self._payload(self.bad))
        out = r.stdout + r.stderr
        self.assertIn("bad.vue", out)
        self.assertNotIn("other_bad.vue", out,
                         "编辑期把无关文件的问题也算进来了（用户已明确要求只扫被编辑文件）")

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_codex_bash_path_extraction(self):
        """Codex 在 Windows 上只有 PowerShell 命令 → 从命令里提取被写文件，同样只查它。"""
        self.fx.write("src/other_bad.vue", BAD_FILE)
        payload = json.dumps({"cwd": self.fx.repo, "tool_name": "Bash",
                              "tool_input": {"command":
                                             "Set-Content -LiteralPath 'src/bad.vue' -Value x"}})
        r = self._hook("codex", payload)
        data = json.loads(r.stdout)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        self.assertIn("bad.vue", ctx)
        self.assertNotIn("other_bad.vue", ctx)

    def test_unrecognized_command_defers_to_stop(self):
        """取不到文件时不扫全仓（否则又吵回来），改为放行 + 由回合结束的 Stop 全仓兜底。"""
        payload = json.dumps({"cwd": self.fx.repo, "tool_name": "Bash",
                              "tool_input": {"command": "Write-Output hello"}})
        r = self._hook("codex", payload)
        self.assertEqual(r.returncode, 0)
        self.assertEqual((r.stdout or "").strip(), "")

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_stop_event_still_covers_whole_repo(self):
        """兜底不放松：回合结束（Stop）仍走全仓收集——仓里任一处违规都要报出来。"""
        other = self.fx.write("src/other_bad.vue", BAD_FILE)
        r = self._hook("codex", json.dumps({"cwd": self.fx.repo}), event="Stop")
        data = json.loads(r.stdout)
        self.assertIn("门禁未通过", data["systemMessage"],
                      f"Stop 兜底必须全仓检查（含未被编辑的文件）\n{r.stdout}{r.stderr}")

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_zcode_flat_payload_is_really_checked(self):
        """ZCode：payload 是**扁平**的（顶层 file_path，没有 tool_input）+ 契约同 Codex。

        2026-09-13 实证：它整个 bundle 里 "tool_input" 出现 0 次、而 "file_path" 12 次
        （读写工具的 zod 参数就是顶层 file_path/content）。若只从 tool_input 取文件，
        接 ZCode 就会"钩子跑了但取不到文件"→ 静默不检查 —— 这条锁同时钉住"取到了"和"真检查了"。
        """
        payload = json.dumps({"cwd": self.fx.repo, "tool_name": "Write",
                              "file_path": self.bad, "content": "x"})
        r = self._hook("zcode", payload)
        self.assertEqual(r.returncode, 0, "ZCode 契约同 Codex：exit 0 + stdout JSON")
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("门禁未通过", ctx)
        self.assertIn("bad.vue", ctx, "必须真的检查了那个文件，而不是空跑")

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_zcode_flat_command_payload_extracts_path(self):
        """ZCode 的 Bash 类工具：扁平 payload 里只有 command，也要能提取出被写文件。"""
        payload = json.dumps({
            "cwd": self.fx.repo, "tool_name": "Bash",
            "command": 'Set-Content -LiteralPath "%s" -Value "console.log(1)"' % self.bad})
        r = self._hook("zcode", payload)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("bad.vue", ctx, "从扁平 command 里提取路径失败 → 编辑期会静默不检查")

    def test_compact_keeps_findings_not_only_tail(self):
        """回灌内容必须**头尾都留**：只留末尾会把「结论」整段丢掉。

        2026-09-15 CI 红（4 条钩子契约锁齐红）的根因：`_compact` 原先只留末尾
        MAX_LINES 行，而调用处拼的是 `stdout + "\\n" + stderr` —— 检查结论
        （`✗ <文件>: <问题>`）在最前面，诊断（[config-warn] 等）在末尾。
        总行数一旦超限，被当成"中间"省略掉的恰好是结论 → 模型只收到"门禁未通过"，
        看不到要改什么（编辑期门禁等于白跑）。

        这里直接喂真实函数的**输入形状**（结论在最前 + 大量诊断在末尾），
        与配置内容、平台都无关 —— 所以以后诊断噪声怎么变，这条锁都还盯着"结论不许被省略"。
        """
        hk = _load_hook_module()
        head = "harness 门禁未通过（自动跑于你本次改动之后）"
        finding = "  ✗ src/bad.vue: 语法错误: Unexpected token ';'"
        text = "\n".join([head, "PC 门禁 [strict] 针对性检查：1 个文件", finding,
                          "❌ 发现 1 个阻断级问题"]
                         + ["[config-warn] gate g%02d 仓库路径不存在: D:/x/g%02d" % (i, i)
                            for i in range(100)])
        out = hk._compact(text)
        lines = out.splitlines()
        self.assertEqual(lines[0], head, "首行（结论的抬头）被丢了")
        self.assertIn(finding, out, "真实命中被当成「中间」省略了（只留末尾的老毛病）")
        self.assertLessEqual(len(lines), hk.MAX_LINES, "回灌行数上限被突破")

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_diagnostic_noise_does_not_flood_hook_context(self):
        """诊断必须**汇总成一条**，且不得把被编辑的文件挤出回灌内容。

        同事机器 / CI 上没 clone 各端仓是常态：每个 gate 若各发一条
        `[config-warn] ... 仓库路径不存在`，40 行的回灌窗口会被诊断整段占满
        （CI 上 4 条钩子契约锁齐红就是这个形状）。

        这里把 base 各 gate 的 repo 全指到不存在处，**再自己造 24 个仓不存在的 gate**，
        在本机复刻"同事机器上 30+ 条诊断"的处境 —— 否则本机有私盘路径 → 0 条诊断 →
        本锁恒绿，等于没锁。

        ⚠ 不能只靠 base 的 gate 数（2026-10-20 变异冒烟实测）：开源裁剪后 base 只剩
        `harness_self`（路径会被 _sanity_check 指回工程根）和 `example` 两个，真正
        会报"仓不存在"的只有 1 个 —— 逐 gate 各发一条也只有 1 行，`<=3` 的断言照样绿，
        锁就空转了（M12 变异后仍然绿）。噪声由夹具自己造，锁才与 base 的 gate 数解耦。

        锁两件事：(a) 诊断已汇总（条数是个位数，不是每 gate 一条）；
                  (b) 被编辑的文件仍出现在回灌内容里（不是"空跑"）。
        """
        cfg = os.path.join(self.fx.dir, "cfg_flood.yaml")
        n_noise = 24
        lines = ["repos:", "  t_gate: %s" % self.fx.repo.replace("\\", "/")]
        for k in _base_repo_keys():
            if k != "t_gate":
                lines.append("  %s: /nonexistent/%s" % (k, k))    # 本机也造"仓不存在"
        for i in range(n_noise):
            lines.append("  noise_repo_%02d: /nonexistent/noise_%02d" % (i, i))
        lines += ["gates:", "  t_gate:", "    enabled: true", "    repo: t_gate",
                  "    script: checks/example_code_check.py"]
        for i in range(n_noise):
            lines += ["  noise_gate_%02d:" % i, "    enabled: true",
                      "    repo: noise_repo_%02d" % i,
                      "    script: checks/example_code_check.py"]
        with open(cfg, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        payload = json.dumps({"cwd": self.fx.repo, "tool_name": "Write",
                              "file_path": self.bad, "content": "x"})
        r = subprocess.run([PY, self.hook, "zcode", "PostToolUse"], cwd=ROOT, input=payload,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=dict(self.fx.env, HARNESS_CONFIG=cfg), timeout=180)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        spam = [l for l in ctx.splitlines() if "仓库路径不存在" in l]
        self.assertTrue(spam, "前提不成立：本条件没造出「仓不存在」的诊断 → 本锁没测到东西")
        self.assertLessEqual(len(spam), 3,
                             "诊断又变回逐 gate 各发一条了（%d 条）→ 会把回灌窗口灌满" % len(spam))
        self.assertIn("bad.vue", ctx, "诊断噪声把真实命中挤出了回灌内容")

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_trae_contract_dual_channel_exit2(self):
        """Trae CN：exit 2（它认的阻断码）+ **stdout JSON 与 stderr 明文双通道**。

        为什么双通道：Trae 在 PowerShell 里执行钩子命令，官方口径是 exit 2 + stderr 回灌模型，
        但它包的 PowerShell 管道会把非 0 退出码**吞成 1**（实测：正本直跑 rc=2 → 经 PS 管道 rc=1），
        于是 Trae 只当"非阻断错误"、模型一无所知（会话记忆原话："未遇到任何自动检查"）。
        所以正本对 trae 三路齐发，配置里另补 `; exit $LASTEXITCODE` 保真退出码。
        """
        r = self._hook("trae", self._payload(self.bad))
        self.assertEqual(r.returncode, 2, f"Trae 认 exit 2；rc={r.returncode}\n{r.stdout}{r.stderr}")
        data = json.loads(r.stdout)
        self.assertIn("门禁", data["hookSpecificOutput"]["additionalContext"])
        self.assertIn("门禁未通过", r.stderr, "stderr 那一路（官方口径）也要有结论")

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_trae_pretooluse_deny_contract(self):
        """Trae 的 PreToolUse 阻断：stdout JSON permissionDecision=deny + exit 0。"""
        r = self._hook("trae", self._payload(self.bad), event="PreToolUse")
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_hook_survives_gbk_console(self):
        """宿主管道是 GBK 中文控制台时钩子**绝不能崩**（打印中文/✗ 会 UnicodeEncodeError）。

        Trae CN 2026-09-13 实测：钩子已走到 trae 分支，但 stdout 是 GBK，
        `print(json.dumps(...))` 抛 UnicodeEncodeError → 整体 traceback、Exit Code 1，
        模型侧表现成「未遇到任何检查」——结论彻底丢失，且从表象完全看不出是编码问题。
        修法：钩子启动即把 stdout/stderr reconfigure 成 UTF-8(errors=replace)。
        """
        env = dict(self.fx.env)
        env["PYTHONIOENCODING"] = "gbk"
        r = subprocess.run([PY, self.hook, "trae", "PostToolUse"], cwd=ROOT,
                           input=self._payload(self.bad), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=180)
        self.assertEqual(r.returncode, 2, f"GBK 管道下钩子崩了：rc={r.returncode}\n{r.stderr[:400]}")
        self.assertIn("门禁", r.stdout)
        self.assertNotIn("UnicodeEncodeError", r.stderr)

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_qoder_pretooluse_deny_contract(self):
        """Qoder：PreToolUse 的阻断 = stdout JSON（permissionDecision=deny）+ **exit 0**。

        出处：Qoder 自带 better-harness 插件的 platforms.mjs::renderQoderBlock（官方适配层）。
        """
        r = self._hook("qoder", self._payload(self.bad), event="PreToolUse")
        self.assertEqual(r.returncode, 0, f"Qoder PreToolUse 要 exit 0 + JSON\n{r.stdout}{r.stderr}")
        data = json.loads(r.stdout)
        out = data["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("门禁", out["permissionDecisionReason"])

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_qoder_posttooluse_json_contract(self):
        """Qoder：非 PreToolUse 事件 = **stdout JSON additionalContext + exit 0**（2026-09-13 实测改判）。

        曾经的实现按"官方文档：exit 2 → stderr 回灌 Agent"写（stdout+stderr 双写 + exit 2），
        但 Qoder CLI 实测把 exit 2 当**钩子失败**：
          "Blocking hook failure(s) [... qoder PostToolUse: unknown error, exit code 2, stderr: harness 门禁未通过…]"
        并且此时模型侧会话段（~/.qoder-cn/logs/sessions/…/segments/*.jsonl）里**搜不到门禁结论**
        —— 钩子跑了、结论发了，但没进模型上下文（判定器"未见回灌证据"是对的）。
        改走 JSON 通道后与 CodeBuddy/Codex 同族；stderr 明文保留给人看。
        反向锁：exit 不能退回 2（那会被 Qoder 记成失败、结论照样丢）。
        """
        r = self._hook("qoder", self._payload(self.bad))
        self.assertEqual(r.returncode, 0, f"Qoder 非 PreToolUse 必须 exit 0\n{r.stdout}{r.stderr}")
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PostToolUse")
        self.assertIn("门禁", out["additionalContext"])
        self.assertIn("门禁未通过", r.stderr, "stderr 明文保留（给人看/留档）")

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_codebuddy_contract_json_additional_context(self):
        """CodeBuddy 契约（2026-09-13 从它的 dist 实证 + 实测确认）：

        非 0 退出码**只把 stderr 给用户看、不回灌模型**（原文：Other exit codes - show stderr
        to user only）——所以照 Claude 那样 exit 2 + stderr 时，模型完全看不到结论
        （实测：钩子确实被触发、日志有记录，但模型说"没有遇到任何检查"）。
        回灌模型必须走 stdout JSON：additionalContext（编辑后）/ permissionDecision=deny（前置阻断）。
        """
        r = self._hook("codebuddy", self._payload(self.bad))
        self.assertEqual(r.returncode, 0, "codebuddy 契约要求 exit 0 + JSON（非 0 只给用户看）")
        data = json.loads(r.stdout)
        self.assertIn("门禁未通过", data["hookSpecificOutput"]["additionalContext"])

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_codebuddy_pretooluse_deny_contract(self):
        """PreToolUse → permissionDecision=deny（真阻断）——留作"危险命令类"前置判断的入口。"""
        r = self._hook("codebuddy", self._payload(self.bad), event="PreToolUse")
        data = json.loads(r.stdout)
        self.assertEqual(data["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("门禁", data["hookSpecificOutput"]["permissionDecisionReason"])

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_clean_file_passes_reverse_lock(self):
        """反向锁：合规文件必须放行——钩子把正常编辑也拦了就是误报型灾难。"""
        os.unlink(self.bad)                      # 关键：钩子走 auto-gate，会收集仓内全部未提交改动
        clean = self.fx.write("src/clean.vue", OK_FILE)
        r = self._hook("claude", self._payload(clean))
        self.assertEqual(r.returncode, 0, f"合规文件应放行\n{r.stdout}{r.stderr}")


class TestHookPathExtraction(unittest.TestCase):
    """`target_file()` 的路径抽取：**命令里的重定向不是"被编辑的文件"**（2026-09-30 修）。

    事故形状（ZCode 会话实录，字节级复现过）：ZCode 的钩子**没有 matcher**（配置里
    `matcher` 缺省 = 所有工具都触发），于是只读的 Bash 命令也进 PostToolUse；而路径抽取
    的第三条规则把 **shell 重定向目标**当成被编辑文件，`python x.py -v > /tmp/t1.log 2>&1`
    里的 `/tmp/t1.log`（或 `2>/dev/null` 里的 `/dev/null`）被原样塞进 `--only` → auto-gate
    一个都匹配不上 → **rc=2 fail-closed** → 每个带重定向的命令都往模型回灌一条
    "harness 门禁调用出错 rc=2（多半是配置问题）"。模型被反复教"门禁坏了"，而真正出问题
    的是**提取**这一步 —— 这类"注入通道自己报假警"比漏检更贵：它会训练 agent 忽略门禁。

    两道防线（两向都锁）：① 重定向目标只给 Codex（它的写入就是 shell 命令，且已过
    WRITE_HINTS）；② 仓外目标一律按"取不到文件"处理（`auto-gate --repo <cwd>` 必然
    匹配不上，是**保证**会响的假警报），交回回合末 Stop 全仓兜底。
    """

    def setUp(self):
        self.fx = _Fixture()
        self.hook = os.path.join(ROOT, "integrations", "hooks", "agentguard-gate.py")

    def tearDown(self):
        self.fx.cleanup()

    def _hook(self, client, payload, event="PostToolUse"):
        return subprocess.run([PY, self.hook, client, event], cwd=ROOT,
                              input=json.dumps(payload), capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              # 批次 4：心跳/跳过写盘隔离（同 TestAgentHookContracts）
                              env=dict(self.fx.env,
                                       HARNESS_HOOK_ACTIVITY_DIR=os.path.join(self.fx.dir, "activity")),
                              timeout=180)

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_zcode_redirect_command_stays_silent(self):
        """★ 主锁：带重定向的**只读**命令必须静默放行（不许产生任何回灌）。"""
        for cmd in ("python harness/run_gate.py -v > /tmp/t1.log 2>&1",
                    "python -m unittest discover -s tests 2>/dev/null",
                    "git log --oneline > /tmp/$t.log 2>&1"):
            r = self._hook("zcode", {"cwd": self.fx.repo, "tool_name": "Bash", "command": cmd})
            self.assertEqual(r.returncode, 0, "%r 不该拦\n%s%s" % (cmd, r.stdout, r.stderr))
            self.assertEqual((r.stdout or "").strip(), "",
                             "%r 产生了回灌 —— 重定向目标又被当成被编辑文件了" % cmd)

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_out_of_tree_path_defers_to_stop(self):
        """仓外路径（哪怕来自 `file_path` 这种最可信的字段）也必须按"取不到"处理。

        理由不是"放宽"，而是 `--only <仓外>` 配上 `--repo <cwd>` **必然** rc=2：
        那条回灌是保证会响的假警报。真实存在的形态是 opencode 把 cwd 设成被编辑文件的
        父目录、以及钩子被别人的编辑器复用 —— 都不该把结论变成"门禁调用出错"。
        """
        outside = os.path.join(self.fx.dir, "not_the_repo", "x.vue")
        r = self._hook("zcode", {"cwd": self.fx.repo, "tool_name": "Write",
                                 "file_path": outside, "content": "x"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((r.stdout or "").strip(), "")

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_codex_in_repo_redirect_still_checked(self):
        """反向锁：Codex 的**写操作**重定向仍要抽出文件并真检查（别把功能一起删了）。

        Codex 在 Windows 上写入就是 shell 命令，`>>` 是它真实形态之一（也在 WRITE_HINTS 里）。
        """
        bad = self.fx.write("src/bad.vue", BAD_FILE)
        r = self._hook("codex", {"cwd": self.fx.repo, "tool_name": "Bash",
                                 "tool_input": {"command": "echo x >> src/bad.vue"}})
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("bad.vue", ctx, "Codex 的 in-repo 重定向写操作没被检查 → 编辑期静默漏检")

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_injected_message_does_not_teach_the_waiver(self):
        """★ 主锁（2026-09-30，用户的安全关切）：注入正文里**不许教**豁免语法。

        `msg` 是**每一次门禁失败/出错都出现、7 家客户端通用**的那一句 —— 最像"门禁
        自己的口吻"。注入通道若可被伪造，攻击者借它就能诱导 agent 把**任意**命中行
        合法豁免。语法因此只住**可信文档**（工程根 `AGENTS.md` 的「行内豁免」一节，
        它本来就在 agent 的上下文里），通道里不再复述。

        判据是**"教"**，不是"提到" —— 这条线不能画错：
        `waiver_summary()`（`rule_base.py:814`）会在**真的用掉豁免**时打一行
        `⚠ 本次用掉 N 条行内豁免（harness-ok）：` 审计，那行是**故意**要可见的
        （`tests/test_review_v13_batch2.py:370` 与 `test_entry_level.py` 的 N7 锁
        都正面要求它出现，理由是"豁免是唯一合法的绕过出口，越看不见越危险"）。
        所以这里**不能**写 `assertNotIn("harness-ok", ctx)`：那样一个"用掉了豁免、
        又另有真违规"的**正常**文件就会把本锁打红 —— 2026-09-30 当场探针验过
        （构造 mix.vue：一行带标记豁免 + 一行未豁免，注入正文里确实带着那行审计），
        即一条**假阳性机器**。要锁的是"谁先开口教"，不是"谁报了自己用过什么"。

        于是判据取**只有教学形态才会命中、审计形态必然不命中**的两点：
        ① 模板占位符 `<规则ID>`（教"怎么拼"）；② 位置指令「紧邻上一行」（教"写哪"）。
        再加一条**兜底**：注入里凡出现 `harness-ok`，就必须同时是那行审计
        （`本次用掉`）—— 除审计行以外的任何提及都算回归。
        """
        # 用 Codex 的**写操作重定向**形态取回灌（与上一条同款）：它的 target 从命令里抽，
        # 走 `file_path` 字段在 codex 契约下抽不到东西 —— 那样会"静默通过"，本锁就成了空转。
        self.fx.write("src/bad.vue", BAD_FILE)
        r = self._hook("codex", {"cwd": self.fx.repo, "tool_name": "Bash",
                                 "tool_input": {"command": "echo x >> src/bad.vue"}})
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        # 正向控制：两种 head（rc=1「未通过」/ rc=2「调用出错」）都以这句开头 ——
        # 只要它是回灌就说明 `msg` 真的拼过；门禁被打哑则这里先红，不会让下面几条空转。
        self.assertIn("harness 门禁", ctx, "这条不是失败回灌，下面几条等于空转\n%s" % ctx)
        self.assertNotIn("<规则ID>", ctx,
                         "注入正文里出现了豁免模板占位符 —— 门禁又在教「怎么拼」了\n%s" % ctx)
        self.assertNotIn("紧邻上一行", ctx,
                         "注入正文里出现了豁免的**位置指令** —— 门禁又在教「写在哪」了\n%s" % ctx)
        if "harness-ok" in ctx:
            self.assertIn("本次用掉", ctx,
                          "注入里提到 harness-ok，却不是「本次用掉」的审计行 —— "
                          "除审计外任何提及都算把语法教回去\n%s" % ctx)

    @_slow("每例真起 hook 子进程跑契约（1.3~2.5s）")
    def test_injected_context_carries_no_waiver_reason_text(self):
        """★ 2026-09-30（与上一条同一条通道的**第二个面**，故分开命名）：

        上一条管的是「**别教**怎么用豁免」；这一条管的是「**别搬运**豁免理由里的字」。
        两者是不同的事故，失败信息也必须能分得开 —— 与 N7 那条"分开命名"的理由同款。

        威胁模型（用户原话）：注入通道若可被伪造，攻击者可诱导 agent 把任意命中行合法
        豁免。③ 已经删掉了**教语法**的两处（hook 的 `msg`、H5 规则命中的 `name`）；
        但 `waiver_summary()` 那一行仍然把**源文件注释里的自由文本**逐字搬进注入 ——
        于是攻击者不必自己编话术：**谁能让一行注释进仓，谁的文字就穿着门禁自己的
        审计口吻进模型上下文**。`Stop` 钩子还会**扫全仓**，所以跨会话也成立。

        做法：造一个**既用掉豁免、又另有真违规**的文件（这一点要紧 —— 只用豁免的话
        门禁 rc=0 不会回灌，本锁就成了空转），理由里塞一个独一无二的标记串，
        然后断言 ① 审计行还在（豁免仍然"看得见"，N7 的实质没被削弱）；
        ② 标记串**不在**注入正文里；③ 规则 ID + 行号**在**（没有反转过头）。
        """
        marker = "ZQMARK-9f13c7-此串不许出现在注入里"
        # ⚠ 空行那行**不是装饰**（2026-09-30 当场踩到）：豁免标记的作用域是
        # 「命中行的本行**或紧邻上一行**」，所以把两行 console.log 挨着写时，
        # L3 的标记会**顺带豁免 L4** ⇒ 门禁 rc=0、**不回灌** ⇒ 本锁在空跑中静默变绿
        # （实测过：那一刻 gate 打的是「本次用掉 2 条」）。隔一个空行才让"另有真违规"
        # 真的存在，本锁的前提（真的有失败回灌）才成立。
        self.fx.write("src/mix.vue",
                      '<template><div /></template>\r\n'
                      '<script setup>\r\n'
                      'console.log("debug")  // harness-ok: NO-CONSOLE ' + marker + '\r\n'
                      '\r\n'
                      'console.log("leak")\r\n'
                      '</script>\r\n')
        r = self._hook("codex", {"cwd": self.fx.repo, "tool_name": "Bash",
                                 "tool_input": {"command": "echo x >> src/mix.vue"}})
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        # 正向控制：没回灌就是空转（未豁免那行必须仍然拦得住）
        self.assertIn("harness 门禁", ctx, "这条不是失败回灌，下面几条等于空转\n%s" % ctx)
        self.assertIn("本次用掉", ctx,
                      "豁免的审计行不见了 —— 本锁的前提（真的用掉了一条豁免）不成立，"
                      "下面那条 assertNotIn 会在空跑中通过\n%s" % ctx)
        self.assertIn("NO-CONSOLE L", ctx,
                      "审计行里必须仍有「规则 ID + 行号」—— 否则就是把「别回显理由」"
                      "做成了「什么都别打印」\n%s" % ctx)
        self.assertNotIn(marker, ctx,
                         "豁免理由里的原文被搬进了**注入正文** —— 那是注释里的自由文本，"
                         "谁能让一行注释进仓，谁就能往模型上下文里投任意文字，"
                         "而且穿着门禁自己的审计口吻。理由留在 diff 里\n%s" % ctx)

    def test_redirect_pattern_does_not_swallow_the_operator(self):
        """`>>` 曾被当成文件名：`echo x >> src/a.vue` 抽出的是 `'>'`（同日实测三例全中）。

        成因：字符类没排除 `>`，且正则里 `>` 先于 `>>` 匹配 → 剩下那个 `>` 落进捕获组。
        后果与主缺陷同形：`--only <仓>/>` → 一个都匹配不上 → rc=2 假警报。
        """
        hk = _load_hook_module()
        for cmd in ("echo x >> src/a.vue", "Add-Content >> src/a.vue 2>&1",
                    "cc a.vue 2>> err.log"):
            got = hk.target_file({"command": cmd}, "codex")
            self.assertNotIn(">", got, "%r 抽出 %r —— 重定向操作符被当成文件名了" % (cmd, got))
        self.assertEqual(hk.target_file({"command": "echo x >> src/a.vue"}, "codex"), "src/a.vue")

    def test_redirect_targets_are_codex_only(self):
        """客户端判据的**唯一差别**就在这一档：命令兜底里只有 Codex 认重定向。

        ZCode 的写入走 Write/Edit 工具（带 file_path、没有 command）—— 它复用 Codex 的
        **输出契约**，不复用命令判据（见正本 main() 里那段注释）。这里连"-LiteralPath"
        那一档一起锁住，防止有人以"反正都要从命令里抽"为由把重定向又放开给所有人。
        """
        hk = _load_hook_module()
        redirect = {"command": "echo x > src/a.vue"}
        self.assertEqual(hk.target_file(redirect, "codex"), "src/a.vue")
        self.assertEqual(hk.target_file(redirect, "zcode"), "")
        self.assertEqual(hk.target_file(redirect), "", "不传 client 时不得默认认重定向")
        # 显式文件参数那一档所有客户端照旧（ZCode 的 Bash 类工具靠它，见既有锁）
        flag = {"command": 'Set-Content -LiteralPath "src/a.vue" -Value x'}
        self.assertEqual(hk.target_file(flag, "zcode"), "src/a.vue")
        self.assertEqual(hk.target_file(flag, "codex"), "src/a.vue")

    def test_in_tree_is_path_aware_not_string_prefix(self):
        """`in_tree` 必须是**按路径**判前缀：`D:/repo2` 不在 `D:/repo` 树内。

        ★ 2026-09-30（CI 红色回修）：末段原先**无条件**断言「大小写不敏感」——
        `in_tree` 靠 `os.path.normcase` 实现，而 **`normcase` 在 POSIX 上是恒等变换**：
        Linux CI 上 `D:/Repo/SRC/a.vue` 与 `d:/repo` 被 abspath 到 cwd 之下后是两个
        大小写不同的真路径，`startswith` 为假 ⇒ `AssertionError: Windows 路径大小写不敏感`。
        实测现场：`4fa05de` 的 harness-ci **两个 Python 版本都红在这一条**（自检失败、
        变异冒烟照过），而本机（Windows）全量 1064 例全绿 —— 典型的「只在另一个平台上
        才红」的锁，本仓管这类叫**假绿**，与 M92 同族。

        修法不是删掉那一句（删了就丢了 Windows 侧的判据），而是**平台忠实**：
        平台用 `os.path.normcase("A") == "a"` 现探，而不是 `os.name` —— 判据直接跟着
        被测函数所用的那套语义走，`in_tree` 换实现时这条锁会跟着说话。
        POSIX 半边断言的是**否定**：大小写不同的两个目录**不许**被当成同一个
        （若这里为真，说明有人把 `normcase` 换成了写死的 `.lower()`，
        那会把两个真实存在的不同目录混为一谈 —— 在这台机器上永远看不出来）。

        ⚠ 护盾的边界（穷举过，两半不对称，这条要记住）：**「整个丢掉 `normcase`」有护盾**
        （`M114`，它在 Windows 上也红 —— `D:\\Repo\\…` 与 `d:\\repo\\` 首字符就对不上）；
        而**「把 `normcase` 换成写死的 `.lower()`」这一半没有、也不该有护盾**：
        它在本机（Windows）上永远变不出红来，硬登记一条只会得到一台**假绿机器** ——
        而这正是本条锁自己刚踩过的那个坑（`4fa05de` 的 CI 红、本机全绿）。
        这一半的第二个平台就是 **CI 的 Linux runner**，不是变异冒烟。
        """
        hk = _load_hook_module()
        self.assertTrue(hk.in_tree("D:/repo/src/a.vue", "D:/repo"))
        self.assertTrue(hk.in_tree("D:/repo", "D:/repo"))
        self.assertFalse(hk.in_tree("D:/repo2/a.vue", "D:/repo"),
                         "字符串前缀当路径前缀用 → 隔壁同名前缀的仓被误判为仓内")
        self.assertFalse(hk.in_tree("/dev/null", "D:/repo"))
        if os.path.normcase("A") == "a":                     # 平台探测：Windows 折叠大小写
            self.assertTrue(hk.in_tree("D:/Repo/SRC/a.vue", "d:/repo"),
                            "本平台 normcase 折叠大小写，`in_tree` 却没跟着折叠")
        else:                                                # POSIX：normcase 是恒等变换
            self.assertFalse(hk.in_tree("D:/Repo/SRC/a.vue", "d:/repo"),
                             "本平台 normcase 是恒等变换，`in_tree` 却把大小写不同的两个路径"
                             "当成同一个 —— 多半是有人把 normcase 换成了写死的 .lower()/.upper()")


class TestVerifyScriptProbesArePerEnd(unittest.TestCase):
    """验证脚本的探针**必须按端写行尾**（PC=CRLF / H5=LF）。

    2026-09-13 实测：`sandbox/verify_integrations.sh` 的"合规探针"硬编码 CRLF（PC 口径），
    跑 H5 gate 时被 H5 行尾规则判为「行尾污染：文件含 CRLF（H5 仓要求 LF）」→
    反向锁输出「❌ 反向锁失败：合规文件被拦（exit=2）—— 误报型灾难」，看起来像门禁误报，
    其实是**探针本身不合规**（门禁判断是对的）。PC 端一直没暴露，因为硬编码正好对。
    这里做源码级锁：合规探针必须按 `$GATE` 分支写行尾；顺便锁住"违规探针仍会触发行尾规则"的前提。
    """

    def setUp(self):
        self.sh = pathlib.Path(os.path.join(ROOT, "sandbox", "verify_integrations.sh")).read_text(encoding="utf-8")

    def test_clean_probe_has_per_end_branch(self):
        self.assertIn('case "$GATE" in', self.sh, "合规探针必须按 gate 分支决定行尾")
        self.assertIn("*_h5)", self.sh, "缺 H5 分支（H5 仓要求 LF）")
        # H5 分支里必须是纯 \n 写法
        m = re.search(r"\*_h5\)(.*?);;", self.sh, re.S)
        self.assertIsNotNone(m, "找不到 *_h5) 分支体")
        body = m.group(1)
        self.assertNotIn("\\r\\n", body, "H5 分支不得写 CRLF")
        self.assertIn("\\n", body, "H5 分支必须用 LF")

    def test_reverse_lock_still_asserts_no_false_block(self):
        """反向锁本身不能删（它是"误报型灾难"的哨兵）。"""
        self.assertIn("反向锁", self.sh)
        self.assertIn("误报型灾难", self.sh)


class TestLedgerEntryFallback(unittest.TestCase):
    """批次 3（2026-10-15）：直调 run_gate 的**台账入口兜底**。

    病灶：run_gate 的写入器原先只在 HARNESS_ENTRY 存在时落 entry 字段 —— doctor / demo /
    acceptance 等直接调 run_gate 的路径与手敲命令在台账里全是**空 entry**（10 月实测 79 条），
    「谁跑的」永久无法归因（CodeBuddy 漏跑排查现场正卡在这）。
    现在：无标者记 "run_gate"，显式标（doctor 等）原样胜出。护盾 M137 把兜底拆掉即红。
    """

    def setUp(self):
        self.dir = _mkdtemp("entryfall-")
        self.repo = os.path.join(self.dir, "repo_gate")
        os.makedirs(os.path.join(self.repo, "src"), exist_ok=True)
        self.vue = os.path.join(self.repo, "src", "a.vue")
        with open(self.vue, "w", encoding="utf-8", newline="") as f:
            f.write("<template><div /></template>\n<script setup>\nconst a = 1;\n</script>\n")
        self.cfg = os.path.join(self.dir, "cfg.toml")
        with open(self.cfg, "w", encoding="utf-8", newline="\n") as f:
            f.write("[repos]\n"
                    "t_gate = \"%s\"\n"
                    "[gates.t_gate]\n"
                    "enabled = true\n"
                    "repo = \"t_gate\"\n"
                    "script = \"checks/example_code_check.py\"\n"
                    % self.repo.replace("\\", "/"))
        self.log = os.path.join(self.dir, "gate_log.jsonl")

    def tearDown(self):
        _rmtree_force(self.dir)

    def _direct(self, extra_env=None):
        env = dict(os.environ, HARNESS_CONFIG=self.cfg, HARNESS_LOG_FILE=self.log)
        env.pop("HARNESS_ENTRY", None)
        if extra_env:
            env.update(extra_env)
        return _run([PY, os.path.join(ROOT, "harness", "run_gate.py"), "t_gate", self.vue], env=env)

    def _last_rec(self):
        with open(self.log, encoding="utf-8") as f:
            recs = [json.loads(l) for l in f.read().splitlines() if l.strip()]
        self.assertTrue(recs, "直调必须留下台账记录")
        return recs[-1]

    @_slow("每例起 run_gate 子进程 + 临时配置（~1.5s）")
    def test_direct_run_gate_records_fallback_entry(self):
        r = self._direct()
        self.assertIn(r.returncode, (0, 1), r.stdout + r.stderr)
        self.assertEqual(self._last_rec().get("entry"), "run_gate",
                         "无标直调必须落 run_gate —— 空 entry 就是归因断头（ M137）")

    @_slow("每例起 run_gate 子进程 + 临时配置（~1.5s）")
    def test_explicit_entry_wins_over_fallback(self):
        r = self._direct({"HARNESS_ENTRY": "doctor"})
        self.assertIn(r.returncode, (0, 1), r.stdout + r.stderr)
        self.assertEqual(self._last_rec().get("entry"), "doctor",
                         "显式标必须原样胜出（doctor/demo/acceptance 的命名接线）")


class TestHookEntryAttribution(unittest.TestCase):
    """批次 3（2026-10-15）：编辑器钩子的**来源归因** —— via=hook:<client>:<event>。

    原先所有编辑器钩子一律塌缩成 entry=auto-gate，「哪家客户端、哪个事件」分不出来；
    CodeBuddy 漏跑排查现场正卡在这（台账里分不清是钩子跑的还是人跑的）。
    """

    def setUp(self):
        self.fx = _Fixture()
        self.hook = os.path.join(ROOT, "integrations", "hooks", "agentguard-gate.py")
        self.bad = self.fx.write("src/bad.vue", BAD_FILE)

    def tearDown(self):
        self.fx.cleanup()

    def test_hook_source_hard_assigns_entry(self):
        src = _read("integrations", "hooks", "agentguard-gate.py")
        self.assertIn('os.environ["HARNESS_ENTRY"] = "hook:%s:%s" % (client, event)', src,
                      "编辑器钩子必须硬赋值 hook:<client>:<event> —— 否则台账分不出哪家触发的")
        self.assertNotIn('setdefault("HARNESS_ENTRY"', src,
                         "用 setdefault → 外层残留值会冒充本次来源")

    @_slow("真起 hook 子进程（1.3~2.5s）")
    def test_hook_run_records_via_attribution(self):
        log = os.path.join(self.fx.dir, "hook_gate_log.jsonl")
        payload = json.dumps({"cwd": self.fx.repo, "tool_name": "Write",
                              "tool_input": {"file_path": self.bad, "content": "x"}})
        r = subprocess.run([PY, self.hook, "codebuddy", "PostToolUse"], cwd=ROOT, input=payload,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=dict(self.fx.env, HARNESS_LOG_FILE=log), timeout=180)
        self.assertEqual(r.returncode, 0, "CodeBuddy 契约：exit 0 + stdout JSON")
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("门禁未通过", ctx)
        with open(log, encoding="utf-8") as f:
            recs = [json.loads(l) for l in f.read().splitlines() if l.strip()]
        self.assertTrue(recs, "钩子必须真的跑了门禁（台账为空 = 静默没跑）")
        self.assertEqual(recs[-1].get("entry"), "auto-gate")
        self.assertEqual(recs[-1].get("via"), "hook:codebuddy:PostToolUse",
                         "via 归因没落到台账 —— 漏跑排查会失去仪表（M138）")


class TestHookVisibility(unittest.TestCase):
    """批次 4（2026-10-15）：钩子可见性三件 —— 心跳 / 跳过不静默 / payload 捕获。

    病灶（CodeBuddy 对照实验）：IDE 形态 PostToolUse 零记录，而「钩子没被调」与
    「调了但提取失败被静默放行」在台账上不可区分。三向钉住：
      ① 写类事件无文件 → 心跳 skip-no-file + skip 行；
      ② 读类事件无文件 → 只心跳、不落 skip（防刷屏闸门）；
      ③ dump 开关 → 留证且长串截断（不允许把代码全文写盘）。
    """

    def setUp(self):
        self.fx = _Fixture()
        self.hook = os.path.join(ROOT, "integrations", "hooks", "agentguard-gate.py")
        self.act = os.path.join(self.fx.dir, "activity")

    def tearDown(self):
        self.fx.cleanup()

    def _hook(self, payload, client="codebuddy", event="PostToolUse"):
        env = dict(self.fx.env, HARNESS_HOOK_ACTIVITY_DIR=self.act,
                   HARNESS_LOG_FILE=os.path.join(self.fx.dir, "hook_gate_log.jsonl"))
        return subprocess.run([PY, self.hook, client, event], cwd=ROOT, input=payload,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, timeout=180)

    def _activity(self):
        with open(os.path.join(self.act, "hook-activity-codebuddy.json"), encoding="utf-8") as f:
            return json.load(f)

    @_slow("真起 hook 子进程（1.3~2.5s）")
    def test_write_without_file_leaves_heartbeat_and_skip(self):
        r = self._hook(json.dumps({"cwd": self.fx.repo, "tool_name": "Write",
                                   "tool_input": {"content": "x"}}))
        self.assertEqual(r.returncode, 0)
        a = self._activity()
        self.assertEqual(a["last_decision"], "skip-no-file",
                         "心跳必须记下本次决策（没跑 vs 静默放行，靠它区分）")
        skips = os.path.join(self.act, "hook-skips-%s.jsonl" % a["day"][:7])
        self.assertTrue(os.path.isfile(skips), "写类事件提取失败必须留跳过记录（不静默）")
        with open(skips, encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]
        self.assertEqual(lines[-1]["client"], "codebuddy")
        self.assertEqual(lines[-1]["decision"], "no-file")

    @_slow("真起 hook 子进程（1.3~2.5s）")
    def test_read_event_does_not_write_skip_noise(self):
        """读类工具（无文件可取）不应写 skip 行 —— 防刷屏闸门（会训练人忽略它）。"""
        r = self._hook(json.dumps({"cwd": self.fx.repo, "tool_name": "Read",
                                   "tool_input": {"query": "x"}}))
        self.assertEqual(r.returncode, 0)
        a = self._activity()
        self.assertEqual(a["last_decision"], "skip-no-file")
        skips = os.path.join(self.act, "hook-skips-%s.jsonl" % a["day"][:7])
        self.assertFalse(os.path.isfile(skips), "读类事件不该落 skip 行")

    @_slow("真起 hook 子进程（1.3~2.5s）")
    def test_dump_records_payload_redacted(self):
        bad = self.fx.write("src/bad.vue", BAD_FILE)
        os.makedirs(self.act, exist_ok=True)
        with open(os.path.join(self.act, "hook-dump.on"), "w") as f:
            f.write("1")
        long_content = "x" * 500
        r = self._hook(json.dumps({"cwd": self.fx.repo, "tool_name": "Write",
                                   "tool_input": {"file_path": bad, "content": long_content}}))
        self.assertEqual(r.returncode, 0, "CodeBuddy PostToolUse 契约恒 exit 0")
        with open(os.path.join(self.act, "hook-payload-last-codebuddy.json"),
                  encoding="utf-8") as f:
            dump = json.load(f)
        self.assertEqual(dump["payload"]["tool_input"]["file_path"], bad)
        self.assertIn("<len 500>", dump["payload"]["tool_input"]["content"],
                      "长字符串必须截断 —— dump 不允许把代码全文写盘")
        self.assertEqual(self._activity()["last_decision"], "ran:rc=1",
                         "坏文件应被拦（rc=1）且心跳记 ran —— 两机制同链")


class TestRepoRootFromFile(unittest.TestCase):
    """批次 6（2026-10-15，CodeBuddy IDE 抓包实测）：仓根必须**从文件路径反推**。

    病灶：CodeBuddy IDE 的 `payload.cwd` 是它自己的**安装目录**（实测
    `D:\\...\\Programs\\CodeBuddy CN`）⇒ 旧实现 ① `in_tree` 判「不在树内」
    （skip-out-of-tree）② 即便放行 `--repo` 也指向安装目录 ⇒ **编辑期门禁 100% 静默放行**；
    而被编辑文件是绝对路径、Write/Edit 两种工具都能取到。护盾 M142：拆掉“从文件反推”即红。
    """

    def setUp(self):
        self.fx = _Fixture()
        self.hook = os.path.join(ROOT, "integrations", "hooks", "agentguard-gate.py")
        self.act = os.path.join(self.fx.dir, "activity")
        # ★ 必须预建：门禁台账写进**不存在**的目录会静默失败（“台账写入失败不反拦门禁”
        #   是既定设计）—— 那是产品行为、不是本测试要测的东西，所以夹具把目录先造好。
        os.makedirs(self.act, exist_ok=True)
        self.log = os.path.join(self.fx.dir, "hook_gate_log.jsonl")
        self.bad = self.fx.write("src/bad.vue", BAD_FILE)
        # 夹具仓要变成**真 git 仓**：_repo_root_for 的判据是「文件在 git 工作树里」
        subprocess.run(["git", "init", "-q"], cwd=self.fx.repo, capture_output=True)

    def tearDown(self):
        self.fx.cleanup()

    def _hook(self, payload):
        env = dict(self.fx.env, HARNESS_HOOK_ACTIVITY_DIR=self.act, HARNESS_LOG_FILE=self.log)
        return subprocess.run([PY, self.hook, "codebuddy", "PostToolUse"], cwd=ROOT,
                              input=json.dumps(payload), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env, timeout=180)

    def _activity(self):
        with open(os.path.join(self.act, "hook-activity-codebuddy.json"), encoding="utf-8") as f:
            return json.load(f)

    @_slow("真起 hook 子进程 + git 仓夹具（~2.5s）")
    def test_wrong_cwd_still_gates_the_edited_file(self):
        """cwd 指向“安装目录”（非仓、非工作区）时，仍须按 filePath 找到仓并把门跑起来。"""
        fake_install = os.path.join(self.fx.dir, "fake_install")
        os.makedirs(fake_install, exist_ok=True)
        r = self._hook({"cwd": fake_install, "tool_name": "Write",
                        "tool_input": {"filePath": self.bad, "content": "x"}})
        self.assertEqual(r.returncode, 0, "CodeBuddy 契约恒 exit 0")
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("门禁未通过", ctx, "坏文件必须被真检查到（修复前这里会静默放行）")
        self.assertEqual(self._activity()["last_decision"], "ran:rc=1",
                         "必须真跑门禁（ran），而不是 skip-out-of-tree（M142）")

    @_slow("真起 hook 子进程（~1.5s）")
    def test_file_outside_any_repo_still_skips(self):
        """反锁：文件不在任何 git 仓内（重定向目标一类）→ 维持旧判据，跳过而不是乱跑。"""
        loose = os.path.join(self.fx.dir, "loose", "x.vue")
        os.makedirs(os.path.dirname(loose), exist_ok=True)
        with open(loose, "w", encoding="utf-8") as f:
            f.write("x")
        r = self._hook({"cwd": self.fx.repo, "tool_name": "Write",
                        "tool_input": {"filePath": loose, "content": "x"}})
        self.assertEqual(r.returncode, 0)
        self.assertEqual((r.stdout or "").strip(), "", "不该产生任何回灌")
        self.assertEqual(self._activity()["last_decision"], "skip-out-of-tree")


if __name__ == "__main__":
    unittest.main()
