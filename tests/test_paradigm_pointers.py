# -*- coding: utf-8 -*-
"""
范式指路锁（2026-10-15 批次 1，评审报告建议 #1 的防漂移层）
================================================================
背景：评审报告与外部确认一致指出「agent 被拦的那一刻最愿意读范式，但 PC/H5 命中文案
既没有规则 ID、也不给范式路径」—— 走廊断在最后一步。本批把两样都接上：

  · Rule.run 统一把规则类的 `see` 类属性盖章到 Finding（单一来源，后端原有 see 不受影响）；
  · format_finding 输出 `[规则ID] 消息` + `📖 See: <文档路径>`（与后端 `❌ RULE L行` 对齐）。

本文件锁三件事（全部只读、零临时文件）：
  ① 范式类规则必须带 see —— 缺了就退回「只说应走 eagle-form、不给路径」的断走廊；
  ② 所有非空 see 指向的文件在仓内真实存在 —— 指路指到空处比不给路径更糟
    （GLOBAL_AGENTS.md 静默死掉、留守行出货，都是这个同形失效）；
  ③ 盖章与输出的接线还在 —— 转换点再漂移会被这里拦住。

护盾：M133（Rule.run 去掉 see 盖章即红）/ M134（format_finding 丢 ID 即红），
见 sandbox/mutation_smoke.py 与 tests/lock-inventory.json。

设计取舍（为什么卫生规则不在必填名单）：CONSOLE/IP/TODO/CRLF/SYNTAX/EOL/SFC/VFOR
没有对应的范式文档，硬造指针等于指歪路 —— 必填名单只收「有真实文档可指」的范式规则。
"""
import os
import re
import sys
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, os.path.join(ROOT, "checks")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from rule_base import CheckContext, Finding, Rule, format_finding  # noqa: E402
from pc_ruleset import PC_RULES  # noqa: E402
from h5_ruleset import H5_RULES  # noqa: E402

# 范式类规则（必须有 see）。
REQUIRED_PC = {"PC-COMP", "PC-ELDENY", "PC-ELFORM", "PC-ELMSG", "PC-MODAL",
               "PC-VALIDATE", "PC-AXIOS", "PC-VAUTH"}
REQUIRED_H5 = {"H5-COMP", "H5-UPLOAD"}

# see 串里允许出现的路径形态：只许指仓内文档（golden-patterns/、checks/），
# 不许指仓外绝对路径、URL 或占位符 —— 指针必须机器无关（与业务仓 AGENTS.md 同一条纪律）。
_SEE_PATH_RE = re.compile(r"(?:golden-patterns|checks)/[^\s（;；，,）]+")


def _by_id(rules):
    return {r.id: r for r in rules}


class TestParadigmRulesHaveSee(unittest.TestCase):

    def test_pc_paradigm_rules_have_see(self):
        ids = _by_id(PC_RULES)
        missing = sorted(i for i in REQUIRED_PC
                         if i not in ids or not getattr(ids[i], "see", "").strip())
        self.assertEqual(missing, [], "这些 PC 范式规则缺 see 指针（走廊断在最后一步）：%s" % missing)

    def test_h5_paradigm_rules_have_see(self):
        ids = _by_id(H5_RULES)
        missing = sorted(i for i in REQUIRED_H5
                         if i not in ids or not getattr(ids[i], "see", "").strip())
        self.assertEqual(missing, [], "这些 H5 范式规则缺 see 指针（走廊断在最后一步）：%s" % missing)


class TestSeePathsExist(unittest.TestCase):

    def test_all_nonempty_see_paths_exist_in_repo(self):
        bad = []
        for rules in (PC_RULES, H5_RULES):
            for r in rules:
                for path in _SEE_PATH_RE.findall(getattr(r, "see", "") or ""):
                    if not os.path.isfile(os.path.join(ROOT, path.replace("/", os.sep))):
                        bad.append("%s -> %s" % (r.id, path))
        self.assertEqual(bad, [], "see 指向的文件在仓内不存在（指路指到空处）：%s" % bad)

    def test_see_is_machine_independent(self):
        """see 里不许出现盘符 / 仓库外绝对路径 / <HARNESS> 之外的占位符形态。"""
        bad = []
        for rules in (PC_RULES, H5_RULES):
            for r in rules:
                see = getattr(r, "see", "") or ""
                if re.search(r"(?<![A-Za-z])[A-Za-z]:[/\\]", see) or see.startswith("/"):
                    bad.append("%s -> %r" % (r.id, see[:60]))
        self.assertEqual(bad, [], "see 里混进了机器相关路径：%s" % bad)


class TestSeeWiring(unittest.TestCase):

    def test_rule_run_stamps_see_onto_findings(self):
        """护盾 M133 的锁：Rule.run 必须把类属性 see 盖章到 Finding。"""

        class _Stub(Rule):
            id = "STUB"
            name = "s"
            see = "golden-patterns/pc/pc_components_request.md"

            def check(self, ctx):
                return [Finding(self.id, "msg")]

        out = _Stub().run(CheckContext(path=Path("x.vue")))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].see, _Stub.see)

    def test_format_finding_embeds_rule_id_and_see(self):
        """护盾 M134 的锁：输出行必须带 [规则ID] 前缀与 📖 See 行。"""
        line = format_finding(Finding("PC-X", "消息",
                                      see="golden-patterns/pc/pc_components_request.md"))
        self.assertTrue(line.startswith("[PC-X] "), "规则 ID 丢失（agent 只能猜 ID 写豁免）：%r" % line)
        self.assertIn("消息", line)
        self.assertIn("📖 See: golden-patterns/pc/pc_components_request.md", line)
        # 无 see 时不得输出空 See 行
        plain = format_finding(Finding("PC-Y", "纯消息"))
        self.assertNotIn("See:", plain)
        self.assertIn("[PC-Y]", plain)

    def test_see_text_carries_no_waiver_syntax(self):
        """see 随 gate stdout 逐字进模型上下文 —— 与 2026-09-30 注入边界一致：
        指路可以，教豁免语法不行（豁免语法的唯一出处是工程根 AGENTS.md）。"""
        for rules in (PC_RULES, H5_RULES):
            for r in rules:
                see = getattr(r, "see", "") or ""
                self.assertNotIn("harness-ok", see,
                                 "%s 的 see 里混进了豁免语法（注入边界）" % r.id)


if __name__ == "__main__":
    unittest.main()
