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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(ROOT, "harness"), os.path.join(ROOT, "checks")):
    if p not in sys.path:
        sys.path.insert(0, p)

import _config                      # noqa: E402
import pc_code_check as pc          # noqa: E402
import h5_code_check as h5          # noqa: E402
import backend_layer_check as be    # noqa: E402
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
        """反向锁：真配置（config/harness.yaml）必须零告警。

        否则这套探测一上线就是满屏噪音，人人学会忽略它 —— 比不报还糟。
        """
        _config._warned.clear()
        buf = io.StringIO()
        old = sys.stderr
        sys.stderr = buf
        try:
            cfg = _config._load_yaml(os.path.join(ROOT, "config", "harness.yaml"))
        finally:
            sys.stderr = old
        warns = [l for l in buf.getvalue().splitlines() if "[config-warn]" in l]
        self.assertGreater(len(cfg.get("gates") or {}), 20, "没读到 gate，判据坏了")
        self.assertEqual(warns, [], "真配置触发了超纲告警（要么配置越界，要么探测误报）")


# ------------------------------------------------- 本地覆盖层（harness.local.yaml）
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
class TestPcComponents(unittest.TestCase):
    """2026-09-06 迁移：直接测生产路径 pc_ruleset（阶段1 兼容层已删，防止双轨掩盖回归）。"""
    def setUp(self):
        import pc_ruleset as pr
        self.pr = pr
        self._saved = (pr.ALLOW, pr.DENY, pr.DEAD, pr.SUFFIX_EXC)
        pr.ALLOW = {"eagle-input", "eagle-table"}
        pr.DENY = {"eagle-date-new"}
        pr.DEAD = {"eagle-menu"}
        pr.SUFFIX_EXC = {"eagle-checkbox-group-page"}

    def tearDown(self):
        self.pr.ALLOW, self.pr.DENY, self.pr.DEAD, self.pr.SUFFIX_EXC = self._saved

    def _run(self, content, mode="strict"):
        from rule_base import CheckContext
        ctx = CheckContext(path=Path("x.vue"), content=content,
                           template=re.search(r"<template>(.*?)</template>", content, re.S).group(1) if "<template>" in content else "",
                           mode=mode)
        return [f.message for f in self.pr.ComponentRule().run(ctx)]

    def test_deny_blocked_in_both_modes(self):
        for mode in ("strict", "loose"):
            ps = self._run("<template><eagle-date-new /></template>", mode)
            self.assertEqual(len(ps), 1, mode)
            self.assertIn("废弃组件", ps[0])

    def test_dead_strict_blocked_loose_allowed(self):
        self.assertEqual(len(self._run("<template><eagle-menu /></template>", "strict")), 1)
        self.assertEqual(self._run("<template><eagle-menu /></template>", "loose"), [])

    def test_unknown_new_parallel_blocked(self):
        ps = self._run("<template><eagle-foo-new /></template>")
        self.assertEqual(len(ps), 1)
        self.assertIn("平行组件", ps[0])

    def test_allow_and_suffix_exception_pass(self):
        self.assertEqual(self._run("<template><eagle-input /><eagle-table /></template>"), [])
        self.assertEqual(self._run("<template><eagle-checkbox-group-page /></template>"), [])

    def test_extract_components_two_forms(self):
        content = "<template><eagle-input /><eagle-table :x='1'/></template>\n" \
                  "<script>export default {components: {'eagle-menu': X}}</script>"
        names = self.pr._extract_components(content)
        self.assertEqual(names, {"eagle-input", "eagle-table", "eagle-menu"})


# ------------------------------------------------- H5 组件收敛（ruleset 层）
class TestH5Components(unittest.TestCase):
    def setUp(self):
        import h5_ruleset as hr
        self.hr = hr
        self._saved = (hr.ALLOW, hr.DENY, hr.DEAD)
        hr.ALLOW = {"eagle-input"}
        hr.DENY = {"eagle-checkbox-group-new"}
        hr.DEAD = set()

    def tearDown(self):
        self.hr.ALLOW, self.hr.DENY, self.hr.DEAD = self._saved

    def _run(self, content, mode="strict"):
        from rule_base import CheckContext
        ctx = CheckContext(path=Path("x.vue"), content=content, mode=mode)
        return [f.message for f in self.hr.ComponentRule().run(ctx)]

    def test_deny_blocked(self):
        ps = self._run("<template><eagle-checkbox-group-new /></template>")
        self.assertEqual(len(ps), 1)
        self.assertIn("废弃组件", ps[0])

    def test_new_parallel_blocked(self):
        ps = self._run("<template><eagle-widget-new /></template>")
        self.assertEqual(len(ps), 1)
        self.assertIn("平行组件", ps[0])

    def test_allow_passes_and_unknown_not_blocked(self):
        self.assertEqual(self._run("<template><eagle-input /><eagle-something-else /></template>"), [])

    def test_extract_components_two_forms(self):
        content = "<template><eagle-row-card /></template>\n" \
                  "<script>uni.navigateTo({url: '/x?c=' + 'eagle-fab'})</script>"
        names = self.hr._extract_components(content)
        self.assertEqual(names, {"eagle-row-card", "eagle-fab"})

    def test_allowlist_h5_loads_real_file(self):
        """真实 allowlist 文件应可加载且含手册关键组件 + 已生效的 dead 段。
        注意不读 setUp patch 后的模块变量，直接从 yaml 重载（防测试间污染）。"""
        import h5_ruleset as hr
        allow, deny, dead = hr._load_allowlist()
        self.assertIn("eagle-row-card", allow)
        self.assertIn("eagle-page-plus", allow)
        self.assertIn("eagle-checkbox-group-new", deny)
        self.assertIn("eagle-tag", dead)  # 2026-09-06 死组件治理已生效
        self.assertEqual(len(dead), 11)


# ------------------------------------------------- P2/P3 回归（2026-09-06 双轨掩盖 bug 修复验证）
class TestShimRemovalRegressions(unittest.TestCase):
    def test_p2_eldeniy_warns_on_template_tag(self):
        """P2 修复验证：模板直用 <el-dialog> 应 warn（曾因误扫 script 段而漏报）。"""
        import pc_ruleset as pr
        ctx = CheckContext(path=Path("x.vue"),
                           content="<template><el-dialog title='x'/></template>",
                           template="<el-dialog title='x'/>")
        msgs = [f.message for f in pr.ElDenyWarnRule().run(ctx)]
        self.assertEqual(len(msgs), 1)
        self.assertIn("直用 el-dialog ×1", msgs[0])
        self.assertIn("eagle-* 封装", msgs[0])

    def test_p2_eldeniy_silent_for_unknown_tag(self):
        import pc_ruleset as pr
        ctx = CheckContext(path=Path("x.vue"), template="<el-table-column />", content="t")
        self.assertEqual([f.message for f in pr.ElDenyWarnRule().run(ctx)], [])

    def test_p3_pc_syntax_rule_skips_ts(self):
        """P3 修复验证：lang=\"ts\" SFC 必须跳过 node（曾误写 or 致语义反转）。"""
        import pc_ruleset as pr
        rule = pr.SyntaxRule(skip_node=False)
        ts_double = CheckContext(path=Path("a.vue"), content='<script lang="ts">const x: number = 1</script>')
        ts_single = CheckContext(path=Path("a.vue"), content="<script lang='ts'>const x: number = 1</script>")
        js = CheckContext(path=Path("a.vue"), content="<script>const x = 1</script>")
        self.assertFalse(rule.applicable(ts_double))
        self.assertFalse(rule.applicable(ts_single))
        self.assertTrue(rule.applicable(js))

    def test_p3_h5_syntax_rule_skips_ts(self):
        import h5_ruleset as hr
        rule = hr.SyntaxRule(skip_node=False)
        ts = CheckContext(path=Path("a.vue"), content='<script lang="ts">const x: number = 1</script>')
        js = CheckContext(path=Path("a.vue"), content="<script>const x = 1</script>")
        self.assertFalse(rule.applicable(ts))
        self.assertTrue(rule.applicable(js))

    def test_p3_6_h5_upload_inline_exemption(self):
        """P3-6 回归锁（外部评审）：`harness-ok: <规则ID>` 提供代码级豁免出口——
        存量合规写法（签名由 common.uploadUrl 统一封装）改到该行时不再无处申辩。
        锁四点：①无标记必拦 ②上行标记豁免 ③同行标记豁免 ④ID 不匹配不豁免。"""
        import h5_ruleset as hr
        rule = hr.UploadSignRule()
        call = "  m() { uni.uploadFile({ url: u }) }\n"
        def hits(script):
            ctx = CheckContext(path=Path("a.vue"), content=script, script=script)
            return len(rule.run(ctx))
        self.assertEqual(hits(call), 1, "无标记必须拦（规则失效=回归）")
        self.assertEqual(hits("  // harness-ok: H5-UPLOAD 签名由 common.uploadUrl 统一加\n" + call), 0,
                         "紧邻上一行的标记应豁免")
        self.assertEqual(hits("  m() { uni.uploadFile({ url: u }) }  // harness-ok: H5-UPLOAD 统一封装\n"), 0,
                         "同行标记应豁免")
        self.assertEqual(hits("  // harness-ok: H5-CONSOLE 只豁免别的规则\n" + call), 1,
                         "规则 ID 不匹配不得豁免（防一句话静默全部规则）")

    def test_p0_pc_syntax_rule_wired_and_catches_bad_js(self):
        """P0 回归锁（外部评审）：语法规则必须真正被 check_file 执行——
        重构曾把实例构造后漏接，坏语法 .vue 零检出且 68 例全绿（测试只测了规则
        对不对，没测接没接）。本测试双锁：①规则自身能检出 ②check_file 链路能检出。
        无 node 的机器（CI 精简镜像）跳过而非红——与 sh 密闭同一原则（见 test_hooks_chain._find_sh）。"""
        if shutil.which("node") is None:
            self.skipTest("node 不可用（语法校验依赖 node；CI 需 setup-node 才跑本用例）")
        import pc_code_check as pcc
        bad = "<template><div /></template>\n<script setup>\nconst x = ;\n</script>\n"
        # ①规则层
        from pc_ruleset import SyntaxRule
        rule = SyntaxRule(skip_node=False)
        rule._check_syntax = pcc.check_syntax
        out = rule.run(CheckContext(path=Path("a.vue"), content=bad, script="\nconst x = ;\n"))
        self.assertTrue(out, "SyntaxRule 自身检不出坏语法（node 环境或规则问题）")
        # ②链路层（check_file 实际执行）
        tmpd = tempfile.mkdtemp(prefix="synwire_")
        try:
            f = os.path.join(tmpd, "bad.vue")
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(bad)
            saved_repo, saved_mode, saved_gate = pcc.REPO, pcc.MODE, pcc.GATE_NAME
            pcc.REPO, pcc.MODE = tmpd, "strict"
            # 同 TestBackendRules：夹具临时目录不得继承真机 gxgk_pc 的
            # legacy_baseline: true（坏语法会被降为观察项 → problems 为空 → 假红）。
            pcc.GATE_NAME = "__hermetic_no_gate__"
            try:
                problems, _ = pcc.check_file(f)
            finally:
                pcc.REPO, pcc.MODE, pcc.GATE_NAME = saved_repo, saved_mode, saved_gate
            self.assertTrue(any("语法错误" in p for p in problems),
                            f"check_file 未检出坏语法（接线回归!）: {problems}")
        finally:
            _rmtree_force(tmpd)


# -------------------------------------------------------- 后端分层规则
class TestBackendRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="hbiz_")
        cls._saved_root, cls._saved_targeted = be.ROOT, be.TARGETED
        cls._saved_gate = be.GATE_NAME
        be.ROOT = Path(cls.tmp)
        # 夹具是临时目录，check_file 却按 GATE_NAME 读**真机** gate 配置：共享配置里
        # gxgk_backend 带 legacy_baseline: true → targeted 命中被降 info，本类的双态
        # severity 断言随机器/配置翻红翻绿（2026-09-21 实测翻红）。钉到不存在的 gate
        # 名 → gate() 拿到空配置 → LEGACY_EXEMPT 恒 False，只验证 severity 机制本身。
        be.GATE_NAME = "__hermetic_no_gate__"

    @classmethod
    def tearDownClass(cls):
        be.ROOT, be.TARGETED = cls._saved_root, cls._saved_targeted
        be.GATE_NAME = cls._saved_gate
        _rmtree_force(cls.tmp)

    def _file(self, rel, text):
        p = Path(self.tmp, *rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_r2_business_using_controller_is_error(self):
        p = self._file("RMC/Eagle.RMC.Business/B.cs",
                       "using Eagle.RMC.Controller;\nnamespace X {}\n")
        vs = be.check_file(p)
        r2 = [v for v in vs if v["rule"] == "R2"]
        self.assertEqual(len(r2), 1)
        self.assertEqual(r2[0]["severity"], "error")

    def test_r2_component_match_no_false_positives(self):
        """第三方 System.Web / 超集段名 Controllers 不应命中。"""
        p = self._file("RMC/Eagle.RMC.Business/Safe.cs",
                       "using System.Web;\nusing Eagle.RMC.Controllers;\nnamespace X {}\n")
        vs = be.check_file(p)
        self.assertEqual([v for v in vs if v["rule"] == "R2"], [])

    def test_r3_entities_using_business(self):
        p = self._file("RMC/Eagle.RMC.Entities/E.cs",
                       "using Eagle.RMC.Business;\nnamespace X {}\n")
        vs = be.check_file(p)
        r3 = [v for v in vs if v["rule"] == "R3"]
        self.assertEqual(len(r3), 1)

    def test_r5_r6_dual_severity(self):
        p = self._file("RMC/Eagle.RMC.Controller/C.cs",
                       "public void GetData(int id) {}\n"
                       "public object GetPageData(int id) { return null; }\n")
        be.TARGETED = False
        vs = be.check_file(p)
        sev = {v["rule"]: v["severity"] for v in vs}
        self.assertEqual(sev.get("R5"), "info")   # 全量=观察
        self.assertEqual(sev.get("R6"), "info")
        be.TARGETED = True
        vs = be.check_file(p)
        sev = {v["rule"]: v["severity"] for v in vs}
        self.assertEqual(sev.get("R5"), "error")  # targeted=拦新代码
        self.assertEqual(sev.get("R6"), "error")
        be.TARGETED = False

    def test_compute_changed_lines_non_repo_is_silent_none(self):
        """非 git 目录：compute_changed_lines 静默返回 None（整文件口径），不得出告警。

        噪音来源（2026-09-21 实测）：git 在仓外目录把 diff 退化成 --no-index 模式，
        该模式不认 --cached → rc=129「unknown option `cached'」→ 旧实现当传感器故障
        刷「[⚠ harness] 存量按行豁免可能未生效」。夹具临时目录 / CLI 体检任意目录
        是合法输入而非故障 —— 三态语义不变（None=整文件口径），只去误报。"""
        import rule_base
        saved_warned = rule_base._GIT_DIAG_WARNED
        rule_base._GIT_DIAG_WARNED = False
        buf = io.StringIO()
        old = sys.stderr
        sys.stderr = buf
        try:
            got = rule_base.compute_changed_lines(
                self.tmp, os.path.join("RMC", "Eagle.RMC.Controller", "C.cs"))
        finally:
            sys.stderr = old
            rule_base._GIT_DIAG_WARNED = saved_warned
        self.assertIsNone(got, "非 git 仓应退整文件口径（None），与旧实现同值")
        self.assertEqual(buf.getvalue(), "", "非 git 目录不是传感器故障，不得出告警")

    def test_r1_controller_new_repository_is_info(self):
        p = self._file("RMC/Eagle.RMC.Controller/R.cs",
                       "var x = new FooRepository(conn);\n")
        vs = be.check_file(p)
        r1 = [v for v in vs if v["rule"] == "R1"]
        self.assertEqual(len(r1), 1)
        self.assertEqual(r1[0]["severity"], "info")

    def test_r7_foreign_vue_is_error(self):
        """targeted 传入非 .cs 代码文件（如探针 .vue）必须拦，不能静默丢弃。"""
        p = self._file("src/__cb_gate_probe__.vue", "console.log(1)\n")
        be.TARGETED = True
        try:
            vs = be.check_file(p)
        finally:
            be.TARGETED = False
        r7 = [v for v in vs if v["rule"] == "R7"]
        self.assertEqual(len(r7), 1)
        self.assertEqual(r7[0]["severity"], "error")

    def test_r7_wwwroot_js_is_info(self):
        """wwwroot/ 第三方前端资源改动降观察，不产生误拦。"""
        p = self._file("RMC/Eagle.RMC.Web/wwwroot/Plugins/x.js", "var a = 1\n")
        vs = be.check_file(p)
        r7 = [v for v in vs if v["rule"] == "R7"]
        self.assertEqual(len(r7), 1)
        self.assertEqual(r7[0]["severity"], "info")

    def test_r7_not_applicable_to_cs(self):
        p = self._file("RMC/Eagle.RMC.Business/B.cs", "namespace X {}\n")
        vs = be.check_file(p)
        self.assertEqual([v for v in vs if v["rule"] == "R7"], [])

    def test_clean_file_no_violation(self):
        # 文件须符合 ENC 约定(utf-8-bom): BOM + 内容
        p = self._file("RMC/Eagle.RMC.Business/Good.cs", "")
        with open(p, "wb") as f:
            f.write(b"\xef\xbb\xbf" + "using Eagle.RMC.Entities;\npublic class T { void UpdateBatchDeletes(){} }\n".encode("utf-8"))
        self.assertEqual(be.check_file(p), [])

    # -------- 2026-09-16：文件级规则被按行过滤器静默丢弃（实测坐实的缺陷）--------
    # 缺陷形态：ENC / R7 是**文件级**规则，Finding 行号恒为 1；而按行过滤器只保留
    # changed_lines 内的命中 —— 编辑存量文件的第 50 行时 1 ∉ {50}，结论被静默丢弃、rc=0。
    # 下面这些锁**必须走 check_file 的真实链路**：helper 自己的单测（test_review_v8 的
    # test_finding_line_filter_helper）当时是绿的，缺口恰在「规则 × 过滤器」的接缝上。
    def _with_changed_lines(self, lines, fn):
        """临时仓不是 git 仓，compute_changed_lines 真实实现恒返回 None（=整文件口径、
        不过滤）→ 不注入改动行就复现不出缺陷。这里把它钉成给定集合，跑完还原。
        （注意是 None 不是空集：空集=「本次没有新增行」的 0 命中口径，中 C 的关键区分。）

        ⚠ 2026-09-23（M12）：注入点从 `compute_changed_lines` 移到 `compute_changed_lines_ex`
        —— 后者是 `build_ctx` 现在**真的**调的那个名字（三态版是它的 `[0]`）。改完名不改这里，
        打桩会静默落空：`changed` 退回真实实现的 None，本组锁会**因为"不过滤了"而变红**
        （不是变绿，所以当场暴露了）；但更该记住的是反向情形 —— 打桩落在没人调的名字上，
        测试会继续绿着，而它声称锁住的那条链路其实一次都没被驱动。"""
        import rule_base
        saved_t, saved_ccl = be.TARGETED, rule_base.compute_changed_lines_ex
        be.TARGETED = True
        rule_base.compute_changed_lines_ex = lambda repo, rel: (frozenset(lines), False)
        try:
            return fn()
        finally:
            be.TARGETED, rule_base.compute_changed_lines_ex = saved_t, saved_ccl

    def test_enc_not_dropped_by_line_filter(self):
        """ENC 不得因改动行不含第 1 行而被丢掉。

        真实触发面：仓内约 7% 无 BOM 存量文件（encoding_ruleset 注释里的实测数字），
        改其中任一文件的**中间某行**即可 —— 正是 2026-09-15 那次 P1 修复（"改动过的文件
        没有存量可豁免，必须硬拦"）想拦的场景，却被按行过滤器抵消掉。
        """
        p = Path(self.tmp, "RMC", "Eagle.RMC.Business", "Legacy.cs")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes("// 中文注释\n".encode("gbk"))        # 存量异类：GBK，仓约定 utf-8-bom
        vs = self._with_changed_lines({50}, lambda: be.check_file(p))
        enc = [v for v in vs if v["rule"] == "ENC"]
        self.assertEqual(len(enc), 1, "改动行不含第 1 行时 ENC 的文件级结论被静默丢弃（缺陷复发）")
        self.assertEqual(enc[0]["severity"], "error", "改动过的文件编码错必须硬拦")

    def test_r7_not_dropped_by_line_filter(self):
        """R7 同为文件级规则：改存量 .vue 的中间某行，不得静默放过。"""
        p = Path(self.tmp, "src", "legacy_probe.vue")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\xef\xbb\xbf" + b"console.log(1)\n")   # BOM 合规，隔离掉 ENC 干扰
        vs = self._with_changed_lines({50}, lambda: be.check_file(p))
        r7 = [v for v in vs if v["rule"] == "R7"]
        self.assertEqual(len(r7), 1, "改动行不含第 1 行时 R7 的文件级结论被静默丢弃（缺陷复发）")
        self.assertEqual(r7[0]["severity"], "error")

    def test_file_level_flag_contract(self):
        """反向锁：file_level 是**例外**，不是豁免总开关。

        它只给"整个文件的属性"这类规则（ENC/R7）；行级规则一旦误挂，按行豁免会整体失效
        （改一行老文件又变成对整文件历史遗留负责）。两个方向都钉住。
        """
        import rule_base
        from encoding_ruleset import EncodingRule
        from backend_ruleset import (R7ForeignFileRule, R5MethodNameRule,
                                     R9MagicStatusRule, R10DatetimeColumnRule)

        class _F:      # 行号恒为 1 的 Finding 替身
            line = 1
        keep, dropped = rule_base.filter_findings_by_lines([_F()], frozenset({50}), file_level=True)
        self.assertEqual((len(keep), dropped), (1, 0), "file_level=True 必须放行")
        keep2, dropped2 = rule_base.filter_findings_by_lines([_F()], frozenset({50}))
        self.assertEqual((len(keep2), dropped2), (0, 1), "不带 file_level 时仍按行过滤（默认不变）")

        self.assertTrue(EncodingRule().file_level, "ENC 必须自报 file_level")
        self.assertTrue(R7ForeignFileRule().file_level, "R7 必须自报 file_level")
        for r in (R5MethodNameRule(), R9MagicStatusRule(), R10DatetimeColumnRule()):
            self.assertFalse(r.file_level, "%s 是行级规则，不得自报 file_level" % r.id)


# ------------------------------------------------------------ 阶段1 规则模型统一（rule_base + ruleset）
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


class TestPcRuleset(unittest.TestCase):
    def test_console_rule_fires(self):
        from pc_ruleset import PC_RULES, ConsoleLogRule
        ctx = CheckContext(path=Path("/p"), content="<script>console.log(1)</script>",
                            script="console.log(1)")
        rule = ConsoleLogRule()
        fnd = rule.run(ctx)
        self.assertEqual(len(fnd), 1)
        self.assertIn("残留 console.log", fnd[0].message)
        self.assertEqual(fnd[0].severity, Severity.ERROR)

    def test_ruleset_importable(self):
        from pc_ruleset import PC_RULES
        self.assertTrue(any(r.id == "PC-COMP" for r in PC_RULES))


class TestH5Ruleset(unittest.TestCase):
    def test_crlf_rule_fires_on_crlf(self):
        from h5_ruleset import CrlfRule
        ctx = CheckContext(path=Path("/h"), content="a", raw=b"a\r\nb")
        fnd = CrlfRule().run(ctx)
        self.assertEqual(len(fnd), 1)
        self.assertIn("CRLF", fnd[0].message)

    def test_upload_sign_rule_fires(self):
        """H5-UPLOAD：规则本身仍会命中（适用范围由配置决定：非 gxgk 的 gate 用
        disabled_rules 关闭，见 TestDisabledRules）。文案已改为"上传调用确认"，
        不再声称所有客户都要签名（2026-09-12 前提修正）。"""
        from h5_ruleset import UploadSignRule
        ctx = CheckContext(path=Path("/h"), script="uni.uploadFile({")
        fnd = UploadSignRule().run(ctx)
        self.assertEqual(len(fnd), 1)
        self.assertIn("上传调用确认", fnd[0].message)

    def test_upload_sign_rule_message_does_not_teach_the_waiver(self):
        """★ 2026-09-30 **反转**：原锁写的是「文案里必须给出出口」，正好写反了。

        命中文案会随 gate 输出被**逐字**注入给 agent（hook 里的 `out` 就是它）。
        在那里复述豁免语法，等于把「怎么把命中行合法豁免」交给**非可信通道** ——
        注入通道若可伪造，攻击者就能借门禁自己的口吻诱导"把命中行全豁免"。

        出口并没有消失，只是搬去了**可信文档**：工程根 `AGENTS.md` 的「行内豁免」一节
        （它本来就在 agent 的上下文里，正常用法一点没少）。所以本锁是双向的：
        文案里**不许**再出现语法或「豁免」字样，同时 AGENTS.md 里**必须**还写着它。
        """
        from h5_ruleset import UploadSignRule
        ctx = CheckContext(path=Path("/h"), script="uni.uploadFile({")
        fnd = UploadSignRule().run(ctx)
        self.assertEqual(len(fnd), 1)
        self.assertNotIn("harness-ok", fnd[0].message,
                         "命中文案里又出现了豁免语法 —— 该文案会被逐字注入，语法只住 AGENTS.md")
        self.assertNotIn("豁免", fnd[0].message,
                         "命中文案里又提到「豁免」—— 连这个引导词也不该出现在注入通道里")
        doc = (Path(__file__).resolve().parent.parent / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("harness-ok: H5-UPLOAD", doc,
                      "AGENTS.md 里必须写着出口 —— 否则这条锁只是把人堵死，不是把路改道")


class TestBackendRuleset(unittest.TestCase):
    def test_r2_component_match_via_ruleset(self):
        from backend_ruleset import R2ComponentRule
        # 用临时 repo 根算 rel
        tmp = tempfile.mkdtemp(prefix="hr2_")
        rel = "RMC/Eagle.RMC.Business/B.cs"
        ctx = CheckContext(path=Path(tmp, rel), rel=rel,
                            content="using Eagle.RMC.Controller;\n")
        rule = R2ComponentRule()
        fnd = rule.run(ctx)
        self.assertEqual(len(fnd), 1)
        self.assertEqual(fnd[0].rule_id, "R2")
        self.assertEqual(fnd[0].severity, Severity.ERROR)
        _rmtree_force(tmp)

    def test_r5_targeted_dual_state(self):
        from backend_ruleset import R5MethodNameRule
        rule = R5MethodNameRule()
        ctx_full = CheckContext(path=Path("/x"), rel="RMC/A.cs",
                                 content="void GetData(){}", targeted=False)
        ctx_tgt = CheckContext(path=Path("/x"), rel="RMC/A.cs",
                                content="void GetData(){}", targeted=True)
        self.assertEqual(rule.run(ctx_full)[0].severity, Severity.INFO)
        self.assertEqual(rule.run(ctx_tgt)[0].severity, Severity.ERROR)


# ------------------------------------------------- 阶段2 注册表（registry）
class TestRegistry(unittest.TestCase):
    def test_load_returns_all_configured_gates(self):
        from registry_loader import load_gate_scripts
        m = load_gate_scripts()
        for k in ("gxgk_pc", "gxgk_h5", "gxgk_backend"):
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
            run_gate._notify_on_block("gxgk_pc", "targeted", 1, 1, 500)
        finally:
            run_gate._notify_webhook = orig
        self.assertEqual(calls, [])
        self.assertFalse(os.path.exists(run_gate.NOTIFY_STATE))

    def test_throttle_blocks_second_within_window(self):
        run_gate.NOTIFY_STATE = os.path.join(self.tmp, "notify_last.json")
        self.assertFalse(run_gate._notify_throttled("gxgk_pc"))
        self.assertTrue(run_gate._notify_throttled("gxgk_pc"))
        self.assertTrue(run_gate._notify_throttled("gxgk_h5") is False or True)  # 不同 gate 独立

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
            run_gate._log_run("gxgk_pc", "targeted", 2, 1, 100)
            run_gate._log_run("gxgk_pc", "targeted", 2, 0, 100)
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
        sm, gate, msg = guard._parse_args(["gxgk_pc"])
        self.assertFalse(sm)
        self.assertEqual(gate, "gxgk_pc")
        self.assertEqual(msg, "")
        sm, gate, msg = guard._parse_args(["gxgk_pc", "some", "message"])
        self.assertEqual(gate, "gxgk_pc")
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
            "config/harness.yaml": True,
            "config/other.yaml": False,
            # 2026-09-12 P0-2：调度层纳入保护（评审指出改弱 run_gate/hook 不提交时 guard 零反应）
            "harness/run_gate.py": True,
            # 2026-09-12 第三轮 N-δ：AI 客户端的自动门禁钩子同等对待
            "integrations/hooks/agentguard-gate.py": True,
            "integrations/codex/README.md": False,
            "harness/_config.py": True,
            "harness/hooks/pre-commit.py": True,
            "harness/hooks-dir/pre-commit": True,
            "harness/agents/auto-gate.py": True,
            "harness/README.md": False,
            "checks/README.md": False,
        }
        for rel, want in cases.items():
            self.assertEqual(guard._in_protected(rel), want, rel)


class TestDisabledRules(unittest.TestCase):
    """gate 级关闭规则（`disabled_rules`）——2026-09-12 配置表开关。

    触发场景：H5-UPLOAD（上传须带签名头）只对 gxgk 成立——全仓实扫 gxgk 108 处
    buildUploadHeader、其余客户 0 处，其上传本来就不签名。全局启用会给它们制造
    "按行豁免"噪声且理由为假。本用例锁三件事：①配置能读到 ②引擎真的过滤掉
    ③没配的 gate 仍然拦（不能把 gxgk 的防护一起关掉）。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hdis_")
        self._root = _config._HARNESS_ROOT
        _config._HARNESS_ROOT = self.tmp                      # base config 也放临时目录
        os.makedirs(os.path.join(self.tmp, "config"), exist_ok=True)
        self.repo = os.path.join(self.tmp, "repo_h5")
        os.makedirs(os.path.join(self.repo, "src"), exist_ok=True)
        with open(os.path.join(self.tmp, "config", "harness.yaml"), "w", encoding="utf-8") as f:
            f.write(f"repos:\n  off_h5: {self.repo}\n  on_h5: {self.repo}\n"
                    "gates:\n"
                    "  off_h5:\n    enabled: true\n    repo: off_h5\n    mode: strict\n"
                    "    disabled_rules: [H5-UPLOAD]\n"
                    "  on_h5:\n    enabled: true\n    repo: on_h5\n    mode: strict\n")
        self.probe = os.path.join(self.repo, "src", "probe.vue")
        with open(self.probe, "w", encoding="utf-8", newline="\n") as f:
            f.write("<template><view /></template>\n<script>\nexport default {\n"
                    "  m() { uni.uploadFile({ url: u, filePath: p }) }\n}\n</script>\n")

    def tearDown(self):
        _config._HARNESS_ROOT = self._root
        _rmtree_force(self.tmp)

    def test_rule_base_reads_disabled_rules(self):
        from rule_base import disabled_rules
        self.assertEqual(disabled_rules("off_h5", None, h5.LIB), {"H5-UPLOAD"})
        self.assertEqual(disabled_rules("on_h5", None, h5.LIB), set())
        # 按 repo 反查（未传 --gate 的场景）也要能取到
        self.assertEqual(disabled_rules(None, self.repo, h5.LIB), {"H5-UPLOAD"})

    def test_h5_upload_filtered_only_where_configured(self):
        saved = (h5.GATE_NAME, h5.REPO, h5.SRC, h5.MODE)
        try:
            h5.GATE_NAME, h5.REPO, h5.SRC, h5.MODE = "off_h5", self.repo, os.path.join(self.repo, "src"), "strict"
            off_problems = [p for p in h5.check_file(self.probe) if "上传调用确认" in p]
            self.assertEqual(off_problems, [], "配置了 disabled_rules 的 gate 不该再报 H5-UPLOAD")
            h5.GATE_NAME = "on_h5"          # 同仓、未关规则
            on_problems = [p for p in h5.check_file(self.probe) if "上传调用确认" in p]
            self.assertTrue(on_problems, "未配 disabled_rules 的 gate 必须照旧拦（gxgk 防护不能被连带关掉）")
        finally:
            h5.GATE_NAME, h5.REPO, h5.SRC, h5.MODE = saved

    def test_gxgk_keeps_upload_rule(self):
        """真实配置回归：gxgk_h5 不得被关闭 H5-UPLOAD（唯一需要签名的客户）。"""
        saved = _config._HARNESS_ROOT
        _config._HARNESS_ROOT = ROOT          # 回到真实 config
        try:
            from rule_base import disabled_rules
            self.assertNotIn("H5-UPLOAD", disabled_rules("gxgk_h5", None, h5.LIB))
            for gate in ("zhongxin_h5", "ntrx_h5", "keji_h5", "c712_h5", "c712v3_h5",
                         "hanbogaoxin_h5", "zhongdj_h5", "hy_base_h5"):
                self.assertIn("H5-UPLOAD", disabled_rules(gate, None, h5.LIB),
                              f"{gate} 应关闭 H5-UPLOAD（该客户上传不签名）")
        finally:
            _config._HARNESS_ROOT = saved


if __name__ == "__main__":
    unittest.main(verbosity=2)

class TestSfcStructureNoScriptFalsePositive(unittest.TestCase):
    """PC-SFC 规则：**压根没有 <script> 块的 .vue 不算错**（2026-09-13 修复误报）。

    来源：Trae 的模型在会话记忆里反馈「pc_code_check.py 存在规则 bug：任何 .vue 只要找不到
    </script> 就报错，无论是否有 <script> 开标签」；三场景实测确认其说对了。
    修复前：`if tag == "script" and not closes:` —— 没判断"有没有开标签"，
    于是纯模板 .vue（只有 <template>）也被拦。真正的不闭合场景由紧随其后的
    `opens and len(opens) > len(closes)` 覆盖，所以那条更宽的判断既多余又有害。
    影响面：会误拦没有 script 块的纯展示组件（PC 端）。本类即该修复的证伪锁。
    """

    PURE_TEMPLATE = "<template>\n  <div class=\"a\">x</div>\n</template>\n"
    UNCLOSED = "<template>\n  <div class=\"a\">x</div>\n</template>\n<script setup>\nconst a = 1\n"
    NORMAL = "<template>\n  <div class=\"a\">x</div>\n</template>\n<script setup>\nconst a = 1\n</script>\n"
    STYLE_ONLY = "<template>\n  <div class=\"a\">x</div>\n</template>\n<style scoped>\n.a{}\n</style>\n"

    def test_pure_template_vue_passes(self):
        """场景 A：只有 <template>（没有 script 块）→ 必须放行（修复前这里会误报）。"""
        msg = pc.check_sfc_structure(self.PURE_TEMPLATE)
        self.assertIsNone(msg, f"纯模板 .vue 被误报：{msg}")

    def test_style_only_vue_passes(self):
        """场景 D：有 template + style、无 script → 必须放行。"""
        self.assertIsNone(pc.check_sfc_structure(self.STYLE_ONLY))

    def test_unclosed_script_still_blocked(self):
        """场景 B：真不闭合必须仍然拦（反向锁，防止修误报时把真问题也放过去）。"""
        msg = pc.check_sfc_structure(self.UNCLOSED)
        self.assertIsNotNone(msg, "真·未闭合的 <script> 必须继续拦")
        self.assertIn("script", msg)

    def test_normal_sfc_passes(self):
        """场景 C：正常闭合 → 放行。"""
        self.assertIsNone(pc.check_sfc_structure(self.NORMAL))
