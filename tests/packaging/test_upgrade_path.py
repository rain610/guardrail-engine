# -*- coding: utf-8 -*-
"""升级路径锁（剖析报告「升级路径的测试覆盖：0」）—— **装 A 版 → 再装 B 版**，四条通道逐条走。

报告的原话：pip、插件、OpenClaw 扩展、技能副本**四条通道一条都没有**覆盖「装 A 版 → 再装 B 版」，
覆盖面严格是「全新安装 + 整机卸载」—— 只测了两端，中间那段没人走。本文件走中间那段：
同一台**假机器**上先放着 A 版的内容，再拿 B 版包跑一次升级，然后断言**留下的确实是 B**。

四条通道的「B 真的落地了」判据不同，因为它们本来的刷新机制就不同 —— 写下来免得下一个人
以为四条锁写得不齐：
  · OpenClaw 扩展：upgrade.py **自己**走文件树覆盖 → 判据是产物文件；
  · 技能副本    ：转发**真** cli.py 的 `skill install` → 判据是产物文件（跑的是真实现，不是替身）；
  · pip        ：本机 pip 不能在测试里跑真的（会改装**测试环境自己**）→ 用替身解释器，两场都
                  **刻意复刻 pip 的真实行为**（2026-09-17 隔离 venv 实测）：同版本号时 rc=0 且
                  一个字节都不换（pip 原话「is already installed with the same version as the
                  provided wheel」），而 `--force-reinstall` 在同版本号下**确实会换内容**。
                  所以这一路锁两件事：脚本会不会把"没换内容"报成成功，以及它代跑
                  `--force-reinstall` 之后**产物是不是真的换了**（判据落在产物字节上）；
  · Claude 插件：真正的搬运由宿主 CLI 做，测试里没有真宿主（也不许碰真宿主）→ 判据只能是
                  **诊断**：内容没落地时必须说出来，且不许拿一个错的理由（版本号）去解释。

安全约定（比 test_upgrade.py 更严）：全程在临时 HOME / 临时包里跑；任何 `--yes` 都必须
显式 `--skip claude` 或注入假 `claude` —— 本机 PATH 上可能真有 claude，一次误调就会去改
**真机**的插件配置（`_run` 里有硬断言，别绕过）。
"""
import glob
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

# UTF-8 输出兜底：本模块带非 ASCII 文案（且替身脚本里含 sys.stdout.write 字面量），
# GBK 控制台上直接跑会崩。N9 回归锁 tests/test_entry_level.py::TestEntryUtf8Fallback
# 会把缺它的入口报出来。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 快跑（HARNESS_FAST=1，2026-09-17 批 4 接线）：本文件是**升级通道的端到端**文件
# （每条都真起临时目录 + 真跑 upgrade.py 的替身 pip / 真拷技能与扩展），接线前完全没接
# （全文 0 处 HARNESS_FAST），快跑档单跑 10.7~15.3s。
# 落点 = **方法级 4 条 ≥1s**（2.97 / 2.44 / 2.42 / 2.24s），**刻意留跑 2 条亚秒的**：
#   · test_openclaw_extension_gets_b_content_and_keeps_the_orphan（0.66s）＝ 变异 M26 唯一判据
#   · test_plugin_copy_stale_across_versions_is_not_excused_by_the_version（0.85s）＝ 变异 M30 唯一判据
# 这两条是"便宜、但某条变异的牙齿全长在它身上"的形状（同批 3 的 M11 处理）。为省 1.5s
# 把它们的判据在快跑档里清掉，是拿护盾换零头 —— 故用方法级而非类级。
# ★ 这两条**不按 1s 线判**：0.85s 离 1s 太近，而本机复量显示这条线在用例挤在 0.3~3s 的
#   文件上不重复（见 test_coverage.py 名单锁 docstring 的第三条量法坑）。判据是"它是不是
#   某条变异的唯一牙齿"，不是"这次量到几点几秒"。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPGRADE = os.path.join(ROOT, "sandbox", "upgrade.py")
CLI = os.path.join(ROOT, "agentguard", "cli.py")
FIXER = os.path.join(ROOT, "sandbox", "fix_paths.py")
SKILL_SRC = os.path.join(ROOT, "integrations", "guardrail-engine-skill")
PLUGIN = "agentguard@agentguard-tools"
PLACEHOLDER_MCP = json.dumps({
    "mcpServers": {"agentguard": {
        "command": "__PY__", "args": ["__MCP__"], "env": {"GUARDRAIL_ENGINE_DIR": "__HARNESS__"},
    }},
}, indent=2) + "\n"

# 替身解释器：只替 `-m pip`，其余（fix_paths.py 等）原样转发给真解释器。
# 用 __TOKEN__ + replace 组装，不用 %-格式化 —— 模板里有太多 % 与引号，格式化的坑比收益大。
FAKE_MAIN = '''# -*- coding: utf-8 -*-
"""替身解释器：`-m pip` 走替身，其他命令原样转发给真解释器。

为什么允许替身：测试里不能跑真 pip（会改装**跑测试的那个环境自己**）。替身只做两件事 ——
`show` 报出这台假机器上"装了什么"，`install` 按 MODE 决定"换不换内容"。两种 MODE 复刻的都是
pip 的**真实**行为（2026-09-17 隔离 venv 实测）：同版本号时 rc=0 且什么都不做；而
`--force-reinstall` 在同版本号下**确实会换内容** —— 所以 noop 只在"没有 --force-reinstall"时拒绝。
refuse 是第三种（连 --force-reinstall 也换不动：权限 / 索引 / 被别的安装遮住），
它是"代跑之后仍不一致"那条路的替身。
"""
import os
import subprocess
import sys
import zipfile

REAL = "__REAL__"
SITE = "__SITE__"
MODE = "__MODE__"      # install = 真铺开；noop = 同版本拒绝但尊重 --force-reinstall；refuse = 怎么都不换
VER = "__VER__"


def main():
    argv = sys.argv[1:]
    if argv[:2] != ["-m", "pip"]:
        return subprocess.call([REAL] + argv)
    rest = argv[2:]
    PKGS = ("guardrail_engine", "agentguard")
    if rest[:1] == ["show"]:
        if not any(os.path.isdir(os.path.join(SITE, p)) for p in PKGS):
            sys.stdout.write("WARNING: Package(s) not found: agentguard\\n")
            return 1
        sys.stdout.write("Name: guardrail-engine\\nVersion: %s\\nLocation: %s\\n" % (VER, SITE))
        return 0
    if rest[:1] == ["install"]:
        wheel = rest[-1]
        forced = "--force-reinstall" in rest
        if MODE == "refuse" or (MODE == "noop" and not forced):
            sys.stdout.write("Processing %s\\n" % wheel)
            sys.stdout.write(
                "agentguard is already installed with the same version as the provided "
                "wheel. Use --force-reinstall to force an installation of the wheel.\\n")
            return 0
        with zipfile.ZipFile(wheel) as z:
            for n in z.namelist():
                if n.split("/", 1)[0] in PKGS and n.endswith(".py"):
                    dst = os.path.join(SITE, n.replace("/", os.sep))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with open(dst, "wb") as f:
                        f.write(z.read(n))
        sys.stdout.write("Successfully installed agentguard-%s\\n" % VER)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _read(path):
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _fake_exe(root, name, rc=0):
    """造一个能被 run_cmd 跑起来的假命令；**路径必须带 .cmd 后缀**（否则 Windows 上给短名会
    找一个不存在的文件 → rc=127）。

    ⚠ 2026-09-23（评审 L14）订正旧注释：原来说的理由是"run_cmd 只对 .cmd/.bat 走 `cmd /c`"，
    那层 `cmd /c` 已按 L14 删掉（实测结论见 sandbox/upgrade.py::run_cmd 的 docstring）。
    后缀要求**照旧成立**，原因与 run_cmd 无关：`CreateProcessW` 无扩展名时只补 `.exe`、不补
    `.cmd`；显式写 `name.cmd` 时操作系统本人就认。与 tests/test_upgrade.py::_fake_exe 同口径。
    """
    if os.name == "nt":
        p = os.path.join(root, name + ".cmd")
        _write(p, "@echo off\r\nexit /b %d\r\n" % rc)
    else:
        p = os.path.join(root, name)
        _write(p, "#!/bin/sh\nexit %d\n" % rc)
        os.chmod(p, 0o755)
    return p


def _fake_python(root, site, mode, ver):
    """造一个"能当解释器用"的替身：`-m pip` 走替身逻辑，别的命令转发给真解释器。

    四个值都用 repr() 替换**连同引号**（`"__SITE__"` → `'C:\\...'`），不能只换内容：
    Windows 路径里的 `\\U` / `\\t` 在源码里是转义序列，裸着填进去就是一个 SyntaxError。
    """
    main = os.path.join(root, "fake_pip_main.py")
    body = FAKE_MAIN
    for token, val in (("REAL", sys.executable), ("SITE", site), ("MODE", mode), ("VER", ver)):
        body = body.replace('"__%s__"' % token, repr(val))
    _write(main, body)
    if os.name == "nt":
        p = os.path.join(root, "fakepy.cmd")
        _write(p, '@echo off\r\n"%s" "%s" %%*\r\nexit /b %%ERRORLEVEL%%\r\n' % (sys.executable, main))
    else:
        p = os.path.join(root, "fakepy")
        _write(p, '#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, main))
        os.chmod(p, 0o755)
    return p


def _wheel(path, marker):
    """造一个最小但真的 wheel（zip）：两个壳包的 `__init__.py` 里写着这一版的标记。

    两个包都要写：v0.2 起 wheel 同时带 `guardrail_engine/` 与兼容壳 `agentguard/`，
    升级的 pip 通道按"两个包逐字节比轮子"对账 —— 只写旧名会让对账把新包读成缺失。
    """
    with zipfile.ZipFile(path, "w") as z:
        for pkg in ("guardrail_engine", "agentguard"):
            z.writestr(pkg + "/__init__.py", "MARKER = %r\n" % marker)


def _engine_wheel(path, version, marker):
    """造一个**自带引擎**的 wheel：真壳文件 + 迷你引擎树（[5/7] 解包后由它跑 init）。

    迷你引擎只要求 `harness/run_gate.py --list` rc=0（init 自检的唯一硬条件）和一份 `VERSION`；
    `harness/engine_marker.py` 是这一版的正文指纹 —— 断言"换掉的确实是内容"靠它，不靠版本号。
    """
    with zipfile.ZipFile(path, "w") as z:
        for pkg in ("guardrail_engine", "agentguard"):
            for fn in ("__init__.py", "cli.py", "_shell.py", "mcp.py"):
                z.write(os.path.join(ROOT, pkg, fn), pkg + "/" + fn)
            z.writestr(pkg + "/_engine/VERSION", version + "\n")
            z.writestr(pkg + "/_engine/harness/run_gate.py",
                       "# 迷你引擎：init 自检只要求 --list rc=0\n"
                       "import sys\nprint('[ON ] probe: probe @ /x')\nsys.exit(0)\n")
            z.writestr(pkg + "/_engine/harness/engine_marker.py", "MARKER = %r\n" % marker)


FAKE_NOOP_INIT = '''# -*- coding: utf-8 -*-
"""替身解释器：init **假装成功但什么都不做**；其余命令原样转发给真解释器。

用来锁 [5/7] 的判据：init 的 rc 不是判据，产物逐字节对得上才算刷新成功。
"""
import subprocess
import sys

REAL = "__REAL__"


def main():
    argv = sys.argv[1:]
    if argv[:2] == ["-m", "pip"]:
        sys.stdout.write("WARNING: Package(s) not found: agentguard\\n")
        return 1
    if "init" in argv and any(a.endswith("cli.py") for a in argv):
        sys.stdout.write("pretend init ok\\n")
        return 0
    return subprocess.call([REAL] + argv)


if __name__ == "__main__":
    sys.exit(main())
'''


def _fake_noop_init_python(root):
    """假解释器：init 返回 0 但一个字节都不写（其余命令转发给真解释器）。"""
    main = os.path.join(root, "fake_noop_init_main.py")
    _write(main, FAKE_NOOP_INIT.replace('"__REAL__"', repr(sys.executable)))
    if os.name == "nt":
        p = os.path.join(root, "noopinit.cmd")
        _write(p, '@echo off\r\n"%s" "%s" %%*\r\nexit /b %%ERRORLEVEL%%\r\n'
               % (sys.executable, main))
    else:
        p = os.path.join(root, "noopinit")
        _write(p, '#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, main))
        os.chmod(p, 0o755)
    return p


class UpgradePathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)     # 显式清理：GC 期抛 ResourceWarning 会让严格门槛红
        self.base = self.tmp.name
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self.ops = os.path.join(self.base, "ops.jsonl")

    # ---- 夹具 ----

    def _harness(self):
        """最小引擎仓：只满足 fix_paths 的校验（它要 <harness>/agentguard/mcp.py 在）。"""
        h = os.path.join(self.base, "harness")
        _write(os.path.join(h, "agentguard", "mcp.py"), "# 只为让 fix_paths 的校验通过\n")
        return h

    def _pack(self, name, version, marker, only_file, wheel_marker):
        """造一个版本的安装包：包内正文带 marker，另有一个只有这一版才有的文件。

        `wheel_marker` 与 `marker` 分开：**同版本号、内容不同**那一场（G3 的形状）要求两个包的
        轮子文件名一模一样而内容不同 —— 轮子里的标记因此不能是版本的函数（否则两边必然相同，
        那条"拒绝换内容"的锁就会因为"内容本来就一致"而假绿）。
        """
        pack = os.path.join(self.base, name)
        bundle = os.path.join(pack, "marketplace", "plugins", "agentguard")
        _write(os.path.join(bundle, ".claude-plugin", "plugin.json"),
               json.dumps({"name": "agentguard", "version": version}, indent=2) + "\n")
        _write(os.path.join(bundle, ".mcp.json"), PLACEHOLDER_MCP)
        _write(os.path.join(bundle, "README.md"), marker)
        _write(os.path.join(bundle, only_file), "只有这一版有\n")
        _write(os.path.join(pack, "marketplace", ".claude-plugin", "marketplace.json"),
               json.dumps({"name": "agentguard-tools"}, indent=2) + "\n")
        shutil.copyfile(FIXER, os.path.join(pack, "fix_paths.py"))
        _wheel(os.path.join(pack, "agentguard-%s-py3-none-any.whl" % version), wheel_marker)
        return pack

    def _bundle(self, pack):
        return os.path.join(pack, "marketplace", "plugins", "agentguard")

    def _engine_pack(self, name, version, marker):
        """安装包 + **自带引擎**的轮子（[5/7] 的刷新源就是这只轮子）。"""
        pack = self._pack(name, version, "# %s 版\n" % name, "only-%s.md" % name, marker)
        _engine_wheel(os.path.join(pack, "agentguard-%s-py3-none-any.whl" % version),
                      version, marker)
        return pack

    def _install_engine_a(self, pack):
        """用 A 包轮子里的 cli 真跑一次 init —— 造出「这台机器上已经是 A」的引擎副本。

        不拿本仓库当源：那样"前一代"就变成几千个真实文件，慢且与被测行为无关。
        走的是同一段 `agentguard init` 代码，产出（标记 / 指针 / 布局）与真包一致。
        """
        wheel = glob.glob(os.path.join(pack, "*.whl"))[0]
        ext = os.path.join(self.base, "extract-" + os.path.basename(pack))
        with zipfile.ZipFile(wheel) as z:
            for n in z.namelist():
                if n.split("/", 1)[0] in ("guardrail_engine", "agentguard") and not n.endswith("/"):
                    dst = os.path.join(ext, n.replace("/", os.sep))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with open(dst, "wb") as f:
                        f.write(z.read(n))
        eng = os.path.join(self.home, ".agentguard", "engine")
        env = dict(os.environ)
        env["HOME"] = self.home
        env["USERPROFILE"] = self.home
        # init 的自检会跑 <dest>/harness/run_gate.py --list：探针记录钉到临时文件，
        # 不许落到仓库的 reports/gate_log.jsonl（test_coverage 的隔离判据要求）。
        env["HARNESS_LOG_FILE"] = os.path.join(self.base, "gate_log.jsonl")
        for k in ("GUARDRAIL_ENGINE_DIR", "AGENTGUARD_DIR", "HARNESS_DIR"):
            env.pop(k, None)
        r = subprocess.run([sys.executable, os.path.join(ext, "agentguard", "cli.py"),
                            "init", "--dir", eng], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)
        self.assertEqual(r.returncode, 0, (r.stdout or "") + (r.stderr or ""))
        return eng

    # ---- 运行 ----

    def _run(self, *args, env_extra=None):
        args = list(args)
        # 硬护栏：--yes 且没点名跳过/注入 claude 时，本机会真的去调 PATH 上的 claude →
        # 那是**真机配置**。宁可测试红，也不能悄悄动它。
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
        # 同 _install_engine_a：引擎步会调 init→run_gate，探针记录一律落临时目录。
        env["HARNESS_LOG_FILE"] = os.path.join(self.base, "gate_log.jsonl")
        for k in ("GUARDRAIL_ENGINE_DIR", "AGENTGUARD_DIR", "HARNESS_DIR"):
            env.pop(k, None)
        if env_extra:
            env.update(env_extra)
        return subprocess.run([sys.executable, UPGRADE] + args, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env)

    def _installed(self):
        """跑真的 `cli.py installed`（假 HOME）—— G4 的安装矩阵，用作升级前后对照。"""
        env = dict(os.environ)
        env["HOME"] = self.home
        env["USERPROFILE"] = self.home
        env["CLAUDE_CONFIG_DIR"] = os.path.join(self.home, ".claude")
        r = subprocess.run([sys.executable, CLI, "installed"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, cwd=ROOT)
        return (r.stdout or "") + (r.stderr or "")

    @staticmethod
    def _matrix_line(text, prefix):
        for ln in text.splitlines():
            if ln.strip().startswith(prefix):
                return ln.strip()
        return ""

    # ================= 通道 ①：OpenClaw 扩展（upgrade.py 自己搬文件） =================

    def test_openclaw_extension_gets_b_content_and_keeps_the_orphan(self):
        """装过 A 的扩展 → 拿 B 包升级 → 留下的必须是 B 的内容，且 A 的孤儿文件不被删。

        两个方向都要断言：只断言"B 的新文件在"是不够的 —— 复制一份新的、旧的还在，
        也能让那句成立。**共享文件的内容变成 B** 才是"换掉了"的证据；而 A 独有的那个文件
        **必须还在**（升级不做任何撤回，旧文件只列出来交给人判断）。
        """
        pa = self._pack("packA", "0.1.2.dev0", "# A 版\n", "only-A.md", "AAA")
        pb = self._pack("packB", "0.1.3.dev0", "# B 版\n", "only-B.md", "BBB")
        ext = os.path.join(self.home, ".openclaw-autoclaw", "extensions", "agentguard")
        shutil.copytree(self._bundle(pa), ext)          # 这台机器上装的是 A
        self.assertIn("A 版", _read(os.path.join(ext, "README.md")))

        r = self._run("--pack", pb, "--harness", self._harness(), "--yes",
                      "--skip", "claude,pip,skills,shim")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("已覆盖", r.stdout)
        self.assertEqual(_read(os.path.join(ext, "README.md")),
                         _read(os.path.join(self._bundle(pb), "README.md")),
                         "共享文件的内容没换成 B —— 这次升级没真的搬东西")
        self.assertTrue(os.path.isfile(os.path.join(ext, "only-B.md")), "B 的新文件没落地")
        self.assertTrue(os.path.isfile(os.path.join(ext, "only-A.md")),
                        "A 独有的文件被删了 —— 升级不做任何撤回（它只该列出来交给人判断）")
        # 覆盖前那份 .mcp.json 是本机路径、新包里是占位符：fix_paths 必须先跑（[1/7]），
        # 否则这一格就是把 __PY__ 覆盖进去 —— 那正是"编辑期门禁整体无声失效"的现场。
        got = json.loads(_read(os.path.join(ext, ".mcp.json")))["mcpServers"]["agentguard"]
        self.assertNotIn("__", got["command"], "覆盖进去的 .mcp.json 还是占位符：%r" % got["command"])

    # ================= 通道 ②：技能副本（转发**真** cli.py） =================

    @_slow("真拷技能副本 + 真跑安装矩阵（2.44s）")
    def test_skill_copy_gets_b_content_and_the_matrix_notices(self):
        """装过 A 的技能副本 → 拿 B 包升级 → 内容换成引擎仓现状，孤儿不删，矩阵也跟着变。

        这一格的"B"就是**引擎仓里当前的技能源**（技能副本没有版本号，本来就没有"哪一版"）：
        A 是"与源不一致的陈旧副本"。所以判据是"副本重新与源逐字节一致"，而不是某个版本号。
        顺带把 G4 的安装矩阵一起断言：升级前后它必须从「落后」变成「与引擎仓一致」——
        否则"机器上到底是哪一版"这个视图就只是打印出来好看，升级它读不出来。
        """
        pb = self._pack("packB", "0.1.3.dev0", "# B 版\n", "only-B.md", "BBB")
        target = os.path.join(self.home, ".claude", "skills", "agentguard")
        shutil.copytree(SKILL_SRC, target)
        _write(os.path.join(target, "SKILL.md"), "# 上一版的内容（与引擎仓不同）\n")
        _write(os.path.join(target, "ORPHAN.md"), "上一版卸载/改名留下的孤儿\n")

        before = self._matrix_line(self._installed(), "技能副本[claude]")
        self.assertIn("落后", before, "升级前矩阵没报出技能副本陈旧：%s" % before)

        r = self._run("--pack", pb, "--harness", ROOT, "--yes", "--skip", "claude,openclaw,pip,shim")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("转发 cli.py skill install → rc=0", r.stdout)
        self.assertEqual(_read(os.path.join(target, "SKILL.md")),
                         _read(os.path.join(SKILL_SRC, "SKILL.md")),
                         "技能副本没换成引擎仓当前的内容")
        self.assertTrue(os.path.isfile(os.path.join(target, "ORPHAN.md")),
                        "孤儿文件被删了 —— `skill install` 是无删除的幂等复制")

        after = self._matrix_line(self._installed(), "技能副本[claude]")
        self.assertIn("与引擎仓一致", after, "升级后矩阵没认出来：%s" % after)

    # ================= 通道 ③：pip 包（替身 pip；真 pip 不能跑） =================

    @_slow("真跑替身 pip 的装 A→装 B（2.24s）")
    def test_pip_channel_refreshes_a_to_b_and_says_so(self):
        """这台机器的 pip 包是 A、包是 B → 升完**产物**必须是 B 的内容（不是"rc=0 就算数"）。"""
        site = os.path.join(self.base, "site-packages")
        _write(os.path.join(site, "agentguard", "__init__.py"), "MARKER = 'AAA'\n")
        py = _fake_python(self.base, site, "install", "0.1.2.dev0")
        pb = self._pack("packB", "0.1.3.dev0", "# B 版\n", "only-B.md", "BBB")

        r = self._run("--pack", pb, "--harness", self._harness(), "--python", py, "--yes",
                      "--skip", "claude,openclaw,skills,shim")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("✓ 本机这份与本包逐字节一致", r.stdout)
        self.assertIn("✅ 升级完成", r.stdout)
        self.assertIn("'BBB'", _read(os.path.join(site, "agentguard", "__init__.py")),
                      "报成功了，但产物还是 A —— 这条通道的判据是产物，不是 rc")

    @_slow("真跑替身 pip + --force-reinstall 那一路（2.97s）")
    def test_pip_same_version_noop_is_healed_by_an_automatic_force_reinstall(self):
        """**同版本号、内容不同** → pip 会 rc=0 且一个字节都不换；脚本必须接着代跑 --force-reinstall。

        三件事一起锁，缺一条就有一种假成功能溜过去：
          · 判据不能是 rc —— 同版本号时 pip 的 rc **就是 0**。这是 2026-09-17 修掉的形状：
            判据挂在 `rc != 0` 上，那句提示于是永远不执行，脚本照报「✅ 升级完成」，
            机器上跑的却还是旧内容；
          · 没换就得**说出来**（引用 pip 的真实原话，照 `already satisfied` 搜是搜不到的）；
          · 说了还得**真换**：用户 2026-09-17 拍板由脚本代跑 `--force-reinstall`（实测同版本号下
            它确实能换内容）。所以判据落在**产物字节**上，而不是"输出里出现了 --force-reinstall
            这个字样"—— 替身 MODE=noop 复刻的正是实测到的 pip 行为（原话见 FAKE_MAIN）。
        """
        site = os.path.join(self.base, "site-packages")
        _write(os.path.join(site, "agentguard", "__init__.py"), "MARKER = 'AAA'\n")
        py = _fake_python(self.base, site, "noop", "0.1.2.dev0")
        # 同版本号重建的包：文件名里的版本与已装的完全一样，内容却是 B
        pb = self._pack("packB", "0.1.2.dev0", "# B 版\n", "only-B.md", "BBB")

        preview = self._run("--pack", pb, "--harness", self._harness(), "--python", py,
                            "--skip", "claude,openclaw,skills,shim")
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertIn("拒绝换内容", preview.stdout,
                      "预演就该说破同版本号的坑，而不是等同事跑完 --yes 才发现：\n%s" % preview.stdout)
        self.assertIn("代跑", preview.stdout,
                      "预演没说清脚本会替人跑 --force-reinstall → 同事会以为还得自己动手：\n%s"
                      % preview.stdout)

        r = self._run("--pack", pb, "--harness", self._harness(), "--python", py, "--yes",
                      "--skip", "claude,openclaw,skills,shim")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("仍不一致", r.stdout, "第一次装完没换，必须当场报出来：\n%s" % r.stdout)
        self.assertIn("already installed with the same version", r.stdout,
                      "没引用 pip 的**真实**原话 —— 上一版引的是 already satisfied，"
                      "而 pip 实际不这么说（实测），照着搜会搜不到")
        self.assertIn("--force-reinstall", r.stdout)
        self.assertIn("代跑之后与本包逐字节一致", r.stdout,
                      "代跑完没报告结果 —— 人无从知道到底是哪一步把内容换过来的：\n%s" % r.stdout)
        self.assertIn("✅ 升级完成", r.stdout)
        self.assertIn("'BBB'", _read(os.path.join(site, "agentguard", "__init__.py")),
                      "报了「已代跑」而产物还是 A —— 这条通道的判据是产物，不是输出里的话")
        self.assertIn("已代跑 --force-reinstall", _read(self.ops),
                      "代跑动了本机的东西却没留痕 —— 台账是事后唯一能查「它到底跑过什么」的地方")

    @_slow("真跑替身 pip 的拒绝支路（2.42s）")
    def test_pip_gives_up_honestly_when_force_reinstall_does_not_help(self):
        """连**代跑的** `--force-reinstall` 也换不动 → 判失败（rc=1），不许报成功。

        为什么单独锁这条：代跑引入了"第二条命令也会失败"这个新分支（权限不足 / 索引指向别处 /
        被另一份安装遮住）。若那段写错成"跑过 force-reinstall 就算刷新了"，这条通道就退回
        "动作做过了 = 成功了"的形状 —— 而整批要杀的正是这个形状。替身 MODE=refuse 复刻它。
        """
        site = os.path.join(self.base, "site-packages")
        _write(os.path.join(site, "agentguard", "__init__.py"), "MARKER = 'AAA'\n")
        py = _fake_python(self.base, site, "refuse", "0.1.2.dev0")
        pb = self._pack("packB", "0.1.2.dev0", "# B 版\n", "only-B.md", "BBB")

        r = self._run("--pack", pb, "--harness", self._harness(), "--python", py, "--yes",
                      "--skip", "claude,openclaw,skills,shim")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("--force-reinstall", r.stdout)
        self.assertIn("仍与本包不同", r.stdout, "代跑也没换动，却没说清结论：\n%s" % r.stdout)
        self.assertNotIn("✅ 升级完成", r.stdout, "内容没换却报成功（rc 已 1，文案更不许骗人）")
        self.assertIn("'AAA'", _read(os.path.join(site, "agentguard", "__init__.py")),
                      "替身本该什么都没做 —— 那正是要复刻的 pip 行为")
        log = _read(self.ops)
        self.assertIn('"stale"', log, "没留痕说清「本机这份仍与包不同」：%s" % log)

    # ================= 通道 ⑤：引擎副本（upgrade.py 解包轮子跑 init） =================

    @_slow("真装 A 再升 B（[5/7] 引擎副本 + 归档回滚）")
    def test_engine_copy_gets_b_content_and_the_old_one_is_archived(self):
        """init 复制出来的引擎副本：A → B 之后，VERSION 与**正文**都必须是 B，旧副本可回滚。

        判据三向，缺一条就有一种假成功能溜过去：
          · VERSION 换了 —— 最容易被 `init` 的退出码冒充的那一条；
          · 正文换了（engine_marker）—— 只换版本号不换内容时它不动；
          · 旧目录归档且**里面还是 A** —— 没有归档就没有回退，等于升级夹带了删除。
        """
        pa = self._engine_pack("packA", "0.1.2.dev0", "AAA")
        pb = self._engine_pack("packB", "0.1.3.dev0", "BBB")
        eng = self._install_engine_a(pa)
        self.assertEqual(_read(os.path.join(eng, "VERSION")).strip(), "0.1.2.dev0")
        self.assertIn("'AAA'", _read(os.path.join(eng, "harness", "engine_marker.py")))
        self.assertEqual(json.loads(_read(os.path.join(eng, ".guardrail-engine-init.json"))).get("kind"),
                         "guardrail-engine-init")

        r = self._run("--pack", pb, "--harness", eng, "--yes",
                      "--skip", "claude,openclaw,pip,skills,shim")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("✅ 升级完成", r.stdout)
        self.assertIn("与本包逐字节一致", r.stdout)
        self.assertEqual(_read(os.path.join(eng, "VERSION")).strip(), "0.1.3.dev0",
                         "引擎副本的 VERSION 没换成 B")
        self.assertIn("'BBB'", _read(os.path.join(eng, "harness", "engine_marker.py")),
                      "报成功了，但引擎正文还是 A —— 这条通道的判据是产物，不是 init 的 rc")
        archives = glob.glob(eng + ".disabled-*")
        self.assertEqual(len(archives), 1, "旧引擎没留下可回滚的归档：%s" % r.stdout)
        self.assertEqual(_read(os.path.join(archives[0], "VERSION")).strip(), "0.1.2.dev0",
                         "归档里不是升级前那份引擎（回滚会回错版本）")
        self.assertIn("'AAA'", _read(os.path.join(archives[0], "harness", "engine_marker.py")))
        marker = json.loads(_read(os.path.join(eng, ".guardrail-engine-init.json")))
        self.assertEqual(marker.get("version"), "0.1.3.dev0")
        self.assertEqual(marker.get("source"), "wheel", "从轮子 init 出来的副本应标 source=wheel")
        # 配置目录迁移期双读：新目录不存在而旧目录在时继续用旧目录（不迁移、不丢配置）。
        ptr = next((p for p in (os.path.join(self.home, ".guardrail-engine", "harness.path"),
                                os.path.join(self.home, ".agentguard", "harness.path"))
                    if os.path.isfile(p)), None)
        self.assertIsNotNone(ptr, "init 没写 harness.path 指针（find_root 会解析到别处）")
        self.assertEqual(_read(ptr).strip(), eng,
                         "指针没指回刷新后的引擎副本（find_root 会解析到别处）")
        log = _read(self.ops)
        self.assertIn('"step": "engine"', log)
        self.assertIn('"result": "ok"', log)

    @_slow("真跑 [5/7] 的「仓库不是靶」支路")
    def test_engine_step_never_archives_a_repo(self):
        """`--harness` 是引擎源（带 .git）：绝不归档、绝不写标记 —— 那是把源搬走。"""
        repo = os.path.join(self.base, "engine-repo")
        _write(os.path.join(repo, "agentguard", "mcp.py"), "#\n")
        _write(os.path.join(repo, "harness", "run_gate.py"), "#\n")
        os.makedirs(os.path.join(repo, ".git"))
        pb = self._engine_pack("packB", "0.1.3.dev0", "BBB")

        r = self._run("--pack", pb, "--harness", repo, "--yes",
                      "--skip", "claude,openclaw,pip,skills,shim")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("引擎源（仓库）", r.stdout, "没认出 --harness 是仓库：\n%s" % r.stdout)
        self.assertFalse(os.path.exists(os.path.join(repo, ".guardrail-engine-init.json")),
                         "给仓库写了 init 标记 —— 下次升级会把它当成可归档的副本")
        self.assertFalse(glob.glob(repo + ".disabled-*"), "仓库被归档了（源被搬走）")
        self.assertTrue(os.path.isfile(os.path.join(repo, "harness", "run_gate.py")))

    @_slow("真跑 [5/7] 的「init rc=0 但没换内容」支路")
    def test_engine_step_fails_when_init_reports_ok_but_copies_nothing(self):
        """init 报 rc=0、产物没换 → 必须判失败（与 pip / 插件同一条纪律：rc 不是判据）。"""
        pa = self._engine_pack("packA", "0.1.2.dev0", "AAA")
        pb = self._engine_pack("packB", "0.1.3.dev0", "BBB")
        eng = self._install_engine_a(pa)
        py = _fake_noop_init_python(self.base)

        r = self._run("--pack", pb, "--harness", eng, "--python", py, "--yes",
                      "--skip", "claude,openclaw,pip,skills,shim")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("init --force → rc=0", r.stdout)
        self.assertIn("仍不一致", r.stdout,
                      "init 报成功但什么都没换，脚本没当场说破：\n%s" % r.stdout)
        self.assertNotIn("✅ 升级完成", r.stdout, "内容没换却报成功（rc 已 1，文案更不许骗人）")
        self.assertEqual(_read(os.path.join(eng, "VERSION")).strip(), "0.1.2.dev0",
                         "替身本该什么都没做 —— 那正是要复刻的 init 假成功")
        self.assertIn('"stale"', _read(self.ops), "没留痕说清「引擎副本仍与包不同」：%s" % _read(self.ops))

    @_slow("真装 A 再预演 B（[5/7] 预演不改东西）")
    def test_engine_rehearsal_prints_the_planned_init_and_changes_nothing(self):
        """预演要说清「会解包轮子 + 对哪个目录跑 init --force」，且一个字节都不写。"""
        pa = self._engine_pack("packA", "0.1.2.dev0", "AAA")
        pb = self._engine_pack("packB", "0.1.3.dev0", "BBB")
        eng = self._install_engine_a(pa)
        before = _read(os.path.join(eng, "VERSION"))

        r = self._run("--pack", pb, "--harness", eng,
                      "--skip", "claude,openclaw,pip,skills,shim")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("将刷新", r.stdout)
        self.assertIn("解包轮子", r.stdout)
        self.assertIn("init --force --dir", r.stdout)
        self.assertEqual(_read(os.path.join(eng, "VERSION")), before, "预演改了引擎副本")
        self.assertFalse(glob.glob(eng + ".disabled-*"), "预演产生了归档")

    # ================= 通道 ④：Claude 插件（真搬运在宿主，只锁诊断） =================

    def test_plugin_copy_stale_across_versions_is_not_excused_by_the_version(self):
        """副本是 A、包是 B（**版本号也不同**）而宿主没搬 → 必须报没刷新，且不许拿版本号解释。

        为什么单独锁这条：同版本号那一路已有锁（`test_same_version_silent_noop_is_called_out`），
        但真实事故里更常见的是"版本号确实升了、内容却没换"（宿主没重启、市场指错、缓存没落）。
        那时若把原因写成「版本号相同 → plugin update 判了已是最新」，读的人会去升一个**已经升过**的号，
        然后困惑为什么还是没换。诊断错了比不报更坏 —— 假警报比漏报更磨损可信度。
        """
        pack = self._pack("packB", "0.1.3.dev0", "# B 版\n", "only-B.md", "BBB")
        inst = os.path.join(self.home, ".claude", "plugins", "cache",
                            "agentguard-tools", "agentguard", "0.1.2.dev0")
        _write(os.path.join(inst, ".claude-plugin", "plugin.json"),
               json.dumps({"name": "agentguard", "version": "0.1.2.dev0"}, indent=2) + "\n")
        _write(os.path.join(inst, ".mcp.json"), PLACEHOLDER_MCP)
        _write(os.path.join(inst, "README.md"), "# A 版（与包不同）\n")
        _write(os.path.join(self.home, ".claude", "plugins", "installed_plugins.json"),
               json.dumps({"version": 2, "plugins": {PLUGIN: [
                   {"scope": "user", "installPath": inst, "version": "0.1.2.dev0"}]}}, indent=2) + "\n")
        cli = _fake_exe(self.base, "claude", rc=0)      # 宿主 CLI 什么都不做

        r = self._run("--pack", pack, "--harness", self._harness(), "--yes",
                      "--skip", "openclaw,pip,skills,shim", "--claude", cli)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("仍不一致", r.stdout)
        self.assertNotIn("版本号相同", r.stdout,
                         "副本 0.1.2 与包 0.1.3 是两个版本号 —— 拿「版本号相同」去解释会让"
                         "人跑去升一个已经升过的号：\n%s" % r.stdout)
        self.assertNotIn("✅ 升级完成", r.stdout)


if __name__ == "__main__":
    unittest.main()
