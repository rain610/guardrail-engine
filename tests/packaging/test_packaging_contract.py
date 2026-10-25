# -*- coding: utf-8 -*-
import pathlib
"""打包契约锁（M0 骨架，2026-09-14）

ADR-0001 铁律 1/5：引擎逻辑只一份，所有「壳」（CLI 转发器 / MCP 服务 / 技能脚本 /
宿主插件）只做转发——不得重定义引擎符号，不得在壳层做行号 / 坐标系运算（K1 教训）。

用法：新壳落地时把其仓库相对路径加入 SHELL_FILES；本锁随全量套件一起跑。
`agentguard/` 下的文件**由机器推导强制登记**（test_every_gate_module_is_registered）——
漏加不会被忽略，而是当场红。
"""
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# M1 起逐个加入，例如：
#   "agentguard/cli.py"、"agentguard/mcp.py"、
#   "integrations/guardrail-engine-skill/scripts/gate.py"、宿主插件转发文件……
SHELL_FILES = [
    # 打包路线壳（ADR-0001：壳只转发、禁行号运算）——2026-09-14 起逐个注册
    "guardrail_engine/__init__.py",
    "guardrail_engine/_shell.py",
    "guardrail_engine/cli.py",
    "guardrail_engine/mcp.py",
    # 迁移期兼容壳（v0.3 移除）：同样受禁令约束
    "agentguard/__init__.py",
    "agentguard/_shell.py",
    "agentguard/cli.py",
    "agentguard/mcp.py",
]

# 自动登记要覆盖的包目录：漏登记 = 新壳不受禁令约束，而契约锁仍然全绿。
SHELL_DIRS = ("guardrail_engine", "agentguard")

# 引擎符号：壳内出现这些「定义」 = 双实现开始漂移（ADR-0001 铁律 1）
BANNED_ENGINE_DEFS = (
    "def compute_changed_lines",
    "def _find_hits",
    "def filter_hits_by_lines",
    "def is_untracked",
    "def script_line_offset",
)

# 坐标系禁令：壳层不得接触 changed_lines（行号运算只允许在引擎层，K1 教训）
BANNED_IN_SHELLS = ("changed_lines",)


class TestPackagingContract(unittest.TestCase):
    def test_registry_wellformed(self):
        self.assertIsInstance(SHELL_FILES, list)
        for rel in SHELL_FILES:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, rel)), "壳文件不存在：%s" % rel)

    def test_every_gate_module_is_registered(self):
        """包目录下的每个 .py 都必须在 SHELL_FILES 里登记（机器推导，不靠手抄）。

        为什么要这条：手工清单的危险不在「有人偷懒」，而在**漏登记时本文件会静默变成不设防**
        —— 新壳不受任何禁令约束，而契约锁仍然是绿的（这正是 ADR-0001 铁律 2 要求登记、
        却一直只有人眼在守的那条）。2026-09-16 剖析报告 A3 抽公用件时补上。
        2026-10-25 改名：新主包与兼容壳两个目录都要枚举，只盯旧目录会让新壳变成盲区。
        """
        present = []
        for pkg in SHELL_DIRS:
            d = os.path.join(ROOT, pkg)
            present += sorted(pkg + "/" + n for n in os.listdir(d) if n.endswith(".py"))
            self.assertTrue(any(p.startswith(pkg + "/") for p in present),
                            "枚举 %s/ 失败（目录或路径写错了？本锁会因此空转）" % pkg)
        missing = [p for p in present if p not in SHELL_FILES]
        self.assertEqual(missing, [],
                         "这些打包壳没登记进 SHELL_FILES（漏登记 = 不受禁令约束且无人知道）：%s"
                         % missing)

    def test_shells_do_not_redefine_engine(self):
        for rel in SHELL_FILES:
            src = pathlib.Path(os.path.join(ROOT, rel)).read_text(encoding="utf-8", errors="replace")
            for banned in BANNED_ENGINE_DEFS:
                self.assertNotIn(banned, src,
                                 "壳 %s 不得重定义引擎符号：%s（ADR-0001 铁律 1）" % (rel, banned))

    def test_shells_stay_out_of_line_math(self):
        for rel in SHELL_FILES:
            src = pathlib.Path(os.path.join(ROOT, rel)).read_text(encoding="utf-8", errors="replace")
            for banned in BANNED_IN_SHELLS:
                self.assertNotIn(banned, src,
                                 "壳 %s 不得接触 %s（行号坐标系属于引擎层，K1 教训）" % (rel, banned))


if __name__ == "__main__":
    unittest.main()
