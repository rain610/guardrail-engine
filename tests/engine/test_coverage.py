# -*- coding: utf-8 -*-
"""覆盖度自评的回归锁（2026-09-12 第四轮）。

锁三件事：
1. 聚合/分类学正确——「没响」必须被拆成可行动的分类（活跃 / 从未触发 / 按 gate 禁用 / 该端无运行），
   并且**没有规则级数据时不许判定**（数据缺失 ≠ 从未触发，这条最容易被写成假结论）。
2. 埋点真的在工作——跑一次真实门禁能把规则级统计带回 gate_log（传感器自己也要被监考）。
3. 埋点对直跑脚本零影响——不设 HARNESS_STATS_FILE 时不产生任何文件、输出不变。
"""
import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _tmpclean import rmtree_force as _rmtree_force  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = sys.executable

# 「整串就是 run_gate 那条模块路径」——即命令行调用里会写的那一段。
# 只认**独占整串**的路径：带反引号 / 空格 / 中文的散文（docstring、注释、断言消息）一律不匹配，
# 而那正是这条判据要放过的东西。
# 字符类刻意用 **ASCII**（不是 `\w`）：`\w` 在 str 模式里连中文都算，于是不带空格的
# 中文串（`提到run_gate`）会被读成"像个模块路径"。路径本来就是 ASCII 的，收窄没有代价。
_RUN_GATE_PATH = re.compile(r"^[A-Za-z0-9_./\\-]*run_gate(\.py)?$")


def _code_calls_run_gate(src):
    """这份源码在**代码**里用到 `run_gate` 吗？（散文里的提及不算。）

    `TestIsolationAndScripts.test_tests_set_harness_log_file` 的判据本体。单独提出来是为了
    能被直接喂合成文本做两向证伪（同批 `test_lock_inventory._anchor_problem` 的写法）。

    路径字符串只在**调用表达式里**才算调用（2026-10-21 收窄）：数据表里的成员名不是命令。
    现场：`test_release_artifact.py` 的 `ENGINE_PROBE` 是"轮子里应当出现哪些文件"的期望
    成员名单，其中一项恰好是 `agentguard/_engine/harness/run_gate.py` —— 该文件一行都没
    调过 run_gate，却被判成"会调却没设 HARNESS_LOG_FILE"。与 2026-09-24 那次假警同源：
    判据比它声称的"会**调**"宽了一档。真调用一定落在 `os.path.join(...)` / `subprocess.run(...)`
    这类调用参数里（`import run_gate` 两条分支照旧覆盖模块形态）。
    """
    tree = ast.parse(src)
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    def _inside_call(node):
        while node in parent:
            node = parent[node]
            if isinstance(node, ast.Call):
                return True
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "run_gate":
            return True                       # run_gate(...) / run_gate.LOG_DIR = ...
        if isinstance(node, ast.Attribute) and node.attr == "run_gate":
            return True                       # pkg.run_gate.xxx
        if isinstance(node, ast.Import):
            if any(a.name == "run_gate" or a.name.endswith(".run_gate") for a in node.names):
                return True
        if isinstance(node, ast.ImportFrom):
            mods = [a.name for a in node.names] + ([node.module] if node.module else [])
            if any(m == "run_gate" or m.endswith(".run_gate") for m in mods):
                return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and _RUN_GATE_PATH.match(node.value) and _inside_call(node):
            return True                       # os.path.join(..., "run_gate.py") / "harness/run_gate.py"
    return False


def _load_coverage():
    p = os.path.join(ROOT, "harness", "coverage.py")
    spec = importlib.util.spec_from_file_location("harness_coverage_t", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


COV = _load_coverage()


def _run(cmd, cwd=None, env=None):
    e = dict(os.environ)
    e.setdefault("PYTHONUTF8", "1")
    e.pop("HARNESS_STATS_FILE", None)
    if env:
        e.update(env)
    return subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, env=e)


# 快跑（HARNESS_FAST=1）：只跳过**慢的链路集成类**（子进程/git/真库那些），纯逻辑与文件锁照跑。
# 第四轮外审 ①：快跑开关原先只接了 test_hooks_chain（14 例），新加的这两个文件没接，
# 快跑从 4.1s 退化到 19.4s —— 开关价值被稀释。按实测耗时决定跳过谁（>1s 的类）。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, f"快速模式（HARNESS_FAST=1）跳过{reason}")


class TestAggregateAndClassify(unittest.TestCase):
    """聚合 + 分类学（纯函数，喂合成记录）。"""

    def setUp(self):
        self.cfg = {
            "pcA": {"enabled": True, "endpoint": "pc", "disabled": [], "repo": "a"},
            "pcB": {"enabled": True, "endpoint": "pc", "disabled": ["PC-IP"], "repo": "b"},
            "h5A": {"enabled": True, "endpoint": "h5", "disabled": [], "repo": "c"},
        }
        self.rules = {
            "PC-CONSOLE": {"name": "残留 console.log", "endpoint": "pc"},
            "PC-IP": {"name": "硬编码 IP", "endpoint": "pc"},
            "H5-CONSOLE": {"name": "console", "endpoint": "h5"},
        }
        self.records = [
            {"ts": "2026-09-12T10:00:00", "gate": "pcA", "scope": "targeted", "rc": 1, "ms": 100, "files": 2,
             "rules": {"PC-CONSOLE": {"error": 3}, "PC-IP": {"waived_legacy": 2}}},
            {"ts": "2026-09-12T11:00:00", "gate": "pcA", "scope": "targeted", "rc": 0, "ms": 200, "files": 1, "rules": {}},
            {"ts": "2026-09-12T12:00:00", "gate": "pcB", "scope": "targeted", "rc": 0, "ms": 300, "files": 1,
             "rules": {"PC-CONSOLE": {"warn": 1, "waived_inline": 1}}},
        ]

    def test_counts_and_waiver_rate(self):
        rep = COV.build_report(self.records, self.cfg, self.rules)
        rows = {r["rule"]: r for r in rep["rules"]}
        c = rows["PC-CONSOLE"]                       # 两 gate 汇总：error 3 + warn 1，豁免 1
        self.assertEqual((c["error"], c["warn"]), (3, 1))
        self.assertEqual(c["waived_inline"], 1)
        self.assertEqual(c["waiver_rate"], round(1 / 5, 3))   # 命中 4 + 豁免 1
        self.assertEqual(c["status"], "活跃")
        self.assertEqual(rep["coverage"]["fired"], 1)

    def test_status_taxonomy(self):
        rep = COV.build_report(self.records, self.cfg, self.rules)
        rows = {r["rule"]: r["status"] for r in rep["rules"]}
        # PC-IP 只在 pcB 被禁用，而 pcA 并未禁用 → 不算"按 gate 禁用"；pcA/pcB 都跑过且它一次没响
        self.assertEqual(rows["PC-IP"], "从未触发（需确认：不适用 or 失效）")
        # h5 端窗口内一次没跑过 → 不能对 H5 的规则下任何结论
        self.assertEqual(rows["H5-CONSOLE"], "该端窗口内无运行")

    def test_disabled_only_when_all_gates_disable(self):
        cfg = dict(self.cfg)
        cfg["pcA"] = dict(cfg["pcA"], disabled=["PC-IP"])
        rep = COV.build_report(self.records, cfg, self.rules)
        rows = {r["rule"]: r["status"] for r in rep["rules"]}
        self.assertEqual(rows["PC-IP"], "按 gate 禁用")

    def test_never_fired_when_endpoint_ran(self):
        rep = COV.build_report(self.records, self.cfg, self.rules)
        rows = {r["rule"]: r["status"] for r in rep["rules"]}
        self.assertEqual(rows["PC-IP"], "从未触发（需确认：不适用 or 失效）")

    def test_gate_overview_and_window(self):
        rep = COV.build_report(self.records, self.cfg, self.rules)
        by = {g["gate"]: g for g in rep["gates"]}
        self.assertEqual(by["pcA"]["runs"], 2)
        self.assertEqual(by["pcA"]["blocked"], 1)
        self.assertEqual(by["pcA"]["avg_ms"], 150)
        self.assertEqual(by["h5A"]["runs"], 0)


class TestScopeSplit(unittest.TestCase):
    """③ 第四轮外审：全量扫描(观察) 与 定向(真拦截) 必须分列，否则数字失衡、豁免率失真。"""

    def setUp(self):
        self.cfg = {"pcA": {"enabled": True, "endpoint": "pc", "disabled": [], "repo": "a"}}
        self.rules = {"PC-CONSOLE": {"name": "console", "endpoint": "pc"},
                      "PC-TODO": {"name": "todo", "endpoint": "pc"}}
        self.records = [
            # 定向：真拦了 2 处，另有 2 处按行豁免 → 豁免率 0.5
            {"ts": "2026-09-12T10:00:00", "gate": "pcA", "scope": "targeted", "rc": 1, "ms": 10, "files": 1,
             "rules": {"PC-CONSOLE": {"error": 2, "waived_legacy": 2}}},
            # 全量：存量一大片（观察，不该影响拦截判断）
            {"ts": "2026-09-12T11:00:00", "gate": "pcA", "scope": "all", "rc": 1, "ms": 9000, "files": 900,
             "rules": {"PC-CONSOLE": {"error": 500}, "PC-TODO": {"error": 42}}},
        ]

    def test_columns_split_and_waiver_rate_uses_targeted(self):
        rep = COV.build_report(self.records, self.cfg, self.rules)
        row = {r["rule"]: r for r in rep["rules"]}["PC-CONSOLE"]
        self.assertEqual(row["targeted"]["error"], 2, "定向计数不对")
        self.assertEqual(row["full"]["error"], 500, "全量计数不对")
        self.assertEqual(row["error"], 502, "合计应为两口径之和")
        self.assertEqual(row["waiver_rate"], 0.5, "豁免率必须只按定向口径算（2 豁免/(2 命中+2 豁免)）")
        self.assertTrue(row["fired_targeted"])

    def test_only_full_fired_is_called_out(self):
        rep = COV.build_report(self.records, self.cfg, self.rules)
        text = COV.render_text(rep)
        # PC-TODO 只在全量响过 → 必须被单独点名（日常提交路径 0 拦截）
        row = {r["rule"]: r for r in rep["rules"]}["PC-TODO"]
        self.assertFalse(row["fired_targeted"])
        self.assertIn("只在全量口径响过", text)
        self.assertIn("PC-TODO", text)
        self.assertIn("全量观察", text, "表头应分口径")

    def test_gate_overview_shows_scope_split(self):
        rep = COV.build_report(self.records, self.cfg, self.rules)
        text = COV.render_text(rep)
        self.assertIn("定向 1 / 全量 1", text)

    def test_hot_waiver_line_is_self_consistent(self):
        """N9 回归锁：高豁免率那行打印的分子分母，必须能复算出**同一个**百分比。

        这条锁是「读者视角」的：报告里印了数字又印了百分比，读者就会去算一遍。
        原先打印的是合计口径的 `豁免/(error+warn+info)`，而 waiver_rate 取的是定向口径的
        `wv/(命中+豁免)` —— 两个数都错，且分母语义也不同（印的是「命中」，rate 的分母是
        「命中+豁免」），于是**怎么算都对不上**。这里从渲染出的文本里把数字抠出来复算，
        而不是去读 row 内部字段：字段对了但打印错了正是本缺陷的形状。
        """
        import re
        rep = COV.build_report(self.records, self.cfg, self.rules)
        row = {r["rule"]: r for r in rep["rules"]}["PC-CONSOLE"]
        self.assertGreaterEqual(row["waiver_rate"], 0.5, "前置：该规则应落进高豁免率清单")
        text = COV.render_text(rep)
        m = re.search(r"PC-CONSOLE：豁免 (\d+) / 总计 (\d+)（命中 (\d+) \+ 豁免 (\d+)）= ([\d.]+)%", text)
        self.assertIsNotNone(m, "找不到高豁免率那行（改措辞请同步本锁）：\n%s" % text)
        wv, tot, hits, wv2, pct = (float(x) for x in m.groups())
        # ① 行内自洽：命中 + 豁免 = 总计
        self.assertEqual(hits + wv2, tot, "行内「命中 + 豁免 ≠ 总计」")
        # ② 行内自洽：分子分母算出的百分比 = 印出来的百分比
        self.assertAlmostEqual(wv / tot * 100, pct, places=1,
                               msg="打印的 豁免/总计 复算不出打印的百分比（口径或分母不一致）")
        # ③ 与 row 字段同源：复算值必须等于 waiver_rate（否则同一事实两个说法）
        self.assertEqual(round(wv / tot, 3), row["waiver_rate"],
                         "打印口径与 waiver_rate 口径不一致")
        # ④ 反向：旧形状（合计口径的「豁免 N / 命中 M」）不许复活 —— 只锁新形状的话，
        #    下次有人改回旧写法、锁仍然绿。
        self.assertNotIn("：豁免 4 / 命中 502", text, "高豁免率那行退回合计口径了")
        self.assertIsNone(re.search(r"：豁免 \d+ / 命中 \d+\s*$", text, re.M),
                          "又出现「豁免 N / 命中 M」这种读者复算不出的写法")


class TestRecordLoading(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="covlog_")
        self.log = os.path.join(self.dir, "log.jsonl")
        recs = [
            {"ts": "2026-01-01T00:00:00", "gate": "g1", "rc": 0},
            {"ts": "2026-09-12T10:00:00", "gate": "g1", "rc": 0},
            {"ts": "2026-09-12T11:00:00", "gate": "g2", "rc": 1},
            {"ts": "坏行不是 json"},
        ]
        with open(self.log, "w", encoding="utf-8", newline="") as f:
            for r in recs:
                f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")

    def tearDown(self):
        _rmtree_force(self.dir)

    def test_last_n(self):
        self.assertEqual([r["gate"] for r in COV.load_records(self.log, last=2)], ["g1", "g2"])

    def test_bad_lines_skipped(self):
        self.assertEqual(len(COV.load_records(self.log)), 3)

    def test_missing_file(self):
        self.assertEqual(COV.load_records(os.path.join(self.dir, "nope.jsonl")), [])

    def test_days_filter(self):
        old = COV.load_records(self.log, days=1)
        self.assertTrue(all(r["ts"] >= "2026-09-11" for r in old))


class TestHonestWithoutData(unittest.TestCase):
    """没有规则级数据时**不许判定**——数据缺失 ≠ 从未触发（第三轮那种"空过"的教训）。"""

    def setUp(self):
        self.cfg = {"pcA": {"enabled": True, "endpoint": "pc", "disabled": [], "repo": "a"}}
        self.rules = {"PC-CONSOLE": {"name": "console", "endpoint": "pc"}}
        self.recs = [{"ts": "2026-09-12T10:00:00", "gate": "pcA", "rc": 0, "ms": 10, "files": 1}]

    def test_text_warns_and_defers(self):
        rep = COV.build_report(self.recs, self.cfg, self.rules)
        self.assertFalse(rep["has_rule_data"])
        txt = COV.render_text(rep, "mem")
        self.assertIn("没有规则级数据", txt)
        self.assertIn("没有数据 ≠ 从未触发", txt)
        self.assertNotIn("从未触发 · 1 条", txt)      # 不许把"没数据"渲染成"从未触发"
        self.assertIn("gate 运行概览", txt)

    def test_with_data_renders_sections(self):
        recs = [dict(self.recs[0], rules={"PC-CONSOLE": {"error": 2}})]
        rep = COV.build_report(recs, self.cfg, self.rules)
        txt = COV.render_text(rep, "mem")
        self.assertIn("【覆盖度】", txt)
        self.assertIn("PC-CONSOLE", txt)
        self.assertIn("100.0%", txt)


@_slow("覆盖度链路集成（起子进程跑 run_gate）")
class TestInstrumentationEndToEnd(unittest.TestCase):
    """埋点必须真的工作：跑一次真实门禁 → 规则级数据进 gate_log（传感器自己也要被监考）。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="covinst_")
        self.repo = os.path.join(self.dir, "repo_pc")
        os.makedirs(os.path.join(self.repo, "src"), exist_ok=True)
        with open(os.path.join(self.repo, "src", "demo.vue"), "w", encoding="utf-8", newline="") as f:
            f.write('<template>\n  <div>x</div>\n</template>\n<script setup>\nconsole.log("d");\n</script>\n')
        self.cfg = os.path.join(self.dir, "cfg.yaml")
        with open(self.cfg, "w", encoding="utf-8", newline="") as f:
            f.write("repos:\n  t_pc: %s\ngates:\n  t_pc:\n    enabled: true\n    repo: t_pc\n"
                    "    script: checks/example_code_check.py\n    src: src\n    mode: strict\n"
                    "    run_on: [pre_commit, post_edit]\n" % self.repo.replace("\\", "/"))
        self.file = os.path.join(self.repo, "src", "demo.vue")
        self.log = os.path.join(self.dir, "log.jsonl")

    def tearDown(self):
        _rmtree_force(self.dir)

    def test_check_script_writes_stats_when_asked(self):
        stats = os.path.join(self.dir, "stats.json")
        r = _run([PY, os.path.join(ROOT, "checks", "example_code_check.py"), "--gate", "t_pc",
                  "--repo", self.repo, self.file],
                 env={"HARNESS_STATS_FILE": stats, "HARNESS_CONFIG": self.cfg})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(stats), "设了 HARNESS_STATS_FILE 就该写出统计")
        with open(stats, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("NO-CONSOLE", data["rules"], data)
        self.assertEqual(data["gate"], "t_pc")
        self.assertEqual(data["scope"], "targeted")

    def test_no_stats_file_when_not_asked(self):
        """零影响契约：不设环境变量 → 不产生任何统计文件。"""
        r = _run([PY, os.path.join(ROOT, "checks", "example_code_check.py"), "--gate", "t_pc",
                  "--repo", self.repo, self.file], env={"HARNESS_CONFIG": self.cfg})
        self.assertEqual(r.returncode, 1)
        stray = [f for f in os.listdir(self.dir) if f.startswith("harness_stats")]
        self.assertEqual(stray, [], f"直跑不该留下统计文件：{stray}")

    def test_run_gate_merges_rules_into_log(self):
        r = _run([PY, os.path.join(ROOT, "harness", "run_gate.py"), "t_pc", self.file],
                 env={"HARNESS_CONFIG": self.cfg, "HARNESS_LOG_FILE": self.log})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        with open(self.log, encoding="utf-8") as f:
            rec = json.loads(f.readline())
        self.assertIn("rules", rec, "run_gate 应把子进程统计并入运行记录")
        self.assertIn("NO-CONSOLE", rec["rules"])
        self.assertEqual(rec["gate"], "t_pc")

    def test_coverage_main_json_smoke(self):
        _run([PY, os.path.join(ROOT, "harness", "run_gate.py"), "t_pc", self.file],
             env={"HARNESS_CONFIG": self.cfg, "HARNESS_LOG_FILE": self.log})
        # 自评脚本与工程其它部分同一约定：配置走 HARNESS_CONFIG 覆盖层（测试才指向临时 config）
        old = os.environ.get("HARNESS_CONFIG")
        os.environ["HARNESS_CONFIG"] = self.cfg
        try:
            rc = COV.main(["--log", self.log, "--json"])
            self.assertEqual(rc, 0)
            rep = COV.build_report(COV.load_records(self.log), COV.load_config(), COV.static_rules())
        finally:
            if old is None:
                os.environ.pop("HARNESS_CONFIG", None)
            else:
                os.environ["HARNESS_CONFIG"] = old
        self.assertTrue(rep["has_rule_data"])
        self.assertGreaterEqual(rep["coverage"]["fired"], 1)


class TestStaticCatalog(unittest.TestCase):
    """静态清单必须覆盖示例规则集（否则某些规则在自评里隐身）。"""

    def test_example_rules_present(self):
        rules = COV.static_rules()
        eps = {v["endpoint"] for v in rules.values()}
        self.assertEqual(eps, {"example"}, eps)
        for rid in ("NO-CONSOLE", "NO-HARDIP", "EOL-UNIFIED"):
            self.assertIn(rid, rules)

    def test_catalog_follows_registry_registration(self):
        """新增端只登记 registry，不许再改 coverage —— 反推链断了这里就红。"""
        import registry
        expect = {os.path.basename(s): ep for ep, s in registry.ENDPOINT_SCRIPTS.items()}
        self.assertEqual(COV.ENDPOINT_BY_SCRIPT, expect,
                         "coverage 的端标签必须从 registry.ENDPOINT_SCRIPTS 反推")
        self.assertEqual(COV.RULESETS, registry.RULESETS,
                         "coverage 的规则集来源必须直接取 registry.RULESETS")

    def test_newly_registered_ruleset_becomes_visible(self):
        """模拟新增一个 gate：只登记 ruleset + 端标签，static_rules 必须自动看到它的规则。"""
        tmpdir = tempfile.mkdtemp(prefix="cov-ruleset-")
        self.addCleanup(_rmtree_force, tmpdir)
        mod_name = "_tmp_new_gate_ruleset"
        with open(os.path.join(tmpdir, mod_name + ".py"), "w", encoding="utf-8") as f:
            f.write("class _R:\n"
                    "    id = 'NEW-GATE-ONE'\n"
                    "    message = 'new gate rule'\n"
                    "NEW_GATE_RULES = [_R()]\n")
        sys.path.insert(0, tmpdir)
        old_rules, old_eps = COV.RULESETS, COV.ENDPOINT_BY_SCRIPT
        COV.RULESETS = {"new_gate_check.py": mod_name}
        COV.ENDPOINT_BY_SCRIPT = {"new_gate_check.py": "newgate"}
        try:
            rules = COV.static_rules()
        finally:
            COV.RULESETS, COV.ENDPOINT_BY_SCRIPT = old_rules, old_eps
            sys.path.remove(tmpdir)
        self.assertIn("NEW-GATE-ONE", rules, "新登记的规则集没有进入自评清单（覆盖度会瞎）")
        self.assertEqual(rules["NEW-GATE-ONE"]["endpoint"], "newgate")

    def test_loader_level_checks_included(self):
        """loader 级检查（非 Rule 实例）也必须在册，否则在报告里隐身。"""
        rules = COV.static_rules()
        # 示例门禁现在没有 loader 级检查（3 条全是 Rule 实例），清单应为空而不报错。
        self.assertEqual(COV.LOADER_LEVEL_CHECKS, {})
        self.assertIn("EOL-UNIFIED", rules, "行尾检查是示例门禁的实际规则，必须在自评清单里")

    def test_all_loader_level_bump_ids_are_in_catalog(self):
        """机械锁（不靠人眼）：checks/*.py 里所有 `bump("<字面量>")` 的 ID 都必须在清单里。

        将来谁再在 loader 里加一条检查却忘了登记清单，这条立刻红——比"人工复核清单"可靠。
        动态 ID（`bump(rule.id, ...)`）由 ruleset 侧被 static_rules 自动收集，这里只管字面量。
        """
        import re as _re
        checkdir = os.path.join(ROOT, "checks")
        literal = _re.compile(r'bump\(\s*"([^"]+)"')
        # 扫描器自检：统一 Rule API 后 loader 已没有字面量埋点，空集是合法结果；
        # 用合成文本证明正则没坏，否则"扫不到"会被误读成"没问题"。
        self.assertEqual(literal.findall('bump("SYNTHETIC", "error")'), ["SYNTHETIC"])
        ids, where = {}, {}
        for name in sorted(os.listdir(checkdir)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(checkdir, name), encoding="utf-8") as f:
                src = f.read()
            for m in literal.finditer(src):
                ids[m.group(1)] = True
                where.setdefault(m.group(1), name)
        catalog = set(COV.static_rules())
        missing = sorted(set(ids) - catalog)
        self.assertEqual(missing, [],
                         f"这些 loader 级埋点 ID 不在自评清单里（会在报告里隐身）："
                         f"{ {m: where[m] for m in missing} }")

    def test_targeted_only_table_is_consistent(self):
        """口径标注不能"写死就不管"：表里每个 ID 都必须是清单里的真规则。"""
        self.assertEqual(COV.TARGETED_ONLY, {},
                         "开源裁剪后没有「只在定向口径可能触发」的规则；新增请同时补 loader 依据")
        catalog = set(COV.static_rules())
        self.assertEqual(sorted(set(COV.TARGETED_ONLY) - catalog), [],
                         "TARGETED_ONLY 里出现了清单外的规则 ID")

    def test_extra_rules_surfaced(self):
        """第四轮 N-4：清单外数据不许静默丢弃，必须单独成表。"""
        cfg = {"pcA": {"enabled": True, "endpoint": "pc", "disabled": [], "repo": "a"}}
        rules = {"PC-CONSOLE": {"name": "console", "endpoint": "pc"}}
        recs = [{"ts": "2026-09-12T10:00:00", "gate": "pcA", "rc": 1, "ms": 10, "files": 1,
                 "rules": {"PC-CONSOLE": {"error": 1}, "PC-幽灵规则": {"error": 2, "waived_legacy": 1}}}]
        rep = COV.build_report(recs, cfg, rules)
        extra = {r["rule"]: r for r in rep["extra_rules"]}
        self.assertIn("PC-幽灵规则", extra, "清单外数据没进 extra_rules（又静默丢弃了）")
        self.assertEqual(extra["PC-幽灵规则"]["error"], 2)
        text = COV.render_text(rep)
        self.assertIn("清单外规则级数据", text)

    def test_waiver_columns_are_labeled_honestly(self):
        """第四轮 N-5：两列豁免 + info 列的口径要有说明，别让读者把整文件降级当普通观察项。"""
        cfg = {"pcA": {"enabled": True, "endpoint": "pc", "disabled": [], "repo": "a"}}
        rules = {"PC-CONSOLE": {"name": "console", "endpoint": "pc"}}
        recs = [{"ts": "2026-09-12T10:00:00", "gate": "pcA", "rc": 1, "ms": 10, "files": 1,
                 "rules": {"PC-CONSOLE": {"error": 1, "waived_legacy": 5}}}]
        text = COV.render_text(COV.build_report(recs, cfg, rules))
        self.assertIn("按行豁免", text)
        self.assertNotIn("存量豁免", text, "列名仍叫'存量豁免'（与 info 列的整文件降级混淆）")
        self.assertIn("整文件", text, "缺 legcay 整文件降级落在 info 列的说明")


class TestIsolationAndScripts(unittest.TestCase):
    """第四轮 N-2 / N-3 的源码级锁：测试别污染真实日志；脚本的 rc 要取真退出码。"""

    def test_tests_set_harness_log_file(self):
        """任何**真的会调 `run_gate`** 的测试文件，都必须把运行日志钉到临时文件（第四轮 N-2）。

        判据取自**语法树**，不是 grep 源码里的字面（2026-09-24 收窄，第四轮 N-2 的判据订正）：
        原先只要文件里出现 `run_gate` 这七个字母，就要求它设 `HARNESS_LOG_FILE` —— 比本条
        自己声称的那件事（"会**调**"）宽了一档：**docstring / 注释里提一句文件名不是调用**。
        现场（2026-09-24）：`tests/test_lock_inventory.py` 新加的锚点看守 docstring 里写了
        「M44：H1 把 `harness/run_gate.py` 的 `_log_run` 调用点拆成多行」，就被判成
        "会调 run_gate 却没设 HARNESS_LOG_FILE"，而该文件一行都没调过它 ——
        本机与 Linux 同时红，release 的「全量自检」正是死在这条假警上。

        ★ 收窄只会**少报假警**，不会漏掉真调用：真调用一定落在语法树里
        （`import run_gate` / `run_gate.xxx` / **调用参数里**那条模块路径），
        而"两种形态都不在语法树里"的调用不存在。
        ⚠ 判据边界（有意留的，写在这里免得后人以为它没洞）：写成**带参数的整串**命令行
        （`"…/run_gate.py --all"`）不在判据内 —— 本仓现有写法都是把路径单独成串
        （`os.path.join(..., "run_gate.py")`），若将来出现整串写法，这里要跟着放宽。
        ⚠ 第二条边界（2026-10-21 补）：**数据里的路径字符串不算调用**——期望成员名、
        文件名常量放在元组/列表里赋值，不会执行任何东西。现场是 `test_release_artifact.py`
        的 `ENGINE_PROBE`。真正的命令行写法仍会被抓（路径必然出现在调用参数里）。
        """
        tdir = os.path.join(ROOT, "tests")
        for dirpath, dirnames, filenames in os.walk(tdir):
            dirnames[:] = sorted(d for d in dirnames
                                 if os.path.isfile(os.path.join(dirpath, d, "__init__.py")))
            for name in sorted(filenames):
                if not name.endswith(".py") or not name.startswith("test_"):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, name), tdir).replace("\\", "/")
                with open(os.path.join(dirpath, name), encoding="utf-8") as f:
                    src = f.read()
                if not _code_calls_run_gate(src):
                    continue
                self.assertIn("HARNESS_LOG_FILE", src,
                              f"tests/{rel} 会调 run_gate 却没设 HARNESS_LOG_FILE —— "
                              f"探针记录会写进真实的 reports/gate_log.jsonl（N-2 实测污染 124 条）")

    def test_the_run_gate_criterion_is_not_vacuous(self):
        """上面那条的判据本体两向都验：拿**合成文本**喂 `_code_calls_run_gate`。

        少了这一条，"没有违规文件"可能只是因为判据永远返回 False —— 那就又成了一条空锁。
        四个"算调用"的形态各验一遍（真调用就长这样），四个"不算调用"的形态也各验一遍
        （"散文假警原样"与"数据表里的期望成员名"都必须不再触发）。
        """
        yes = [
            'import run_gate\n',                                            # 直接 import
            'from harness import run_gate\n',                               # 从包里取
            'def t():\n    run_gate.main(["--all"])\n',                     # 直接调
            'def t():\n    run_gate.LOG_DIR = tmp\n',                       # 改它的模块状态
            'import os\np = os.path.join(ROOT, "harness", "run_gate.py")\n',  # 命令行：路径单独成串
        ]
        for src in yes:
            self.assertTrue(_code_calls_run_gate(src), "漏判了真调用形态：%r" % src)
        no = [
            '# 这段注释提到 run_gate，但没调它\nX = 1\n',                     # 注释
            'def t():\n    """提到 run_gate 的 docstring"""\n    pass\n',     # 本文件自己的 docstring
            'def t():\n    """M44：H1 把 `harness/run_gate.py` 的 `_log_run` 调用点拆成多行"""\n    pass\n',
            'msg = "run_gate 应把子进程统计并入运行记录"\n',                   # 断言消息（散文）
            'ENGINE_PROBE = ("agentguard/_engine/harness/run_gate.py",)\n',  # 数据表：期望成员名
        ]
        for src in no:
            self.assertFalse(_code_calls_run_gate(src),
                             "把散文里的提及当成调用了（假警复发）：%r" % src)

    def test_slow_test_files_wire_the_fast_switch(self):
        """① 第四轮外审：慢的链路集成测试文件必须接 HARNESS_FAST（否则快跑档被稀释）。

        维护规则（2026-09-17 按实测修订——原口径「整文件 >5s」与既有实践矛盾，见下）：
          **新增测试文件时先逐例量耗时**——满足「整文件 >5s」**且**「其中有单例 ≥1s」的才接
          `@_slow(...)`，并把文件名加进下面这张表。
          只满足前一半的是**摊薄型**（几十个小例各 <1s），不接：它们就是本节的"单测"，
          快跑档本该跑。旧口径把「1 个用例 46s」和「41 个用例各 0.3s」当成同一类，
          而 test_checks.py（8.2s，>5s 却没接）早已按后者办——**规则与既有实践不一致，
          下一个人会照字面办**，所以把字面改对。
          判据是"省下的秒数"，不是"文件的名义耗时"。
        装饰落点（2026-09-17 批 2 补——上面两条只说到了文件级，落点还有两个坑）：
          · **落到类上时沿用同口径**：该类没有 ≥1s 的单例就不接。省不下多少秒，
            而那些"个个 0.5~0.8s"的类往往钉的是好东西（例：v13_batch3 里
            「disabled 的 gate 不许被记成 pass」、v13_batch2 里 guard 的极性分支）。
          · **同类里若混着纯源码级锁（<0.1s 只读文件那种），改用方法级**：只装饰慢的那些。
            类级会把它们一起清掉，而"有人把链式调用删了"恰恰会在最快的档位下无人发现
            （例：test_review_v8_batch2.py 的 TestB1HooksPathChain 6 例里，
            3 例端到端 12.8/6.0/2.1s + 3 例源码级 0.00~0.03s）。
        不接的（有测量记录，别再顺手接上）：
          · test_checks.py 8.2s —— 15 个类最慢 1.65s，纯逻辑锁（第四轮外审 ① 明确要求不接）；
          · test_release_artifact.py 25.7s —— 最慢单例 2.0s，41 例摊薄；且兜着 dist/ 真产物锁；
          · test_lock_inventory.py 7.4s —— 用例只占 1.8s，其余是导入开销。
          · test_review_v8_batch3.py 7.1~8.0s（批 5 空载三跑；19 例里 2 例 ≥1s 就占 ≈2.9s，
            约四成。批 4 那次读到 9.2~10.9s / 5 例 ≥1s 是同一条坑的另一端，见下坑③）
            —— ★ **它不是摊薄型**（见下），不接的理由是**牙齿**不是秒数：它兜着**两条变异的
            唯一判据** —— M17 的
            `test_run_on_whitelist_equals_actually_consumed_triggers`（0.023s，纯源码级比对
            run_on 白名单与实际被消费的触发器）与 M19 的
            `test_self_gate_probe_writes_nothing_into_the_engineering_tree`（**1.0~1.6s**）。
            按"只装饰 ≥1s 的例"这条口径接线，会**正好把 M19 那条判据清掉**（它就是 ≥1s），
            等于让 M19 在快跑档变盲。将来若要接，唯一正确的接法是"接别的几条、把这两条
            显式留跑"，**别整类级** —— 类级会连 M17 那条 0.023s 的一起清掉。
            ⚠ **2026-09-17 批 5 复量：那个"正确接法"实测收益为 0 —— 本项已了结，别重开。**
            它确实省得下秒数（7.3 → 1.5s），但那些秒**全在快跑档的关键路径之外**：带时间戳的
            调度探针实测该文件 16.98s 就跑完，墙钟却到 32.92s；反事实模拟"7.3 → 1.5s / → 0.5s"
            两次墙钟 **Δ 都是 +0.00s**（同耗时集合重放 32.91s vs 实测 32.92s，模型先自证过）。
            证据与工具在 gitignore 的 reports/ 里：`_batch5_tail_probe.txt` / `_fast_tail_probe.py`。
            ★ **由此补出的判据边界（比"省得下多少秒"更要紧）**：省下的秒数只有落在**同一档的
            关键路径**上才值钱 —— "整文件 >5s 且 有单例 ≥1s"只说明它贵，**不说明有谁在等它**；
            接线前先问一句"这些秒在并行调度里排第几"。本文件是第一个反例：按旧口径该接、
            接了 0 收益。（当时那根真杆是 `test_review_v13_batch4.py`：自身 26.2s、结束最晚，
            反事实 −8.20s；但它是摊薄型、要削得清掉 44 例判据，故仍不接。）
            （初判此文件时我按旧读数写成"摊薄、最慢 1.41s"并据此建议不接；批 4 复量推翻了
            那个理由，结论碰巧不变但**依据换了** —— 这正是"先量再断言"那条教训的又一次。）
         ★ 批 6（2026-09-17）：`test_review_v13_batch4.py` **已接，从上面那份不接清单里移出**。
            它是快跑档**结束最晚的那根杆**（争用下 22.8~26.2s；单跑 12.28s / 44 例），
            也是当时唯一还能推动墙钟的文件。44 例里只有 2 例 ≥1s，**只装饰其中一条**：
              · test_end_to_end_delete_only_...（中C 的**可观测后果**，1.47s）→ 装饰。底层语义
                （compute_changed_lines 三态）本文件另有 3 例亚秒单元锁留跑，另有 4 份**已接**
                文件覆盖同一函数（test_entry_level / v13_batch1 / v9_batch1 / test_staged_content）
                —— 不是唯一判据，可跳。
              · test_every_declared_rule_id_is_actually_disableable（2.01s）→ **刻意留跑**：
                全仓锁「loader 级伪规则 `if ... not in off` 守卫」的**只有它一条**
                （`git grep "not in off" tests/` 仅命中它自己；test_checks.py 的
                TestDisabledRules 验的是 ruleset 规则 H5-UPLOAD，走的是另一条代码路径）。
                低19/中E 是**静默失效**型缺陷（写了 `disabled_rules` 照样拦、没有任何症状），
                唯一牙齿不进快跑档跳过名单 —— 同批 3/批 4 两次返工的理由。
            墙钟：空载档实测 32.90s，按**同一跑**的耗时重放、只装饰这一条后为 29.77s
            （Δ−3.13s；重放模型自证：同集合重放 32.90 vs 实测 32.92）。若两条都装饰是 −7.41s，
            但会送掉那条唯一牙齿（拿 4.28s 换一个"快跑档看不见的静默失效"，不划算）。
            ⚠ **这笔收益分档位，别当成无条件**：机器有负载时**杆会换人** —— 本机负载跑实测
            墙钟 41.1s，那一跑里 `test_release_artifact.py` 自身涨到 34.6s 成了杆，
            `v13_batch4` 提前 9s 完成，此时削它 **Δ0.00s**（`release_artifact` 比它更吃负载）。
            即：空载档值 ~2.9s，负载档值 0。
            ⚠ **接完之后快跑档的杆就是 `test_release_artifact.py`（空载档 20.1s / 负载档 34.6s，
            11 条变异锁着它 + dist/ 真产物锁）→ 约 24.7s 就是这套快跑档的下限**，别再往那儿找收益。
            证据：`reports/_batch5_tail_probe.txt`、`_batch5_replay*.txt`、`_batch6_tail_probe.txt`、
            `_batch6_wiring_effect.txt`、`_slow_wiring_batch6.txt`。
        锁的作用：谁把这几个文件里的开关删了/改名了，立刻红。
        已知边界（两条，都只能靠人补，故写在这里）：
          ① **新出现的慢性文件**：本锁只保证**名单里**的文件还带开关，不保证"新出现的慢性
             文件会被登记"——无法在源码级判断耗时，只能由上面那条维护规则由人执行。
          ② **名单里的文件"半接"**（批 2 量出、**批 3 已接，但边界会复发**）：本锁 grep 的是
             `HARNESS_FAST` 这个**字符串**，不看接在哪几个类上，于是"接了开关"≠"接全了"。
             批 2 量出三份半接的（快跑档单跑；下表 = 照跑例数 / 其中 ≥1s 的例数与合计）：
               test_entry_level.py  37.72s（照跑 34 例，其中 18 例 ≥1s = 35.5s）
               test_hooks_chain.py  14.66s（照跑 11 例，4 例 ≥1s = 10.7s）
               test_review_v6.py    14.58s（只跳了那条 46.5s 的，另 7 例 ≥1s = 13.9s）
             小计 ≈67s —— **比名单外未接的三条（≈28.7s）还大**（取舍见
             `reports/_slow_wiring_batch2.txt` §⑨）。**批 3（2026-09-17）已按"只装饰 ≥1s 的"
             接完这三份** → 快跑档单跑降到 ≈3.3 / 2.1 / 0.6s，且**三份都做到 0 例 ≥1s**
             （全量档 55 / 22 / 19 例、跳 0；证据：`reports/_slow_wiring_batch3.txt`）。
             **但这条边界本身不消失、将来还会复发**，所以留在这里。
             复发时唯一的发现办法：**快跑档下单跑那几份文件**，看还有多少 ≥1s 的例在跑。
             ★ 三个量法坑（都踩过）：① 别读 `run_tests.py --fast` 并行档打印的每文件耗时，
             那里带争用（同一份 test_entry_level 并行 82.1s / 单跑 37.7s）；
             ② 也别拿"单例单跑"的数当判据 —— 同一例单跑与**类内**能差一倍（批 3 实测
             run_gate_unregistered_gate_rc2：单跑 0.568s / 类内 1.17s，同类临时 git 仓会累积）。
             **一律以"类内、快跑档单跑整文件"的读数为准。**
             ③ **这 1s 线本身在"用例挤在 1s 附近"的文件上不重复**：同一份
             `test_review_v8_batch3.py` 连量四次（批 4，本机，同一台机器、同一份文件），
             ≥1s 的例数是 **5 / 3 / 4 / 2**，墙钟 9.15 / 9.34 / 10.22 / 10.86s —— 判据能差 2.5 倍。
             所以别拿一次读数去切 1s 线：批 4 在 `test_review_v13_batch1.py` 上就遇到
             一例 1.006s 与一例 0.979s（同一种形状、同一笔开销），按线切开纯属按噪声切。
             问的应该是"**这段秒数省不省得下来**"和"**它是不是某条变异的唯一牙齿**"，
             不是"这次量到几点几秒"。
             **刻意不补看守**（用户 2026-09-17 拍板）：真判据是耗时，套件里量不起；源码级
             只剩"装饰器有没有 / 几条"这类代理判据，而**仓库里已经躺着一条活的反例**——
             就是本节末对 test_entry_level 的 `assertGreaterEqual(count("@_slow("), 5)`：
             批 3 之前它一直是绿的，而同一份文件当时快跑档仍留着 35.5s。再补一条同样验不出
             东西的看守，只会给人"这条已经守住了"的错觉 —— **假平安比不守更坏**，故只登记。
        """
        must_wire = ("test_hooks_chain.py", "test_coverage.py", "test_entry_level.py",
                     "test_contracts.py", "test_review_v13_batch5.py",
                     "test_review_v13_batch6.py", "test_review_v13_batch8.py",
                     # 2026-09-17 批 1：实测漏接的三个最贵的（56.7 / 38.1 / 31.6s），
                     # 在快跑档里正是最后完成的三个（= 快跑档的关键路径）
                     "test_staged_content.py", "test_review_v9_batch1.py",
                     "test_review_v9_install_locks.py",
                     # test_review_v6.py 早已内联接了开关（:245 的 skipTest），却没进名单 ——
                     # 等于它的开关无人看守；它那一条独占 46.5s（全文件 58.7s），补进来
                     "test_review_v6.py",
                     # 2026-09-17 批 2：余下 >10s 的五条（逐例量过：29.6 / 27.5 / 25.8 /
                     # 21.1 / 15.5s；每份都满足"整文件 >5s 且 有单例 ≥1s"）
                     "test_review_v13_batch2.py", "test_review_v13_batch3.py",
                     "test_agentguard.py", "test_review_v8_batch2.py",
                     "test_audit_reconcile.py",
                     # 2026-09-17 批 4：余下 >5s 且 有单例 ≥1s 的五条（快跑档单跑复量：
                     # test_upgrade_path 10.7~15.3 / test_review_v9_batch3 10.2~17.5 /
                     # test_upgrade 8.8~12.0 / test_review_v13_batch1 6.5~8.2 /
                     # test_review_v9_batch2 6.6~8.0s）。落点见各文件头的"快跑"注释：
                     # 4 条方法级 + 1 条类级 + 5 条方法级 + 2 个类级 + 1 条类级
                     "test_upgrade_path.py", "test_review_v9_batch3.py", "test_upgrade.py",
                     "test_review_v13_batch1.py",
                     # 2026-09-17 批 6：快跑档**结束最晚的那根杆**（争用下 22.8~26.2s）。
                     # 44 例摊薄型（只 2 例 ≥1s = 3.5s），**方法级装饰 1 条**：中C 的端到端
                     # 后果锁；disableability 那条 2.01s 是**唯一牙齿**、刻意留跑（见上文批 6）
                     "test_review_v13_batch4.py",
                     # 2026-09-19：`test_global_agents.py` 末了新增一个类，它要**真跑整脚本**
                     # （第 0/1b/3 节遍历 33 个受管仓，单跑实测 25.3 / 28.7s，类内 4 例合计
                     # 21.4s）。同文件其余各类是最快档的纯逻辑与文件锁（最慢单例 <0.1s），
                     # 所以按"同类里混着纯源码级锁就改用**方法级**"那条规矩 —— **只装饰那一个类**，
                     # 不整文件级。它兜的三条判据（薄壳路径形态 / 手写内容不丢 / 新机器不留正文副本）
                     # 都是"看起来成功了其实没有"型：路径写错照样绿、覆盖掉手写行照样绿、
                     # 链接没链成留了份拷贝也照样绿。接线前先问"哪些秒在关键路径上"——
                     # 这条接得值，它替掉的是三次人工现场复现。
                     "test_global_agents.py")

        def _locate(base):
            # 测试按领域分了子目录：名单仍用 basename（它表达的是"哪份锁"），
            # 路径在这里递归解析；找不到就直接开顶层路径 → 报 FileNotFoundError 以外的
            # 明确失败由 assertIn 给出（与旧版行为一致）。
            for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, "tests")):
                dirnames[:] = sorted(d for d in dirnames
                                     if os.path.isfile(os.path.join(dirpath, d, "__init__.py")))
                if base in filenames:
                    return os.path.join(dirpath, base)
            return os.path.join(ROOT, "tests", base)

        for name in must_wire:
            with open(_locate(name), encoding="utf-8") as f:
                src = f.read()
            self.assertIn("HARNESS_FAST", src, f"tests/{name} 是慢链路文件，必须接快跑开关")
        with open(_locate("test_entry_level.py"), encoding="utf-8") as f:
            src = f.read()
        # ★ 本条只是**代理判据**（数装饰器个数），不是真判据（耗时）—— 见上面②里那条
        #   "活的反例"：批 3 之前它一直是绿的，而当时 test_entry_level 快跑档仍留 35.5s。
        #   阈值刻意松（5），不作收紧，免得把"两个慢类被合理地合并成一个"判成红；
        #   它现在的作用只剩"有人把这文件的开关整个删了/改名了"这一种粗事故。
        self.assertGreaterEqual(src.count("@_slow("), 5,
                                "test_entry_level 的慢类应各自带 @_slow 标记"
                                "（实测 24 个 = 7 类级 + 17 方法级）")

    def test_sweep_script_captures_real_rc(self):
        p = os.path.join(ROOT, "sandbox", "sweep_coverage.sh")
        with open(p, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("rc=$?", src, "sweep 脚本必须先把退出码存下来再 tail（否则 rc 恒 0 = tail 的码）")
        self.assertRegex(src, r"(?s)rc=\$\?.*tail",
                         "rc 必须在 tail **之前**取（`... | tail` 之后的 $? 是 tail 的退出码）")


if __name__ == "__main__":
    unittest.main()
