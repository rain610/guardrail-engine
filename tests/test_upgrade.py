# -*- coding: utf-8 -*-
"""upgrade.py 回归锁：预演不动手 / 路径适配不可跳过 / 只刷新已装通道 / 同版本静默的现场检测。

为什么这些锁必须存在（升级链路剖析报告 G2 + 测试章）：G1–G8 里**没有一条有锁** ——
不是"测试写得少"，而是"升级这条路从来没被当成一条路来测"：只测了两端（装、卸），
中间那段没人走。本文件覆盖中间那段。

安全约定：全部在临时 HOME / 临时包目录里跑，并且**任何 `--yes` 的运行都必须显式
`--skip claude` 或 `--claude <假 CLI>`** —— 本机 PATH 上可能真有 `claude`，
一次误调就会去改真机的插件配置（`_run` 里对此有硬断言，别绕过）。
"""
import argparse
import ast
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# 快跑（HARNESS_FAST=1，2026-09-17 批 4 接线）：本文件慢在**真跑 upgrade.py 全流程**
# （真起临时 HOME + 真跑六个步骤），接线前完全没接（全文 0 处 HARNESS_FAST），
# 快跑档单跑 8.8~12.0s。
# 落点 = **方法级 5 条 ≥1s**（1.93 / 1.75 / 1.29 / 1.13 / 1.07s），全在 UpgradeTest 里；
# 同类另 3 条亚秒校验锁（0.34~0.59s：未知 skip 键必须拒绝 / 缺 harness 必须带理由拒绝 /
# 路径改写失败必须中止全部通道）**留跑** —— 它们是"改坏了立刻知道"的那类，类级会一起清掉。
# TestUnitGuards 的 4 条全 <0.3s（纯逻辑），不动。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "sandbox", "upgrade.py")
FIXER = os.path.join(ROOT, "sandbox", "fix_paths.py")
PLUGIN = "agentguard@agentguard-tools"
PLACEHOLDER_MCP = json.dumps({
    "mcpServers": {"agentguard": {
        "command": "__PY__", "args": ["__MCP__"], "env": {"AGENTGUARD_DIR": "__HARNESS__"},
    }},
}, indent=2) + "\n"


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _read(path):
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _fake_exe(root, name, rc=0, log=None):
    """造一个"能被 run_cmd 跑起来、退出码可控"的假命令；**返回的路径必须带 .cmd 后缀**。

    （踩过：Windows 上给**短名**（无扩展名）起不来 → rc=127 → 探针把"假解释器没跑起来"
    读成"这台机器没装过 pip 包"而提前返回。）

    ⚠ 2026-09-23（评审 L14）订正旧注释：这里原先写的理由是"run_cmd 只对 .cmd/.bat 走
    `cmd /c`"——那层 `cmd /c` 已按 L14 **删掉**（实测结论见 upgrade.py::run_cmd 的 docstring）。
    后缀要求**照旧成立**，但原因与 run_cmd 无关：`subprocess` 最终落到 `CreateProcessW`，
    它只按 PATH 找、无扩展名时**只补 `.exe`、不补 `.cmd`** ⇒ 短名会去找 `name.exe`、找不到；
    而显式写 `name.cmd` 时**操作系统本人**就认这个真实文件。换 run_cmd 的实现不影响这条。
    """
    if os.name == "nt":
        p = os.path.join(root, name + ".cmd")
        body = "@echo off\r\n"
        if log:
            body += 'echo %* >> "%s"\r\n' % log
        body += "exit /b %d\r\n" % rc
        _write(p, body)
    else:
        p = os.path.join(root, name)
        body = "#!/bin/sh\n"
        if log:
            body += 'echo "$@" >> %s\n' % log
        body += "exit %d\n" % rc
        _write(p, body)
        os.chmod(p, 0o755)
    return p


class UpgradeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)     # 显式清理：GC 期抛 ResourceWarning 会让 CI 严格门槛红
        self.base = self.tmp.name
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self.ops = os.path.join(self.base, "ops.jsonl")

    # ---- 夹具 ----

    def _fake_exe(self, name, rc=0, log=None):
        return _fake_exe(self.base, name, rc=rc, log=log)

    def _harness(self, with_cli=True):
        h = os.path.join(self.base, "harness")
        _write(os.path.join(h, "agentguard", "mcp.py"), "# 只为让 fix_paths 的校验通过\n")
        if with_cli:
            _write(os.path.join(h, "agentguard", "cli.py"), "# 假 CLI\n")
        return h

    def _pack(self, version="0.1.2-dev", wheels=1):
        pack = os.path.join(self.base, "pack")
        bundle = os.path.join(pack, "marketplace", "plugins", "agentguard")
        _write(os.path.join(bundle, ".claude-plugin", "plugin.json"),
               json.dumps({"name": "agentguard", "version": version}, indent=2) + "\n")
        _write(os.path.join(bundle, ".mcp.json"), PLACEHOLDER_MCP)
        _write(os.path.join(bundle, "README.md"), "# 本包这一版\n")
        _write(os.path.join(pack, "marketplace", ".claude-plugin", "marketplace.json"),
               json.dumps({"name": "agentguard-tools"}, indent=2) + "\n")
        shutil.copyfile(FIXER, os.path.join(pack, "fix_paths.py"))
        for i in range(wheels):
            _write(os.path.join(pack, "agentguard-0.1.2.dev%d-py3-none-any.whl" % i), "PK\x03\x04")
        return pack

    def _install_plugin_copy(self, version="0.1.2-dev", readme="# 旧内容\n"):
        """在假 HOME 里造一份"已装副本"+ 宿主登记（模拟装过插件的机器）。"""
        cfg = os.path.join(self.home, ".claude")
        inst = os.path.join(cfg, "plugins", "cache", "agentguard-tools", "agentguard", version)
        _write(os.path.join(inst, ".claude-plugin", "plugin.json"),
               json.dumps({"name": "agentguard", "version": version}, indent=2) + "\n")
        _write(os.path.join(inst, ".mcp.json"), json.dumps({
            "mcpServers": {"agentguard": {
                "command": sys.executable,
                "args": [os.path.join(self.base, "harness", "agentguard", "mcp.py")],
                "env": {"AGENTGUARD_DIR": self.base.replace("\\", "/") + "/harness"},
            }}}, indent=2) + "\n")
        _write(os.path.join(inst, "README.md"), readme)
        _write(os.path.join(cfg, "plugins", "installed_plugins.json"),
               json.dumps({"version": 2, "plugins": {PLUGIN: [
                   {"scope": "user", "installPath": inst, "version": version}]}}, indent=2) + "\n")
        return inst

    # ---- 运行 ----

    def _run(self, *args, env_extra=None):
        args = list(args)
        # 硬护栏：--yes 且没点名跳过/注入 claude 时，本机会真的去调 PATH 上的 claude →
        # 那是**真机配置**（用户自己的插件市场）。宁可测试红，也不能悄悄动它。
        if "--yes" in args:
            skips = [args[i + 1] for i in range(len(args) - 1) if args[i] == "--skip"]
            self.assertTrue("--claude" in args or any("claude" in s for s in skips),
                            "测试纪律：--yes 必须 --skip claude 或 --claude <假 CLI>（否则会动真机）")
        env = dict(os.environ)
        env["HOME"] = self.home
        env["USERPROFILE"] = self.home
        env["CLAUDE_CONFIG_DIR"] = os.path.join(self.home, ".claude")
        env["OPENCLAW_STATE_DIR"] = os.path.join(self.home, ".openclaw-autoclaw")
        env["AGENTGUARD_OPS_LOG"] = self.ops
        for k in ("AGENTGUARD_DIR", "HARNESS_DIR"):
            env.pop(k, None)
        if env_extra:
            env.update(env_extra)
        return subprocess.run([sys.executable, SCRIPT] + args, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env)

    # ---- 用例 ----

    @_slow("真跑 upgrade.py 全六步的 --dry-run 支路（1.93s）")
    def test_preview_changes_nothing(self):
        """预演必须一个字节都不写：包内 .mcp.json 仍是占位符，且不提任何通道的归档/覆盖。"""
        pack = self._pack()
        mj = os.path.join(pack, "marketplace", "plugins", "agentguard", ".mcp.json")
        before = _read(mj)
        r = self._run("--pack", pack, "--harness", self._harness(), "--skip", "claude")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("预演", r.stdout)
        self.assertIn("将执行", r.stdout)
        self.assertEqual(_read(mj), before, "预演改了包内 .mcp.json")
        self.assertIn("__PY__", _read(mj), "预演就把占位符填了 —— 预演的定义是「不改任何东西」")

    @_slow("真跑六个步骤 + 落台账（1.07s）")
    def test_execute_adapts_paths_and_records_ledger(self):
        """执行时第一步就是路径适配，且台账留痕（entry=upgrade / step=paths）。"""
        pack = self._pack()
        h = self._harness()
        r = self._run("--pack", pack, "--harness", h, "--yes", "--skip", "claude,openclaw,pip,skills,shim")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("适配完成", r.stdout)
        mj = os.path.join(pack, "marketplace", "plugins", "agentguard", ".mcp.json")
        got = json.loads(_read(mj))["mcpServers"]["agentguard"]
        self.assertNotIn("__", got["command"], "占位符没被填掉：%r" % got["command"])
        self.assertEqual(got["env"]["AGENTGUARD_DIR"], h.replace("\\", "/"),
                         "引擎路径没适配到 --harness 指定的那份")
        log = _read(self.ops)
        self.assertIn("machine_upgrade", log)
        self.assertIn('"step": "paths"', log)

    def test_path_adaptation_failure_aborts_every_channel(self):
        """适配不过 → **后面一律不做**（只跳过适配继续刷通道，等于把坏路径装进副本）。"""
        pack = self._pack()
        bad = os.path.join(self.base, "not-a-harness")   # 没有 agentguard/mcp.py
        r = self._run("--pack", pack, "--harness", bad, "--yes", "--skip", "claude,openclaw,pip,skills,shim")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("路径适配失败", r.stdout)
        self.assertNotIn("[2/6]", r.stdout, "适配都没过，却继续往下刷通道了")
        self.assertIn("__PY__", _read(os.path.join(pack, "marketplace", "plugins",
                                                   "agentguard", ".mcp.json")))

    @_slow("真跑「只刷已装通道」（1.13s）")
    def test_only_refreshes_channels_that_are_installed(self):
        """升级 ≠ 新增通道：机器上一路都没装时，不许凭空装出任何目录来。"""
        pack = self._pack()
        r = self._run("--pack", pack, "--harness", self._harness(), "--yes",
                      "--skip", "claude,pip,skills,shim")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("升级不替你新增通道", r.stdout)
        for unexpected in (".agentguard", ".openclaw-autoclaw"):
            self.assertNotIn(unexpected, sorted(os.listdir(self.home)),
                             "假 HOME 里凭空多出了 %s（升级不许新增通道）" % unexpected)

    @_slow("真跑同版本静默 noop 那一路（1.75s）")
    def test_same_version_silent_noop_is_called_out(self):
        """G3 的现场：同版本号 + 内容不同 → `plugin update` 绿着什么都不换，升级必须当场说破。"""
        pack = self._pack(version="0.1.2-dev")
        self._install_plugin_copy(version="0.1.2-dev", readme="# 旧内容，与包不同\n")
        cli = self._fake_exe("claude", rc=0)
        r = self._run("--pack", pack, "--harness", self._harness(), "--yes",
                      "--skip", "openclaw,pip,skills,shim", "--claude", cli)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("版本号相同", r.stdout)
        self.assertIn("什么都没换", r.stdout)
        self.assertNotIn("✅ 升级完成", r.stdout, "内容没刷新却报成功（rc 已经给了 1，文案更不许骗人）")

    @_slow("真跑「已是最新」那一路（1.29s）")
    def test_up_to_date_copy_is_reported_as_success(self):
        """基线（防恒真）：副本内容确实与本包一致时，不许再报"没刷新"。"""
        pack = self._pack(version="0.1.2-dev")
        inst = self._install_plugin_copy(version="0.1.2-dev", readme="# 旧内容\n")
        # 让副本除 .mcp.json 外与本包逐字节相同（.mcp.json 本就是"每台机器一份"，不参与对账）
        shutil.copyfile(os.path.join(pack, "marketplace", "plugins", "agentguard",
                                     ".claude-plugin", "plugin.json"),
                        os.path.join(inst, ".claude-plugin", "plugin.json"))
        shutil.copyfile(os.path.join(pack, "marketplace", "plugins", "agentguard", "README.md"),
                        os.path.join(inst, "README.md"))
        cli = self._fake_exe("claude", rc=0)
        r = self._run("--pack", pack, "--harness", self._harness(), "--yes",
                      "--skip", "openclaw,pip,skills,shim", "--claude", cli)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("副本内容与本包一致", r.stdout)
        self.assertIn("✅ 升级完成", r.stdout)

    def test_unknown_skip_key_is_rejected(self):
        r = self._run("--pack", self._pack(), "--harness", self._harness(), "--skip", "everything")
        self.assertEqual(r.returncode, 1)
        self.assertIn("不认识", r.stdout)

    def test_missing_harness_is_refused_with_a_reason(self):
        """不给引擎仓就要当场拒绝并说明为什么 —— 而不是"装完了、但门禁整体失效"。"""
        r = self._run("--pack", self._pack())
        self.assertEqual(r.returncode, 1)
        self.assertIn("--harness", r.stdout)

    def test_shipped_tool_never_removes_anything(self):
        """结构锁：升级**不做任何撤回**（撤销属于 uninstall.py 的语义，不属于升级）。

        这条不是洁癖：升级面一旦夹带删除动作，"刷新"与"重装"就没有区别了 —— 而同事选升级
        正是为了避开卸载那一段空窗（G2 的原话：工具空缺把人推向了那条唯一能一键跑完的路）。

        判据走 AST，不 grep 全文：本脚本的文档字符串里**必须**能写"没有 rmtree / os.remove"
        这句话（那是给人的承诺）。grep 全文会把"写着不删"判成"删了"——那种锁只会逼人删注释。
        """
        tree = ast.parse(_read(SCRIPT))
        bad_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("rmtree", "remove", "unlink", "rmdir", "removedirs"):
                    bad_calls.append("%s (line %d)" % (node.func.attr, node.lineno))
        self.assertEqual(bad_calls, [], "升级脚本里出现了删除类调用：%s" % bad_calls)

        # 真正跑起来的命令（argv 形状的列表字面量）里不许有撤回类动词
        argvs = []
        for node in ast.walk(tree):
            if isinstance(node, ast.List) and node.elts:
                parts = [e.value for e in node.elts if isinstance(e, ast.Constant)]
                if len(parts) >= 2 and all(isinstance(p, str) for p in parts):
                    argvs.append(" ".join(parts))
        self.assertTrue(any("marketplace add" in a for a in argvs),
                        "一条 argv 都没解析到（或升级命令没了）—— 本锁会因此空转：%s" % argvs)
        for a in argvs:
            self.assertNotIn("uninstall", a, "升级会跑撤回类命令：%s" % a)
            self.assertNotIn("remove", a, "升级会跑撤回类命令：%s" % a)
        self.assertIn("install_shim.py", _read(SCRIPT), "编辑期 shim 那一格丢了（G5 的装机产物）")

    def test_upgrade_commands_are_the_two_verified_ones(self):
        """覆盖安装痕迹：插件通道必须是 `marketplace add` + `plugin update` 两条。

        （实测 2026-09-17：同名市场会被静默改指到新路径，不需要 remove / uninstall。）
        """
        src = _read(SCRIPT)
        for kw in ('"plugin", "marketplace", "add"', '"plugin", "update"'):
            self.assertIn(kw, src, "升级没走这条命令：%s" % kw)


class TestUnitGuards(unittest.TestCase):
    """不跑整机的判据点：直接 import 模块，避免测试去动真机的 pip / 真插件。"""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("_upgrade_mod", SCRIPT)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _ns(self, **kw):
        import argparse
        a = argparse.Namespace(python=sys.executable, pack=self.tmp.name, yes=True,
                               claude=None, openclaw_state=os.path.join(self.tmp.name, "ow"),
                               skill_dir=[], harness_norm="", harness_cli="")
        for k, v in kw.items():
            setattr(a, k, v)
        return a

    def test_pip_channel_is_left_alone_when_never_installed(self):
        """没装过 pip 包 → 升级**不动它**（不替你装 CLI；那是「安装」）。"""
        a = self._ns(python=os.path.join(self.tmp.name, "no-such-python"))
        self.assertEqual(self.mod.step_pip(a), [])

    def test_pip_channel_refuses_an_ambiguous_pack(self):
        """包内轮子不是恰好一个时当场拒绝 —— 不然 `pip install -U` 会装到哪个全看运气。"""
        for n in (0, 2):
            pack = os.path.join(self.tmp.name, "pack%d" % n)
            os.makedirs(pack, exist_ok=True)
            for i in range(n):
                _write(os.path.join(pack, "x%d.whl" % i), "PK")
            # 假 pip 退出码 0 = "这台机器装过 pip 包" → 探针放行，才会走到轮子数量那一步
            fake = _fake_exe(self.tmp.name, "pip%d" % n, rc=0)
            fails = self.mod.step_pip(self._ns(python=fake, pack=pack, yes=False))
            self.assertTrue(any("轮子数量异常" in f for f in fails),
                            "包内 %d 个轮子却没拦住：%s" % (n, fails))

    def test_tree_hash_ignores_the_per_machine_file(self):
        """`.mcp.json` 是每台机器一份 → 不参与内容对账（否则换台机器就假报"内容不同"）。"""
        d1 = os.path.join(self.tmp.name, "a")
        d2 = os.path.join(self.tmp.name, "b")
        for d, mj in ((d1, '{"x": 1}'), (d2, '{"x": 2}')):
            _write(os.path.join(d, "keep.md"), "same\n")
            _write(os.path.join(d, ".mcp.json"), mj)
        self.assertEqual(self.mod.tree_hash(d1)[0], self.mod.tree_hash(d2)[0])
        _write(os.path.join(d2, "keep.md"), "different\n")
        self.assertNotEqual(self.mod.tree_hash(d1)[0], self.mod.tree_hash(d2)[0],
                            "正文变了指纹却不动 —— 这条对账等于装饰")

    def test_installed_mcp_check_separates_hard_faults_from_notes(self):
        """占位符 / 指向不存在的位置 = 硬故障；只是换了台本机可用的解释器 = 提示（不是错误）。

        判据口径很重要：把"用的解释器和我这次不一样"也报成 ⚠，这条提示很快就会贬值
        —— 本仓纪律：假警报比漏报更磨损可信度。

        断言到**级别**，不止"有报错"：变异冒烟实测，把占位符那条判据删掉后本用例仍会绿 ——
        因为 `__PY__` 恰好也是个不存在的路径，被下一条判据兜住了。级别相同、**诊断不同**：
        用户会看到"路径不存在"，而不是"你漏跑了 fix_paths.py"。占位符是最常见的那种漏步，
        这句话丢了，这条检查就只剩一个红叉。所以下面按用例逐条钉级别。
        """
        harness = os.path.join(self.tmp.name, "h")
        _write(os.path.join(harness, "agentguard", "mcp.py"), "#\n")
        d = os.path.join(self.tmp.name, "plug")
        # 一个"本机确实存在、但不是本次升级用的那个"解释器（同事机器上宿主自带 python 就是这种）
        other_py = os.path.join(self.tmp.name, "other-python.exe")
        _write(other_py, "")
        mcp = os.path.join(harness, "agentguard", "mcp.py")
        cases = (
            ("漏跑 fix_paths（占位符）",
             {"command": "__PY__", "args": [mcp], "env": {"AGENTGUARD_DIR": harness}},
             ["bad"], "占位符"),
            ("解释器占位符",
             {"command": sys.executable, "args": ["__MCP__"], "env": {"AGENTGUARD_DIR": harness}},
             ["bad"], "占位符"),
            ("mcp 路径指向别处（本机没有）",
             {"command": sys.executable, "args": ["D:/gone/mcp.py"],
              "env": {"AGENTGUARD_DIR": harness}}, ["bad"], "不存在"),
            ("引擎目录不存在",
             {"command": sys.executable, "args": [mcp],
              "env": {"AGENTGUARD_DIR": os.path.join(self.tmp.name, "gone-dir")}},
             ["bad"], "不存在"),
            ("换了台本机可用的解释器（能跑，只是与本次不同）",
             {"command": other_py, "args": [mcp], "env": {"AGENTGUARD_DIR": harness}},
             ["note"], "不同而已"),
            ("完全对得上",
             {"command": sys.executable, "args": [mcp], "env": {"AGENTGUARD_DIR": harness}},
             [], None),
        )
        for label, fields, want_levels, want_words in cases:
            _write(os.path.join(d, ".mcp.json"),
                   json.dumps({"mcpServers": {"agentguard": fields}}, indent=2) + "\n")
            got = self.mod.check_installed_mcp(d, harness.replace("\\", "/"), sys.executable)
            self.assertEqual([lvl for lvl, _ in got], want_levels, "%s → %s" % (label, got))
            if want_words:
                self.assertTrue(any(want_words in txt for _, txt in got),
                                "%s：级别对了但话没说到点子上（缺 %r）→ %s"
                                % (label, want_words, got))


if __name__ == "__main__":
    unittest.main()
