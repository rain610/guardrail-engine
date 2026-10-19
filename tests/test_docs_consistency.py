#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""文档一致性锁（2026-09-13）

为什么要有：这仓库踩过"改了内容忘了改数字"的坑——分享 PPT 曾经
"9 页 / 33 条规则 / 24 条规则" 三处自相打架，是人工看出来的。
这类漂移不该再靠人眼，用最便宜的两条锁钉住：

  1. PPT 静态页码里的总数 == 实际 section.slide 数量 == 分页注释数量，
     且注释编号 1..N 顺序不间断（加页忘重编号就会被逮住）。
  2. README「接入模式」表里点名的客户端，必须在 integrations/<客户端>/ 下有 README
     （新接一款工具只改表不写文档 → 红）。

2026-09-16 扩充（剖析报告 A1）——上面第 1、2 条是**页面级**一致性，覆盖不到「同一个
事实散落在多份文档、各自写了一个数」。本轮修了 7 处这类漂移，根因是锁的覆盖面：
原来只有 README.md / AGENTS.md 受计数锁保护。故补三类：
  3. 数字断言（TestDocNumericClaims）：gate 数 / 仓数 / PPT 页数 / Python 版本要求
     —— 一律从 `config/harness.yaml`、PPT、`pyproject.toml` **动态取事实**，不抄数字。
  4. 历史记录豁免（test_historical_docs_stay_out_of_numeric_locks）：CHANGELOG /
     docs/review-loop.md 的数字是**当时的真实值**，纳入数字锁 = 逼人篡改历史。
  5. 缺口语义（TestAbsenceClaimsAreHonest）：文档说「某某尚未入库」而该路径**存在**
     → 红。这把「Known gaps」从人肉维护变成可机械判定。
  6. 范式索引一致性（TestGoldenPatternsIndexIsComplete）：`golden-patterns/index.md`
     的「按端速查」是手维目录，与目录实况**双向**对齐——漏登记报错，指空也报错。
     （2026-09-20 补；被锁的那份文档自己就写着「根治办法是加一条锁…尚未做」。）
  7. 组件数口径一致（TestComponentCountsAreUnambiguous）：`h5/组件手册/索引.md`
     里同一个端只能有**一个**组件数，且「N 个…目录（a/b/c）」的声明数要等于列出项数。
     （2026-09-20 补；订正前该文件的基准行把 9 个**重封装目录**写成了「PC 9 组件」，
     与下方的 86 打架——两个集合被同一个名词「组件」并排写着，谁读都会错。
     真出过，不是假想。）

只做文件解析，不起子进程、不依赖平台，毫秒级。
"""
from __future__ import unicode_literals

import pathlib

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PPT = os.path.join(ROOT, "docs", "harness分享PPT.html")
README = os.path.join(ROOT, "README.md")

# README 的「接入模式」表里点名的客户端 → 必须有对应接入文档
# (目录名, README 里的显示名)——两样都要对，避免"加了目录忘了写进表"或反之
CLIENTS = [
    ("claude-code", "Claude Code"),
    ("codex", "Codex"),
    ("opencode", "OpenCode"),
    ("codebuddy", "CodeBuddy"),
    ("trae-cn", "Trae CN"),
    ("qoder-cn", "Qoder CN"),
    ("zcode", "ZCode"),
]

# 注意：封面那页是 class="slide active"，所以不能精确匹配 class="slide"，
# 必须容许额外的 class（这个坑我第一版就踩了：11 页只数到 10）。
SLIDE_RE = r'<section[^>]*class="[^"]*\bslide\b[^"]*"'


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ── 可移植性（2026-09-15）：门禁要能原样分享给同事 ──────────────────────────
# 维护者自用的个人 skill / 个人工具**不得**成为运行时依赖：引擎层（下列目录）出现这些名字即红。
# 文档里提到时必须**条件化**（"不依赖 / 可跳过 / 若你… / 没有也…"），否则同事会以为要先装它们。
PERSONAL_DEPS = ["worktree-manager", "huayu-manager", "codebase-memory", "farmerCore"]
ENGINE_DIRS = ["checks", "harness", "config", "sandbox"]
CONDITIONAL = ("不依赖", "不受影响", "照常", "可跳过", "若你", "可选", "有则", "没有也")
_ENGINE_SUFFIX = (".py", ".sh", ".yaml", ".yml", ".json", ".md")


class TestNoPersonalDependencies(unittest.TestCase):
    def test_engine_layer_has_no_personal_dep(self):
        """引擎层**不得依赖**个人 skill / 个人工具。

        判据用「名字 + 上下文」而不是单纯出现：实测 `sandbox/bootstrap_agent_env.sh` 里有一句
        「MCP/codebase-memory 等工具自动注入的内容不受影响，照常生效」—— 那是**中性说明**，
        明说与本工程无关（不是依赖）。所以：命中名字时，看该行 ±3 行内有没有条件化措辞；
        没有才算依赖（这才是"同事拿到就能跑"的真正判据）。
        """
        hits = []
        for d in ENGINE_DIRS:
            for f in pathlib.Path(ROOT, d).rglob("*"):
                if f.is_dir() or "__pycache__" in f.parts or f.suffix.lower() not in _ENGINE_SUFFIX:
                    continue
                rel = f.relative_to(ROOT).as_posix()
                lines = _read(rel).splitlines()
                for i, line in enumerate(lines, 1):
                    low = line.lower()
                    for name in PERSONAL_DEPS:
                        if name.lower() not in low:
                            continue
                        ctx = " ".join(lines[max(0, i - 4):i + 3])
                        if not any(c in ctx for c in CONDITIONAL):
                            hits.append("%s:%d:%s" % (rel, i, name))
        self.assertEqual(hits, [],
                         "引擎层出现个人 skill/工具且未说明「不受影响/可跳过」（= 变成依赖了）：%s" % hits)

    def test_docs_mentioning_personal_dep_are_conditional(self):
        """文档里出现这些名字时，同文件必须有条件化措辞（不能让人以为'必须先装它'）。"""
        for rel in ("README.md", "docs/packaging/INSTALL.md"):
            p = pathlib.Path(ROOT, rel)
            if not p.exists():
                continue
            txt = _read(rel)
            for n in PERSONAL_DEPS:
                if n.lower() in txt.lower():
                    self.assertTrue(any(c in txt for c in CONDITIONAL),
                                    "%s 提到 %s 但没有条件化措辞（不依赖/可跳过/若你/没有也…）"
                                    % (rel, n))

    def test_readme_states_no_personal_dependency(self):
        """README 必须明写「不依赖任何个人 …」——否则同事接入时会先去找那个 skill。"""
        txt = _read("README.md")
        self.assertIn("不依赖任何个人", txt,
                      "README 未说明门禁不依赖个人 skill/工具（同事会以为需要先装）")


class TestDeckConsistency(unittest.TestCase):
    def test_pageno_total_matches_slide_count(self):
        """号码总数必须等于真实页数（加页/删页忘了改页码 → 红）。"""
        html = _read(PPT)
        slides = len(re.findall(SLIDE_RE, html))
        m = re.search(r'class="pageno"[^>]*>\s*\d+\s*/\s*(\d+)\s*<', html)
        self.assertIsNotNone(m, "PPT 里找不到静态页码（class=pageno）")
        self.assertEqual(
            int(m.group(1)), slides,
            "PPT 静态页码写的是 /%s，实际有 %d 页" % (m.group(1), slides))

    def test_slide_comment_numbering_is_sequential(self):
        """分页注释 <!-- N 标题 --> 必须 1..N 连续（加页忘重编号 → 红）。"""
        html = _read(PPT)
        nums = [int(n) for n in re.findall(r"<!--\s*(\d+)\s+[^>]*?-->", html)]
        slides = len(re.findall(SLIDE_RE, html))
        self.assertEqual(
            nums, list(range(1, slides + 1)),
            "分页注释编号不连续：%s（共 %d 页）" % (nums, slides))


class TestClientDocsExist(unittest.TestCase):
    def test_every_client_in_readme_has_integration_readme(self):
        """README 点名的客户端，都要有自己的 integrations/<客户端>/README.md。"""
        readme = _read(README)
        missing, unlisted = [], []
        for d, display in CLIENTS:
            if not os.path.isfile(os.path.join(ROOT, "integrations", d, "README.md")):
                missing.append(d)
            if display not in readme:
                unlisted.append(display)
        self.assertEqual(missing, [], "这些客户端没有接入文档：%s" % missing)
        self.assertEqual(unlisted, [], "README「接入模式」表漏了这些客户端：%s" % unlisted)



class TestPptFitsLaptopViewport(unittest.TestCase):
    """PPT 必须在笔记本视口（1366x768 等）下**整页可见**。

    2026-09-13 用户实测反馈"笔记本看不全"+ CDP 复核：`#deck` 原本只靠 #stage 网格居中 + scale，
    布局框仍是 1920x1080 → 1366x768 下缩放后的视觉框 top=156、底=924 > 视口 768，**底部 156px 出屏**
    （1280x720、1024x600 同样被裁）。
    修法：#deck 用 `position: fixed; left/top: 50%` + `transform: translate(-50%,-50%) scale(s)`，
    视觉框永远与视口中心对齐。这两处是配套的，改一处就会退回被裁状态 → 这里锁住。
    """

    def setUp(self):
        self.html = pathlib.Path(os.path.join(ROOT, "docs", "harness分享PPT.html")).read_text(encoding="utf-8")

    def test_deck_is_fixed_and_self_centered(self):
        import re
        m = re.search(r"#deck\s*\{[^}]*\}", self.html, re.S)
        self.assertIsNotNone(m, "找不到 #deck 规则")
        rule = m.group(0)
        self.assertIn("position: fixed", rule, "#deck 必须 fixed（否则缩放后视觉框会掉到视口外）")
        self.assertIn("left: 50%", rule)
        self.assertIn("top: 50%", rule)
        self.assertIn("translate(-50%, -50%)", rule, "#deck 必须自带 translate 居中")

    def test_js_scale_keeps_centering(self):
        self.assertIn("translate(-50%, -50%) scale(", self.html,
                      "JS 缩放必须带 translate，否则居中丢失、小屏又被裁")

# ── 数字断言锁（2026-09-16 剖析报告 A1） ──────────────────────────────────────
# 为什么补：本次修了 6 处「文档与实现漂移」。逃逸原因不是人懒，是**锁的覆盖面** ——
# test_entry_level.py 的计数锁只扫 README.md / AGENTS.md，其余文档里同一条事实可以
# 自由地写成另一个数（README_推广接入 与 ADR 同时写着「33 个 gate」时无人发现）。
# 这里把**数字事实**扩到全部会写它的文档。
#
# 两条纪律（缺一个这锁就会变成噪音，而噪音的害处是「下次真漂移没人看」）：
#  ① 只锁**能机械取到事实**的数（gate 数 / 仓数 / PPT 页数 / Python 版本）。取不到来源的
#     宁可不锁 —— 例：`README_推广接入.md` 的「5 款客户」没有单一事实源，故不锁。
#  ② `N 仓` 在本仓有**三种含义**（实测）：RUNBOOK「34 仓钩子体检」= 全量；推广文档
#     「已全量清除（29 仓）」= 当时扫过的仓数、「✅ 6 仓已有」= 已有某文档的仓数
#     （后者 2026-09-19 已从推广文档删掉 —— 实测早就是 29 个，改成了「不写数、指命令」）。
#     所以仓数锁只认**唯一无歧义的那个短语**，不做泛化匹配。
#  ③ CHANGELOG / docs/review-loop.md 是**带日期的历史记录**，里面的数字是当时的真实值；
#     拿今天的数去比等于要求人篡改历史 —— 明确排除。
# 低 18（评审 v13）：原先缺 OPENCLAW.md —— 而它恰恰是**随包出货**的那份（pack_release.py
# 把它复制进安装包的 OPENCLAW.md），读者是拿到包的同事。它写着「harness_list_gates 返回
# 33/33」，config 早已是 34（2026-09-14 本仓注册 harness_self 自检 gate 时从 33 变 34）——
# 数字锁管不到，于是漂移一直躺在出货件里。补进来，并补上它用的那种写法（见下）。
NUMERIC_DOCS = ("README.md", "AGENTS.md", "README_推广接入.md",
                "docs/adr/0001-packaging-principles.md",
                "docs/packaging/OPENCLAW.md",
                "docs/packaging/RUNBOOK.md", "docs/packaging/COMPAT.md",
                "docs/packaging/INSTALL.md")
HISTORICAL_DOCS = ("CHANGELOG.md", "docs/review-loop.md")
# 只认「**要求**」形态，不认「某个 API 从哪个版本起有」这类说明：
#   README:17「3.10 及以下缺少 write_text(newline=) 等 API」、
#   推广:33「引擎用到 write_text(newline=) 等 3.10+ API」
# 这两句都是**真话**且与「要求 3.11+」不矛盾 —— 泛化匹配会把它误判成漂移（本锁首跑就
# 真踩了这个坑：报了「推广文档要求 3.10+」）。所以要锚定在「Python」/加粗/「≥」上。
_PY_REQ_PATTERNS = (
    r"Python\s*3\.(\d+)\+",      # 「Python 3.11+」
    r"Python\s*≥\s*3\.(\d+)",    # 「Python ≥ 3.11」
    r"\*\*3\.(\d+)\+\*\*",       # README 表格单元格「**3.11+**」
)
# 否定词只用**不互为子串**的形式（原先同时写了「未提供」和「尚未提供」，后者恒被前者覆盖）
ABSENCE_WORDS = ("未提供", "未入库", "未纳入", "待补", "尚未支持")
_PATH_TOKEN = r"`?([\w][\w./-]*\.(?:py|md|json|toml|yaml|yml|sh|html|js|vue|cs))`?"


def _config_facts():
    """动态取「事实」：gate 数。与引擎**同一份 config**（不抄数字）。

    **不再回仓数**（2026-09-18 跨平台批次 2）：`config/harness.yaml` 的 `repos:` 段已刻意
    留空，路径下沉到 gitignored 的 `config/harness.local.yaml`（各人不同）。换句话说
    `len(cfg["repos"])` **已经不是团队事实、而是本机事实** —— 拿它去比文档数字，等于要求
    文档写死「我这台机器几个仓」，同事一 clone 就变假。要数本机仓用 `_machine_repos()`。

    注意措辞：本文件只 import `_config` 读配置，**不起任何子进程**，故不需要
    `HARNESS_LOG_FILE` 隔离。但 `tests/test_coverage.py` 的隔离锁是**全文件子串**判据
    （源码里出现引擎入口名就要求隔离），所以这里刻意不写那个名字 —— 写了会误报
    「探针会污染真实台账」。真要在这里起子进程时，请连同 HARNESS_LOG_FILE 一起补上。
    """
    import sys as _sys
    _sys.path.insert(0, os.path.join(ROOT, "harness"))
    import _config
    cfg = _config.load()                    # 只读一次（load 会走一遍解析 + 告警）
    return len(cfg.get("gates") or {})


def _machine_repos():
    """本机 repos 段的键名（**已排除引擎无条件注入的 `harness_self`**）。"""
    import sys as _sys
    _sys.path.insert(0, os.path.join(ROOT, "harness"))
    import _config
    return _config.machine_repos()


def false_absence_claims(text, exists):
    """返回 `[(行号, 路径)]`：文档里「声称某物尚未提供/未入库」但该路径**确实存在**的行。

    `exists` 注入成可调用对象，便于正/反两向单测（见 TestAbsenceClaimsAreHonest）。
    只认同一行里的 `否定词 + 仓库相对路径` —— 泛化匹配会误伤
    「未提供的是公开发布到 PyPI」（无路径 → 本来就不该报）。
    """
    out = []
    for i, line in enumerate(text.split("\n"), 1):
        if not any(w in line for w in ABSENCE_WORDS):
            continue
        for p in re.findall(_PATH_TOKEN, line):
            if exists(p):
                out.append((i, p))
    return out


class TestDocNumericClaims(unittest.TestCase):
    """文档里的数字必须与代码事实一致（扩到全部会写数字的文档）。"""

    def test_gate_count_claims_match_config(self):
        total = _config_facts()
        for rel in NUMERIC_DOCS:
            if not os.path.isfile(os.path.join(ROOT, rel)):
                continue
            txt = _read(rel)
            seen = []
            for m in re.finditer(r"(\d+)\s*个\s*gate", txt):          # 「34 个 gate」
                seen.append(("个 gate", int(m.group(1))))
            for m in re.finditer(r"完整清单（(\d+)\s*个", txt):        # AGENTS.md 的形态
                seen.append(("完整清单", int(m.group(1))))
            # OPENCLAW.md 的形态：「harness_list_gates 返回 34/34（全部启用）」。
            # 锚在「（全部启用）」上而不是裸 `\d+/\d+`：后者会命中版本号、日期、比例等
            # 无关文本 —— 假漂移报告的代价是下一次真漂移没人信（本文件头两条纪律）。
            # 两个数都要等于 total：MCP 的 list_gates 返回**全部** gate（34），
            # 而「已启用」也是 34 —— 写成 33/34 这类同样当场红。
            for m in re.finditer(r"返回\s*(\d+)\s*/\s*(\d+)\s*（全部启用）", txt):
                seen.append(("list_gates 全启用", int(m.group(1))))
                seen.append(("list_gates 全启用", int(m.group(2))))
            for kind, n in seen:
                self.assertEqual(n, total,
                                 "%s 里写着「%d」（%s），config 实际是 %d 个 gate"
                                 % (rel, n, kind, total))

    def test_historical_docs_stay_out_of_numeric_locks(self):
        """带日期的历史记录**不得**纳入数字锁 —— 否则等于逼人篡改历史。

        `docs/review-loop.md:13` 的「399 例」是 v12 那轮的真实记录，今天的 426 例
        不使它变假（本次修 A1 时**刻意没改**它）。这条锁防的是后来者顺手把
        CHANGELOG 塞进 NUMERIC_DOCS。
        """
        self.assertEqual([], [d for d in HISTORICAL_DOCS if d in NUMERIC_DOCS],
                         "历史记录文档被纳入数字锁了：这些文件的数字是**当时的真实值**，"
                         "拿今天的实现去比会要求人篡改历史。要锁就锁当下的活文档。")

    def test_runbook_does_not_hardcode_repo_count(self):
        """RUNBOOK **不许**写死「N 仓钩子体检」—— 仓数是每机事实，写死必然在同事机器上变假。

        旧形态是 `assertEqual(N, len(cfg["repos"]))`（2026-09-18 跨平台批次 2 改掉），它有两个
        毛病：① repos 段已下沉到 gitignored 的 local.yaml，那个数**各人不同**，断言在别人机器上
        必红或必假；② `harness_self` 由引擎无条件注入，那个数**最小是 1**，RUNBOOK 写
        「1 仓钩子体检」语义荒谬。
        改锚点而不是改数字：文档改说「全部受管仓」并指到 `run_gate.py --list`，本锁改为
        「不许出现具体仓数 **+** 必须指对清单入口」。
        """
        txt = _read("docs/packaging/RUNBOOK.md")
        m = re.search(r"\d+\s*仓", txt)
        if m is not None:
            self.fail("RUNBOOK 里又出现了写死的仓数「%s」—— 那是本机事实（repos 段在 gitignored 的 "
                      "config/harness.local.yaml 里），别人 clone 后这个数字就是错的；"
                      "要写就写「全部受管仓」并指到 run_gate.py --list。" % m.group(0))
        self.assertIn("run_gate.py --list", txt,
                      "RUNBOOK 不再写死仓数，那就必须告诉读者**去哪看本机清单**："
                      "补一句 `python <HARNESS>/harness/run_gate.py --list`。")

    def test_machine_repos_excludes_engine_injected_self(self):
        """`harness_self` 是引擎注入的，不算「同事自己配的那些仓」—— 口径必须与 run_gate 一致。

        `_config.machine_repos()` 与 `run_gate.py::_validate` 用的是同一条判据的两端：
        前者说「本机**填了**几条路径」，后者（批次 7 起直接读合并后的 `repos`）拿
        「键在不在 repos 段」判真配置错、「值空不空」判本机有没有配。
        两边口径一旦分叉，干净容器里的 CI 就会红（或反过来静默放行）。

        2026-09-18（跨平台批次 7）：旧文本点名的 `_has_machine_paths` 已被删掉（它数的是
        「键在不在」，而键名回到共享配置后那个数恒为 33/0，区分不出任何东西）——
        判据的定义现在只在 `_config._machine_keys()` 一处，这条锁测的仍是它的**去重/有序/不含注入项**
        这三个契约，与调用方换不换无关。
        """
        names = _machine_repos()
        self.assertNotIn("harness_self", names,
                         "machine_repos() 把引擎注入的 harness_self 也算进来了：它是无条件注入的，"
                         "算进来会让「本机一条路径都没配」永远判不出来（最小值 1）→ CI 上阻断。")
        self.assertEqual(sorted(set(names)), names, "machine_repos() 应返回去重且有序的键名列表")

    def test_ppt_page_count_claim_matches_deck(self):
        """推广文档的「N 页 HTML 幻灯片」必须等于 PPT 实际页数。"""
        slides = len(re.findall(SLIDE_RE, _read(PPT)))
        txt = _read("README_推广接入.md")
        m = re.search(r"(\d+)\s*页\s*HTML", txt)
        self.assertIsNotNone(m, "推广文档里找不到「N 页 HTML」（改了措辞请同步本锁）")
        self.assertEqual(int(m.group(1)), slides,
                         "推广文档写「%s 页」，PPT 实际 %d 页" % (m.group(1), slides))

    def test_python_requirement_is_consistent(self):
        """文档里的 Python **版本要求**必须等于 pyproject 的 requires-python。

        只认要求形态（见 `_PY_REQ_PATTERNS` 的注释）：本锁首跑时报了
        「推广文档要求 3.10+」，实际那句是「引擎用到 3.10+ API」—— 说明/要求不分家
        就会产出**假漂流报告**，而假报告的代价是下一次真漂移没人信。
        """
        pyproj = _read("pyproject.toml")
        req = re.search(r'requires-python\s*=\s*">=\s*3\.(\d+)"', pyproj)
        self.assertIsNotNone(req, "pyproject.toml 里找不到 requires-python = \">=3.N\"")
        want = req.group(1)
        found = 0
        for rel in NUMERIC_DOCS:
            if not os.path.isfile(os.path.join(ROOT, rel)):
                continue
            for pat in _PY_REQ_PATTERNS:
                for m in re.finditer(pat, _read(rel)):
                    got = m.group(1)
                    found += 1
                    self.assertEqual(got, want,
                                     "%s 要求 Python 3.%s+，pyproject 要求 3.%s+"
                                     "（同一个仓两套说法）" % (rel, got, want))
        # 两向探针：正则若因措辞变化而**一条都匹配不到**，上面的循环会静默空转 → 假绿。
        self.assertGreaterEqual(
            found, 4, "只认出 %d 处 Python 版本要求（预期 ≥4）—— 文档改了措辞，"
                      "请同步 _PY_REQ_PATTERNS，别让这条锁空转" % found)


class TestEscapeHatchScopeMatchesCode(unittest.TestCase):
    """逃逸口的**适用范围**必须与代码实况一致（本轮勘察新增的缺口）。

    发现：README 泛泛写着 `harness-ok: <规则ID> <理由>` 是行内豁免，没说它只在部分端成立。
    实测 `harness-ok` 只实现在 `pc_ruleset.py` / `h5_ruleset.py` 两个规则集里，
    `backend_ruleset.py` / `encoding_ruleset.py`（后端端）**完全没有**，而 `rule_base.py`
    里也没有通用实现（只有汇总打印）。后端有 ERROR 级规则、确实会拦，于是后端同事
    照 README 加了标记 → **标记毫无作用、门禁照样拦**，而人会以为自己已经合法绕过了。

    这是「文档承诺 > 代码实现」的典型：不危险（fail-closed），但把人的认知带偏。
    锁成两向：谁在别的端实现了，或撤掉了某端的实现，README 都必须同步。

    2026-09-23（外部评审报告 M2）——**本条锁自己也是那个 bug 的一部分**，修的是它的盲区：
      · 后端端的豁免实现在 `2013d14`(2026-09-16 16:26) 落进 **loader**
        `backend_layer_check.py::_inline_waived`，而 README 那句更新于更早的 `96fc700`(15:27)；
        当时的 `END_RULESETS["后端"]` 只列 `*_ruleset.py` 两个文件，**扫不到 loader**
        → 「README 说没有」与「锁扫不到实现」互相印证，持续假绿了 7 天。
      · 教训与 `checks/` 的分层有关：PC/H5 的豁免在 ruleset 里，后端的在 loader 里 ——
        「同一个机制在三个端落在不同层」，所以清单必须逐个端把两侧都列全，不能按文件后缀猜。
      · 判据同时收紧：从「文件里出现过 harness-ok 这个词」改成「文件真的构造了那条匹配模式」
        （`harness-ok` + `re.escape(`）。否则 `rule_base.py:670` 那种**只负责打印汇总**的
        文件也会被判成"实现了"，等于换一种假绿。
      · 刻意**不**把扫描面扩成"全部 check 脚本"：脚本→端的归属没有单一数据源
        （`registry.py` 只映射 gate→脚本），按前缀猜归属会把这个锁变成另一个不可靠判据。
    """

    # 端 → 该端**可能实现行内豁免**的文件（loader + 规则集；后端端的编码规则由 loader 经
    # encoding_ruleset 跑）。见上面那条注释：三个端落点不同层，别按后缀缩清单。
    END_RULESETS = {
        "PC": ("checks/pc_code_check.py", "checks/pc_ruleset.py"),
        "H5": ("checks/h5_code_check.py", "checks/h5_ruleset.py"),
        "后端": ("checks/backend_layer_check.py", "checks/backend_ruleset.py",
                 "checks/encoding_ruleset.py"),
    }

    @staticmethod
    def _implements(rel):
        """该文件是否**真的实现了**行内豁免（不只是提到这个词）。

        实现签名三端逐字一致：`re.compile(r"harness-ok:\\s*" + re.escape(<规则ID>) ...)`。
        要求 `re.escape(` 同现，就是为了把"只打印提示"的文件排除在外。
        """
        try:
            src = _read(os.path.join(ROOT, rel))
        except OSError:
            return False
        return "harness-ok" in src and "re.escape(" in src

    def test_readme_names_exactly_the_ends_that_implement_it(self):
        impl = sorted(end for end, rels in self.END_RULESETS.items()
                      if any(self._implements(r) for r in rels))
        self.assertTrue(impl, "一个端都没实现 harness-ok？那 README 整条都该删")
        txt = _read(os.path.join(ROOT, "README.md"))
        m = re.search(r"harness-ok[^\n]*（适用端：([^）]+)）", txt)
        self.assertIsNotNone(
            m, "README 未标注 `harness-ok` 的适用范围（应写成「（适用端：X / Y / Z）」）——"
               "不标注的话，没实现该机制的端会照做且困惑")
        claimed = sorted(x.strip() for x in m.group(1).split("/"))
        self.assertEqual(claimed, impl,
                         "README 说 harness-ok 适用于 %s，代码里实际实现的端是 %s"
                         "（改了实现记得同步 README 这句）" % (claimed, impl))

    def test_predicate_is_not_vacuous(self):
        """上面那条锁的判据不许空转：`rule_base.py` 里出现过 `harness-ok`（只打印汇总），
        必须被判成"未实现"；三个端的实现文件必须被判成"实现了"。

        没有这一条，把 `_implements` 写成 `return False` 也能让主锁"通过"（只是 impl 变空，
        然后被 assertTrue 拦住）；写成 `return True` 则清单永远等于 README 写的 —— 两向都要钉住。
        """
        self.assertFalse(self._implements("checks/rule_base.py"),
                         "rule_base.py 只汇总打印 harness-ok（rule_base.py:670），没有实现豁免机制，"
                         "判据不能把它算成实现")
        for end, rels in self.END_RULESETS.items():
            self.assertTrue(any(self._implements(r) for r in rels),
                            "%s 端的实现文件里一个都没被判成实现：%s" % (end, rels))


class TestAbsenceClaimsAreHonest(unittest.TestCase):
    """「尚未提供 / 未入库」类断言必须仍然为真（把 Known gaps 变成可机械判定）。"""

    def test_no_doc_claims_absence_of_something_present(self):
        bad = []
        for rel in NUMERIC_DOCS:
            if not os.path.isfile(os.path.join(ROOT, rel)):
                continue
            txt = _read(rel)
            for line, p in false_absence_claims(
                    txt, lambda q: os.path.exists(os.path.join(ROOT, q.replace("/", os.sep)))):
                bad.append("%s:%d 声称「未提供/未入库」但文件存在：%s" % (rel, line, p))
        self.assertEqual([], bad,
                         "文档对**已经存在**的东西说「尚未提供/未入库」——\n"
                         "这正是本轮修的 Known gaps 漂移（缺口早已闭合却仍挂着未闭合）：\n"
                         + "\n".join(bad))

    def test_predicate_flags_true_claim_and_spares_false_one(self):
        """谓词两向自检（上面那条锁目前无命中，靠这里证明它不是空转）。"""
        txt = ("- `checks/registry.py` 尚未入库\n"          # 存在 → 必须报
               "- `checks/no_such_file.py` 尚未入库\n"      # 不存在 → 不报（真话）
               "- 未提供的是公开发布到 PyPI\n"               # 无路径 → 不报
               "- 台账写入失败静默（设计使然）\n")            # 无否定词 → 不报
        exists = lambda q: q == "checks/registry.py"
        self.assertEqual([(1, "checks/registry.py")], false_absence_claims(txt, exists))


# 评审 v9（V9-8）：main() 必须留在**文件末尾** —— 放中间时直跑会漏收其后的用例
# （发现：`python tests/test_docs_consistency.py` 少跑 2 例，而 discover 不受影响）。


# ── 升级面文档（2026-09-17 剖析报告 U-4：G6 + 两处顺带发现） ────────────────
# G6 的形状：`INSTALL.md` 用了「原地升级」这个词，全文却没有定义，也没说包该解压到哪 ——
# 同一个词在「覆盖同一目录」与「解压到新目录」两种布局下**含义相反**，同事照着读会走岔。
# 文档里的"用了一个没定义的词"靠人眼发现不了（它读起来很顺），故锁成机械判据。
#
# 与 `test_historical_docs_stay_out_of_numeric_locks` 不冲突：那条排除的是**数字**锁
# （CHANGELOG 里的数字是当时的真实值，拿今天去比=逼人篡改历史）。这里锁的是**结构**
# —— 「有没有这个版本段」与"数字"无关，加版本段不会让任何历史数字变假。
class TestUpgradeSurfaceDocsAreSelfConsistent(unittest.TestCase):
    """升级这条路上的三个文档事实：定义 / 布局 / 过期说法。"""

    def test_in_place_upgrade_is_defined_where_it_is_used(self):
        """用了「原地升级」这个词，就必须有它的定义句（G6：全文没有定义）。"""
        txt = _read("docs/packaging/INSTALL.md")
        self.assertIn("原地升级", txt,
                      "安装说明里已经不出现「原地升级」了？那本锁连同本节可以一起删")
        m = re.search(r"「\*\*原地升级\*\*」\s*=\s*\*\*", txt)
        self.assertIsNotNone(
            m, "安装说明用了「原地升级」却没有定义句 —— 报告 G6 原文：同一个词在"
               "「覆盖同一目录」与「解压到新目录」两种布局下含义完全相反，不定义就会走岔。"
               "定义句的形态：`「**原地升级**」= **……**`（改了措辞请同步本锁）")
        body = txt[m.start():m.start() + 200]
        self.assertIn("路径", body,
                      "定义句没说清「不变的是什么」——原地指的是**包目录位置不变**，"
                      "不是「什么都不用管」（这两句必须说开）")

    def test_both_layouts_and_their_market_commands_are_documented(self):
        """两种布局都要写明，且插件升级命令要**按布局分开写**。

        报告 05 的复测更正把这件事收窄成一句：布局的真实差别只在第一条命令是
        `marketplace update`（路径没变）还是 `marketplace add <新路径>`（路径变了）。
        只写其中一条，另一种布局的同事就会照做着失败（而 add 是**静默改指**，不报错）。
        """
        txt = _read("docs/packaging/INSTALL.md")
        self.assertIn("按版本目录", txt,
                      "安装说明没写「按版本目录」这个例外布局（只写一种，另一种的同事会照错）")
        self.assertRegex(txt, r"%USERPROFILE%\\agentguard",
                         "安装说明没给出**固定目录**的具体路径（建议值 %USERPROFILE%\\agentguard）")
        self.assertIn("marketplace update", txt,
                      "插件升级少了「路径没变」那一条（marketplace update）")
        self.assertIn("marketplace add", txt,
                      "插件升级少了「换过目录」那一条（marketplace add <新路径>）——"
                      "漏了它，按版本目录的同事会以为必须 uninstall 重装")

    def test_release_notes_do_not_revive_the_dead_download_channel_claim(self):
        """发布说明里不许再出现「内网渠道用中文名 zip」这句**已不成立**的旧说法。

        依据在仓内且可查：`_TEMPLATE.md:26` 与 `docs/releases/README.md:16` 都写明
        「内网用中文名 zip」是旧说法；代码佐证 `pack_release.py` 只产出中文名 zip，
        而内网镜像用的是 **ASCII 名**（与 GitHub 附件同一文件）。2026-09-17 之前
        v0.1.1 / v0.1.2 两份正文都还挂着它，与自己的模板打架。
        """
        import glob
        bad = []
        for p in sorted(glob.glob(os.path.join(ROOT, "docs", "releases", "v*.md"))):
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            for i, line in enumerate(_read(rel).splitlines(), 1):
                if "内网渠道用中文名" in line or "内网用中文名" in line:
                    bad.append("%s:%d" % (rel, i))
        self.assertEqual([], bad,
                         "发布说明里又出现「内网渠道用中文名 zip」这句旧说法（模板已判它不成立）：%s"
                         % bad)

    def test_changelog_has_a_section_for_the_current_version(self):
        """`CHANGELOG.md` 必须有与 `VERSION` 同名的版本段。

        这条机制此前不存在：全文只有 `## [Unreleased]` 一个标题，于是「这条改动是哪一版
        出的」在 CHANGELOG 里查不到，只能去翻 `docs/releases/`（报告 2026-09-17 顺带发现）。
        发版流程的判据因此补成：升 `VERSION` 的同一次提交，必须把 `[Unreleased]` 里属于
        本版的内容移进 `## [<版本>]`。
        """
        version = _read("VERSION").strip()
        self.assertTrue(version, "VERSION 是空的")
        txt = _read("CHANGELOG.md")
        self.assertIn("## [%s]" % version, txt,
                      "CHANGELOG 里没有 `## [%s]` 版本段（VERSION 已是这一版，"
                      "发版时却忘了留段）—— 那样「这条改动是哪一版出的」就无从查起" % version)
        self.assertIn("## [Unreleased]", txt,
                      "CHANGELOG 少了 `## [Unreleased]`（新改动该往哪写就没了约定）")


# ── 范式索引一致性（2026-09-20）：让 `golden-patterns/index.md` 不再漂 ──────────
# 被锁的那份文档自己就把这件事写成了待办（`index.md:28-33`）：索引漂过一次 ——
# 2026-09-19 查出 6 份新风范**整批漏登记**（它们 09-07 就建好了，索引 09-12 改过一轮
# 仍没带上）。那是「同一份映射两个手维副本」这个病，本仓已经复发过一次。
GOLDEN = os.path.join(ROOT, "golden-patterns")

# index.md 里点名一个范式文件时的写法是**反引号包住全相对路径**（`pc/pc_batch.md`）。
TOKEN_RE = re.compile(r"`([A-Za-z0-9_\-]+/[^`]*\.md)`")


def golden_dirs(root):
    """`golden-patterns/` 下的一级子目录（backend / pc / h5 / endtoend / rules / templates）。

    **从目录实况取，不写死清单** —— 写死的话，新加一个端目录就等于给锁开了个后门。
    """
    return sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))


def golden_listing(root):
    """`{端目录: [直接子 .md 文件名]}` —— **只取直接子文件，不下钻**。

    不下钻是刻意的：`h5/组件手册/h5/` 下那 24 份组件手册由 `h5/组件手册/索引.md`
    统一登记，逐个要求登记等于把索引变成第二份名单（正是本锁要治的那个病）。
    """
    out = {}
    for end in golden_dirs(root):
        d = os.path.join(root, end)
        out[end] = sorted(f for f in os.listdir(d) if f.endswith(".md"))
    return out


def unregistered_patterns(txt, listing):
    """目录里有、但索引里没登记的范式文件（返回 `端/文件.md`）。

    判据是**全相对路径的子串包含**，不是裸文件名：`pc_dev_sop.md` 这种短名可能被
    更长的名字包含，按裸名匹配会漏报；而全路径正是文档自己的写法。

    ⚠ 已知的宽松处（**刻意留着**）：路径出现在索引的**任何位置**都算登记 ——
      表格行、`## 规则层` 的列表项、甚至正文里的一句提及（`templates/AGENTS_pc_slim.md`
      就是这样登记的）。所以它判的是「这份范式在索引里被提到过、找得到」，
      **不是**「它在表格里有一行」。要收紧成"必须占一行"就得先重排现有索引，
      收益不抵改动，故不做；真正的失效模式（新建了范式、通篇没人提）它照样逮得住。
    """
    bad = []
    for end in sorted(listing):
        for f in listing[end]:
            rel = "%s/%s" % (end, f)
            if rel not in txt:
                bad.append(rel)
    return bad


def dangling_pattern_paths(txt, isfile, dirs):
    """索引里点名、但文件不存在的范式路径（返回那个 token）——「指空」。

    只认**第一段是 golden-patterns 真子目录**的 token。文档里还写着
    `KnowledgeVault/concepts/…`、`D:/MyProject/aiauto/…` 这类**外部**路径，
    它们本来就不该在 golden-patterns 下存在，不能要求它们存在。
    """
    bad = []
    for tok in TOKEN_RE.findall(txt):
        if tok.split("/")[0] not in dirs:
            continue
        if not isfile(tok):
            bad.append(tok)
    return bad


class TestGoldenPatternsIndexIsComplete(unittest.TestCase):
    """`golden-patterns/index.md` 的「按端速查」是手维目录 → 用锁钉住它与目录实况的双向一致。

    ⚠ 这把锁只管**目录 ↔ 索引的映射**，**管不了范式正文的对错** —— 正文写错了它不会响。
      正文质量靠覆盖审计（人/agent），别把这条锁读成质量保证。
    """

    def test_every_pattern_file_is_registered_in_index(self):
        """目录里每一份范式都必须在索引里出现（漏登记 → 红）。

        加锁当天（2026-09-20）实测索引是**全**的（6 个子目录 35 份直接子文件全在表内），
        所以这条锁是**防复发**，不是修红。它要逮的是下一次「新建了范式忘了登记」。

        ⚠ 边界：只扫**端目录的直接子文件**。`golden-patterns/` 根下若将来出现散落的 `.md`
          （`index.md` 自己不算，目前根下无其它 `.md`），不在本锁范围内 —— 那种文件
          要不要进索引是编辑判断，机械要求反而会逼人给便签也登记一行。
        """
        txt = _read(os.path.join(GOLDEN, "index.md"))
        missing = unregistered_patterns(txt, golden_listing(GOLDEN))
        self.assertEqual([], missing,
                         "这些范式文件存在、却没在 `golden-patterns/index.md` 里登记：\n  "
                         + "\n  ".join(missing)
                         + "\n（索引漂过：2026-09-19 查出 6 份整批漏登记。"
                           "往「按端速查」表或对应小节补一行即可；取不准就以 `ls` 为准。）")

    def test_every_registered_path_exists(self):
        """索引里点名的每个范式路径都必须真的存在（指空 → 红）。

        与上一条是**两个方向**：漏登记是"有文件没人指"，指空是"有人指没文件"。
        改文件名 / 删范式时只改一边，这条就会响。
        """
        txt = _read(os.path.join(GOLDEN, "index.md"))
        dirs = set(golden_dirs(GOLDEN))

        def isfile(rel):
            return os.path.isfile(os.path.join(GOLDEN, rel.replace("/", os.sep)))

        dangling = dangling_pattern_paths(txt, isfile, dirs)
        self.assertEqual([], dangling,
                         "`golden-patterns/index.md` 点名了这些路径，但它们不存在（指空）：\n  "
                         + "\n  ".join(dangling)
                         + "\n（多半是改了文件名或删了范式，只改了一边。）")

    def test_predicates_are_not_vacuous(self):
        """两个判据各自两向自检 —— 证上面两条锁**不是空转**（本仓既有习惯）。

        没有这条，一个恒返回 `[]` 的判据会让上面两条永远绿，而没人发现。
        """
        listing = {"pc": ["pc_real.md"]}
        self.assertEqual(["pc/pc_new.md"],
                         unregistered_patterns("`pc/pc_real.md`", {"pc": ["pc_real.md", "pc_new.md"]}),
                         "有文件没登记时判据没报出来")
        self.assertEqual([], unregistered_patterns("`pc/pc_real.md`", listing),
                         "登记齐了却误报")

        dirs = {"pc", "rules"}
        exists = lambda rel: rel == "pc/pc_real.md"
        self.assertEqual(["pc/pc_gone.md"],
                         dangling_pattern_paths("`pc/pc_real.md` `pc/pc_gone.md`", exists, dirs),
                         "指空时判据没报出来")
        self.assertEqual([], dangling_pattern_paths("`pc/pc_real.md`", exists, dirs),
                         "没指空却误报")
        # 外部路径（第一段不是 golden-patterns 子目录）不该被要求存在 —— 这是最常见的误报源
        self.assertEqual([], dangling_pattern_paths(
            "`KnowledgeVault/concepts/技术规范/跨文件Partial类对照表.md`", exists, dirs),
            "把文档里的外部路径当成必须存在的范式文件了")


# ── 组件数口径一致（2026-09-20）：同一个端不许有两个组件数 ────────────────────
# 起因是真出过的 bug：`golden-patterns/h5/组件手册/索引.md` 的基准行写「H5 88 组件 +
# PC 9 组件」，同一份文件「PC（要整理）」那段却写「PC 主流组件 = `src/utils/registerGlobComp.ts`
# 全局注册的 86 个 eagle-*.vue 散件」。那个 9 是紧接着那段的**9 个重封装目录**
# （明确标注「不整理」），跟 86 是**两个不同的集合**，却被同一个名词「组件」并排写着。
COMPONENT_MANUAL_INDEX = os.path.join(GOLDEN, "h5", "组件手册", "索引.md")

# 一条「组件数断言」= 数字**紧邻**其后就是 组件 / 散件。
# `(?<![A-Za-z0-9])` 不能省：被锁的文件里就有 `pc/ ← PC Vue3 组件`，
# 少了它会把 `Vue3` 的 3 当成「3 组件」。
COUNT_CLAIM_RE = re.compile(r"(?<![A-Za-z0-9])(\d+)\s*个?\s*(?:eagle-\*?\.?vue\s*)?(散件|组件)")
END_NAME_RE = re.compile(r"(H5|PC)")

# 端名与数字之间可能隔着整串口径说明（那行 PC 与 86 之间就隔了 46 字），
# 故按「同一行内、**最近的前一个**端名」归属，窗口给到 60 字。
END_ATTR_WINDOW = 60

# 「N 个…重封装…（a/b/c/…）」。锚在「重封装」上是刻意的：同一行里另有一个
# `（table/form/…等，全项目可用）`，那是**举例**不是清单，不能拿来比个数。
ENUM_DIR_CLAIM_RE = re.compile(
    r"(?<![A-Za-z0-9])(\d+)\s*个[^\n（(：:]{0,12}重封装[^\n（(：:]{0,6}[（(]([^）)\n]*)[）)]")


def component_count_claims(txt):
    """`{端: {数字}}` —— 组件数断言，按**同一行内最近的前一个端名**归属。

    归属**向后看**而不是向前扫，两个理由都来自真实文本：
      · 那行的 PC 与 86 之间隔了 46 字，向前扫得开很大的窗口；
      · 窗口一开大就会跨过断言边界 —— `H5 88 组件 + PC 9 组件` 里，向前扫会把 9
        记到 H5 名下（我第一版就是这么写的，自检当场逮住）。
    中文语序把限定语放在数字**前**面（「PC … 86 个散件」），所以向后看才对。

    行内找不到端名、或端名离数字超过 `END_ATTR_WINDOW` 字 → **不猜**，跳过。
    """
    out = {}
    for line in txt.splitlines():
        for m in COUNT_CLAIM_RE.finditer(line):
            ends = list(END_NAME_RE.finditer(line[:m.start()]))
            if not ends or m.start() - ends[-1].end() > END_ATTR_WINDOW:
                continue
            out.setdefault(ends[-1].group(1), set()).add(int(m.group(1)))
    return out


def enumerated_dir_count_mismatches(txt):
    """「N 个…重封装…（a/b/c）」里**声明数与列出项数不符**的 `(声明, 实列)`。

    括号里带「等」的**跳过** —— 那是举例（被锁文件里「9 个重封装目录
    （`eagle-pro-table`/`ProForm`/`svgIcon` 等）」就是），拿它比个数必错；
    只有把整份清单摆进括号里，个数才有可比性。

    ⚠ 已知宽松处（**刻意**）：清单若写成斜杠/顿号/逗号以外的分隔形式就不比了 ——
      宁可漏报也不误报（误报会逼人把锁删掉）。
    """
    bad = []
    for m in ENUM_DIR_CLAIM_RE.finditer(txt):
        if "等" in m.group(2):
            continue
        items = [x for x in re.split(r"[/、,，]\s*", m.group(2).strip()) if x]
        if len(items) != int(m.group(1)):
            bad.append((int(m.group(1)), len(items)))
    return bad


class TestComponentCountsAreUnambiguous(unittest.TestCase):
    """`h5/组件手册/索引.md` 里「同一个端只能有一个组件数」——治 2026-09-20 查出的真矛盾。

    被锁的文件自己就是反例（**订正前**的基准行）：「H5 88 组件 + PC 9 组件」，而同文件
    「PC（要整理）」那段写 PC 是 **86** 个全局注册散件。那 9 是**9 个重封装目录**
    （明确标注「不整理」），跟 86 是**两个不同的集合**，却被同一个名词「组件」并排写着 ——
    谁读都会把 9 当成 PC 的组件数。这份文档是 AI 写组件代码时的入口，读错一个数就照错的去查手册。

    ⚠ 边界（**刻意收窄**，别读成质量保证）：
      · 只管**这一个文件**。判据函数是通用的，但「端名离数字不超过 60 字」这条放到
        别的文档上会误报 —— 客户端仓的真数本来就散在 56~86，写「某仓有 56 个散件」
        是**对的**，不该被拦。
      · 只认**紧邻**数字的 组件/散件（`86 个 eagle-*.vue 散件`）。写成「PC 的组件数是
        86」这种隔了字的句子它抓不到 —— **抓不到就不报**，宁漏不误。
      · **数字本身对不对它不管**：88 / 86 都出自基线仓，而基线仓是客户端仓、不在本仓里。
        拿它当判据就是把夹具写死在开发机上 —— 本仓为这个病栽过三次，不再栽第四次。
        它只管「同一份文档里自相矛盾」。
    """

    def test_each_end_has_only_one_component_count(self):
        """同一个端出现两个组件数 → 红（正是那把真出过的 bug）。"""
        claims = component_count_claims(_read(COMPONENT_MANUAL_INDEX))
        bad = {e: sorted(v) for e, v in claims.items() if len(v) > 1}
        self.assertEqual({}, bad,
                         "`golden-patterns/h5/组件手册/索引.md` 里同一个端出现了多个组件数：%s\n"
                         "（2026-09-20 真出过：「PC 9 组件」里那个 9 其实是 9 个**重封装目录**，"
                         "跟 86 个全局注册散件不是一回事。把两个集合的名词写清即可。）" % (bad,))

    def test_enumerated_dir_count_matches_what_is_listed(self):
        """「N 个…目录（a/b/c）」的 N 必须等于括号里列出的项数（清单长了、数字没跟上 → 红）。"""
        bad = enumerated_dir_count_mismatches(_read(COMPONENT_MANUAL_INDEX))
        self.assertEqual([], bad,
                         "声明数与实际列出的项数不符 (声明, 实列)：%s\n"
                         "（`索引.md` 的「9 个重封装目录（…）」—— 增删目录时数字要一起改。）"
                         % (bad,))

    def test_predicates_are_not_vacuous(self):
        """两个判据各自两向自检，并用**当初那种自相矛盾的原话**证伪（本仓既有习惯）。

        没有这条，一个恒返回空的判据会让上面两条永远绿，而没人发现。
        """
        L73 = ("> ✅ **PC 主流组件 = `src/utils/registerGlobComp.ts` 全局注册的 "
               "86 个 eagle-*.vue 散件**（table/form/select/upload/date/page/tree 等，"
               "全项目可用），与 H5 的 eagle-* 体系对应。")

        # ① 订正前的基准行（原文）+ 上面这行原文 —— 必须报出 PC 有两个数。
        #    这就是当初那把 bug 的最小复现，判据逮不住它 = 这把锁是空的。
        old = component_count_claims(
            "> **基准**：基线 `base_custom_new`（所有客户共享，一库通用）。H5 88 组件 + PC 9 组件。\n"
            + L73)
        self.assertEqual({"H5": {88}, "PC": {9, 86}}, old,
                         "当初那种自相矛盾（基准行的 9 与下方那行的 86）没被逮住 —— 锁是空的")
        # ② 订正后的写法不该报（反向自检：判据不是恒红）
        self.assertEqual({"H5": {88}, "PC": {86}},
                         component_count_claims("H5 88 组件 + PC 86 组件。"),
                         "订正后仍报，说明判据过紧")
        # ③ 端名与数字之间隔着 46 字的口径说明时仍要归到 PC，不能落给行内更早的 H5
        self.assertEqual({"PC": {86}}, component_count_claims(L73),
                         "端名离数字远就丢了归属")
        # ④ `Vue3 组件` 不是断言 —— 被锁的文件里就有一处（`pc/ ← PC Vue3 组件`）
        self.assertEqual({}, component_count_claims("  pc/                  ← PC Vue3 组件"),
                         "把 `Vue3 组件` 当成了「3 组件」")
        # ⑤ 目录声明数：不符要报
        self.assertEqual([(9, 8)],
                         enumerated_dir_count_mismatches("9 个重封装目录（a/b/c/d/e/f/g/h）"),
                         "声明 9 列 8 却没报")
        # ⑥ 相符不报；括号里带「等」的是**举例**不是清单，跳过（否则基准行那句自己就会被误报）
        self.assertEqual([], enumerated_dir_count_mismatches(
            "9 个重封装目录（eagle-pro-table/ProForm/ProModel/eagle-charts/"
            "eagle-risk-position/eagle-set/eagle-user/icons/svgIcon）"),
            "相符却误报")
        self.assertEqual([], enumerated_dir_count_mismatches(
            "9 个重封装目录（`eagle-pro-table`/`ProForm`/`svgIcon` 等）"),
            "把「等」字举例当成了清单，去比个数")


# ── 端仓文件形态锁（2026-09-22）────────────────────────────────────────────────
# 为什么补：`README_推广接入.md` 曾**自相矛盾 12 天没人发现** —— 同一份文件里
#   §项目级文件最终形态：「**不要创建** `CODEBUDDY.md` / `.trae/` / `.qoder/`」
#   §各端仓库应放置：  「应放置三件套 …… - `.codebuddy/rules/agentguard/RULE.mdc`」
# 一边禁止一边推荐，而两处各自读起来都"很顺" —— 人眼对这类矛盾几乎无感。
# 2026-09-22 用户拍板：端仓只放**两件套**（`AGENTS.md` 权威源 + `CLAUDE.md` 一行桥）。
#
# 判据为什么这样定（本文件开头那两条纪律的延续：假漂移报告的代价是下一次真漂移没人信）：
#   ① 只判**列表项 / 表格行**，散文段免除。这不是宽容，是**漂移面**的实际形状：
#      散文里的判决语天然跨折行（实测本仓 6 处提及里有 3 处判决语落在续行上），
#      按"同一行"判会逼人把句子写瘸；按"±N 行窗"判又会造出一段影子区、违规躲进去看不见
#      —— 本锁的第一版探针就真漏了：注入行贴在文末，被上面 3 行的「不要放」罩住了。
#      而当年真复发的形态**就是一条列表项** ⇒ 判形态比判邻域准。
#   ② 只认**仓级**形态，`~/` 前缀的**用户级**配置是合法的：`~/.codebuddy/settings.json`
#      是 CodeBuddy 的钩子配置、`.trae-cn/` 是 Trae 的目录名，都不该误伤。
#      ★ 这条也是探针当场抓出来的：第一版判据把 `~/.trae-cn/hooks.json` 报成了违规。
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s")
_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_REPO_SHELL = re.compile(r"(?<!~/)(?<!~\\)(?:CODEBUDDY\.md|\.codebuddy/|\.trae/|\.qoder/)")
_SHELL_NEGATED = ("不要", "不放", "别", "不许", "禁止", "已删", "删除", "删掉",
                  "零残留", "不需要", "无生成者", "不走", "刻意不", "不是", "过头")
# 本锁扫的文档（新同事接入指南尤其容易成为回退的落点）
_SHAPE_DOCS = ("README.md", "README_推广接入.md", "docs/新同事接入指南.md")


class TestEndRepoFileShapeIsTwoFiles(unittest.TestCase):
    """端仓文件形态 = 「两件套」。防的是**回退**（把第三件加回文档），不是修红。"""

    DOC = "README_推广接入.md"

    def test_it_declares_two_files_and_stops_recommending_three(self):
        """「两件套」必须在；「放置三件套」这种推荐形态不许再出现。"""
        txt = _read(self.DOC)
        self.assertIn("两件套", txt,
                      "%s 不再声明「两件套」了？改了措辞请同步本锁，别直接删锁" % self.DOC)
        flat = txt.replace("**", "")
        for bad in ("放置三件套", "三件套（", "三件套："):
            self.assertNotIn(bad, flat,
                             "文档又在推荐「三件套」了（2026-09-22 已拍板端仓只放两件；"
                             "若这是「三件套」的**其它含义**，请把本判据收窄，不要删锁）")

    def test_no_row_or_bullet_recommends_a_repo_level_shell(self):
        """列表项 / 表格行提到**仓级**方言薄壳时，必须**本行**带「已否决/已处置」措辞。"""
        bad = []
        for rel in _SHAPE_DOCS:
            if not os.path.isfile(os.path.join(ROOT, rel)):
                continue
            for i, line in enumerate(_read(rel).split("\n"), 1):
                if not _REPO_SHELL.search(line):
                    continue
                if not (_LIST_RE.match(line) or _ROW_RE.match(line)):
                    continue                       # 散文段：判决语可跨折行，不在此判
                if not any(n in line for n in _SHELL_NEGATED):
                    bad.append("%s:%d: %s" % (rel, i, line.strip()[:120]))
        self.assertEqual(bad, [],
                         "这些列表项/表格行在推荐往端仓放方言薄壳 —— 2026-09-22 已删净"
                         "（那两份 `RULE.mdc` 是规则正文的第二份副本，还把维护机绝对路径写死 3 处；"
                         "且 CodeBuddy 本来就走 `AGENTS.md` fallback）：\n"
                         + "\n".join(bad))

    def test_the_predicate_is_not_vacuous(self):
        """判据两向自检 —— 证上面那条锁**不是空转**（本仓既有习惯）。"""
        def flags(line):
            return bool(_REPO_SHELL.search(line)
                        and (_LIST_RE.match(line) or _ROW_RE.match(line))
                        and not any(n in line for n in _SHELL_NEGATED))

        # 当年真复发的形态 → 必须报
        self.assertTrue(flags("- `.codebuddy/rules/agentguard/RULE.mdc`（`alwaysApply: true`）"),
                        "旧「三件套」那条列表项现在竟然不报了")
        self.assertTrue(flags("| **CodeBuddy** | 根 `CODEBUDDY.md` 薄壳指路 |"),
                        "推荐的表格行不报")
        self.assertTrue(flags("2. 根目录放 `.trae/rules` 薄壳"), "有序列表项不报")
        # 合规写法 → 不许误报
        self.assertFalse(flags("- **不要创建** `CODEBUDDY.md` / `.codebuddy/` / `.trae/`"),
                         "禁止语被误报")
        self.assertFalse(flags("| **Qoder CN** | 根 `AGENTS.md` | 不需要任何 `.qoder/` 薄壳 |"),
                         "「不需要」被误报")
        # 用户级配置与散文 → 不在本锁射程内
        self.assertFalse(flags("| CodeBuddy | `~/.codebuddy/settings.json` |"),
                         "用户级钩子配置被误伤（本锁第一版真踩过这个坑）")
        self.assertFalse(flags("- `.trae-cn/` 是 Trae 的目录名"), "`.trae-cn/` 被当成仓级 `.trae/`")
        self.assertFalse(flags("它**不是指路，是把规则正文抄了一份**，那份 `.codebuddy/x` 已删"),
                         "散文段本该免除")


class TestElDrawerDecisionIsConsistent(unittest.TestCase):
    """el-drawer = **可直接用**（2026-09-19 业务决定：项目无 `eagle-drawer` 封装）。
    规则文档不得把它写回「禁止」清单 —— 用户 2026-10-15 当场抓到一处陈旧清单。

    病灶：`pc_CODE_RULES.md` 第二章硬约束把 `el-drawer` 留在禁用枚举里，而**同一文件**
    §6.3 / §10.1 写着「抽屉直接用 el-drawer；项目无 eagle-drawer」—— 自相矛盾；
    候选表 P4 行也带着同一个陈旧枚举（09-01 写的，没跟上 09-19 决定）。若照陈旧清单
    加规则，就是报一个指向不存在组件的假规则（allowlist 原话：拿假规则骚扰真代码，
    只会训练使用者忽略它）。

    判据（宁可漏报、不可误报）：规则文档里**同一行**同时出现 `el-drawer` 与「禁」字时，
    必须同行带**放行/例外标记**；合法写法如 §10.1 的「禁止直接使用：…el-dialog…。
    `el-drawer` **可直接用**」带标记 → 不误报。
    ⚠ 标记里刻意**不收**「确无对应能力」——那句笼统豁免正是旧 L67 陈旧清单自带的尾句，
    收它就等于给旧文案发通行证（变异 M136 会打回旧文案，验证这把锁真咬得住）。
    """

    RULE_DOCS = (
        "golden-patterns/rules/pc_CODE_RULES.md",
        "golden-patterns/pc/pc_components_request.md",
        "golden-patterns/pc/pc_page_patterns.md",
        "golden-patterns/pc/pc_vue_form_drawer.md",
        "golden-patterns/templates/AGENTS_pc_slim.md",
        "golden-patterns/rules/_01_门禁规则候选.md",
    )
    _ALLOW_MARKERS = ("可直接用", "可以直接用", "直接用 `el-drawer`",
                      "不拦", "移出", "不在此列", "允许")

    def test_no_rule_doc_line_forbids_el_drawer(self):
        bad = []
        for rel in self.RULE_DOCS:
            path = os.path.join(ROOT, rel.replace("/", os.sep))
            if not os.path.isfile(path):
                continue
            for i, line in enumerate(_read(path).splitlines(), 1):
                if "el-drawer" not in line or "禁" not in line:
                    continue
                if any(m in line for m in self._ALLOW_MARKERS):
                    continue
                bad.append("%s:%d  %s" % (rel, i, line.strip()[:100]))
        self.assertEqual(bad, [],
                         "规则文档把 el-drawer 写回了禁止清单（2026-09-19 已定：可直接用）：\n  "
                         + "\n  ".join(bad))

    def test_allowlist_keeps_el_drawer_out_of_deny(self):
        """引擎真值：components_allowlist.yaml 的 el_deny 只许 el-dialog，el-drawer 不得回归。"""
        txt = _read(os.path.join(ROOT, "checks", "components_allowlist.yaml"))
        entries, cur = [], None
        for line in txt.splitlines():
            s = line.strip()
            if s and not s.startswith("#") and s.endswith(":") and not s.startswith("-"):
                cur = s[:-1]
                continue
            if cur == "el_deny" and s.startswith("-"):
                entries.append(s[1:].split("#", 1)[0].strip())
        self.assertIn("el-dialog", entries)
        self.assertNotIn("el-drawer", entries,
                         "el-drawer 不得回到 el_deny —— 项目无 eagle-drawer，报它=指向不存在组件的假规则")


if __name__ == "__main__":
    unittest.main()
