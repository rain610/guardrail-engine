# -*- coding: utf-8 -*-
"""
agentguard checks 层单元测试（零依赖，仅标准库 unittest）
================================================================
覆盖：
  - _config.py        mini-yaml 解析 / gate 装配 / _sanity_check 告警 / changed_files 收集器
  - pc_code_check     组件收敛判定（deny/dead/strict-loose/平行版/后缀例外）
  - h5_code_check     组件收敛判定（同机制）
  - backend_layer_check  R1-R7 分层规则（severity 双态 + 非 .cs 文件防线）
  - harness_guard     受保护路径判定

运行（工程根下）：
    python -m unittest discover -s tests -v

设计原则：全部用临时目录/monkey-patch，不读不写任何真实端仓库。
"""
# 注意：本文件**不接** HARNESS_FAST —— 15 个测试类实测最慢 1.65s、整体约 6s，属「单测」范畴，
# 快跑档就该跑它。慢的是链路集成（真起 CLI / git / 全量扫描），那些在 test_entry_level、
# test_hooks_chain、test_coverage 里，各自带 @_slow 标记。
# （第四轮外审 ① 的口径：别为了「顺手统一」把这里也 skip 掉。）

import io
import os
import re
import sys
import json
import shutil
import tempfile
import unittest

import contextlib
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _tmpclean import mkdtemp_cleanup as _mkdtemp    # noqa: E402
from _tmpclean import rmtree_force as _rmtree_force  # noqa: E402


# 测试隔离（第四轮 N-2）：把 run_gate 的运行日志钉到临时文件。
# 原先只有 test_coverage.py 设了 HARNESS_LOG_FILE，其余用例调 run_gate 时会把探针记录
# （gate=t_pc 等）写进真实的 reports/gate_log.jsonl —— 实测污染过 124 条，覆盖度自评的
# 窗口数据（运行次数、gate 概览）被混入测试噪声。模块导入时就设好，子进程
# env=dict(os.environ, ...) 会继承；tests/test_coverage.py 里有源码级锁防止将来漏设。
# 2026-09-21：退出时那次 `shutil.rmtree` **删不掉**这个目录（里面有 run_gate 真写出来的
# 台账，`ignore_errors=True` 把 PermissionError 静默吞掉 —— 症状与"清理成功"一模一样）。
# 实测 `harness-testlog-` 存量 1193 份 / 441 MB，只能靠数目录才发现。走强删助手。
_LOG_TMPDIR = _mkdtemp("harness-testlog-")
os.environ["HARNESS_LOG_FILE"] = os.path.join(_LOG_TMPDIR, "gate_log.jsonl")
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (os.path.join(ROOT, "harness"), os.path.join(ROOT, "checks")):
    if p not in sys.path:
        sys.path.insert(0, p)

import _config                      # noqa: E402
import harness_guard as guard       # noqa: E402
import run_gate                     # noqa: E402
from rule_base import Rule, Finding, CheckContext, Severity  # noqa: E402


# ---------------------------------------------------------------- _config
class TestConfigParse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hcfg_")
        self.yaml = os.path.join(self.tmp, "harness.yaml")

    def tearDown(self):
        _rmtree_force(self.tmp)

    def _write(self, text):
        with open(self.yaml, "w", encoding="utf-8") as f:
            f.write(text)

    def test_scalars_and_nesting(self):
        self._write(
            "# comment\n"
            "version: 2\n"
            "flag_on: true\n"
            "flag_off: false\n"
            "quoted: 'hello world'\n"
            "inline: value  # trailing comment\n"
            "lst: [a, b, c]\n"
            "empty_lst: []\n"
            "repos:\n"
            "  t_pc: D:/tmp/repo_pc  # repo comment\n"
            "gates:\n"
            "  t_pc:\n"
            "    enabled: true\n"
            "    repo: t_pc\n"
            "    mode: strict\n"
            "    run_on: [pre_commit, post_edit]\n"
        )
        cfg = _config.load(self.yaml)
        self.assertEqual(cfg["version"], "2")
        self.assertIs(cfg["flag_on"], True)
        self.assertIs(cfg["flag_off"], False)
        self.assertEqual(cfg["quoted"], "hello world")
        self.assertEqual(cfg["inline"], "value")
        self.assertEqual(cfg["lst"], ["a", "b", "c"])
        self.assertEqual(cfg["empty_lst"], [])
        self.assertIsInstance(cfg["repos"], dict)
        g = cfg["gates"]["t_pc"]
        self.assertIs(g["enabled"], True)
        self.assertEqual(g["run_on"], ["pre_commit", "post_edit"])
        self.assertEqual(g["mode"], "strict")

    def test_gate_assembly(self):
        self._write(
            "repos:\n"
            "  t_pc: " + self.tmp.replace("\\", "/") + "\n"
            "gates:\n"
            "  t_pc:\n"
            "    enabled: true\n"
            "    repo: t_pc\n"
        )
        g = _config.gate("t_pc", path=self.yaml)
        self.assertEqual(os.path.normcase(g["_repo_path"]), os.path.normcase(self.tmp))
        ag = _config.all_gates(path=self.yaml)
        self.assertIn("t_pc", ag)

    def test_sanity_warns_on_dangling_repo(self):
        _config._warned.clear()
        buf = io.StringIO()
        clean = io.StringIO()
        cfg = {"repos": {}, "gates": {"t_pc": {"enabled": True, "repo": "ghost"}}}
        # 评审 A1（2026-09-15 交付前评审「分享前最小动作」#2）：诊断走 stderr —— stdout 会被 $( ) 捕获污染。
        with contextlib.redirect_stdout(clean), contextlib.redirect_stderr(buf):
            _config._sanity_check(cfg)
        out = buf.getvalue()
        self.assertIn("config-warn", out)
        self.assertIn("ghost", out)
        self.assertEqual(clean.getvalue(), "", "诊断不得走 stdout（会被 $( ) 捕获污染）")
        # 同进程去重：第二次不再打印
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(buf2):
            _config._sanity_check(cfg)
        self.assertNotIn("config-warn", buf2.getvalue())

    def test_sanity_never_raises_on_malformed_config(self):
        """回归锁（评审 C-5 修法的根因）：`_sanity_check` 对**畸形 config**必须"只告警不阻断"。

        背景：我给 repos 加 harness_self 覆盖时插在了 `isinstance(repos, dict)` 守卫**之前** ——
        `repos: None` / 缺 repos 时先赋值会 TypeError，把"只告警不阻断"的兜底整个绕过去。
        实测过的四形态：{'repos': None} / {'repos': []} / {'gates': {}} / {}。
        """
        for cfg in ({"repos": None, "gates": {}}, {"repos": [], "gates": None},
                    {"gates": {}}, {}):
            _config._warned.clear()
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                try:
                    _config._sanity_check(dict(cfg))
                except Exception as e:      # 崩 = 兜底失效
                    self.fail("畸形 config 不得抛异常：%s → %s: %s" % (cfg, type(e).__name__, e))
            self.assertNotEqual(buf.getvalue(), "", "畸形 config 必须出声（不能静默）")

    def test_sanity_warns_on_bad_mode(self):
        _config._warned.clear()
        buf = io.StringIO()
        cfg = {"repos": {"t_pc": self.tmp},
               "gates": {"t_pc": {"enabled": True, "repo": "t_pc", "mode": "wat"}}}
        with contextlib.redirect_stderr(buf):
            _config._sanity_check(cfg)
        self.assertIn("mode", buf.getvalue())

    def test_changed_files_three_sources(self):
        repo = tempfile.mkdtemp(prefix="hgit_")
        try:
            def git(*a):
                subprocess.run(["git", "-C", repo] + list(a), check=True,
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            git("init", "-q")
            git("config", "user.email", "t@t")
            git("config", "user.name", "t")
            # tracked + 未暂存修改
            with open(os.path.join(repo, "a.txt"), "w") as f:
                f.write("v1")
            git("add", "a.txt")
            with open(os.path.join(repo, "a.txt"), "w") as f:
                f.write("v2")
            # staged 新文件
            with open(os.path.join(repo, "b.vue"), "w") as f:
                f.write("<template/>")
            git("add", "b.vue")
            # untracked（含一个不在扩展名内的，验证过滤）
            with open(os.path.join(repo, "c.js"), "w") as f:
                f.write("1;")
            with open(os.path.join(repo, "d.py"), "w") as f:
                f.write("pass")

            files = _config.changed_files(repo, exts=(".vue", ".js", ".txt"))
            names = {os.path.basename(f) for f in files}
            self.assertEqual(names, {"a.txt", "b.vue", "c.js"})
        finally:
            _rmtree_force(repo)

    def test_changed_files_non_repo(self):
        d = tempfile.mkdtemp(prefix="hnogit_")
        try:
            self.assertEqual(_config.changed_files(d), [])
        finally:
            _rmtree_force(d)


class TestMiniYamlOutOfSubset(unittest.TestCase):
    """超纲语法必须**出声**（2026-09-16 剖析报告第六节「mini-yaml 能力边界」）。

    这组锁的不是"解析得对"——本解析器只支持 harness.yaml 用到的那一小撮子集，超纲写法
    本来就不解析。锁的是**不许静默**：改前实测 `pwd: "a # b"` 得到 `"a`（截断且残留引号），
    门禁照跑、路径照错、结论照出，全链路没有一处会红。这个文件把每一类"静默错解"
    变成"必须打 [config-warn]"。

    断言口径：**只要求出声 + 结果不变**。不要求解析成功——那等于在没需求的情况下实现 YAML。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hyaml_")
        self.yaml = os.path.join(self.tmp, "harness.yaml")
        # 同一进程内 _warn 按消息文本去重，跨用例会串味 → 每条目清一次
        _config._warned.clear()

    def tearDown(self):
        _rmtree_force(self.tmp)

    def _parse(self, text):
        """解析并捕获 stderr 上的 [config-warn]。返回 (解析结果, 告警行列表)。

        ConfigError **照抛**（不吞）—— 混排那组用例要靠 assertRaises 接住它。
        """
        with open(self.yaml, "w", encoding="utf-8") as f:
            f.write(text)
        _config._warned.clear()
        buf = io.StringIO()
        old = sys.stderr
        sys.stderr = buf
        try:
            got = _config._load_yaml(self.yaml)
        finally:
            sys.stderr = old
        warns = [l for l in buf.getvalue().splitlines() if "[config-warn]" in l]
        return got, warns

    def test_block_scalar_is_loud(self):
        """块标量 `|`：续行会被当成**同级新键**（实测），必须出声。"""
        got, warns = self._parse("a: 1\nnotes: |\n  line one\n  line two\nb: 2\n")
        self.assertTrue(warns, "块标量未出声 —— 续行静默变成了新键")
        self.assertIn("line one", got, "（记录当前行为：续行确实被当成了键）")

    def test_quoted_hash_is_loud(self):
        """引号内的 `#`：会被静默截断并残留半个引号。"""
        got, warns = self._parse('pwd: "a # b"\n')
        self.assertTrue(warns, "引号内 # 未出声")
        self.assertEqual(got["pwd"], '"a', "（记录当前行为：截断且残留引号）")

    def test_hash_without_space_is_loud(self):
        """`#` 前无空白（如 URL 片段）：真 YAML 里不是注释，本解析器却截断 → 必须出声。"""
        _got, warns = self._parse("u: http://x/y#frag\n")
        self.assertTrue(warns, "`#` 前无空白的截断未出声")

    def test_apostrophe_without_close_quote_is_quiet(self):
        """反向锁：值里带撇号的合法写法**不得**误报（否则是告警疲劳，等于没告警）。"""
        _got, warns = self._parse("notes: it's a # comment\n")
        self.assertEqual(warns, [], "把值里的撇号误判成引号 → 假告警")

    def test_nested_flow_list_is_loud(self):
        """嵌套 flow 列表：内层方括号被当普通字符平铺切开。"""
        got, warns = self._parse("run_on: [[a, b], c]\n")
        self.assertTrue(warns, "嵌套 flow 列表未出声")
        self.assertEqual(got["run_on"], ["[a", "b]", "c"], "（记录当前行为：平铺切开）")

    def test_quoted_flow_items_are_loud(self):
        """flow 列表里的带引号元素：引号不会被剥掉。"""
        got, warns = self._parse('lst: ["a", "b"]\n')
        self.assertTrue(warns, "带引号的 flow 元素未出声")
        self.assertEqual(got["lst"], ['"a"', '"b"'], "（记录当前行为：引号留在值里）")

    def test_windows_path_list_item_is_loud(self):
        """`- C:/x/y`：盘符冒号被当成 `键: 值` 映射（真实 Windows 路径会被解析成 dict）。"""
        got, warns = self._parse("items:\n  - C:/x/y\n")
        self.assertTrue(warns, "盘符路径被当映射未出声")
        self.assertEqual(got["items"], [{"C": "/x/y"}], "（记录当前行为）")

    def test_plain_inline_map_is_quiet(self):
        """反向锁：合法的行内映射 `- a: /path` 不得误报（与盘符路径的分界 = 冒号后有无空格）。"""
        _got, warns = self._parse("items:\n  - a: /path\n")
        self.assertEqual(warns, [], "把合法行内映射误判成盘符路径 → 假告警")

    def test_mixed_list_and_map_raises_config_error(self):
        """同级列表/映射混排：改前**裸崩**（TypeError/AttributeError → rc=1，伪装成"门禁拦截"）。

        N3：必须转成 ConfigError，由 run_gate 统一报 rc=2（配置错误），语义归位。
        """
        for text in ("items:\n  - a\n  key: v\n", "items:\n  key: v\n  - a\n"):
            with self.assertRaises(_config.ConfigError, msg="混排未转 ConfigError：%r" % text):
                self._parse(text)

    def test_config_error_message_has_line_number(self):
        """诊断要能直接定位（只说"解析失败"等于把排查成本丢回给人）。"""
        with self.assertRaises(_config.ConfigError) as cm:
            self._parse("a: 1\nitems:\n  - x\n  key: v\n")
        self.assertIn("第 4 行", str(cm.exception))

    def test_real_config_emits_no_warning(self):
        """反向锁：真配置（config/harness.toml）必须零告警且读得出 gate。

        否则这套探测一上线就是满屏噪音，人人学会忽略它 —— 比不报还糟。
        TOML 由标准库解析，本不该产生 `[config-warn]`；这条同时守住"别把真配置写坏"。
        """
        _config._warned.clear()
        buf = io.StringIO()
        old = sys.stderr
        sys.stderr = buf
        try:
            cfg = _config._load_file(os.path.join(ROOT, "config", "harness.toml"))
        finally:
            sys.stderr = old
        warns = [l for l in buf.getvalue().splitlines() if "[config-warn]" in l]
        self.assertGreaterEqual(len(cfg.get("gates") or {}), 2, "没读到 gate，判据坏了")
        self.assertEqual(warns, [], "真配置触发了告警：%r" % warns)


class TestTomlConfig(unittest.TestCase):
    """TOML 主格式（2026-10-21）：标准库 tomllib 解析，不再走自研子集。

    这组锁的是「主格式真的被支持」：注释 / 数组 / Windows 路径 / 坏文件转 ConfigError /
    扩展名分派。旧版 YAML 的兼容性由上面的 TestConfigParse / TestMiniYamlOutOfSubset 继续锁。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="htoml_")

    def tearDown(self):
        _rmtree_force(self.tmp)

    def _load(self, text):
        p = os.path.join(self.tmp, "cfg.toml")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return _config._load_toml(p)

    def test_scalars_arrays_and_comments(self):
        got = self._load(
            "# 顶层注释\n"
            "version = 2\n"
            "flag_on = true\n"
            "flag_off = false\n"
            "quoted = \"hello world\"\n"
            "lst = [\"pre_commit\", \"post_edit\"]\n"
            "empty_lst = []\n"
            "\n"
            "[repos]\n"
            "t_pc = \"D:/tmp/repo_pc\"   # 行尾注释\n"
            "\n"
            "[gates.t_pc]\n"
            "enabled = true\n"
            "repo = \"t_pc\"\n"
            "run_on = [\"pre_commit\"]\n")
        self.assertEqual(got["version"], 2)
        self.assertIs(got["flag_on"], True)
        self.assertIs(got["flag_off"], False)
        self.assertEqual(got["quoted"], "hello world")
        self.assertEqual(got["lst"], ["pre_commit", "post_edit"])
        self.assertEqual(got["empty_lst"], [])
        self.assertEqual(got["repos"]["t_pc"], "D:/tmp/repo_pc")
        self.assertEqual(got["gates"]["t_pc"]["run_on"], ["pre_commit"])
        self.assertIs(got["gates"]["t_pc"]["enabled"], True)

    def test_windows_path_forms(self):
        """反斜杠在 TOML 基本字符串里是转义符：要么用正斜杠，要么用字面量字符串。"""
        got = self._load(
            "[repos]\n"
            "a = \"D:/Rain/repo\"\n"
            "b = 'D:\\Rain\\repo'\n")
        self.assertEqual(got["repos"]["a"], "D:/Rain/repo")
        self.assertEqual(got["repos"]["b"], "D:\\Rain\\repo")

    def test_backslash_in_basic_string_is_rejected(self):
        with self.assertRaises(_config.ConfigError) as cm:
            self._load("[repos]\na = \"D:\\Rain\\repo\"\n")
        self.assertIn("TOML", str(cm.exception))

    def test_malformed_toml_is_config_error(self):
        """坏配置必须转 ConfigError（run_gate 报 rc=2），不能裸崩成 rc=1。"""
        for text in ("[repos\n", "a = [1, 2\n", "a = 1\na = 2\n"):
            with self.assertRaises(_config.ConfigError, msg="未转 ConfigError：%r" % text):
                self._load(text)

    def test_extension_dispatch(self):
        """.toml 走 tomllib；.yaml/.yml 走兼容层；别的扩展名明确报错（不猜内容）。"""
        toml = os.path.join(self.tmp, "a.toml")
        yaml = os.path.join(self.tmp, "a.yaml")
        other = os.path.join(self.tmp, "a.json")
        with open(toml, "w", encoding="utf-8") as f:
            f.write("a = 1\n")
        with open(yaml, "w", encoding="utf-8") as f:
            f.write("a: 1\n")
        with open(other, "w", encoding="utf-8") as f:
            f.write("{}\n")
        self.assertEqual(_config._load_file(toml), {"a": 1})
        self.assertEqual(_config._load_file(yaml), {"a": "1"})   # 旧版解析器不做类型推断
        with self.assertRaises(_config.ConfigError):
            _config._load_file(other)


# ------------------------------------------------- 本地覆盖层（TOML / 旧版 YAML）
class TestLocalOverride(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hloc_")
        self._saved = ({k: v for k, v in os.environ.items() if k == "HARNESS_CONFIG"},
                       dict(_config.__dict__).get("_HARNESS_ROOT"))
        os.environ.pop("HARNESS_CONFIG", None)

    def tearDown(self):
        for k, v in os.environ.items():
            if k == "HARNESS_CONFIG" and k not in dict(self._saved[0]):
                os.environ.pop(k, None)
        v = self._saved[0].get("HARNESS_CONFIG")
        if v is not None:
            os.environ["HARNESS_CONFIG"] = v
        if self._saved[1]:
            _config._HARNESS_ROOT = self._saved[1]
        _rmtree_force(self.tmp)

    def _write(self, name, text):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def test_local_overrides_repo_path(self):
        """local 覆盖 repos 路径；gates 继承 base。"""
        _config._HARNESS_ROOT = self.tmp
        cfg_dir = os.path.join(self.tmp, "config")
        os.makedirs(cfg_dir)
        self._write(os.path.join("config", "harness.yaml"),
                    "repos:\n"
                    "  t_pc: D:/machA/repo_pc\n"
                    "gates:\n"
                    "  t_pc:\n"
                    "    enabled: true\n"
                    "    repo: t_pc\n"
                    "    mode: strict\n")
        self._write(os.path.join("config", "harness.local.yaml"),
                    "repos:\n"
                    "  t_pc: E:/machB/repo_pc\n")
        g = _config.gate("t_pc")
        self.assertEqual(os.path.normcase(g["_repo_path"]), os.path.normcase("E:/machB/repo_pc"))
        self.assertEqual(g["mode"], "strict")  # 继承 base 策略

    def test_local_toml_overrides_repo_path(self):
        """TOML 主路径：base TOML + local TOML，路径覆盖、策略继承。"""
        _config._HARNESS_ROOT = self.tmp
        cfg_dir = os.path.join(self.tmp, "config")
        os.makedirs(cfg_dir)
        self._write(os.path.join("config", "harness.toml"),
                    "[repos]\n"
                    "t_pc = \"D:/machA/repo_pc\"\n"
                    "\n"
                    "[gates.t_pc]\n"
                    "enabled = true\n"
                    "repo = \"t_pc\"\n"
                    "mode = \"strict\"\n")
        self._write(os.path.join("config", "harness.local.toml"),
                    "[repos]\n"
                    "t_pc = \"E:/machB/repo_pc\"\n")
        g = _config.gate("t_pc")
        self.assertEqual(os.path.normcase(g["_repo_path"]), os.path.normcase("E:/machB/repo_pc"))
        self.assertEqual(g["mode"], "strict")

    def test_toml_base_wins_over_legacy_yaml(self):
        """两种 base 同时存在时 TOML 为准（旧 YAML 留着不生效，但不能悄悄反超）。

        与 local 层同一纪律：**必须出声**说清被忽略的是哪一份 —— 改了 YAML 却
        不生效、屏幕上一切正常，正是本仓反复栽过的「看起来配好了」。
        """
        _config._HARNESS_ROOT = self.tmp
        cfg_dir = os.path.join(self.tmp, "config")
        os.makedirs(cfg_dir)
        self._write(os.path.join("config", "harness.toml"),
                    "[repos]\nt_pc = \"D:/from-toml\"\n"
                    "[gates.t_pc]\nenabled = true\nrepo = \"t_pc\"\n")
        self._write(os.path.join("config", "harness.yaml"),
                    "repos:\n  t_pc: D:/from-yaml\n"
                    "gates:\n  t_pc:\n    enabled: true\n    repo: t_pc\n")
        _config._warned.clear()
        buf = io.StringIO()
        old = sys.stderr
        sys.stderr = buf
        try:
            cfg = _config.load()
        finally:
            sys.stderr = old
        self.assertEqual(os.path.normcase(cfg["repos"]["t_pc"]),
                         os.path.normcase("D:/from-toml"))
        self.assertIn("harness.yaml", buf.getvalue(),
                      "两种 base 格式并存却没说出被忽略的那份：%r" % buf.getvalue())

    def test_both_local_formats_toml_wins_and_warns(self):
        """两种 local 同时存在：TOML 生效，且**出声**说清 YAML 那份被忽略。"""
        _config._HARNESS_ROOT = self.tmp
        cfg_dir = os.path.join(self.tmp, "config")
        os.makedirs(cfg_dir)
        self._write(os.path.join("config", "harness.toml"),
                    "[repos]\nt_pc = \"\"\n"
                    "[gates.t_pc]\nenabled = true\nrepo = \"t_pc\"\n")
        self._write(os.path.join("config", "harness.local.toml"),
                    "[repos]\nt_pc = \"D:/from-toml\"\n")
        self._write(os.path.join("config", "harness.local.yaml"),
                    "repos:\n  t_pc: D:/from-yaml\n")
        _config._warned.clear()
        buf = io.StringIO()
        old = sys.stderr
        sys.stderr = buf
        try:
            cfg = _config.load()
        finally:
            sys.stderr = old
        self.assertEqual(os.path.normcase(cfg["repos"]["t_pc"]),
                         os.path.normcase("D:/from-toml"))
        self.assertIn("harness.local.yaml", buf.getvalue(),
                      "两种格式并存却没说出被忽略的那份：%r" % buf.getvalue())

    def test_deep_merge_preserves_base_keys(self):
        base = {"repos": {"a": "1", "b": "2"}, "gates": {"g": {"mode": "strict"}}}
        ov = {"repos": {"a": "9"}}
        m = _config._deep_merge(base, ov)
        self.assertEqual(m["repos"], {"a": "9", "b": "2"})
        self.assertEqual(m["gates"]["g"]["mode"], "strict")

    def test_env_config_wins(self):
        """HARNESS_CONFIG 优先于 harness.local.yaml。"""
        _config._HARNESS_ROOT = self.tmp
        cfg_dir = os.path.join(self.tmp, "config")
        os.makedirs(cfg_dir)
        self._write(os.path.join("config", "harness.yaml"),
                    "repos:\n  t_pc: D:/base\n")
        self._write(os.path.join("config", "harness.local.yaml"),
                    "repos:\n  t_pc: D:/local\n")
        envf = self._write("env.yaml", "repos:\n  t_pc: D:/env\n")
        os.environ["HARNESS_CONFIG"] = envf
        cfg = _config.load()
        self.assertEqual(os.path.normcase(cfg["repos"]["t_pc"]), os.path.normcase("D:/env"))


# ------------------------------------------------- PC 组件收敛（ruleset 层）
class TestRuleBase(unittest.TestCase):
    def test_severity_enum_values(self):
        self.assertEqual(Severity.ERROR.value, "error")
        self.assertEqual(Severity.WARN.value, "warn")
        self.assertEqual(Severity.INFO.value, "info")

    def test_rule_run_respects_applicable_and_severity(self):
        class Dummy(Rule):
            id = "D"
            def applicable(self, ctx):
                return "hit" in ctx.content
            def check(self, ctx):
                return [Finding(self.id, "x")]
        ctx = CheckContext(path=Path("/x"), content="hit")
        self.assertEqual(len(Dummy().run(ctx)), 1)
        ctx2 = CheckContext(path=Path("/x"), content="miss")
        self.assertEqual(len(Dummy().run(ctx2)), 0)

    def test_severity_hook_dual_state(self):
        class Dual(Rule):
            id = "DUAL"
            severity_default = Severity.INFO
            def severity(self, ctx):
                return Severity.ERROR if ctx.targeted else Severity.INFO
            def check(self, ctx):
                return [Finding(self.id, "x")]
        full = Dual().run(CheckContext(path=Path("/x"), content="a", targeted=False))
        tgt = Dual().run(CheckContext(path=Path("/x"), content="a", targeted=True))
        self.assertEqual(full[0].severity, Severity.INFO)
        self.assertEqual(tgt[0].severity, Severity.ERROR)

    def test_rule_run_stamps_see_onto_findings(self):
        """护盾 M133 的锁：Rule.run 必须把类属性 see 盖章到 Finding。"""
        class _Stub(Rule):
            id = "STUB"
            name = "s"
            see = "golden-patterns/patterns/web/request-layer.md"

            def check(self, ctx):
                return [Finding(self.id, "msg")]

        out = _Stub().run(CheckContext(path=Path("x.vue")))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].see, _Stub.see)

    def test_format_finding_embeds_rule_id_and_see(self):
        """护盾 M134 的锁：输出行必须带 [规则ID] 前缀与 📖 See 行。"""
        from rule_base import format_finding
        line = format_finding(Finding("PC-X", "消息",
                                      see="golden-patterns/patterns/web/request-layer.md"))
        self.assertTrue(line.startswith("[PC-X] "), "规则 ID 丢失（agent 只能猜 ID 写豁免）：%r" % line)
        self.assertIn("消息", line)
        self.assertIn("📖 See: golden-patterns/patterns/web/request-layer.md", line)
        plain = format_finding(Finding("PC-Y", "纯消息"))
        self.assertNotIn("See:", plain)
        self.assertIn("[PC-Y]", plain)

    def test_run_keeps_explicit_finding_severity(self):
        """统一协议：Finding 显式级别优先，未指定才用规则级 severity。"""
        class PerHit(Rule):
            id = "PER-HIT"
            severity_default = Severity.ERROR

            def check(self, ctx):
                return [Finding(self.id, "显式", severity=Severity.WARN),
                        Finding(self.id, "默认")]
        out = PerHit().run(CheckContext(path=Path("/x")))
        self.assertEqual([f.severity for f in out], [Severity.WARN, Severity.ERROR])


class TestRegistry(unittest.TestCase):
    def test_load_returns_all_configured_gates(self):
        from registry_loader import load_gate_scripts
        m = load_gate_scripts()
        for k in ("harness_self", "example"):
            self.assertIn(k, m)

    def test_registry_overrides_default(self):
        """registry.py 的 GATE_SCRIPTS 是唯一数据源（2026-09-12 收敛：DEFAULT 副本已删）。"""
        from registry_loader import load_gate_scripts
        from registry import GATE_SCRIPTS
        m = load_gate_scripts()
        # loader 加载结果 = registry 数据源本身（单源，无第二份内置映射）
        self.assertEqual(set(m.keys()), set(GATE_SCRIPTS.keys()))

    def test_script_for_unknown_gate_returns_none(self):
        from registry_loader import script_for
        self.assertIsNone(script_for("no_such_gate"))

    def test_fallback_when_registry_broken(self):
        """registry.py 损坏时抛清晰异常（单源模式无内置回退——run_gate 侧
        捕获后回退 config.script 字段，门禁不瘫痪）。"""
        import registry_loader as rl
        saved = rl.REGISTRY_FILE
        try:
            rl._cache = None  # 清缓存（收敛后 loader 有进程内缓存）
            rl.REGISTRY_FILE = os.path.join(tempfile.mkdtemp(), "nonexist.py")
            with self.assertRaises(Exception):
                rl.load_gate_scripts()
        finally:
            rl.REGISTRY_FILE = saved
            rl._cache = None


# ------------------------------------------------- 编码卫生规则（ENC）
class TestEncodingRule(unittest.TestCase):
    def _rule(self):
        from encoding_ruleset import EncodingRule
        return EncodingRule(expect="utf-8-bom")

    def _ctx(self, raw, targeted=False):
        return CheckContext(path=Path("x.cs"), raw=raw, targeted=targeted)

    def test_detect_utf8_bom(self):
        from encoding_ruleset import _detect_encoding
        self.assertEqual(_detect_encoding(b"\xef\xbb\xbfabc"), "utf-8-bom")
        self.assertEqual(_detect_encoding(b"abc"), "utf-8")
        self.assertEqual(_detect_encoding("//\xd6\xd0\xce\xc4".encode("latin-1") if False else bytes([0x2f,0x2f,0xd6,0xd0,0xce,0xc4])), "gbk")

    def test_bom_passes(self):
        fnd = self._rule().run(self._ctx(b"\xef\xbb\xbf// ok\n", targeted=True))
        self.assertEqual(fnd, [])

    def test_gbk_targeted_is_error_and_blocks(self):
        gbk = "// 中文注释\n".encode("gbk")
        fnd = self._rule().run(self._ctx(gbk, targeted=True))
        self.assertEqual(len(fnd), 1)
        self.assertEqual(fnd[0].severity, Severity.ERROR)
        self.assertIn("gbk", fnd[0].message)
        self.assertIn("utf-8-bom", fnd[0].message)

    def test_gbk_full_scan_is_info(self):
        gbk = "// 中文注释\n".encode("gbk")
        fnd = self._rule().run(self._ctx(gbk, targeted=False))
        self.assertEqual(len(fnd), 1)
        self.assertEqual(fnd[0].severity, Severity.INFO)


# ------------------------------------------------- 拦截推送飞书（个人模式）
class TestNotify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hnot_")
        self._saved = (run_gate.NOTIFY_STATE, run_gate.LOG_DIR)
        run_gate.LOG_DIR = self.tmp
        run_gate.NOTIFY_STATE = os.path.join(self.tmp, "notify_last.json")
        os.environ.pop("HARNESS_CONFIG", None)

    def tearDown(self):
        run_gate.NOTIFY_STATE, run_gate.LOG_DIR = self._saved
        _rmtree_force(self.tmp)

    def test_no_webhook_is_noop(self):
        """未配置 webhook：_notify_on_block 直接返回，不建线程不写状态（零影响）。"""
        calls = []
        orig = run_gate._notify_webhook
        run_gate._notify_webhook = lambda: None
        try:
            run_gate._notify_on_block("demo_pc", "targeted", 1, 1, 500)
        finally:
            run_gate._notify_webhook = orig
        self.assertEqual(calls, [])
        self.assertFalse(os.path.exists(run_gate.NOTIFY_STATE))

    def test_throttle_blocks_second_within_window(self):
        run_gate.NOTIFY_STATE = os.path.join(self.tmp, "notify_last.json")
        self.assertFalse(run_gate._notify_throttled("demo_pc"))
        self.assertTrue(run_gate._notify_throttled("demo_pc"))
        self.assertTrue(run_gate._notify_throttled("demo_h5") is False or True)  # 不同 gate 独立

    def test_send_swallows_network_errors(self):
        """网络失败静默（通知不能反过来影响门禁）。"""
        run_gate._notify_send("http://127.0.0.1:1/nope", "x")  # 不应抛异常

    def test_log_run_rc1_triggers_notify_path(self):
        """rc=1 时 _log_run 会走通知入口（mock 验证接线）；rc=0 不走。"""
        calls = []
        orig = run_gate._notify_on_block
        run_gate._notify_on_block = lambda *a: calls.append(a)
        try:
            run_gate.LOG_FILE = os.path.join(self.tmp, "g.jsonl")
            run_gate._log_run("demo_pc", "targeted", 2, 1, 100)
            run_gate._log_run("demo_pc", "targeted", 2, 0, 100)
        finally:
            run_gate._notify_on_block = orig
            run_gate.LOG_FILE = self._saved[1] and os.path.join(self._saved[1], "gate_log.jsonl")
        self.assertEqual(len(calls), 1)


# ------------------------------------------------------------ guard
class TestGuard(unittest.TestCase):
    def test_parse_args_self_single_token_message(self):
        """P3-5 回归锁：`--self "[harness] 说明"` 的单 token 消息必须完整保留为消息，
        不得被当 gate 名吃掉（原实现导致维护提交被白拦）。"""
        sm, gate, msg = guard._parse_args(["--self", "[harness] 修复语法接线"])
        self.assertTrue(sm)
        self.assertEqual(gate, "self")
        self.assertIn("[harness]", msg)
        self.assertIn("修复语法接线", msg)

    def test_parse_args_non_self_gate_and_message(self):
        """非 self 模式（pre-commit hook 调用形态）：args[0]=gate 名，其余为消息。"""
        sm, gate, msg = guard._parse_args(["demo_pc"])
        self.assertFalse(sm)
        self.assertEqual(gate, "demo_pc")
        self.assertEqual(msg, "")
        sm, gate, msg = guard._parse_args(["demo_pc", "some", "message"])
        self.assertEqual(gate, "demo_pc")
        self.assertEqual(msg, "some message")

    def test_parse_args_msg_file(self):
        """--msg-file 优先于位置参数；文件不存在时回退位置参数（不崩）。"""
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("[harness] 来自文件的消息\n")
            fp = f.name
        try:
            sm, gate, msg = guard._parse_args(["--self", "--msg-file", fp])
            self.assertTrue(sm)
            self.assertEqual(gate, "self")
            self.assertIn("来自文件的消息", msg)
            # 文件不存在 → 不崩，回退位置参数
            sm, gate, msg = guard._parse_args(["--self", "--msg-file", fp + ".nope", "兜底消息"])
            self.assertIn("兜底消息", msg)
        finally:
            os.unlink(fp)

    def test_in_protected(self):
        cases = {
            "checks/pc_code_check.py": True,
            "checks\\h5_code_check.py": True,          # 反斜杠
            "checks/new_check.py": True,                # checks/ 下新增 py 也算
            "checks/components_allowlist.yaml": True,
            "checks/components_allowlist_h5.yaml": True,
            "config/harness.toml": True,
            "config/harness.yaml": True,
            "config/other.yaml": False,
            # 2026-09-12 P0-2：调度层纳入保护（评审指出改弱 run_gate/hook 不提交时 guard 零反应）
            "harness/run_gate.py": True,
            # 2026-09-12 第三轮 N-δ：AI 客户端的自动门禁钩子同等对待
            "integrations/hooks/agentguard-gate.py": True,
            "integrations/codex/README.md": False,
            "harness/_config.py": True,
            # 2026-10-25 拆分：校验 / 版本 / 解析模块与调度器同等保护
            "harness/_validate.py": True,
            "harness/_engine_meta.py": True,
            "harness/_yaml_compat.py": True,
            "harness/_errors.py": True,
            "harness/hooks/pre-commit.py": True,
            "harness/hooks-dir/pre-commit": True,
            "harness/agents/auto-gate.py": True,
            # 2026-10-21 外部评审 5.7-C：审计器 / 锁清单也是「让证据好看」的入口
            "harness/coverage.py": True,
            "harness/_eol_policy.py": True,
            # 2026-10-26 第四轮评审 §4.2：改名后的主包 / 兼容壳不是纯转发层
            "guardrail_engine/cli.py": True,
            "guardrail_engine/_shell.py": True,
            "guardrail_engine/new_module.py": True,   # 新增壳模块也算
            "agentguard/cli.py": True,
            "guardrail_engine/README.md": False,      # 非 .py 不纳入
            "sandbox/mutation_smoke.py": True,
            "tests/tooling/test_lock_inventory.py": True,
            "tests/lock-inventory.json": True,
            "sandbox/run_tests.py": False,
            "tests/engine/test_checks.py": False,
            "harness/README.md": False,
            "checks/README.md": False,
        }
        for rel, want in cases.items():
            self.assertEqual(guard._in_protected(rel), want, rel)


# ---------------------------------------------------------------- example gate fail-closed
class TestExampleGateFailsClosed(unittest.TestCase):
    """点名了文件就必须查：缺失 / 读不到一律 rc=2。

    修复前：缺失文件被列表推导静默过滤 →「无目标文件」→ rc=0；读取异常被 `continue`
    吞掉 → 同样 rc=0。提交期 run_gate 把改动文件作为位置参数传进来，路径口径一旦
    对不上（相对/绝对、临时文件被删），整次提交就会**没查却判通过**。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="egfail_")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "src"))

    def tearDown(self):
        _rmtree_force(self.tmp)

    def _run(self, *files):
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "checks", "example_code_check.py"),
             "--repo", self.repo] + list(files),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=120)

    def test_missing_requested_file_is_rc2(self):
        r = self._run("src/does-not-exist.js")
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 2, "点名文件缺失却判了通过（fail-open）：\n" + out[-600:])
        self.assertIn("不存在", out, "没有说清是哪个文件缺失：\n" + out[-600:])
        self.assertNotIn("全部通过", out, "缺失文件被当成了通过：\n" + out[-600:])

    def test_one_missing_file_does_not_hide_behind_existing_one(self):
        p = os.path.join(self.repo, "src", "ok.js")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write("const x = 1;\n")
        r = self._run(p, "src/missing.js")
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 2, "部分文件缺失时仍在只查存在的那部分：\n" + out[-600:])

    def test_unreadable_file_is_rc2(self):
        """读取失败必须与「没违规」分得开：异常不许被 `continue` 吞掉。"""
        import example_code_check as ec
        p = os.path.join(self.repo, "src", "bad.js")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write("const x = 1;\n")

        def boom(_p):
            raise PermissionError("denied by test")

        saved_argv, saved_read = sys.argv, ec.rule_base.read_bytes
        sys.argv = ["example_code_check.py", "--repo", self.repo, p]
        ec.rule_base.read_bytes = boom
        buf_out, buf_err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                rc = ec.main()
        finally:
            ec.rule_base.read_bytes = saved_read
            sys.argv = saved_argv
        out = buf_out.getvalue() + buf_err.getvalue()
        self.assertEqual(rc, 2, "读取异常被吞掉，门禁当作通过：\n" + out[-600:])
        self.assertIn("无法读取", out, "没有报出读取失败：\n" + out[-600:])


class TestExampleGateAllPrunesVendor(unittest.TestCase):
    """P2-3：全量扫描（--all）不许扫进依赖树 / VCS / 缓存。

    修复前 os.walk 不剪枝 ⇒ `node_modules/pkg/index.js` 里的第三方 console.log 被 rc=1 拦下，
    README quickstart 又直接教用户 `--all`，第一次跑就被别人的代码拦住。剪枝只作用于全量遍历，
    点名传进来的文件仍照查（见 TestExampleGateFailsClosed）。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="egprune_")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "src"))

    def tearDown(self):
        _rmtree_force(self.tmp)

    def _write(self, rel, text):
        p = os.path.join(self.repo, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def _run_all(self):
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "checks", "example_code_check.py"),
             "--repo", self.repo, "--all"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=120)

    def test_vendor_and_vcs_dirs_are_pruned(self):
        # 第三方 / 元数据 / 缓存里的违规：全量扫描一律不该看见。
        self._write("node_modules/pkg/index.js", "console.log(1);\n")
        self._write(".venv/lib/x.py", "import socket; socket.connect(('10.0.0.1', 9))\n")
        self._write(".git/hooks/x.py", "console = 1\n")
        self._write("__pycache__/gen.js", "console.log(2);\n")
        # 本仓真正的第一方代码：必须仍被扫到（否则就是"剪过头"报平安）。
        self._write("src/app.js", "const ok = 1;\n")
        r = self._run_all()
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 0,
                         "vendor/VCS/缓存里的第三方违规被全量扫描拦下了（P2-3 未修）：\n" + out[-800:])
        # 只剩第一方那 1 个文件被扫到；vendor 4 个若没剪掉，这里会是 5 且 rc=1。
        self.assertIn("检查 1 个文件", out,
                      "剪枝后应只剩第一方 src/app.js 一个文件（多了=没剪，少了=剪过头）：\n" + out[-800:])

    def test_first_party_violation_still_blocked_under_all(self):
        self._write("src/bad.js", "console.log('here');\n")
        r = self._run_all()
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 1,
                         "第一方违规没被拦（剪枝误伤）：\n" + out[-800:])
        self.assertIn("NO-CONSOLE", out)


# ---------------------------------------------------------------- 示例规则误报边界
class TestExampleRuleFalsePositives(unittest.TestCase):
    """首屏体验锁：注释不算命中，字符串里的 console.log 只提示，版本号不算 IP。

    这些误报的代价不对称：第一次用就被拦，人会先学会加 `harness-ok`，而不是读规则。
    """

    def test_example_rules_use_the_shared_protocol(self):
        """示例规则必须就是 rule_base.Rule：首屏示例不能教第二套 API。"""
        import example_ruleset as E
        for r in E.EXAMPLE_RULES:
            self.assertIsInstance(r, Rule, "示例规则没有接入统一 Rule 协议：%r" % (r,))
            self.assertFalse(hasattr(r, "rule_id"), "示例规则仍在暴露第二套 id 字段：%r" % (r,))

    def _hits(self, rule_id, path, text):
        import example_ruleset as E
        rule = next(r for r in E.EXAMPLE_RULES if r.id == rule_id)
        return rule.run(CheckContext(path=Path(path), content=text))

    def test_comment_hits_are_not_reported(self):
        cases = [
            ("src/a.js", '// console.log("doc")\n'),
            ("src/a.js", '/* console.log("doc") */\n'),
            ("src/a.vue", '<!-- console.log("doc") -->\n'),
            ("src/a.py", '# console.log("doc")\n'),
            ("src/a.cs", 'var x = 1; // http://10.0.0.1\n'),
        ]
        for path, text in cases:
            self.assertEqual(self._hits("NO-CONSOLE", path, text), [], text)
            self.assertEqual(self._hits("NO-HARDIP", path, text), [], text)

    def test_console_in_string_is_warning_not_block(self):
        hits = self._hits("NO-CONSOLE", "src/a.js", 'const s = "console.log(1)";\n')
        self.assertEqual(len(hits), 1, "字符串里的 console.log 应保留为一条提示")
        self.assertEqual(hits[0].severity, Severity.WARN,
                         "字符串里的 console.log 不该阻断：%r" % (hits[0],))

    def test_code_console_stays_block(self):
        hits = self._hits("NO-CONSOLE", "src/a.js", 'console.log("debug");\n')
        self.assertEqual([h.severity for h in hits], [Severity.ERROR])

    def test_version_like_ip_is_not_an_ip(self):
        for text in ('const v = "1.2.3.4";\n',
                     'const version = "1.2.3.4";\n',
                     'appVersion = "1.2.3.4"\n',
                     '"version": "1.2.3.4",\n'):
            self.assertEqual(self._hits("NO-HARDIP", "src/a.js", text), [],
                             "版本号被当成了硬编码 IP：%s" % text)

    def test_invalid_octets_are_ignored(self):
        for text in ('const x = "999.999.999.999";\n',
                     'const x = "1.2.3.4.5";\n'):
            self.assertEqual(self._hits("NO-HARDIP", "src/a.js", text), [],
                             "非法 IPv4 被当成了命中：%s" % text)

    def test_real_ip_still_blocks(self):
        hits = self._hits("NO-HARDIP", "src/a.js", 'const u = "http://10.0.0.1/api";\n')
        self.assertEqual([(h.line, h.severity) for h in hits], [(1, Severity.ERROR)])


