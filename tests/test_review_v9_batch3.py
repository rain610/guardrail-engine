# -*- coding: utf-8 -*-
r"""评审 v3 批次 3 回归锁：接入验收门 ⑤ syntax 传感器 + registry 校验三态
================================================================================
对应外部评审 v3 两条 P2：
  ① 接入验收（acceptance_check）原先只验 hook/探针/反锁/台账 —— node 语法传感器坏了
     （缺失 / 权限 / 调用失败）验收四项照样全过 → 上线证据缺一类"历史上多次静默失效"的传感器。
     现在 ⑤ 用**两向探针**：合规文件 + required=1 必须 rc=0（反证 node 可用）、
     改成坏语法必须 rc=1（证语法规则真在拦）；非 pc/h5 的 gate 记 None 不参与判定。
  ② `run_gate --validate` 原先把 registry 未登记（script_for→None，靠 config.script 兜底）
     也算进"全部解析"→ 输出比实际注册表覆盖强。现在三态分开报：命中 / 兜底 / 失败。

判据环境无关：容器里没有端仓时 ① 会 ❌（属环境，不是缺陷），但 ⑤ 用**密闭夹具**与端仓无关 ——
因此 ⑤ 为假时必须有说明，而不是凭环境直接放过（也不许静默）。
"""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _tmpclean import mkdtemp_cleanup as _mkdtemp    # noqa: E402

# 2026-09-21：模块级 mkdtemp 没有 setUp/tearDown 可挂 —— 原先每跑一次留一个空目录
# （实测存量 v9b3log 272 / v9b2log 201 / v9log 219 份）。走进程退出强删助手。
# ⚠ 必须**先判再建**，不能写 `setdefault(..., _mkdtemp(...))`：实参在调用前求值，
# 那样即使 setdefault 最后没采纳（别的模块已经设过这个变量），目录也已经建出来了 ——
# 进程被 kill 时 atexit 不跑，就白留一份。
if "HARNESS_LOG_FILE" not in os.environ:
    os.environ["HARNESS_LOG_FILE"] = os.path.join(_mkdtemp("harness-v9b3log-"),
                                                 "gate_log.jsonl")

# 快跑（HARNESS_FAST=1，2026-09-17 批 4 接线）：本文件慢在**真跑 acceptance_check.py**
# （每条起 1~3 个子进程、真扫 gate），接线前完全没接（全文 0 处 HARNESS_FAST），
# 快跑档单跑 10.2~17.5s。
# 落点：只给 TestAcceptanceSyntaxSensor 加**类级**（4.31 / 3.96 / 1.49s，类里没有 <1s 的例）；
# 另一个类 TestRegistryValidateThreeStates 只有一条 0.43s，**不动**（它是"改坏了立刻知道"的
# 便宜契约锁，留着当快跑档的前哨）。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def _accept(gate):
    r = subprocess.run([PY, os.path.join(ROOT, "sandbox", "acceptance_check.py"), gate, "--json"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=ROOT, stdin=subprocess.DEVNULL, timeout=600)
    return r, (json.loads(r.stdout) if r.stdout.strip().startswith("[") else [])


@_slow("真跑 acceptance_check.py（类里 3 例 4.31 / 3.96 / 1.49s，没有更便宜的例）")
class TestAcceptanceSyntaxSensor(unittest.TestCase):
    def test_self_gate_marks_syntax_not_applicable(self):
        r, rows = _accept("harness_self")
        self.assertTrue(rows, "acceptance_check --json 没输出 JSON：%s" % (r.stdout + r.stderr)[-200:])
        row = rows[0]
        self.assertIn("syntax", row, "验收结果缺少 ⑤ syntax 项（评审 v3 P2 未接线）")
        self.assertIsNone(row["syntax"], "工程仓自检 gate 没有 node 语法层 → ⑤ 应为 None（不适用）")
        self.assertTrue(any("不适用" in n for n in row["notes"]),
                        "⑤ 不适用时必须写明原因，不能沉默：%s" % row["notes"])

    def test_pc_gate_syntax_probe_is_two_way(self):
        """pc 端 ⑤ 必须能判定；若判为假，备注里必须给出探针的两次 rc（不是静默 False）。"""
        r, rows = _accept("gxgk_pc")
        if not rows:
            self.skipTest("没有 gxgk_pc gate（配置裁剪）：%s" % (r.stdout + r.stderr)[-120:])
        row = rows[0]
        self.assertIn("syntax", row)
        if row["syntax"] is False:
            self.assertTrue(any("syntax 传感器可疑" in n for n in row["notes"]),
                            "⑤ 为假却没有任何说明（node 缺失/断言失败都被吞了）：%s" % row["notes"])

    def test_pc_gate_syntax_is_true_when_node_present(self):
        """本机有 node 时 ⑤ 必须为真 —— 这是这条锁的核心价值（反向：node 坏了要变红）。"""
        import shutil as _sh
        if not _sh.which("node"):
            self.skipTest("本机没有 node（⑤ 的正确行为是报可疑并拦，用上一条锁覆盖）")
        r, rows = _accept("gxgk_pc")
        if not rows:
            self.skipTest("没有 gxgk_pc gate")
        self.assertTrue(rows[0]["syntax"],
                        "有 node 时 syntax 两向探针未通过（合规必须放行、坏语法必须拦）：%s"
                        % rows[0]["notes"])


class TestRegistryValidateThreeStates(unittest.TestCase):
    def test_validate_reports_registry_miss_separately(self):
        """`--validate` 必须把 registry 未登记（config.script 兜底）与命中分开报 —— **两向**锁。

        2026-09-16 重写：旧版靠「`harness_self` 永远未登记」间接成立（它当时是唯一漏网 gate），
        把 harness_self 补进 registry 后该前提消失，旧断言反而会把**真话**判红。这里改成
        从 registry 自身算出**应有**的兜底集，再要求输出与它一致：
          · 有兜底 → 必须出现「未登记/兜底」措辞，且**不得**出现「全部由 registry 命中」；
          · 无兜底 → 必须出现「全部由 registry 命中」，且**不得**出现「未登记」字样。
        这样不论以后 registry 是补全还是又漏登，锁都咬得住（旧版第二种情况下是空转的）。
        """
        r = subprocess.run([PY, os.path.join(ROOT, "harness", "run_gate.py"),
                            "--validate", "--structure-only"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           cwd=ROOT, stdin=subprocess.DEVNULL, timeout=600)
        out = r.stdout + r.stderr
        self.assertIn("registry", out)

        # 事实来源与 run_gate 同源：配置里的 gate 名 + registry_loader.script_for（None = 兜底）
        sys.path.insert(0, os.path.join(ROOT, "harness"))
        sys.path.insert(0, os.path.join(ROOT, "checks"))
        import _config
        import registry_loader
        gates = _config.all_gates()
        fallback = sorted(n for n in gates if not registry_loader.script_for(n))

        if fallback:
            self.assertIn("未登记", out,
                          "存在靠 config.script 兜底的 gate %s，输出却没报「未登记」：\n%s"
                          % (fallback[:6], out[-400:]))
            self.assertNotIn("全部由 registry 命中", out,
                             "registry 未登记的 gate 被算进'全部命中'（评审 v3 P2）：\n" + out[-400:])
        else:
            self.assertNotIn("未登记", out,
                             "registry 已无兜底 gate，输出却仍报'未登记'（常驻假告警＝告警疲劳）：\n"
                             + out[-400:])
            self.assertIn("全部由 registry 命中", out,
                          "registry 全覆盖时未报'全部命中'（覆盖率结论丢失）：\n" + out[-400:])
        self.assertNotIn("全部解析", out,
                         "文案回到'全部解析'（掩盖 registry 未登记）：\n" + out[-400:])


if __name__ == "__main__":
    unittest.main()
