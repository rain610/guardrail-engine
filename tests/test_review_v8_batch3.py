# -*- coding: utf-8 -*-
"""Codex 评审第 3 批（治理项）的回归锁。

D1 工程仓自检 gate（harness_self 注册 + 能变红）
D2 --validate 值域（run_on/endpoint/encoding/disabled_rules + registry 全覆盖）
D3 接入验收门四项（hook 活性 / 违规探针 / 合规反锁 / 台账记录）
D4 规则能力边界标「启发式」
D5 审计证据强度声明（线索级，不得表述为"可自证"）
"""
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _tmpclean import rmtree_force as _rmtree_force  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    return pathlib.Path(os.path.join(ROOT, rel)).read_text(encoding="utf-8", errors="replace")


class _RecordingIO(object):
    """`io` 的替身：拦下 open()，把每次**写模式**的目标路径记进 sink。

    只换模块自己的名字（`acceptance_check.io`），**不动全局 `io`** —— 否则整条套件一起遭殃。
    `sandbox/acceptance_check.py` 只用到 `io.open` 一个成员，故这里只需实现它（其余成员一旦
    被用上会 AttributeError 立刻暴露，不会静默少记一笔）。
    """

    def __init__(self, sink):
        self.sink = sink

    def open(self, file, mode="r", *a, **kw):
        if any(c in mode for c in "wax+"):
            self.sink.append(os.path.abspath(file))
        return io.open(file, mode, *a, **kw)


class TestD1SelfGate(unittest.TestCase):
    def test_self_gate_registered_and_healthy(self):
        sys.path.insert(0, os.path.join(ROOT, "harness"))
        import _config as C
        cfg = C.load()
        g = (cfg.get("gates") or {}).get("harness_self")
        self.assertIsNotNone(g, "工程仓必须注册成自检 gate（否则本仓提交只走「未注册 → rc=2」）")
        self.assertEqual(g.get("script"), "checks/harness_self_check.py")
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "checks", "harness_self_check.py")))

    def test_self_check_goes_red_on_third_party_import(self):
        """行为：注入一个第三方 import → 自检必须 ❌（否则这条 gate 是摆设）。"""
        probe = os.path.join(ROOT, "sandbox", "_z_probe_stdlib.py")
        with io.open(probe, "w", encoding="utf-8", newline="") as f:
            f.write("import requests  # 反向验证\n")
        try:
            r = subprocess.run([sys.executable, "checks/harness_self_check.py"], cwd=ROOT,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
            self.assertEqual(r.returncode, 1, "第三方依赖必须让自检红（rc=1）")
            self.assertIn("第三方依赖", (r.stdout or "") + (r.stderr or ""))
        finally:
            os.remove(probe)


class TestD2ValidateValueDomain(unittest.TestCase):
    def test_bad_run_on_is_blocking(self):
        d = tempfile.mkdtemp(prefix="d2-")
        try:
            cfg = os.path.join(d, "cfg.yaml")
            with io.open(cfg, "w", encoding="utf-8", newline="") as f:
                f.write("repos:\n  r: %s\ngates:\n  g1:\n    enabled: true\n    repo: r\n"
                        "    script: checks/pc_code_check.py\n    run_on: [pre-commmit]\n" % ROOT.replace("\\", "/"))
            env = dict(os.environ, HARNESS_CONFIG=cfg,
                       HARNESS_LOG_FILE=os.path.join(d, "gate_log.jsonl"))
            r = subprocess.run([sys.executable, os.path.join(ROOT, "harness", "run_gate.py"), "--validate"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env=env, cwd=ROOT, timeout=180)
            out = (r.stdout or "") + (r.stderr or "")
            self.assertEqual(r.returncode, 2, "run_on 拼错必须体检不通过（rc=2）；输出=%s" % out[:200])
            self.assertIn("run_on", out)
        finally:
            _rmtree_force(d)

    def test_bad_endpoint_blocking(self):
        d = tempfile.mkdtemp(prefix="d2b-")
        try:
            cfg = os.path.join(d, "cfg.yaml")
            with io.open(cfg, "w", encoding="utf-8", newline="") as f:
                f.write("repos:\n  r: %s\ngates:\n  g1:\n    enabled: true\n    repo: r\n"
                        "    endpoint: 前端\n    script: checks/pc_code_check.py\n"
                        % ROOT.replace("\\", "/"))
            env = dict(os.environ, HARNESS_CONFIG=cfg,
                       HARNESS_LOG_FILE=os.path.join(d, "gate_log.jsonl"))
            r = subprocess.run([sys.executable, os.path.join(ROOT, "harness", "run_gate.py"), "--validate"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env=env, cwd=ROOT, timeout=180)
            self.assertEqual(r.returncode, 2)
            self.assertIn("endpoint", (r.stdout or "") + (r.stderr or ""))
        finally:
            _rmtree_force(d)

    def test_consumerless_run_on_is_blocking(self):
        """run_on 里**没有消费方**的触发点必须被体检拦下（2026-09-16）。

        `stop` / `auto` 曾经在白名单里，但没有任何代码认它们：写进配置能过体检，
        gate 却永远不在任何时机触发 —— 这条校验存在的意义正是防「触发点静默不跑」，
        而那两个值恰好保证静默不跑。故收回白名单，写法非法即 rc=2。
        """
        for bogus in ("stop", "auto"):
            d = tempfile.mkdtemp(prefix="d2c-")
            try:
                cfg = os.path.join(d, "cfg.yaml")
                with io.open(cfg, "w", encoding="utf-8", newline="") as f:
                    f.write("repos:\n  r: %s\ngates:\n  g1:\n    enabled: true\n    repo: r\n"
                            "    script: checks/pc_code_check.py\n    run_on: [%s]\n"
                            % (ROOT.replace("\\", "/"), bogus))
                env = dict(os.environ, HARNESS_CONFIG=cfg,
                           HARNESS_LOG_FILE=os.path.join(d, "gate_log.jsonl"))
                r = subprocess.run([sys.executable, os.path.join(ROOT, "harness", "run_gate.py"),
                                    "--validate"],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   env=env, cwd=ROOT, timeout=180)
                out = (r.stdout or "") + (r.stderr or "")
                self.assertEqual(r.returncode, 2,
                                 "run_on: [%s] 没有任何消费方，必须 rc=2 而不是放行；输出=%s"
                                 % (bogus, out[:200]))
                self.assertIn("run_on", out)
            finally:
                _rmtree_force(d)

    def test_run_on_whitelist_equals_actually_consumed_triggers(self):
        """白名单必须**等于**真有消费方的触发点集合 —— 机器推导，不靠人记。

        人记的白名单会漂移：加了值没人消费 = 白写，删了值有人消费 = 该触发点再也写不出来。
        这里两边都从源码推：白名单取 `_RUN_ON_OK` 的字面量，消费方取两个触发点实现文件里
        `"<名字>" not in run_on` 这种判断用到的字面量。两边不等即红。
        """
        import ast
        import re
        src = _read("harness/run_gate.py")
        allowed = None
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "_RUN_ON_OK" for t in node.targets):
                allowed = {e.value for e in node.value.elts}
        self.assertIsNotNone(allowed, "没找到 _RUN_ON_OK —— 锁的锚点失效了，别当成通过")

        # 消费方：只有「提交」与「编辑后」两个触发点各有一个实现文件
        consumers = {"harness/hooks/pre-commit.py", "harness/agents/auto-gate.py"}
        seen = set()
        for rel in consumers:
            body = _read(rel)
            seen |= set(re.findall(r'"([a-z_]+)"\s+not in\s+run_on', body))
        self.assertTrue(seen, "没从消费方文件里解析出任何触发点 —— 锁的锚点失效了")

        self.assertEqual(allowed, seen,
                         "_RUN_ON_OK 与「真有消费方的触发点」不一致：\n"
                         "  白名单独有（写了永远不触发，应删）= %s\n"
                         "  有消费方却不在白名单（写了会被误拦）= %s"
                         % (sorted(allowed - seen), sorted(seen - allowed)))

    def test_registry_covers_all_gates(self):
        src = _read("harness/run_gate.py")
        self.assertIn("script_for(_name)", src, "registry 校验必须遍历全部 gate，不是只抽查第一个")


class TestD3AcceptanceGate(unittest.TestCase):
    XML = None

    def test_four_items_all_true_for_self_gate(self):
        # 用干净 env：别的测试会设 HARNESS_LOG_FILE（隔离日志），继承过来会让"台账"那一项
        # 指向空文件 → 假红（实测：单跑 OK、与其他模块同跑 FAILED）。这里显式指回真台账。
        env = dict(os.environ)
        env["HARNESS_LOG_FILE"] = os.path.join(ROOT, "reports", "gate_log.jsonl")
        r = subprocess.run([sys.executable, "sandbox/acceptance_check.py", "harness_self", "--json"],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=400, env=env)
        # 环境无关（2026-09-15 CI 实红）：容器里没有端仓 → ①「hook 活性」必然 ❌ →
        # 脚本按设计 rc=1。**rc=1 是合法结果**，不是缺陷：缺的是接入机，不是门禁。
        # 但脚本自身崩掉（rc≥2 或无 JSON）必须红——那才是真故障；具体四项由下面逐项断言。
        self.assertIn(r.returncode, (0, 1),
                      "验收脚本异常退出（应为 0=全过 / 1=有项未过，实得 %d）；输出=%s"
                      % (r.returncode, ((r.stdout or "") + (r.stderr or ""))[-300:]))
        rows = json.loads(r.stdout)
        row = rows[0]
        notes = " ".join(row.get("notes") or [])
        # ①（hook 活性）与 ④（台账记录）依赖「本机是接入机 + 有运行历史」——CI 容器、
        # 新检出、同事刚 clone 的机器上必然缺。此时备注里**必须说明原因**：
        # 既不能把环境差异当缺陷（假红），也不许静默（那才是报平安）。
        # 「四项全真」的硬要求，由接入机上的 acceptance_check 输出（pass 字段）+ 验收记录承担。
        if not row.get("hook"):
            # ① 有**两条**合法的为假路径，备注必须说明是哪一条（静默 = 报平安）：
            #   · 「端仓路径本机不存在」——仓本身不在（同事机常态），走 else 支；
            #   · 「hooksPath=…」——仓在、本仓没接入钩子（干净 clone / CI 容器），走 isdir 支。
            # 2026-09-19：原先只认前者。批次 2 给 `harness_self` 补上 repo 之后
            # （`acceptance_check.check_gate`：harness_self 的仓就是工程仓自身），本 gate
            # 永远走 isdir 支 —— 于是这条断言**只在 ① 为假的机器上执行、却只认另一种措辞**：
            # 本机 hooksPath 已设 → 整段被跳过（绿），CI 容器 → 必然红（实测 35366806038）。
            # 判据改成"说明了原因"这件事本身，而不是某一个词。
            self.assertTrue("端仓" in notes or "hooksPath" in notes,
                            "① 为假但备注没说明原因（环境不适用 vs 真故障）：%s" % notes)
        if not row.get("ledger"):
            self.assertIn("台账", notes, "④ 为假但备注没说明原因")
        # ②③ 用密闭夹具（临时仓 + 指向临时 gate 的配置），不碰端仓 —— **任何环境都必须真**，
        # 这是本锁的核心价值：违规探针拦得住、合规反锁不误拦。
        for k in ("probe", "clean"):
            self.assertTrue(row.get(k), "接入验收门第 %s 项未通过：%s" % (k, notes))

    def test_self_gate_probe_writes_nothing_into_the_engineering_tree(self):
        """`harness_self` 那支的 ②③ 必须是**密闭**的：跑完不许往工程树里写半个字节。

        为什么要有这条锁：`acceptance_check.py` 头注承诺「②③ 一律走密闭夹具，不向工程树写任何
        文件」，而 harness_self 查的是**本工程仓的仓库级事实**、用不了临时仓 —— 它历史上就靠
        「在工程树里建一个 `sandbox/_accept_probe_selfcheck_<pid>.py`、跑完删掉」造违规。承诺与
        实现相抵，且留了个真隐患：验收被 Ctrl-C / 超时打断时那个文件就留在树里，此后**每次提交
        都红**（第三方依赖是阻断级），得人工去翻树才知道为什么。

        ★ 判据为什么不是"跑完看看树里多了什么"：旧实现用 try/finally **当场删掉**探针，
        「建了又删」在跑完之后根本看不见 —— 那种锁在"回退成 in-tree 探针"的变异下照样绿
        （本仓 M15 已经栽过一回同款假绿，见 `test_source_hygiene.py` 里那段★）。所以这里改成
        **在写入发生的那一刻记账**：把模块自己的 `io` 换成记账替身，任何写模式的 open 都留下
        路径，再断言没有一个落在 ROOT 底下。路径是怎么算出来的都拦得住（不假设它一定写成
        `os.path.join(ROOT, ...)` 那个形状）。

        反空转（三向，缺一不成）：
          ① ②③ 仍必须为真 —— 否则「探针没跑」也能让"没写文件"成立；
          ② 必须**确实发生过写入** —— 夹具被换成空转时同样能让"没写文件"成立，那是另一种假绿；
          ③ 写入必须落在工程树之外（本锁的正题）。
        """
        sys.path.insert(0, os.path.join(ROOT, "harness"))
        import _config as C

        # 按路径加载 acceptance_check（同 test_source_hygiene.py 加载 list_entries 的做法）：
        # 不把 sandbox/ 塞进 sys.path —— 那目录里有 run_tests.py / uninstall.py 等常见名字，
        # 留在 sys.path 上会给后续测试埋"导入到另一个模块"的雷（本仓 2026-09-14 已经栽过
        # 一次 sandbox 导入事故，见 TestD7SelfCheckLayering 的注释）。模块自己会把它需要的
        # harness/ 与 checks/ 插进 sys.path，不需要我代劳。
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_acc_probe_tree_write", os.path.join(ROOT, "sandbox", "acceptance_check.py"))
        A = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(A)

        # 按**真实配置**判：调用方环境里若残留 HARNESS_CONFIG（别的锁设过），合规那支取到的
        # repo 键就不是真的，判定会随环境漂移。这里临时摘掉，用完原样放回。
        old_cfg_env = os.environ.pop("HARNESS_CONFIG", None)
        writes, real_io = [], A.io
        A.io = _RecordingIO(writes)
        try:
            row = A.check_gate("harness_self", C.load(),
                               os.path.join(ROOT, "reports", "gate_log.jsonl"))
        finally:
            A.io = real_io
            if old_cfg_env is not None:
                os.environ["HARNESS_CONFIG"] = old_cfg_env

        notes = " ".join(row.get("notes") or [])
        self.assertTrue(row["probe"] and row["clean"],
                        "密闭探针本身没成立（②③ 都得真，否则下面两条断言等于没测）：%s" % notes)
        self.assertTrue(writes, "一次写都没发生 —— 夹具被换成空转，这条锁会退化成恒真")
        inside = [p for p in writes
                  if os.path.normcase(p).startswith(os.path.normcase(ROOT) + os.sep)]
        self.assertEqual([], inside,
                         "harness_self 的验收探针往工程树里写了文件：%s\n"
                         "头注承诺「②③ 用密闭夹具、不写业务仓文件」，实现必须与承诺一致"
                         "（改成临时目录里的配置覆盖层，见 check_gate 内的说明）" % inside)


class TestD4HeuristicBoundary(unittest.TestCase):
    def test_regex_rules_tagged(self):
        sys.path.insert(0, os.path.join(ROOT, "checks"))
        import example_ruleset as E
        tagged = sorted(r.rule_id for r in E.EXAMPLE_RULES if getattr(r, "heuristic", False))
        regex_ids = sorted(r.rule_id for r in E.EXAMPLE_RULES if r.pattern.pattern)
        self.assertEqual(tagged, regex_ids,
                         "单行正则规则必须全部标为启发式：标记 %s / 正则规则 %s"
                         % (tagged, regex_ids))
        self.assertGreaterEqual(len(tagged), 2, "示例规则集至少应有 2 条单行正则规则")

    def test_selfcheck_reports_boundary(self):
        r = subprocess.run([sys.executable, "checks/harness_self_check.py"], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        self.assertIn("启发式", (r.stdout or ""), "自检输出必须列出启发式规则口径（能力边界要可见）")


class TestD5AuditEvidenceStrength(unittest.TestCase):
    def test_tool_declares_evidence_level(self):
        src = _read("sandbox/audit_reconcile.py")
        self.assertIn("线索级", src, "审计工具必须声明证据强度（时间窗匹配不足以自证）")

    def test_docs_warn_against_overclaiming(self):
        src = _read("README.md")
        self.assertIn("证据强度", src)
        self.assertIn("不要写成", src, "文档要明确禁止表述为「提交可自证」")


_HAS_GIT_DIR = os.path.isdir(os.path.join(ROOT, ".git"))


@unittest.skipUnless(_HAS_GIT_DIR, "工程副本没有 .git（评审方拷贝/安装包子集）：本锁依赖 git 目录，跳过而非假红")
class TestD6GuardSelfModeNoDeadlock(unittest.TestCase):
    """guard 自指死结（a）的回归锁：本仓提交必须能通过哨兵，同时不放弃标记把关。"""
    # 环境说明：harness_guard.py 在"不是 git 仓"时会走静默放行分支（打印为空），
    # 于是本类断言会以"输出里没有 self 模式"的形式假红 —— 2026-09-15 外部评审
    # 用无 .git 的工程拷贝跑全量套件时命中过（1/379）。判据用 .git 目录存在性，
    # 与 D3 锁的环境感知口径一致（宁可跳过并出声，不要给出误导性的红）。

    def _guard(self, args, cwd=None):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "checks", "harness_guard.py")] + args,
                           cwd=cwd or ROOT, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def test_self_mode_inferred_from_cwd_not_only_flag(self):
        """hook 场景带 gate 名、没有 --self：cwd=工程根时也必须是 self 模式（否则永久阻断本仓提交）。"""
        rc, out = self._guard(["harness_self"])
        self.assertEqual(rc, 0, "本仓提交（cwd=工程根）必须放行并把标记把关交给 commit-msg；输出=%s" % out[-200:])
        self.assertIn("self 模式", out)

    def test_marker_still_passes(self):
        rc, _ = self._guard(["--self", "[harness] 测试提交"])
        self.assertEqual(rc, 0)

    def test_no_marker_still_blocks_when_gate_files_dirty(self):
        """延后把关 ≠ 放弃把关：self 模式 + 消息里没有维护标记 → 仍走拦截分支。

        注意：哨兵只在"工程仓确实有未提交的门禁文件改动"时才拦（干净仓 + 坏消息 = 无可拦之物），
        所以这条用**结构锁**表达意图（行为锁需要把仓库弄脏，测试里不该那样做）；
        真正在脏仓里拦人已由既有的第三轮锁与本仓日常提交覆盖。
        """
        src = _read("checks/harness_guard.py")
        self.assertIn('self_mode and any(t.lower() in commit_msg.lower() for t in MAINT_TAGS)', src,
                      "self 模式 + 带维护标记才放行（这条分支不能被删）")
        i = src.find("if touched:")
        self.assertGreater(i, 0, "脏仓拦截分支必须在")
        self.assertIn("已拦截", src[i:i + 600], "脏仓 + 无标记消息必须打印拦截说明")

    def test_end_repo_cwd_is_not_self_mode(self):
        """端仓 cwd 不是工程根 → 不能走 self 放行（防"换个目录就绕过"）。"""
        d = tempfile.mkdtemp(prefix="d6-")
        try:
            subprocess.run(["git", "init", "-q", "."], cwd=d, capture_output=True)
            rc, out = self._guard(["demo_pc"], cwd=d)
            # 工程仓此时可能有未提交门禁文件（跑测试时常有）→ 期望仍是 1；若干净则 0，都算对，
            # 关键是**不能**因为 cwd 是别处就打印 self 模式。
            self.assertNotIn("按 self 模式把关", out, "端仓 cwd 不得被当成 self 模式")
        finally:
            _rmtree_force(d)


class TestD7SelfCheckLayering(unittest.TestCase):
    """自检 gate 的**分层边界**：checks/ 不得 import sandbox/（引擎层零依赖守卫的契约）。

    事故（2026-09-14）：harness_self_check.py 首版写了 `import list_entries`（在 sandbox/）→
    CI 与局部复现都红在既有守卫 tests/test_stdlib_only.py 上。那条守卫是对的（引擎不应依赖 sandbox），
    错的是我的代码 → 改为按文件路径加载，并补一条子集布局锁。
    """

    def test_no_top_level_imports_outside_engine(self):
        src = _read("checks/harness_self_check.py")
        top = [l.strip() for l in src.splitlines() if l.startswith(("import ", "from "))]
        allowed = {"import argparse", "import ast", "import os", "import pathlib", "import sys",
                   "from __future__ import annotations"}
        bad = [l for l in top if l not in allowed]
        self.assertEqual(bad, [], "自检脚本顶层只允许标准库（引擎层零依赖契约）：%s" % bad)
        # 只认**代码位置**的 import 语句：文件里那段解释性注释也含 "import list_entries" 字样，
        # 泛匹配会自伤（第一版就是这么红的）。
        import re as _re
        self.assertIsNone(_re.search(r"^\s*import\s+list_entries", src, _re.M),
                          "sandbox/ 属于另一层，不得直接 import")

    def test_subset_layout_degrades_to_warning(self):
        """工程子集布局（无 sandbox/，如安装包）→ UTF-8 清单项记 ⚠ 而非 ❌（否则同事一装就红）。"""
        d = tempfile.mkdtemp(prefix="d7-")
        try:
            for name in ("checks", "harness", "config"):
                src = os.path.join(ROOT, name)
                if os.path.isdir(src):
                    shutil.copytree(src, os.path.join(d, name),
                                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            r = subprocess.run([sys.executable, "checks/harness_self_check.py"], cwd=d,
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=180)
            out = (r.stdout or "") + (r.stderr or "")
            self.assertEqual(r.returncode, 0, "子集布局不该判工程坏；输出=%s" % out[-200:])
            self.assertIn("sandbox/list_entries.py", out, "要说清是哪一项跳过")
        finally:
            _rmtree_force(d)


if __name__ == "__main__":
    unittest.main()
