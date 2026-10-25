# -*- coding: utf-8 -*-
"""uninstall.py 回归锁（v0.1.2-dev）：预演不动手 / 归档可逆 / 防误删 / 转发 cli 台账。

全部在临时目录进行（HOME 重定向 + --skip 隔离 + 台账重定向），不碰真实安装与真实台账。
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "sandbox", "uninstall.py")
SKILL_NAME = "guardrail-engine"


def _read(path):
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


class UninstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # 显式清理：不清理会在 GC 期抛 ResourceWarning（被测的 CI 严格句柄门槛会红）
        self.addCleanup(self.tmp.cleanup)
        self.base = self.tmp.name
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self.state = os.path.join(self.home, ".openclaw-autoclaw")
        self.ext = os.path.join(self.state, "extensions", "agentguard")
        os.makedirs(os.path.join(self.ext, ".claude-plugin"))
        with io.open(os.path.join(self.ext, ".claude-plugin", "plugin.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        # 技能副本（claude 预设目录；HOME 已重定向，不会碰真机）
        self.skill = os.path.join(self.home, ".claude", "skills", SKILL_NAME)
        os.makedirs(self.skill)
        with io.open(os.path.join(self.skill, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("# x\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args, home=None, env_extra=None):
        env = dict(os.environ)
        env["HOME"] = home or self.home            # POSIX expanduser
        env["USERPROFILE"] = home or self.home     # Windows expanduser
        env.pop("GUARDRAIL_ENGINE_DIR", None)
        env.pop("AGENTGUARD_DIR", None)
        env.pop("HARNESS_DIR", None)
        env.pop("OPENCLAW_STATE_DIR", None)
        if env_extra:
            env.update(env_extra)
        return subprocess.run([sys.executable, SCRIPT] + list(args),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env)

    def _listdir(self, d):
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def test_preview_changes_nothing(self):
        r = self._run("--skip", "pip,claude", "--openclaw-state", self.state,
                      "--harness", os.path.join(self.base, "noharness"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("预演", r.stdout)
        self.assertIn("将", r.stdout)
        self.assertTrue(os.path.isdir(self.ext), "预演不得移动扩展目录")
        self.assertTrue(os.path.isdir(self.skill), "预演不得归档技能副本")
        self.assertEqual(self._listdir(self.state), ["extensions"], "预演不得产生归档目录")

    def test_execute_archives_extension_and_skill_fallback(self):
        ops = os.path.join(self.base, "ops.jsonl")
        r = self._run("--yes", "--skip", "pip,claude", "--openclaw-state", self.state,
                      "--harness", os.path.join(self.base, "noharness"),
                      env_extra={"AGENTGUARD_OPS_LOG": ops})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("已移出归档", r.stdout)
        self.assertIn("已归档", r.stdout)
        self.assertFalse(os.path.isdir(self.ext), "扩展应已被移出")
        names = self._listdir(self.state)
        self.assertTrue(any(n.startswith("agentguard.uninstalled-") for n in names), names)
        self.assertFalse(os.path.isdir(self.skill), "技能副本应已归档")
        sk = self._listdir(os.path.dirname(self.skill))
        self.assertTrue(any(n.startswith(SKILL_NAME + ".disabled-") for n in sk), sk)
        with io.open(ops, encoding="utf-8") as f:
            log = f.read()
        self.assertIn("machine_uninstall", log)
        self.assertIn("archived", log)

    def test_execute_forwards_to_cli_with_ledger(self):
        ops = os.path.join(self.base, "ops2.jsonl")
        r = self._run("--yes", "--skip", "pip,claude,openclaw", "--harness", ROOT,
                      env_extra={"AGENTGUARD_OPS_LOG": ops})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("转发 cli.py", r.stdout)
        self.assertFalse(os.path.isdir(self.skill), "技能副本应已被 cli 归档")
        sk = self._listdir(os.path.dirname(self.skill))
        self.assertTrue(any(n.startswith(SKILL_NAME + ".disabled-") for n in sk), sk)
        with io.open(ops, encoding="utf-8") as f:
            self.assertIn("skill_uninstall", f.read())

    def test_refuses_foreign_extension_dir(self):
        shutil.rmtree(os.path.join(self.ext, ".claude-plugin"))
        r = self._run("--yes", "--skip", "pip,claude,skills", "--openclaw-state", self.state,
                      "--harness", os.path.join(self.base, "noharness"))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("拒绝", r.stdout)
        self.assertTrue(os.path.isdir(self.ext), "特征不符时不得动目录")

    def test_unhook_repo_clears_hookspath(self):
        if not shutil.which("git"):
            self.skipTest("git 不可用")
        repo = os.path.join(self.base, "repo-x")
        os.makedirs(repo)
        subprocess.run(["git", "init", "-q"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "--local", "core.hooksPath", "some/hooks"],
                       cwd=repo, capture_output=True)
        ops = os.path.join(self.base, "ops3.jsonl")
        r = self._run("--yes", "--skip", "pip,claude,skills,openclaw", "--harness", ROOT,
                      "--unhook", repo, env_extra={"AGENTGUARD_OPS_LOG": ops})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        p = subprocess.run(["git", "config", "--local", "--get", "core.hooksPath"], cwd=repo,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertNotEqual(p.returncode, 0, "core.hooksPath 应已撤回")
        with io.open(ops, encoding="utf-8") as f:
            self.assertIn("hooks_uninstall", f.read())

    def test_nothing_to_do_is_clean(self):
        home2 = os.path.join(self.base, "home2")
        os.makedirs(home2)
        r = self._run("--yes", "--skip", "claude,pip",
                      "--harness", os.path.join(self.base, "noharness"),
                      home=home2,
                      env_extra={"AGENTGUARD_OPS_LOG": os.path.join(self.base, "ops4.jsonl")})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("无需处理", r.stdout)


class ShimStepTest(unittest.TestCase):
    """[6/6] 编辑期 shim（剖析报告 G5）。

    G5：shim 与指针此前在安装说明、本脚本里**都没有名字**，"整机退出"之后
    `~/.guardrail-engine/`（旧 `~/.agentguard/`）下会原样留着它们、指针还指着别人的引擎仓。

    全部在临时 HOME 里跑（HOME/USERPROFILE 重定向）——本机真实的 ~/.guardrail-engine/
    放着别的东西（GLOBAL_AGENTS.md 等），一个字节都不许碰。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = self.tmp.name
        self.home = os.path.join(self.base, "home")
        self.agents = os.path.join(self.home, ".guardrail-engine")
        os.makedirs(os.path.join(self.agents, "hooks"))
        self.shim = os.path.join(self.agents, "hooks", "agentguard-gate.py")
        self.ptr = os.path.join(self.agents, "harness.path")
        for p, text in ((self.shim, 'GEN_PATH = r"%s"\n' % ROOT.replace("\\", "/")),
                        (self.ptr, ROOT.replace("\\", "/") + "\n")):
            with io.open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        self.keep = os.path.join(self.agents, "GLOBAL_AGENTS.md")
        with io.open(self.keep, "w", encoding="utf-8", newline="\n") as f:
            f.write(u"# 别人家的文件\n")

    def _run(self, *args):
        env = dict(os.environ)
        env["HOME"] = self.home
        env["USERPROFILE"] = self.home
        env.pop("GUARDRAIL_ENGINE_DIR", None)
        env.pop("AGENTGUARD_DIR", None)
        env.pop("HARNESS_DIR", None)
        env["AGENTGUARD_OPS_LOG"] = os.path.join(self.base, "ops.jsonl")
        return subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", env=env)

    def _archives(self, d):
        return sorted(n for n in os.listdir(d) if ".disabled-" in n)

    def _only_shim(self, *extra):
        """只跑 [6/6]：其余五项一律 --skip（免得碰插件 / pip / 真实技能目录）。"""
        return self._run("--skip", "pip,claude,hooks,skills,openclaw", *extra)

    def test_preview_touches_neither_file(self):
        r = self._only_shim("--harness", ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(u"将归档", r.stdout)
        self.assertTrue(os.path.isfile(self.shim), "预演不得动 shim")
        self.assertTrue(os.path.isfile(self.ptr), "预演不得动指针")
        self.assertEqual(self._archives(os.path.join(self.agents, "hooks")), [])

    def test_forwards_to_install_shim_and_archives_both(self):
        r = self._only_shim("--yes", "--harness", ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("转发 install_shim.py --uninstall", r.stdout)
        self.assertFalse(os.path.isfile(self.shim), "shim 应已归档")
        self.assertFalse(os.path.isfile(self.ptr), "指针应已归档")
        self.assertTrue(self._archives(os.path.join(self.agents, "hooks")), "shim 归档没留下？")
        self.assertTrue(self._archives(self.agents), "指针归档没留下？")
        self.assertTrue(os.path.isfile(self.keep), "同目录里别人的文件一个字节都不许动")

    def test_falls_back_to_the_same_naming_without_the_engine_repo(self):
        """包还在、引擎仓联系不上——本脚本随包分发正是为这种场景：兜底也要同口径归档。"""
        r = self._only_shim("--yes", "--harness", os.path.join(self.base, "noharness"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(u"兜底归档", r.stdout)
        self.assertFalse(os.path.isfile(self.shim))
        arch = self._archives(os.path.join(self.agents, "hooks"))
        self.assertEqual(len(arch), 1, arch)
        self.assertTrue(arch[0].startswith("agentguard-gate.py.disabled-"), arch)

    def test_nothing_installed_needs_no_action(self):
        os.remove(self.shim)
        os.remove(self.ptr)
        r = self._only_shim("--yes", "--harness", ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(u"无需处理", r.stdout)


class SurfaceConsistencyTest(unittest.TestCase):
    """G5 的"面"要对齐：自述项数 == 实际步数，且安装面/卸载面文档里都有它。"""

    def test_docstring_item_count_matches_the_step_list(self):
        u"""「处理五项」这句自述必须等于 `steps` 的实际条数。

        报告 G5 正是从这句话看出来的：文件头自述"对照安装面处理五项"，而安装面上
        多出来的一路（编辑期 shim）谁都没提 —— 自述与实况脱节，靠人眼看不出来。
        """
        import ast
        import re
        src = _read(SCRIPT)
        m = re.search(u"处理([一二三四五六七八九十]+)项", src)
        self.assertIsNotNone(m, u"文件头里找不到「处理 N 项」这句自述（改了措辞请同步本锁）")
        cn = {u"一": 1, u"二": 2, u"三": 3, u"四": 4, u"五": 5,
              u"六": 6, u"七": 7, u"八": 8, u"九": 9, u"十": 10}
        claimed = cn[m.group(1)]
        steps = None
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "steps" for t in node.targets):
                steps = node.value
        self.assertIsNotNone(steps, "uninstall.py 里找不到 steps 元组")
        n = len(steps.elts)
        self.assertEqual(claimed, n,
                         u"文件头自述「处理 %d 项」，steps 实际有 %d 条 —— 自述与实况脱节"
                         % (claimed, n))
        titles = [e.elts[1].value for e in steps.elts if isinstance(e, ast.Tuple)]
        self.assertEqual(len(titles), n, "steps 每项都该是 (key, 标题, 函数) 三元组")
        for i, t in enumerate(titles, 1):
            self.assertIn("[%d/%d]" % (i, n), t,
                          "步骤编号与总数对不上：%s（共 %d 步）" % (t, n))

    def test_editing_shim_is_on_both_documented_surfaces(self):
        """安装面（「二」）与卸载面（「四」）都要有编辑期 shim —— 这正是 G5 缺的那一格。"""
        doc = _read(os.path.join(ROOT, "docs", "packaging", "INSTALL.md"))
        secs = {}
        cur = ""
        for line in doc.splitlines():
            if line.startswith("## "):
                cur = line[3:].strip()
                secs[cur] = []
            elif cur:
                secs[cur].append(line)
        install = "\n".join("\n".join(v) for k, v in secs.items() if k.startswith(u"二"))
        uninstall = "\n".join("\n".join(v) for k, v in secs.items() if k.startswith(u"四"))
        self.assertTrue(install, "安装说明里找不到「二、」这一节（改了标题请同步本锁）")
        self.assertTrue(uninstall, "安装说明里找不到「四、」这一节（改了标题请同步本锁）")
        for sec_name, body in ((u"二（安装面）", install), (u"四（卸载面）", uninstall)):
            self.assertIn("install_shim.py", body, u"安装说明「%s」没提编辑期 shim" % sec_name)
            self.assertIn(".guardrail-engine", body, u"安装说明「%s」没写它落在哪" % sec_name)
        self.assertIn("--uninstall", uninstall, u"卸载面没写撤回入口 --uninstall")
        self.assertIn(u"残骸", uninstall, u"卸载面没写归档的代价（G8 的另一半）")
        # 「对照装过什么逐项撤回」是那张表在管：正文里提一句"验证时可以看看 shim"**不算**
        # 撤回面（本锁第一版就栽在这：把行删掉仍绿，因为验证行里还有 install_shim.py）
        rows = [ln for ln in uninstall.splitlines() if ln.startswith("|")]
        self.assertTrue(any("install_shim.py" in r for r in rows),
                        u"卸载面的**表格**里没有编辑期 shim 这一行 —— 同事按表退会漏掉它")

    def test_runbook_also_names_the_shim_uninstall(self):
        rb = _read(os.path.join(ROOT, "docs", "packaging", "RUNBOOK.md"))
        self.assertIn("install_shim.py --uninstall", rb,
                      u"RUNBOOK「4. 卸载与回滚」漏了编辑期 shim（同事按 RUNBOOK 退会退不干净）")


class ArchiveNameDoesNotDrift(unittest.TestCase):
    """三份 archive_name 必须同口径（主实现 + 两份随包分发的镜像）。

    `cli.py` 装成 pip 包、`install_shim.py` / `uninstall.py` 随安装包分发，谁也不能
    import 谁 —— 只能镜像。镜像就会漂移，所以这里**拿同一个输入喂三份实现**对拍
    （与 test_review_v13_batch6 的存档对拍同一套打法）。
    """

    def _mods(self):
        import importlib.util
        out = {}
        sys.path.insert(0, ROOT)
        try:
            from guardrail_engine import cli as cli_mod
        except Exception:                                    # 无包上下文时裸导入
            import cli as cli_mod
        out["cli.py"] = cli_mod
        for label, rel in (("install_shim.py", os.path.join("sandbox", "install_shim.py")),
                           ("uninstall.py", os.path.join("sandbox", "uninstall.py"))):
            spec = importlib.util.spec_from_file_location("_mirror_" + label.replace(".", "_"),
                                                          os.path.join(ROOT, rel))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            out[label] = mod
        return out

    def test_same_archive_name_for_the_same_input(self):
        class _Frozen(object):
            def strftime(self, _f):
                return "20260101-000000"

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        target = os.path.join(tmp.name, "skills", "guardrail-engine")
        os.makedirs(target)
        want = target + ".disabled-20260101-000000"
        for label, mod in self._mods().items():
            real = mod.time
            mod.time = _Frozen()
            try:
                self.assertEqual(mod.archive_name(target), want,
                                 "%s 的归档名与主实现不一致（镜像漂移）" % label)
            finally:
                mod.time = real
        # 撞车时也要退到同一档序号，否则"同口径"只对了没撞车的那一半
        os.makedirs(want)
        for label, mod in self._mods().items():
            real = mod.time
            mod.time = _Frozen()
            try:
                self.assertEqual(mod.archive_name(target), want + "-2",
                                 "%s 撞车时的序号口径与主实现不一致" % label)
            finally:
                mod.time = real

    def test_same_suffix_constant(self):
        for label, rel in (("cli.py", os.path.join("guardrail_engine", "cli.py")),
                           ("install_shim.py", os.path.join("sandbox", "install_shim.py")),
                           ("uninstall.py", os.path.join("sandbox", "uninstall.py"))):
            self.assertIn('ARCHIVE_SUFFIX = ".disabled-"', _read(os.path.join(ROOT, rel)),
                          "%s 缺（或改了）归档后缀常量" % label)


if __name__ == "__main__":
    unittest.main()
