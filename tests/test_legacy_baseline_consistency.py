# -*- coding: utf-8 -*-
"""
回归锁（2026-09-21，2026-10-21 随配置主格式迁到 TOML）：legacy_baseline 在
`config/harness.toml` 中的一致性。

设计源头
================================================================================
· 历史内部约定 "新受管仓一律 legacy_baseline: true（存量豁免，新代码照拦）"
· AGENTS.md 同条纪律
· 配置注释：「声明了但没消费方读」= 空承诺

本批做的事（2026-09-21）
================================================================================
把 11 → 12 个老仓（各客户端的 pc/h5/backend 三端系列）补齐 `legacy_baseline: true`，
与 21 个新仓对齐。**目的不是改拦截行为**（按行过滤主防线未动），
而是把「存量违规进台账可观测」这条链路打通——
否则 12 老仓的存量命中**永远不写台账**，
「清理存量后转 false」永远没有数据基础。

本文件钉三件事
================================================================================
  ① **每个非 harness_self、非外部适配的 gate 必须有显式 `legacy_baseline` 字段**
     —— 防「老仓一键清掉 12 行」/「新 gate 漏写」。机械护栏。
  ② **`legacy_baseline` 值只能是 TOML 布尔（true / false）**
     —— `rule_base.legacy_baseline()` 拿它走 `bool(...)`，字符串 `"false"`
     在 bool() 里为 True（非空即真），与声明方向相反 → 事故级。
  ③ **harness_self 与外部适配 gate 不强制此字段**——前者是工程仓自检，没有「受管仓存量」
     概念；后者由外部工具管严重级，且 `--validate` 会拒绝它读不懂的策略键。
"""
from __future__ import unicode_literals

import os
import sys
import tomllib
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
CONFIG_TOML = os.path.join(ROOT, "config", "harness.toml")

# 外部适配 gate（架构 A）不消费任何 gate 级策略键：它们的 CONFIG_KEYS 是空集，
# `--validate` 会把 legacy_baseline 当成「声明了没人读」而拒绝。存量降级归工具自己的
# severity 配置（ESLint / Semgrep），本仓不再留第二套开关。
ADAPTER_GATES = {"eslint", "semgrep"}

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _load_gates():
    """只读 TOML 文件本身，不依赖 `_config` —— 配置锁不该触发策略告警等副作用。"""
    with open(CONFIG_TOML, "rb") as f:
        cfg = tomllib.load(f)
    return cfg.get("gates") or {}


class TestLegacyBaselineConsistency(unittest.TestCase):
    """机械护栏：所有非 harness_self、非外部适配的 gate 必须显式声明 legacy_baseline。"""

    def setUp(self):
        self.gates = _load_gates()

    def test_every_non_harness_gate_declares_legacy_baseline(self):
        missing = []
        for name, cfg in self.gates.items():
            if name == "harness_self" or name in ADAPTER_GATES:
                continue
            if "legacy_baseline" not in cfg:
                missing.append(name)
        self.assertEqual(
            missing, [],
            "以下 gate 未声明 legacy_baseline（必须是显式 true/false；"
            "未写 = 默认 false = 与全仓门禁锁不一致，历史内部约定 "
            "「新受管仓一律 legacy_baseline: true」纪律）：\n  - "
            + "\n  - ".join(missing),
        )

    def test_legacy_baseline_value_is_boolean(self):
        """值必须是 TOML 布尔（true/false），不能是字符串。

        字符串 "false" 走 `bool()` 转 True（非空即真），与声明方向相反。
        """
        bad = []
        for name, cfg in self.gates.items():
            v = cfg.get("legacy_baseline")
            if v is None:
                continue   # 上条已告警
            if not isinstance(v, bool):
                bad.append("{0}={1!r}".format(name, v))
        self.assertEqual(
            bad, [],
            "legacy_baseline 应是 TOML 布尔(true/false)；"
            "rule_base.legacy_baseline() 直接 bool() 转，字符串 \"false\" "
            "会被判为 True（事故级）：\n  - " + "\n  - ".join(bad),
        )

    def test_harness_self_may_omit_legacy_baseline(self):
        """harness_self 是工程仓自检，无「受管仓存量」概念，不强制此字段。

        仓内 CONFIG_KEYS = frozenset()（空集）也印证它不消费任何 gate 级键。
        若有人加这条到 harness_self，要看一眼是不是误以为它和别的 gate 同性质。
        """
        cfg = self.gates.get("harness_self", {})
        self.assertNotIn(
            "legacy_baseline", cfg,
            "harness_self 不应声明 legacy_baseline（它不是受管仓，"
            "rule_base.legacy_baseline() 对它的反查是 undefined；"
            "如确需统一键集，先在 README 评估再开）",
        )


if __name__ == "__main__":
    unittest.main()
