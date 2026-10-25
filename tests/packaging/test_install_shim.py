# -*- coding: utf-8 -*-
"""install_shim.py --uninstall 回归锁 —— 剖析报告 G5 的撤回面。

G5 说的是：编辑期 shim 由 install_shim.py 产出**两支文件**（`~/.guardrail-engine/hooks/
agentguard-gate.py` 与指针 `~/.guardrail-engine/harness.path`），而此前**没有任何撤回入口**
—— 安装说明里没有它们的名字、`uninstall.py` 的五项清理里也没有。于是"整机退出"之后
`~/.agentguard/` 下会原样留着它们、指针还指着别人的引擎仓。

⚠ **装三样、撤两样**（2026-09-19 起）：安装时本脚本还顺带渲染
`~/.guardrail-engine/GLOBAL_AGENTS.md`（《安装说明》「二、Agent 接入」里同事必然跑到的一步，
此前那支文件根本没有生成者），但**撤回时刻意不退它** —— 它的哨兵区之外属于用户，
归档走就等于把同事自己加的规矩一起拿走。所以下面第 ④ 条锁的是"**留着且一字不动**"。

这里锁四件事：
  ① 装 → 体检 → 卸 → 体检 是一条**闭环**（卸完 `--check` 必须变红，否则"退出"没退干净）；
  ② 撤回**只改名、不删文件**（可逆；且目录本身、目录里别人的东西一律不碰）；
  ③ 特征不符（不是本脚本生成的 shim）→ **拒绝**并两支都别动（防误删；宁可让人工来）；
  ④ 全局守则**在撤回清单之外**，且撤回前后逐字节不变。

安全约定：全部在**临时 HOME** 里跑（重定向 HOME / USERPROFILE），绝不碰本机真实的
`~/.agentguard/`（那里还有 GLOBAL_AGENTS.md 等别人的东西 —— 本文件的第 ④ 条锁
正是从这件事长出来的）。
"""
import ast
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

# UTF-8 输出兜底：本模块带非 ASCII 文案，GBK 控制台上直接跑会崩（N9 回归锁
# tests/test_entry_level.py::TestEntryUtf8Fallback 会把缺它的入口报出来）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "sandbox", "install_shim.py")


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _read(path):
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


class InstallShimTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home)
        self.agents = os.path.join(self.home, ".guardrail-engine")
        self.shim = os.path.join(self.agents, "hooks", "agentguard-gate.py")
        self.ptr = os.path.join(self.agents, "harness.path")
        # 第三样产物（装的时候写、撤的时候**不退**，见模块 docstring ④）
        self.guard = os.path.join(self.agents, "GLOBAL_AGENTS.md")

    def _run(self, *args):
        env = dict(os.environ)
        env["HOME"] = self.home            # POSIX expanduser
        env["USERPROFILE"] = self.home     # Windows expanduser
        env["HARNESS_DIR"] = ROOT          # 免得体检读到本机真实的指针
        env["GUARDRAIL_ENGINE_DIR"] = ROOT   # 主名优先；两跳同钉免得本机环境盖过它
        env["AGENTGUARD_DIR"] = ROOT         # 迁移期旧名（同钉）
        r = subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=env,
                           cwd=ROOT)
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def _run_env(self, extra, *args):
        """`_run` 的变体：允许覆盖/清空环境变量（L13 的锁要能把 HARNESS_DIR 摘掉 ——
        它在场时是解析链的第一跳，会把"指针救回来了"这件事盖住）。"""
        env = dict(os.environ)
        env["HOME"] = self.home
        env["USERPROFILE"] = self.home
        env["HARNESS_DIR"] = ROOT
        env["GUARDRAIL_ENGINE_DIR"] = ROOT
        env["AGENTGUARD_DIR"] = ROOT
        env.update(extra)
        r = subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=env, cwd=ROOT)
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def _siblings(self):
        d = os.path.dirname(self.shim)
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    # ---- ① 装 → 体检 → 卸 → 体检 闭环 ----

    def test_install_uninstall_check_is_a_round_trip(self):
        rc, out = self._run()
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.isfile(self.shim), "装完应有 shim")
        self.assertTrue(os.path.isfile(self.ptr), "装完应有指针")
        # 第三样：全局守则（2026-09-19）。《安装说明》「二、Agent 接入」承诺这一步产出三样，
        # 少一样就是"看起来装好了其实没有" —— 三份端级模板都指着它。
        self.assertTrue(os.path.isfile(self.guard), "装完应有全局守则（端级模板都指着它）")
        self.assertIn("<!-- HARNESS:BEGIN", _read(self.guard), "全局守则应是哨兵分区形态")
        rc, out = self._run("--check")
        self.assertEqual(rc, 0, "装完体检应当通过：%s" % out)

        before = _read(self.guard)
        rc, out = self._run("--uninstall")
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.isfile(self.shim), "卸完 shim 不该还在原处")
        self.assertFalse(os.path.isfile(self.ptr), "卸完指针不该还在原处")
        # 撤回清单**不含**全局守则：撤完它还在，且一字未动（区外是用户的）
        self.assertTrue(os.path.isfile(self.guard), "全局守则不该被撤回（区外属于用户）")
        self.assertEqual(_read(self.guard), before, "撤回不该改动全局守则一个字节")
        arch = [n for n in self._siblings() if n.startswith("agentguard-gate.py.disabled-")]
        self.assertEqual(len(arch), 1, "应当恰好留下一份归档（可逆）：%s" % self._siblings())
        self.assertIn("GEN_PATH", _read(os.path.join(os.path.dirname(self.shim), arch[0])),
                      "归档里应当是原文件的内容，不是空壳")

        rc, out = self._run("--check")
        self.assertEqual(rc, 1, "卸完体检必须变红（否则'退出'是假的）：%s" % out)
        self.assertIn("未安装 shim", out)

    def test_uninstall_says_the_client_hook_config_still_points_here(self):
        """只归档文件不改宿主配置 = 编辑期门禁静默失效 —— 脚本必须当场说出来。"""
        self._run()
        rc, out = self._run("--uninstall")
        self.assertEqual(rc, 0, out)
        # 三句都要在：① 指出来 ② 去宿主配置里删 ③ 不删会静默失效（少一句就少一半信息）
        self.assertIn("仍写着这个路径", out)
        self.assertIn("删掉该 hook", out)
        self.assertIn("静默失效", out)

    def test_uninstall_keeps_neighbours_in_the_agents_dir(self):
        """`~/.agentguard/` 下有别人的东西（本机就有 GLOBAL_AGENTS.md）：一律不碰。"""
        self._run()
        keep = os.path.join(self.agents, "GLOBAL_AGENTS.md")
        _write(keep, "# 别人的文件\n")
        other_hook = os.path.join(self.agents, "hooks", "someone-else.py")
        _write(other_hook, "# 别人的钩子\n")
        rc, out = self._run("--uninstall")
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.isfile(keep), "不得动同目录里别人的文件")
        self.assertTrue(os.path.isfile(other_hook), "不得动 hooks/ 里别人的钩子")
        self.assertTrue(os.path.isdir(os.path.join(self.agents, "hooks")),
                        "不得删目录本身（哪怕空了）")

    # ---- ④ 全局守则：装的时候写、撤的时候不退 ----

    def test_uninstall_leaves_the_rendered_global_agents_alone(self):
        """**这是第 ④ 条锁真正想防的那件事**：撤完，同事自己加的规矩必须还在。

        为什么不能只测"一个叫 GLOBAL_AGENTS.md 的文件还在"（上面那条邻居锁已经测了）：
        那测的是"没误删别人的文件"。这条测的是**本脚本自己装的东西也不许退** —— 判据不是
        "它算不算本脚本的产物"，而是**它的哨兵区之外属于用户**：那里写着同事自己加的规矩，
        归档走就等于把那些一起拿走，而"撤回"本该只退掉 harness 装的东西。

        ⚠ 别把这条改成"撤完应当没有 GLOBAL_AGENTS.md" —— 那正是设计上刻意不要的行为。
        """
        self._run()
        # 模拟同事在哨兵区之外写了自己的规矩（本机那份就是这么用的）
        mine = "\n## 我自己加的规矩\n- 提交信息用中文\n"
        _write(self.guard, _read(self.guard) + mine)
        rc, out = self._run("--uninstall")
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.isfile(self.guard), "全局守则不在撤回清单里")
        self.assertIn("提交信息用中文", _read(self.guard), "区外是用户的 —— 一个字节都不许动")
        self.assertIn("<!-- HARNESS:BEGIN", _read(self.guard), "哨兵区还在，下次装回来继续维护")
        self.assertEqual([n for n in os.listdir(self.agents)
                          if "GLOBAL_AGENTS.md.disabled-" in n], [],
                         "不得给它留归档：留了就等于退掉了")

    def test_check_reports_a_moved_engine_repo_but_still_passes(self):
        """挪过仓之后，`--check` 要为守则**出声**，但仍不许判红。

        这是「补上那个洞」的落点（2026-09-19）：守则里三处绝对路径是渲染那刻烘进去的，
        此后没人再看 —— 「clone 到 A → 装 shim → 把仓挪到 B」这条路上 `--check` 一路 ✅，
        而守则正让 AI 去 A 敲命令。出声与判红是两件事：两个克隆并存时指着另一个仓未必是错，
        是谁的错不该由体检裁决，所以**只提醒**（rc 仍为 0），改不改由人定。
        """
        self._run()
        baked = ROOT.replace("\\", "/")
        self.assertIn(baked, _read(self.guard), "装完守则里应当烘着本仓路径")
        rc, out = self._run("--check")
        self.assertEqual(rc, 0, "本仓路径一致时体检要过：%s" % out)
        self.assertNotIn("路径与本仓不一致", out, "一致时不许报（否则体检开始误报就没人看了）")

        # 模拟"仓后来挪走了"：守则还是老路径，而脚本在别的仓里跑
        _write(self.guard, _read(self.guard).replace(baked, "D:/MOVED/elsewhere"))
        rc, out = self._run("--check")
        self.assertEqual(rc, 0, "**只提醒不判红**：挪仓不是 shim 的错，不该拦：%s" % out)
        self.assertIn("路径与本仓不一致", out, "挪了仓必须出声")
        self.assertIn("D:/MOVED/elsewhere", out, "要说清烘进去的是哪个（陈旧的）路径")
        self.assertIn("install_shim.py", out, "要给出可执行的修法")

    # ---- ⑤ 引擎仓被剪裁：装要报错，体检与撤回仍要能用 ----

    def test_clipped_engine_repo_fails_loudly_but_stays_diagnosable(self):
        """少了 `sandbox/global_agents.py` 时：install 必须**响**，而 --check/--uninstall 仍要能用。

        为什么这条值得一个真跑的用例（而不是只读代码）：`import global_agents` 是
        `try/except ImportError` 兜住的，兜住之后**三条路径各走各的**——最容易写错的形式是
        "三条都静默降级"，那样同事拿到一个缺文件的引擎仓，装 shim 会**打绿灯但没写守则**，
        而这正是本仓反复在治的失效模式。这里的判据就是：**install 返回非 0 且说清缺什么**。
        """
        clipped = os.path.join(self.tmp.name, "clipped")
        os.makedirs(os.path.join(clipped, "sandbox"))
        os.makedirs(os.path.join(clipped, "integrations", "hooks"))
        shutil.copy(SCRIPT, os.path.join(clipped, "sandbox"))
        shutil.copy(os.path.join(ROOT, "integrations", "hooks",
                                 "agentguard-gate-shim.py"),
                    os.path.join(clipped, "integrations", "hooks"))
        script = os.path.join(clipped, "sandbox", "install_shim.py")
        env = dict(os.environ)
        env["HOME"] = self.home
        env["USERPROFILE"] = self.home
        env["HARNESS_DIR"] = clipped

        def run(*args):
            r = subprocess.run([sys.executable, script] + list(args), capture_output=True,
                               text=True, encoding="utf-8", errors="replace", env=env,
                               cwd=clipped)
            return r.returncode, (r.stdout or "") + (r.stderr or "")

        rc, out = run()
        self.assertEqual(rc, 1, "缺模块时 install 不许静默成功：%s" % out)
        self.assertIn("global_agents.py", out)
        self.assertIn("git pull", out, "要说清怎么修，不能只说'缺文件'")
        self.assertFalse(os.path.isfile(os.path.join(self.agents, "GLOBAL_AGENTS.md")),
                         "缺模块时不该凭空造出一个守则文件")
        # 而体检与撤回是「别人机器上的既有安装」的出路，不能因为少一个模块就退不掉
        rc, out = run("--check")
        self.assertIn("跳过", out, "体检要说明为什么跳过，而不是崩掉或假装已生成")
        rc, out = run("--uninstall")
        self.assertEqual(rc, 0, "剪裁仓仍要能撤回：%s" % out)
        self.assertFalse(os.path.isfile(self.shim), "撤回应当生效")

    # ---- ② 防误删：特征不符就拒绝 ----

    def test_foreign_shim_is_refused_and_leaves_both_files_alone(self):
        _write(self.shim, "# 别人手写的钩子，不是本脚本生成的\nprint('hi')\n")
        _write(self.ptr, "/some/other/repo\n")
        rc, out = self._run("--uninstall")
        self.assertEqual(rc, 1, out)
        self.assertIn("拒绝", out)
        self.assertTrue(os.path.isfile(self.shim), "特征不符时不得动它")
        self.assertTrue(os.path.isfile(self.ptr), "拒绝时指针也要一起留着（要么一起退，要么一起留）")
        self.assertEqual([n for n in self._siblings() if ".disabled-" in n], [],
                         "拒绝时不该产生任何归档：%s" % self._siblings())

    def test_nothing_installed_is_clean(self):
        rc, out = self._run("--uninstall")
        self.assertEqual(rc, 0, out)
        self.assertIn("无需撤回", out)

    # ---- ③ 撤回面永不删文件 ----

    def test_uninstall_code_never_deletes_anything(self):
        """结构性锁：整个脚本里不许出现删除类调用（归档 = 改名，退路永远留着）。

        为什么用 AST 不用 grep：文件头与注释里写着"不删任何文件"这句话本身就会命中 grep，
        把「写着不删」判成「删了」（本仓在 upgrade.py 上栽过同一个跟头）。
        """
        tree = ast.parse(_read(SCRIPT))
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if name in ("rmtree", "remove", "unlink", "rmdir", "removedirs",
                            "rmtree_if_exists"):
                    bad.append("%s (line %d)" % (name, node.lineno))
        self.assertEqual(bad, [], "撤回面出现了删除类调用：%s" % bad)


class TestCheckMatchesRuntimeChain(unittest.TestCase):
    """L13（评审 2026-09-23）：`--check` 的判据必须与 shim **运行时的解析链**同源。

    改前的判据是「GEN_PATH 可达 **且** 指针可达」（两跳都得同时好），而运行时的语义是
    「HARNESS_DIR → 指针 → GEN_PATH 逐个试、**取第一个 `REL` 存在的**」
    （`integrations/hooks/agentguard-gate-shim.py` 的 `resolve()`）。
    于是「指针有效、GEN_PATH 因挪仓/换克隆而失效」这种**运行时完全正常**的状态被判 ✗ ——
    偏严的假红；假红与假绿同害：它会把人训练成忽略这条自检，那时真红也没人信。

    ⚠ **这一组锁的判据不是"输出里出现了某句话"**，而是**产品自己的行为**：每一条都同时看
      · `install_shim.py --check` 的判定（rc），与
      · **真去跑那个 shim** 得到的结果（能不能落到实现上）
    两者必须一致。只断言 rc 的话，"整链判据被改回单跳"与"判据全绿"都照样能过。

    ⚠ 夹具为什么要造一个 **stub 仓**：真 `TARGET` 一命中就会 `runpy` 跑起整套门禁钩子。
    stub 只打印一个标记，于是"运行时到底达没达"变成一行可比对的证据。
    """

    # 与 shim 模板里的 REL 同名同义（模板逐字：`os.path.join("integrations", "hooks",
    # "agentguard-gate.py")`）。写死在这里是**有意的**：判据要与产品**比**，
    # 不能 import 产品的常量来比自己（那样同源就退化成同义反复）。
    REL_LITERAL = os.path.join("integrations", "hooks", "agentguard-gate.py")
    STUB = "print('STUB_RAN')\n"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home)
        self.agents = os.path.join(self.home, ".guardrail-engine")
        self.shim = os.path.join(self.agents, "hooks", "agentguard-gate.py")
        self.ptr = os.path.join(self.agents, "harness.path")
        self.stub_root = os.path.join(self.tmp.name, "stubrepo")     # 能被指针指到的"活"目标
        _write(os.path.join(self.stub_root, self.REL_LITERAL), self.STUB)
        self.gone = os.path.join(self.tmp.name, "gone")              # 不存在的目录（陈旧跳）
        self.gone2 = os.path.join(self.tmp.name, "gone2")
        self.env = {"HOME": self.home, "USERPROFILE": self.home,     # expanduser 两平台
                    "HARNESS_DIR": "", "AGENTGUARD_DIR": ""}  # 第一跳摘掉（两个名字都摘），别盖住后两跳

    # ---- 工具 ----

    def _run(self, *args, env=None):
        e = dict(os.environ)
        e.update(self.env)
        if env:
            e.update(env)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "sandbox", "install_shim.py")]
                           + list(args), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=e, cwd=ROOT)
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def _run_shim(self, env=None):
        """真去执行那份生成的 shim —— 返回它有没有跑到实现上。

        TARGET 解析不出来时 shim 走"找不到门禁实现"那条出口：**exit 0 + stderr 提示**
        （"宁可不过门禁也别卡住同事"），所以**不能看退出码**，判据是输出里有没有 STUB_RAN。
        """
        e = dict(os.environ)
        e.update(self.env)
        if env:
            e.update(env)
        r = subprocess.run([sys.executable, self.shim], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=e, cwd=self.tmp.name)
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def _install_with(self, pointer, gen_path):
        """装好之后，把指针与 shim 里的 GEN_PATH **改成指定的值**（模拟挪仓/换克隆的后果）。"""
        rc, out = self._run()
        self.assertEqual(rc, 0, "装 shim 失败：%s" % out)
        _write(self.ptr, pointer + "\n")
        src = _read(self.shim)
        self.assertIn("GEN_PATH = r\"", src, "shim 的 GEN_PATH 形态变了，本夹具需要跟着改：%s" % src[:200])
        _write(self.shim, src.replace("GEN_PATH = r\"%s\"" % ROOT.replace("\\", "/"),
                                      "GEN_PATH = r\"%s\"" % gen_path))

    # ---- ① 判据同源：常数与模板逐字对得上 ----

    def test_rel_matches_the_shim_template(self):
        """`install_shim.REL` 必须与 shim 模板里那句 `REL = os.path.join(...)` 同值。

        为什么单锁一条常量：体检用 `REL` 判"这一跳可不可达"，而运行时用模板里的 `REL` 找目标。
        两者一旦分叉，体检就会开始报**与运行结果相反**的结论 —— 而那种错最像"体检在工作"。
        """
        tmpl = _read(os.path.join(ROOT, "integrations", "hooks", "agentguard-gate-shim.py"))
        m = re.search(r'^REL\s*=\s*os\.path\.join\(([^)]*)\)', tmpl, re.M)
        self.assertIsNotNone(m, "模板里找不到 REL（改过写法就要同步这条锁）")
        parts = re.findall(r'"([^"]+)"', m.group(1))
        self.assertEqual(os.path.join(*parts), self.REL_LITERAL,
                         "模板 REL 与锁里写死的形态不一致：%r" % (parts,))
        src = _read(os.path.join(ROOT, "sandbox", "install_shim.py"))
        m2 = re.search(r'^REL\s*=\s*os\.path\.join\(([^)]*)\)', src, re.M)
        self.assertIsNotNone(m2, "install_shim.py 里的 REL 常量丢了（L13 的判据靠它）")
        self.assertEqual(os.path.join(*re.findall(r'"([^"]+)"', m2.group(1))), self.REL_LITERAL,
                         "install_shim.py 的 REL 与模板分叉了 —— 体检将与运行时给出相反结论")

    # ---- ② 缺陷方向：单跳陈旧 ≠ 体检要红（运行时明明没事） ----

    def test_stale_gen_path_with_a_live_pointer_is_still_green(self):
        """指针有效、GEN_PATH 陈旧（挪仓/换克隆后最常见的形态）→ `--check` 必须绿。

        这是 L13 的本体。判据两半：**rc=0**，且**真跑 shim 时它确实跑到了 stub 上** ——
        少了后半句，这条锁就只是"我改了断言让它绿"，证不了"运行时真的没事"。
        """
        self._install_with(pointer=self.stub_root, gen_path=self.gone)
        rc, out = self._run("--check")
        shim_rc, shim_out = self._run_shim()
        self.assertIn("STUB_RAN", shim_out,
                      "夹具前提没成立：指针没把 shim 救回来，那这条锁证不了任何事：%s" % shim_out)
        self.assertEqual(rc, 0, "指针可达、GEN_PATH 陈旧时体检判红了（偏严的假红）：%s" % out)
        self.assertIn("GEN_PATH", out, "陈旧的那一跳要照报出来，别默默吞掉")
        self.assertIn("指针", out, "要说清运行时**实际用的是**哪一跳")

    def test_stale_pointer_with_a_live_gen_path_is_still_green(self):
        """反方向：GEN_PATH 有效、指针陈旧 → 同样必须绿（运行时会落到第三跳上）。"""
        self._install_with(pointer=self.gone2, gen_path=ROOT.replace("\\", "/"))
        rc, out = self._run("--check")
        self.assertEqual(rc, 0, "GEN_PATH 可达、指针陈旧时体检判红了：%s" % out)
        self.assertIn("GEN_PATH", out)

    # ---- ③ 过度收紧方向：整链失效必须红（否则判据成了万能绿） ----

    def test_a_dead_chain_is_red_and_the_shim_really_gives_up(self):
        """三跳全失效 → `--check` 必须红，且**同一个夹具下真跑 shim 确实放弃了**。

        这条是上面两条的对照（防"把判据改成恒绿"）：判据与运行时行为必须**同向**。
        顺带钉住那条出口的语义：shim 放弃时是 **exit 0**（不阻塞同事），
        所以"看退出码"在这里什么也证不了 —— 判据只能是输出。
        """
        self._install_with(pointer=self.gone2, gen_path=self.gone)
        rc, out = self._run("--check")
        shim_rc, shim_out = self._run_shim()
        self.assertNotIn("STUB_RAN", shim_out, "夹具前提没成立：还有一跳是通的：%s" % shim_out)
        self.assertIn("找不到门禁实现", shim_out,
                      "shim 在整链失效时没说自己在放弃 —— 那么这条锁就不知道自己在比什么：%s" % shim_out)
        self.assertEqual(rc, 1, "整链失效时体检竟然是绿的（判据成了万能绿）：%s" % out)
        self.assertIn("整条失效", out, "要一句话说清是**整条链**失效，而不是某一跳没配好")


if __name__ == "__main__":
    unittest.main()
