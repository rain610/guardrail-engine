# -*- coding: utf-8 -*-
"""
回归锁（2026-09-21）：legacy_baseline 在 harness.yaml 中的一致性。

设计源头
================================================================================
· CHANGELOG "新客户一律 legacy_baseline: true（存量豁免，新代码照拦）"
· AGENTS.md 第 33 行同条纪律
· harness.yaml 第 78-86 行评审 v13 注释：「声明了但没消费方读」= 空承诺

本批做的事（2026-09-21）
================================================================================
把 11 → 12 个老仓（gxgk_pc/h5/backend / hy_pc/h5/backend /
hy_base_pc/h5/backend / hy_hzy_pc/h5/backend）补齐 `legacy_baseline: true`，
与 21 个新仓对齐。**目的不是改拦截行为**（按行过滤主防线未动），
而是把「存量违规进台账可观测」这条链路打通——
否则 12 老仓的存量命中**永远不写台账**，
「清理存量后转 false」永远没有数据基础。

本文件钉三件事
================================================================================
  ① **每个非 harness_self 的 gate 必须有显式 `legacy_baseline` 字段**
     —— 防「老仓一键清掉 12 行」/「新 gate 漏写」。机械护栏。
  ② **`legacy_baseline` 值只能是 yaml bool 字面量（true / false）**
     —— `rule_base.legacy_baseline()` 拿它走 `bool(...)`，字符串 `"false"`
     在 bool() 里为 True（非空即真），与声明方向相反 → 事故级。
  ③ **harness_self 不强制此字段**——它是工程仓自检，没有「客户仓存量」概念；
     仓内 `CONFIG_KEYS = frozenset()`（空集）也印证它不消费任何策略键。
"""
from __future__ import unicode_literals

import os
import re
import sys
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
CONFIG_YAML = os.path.join(ROOT, "config", "harness.yaml")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _load_gates():
    """手写极简 yaml 读——只取 gates 段下每个 gate 的顶层 key/value，不依赖 PyYAML。

    为什么不调用 `_config.all_gates()`：那需要把 harness.lib 加载进 sys.path，
    还会触发 ConfigError / policy notice 等副作用。配置锁**只读 yaml 本身**。
    """
    with open(CONFIG_YAML, "r", encoding="utf-8") as f:
        text = f.read()

    m = re.search(r"^gates:\s*\n", text, re.MULTILINE)
    if not m:
        return {}
    after = text[m.end():]
    # 下一个顶层段（version/repos/rules/baseline…）即终止
    end_m = re.search(r"^(?:\w[\w_]*):\s*$", after, re.MULTILINE)
    body = after[:end_m.start()] if end_m else after

    gates = {}
    cur = None
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # 门键："  name:"  顶 2 空格
        gm = re.match(r"^  (\w+):\s*$", line)
        if gm:
            cur = gm.group(1)
            gates[cur] = {}
            continue
        if cur is None:
            continue
        # 字段："    key: value" 顶 4 空格
        fm = re.match(r"^    (\w+):\s*(.*?)\s*(?:#.*)?$", line)
        if fm:
            key = fm.group(1)
            value = fm.group(2)
            if not key.startswith("_"):
                gates[cur][key] = value
    return gates


class TestLegacyBaselineConsistency(unittest.TestCase):
    """机械护栏：所有非 harness_self 的 gate 必须显式声明 legacy_baseline。"""

    def setUp(self):
        self.gates = _load_gates()

    def test_every_non_harness_gate_declares_legacy_baseline(self):
        missing = []
        for name, cfg in self.gates.items():
            if name == "harness_self":
                continue
            if "legacy_baseline" not in cfg:
                missing.append(name)
        self.assertEqual(
            missing, [],
            "以下 gate 未声明 legacy_baseline（必须是显式 true/false；"
            "未写 = 默认 false = 与全仓门禁锁不一致，CHANGELOG "
            "「新客户一律 legacy_baseline: true」纪律）：\n  - "
            + "\n  - ".join(missing),
        )

    def test_legacy_baseline_value_is_yaml_bool_literal(self):
        """值必须是 yaml 布尔字面量（true/false），不能是字符串。

        字符串 "false" 走 `bool()` 转 True（非空即真），与声明方向相反。
        """
        bad = []
        for name, cfg in self.gates.items():
            v = cfg.get("legacy_baseline")
            if v is None:
                continue   # 上条已告警
            if v not in ("true", "false"):
                bad.append("{0}={1!r}".format(name, v))
        self.assertEqual(
            bad, [],
            "legacy_baseline 应是 yaml 布尔字面量(true/false)；"
            "rule_base.legacy_baseline() 直接 bool() 转，字符串 \"false\" "
            "会被判为 True（事故级）：\n  - " + "\n  - ".join(bad),
        )

    def test_harness_self_may_omit_legacy_baseline(self):
        """harness_self 是工程仓自检，无「客户仓存量」概念，不强制此字段。

        仓内 CONFIG_KEYS = frozenset()（空集）也印证它不消费任何 gate 级键。
        若有人加这条到 harness_self，要看一眼是不是误以为它和别的 gate 同性质。
        """
        cfg = self.gates.get("harness_self", {})
        self.assertNotIn(
            "legacy_baseline", cfg,
            "harness_self 不应声明 legacy_baseline（它不是客户仓，"
            "rule_base.legacy_baseline() 对它的反查是 undefined；"
            "如确需统一键集，先在 README 评估再开）",
        )


if __name__ == "__main__":
    unittest.main()