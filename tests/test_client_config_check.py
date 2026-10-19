# -*- coding: utf-8 -*-
"""客户端配置静态体检器的回归锁（含证伪：坏配置必须被揪出来）。

背景：Trae CN 踩到「命令含引号被 PowerShell 拼崩」+「PowerShell 管道吞掉退出码」两个坑，
表现为「钩子有执行日志、模型却什么都没收到」。体检器就是把这套判断固化下来。
"""
import importlib.util
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest


def _load():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "sandbox", "check_client_configs.py")
    spec = importlib.util.spec_from_file_location("ccc", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


PY = sys.executable
# 夹具里的**解释器**必须是真实存在的路径（2026-09-23 L11 起体检器会校验它）。
# 原先各处写的是 `C:/x/python.exe` 这种占位路径 —— 判据一加，占位路径立刻成了"确定坏"。
# 换成 PY 而不是"在临时目录里造个假解释器"：判据问的是"这个路径本机在不在"，
# 用真解释器才是**夹具自足**（临时目录造的文件只证明判据会查 isfile）。
PY_FWD = PY.replace("\\", "/")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CCC = _load()


def _isolate_managed_repos(case, paths=()):
    """把「本机配了哪些客户仓」从被测环境里摘掉（`main()` 会去问本机覆盖层）。

    不摘的话，`main()` 的退出码就取决于**这台机器**装没装客户仓、那些仓里干不干净 ——
    那正是 `c2b6e1b`「夹具写死了开发机状态」那个反复咬人的病：同一份代码本机绿、CI 红
    （或反过来）。2026-09-21 加项目级检查时就是这么撞上的：本机有 6 个仓带着待撤的
    `zcode.json`，`test_warn_does_not_fail_exit_code` 当场变红，而 CI 上它一直是绿的。
    """
    real = CCC.managed_repo_paths
    CCC.managed_repo_paths = lambda: list(paths)
    case.addCleanup(setattr, CCC, "managed_repo_paths", real)


class TestClientConfigCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # 显式清理：不清理会在 GC 期抛 ResourceWarning（被测的 CI 严格句柄门槛会红）
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        self.gate = os.path.join(self.home, "agentguard-gate.py")   # 名字要带门禁关键字（体检器按它认）
        pathlib.Path(self.gate).write_text("# stub\n", encoding="utf-8")
        self.cfg = [c for c in CCC.CLIENTS if c["name"] == "trae"][0]
        _isolate_managed_repos(self)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rel, obj_or_text):
        p = os.path.join(self.home, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if isinstance(obj_or_text, str):
            pathlib.Path(p).write_text(obj_or_text, encoding="utf-8", newline="")
        else:
            pathlib.Path(p).write_text(json.dumps(obj_or_text, ensure_ascii=False), encoding="utf-8", newline="")
        return p

    def _trae(self, command, events=("PostToolUse", "Stop")):
        hooks = {e: [{"matcher": "Write|Edit",
                      "hooks": [{"type": "command", "command": command, "timeout": 200}]}] for e in events}
        return {"hooks": hooks}

    def _good(self):
        return "%s %s trae; exit $LASTEXITCODE" % (PY_FWD, self.gate.replace("\\", "/"))

    # ---------- 正例 ----------
    def test_good_config_passes(self):
        self._write(".trae-cn/hooks.json", self._trae(self._good()))
        lvl, probs, cmds = CCC.check_client(self.cfg, self.home)
        self.assertEqual(lvl, "ok", probs)
        self.assertEqual(len(cmds), 2)

    # ---------- 反例（证伪：这些必须被揪出来）----------
    def test_quotes_flagged_for_powershell_host(self):
        bad = '"C:/x/python.exe" "%s" trae; exit $LASTEXITCODE' % self.gate.replace("\\", "/")
        self._write(".trae-cn/hooks.json", self._trae(bad))
        lvl, probs, _ = CCC.check_client(self.cfg, self.home)
        self.assertEqual(lvl, "bad")
        self.assertTrue(any("引号" in p for p in probs), probs)

    def test_missing_exit_passthrough_flagged(self):
        bad = "C:/x/python.exe %s trae" % self.gate.replace("\\", "/")
        self._write(".trae-cn/hooks.json", self._trae(bad))
        lvl, probs, _ = CCC.check_client(self.cfg, self.home)
        self.assertEqual(lvl, "bad")
        self.assertTrue(any("LASTEXITCODE" in p for p in probs), probs)

    def test_invalid_json_flagged(self):
        self._write(".trae-cn/hooks.json", '{ "hooks": { "PostToolUse": [ { "command": "C:\\Users\\x gate.py" } ] }')
        lvl, probs, _ = CCC.check_client(self.cfg, self.home)
        self.assertEqual(lvl, "bad")
        self.assertTrue(any("JSON 非法" in p for p in probs), probs)

    def test_wrong_client_arg_flagged(self):
        bad = "C:/x/python.exe %s claude; exit $LASTEXITCODE" % self.gate.replace("\\", "/")
        self._write(".trae-cn/hooks.json", self._trae(bad))
        lvl, probs, _ = CCC.check_client(self.cfg, self.home)
        self.assertEqual(lvl, "bad")
        self.assertTrue(any("client=" in p for p in probs), probs)

    def test_missing_stop_event_flagged(self):
        """缺关键事件 → **⚠ 待复核**（Q4-④ 评审 v4 的三态语义：事件集合非空但与基线不符
        多半是客户端改名/升级，"没见过"不该当"坏"）；确定坏仍由其它锁保证是 ❌。"""
        self._write(".trae-cn/hooks.json", self._trae(self._good(), events=("PostToolUse",)))
        lvl, probs, _ = CCC.check_client(self.cfg, self.home)
        self.assertEqual(lvl, "warn", probs)
        self.assertTrue(any("缺关键事件" in p for p in probs), probs)

    def test_nonexistent_gate_script_flagged(self):
        bad = "C:/x/python.exe C:/nope/agentguard-gate.py trae; exit $LASTEXITCODE"
        self._write(".trae-cn/hooks.json", self._trae(bad))
        lvl, probs, _ = CCC.check_client(self.cfg, self.home)
        self.assertEqual(lvl, "bad")
        self.assertTrue(any("不存在" in p for p in probs), probs)

    def test_not_configured_is_skip_not_fail(self):
        lvl, probs, _ = CCC.check_client(self.cfg, self.home)
        self.assertEqual(lvl, "skip", probs)

    def test_main_exit_code_reflects_bad(self):
        self._write(".trae-cn/hooks.json", self._trae("C:/x/python.exe %s trae" % self.gate.replace("\\", "/")))
        self.assertEqual(CCC.main(["--home", self.home]), 1)
        self._write(".trae-cn/hooks.json", self._trae(self._good()))
        self.assertIn(CCC.main(["--home", self.home, "--json"]), (0, 1))   # 其余客户端在本机未配置 → skip 不算坏



class TestShimForwardTargetChecked(unittest.TestCase):
    """体检器必须校验 **shim 的转发目标**，不能只看"配置里写的脚本文件在不在"。

    2026-09-13 外部评审 G2：客户端配置里写的是本机薄转发器（~/.agentguard/hooks/…），
    它转发到工程仓里的正本；工程仓挪走/换机器后 shim 会静默 exit 0 →
    7 家客户端的编辑期门禁**整体无声失效**。这正是本项目最想消灭的失败类型，
    所以体检器必须把它抓出来（原实现只校验 shim 文件本身存在 → 抓不到）。
    注：写这条锁时发现过自己一个 bug —— 解析指针文件用了真实 ~，导致临时环境假绿；
    所以这里特意用临时 home 造场景，两向都断言。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # 显式清理：不清理会在 GC 期抛 ResourceWarning（被测的 CI 严格句柄门槛会红）
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        os.makedirs(os.path.join(self.home, ".trae-cn"))
        os.makedirs(os.path.join(self.home, ".agentguard", "hooks"))
        self.shim = os.path.join(self.home, ".agentguard", "hooks", "agentguard-gate.py")
        self.cfg = [c for c in CCC.CLIENTS if c["name"] == "trae"][0]

    def tearDown(self):
        self.tmp.cleanup()

    def _write_cfg(self):
        cmd = "%s trae; exit $LASTEXITCODE" % self.shim.replace("\\", "/")
        pathlib.Path(os.path.join(self.home, ".trae-cn", "hooks.json")).write_text(json.dumps(
            {"hooks": {"PostToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": cmd}]}],
                       "Stop": [{"hooks": [{"type": "command", "command": cmd}]}]}}), encoding="utf-8")

    def _real_shim(self, gen_path):
        """按**真实模板**造 shim —— 体检器已改为按内容特征识别（H5b），
        手写的假文件（只含 GEN_PATH、没有 runpy）会被当作"直连正本"而跳过校验。"""
        tmpl = os.path.join(ROOT, "integrations", "hooks", "agentguard-gate-shim.py")
        src = __import__('pathlib').Path(tmpl).read_text(encoding="utf-8").replace("__HARNESS__", gen_path)
        pathlib.Path(self.shim).write_text(src, encoding="utf-8", newline="")

    def test_broken_shim_target_is_reported(self):
        self._real_shim("C:/definitely/not/here")
        self._write_cfg()
        lvl, probs, _ = CCC.check_client(self.cfg, self.home)
        self.assertEqual(lvl, "bad", f"shim 目标失效必须报错，实际 {probs}")
        self.assertTrue(any("转发目标" in p for p in probs), probs)

    def test_resolvable_shim_target_passes(self):
        ok_root = os.path.join(self.tmp.name, "harness_ok")
        os.makedirs(os.path.join(ok_root, "integrations", "hooks"))
        pathlib.Path(os.path.join(ok_root, "integrations", "hooks", "agentguard-gate.py")).write_text("# stub\n", encoding="utf-8")
        self._real_shim(ok_root.replace("\\", "/"))
        self._write_cfg()
        lvl, probs, _ = CCC.check_client(self.cfg, self.home)
        self.assertEqual(lvl, "ok", probs)


class TestShimSpeaksWhenHarnessMissing(unittest.TestCase):
    """shim 在**找不到工程仓**时必须「不阻塞但出声」（2026-09-13 外部评审 G2）。

    设计承诺是"宁可不过门禁也不卡住同事"（exit 0）——这条保留；但原先连提示都没有，
    等于失效不可见。现在要求：① exit 0；② stderr 打一次提示；③ 落去重标记，第二次不再刷屏。
    """

    TEMPLATE = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # 显式清理：不清理会在 GC 期抛 ResourceWarning（被测的 CI 严格句柄门槛会红）
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        tmpl = os.path.join(ROOT, "integrations", "hooks", "agentguard-gate-shim.py")
        src = __import__('pathlib').Path(tmpl).read_text(encoding="utf-8").replace("__HARNESS__", "C:/definitely/not/here")
        self.shim = os.path.join(self.home, "shim.py")
        pathlib.Path(self.shim).write_text(src, encoding="utf-8", newline="")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self):
        env = dict(os.environ, HOME=self.home, USERPROFILE=self.home, HARNESS_DIR="", AGENTGUARD_DIR="")
        return subprocess.run([PY, self.shim, "claude", "PostToolUse"], input='{"cwd":"x"}',
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=env, timeout=90)

    def test_not_blocking_but_speaks_once(self):
        r1 = self._run()
        self.assertEqual(r1.returncode, 0, "shim 目标缺失不得阻塞（exit 0）")
        self.assertIn("找不到门禁实现", r1.stderr, "必须出声，否则失效不可见")
        flag = os.path.join(self.home, ".agentguard", "shim-missing.log")
        self.assertTrue(os.path.isfile(flag), "应落去重标记")
        r2 = self._run()
        self.assertEqual(r2.returncode, 0)
        self.assertNotIn("找不到门禁实现", r2.stderr, "第二次不应再刷屏（去重）")


    # ── 以下四条 = 外部评审 v3 的 H1b / H1 / H3 锁 ──────────────────────────────

    def _write_shim(self, gen_path):
        tmpl = os.path.join(ROOT, "integrations", "hooks", "agentguard-gate-shim.py")
        src = __import__('pathlib').Path(tmpl).read_text(encoding="utf-8").replace("__HARNESS__", gen_path)
        pathlib.Path(self.shim).write_text(src, encoding="utf-8", newline="")

    def _run_env(self, extra_env):
        env = dict(os.environ, HOME=self.home, USERPROFILE=self.home, HARNESS_DIR="", AGENTGUARD_DIR="")
        env.update(extra_env)
        return subprocess.run([PY, self.shim, "claude", "PostToolUse"], input='{"cwd":"x"}',
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=env, timeout=90)

    def test_speaks_again_after_recovery_then_failure(self):
        """H1b 三段：失效→出声；恢复一次→标记被清；再失效→**必须再次出声**。

        原实现只有"首次出声 / 第二次去重"两条断言，"恢复即清标记"这条路径没有任何锁
        —— 一旦清标记的代码坏了，故障会被永久静默（反静默机制的自我背叛）。
        """
        flag = os.path.join(self.home, ".agentguard", "shim-missing.log")
        ok_root = os.path.join(self.home, "harness_ok")
        os.makedirs(os.path.join(ok_root, "integrations", "hooks"))
        pathlib.Path(os.path.join(ok_root, "integrations", "hooks", "agentguard-gate.py")).write_text("# stub\n", encoding="utf-8")

        self._write_shim("C:/definitely/not/here")
        r1 = self._run_env({})
        self.assertIn("找不到门禁实现", r1.stderr)
        self.assertTrue(os.path.isfile(flag), "第 1 阶段应落标记")

        self._write_shim(ok_root.replace("\\", "/"))
        self._run_env({})
        self.assertFalse(os.path.isfile(flag), "恢复一次后标记应被清除，否则下次故障就哑了")

        self._write_shim("C:/definitely/not/here")
        r3 = self._run_env({})
        self.assertIn("找不到门禁实现", r3.stderr, "再次失效必须重新出声")

    def test_notice_survives_gbk_pipe(self):
        """H1（P1）：宿主管道是 GBK 时，提示也必须**可读**送达。

        评审 v3 的复现：不设 PYTHONUTF8 时子进程默认 GBK，shim 的中文提示按 GBK 写出、
        调用方按 UTF-8 解码成乱码（我最初的锁在本地被 PYTHONUTF8=1 掩盖，全绿是假象）。
        修法 = shim 顶部 reconfigure(utf-8, errors=replace)；反向证伪：删掉那两行本锁必红。
        """
        self._write_shim("C:/definitely/not/here")
        r = self._run_env({"PYTHONIOENCODING": "gbk", "PYTHONUTF8": "0"})
        self.assertEqual(r.returncode, 0)
        self.assertIn("找不到门禁实现", r.stderr, "GBK 管道下提示变乱码/丢失了")

    def test_stale_marker_re_notifies(self):
        """H3：标记超过 RENOTIFY_DAYS 仍没恢复 → 再提醒一次（否则长期静默）。"""
        import time as _t
        self._write_shim("C:/definitely/not/here")
        r1 = self._run_env({})
        self.assertIn("找不到门禁实现", r1.stderr)
        flag = os.path.join(self.home, ".agentguard", "shim-missing.log")
        old = _t.time() - 8 * 86400
        os.utime(flag, (old, old))
        r2 = self._run_env({})
        self.assertIn("找不到门禁实现", r2.stderr, "标记过期后应再次提醒")


class TestCheckerThreeTierQ4(unittest.TestCase):
    """Q4-④（评审 v4）：体检器必须是**三态**，且判据要标"知识截止日"。

    动机：原先 ❌/✅ 二态 —— "没见过"（客户端升级改名、schema 不识别）会被当成"坏"，
    假红多了就没人信体检器了。现在：❌ 只留给"确定坏"；"与基线不符/不识别"给 ⚠ 让人复核；
    并且逐家标 basis（基于哪个客户端版本、哪天实测）。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # 显式清理：不清理会在 GC 期抛 ResourceWarning（被测的 CI 严格句柄门槛会红）
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        os.makedirs(os.path.join(self.home, ".trae-cn"))
        self.cfg = [c for c in CCC.CLIENTS if c["name"] == "trae"][0]
        _isolate_managed_repos(self)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, obj):
        pathlib.Path(os.path.join(self.home, ".trae-cn", "hooks.json")).write_text(
            json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def _cmd(self):
        """注意：命令里的钩子脚本必须**真实存在**，否则体检会先报"指向的脚本不存在"（❌）——
        这条新锁本来要验的是三态分级，得先让它活到事件检查那一步。
        解释器同理（2026-09-23 L11）：写 `C:/x/python.exe` 这种占位路径会先被判 ❌，
        这条锁就永远验不到 ⚠ 分级了。"""
        os.makedirs(os.path.join(self.home, "h", "integrations", "hooks"), exist_ok=True)
        gate = os.path.join(self.home, "h", "integrations", "hooks", "agentguard-gate.py")
        if not os.path.isfile(gate):
            pathlib.Path(gate).write_text("# stub\n", encoding="utf-8")
        return "%s %s trae; exit $LASTEXITCODE" % (PY_FWD, gate.replace("\\", "/"))

    def test_every_client_has_basis(self):
        for c in CCC.CLIENTS:
            self.assertTrue((c.get("basis") or "").strip(), "%s 缺 basis（判据知识截止日）" % c["name"])

    def test_renamed_event_is_warn_not_bad(self):
        """事件集合非空但与期望不符（疑似改名）→ ⚠，不是 ❌。"""
        self._write({"hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": self._cmd()}]}]}})
        lvl, probs, _ = CCC.check_client(self.cfg, self.home)
        self.assertEqual(lvl, "warn", f"缺 Stop 事件应给 ⚠ 而不是 ❌：{probs}")
        self.assertTrue(any("缺关键事件" in p for p in probs))

    def test_unknown_schema_is_warn(self):
        """配置结构完全不识别（没有 hooks/events 表）→ ⚠ 让人复核，不是 ❌。"""
        self._write({"somethingElse": {"a": 1}})
        lvl, probs, _ = CCC.check_client(self.cfg, self.home)
        self.assertEqual(lvl, "warn", f"schema 不识别应给 ⚠：{probs}")
        self.assertTrue(any("schema 不识别" in p for p in probs))

    def test_bad_json_still_bad(self):
        """反向锁：确定坏（JSON 非法）必须仍是 ❌ —— 三态分级不许把检测改弱。"""
        pathlib.Path(os.path.join(self.home, ".trae-cn", "hooks.json")).write_text("{bad json", encoding="utf-8")
        lvl, probs, _ = CCC.check_client(self.cfg, self.home)
        self.assertEqual(lvl, "bad")
        self.assertTrue(any("JSON 非法" in p for p in probs))

    def test_host_rule_violation_still_bad(self):
        """反向锁：宿主规则命中（引号）仍是 ❌。"""
        bad = '"C:/x/python.exe" "C:/h/integrations/hooks/agentguard-gate.py" trae; exit $LASTEXITCODE'
        self._write({"hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": bad}]}],
                               "Stop": [{"hooks": [{"type": "command", "command": bad}]}]}})
        lvl, _probs, _ = CCC.check_client(self.cfg, self.home)
        self.assertEqual(lvl, "bad")

    def test_output_mentions_knowledge_baseline(self):
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            CCC.main(["--home", self.home])
        self.assertIn("知识基线", buf.getvalue(), "输出必须打印判据知识基线（客户端升级后知道先复核哪条）")

    def test_warn_does_not_fail_exit_code(self):
        """⚠ 不算失败（避免假红被当噪声）——反向锁：只有 ❌ 才让退出码为 1。"""
        self._write({"hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": self._cmd()}]}]}})
        self.assertEqual(CCC.main(["--home", self.home, "--json"]), 0, "⚠ 态不应让体检退出码变 1")

class TestProjectLevelConfigsAreChecked(unittest.TestCase):
    """**项目级**配置必须被同一套判据看见（2026-09-21 新增，锁住一个已经发生过的盲区）。

    事实经过：33 个受管仓里一度有 32 份 `<仓>/zcode.json`，全部**从来没能工作过** ——
    32/32 连 JSON 都不合法，schema 也是别家的形态（Claude/Codex 扁平式），客户端参数还写成 `codex`。
    它们之所以能烂到那天，根因是体检器只扫 `--home` 下的用户级路径：项目级**没有任何判据看过**。
    这一组锁两件事：① 那些缺陷必须报 ❌；② **不许只靠"JSON 非法"这一条** ——
    把反斜杠补成双的（JSON 就合法了）之后，仍须因为「写死本机路径」被判 ❌，
    否则"修好 JSON"就等于绕过整条锁。
    """

    # 实测原文（33 仓里 31 份逐字相同的那份，754B）：注意 `"C:\Users\..."` 是**单**反斜杠
    REAL_754 = (
        '{\n'
        '  "hooks": {\n'
        '    "PostToolUse": [\n'
        '      {\n'
        '        "matcher": "Write|Edit|MultiEdit|NotebookEdit",\n'
        '        "hooks": [\n'
        '          {\n'
        '            "type": "command",\n'
        '            "command": "\\"C:\\Users\\HP\\AppData\\Roaming\\uv\\python\\cpython-3.11-windows-x86_64-none\\python.exe\\" '
        '\\"C:\\Users\\HP\\.agentguard\\hooks\\agentguard-gate.py\\" codex PostToolUse",\n'
        '            "timeout": 200\n'
        '          }\n'
        '        ]\n'
        '      }\n'
        '    ],\n'
        '    "Stop": []\n'
        '  }\n'
        '}\n'
    )

    # JSON 里合法的转义只有这些：`\` 后面跟 `"`/`\`/`/`/bfnrt`，或 `u` + 4 位十六进制。
    # 其余单个反斜杠（`\U` `\A` `\R` `\p` `\c` `\.` `\h` `\e` …）全是非法的。
    _ILLEGAL_ESCAPE_RE = re.compile(r'\\(?!["\\/bfnrt]|u[0-9A-Fa-f]{4})')

    def _json_legal(self, text):
        """把实测原文里**非法的单反斜杠**逐处补成双的，让它成为一份合法 JSON。

        别写成 `.replace("\\\\U", ...).replace("\\\\u", ...)` 那种手列清单 —— 实测原文里
        除 `\\U`/`\\u` 外还有 `\\A`(AppData)/`\\R`/`\\p`/`\\c`/`\\.`/`\\h`/`\\e` 一大串，
        漏一个这行就自己抛 JSONDecodeError，锁先炸在证伪那一步（2026-09-21 就是这么炸的）。
        """
        return self._ILLEGAL_ESCAPE_RE.sub(lambda m: "\\\\", text)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        self.repo = os.path.join(self.tmp.name, "agentguard_pc_net-demo")
        os.makedirs(self.repo)
        _isolate_managed_repos(self)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, text, rel="zcode.json"):
        p = os.path.join(self.repo, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        pathlib.Path(p).write_text(text, encoding="utf-8", newline="")
        return p

    def _findings(self):
        return CCC.project_level_findings([self.repo], self.home)

    # ---------- 正例：没有项目级配置就什么都不报（不许假红） ----------
    def test_absent_project_config_reports_nothing(self):
        self.assertEqual(self._findings(), [])
        self.assertEqual(CCC.project_level_findings([os.path.join(self.tmp.name, "nope")], self.home), [])

    # ---------- 反例①：实测原文必须被抓 ----------
    def test_real_world_fixture_is_caught(self):
        self._write(self.REAL_754)
        f = self._findings()
        self.assertEqual(len(f), 1, f)
        self.assertEqual(f[0]["level"], "bad")
        self.assertTrue(any("JSON 非法" in p for p in f[0]["problems"]), f[0]["problems"])
        self.assertTrue(any("本机绝对路径" in p for p in f[0]["problems"]), f[0]["problems"])
        self.assertIn("agentguard_pc_net-demo/zcode.json", f[0]["config"])

    # ---------- 反例②（关键）：JSON 合法了也必须仍然被抓 ----------
    def test_json_valid_but_machine_path_still_caught(self):
        fixed = self._json_legal(self.REAL_754)
        json.loads(fixed)          # 先证明它确实合法 —— 否则这条锁自己就是假的
        self._write(fixed)
        f = self._findings()
        self.assertEqual(len(f), 1, f)
        self.assertEqual(f[0]["level"], "bad", f[0]["problems"])
        self.assertTrue(any("本机绝对路径" in p for p in f[0]["problems"]),
                        "JSON 合法后仍须因『写死本机路径』被判 ❌：%s" % f[0]["problems"])

    # ---------- 反例③：`.zcode/config.json` 这个落点也要查 ----------
    def test_dot_zcode_variant_is_checked(self):
        self._write(self.REAL_754, rel=".zcode/config.json")
        f = self._findings()
        self.assertEqual(len(f), 1, f)
        self.assertEqual(f[0]["level"], "bad")
        self.assertIn(".zcode/config.json", f[0]["config"])

    # ---------- 路径形态识别（两种都要认） ----------
    def test_machine_path_forms(self):
        got = CCC.machine_paths_in('a "C:\\Users\\x\\y.exe" b "~/h/z.py" c /usr/local/bin')
        self.assertTrue(any(p.startswith("C:") for p in got), got)
        self.assertTrue(any(p.startswith("~") for p in got), got)
        self.assertEqual(CCC.machine_paths_in("nothing here"), [])

    # ---------- 退出码：项目级 ❌ 必须让体检失败（否则锁不响） ----------
    def test_main_exit_code_reflects_project_level(self):
        real = CCC.managed_repo_paths
        CCC.managed_repo_paths = lambda: [self.repo]
        self.addCleanup(setattr, CCC, "managed_repo_paths", real)
        self.assertEqual(CCC.main(["--home", self.home, "--json"]), 0,
                         "没有项目级配置时不该失败")
        self._write(self.REAL_754)
        self.assertEqual(CCC.main(["--home", self.home, "--json"]), 1,
                         "项目级配置坏掉必须让体检退出码变 1 —— 不然第 33 个仓会再烂一次")

    # ---------- JSON 输出里项目级条目要能认出来，且不破坏顶层形状 ----------
    def test_json_output_marks_project_entries(self):
        import contextlib
        real = CCC.managed_repo_paths
        CCC.managed_repo_paths = lambda: [self.repo]
        self.addCleanup(setattr, CCC, "managed_repo_paths", real)
        self._write(self.REAL_754)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            CCC.main(["--home", self.home, "--json"])
        rows = json.loads(buf.getvalue())
        self.assertIsInstance(rows, list, "顶层保持数组，别破坏既有消费者")
        proj = [r for r in rows if r.get("host") == "project"]
        self.assertEqual(len(proj), 1, rows)
        self.assertEqual(proj[0]["level"], "bad")


class TestShippedExamplesMatchDocumentedContract(unittest.TestCase):
    """出货的示例配置必须**自己先过一遍体检器的判据**（评审 2026-09-23 M8 / M9）。

    动机：`integrations/*/*.example.json` 是全文档最容易被**整份抄走**的东西，而体检器只判
    "用户已经装上去的那份"（`--home` 下的用户级路径）—— 示例本身**没有任何判据看过**。
    于是两份示例一路带着"照抄必然失效"的硬伤躺在仓里，**本机还全绿**：
      · `trae-cn/hooks.example.json`：命令带引号（Trae 在 PowerShell 里执行，引号必崩）、
        客户端参数写成 `claude`（按别家契约输出，结论进不了模型）、缺 `; exit $LASTEXITCODE`
        （PowerShell 管道把 rc=2 吞成 1，Trae 当"非阻断错误"）；
      · `qoder-cn/settings.example.json`：`\\\"` **过度转义** —— JSON 解析出来是字面 `\"`，
        拼出的钩子路径根本不存在。
    判据复用同一套 `judge_config_file`（**不另写一份**），所以示例与真配置不会漂移。
    """

    HERE = os.path.join(ROOT, "integrations")
    # 出货示例的**名字清单**（相对 integrations/）→ 体检器里的客户端名。
    # ★ 这是"先数『这类东西有哪些名字』"那条教训的落点：新增一份示例文件而不登记，
    #   `test_no_unregistered_example_file` 会当场拦下，不会让它悄悄没人看。
    EXAMPLES = {
        "trae-cn/hooks.example.json": "trae",
        "qoder-cn/settings.example.json": "qoder",
    }
    GATE_PY = os.path.join(ROOT, "integrations", "hooks", "agentguard-gate.py").replace("\\", "/")
    PLACEHOLDERS = ("<PYTHON>", "<HARNESS_SHIM>")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def tearDown(self):
        self.tmp.cleanup()

    # ---------- 工具 ----------
    def _raw(self, rel):
        with open(os.path.join(self.HERE, rel.replace("/", os.sep)), "r",
                  encoding="utf-8", newline="") as fh:
            return fh.read()

    def _subst(self, text):
        """把占位符换成**本机一定存在**的真值。

        `<HARNESS_SHIM>` 故意换成**仓内正本**（而不是 ~/.agentguard 下的转发壳）：
        正本里没有 `GEN_PATH` / `runpy.run_path`，`_shim_target_problem` 会干净返回 None；
        换成正斜杠是因为 trae 那条命令要求路径无空格、整条命令无引号。
        """
        return text.replace("<HARNESS_SHIM>", self.GATE_PY).replace("<PYTHON>", PY.replace("\\", "/"))

    def _judge(self, text, client, name="probe.json"):
        p = os.path.join(self.tmp.name, name)
        pathlib.Path(p).write_text(text, encoding="utf-8", newline="")
        return CCC.judge_config_file(p, CCC._client(client), self.tmp.name)

    # ---------- 主锁 ----------
    def test_shipped_examples_pass_the_checker(self):
        """每份出货示例（占位符替换后）都不许判 ❌。

        允许 ⚠：trae 那份只出货 `PostToolUse`（与 README §5.6 的实测形态一致），
        而基线期望两个事件 —— 那是"待复核"档，不是"确定坏"，照抄仍然可用。
        """
        for rel, client in sorted(self.EXAMPLES.items()):
            with self.subTest(example=rel):
                self.assertTrue(os.path.isfile(os.path.join(self.HERE, rel.replace("/", os.sep))),
                                "示例文件不见了：%s（少了它这条锁就空转，故直接红）" % rel)
                level, probs, _ = self._judge(self._subst(self._raw(rel)), client)
                self.assertNotEqual(level, "bad",
                                    "%s 自己就过不了体检器，照抄必然失效：\n  - %s"
                                    % (rel, "\n  - ".join(probs)))

    def test_no_unregistered_example_file(self):
        """反向锁：`integrations/` 下不许有没登记的示例文件（"先数名字再清点"）。"""
        found = set()
        for d in sorted(os.listdir(self.HERE)):
            dd = os.path.join(self.HERE, d)
            if not os.path.isdir(dd):
                continue
            for f in sorted(os.listdir(dd)):
                if f.endswith(".example.json"):
                    found.add("%s/%s" % (d, f))
        self.assertEqual(found, set(self.EXAMPLES),
                         "示例文件清单与登记表不一致 —— 新加的必须登记进 EXAMPLES 才会被判据看住；"
                         "登记了却没有文件说明表烂了，同样要修。")

    def test_placeholders_are_not_hardcoded(self):
        """示例里那两个占位符必须在（不许被某台机器的绝对路径顶掉）。

        这条**不依赖机器**：写死维护者本机路径的文件，在维护者机器上照样能判 ✅，
        换台机器就静默失效 —— 正是最容易漏过体检器的一类。
        """
        for rel in sorted(self.EXAMPLES):
            with self.subTest(example=rel):
                raw = self._raw(rel)
                for ph in self.PLACEHOLDERS:
                    self.assertIn(ph, raw, "%s 缺占位符 %s（写死了某台机器的路径？）" % (rel, ph))

    # ---------- 证伪：这把锁必须真的会红 ----------
    def test_the_lock_is_not_vacuous(self):
        """把 M8 / M9 那三个真实历史缺陷逐个注回去 → 判据必须转 ❌。

        （正向那条锁若因为什么原因空转，这里会一起说明它为什么会红。）
        """
        good_trae = self._subst(self._raw("trae-cn/hooks.example.json"))
        good_qoder = self._subst(self._raw("qoder-cn/settings.example.json"))
        # 先确认"好的"确实是好的，否则下面的"变坏"可能只是把别的问题当成了注入结果
        self.assertNotEqual(self._judge(good_trae, "trae")[0], "bad", "基线就不干净，证伪无从谈起")

        cases = [
            # M8-①：整条命令加引号（Trae 在 PowerShell 里执行 → 引号被拼崩、钩子压根没跑起来）。
            #        注入的是**源文本**里的 `\"`（解析后 = 真引号）；直接写裸 `"` 会把 JSON 写坏，
            #        判据虽然也红，但红在"JSON 非法"上、验不到这条规则。
            ("M8 引号", good_trae.replace(self.GATE_PY, '\\"' + self.GATE_PY + '\\"'),
             "trae", "含引号"),
            # M8-②：客户端参数写成 claude（按别家契约输出，结论进不了模型）
            ("M8 client", good_trae.replace(" trae;", " claude;"), "trae", "未标 client=trae"),
            # M8-③：丢掉 `; exit $LASTEXITCODE`（PowerShell 管道把 rc=2 吞成 1，等于没拦）
            ("M8 退出码", good_trae.replace("; exit $LASTEXITCODE", ""), "trae", "exit $LASTEXITCODE"),
            # M9：`\\\"` 过度转义 —— 解析出来是字面 `\"`，拼出的钩子路径不存在
            # M9：`\\\"` 过度转义（**JSON 源文本**里的四字符 `\` `\` `\` `"` → 解析出来是字面 `\"`），
            #     拼出的钩子路径带着反斜杠 → 文件不存在。★ 必须按"源文本"注入：只写 `\"` 得到的是
            #     真引号（那是 M8 那个坑），判据会照常放行 —— 本条会当场红。
            ("M9 过度转义", good_qoder.replace(
                self.GATE_PY, ("\\" * 3 + '"') + self.GATE_PY + ("\\" * 3 + '"')),
             "qoder", "不存在"),
            # M9-②：客户端参数写成 codex（ZCode/Codex 同族的另一家，方向相同）
            ("M9 client", good_qoder.replace(" qoder PostToolUse", " codex PostToolUse"),
             "qoder", "未标 client=qoder"),
        ]
        for tag, text, client, expect in cases:
            with self.subTest(case=tag):
                level, probs, _ = self._judge(text, client, name="inj.json")
                self.assertEqual(level, "bad", "%s 注进去竟然没被判 ❌：%s" % (tag, probs))
                self.assertTrue(any(expect in p for p in probs),
                                "%s 判了 ❌ 但理由不对（期望含 %r）：%s" % (tag, expect, probs))


if __name__ == "__main__":
    unittest.main()
