#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""文档一致性锁（2026-09-13）

为什么要有：这仓库踩过"改了内容忘了改数字"的坑——同一个事实散落在多份文档、
各自写了一个数。这类漂移不该再靠人眼，用最便宜的锁钉住：

  1. README「接入模式」表里点名的客户端，必须在 integrations/<客户端>/ 下有 README
     （新接一款工具只改表不写文档 → 红）。
  2. 数字断言（TestDocNumericClaims）：gate 数 / 仓数 / Python 版本要求
     —— 一律从 `config/harness.toml`、`pyproject.toml` **动态取事实**，不抄数字。
  3. 历史记录豁免（test_historical_docs_stay_out_of_numeric_locks）：CHANGELOG
     的数字是**当时的真实值**，纳入数字锁 = 逼人篡改历史。
  4. 缺口语义（TestAbsenceClaimsAreHonest）：文档说「某某尚未入库」而该路径**存在**
     → 红。这把「Known gaps」从人肉维护变成可机械判定。
  5. 逃逸口适用范围（TestEscapeHatchScopeMatchesCode）：README 对 `harness-ok`
     适用端的声明必须与代码实况一致。

只做文件解析，不起子进程、不依赖平台，毫秒级。
"""
from __future__ import unicode_literals

import pathlib

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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

def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ── 可移植性（2026-09-15）：门禁要能原样分享给同事 ──────────────────────────
# 维护者自用的个人 skill / 个人工具**不得**成为运行时依赖：引擎层（下列目录）出现这些名字即红。
# 文档里提到时必须**条件化**（"不依赖 / 可跳过 / 若你… / 没有也…"），否则同事会以为要先装它们。
PERSONAL_DEPS = ["worktree-manager", "huayu-manager", "codebase-memory", "farmerCore"]
ENGINE_DIRS = ["checks", "harness", "config", "sandbox"]
CONDITIONAL = ("不依赖", "不受影响", "照常", "可跳过", "若你", "可选", "有则", "没有也")
_ENGINE_SUFFIX = (".py", ".sh", ".toml", ".yaml", ".yml", ".json", ".md")


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



# ── 数字断言锁（2026-09-16 剖析报告 A1） ──────────────────────────────────────
# 为什么补：本次修了 6 处「文档与实现漂移」。逃逸原因不是人懒，是**锁的覆盖面** ——
# test_entry_level.py 的计数锁只扫 README.md / AGENTS.md，其余文档里同一条事实可以
# 自由地写成另一个数（同一句话在两份文档里各写一个数时无人发现）。
# 这里把**数字事实**扩到全部会写它的文档。
#
# 两条纪律（缺一个这锁就会变成噪音，而噪音的害处是「下次真漂移没人看」）：
#  ① 只锁**能机械取到事实**的数（gate 数 / Python 版本）。取不到来源的宁可不锁。
#  ② `N 仓` 在不同文档里可能有不同含义（全量 / 曾扫过的仓数 / 已有某文档的仓数），
#     所以仓数锁只认**唯一无歧义的那个短语**，不做泛化匹配。
#  ③ CHANGELOG 是**带日期的历史记录**，里面的数字是当时的真实值；
#     拿今天的数去比等于要求人篡改历史 —— 明确排除。
# 低 18（评审 v13）：原先缺 OPENCLAW.md —— 而它恰恰是**随包出货**的那份（pack_release.py
# 把它复制进安装包的 OPENCLAW.md），读者是拿到包的同事。它写着「harness_list_gates 返回
# 33/33」，config 早已是 34（2026-09-14 本仓注册 harness_self 自检 gate 时从 33 变 34）——
# 数字锁管不到，于是漂移一直躺在出货件里。补进来，并补上它用的那种写法（见下）。
NUMERIC_DOCS = ("README.md", "AGENTS.md",
                "docs/adr/0001-packaging-principles.md",
                "docs/packaging/OPENCLAW.md",
                "docs/packaging/RUNBOOK.md", "docs/packaging/COMPAT.md",
                "docs/packaging/INSTALL.md")
HISTORICAL_DOCS = ("CHANGELOG.md",)
# 只认「**要求**」形态，不认「某个 API 从哪个版本起有」这类说明：
#   README:17「3.10 及以下缺少 write_text(newline=) 等 API」这类句子是**真话**，
#   与「要求 3.11+」不矛盾 —— 泛化匹配会把它误判成漂移（本锁首跑就真踩过这个坑：
#   报了「文档要求 3.10+」）。所以要锚定在「Python」/加粗/「≥」上。
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

    **不再回仓数**（2026-09-18 跨平台批次 2）：`config/harness.toml` 的 `[repos]` 段已刻意
    留空，路径下沉到 gitignored 的 `config/harness.local.toml`（各人不同）。换句话说
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

        CHANGELOG 里的用例数是**当时那一版**的真实值，今天套件长大了不使它变假。
        这条锁防的是后来者顺手把 CHANGELOG 塞进 NUMERIC_DOCS。
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
                      "config/harness.local.toml 里），别人 clone 后这个数字就是错的；"
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

    def test_python_requirement_is_consistent(self):
        """文档里的 Python **版本要求**必须等于 pyproject 的 requires-python。

        只认要求形态（见 `_PY_REQ_PATTERNS` 的注释）：本锁首跑时报了
        「文档要求 3.10+」，实际那句是「引擎用到 3.10+ API」—— 说明/要求不分家
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
        # 当前文档里剩 README 的 `**3.11+**` 与 INSTALL 的 `Python 3.11+` 两处。
        self.assertGreaterEqual(
            found, 2, "只认出 %d 处 Python 版本要求（预期 ≥2）—— 文档改了措辞，"
                      "请同步 _PY_REQ_PATTERNS，别让这条锁空转" % found)


class TestEscapeHatchScopeMatchesCode(unittest.TestCase):
    """逃逸口的**适用范围**必须与代码实况一致。

    开源裁剪后本仓只剩「示例 gate」，行内豁免由 `rule_base.inline_waiver()` 统一实现、
    `example_code_check.py` 接线；README 必须**恰好**声明这一处，多写或少写都红。
    锁成两向：谁新增/撤掉某端的实现，README 都必须同步。
    """

    END_RULESETS = {
        "示例 gate": ("checks/example_code_check.py",),
    }

    @staticmethod
    def _implements(rel):
        """该文件是否**真的接上了**行内豁免（不只是提到这个词）。

        判据是出现 `inline_waiver(` —— 调用或定义都算（实现在 `rule_base.py`，
        入口在 `example_code_check.py`）。只写 `harness-ok` 字样的注释文件不算。
        """
        try:
            src = _read(os.path.join(ROOT, rel))
        except OSError:
            return False
        return "inline_waiver(" in src

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
        """上面那条锁的判据不许空转：只写规则的正则会判成"未实现"，接线的入口必须判成"实现了"。

        没有这一条，把 `_implements` 写成 `return False` 也能让主锁"通过"（只是 impl 变空，
        然后被 assertTrue 拦住）；写成 `return True` 则清单永远等于 README 写的 —— 两向都要钉住。
        """
        self.assertFalse(self._implements("checks/example_ruleset.py"),
                         "example_ruleset.py 只定义违规正则、没有接豁免，判据不能把它算成实现")
        self.assertTrue(self._implements("checks/example_code_check.py"),
                        "example_code_check.py 调用了 inline_waiver()，必须被判成实现")
        self.assertIn("def inline_waiver", _read("checks/rule_base.py"),
                      "通用豁免判定 inline_waiver() 不在 rule_base.py 里")
        for end, rels in self.END_RULESETS.items():
            self.assertTrue(any(self._implements(r) for r in rels),
                            "%s 的实现文件里一个都没被判成实现：%s" % (end, rels))


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
    """升级这条路上的两个文档事实：定义 / 布局。"""

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

    def test_changelog_has_a_section_for_the_current_version(self):
        """`CHANGELOG.md` 必须有与 `VERSION` 同名的版本段。

        这条机制此前不存在：全文只有 `## [Unreleased]` 一个标题，于是「这条改动是哪一版
        出的」在 CHANGELOG 里查不到（报告 2026-09-17 顺带发现）。
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


if __name__ == "__main__":
    unittest.main()
