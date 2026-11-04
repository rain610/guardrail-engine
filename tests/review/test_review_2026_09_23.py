# -*- coding: utf-8 -*-
r"""外部评审 2026-09-23 §3.2 的 M4/M5/M6/M7 落地锁：后端 R1/R2/R3/R5/R6/R9 边界探针表。

与被锁的判据同源 —— 每条都**直接调规则本体的 check()**（不是 grep 源码，也不是重抄一遍
模式）：改了规则而锁没跟着改，这里立刻红；锁被弱化到空转，`sandbox/mutation_smoke.py`
里同批的变异（M49…M55）会红。

四条评审项的**实测口径**（本机 12 个后端 gate、72434 个 .cs、1051 万行；复量脚本口径见
`reports/` 台账，不写死成"证据"）：

| 项 | 改法 | 存量影响（实测） |
|---|---|---|
| M5 | R1/R2/R3 由 raw `splitlines()` 改吃 `_code_lines()`（注释不算数） | R1 **1146 行**是注释形态（减观察噪声）；R2/R3 **0** |
| M4 | `using` 形态统一交 `_using_ns()`（认 `static` / 别名 / `global`） | **0**（三种形态在存量里一次都没出现）⇒ 关潜在口子 |

★ M5 与 M4 在 R2/R3 上**互相遮蔽**（2026-09-23 做变异证伪时当场发现，记在这里免得后人再踩）：
`_using_ns()` 自己就要求整行以 `using` 开头，所以 `// using X;`、`/* using X; */` 这两种**单行**
形态在 raw 与 `_code_lines` 下**结果相同**（都是 0）—— 拿它们当 M5 的判据，`M50` 那条变异
（退回 raw）**照样全绿**，锁是空转的。真正能区分的只有**跨行块注释**：内层整行 `using X;`
在 raw 下会被判违规、在 `_code_lines` 下那行已成空白（`/*\nusing X;\n*/` ⇒ raw 命中 1、实际 0）；
反方向则是行首块注释把 `using` 顶到行中间（`/* x */ using X;` ⇒ raw 0、实际 1）。
四条表里都用这两种形态钉 M5，单行形态保留但**改按它真正的判据命名**（`_using_ns` 的行首要求）。
| M6 | 豁免由 `"override" in line` 收紧为 `\boverride\b`；删死判据 `override_pat` | **0**；死判据单独命中 **0**（证实"永远不生效"） |
| M7 | R9 的跳过由 `"const" in line` 收紧为 `\bconst\b` | **0** |

★ 有意**不锁**的两条（属业务语义，待用户拍板，见台账）：
  · R6 对 `base.<分页方法>(` 的豁免要不要撤（实测存量 903 处会被翻成命中）；
  · R9 命中面要不要从 `\bStatus\b` 放宽到 `\w*Status`（实测 608 处宽模式候选）。
  在拍板之前把它们锁死，等于把"还没决定的事"伪装成"已经决定的事"。

2026-09-23 同批追加（同一份评审的低项，落在这里是因为它同样是"判据与产品同源"那一类）：
L16 —— 台账摘要里的**历史夹具 gate 名**不许被当成受管 gate，见文末
`TestSummarySeparatesUnregisteredNames`。

2026-09-23 再追加（低项 L1/L2/L3/L10，见文末四条 `TestL1…/TestL2…/TestL3…/TestL10…`）：
这四条不是"功能缺失"，而是**判据读错** —— 改法各一到三行，但改错的后果是结论**反向**
（该拦的放行、该记的没记、参数写 0 变成全量）。因为最小修复往往最像"没改"，每条都配了
反方向的 companion，且判据一律落在真函数上（不 grep 源码形态）。

2026-09-23 三追加（壳层契约 L7/L9，见文末 `TestL7…/TestL9…`）：这两条也不是"少个功能"，
而是**壳层把坏输入的性质归错了** —— MCP 的 `handle_tools_call` 对参数不设防：`name: [1,2]`
让成员判断抛 `TypeError`，被外层兜成 **-32603 Internal error**（读作「服务端自己的 bug」，
宿主可能据此重连甚至禁用门禁），而真相是**调用方传错了参数** ⇒ 该报 **-32602**；
`tool_run_gate` 的 `repo` 支只验 `isdir`，可 inputSchema 里写的契约是「a repository
directory」⇒ 任意目录树都能被交给引擎走一遍。两条都属"错误归因"那一类：门禁把错因说得
越像自己的毛病，越会被当成环境噪声绕过去。★ `repo` 那支只验「是不是仓库」、**不**验
「受不受管」—— `rc=3 = repository not managed` 是本工具对外承诺的返回码之一，
壳层提前拦掉等于把它从契约里删掉。
"""
import ast
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "checks"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PY = sys.executable
CLI_PY = os.path.join(ROOT, "guardrail_engine", "cli.py")


def _load_cli():
    """把 `guardrail_engine/cli.py` 当模块导进来（它有 `__main__` 守卫，导入无副作用）。"""
    sys.path.insert(0, ROOT)
    from guardrail_engine import cli
    return cli
HOOK = os.path.join(ROOT, "integrations", "hooks", "agentguard-gate.py")
PLUGIN = os.path.join(ROOT, "integrations", "opencode", "agentguard-gate.ts")

# 快跑档（HARNESS_FAST=1）：本文件里 M10/M11 那两组要真起 node / 钩子子进程，跳过；
# 纯进程内的规则探针表照跑（与 tests/test_entry_level.py 的 _slow 同一口径）。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, f"快速模式（HARNESS_FAST=1）跳过{reason}")

class TestSummarySeparatesUnregisteredNames(unittest.TestCase):
    """L16 回归锁（2026-09-23 评审报告）：台账里的**历史夹具条目**不许被当成受管 gate。

    现场（修复前实测）：台账 `reports/gate_log.jsonl` 混着夹具在临时配置下真跑出来的判定
    （`t_pc` 18 / `test_pc` 7 / `t_on` 6 / `t_off` 2 / `myproj_pc` 1 / `tmpc_pc` 1，都 ≤2026-09-16），
    而 `--summary` 把它们与受管 gate 平铺在同一张表里 —— 于是表头那句"共 N 个 gate"把
    夹具名也算进去，读者会读成"这台机器有这么多 gate"。评审报告点名的正是这一条。

    ★ 处置取「标注」而不是「清理」，这条锁要钉住的就是这个取舍：**审计日志不删历史行**
    （删历史本身就是一次不可审计的动作），错的不是那几行记录，是聚合时没区分来源。
    ★ 另一条被钉住的取舍是**口径不许悄悄改**：那句"拦截 X 次 (Y%)"的字面意思是
    "窗口内 N 条里有多少被拦"，无论来源都成立 —— 故上方计数**仍按全部行**算，
    未登记名只单列、并把自己的条数与拦截数报出来让人自己扣。若哪天有人"顺手"把未登记行
    从分母里剔掉，`拦截 2 次 (40%)` 会变成 `1 次 (33%)`，本锁立刻红。

    判据落在**读者看到的那张表**上（与 `TestCliMarkColumn` 同一条理由：字段进了 jsonl
    而表里看不见等于没加）。分界线是那段小标题 `── 另有`：它之前是主表、之后是单列块。
    若将来重写这段排版，请连本锁一起改 —— 这条断言失败时先看是不是排版改了，别去改数字。
    """

    REGISTERED = "harness_self"      # 恒被 _config 无条件注入，任何机器上都登记 → CI 里也可用
    FIXTURE = "t_on"                 # 评审报告点名的历史夹具名

    def setUp(self):
        import json
        import subprocess
        import tempfile
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from _tmpclean import rmtree_force
        self.addCleanup(sys.path.remove, os.path.dirname(os.path.abspath(__file__)))
        self.tmp = tempfile.mkdtemp(prefix="rev0923-sum-")
        self.addCleanup(rmtree_force, self.tmp)
        self.ledger = os.path.join(self.tmp, "gate_log.jsonl")
        # 行数刻意取得互相区分：登记名 3 行 1 拦 / 夹具名 2 行 1 拦 → 合计 5 行 2 拦（40%）
        rows = [{"ts": "2026-09-23T00:00:0%d" % i, "gate": self.REGISTERED, "rc": rc}
                for i, rc in enumerate((0, 0, 1))]
        rows += [{"ts": "2026-09-23T00:01:0%d" % i, "gate": self.FIXTURE, "rc": rc}
                 for i, rc in enumerate((0, 1))]
        with open(self.ledger, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        env = dict(os.environ, HARNESS_LOG_FILE=self.ledger, PYTHONUTF8="1")
        p = subprocess.run([sys.executable, os.path.join(ROOT, "harness", "run_gate.py"),
                            "--summary", "50"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=180, env=env)
        self.rc, self.out = p.returncode, (p.stdout or "") + (p.stderr or "")

    def test_fixture_name_is_set_aside_not_listed_as_a_gate(self):
        # 夹具自检：这条锁全靠"两个名字里有一个真的登记过"，先证明它成立（否则整条锁空转）
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_rg_sum_probe", os.path.join(ROOT, "harness", "run_gate.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        gates, err = mod._config_gates()
        self.assertIsNone(err, "配置读不出来 → 本锁无从判定登记与否：%s" % err)
        self.assertIn(self.REGISTERED, gates, "夹具没造对：%s 竟然没登记" % self.REGISTERED)
        self.assertNotIn(self.FIXTURE, gates, "夹具没造对：%s 竟然登记过" % self.FIXTURE)

        self.assertEqual(self.rc, 0, "只读报表不得以 rc=1（=代码不合规）退出：\n%s" % self.out)
        self.assertNotIn("Traceback", self.out)
        head, _, tail = self.out.partition("── 另有")
        self.assertTrue(tail, "没有分出「未登记名」那一块 —— 排版改了就连本锁一起改：\n%s" % self.out)
        self.assertIn("共 1 个 gate", head,
                      "表头的 gate 数必须是**登记过的**那 1 个，不能把夹具名算进去：\n%s" % head)
        self.assertNotIn(self.FIXTURE, head,
                         "历史夹具名仍被列在主表里（评审 L16 要治的就是这一条）：\n%s" % head)
        self.assertIn(self.FIXTURE, tail, "夹具名必须被单列出来（标注而非删除）：\n%s" % tail)
        self.assertIn("共 2 条", tail, "单列块要说清自己被单列了多少条：\n%s" % tail)
        self.assertIn("拦截 1 次", tail, "单列块还要报自己的拦截数，人才扣得动：\n%s" % tail)

    def test_registered_gate_and_totals_are_not_over_filtered(self):
        """反向（防止"一刀切掉"也算通过）：登记过的 gate 照常在主表里、计数照旧。

        两件事一起钉：① 主表没被连坐清空；② 上方比例仍是**全部行**的口径
        （未登记行没被从分母里偷偷剔掉 —— 那是改口径，不是修 bug）。
        """
        head = self.out.partition("── 另有")[0]
        self.assertIn(self.REGISTERED, head, "登记过的 gate 必须照常出现在主表：\n%s" % head)
        self.assertIn("拦截 2 次 (40%)", head,
                      "比例口径必须仍是「窗口内全部行」（5 行里拦 2 次）；"
                      "把它改成只算登记行 = 悄悄改口径：\n%s" % head)


# =====================================================================================
# M10 / M11（§3.2）：门禁接在 PreToolUse 上、以及 opencode 插件传相对路径时的行为
# =====================================================================================
# 两项的实测现场（2026-09-23 本机真跑，不是推断）：
#   M10 —— PreToolUse + **新建文件**（写之前当然不存在）→ auto-gate 的 `--only` 一个都
#          匹配不上 → rc=2；qoder / trae / codebuddy 的 PreToolUse deny 分支据此
#          `permissionDecision=deny`，理由写作「门禁调用出错 rc=2（多半是配置问题）」。
#          后果：**新建任何文件都进不去**，理由还是错的。三种客户端实测皆 deny。
#   M11 —— opencode 插件把 cwd 设成「被编辑文件的父目录」、把宿主给的 filePath **原样**
#          转发；宿主给相对路径时 `src/a.vue` 被 auto-gate 解析成 `<仓>/src/src/a.vue`
#          → 全 miss → rc=2 → 插件回灌「多半是配置问题」。实测：相对路径 rc=2，
#          绝对路径、以及「cwd 改成 process.cwd()」两种形态都正常。
#
# 锁的口径与产品同源：① 直接喂真实 payload 给**钩子正本**（不是复刻它的判据）；
# ② M11 直接 `import` **真实插件**跑（Node ≥22.6 的 --experimental-strip-types），
# 判据落在「插件这次实际交出去的 file_path 是什么」上 —— 不是 grep 源码形态。
BAD_VUE = ('<template><div /></template>\r\n'
           '<script setup>\r\n'
           'console.log("debug");\r\n'
           '</script>\r\n')
OK_VUE = ('<template><div /></template>\r\n'
          '<script setup>\r\n'
          'const x = 1;\r\n'
          '</script>\r\n')


class _HookFixture:
    """临时端仓 + 指向它的最小配置（gate t_pc），并直喂钩子正本。

    与 tests/test_entry_level.py::_Fixture 同款做法；这里自带一份，是因为「相对路径」这件事
    需要控制「钩子进程 cwd ≠ 被编辑仓」（见 TestPreToolUseNewFile 里的 cwd=ROOT）。
    """

    def __init__(self):
        from _tmpclean import mkdtemp_cleanup, rmtree_force
        self._rmtree = rmtree_force
        self.dir = mkdtemp_cleanup("rev0923-hook-")
        self.repo = os.path.join(self.dir, "repo_pc")
        os.makedirs(os.path.join(self.repo, "src"), exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, capture_output=True)
        self.cfg = os.path.join(self.dir, "cfg.yaml")
        with open(self.cfg, "w", encoding="utf-8", newline="") as f:
            f.write("repos:\n"
                    "  t_pc: %s\n" % self.repo.replace("\\", "/") +
                    "gates:\n"
                    "  t_pc:\n"
                    "    enabled: true\n"
                    "    repo: t_pc\n"
                    "    script: checks/example_code_check.py\n")
        self.log = os.path.join(self.dir, "gate_log.jsonl")
        self.env = dict(os.environ, HARNESS_CONFIG=self.cfg, HARNESS_LOG_FILE=self.log,
                        PYTHONUTF8="1", HARNESS_ENTRY="test")

    def write(self, rel, text):
        p = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return p

    def hook(self, client, file_path, event, cwd=None):
        """喂真实 payload 给钩子正本。cwd 默认 = 被编辑仓（宿主在仓根启动的常态）。"""
        payload = json.dumps({"cwd": self.repo.replace("\\", "/"),
                              "tool_name": "Write",
                              "tool_input": {"file_path": file_path}})
        return subprocess.run([PY, HOOK, client, event],
                              cwd=cwd or self.repo, input=payload, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              env=self.env, timeout=300)

    def cleanup(self):
        self._rmtree(self.dir)


class TestPreToolUseNewFile(unittest.TestCase):
    """M10：PreToolUse 阶段「文件还不存在」= 新建场景 → 放行；但 PreToolUse 不许整体空转。"""

    DENY_CLIENTS = ("qoder", "trae", "codebuddy")

    def setUp(self):
        self.fx = _HookFixture()
        self.addCleanup(self.fx.cleanup)
        self.bad = self.fx.write("src/bad.vue", BAD_VUE)

    @_slow("M10：每例真起钩子子进程（含 auto-gate）")
    def test_new_file_is_allowed_through_pretooluse(self):
        """新建文件（写之前不存在）在 PreToolUse 必须**放行**，且不得给出 deny。

        修复前的实测形态：rc=0 + stdout JSON `permissionDecision=deny`，
        理由「harness 门禁调用出错 rc=2（多半是配置问题，别当业务错）」—— 三种客户端一致。
        """
        new = os.path.join(self.fx.repo, "src", "brand_new_page.vue")
        self.assertFalse(os.path.exists(new), "夹具前提不成立：这个文件竟然已经存在")
        for client in self.DENY_CLIENTS:
            with self.subTest(client=client):
                r = self.fx.hook(client, new, "PreToolUse")
                self.assertEqual(r.returncode, 0, "%s：PreToolUse 应 rc=0\n%s%s"
                                 % (client, r.stdout, r.stderr))
                self.assertNotIn("deny", r.stdout,
                                 "%s：新建文件被 PreToolUse 拒了 —— 新建任何文件都进不去：\n%s"
                                 % (client, r.stdout))
                self.assertNotIn("rc=2", r.stdout + r.stderr,
                                 "%s：把「文件还不存在」当成门禁调用出错：\n%s%s"
                                 % (client, r.stdout, r.stderr))

    @_slow("M10：每例真起钩子子进程（含 auto-gate）")
    def test_existing_bad_file_still_denied_with_relative_path(self):
        """反向锁（一条判据钉两件事）：

        ① PreToolUse 没有被「顺手」改成一律放行 —— 已存在的违规文件照样 deny；
        ② 该文件用**相对路径**给出（钩子进程 cwd=ROOT ≠ 被编辑仓）仍然命中。
        ② 是 ① 的前提：钩子把 `--only` 归一成绝对路径之前，存在性是在**钩子进程的 cwd**
        上求值的 —— 存在却判成不存在，就会静默放行（比不判更坏：没报看起来与合规一样）。
        """
        for client in self.DENY_CLIENTS:
            with self.subTest(client=client):
                r = self.fx.hook(client, "src/bad.vue", "PreToolUse", cwd=ROOT)
                self.assertEqual(r.returncode, 0, "%s：PreToolUse 契约要 exit 0\n%s%s"
                                 % (client, r.stdout, r.stderr))
                self.assertIn("deny", r.stdout,
                              "%s：已存在的违规文件在 PreToolUse 没被拦（相对路径没归一？）：\n%s"
                              % (client, r.stdout))

    @_slow("M10：每例真起钩子子进程（含 auto-gate）")
    def test_missing_file_is_still_loud_after_the_edit(self):
        """反向锁：放行**只给 PreToolUse**。PostToolUse 撞到不存在的文件仍按 rc=2 出声。

        为什么必须保留这种吵法：M11 那种「路径形态不对」的毛病正是靠它暴露的
        （rc=2 + `--only 指定的文件都不存在`）。若把「文件不存在」一律当放行，
        M11 就从「显式报错」变成「编辑期静默不检查」—— 把响的故障改成哑的故障。
        """
        missing = os.path.join(self.fx.repo, "src", "was_deleted.vue")
        r = self.fx.hook("opencode", missing, "PostToolUse")
        self.assertEqual(r.returncode, 0, "opencode 契约：exit 0 + stdout JSON\n%s" % r.stderr)
        data = json.loads(r.stdout)["agentguard"]
        self.assertEqual(data["gate_rc"], 2,
                         "PostToolUse 撞到不存在的文件必须仍是 rc=2"
                         "（不许被 M10 的放行顺带吞掉）\n%s" % data["message"])
        self.assertIn("都不存在", data["message"],
                      "结论里要说清「文件找不到」，而不是含糊的「调用出错」：\n%s" % data["message"])


_NODE_TS = None


def _node_ts_ok():
    """本机 node 能否直接跑 .ts（Node ≥22.6 的 --experimental-strip-types）。

    没有它就没法**端到端驱动真实插件** —— 那就跳过（skip 理由里写明），
    不用「grep 源码形态」的弱锁冒充行为锁。
    """
    global _NODE_TS
    if _NODE_TS is None:
        _NODE_TS = False
        node = shutil.which("node")
        if node:
            try:
                _NODE_TS = subprocess.run([node, "--experimental-strip-types", "-e", "0"],
                                          capture_output=True, timeout=60).returncode == 0
            except Exception:
                _NODE_TS = False
    return _NODE_TS


class TestOpenCodePluginFeedsAbsolutePath(unittest.TestCase):
    """M11：驱动**真实插件**（TS 直跑），钉住它交给钩子的 file_path 是绝对路径。

    判据不是「源码里有没有 path.resolve」，而是「这次调用插件实际交出去了什么」：
    在**相对路径**输入下看它回灌的结论，以及它自己那行 `gate_rc=… file=…` 日志。
    修复前的实测形态：回灌「门禁调用出错 rc=2（多半是配置问题）」，日志里 file=src/bad.vue。
    """

    DRIVER = r'''
import { pathToFileURL } from "node:url"
const mod = await import(pathToFileURL(process.env.PLUGIN_TS).href)
const server = await mod.default.server({}, {})
const h = server["tool.execute.after"]
const out = { output: "ORIGINAL-OUTPUT" }
await h({ tool: "write", args: JSON.parse(process.env.PROBE_ARGS) }, out)
process.stdout.write("===OUTPUT===\n" + out.output + "\n===END===\n")
'''

    def setUp(self):
        self.fx = _HookFixture()
        self.addCleanup(self.fx.cleanup)
        self.bad = self.fx.write("src/bad.vue", BAD_VUE)
        self.log = os.path.join(self.fx.dir, "opencode.log")
        self.driver = os.path.join(self.fx.dir, "drive.mjs")
        with open(self.driver, "w", encoding="utf-8", newline="") as f:
            f.write(self.DRIVER)

    def drive(self, rel, cwd=None):
        """跑真实插件。**进程 cwd = 被编辑仓**（模拟 opencode 在仓根启动）。"""
        env = dict(self.fx.env,
                   AGENTGUARD_DIR=ROOT,
                   HARNESS_DIR=ROOT,
                   HARNESS_PYTHON=PY,
                   HARNESS_OPENCODE_LOG=self.log,
                   PLUGIN_TS=PLUGIN,
                   PROBE_ARGS=json.dumps({"filePath": rel}))
        r = subprocess.run([shutil.which("node"), "--experimental-strip-types", self.driver],
                           cwd=cwd or self.fx.repo, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=300)
        out = r.stdout.partition("===OUTPUT===")[2].partition("===END===")[0]
        logged = ""
        if os.path.exists(self.log):
            with open(self.log, encoding="utf-8", errors="replace") as f:
                logged = f.read()
        return r, out, logged

    @unittest.skipUnless(_node_ts_ok(), "本机 node 不能直跑 .ts（需 Node ≥22.6）→ 跳过")
    @_slow("M11：真起 node 跑插件 → 钩子 → auto-gate")
    def test_relative_file_path_is_not_mangled_by_the_parent_dir_cwd(self):
        r, out, logged = self.drive("src/bad.vue")
        self.assertNotIn("Traceback", r.stderr + r.stdout, r.stderr)
        self.assertIn("gate_rc=", logged,
                      "插件没跑起来（日志为空）→ 本锁没测到东西：\n%s%s" % (r.stdout, r.stderr))
        self.assertIn("门禁未通过", out,
                      "相对路径下编辑期门禁没生效 —— 这正是 M11 的现场：\n回灌=%r\n日志=%s"
                      % (out, logged))
        self.assertNotIn("调用出错", out, "被解析成「门禁调用出错」了（路径没归一）：\n%s" % out)
        self.assertIn("gate_rc=1", logged, "日志里的 gate_rc 不是 1：\n%s" % logged)
        # 直接钉「交出去的到底是什么路径」：必须是绝对路径，且就是那个文件
        # （分隔符按平台归一后再比：插件日志里是 Windows 反斜杠，本锁不该钉死分隔符）
        line = [l for l in logged.splitlines() if "gate_rc=" in l][-1]
        want = os.path.normcase(self.bad)
        got = os.path.normcase(line.partition("file=")[2].strip())
        self.assertEqual(got, want,
                         "交出去的 file_path 必须是**绝对路径的那个文件**"
                         "（相对路径 = 本锁要治的 M11 现场）：\n%s" % line)

    @unittest.skipUnless(_node_ts_ok(), "本机 node 不能直跑 .ts（需 Node ≥22.6）→ 跳过")
    @_slow("M11：真起 node 跑插件 → 钩子 → auto-gate")
    def test_absolute_file_path_and_clean_file_reverse_checks(self):
        """反向锁两条：绝对路径输入照旧命中（归一没把好形态弄坏）；合规文件不打扰模型。"""
        r, out, logged = self.drive(self.bad.replace("\\", "/"))
        self.assertIn("门禁未通过", out, "绝对路径输入被弄坏了：\n%s" % out)
        clean = self.fx.write("src/clean.vue", OK_VUE)
        r2, out2, logged2 = self.drive(os.path.relpath(clean, self.fx.repo))
        self.assertEqual(out2.strip(), "ORIGINAL-OUTPUT",
                         "合规文件不该往 tool output 里追加任何东西：\n%s" % out2)


# =====================================================================================
# 2026-09-23 低项 L1 / L2 / L3 / L10 —— 同一份评审 §3.3，四条都是「判据读错」类缺陷。
#
# 共同点：改法都很小（一行到三行），但改错的代价是**结论反向**，而不是报错 ——
# 所以每条都配了反方向的companion：只证明"该报的报了"不够，还得证明"该放的还放着"。
# 锁的判据一律落在**真函数**上（severity() / compute_changed_lines() / check_file() /
# _read_jsonl()），不 grep 源码形态；同批在 `sandbox/mutation_smoke.py` 登记了 M60/M61。
# =====================================================================================


class TestL1EncodingTouchIsThreeState(unittest.TestCase):
    """L1：ENC 的"改动过"必须是**三态**语义（`is not None`），不是"非空"。

    三态里 `frozenset()` 有确切含义（见 rule_base.compute_changed_lines 文件头）：
    文件本次**动过**、只是没有新增行（纯删除型改动）。旧写法 `bool(changed_lines)`
    把这一态读成"没动过" → legacy 仓走存量豁免报 INFO → rc=0 放行。
    而纯删除恰恰是编码事故最常见的载体：编辑器（VS 尤其）保存是**整文件重写**，
    只删一行也能把整个文件顺手存成 GBK —— 正是本规则 2026-09-07 立规时要拦的那件事。
    ENC 自己的注释也早就写着"改动过 → 硬拦"，只是括号里把"改动过"注解成了"非空"。

    矩阵两向都钉：`None`（读不到 diff / 全量体检 / 编辑期没动这个文件）**必须**继续豁免 ——
    否则存量仓里任何一次整文件体检都会把老文件全翻成 ❌，legacy_baseline 的承诺当场作废。
    这一步是**行为变更**（不是纯重构），所以先在这里把边界写全再改。
    """

    def _sev(self, changed, legacy, is_new):
        import rule_base
        import encoding_ruleset
        ctx = rule_base.CheckContext(path=pathlib.Path("Program.cs"), rel="Program.cs",
                                     raw=b"", content="", script="", template="",
                                     mode="strict", repo="", changed_lines=changed,
                                     legacy_exempt=legacy, is_new_file=is_new, targeted=True)
        return encoding_ruleset.EncodingRule(expect="utf-8-bom").severity(ctx)

    def test_touch_is_three_state_not_truthiness(self):
        import rule_base
        E, I = rule_base.Severity.ERROR.name, rule_base.Severity.INFO.name
        cases = [
            ("★纯删除型改动（frozenset()）→ 硬拦：L1 本体，旧代码在这里报 INFO 放行",
             frozenset(), True, False, E),
            ("读到了 diff、这个文件确实没动过（None）→ 存量豁免必须保留",
             None, True, False, I),
            ("有新增行（{5}）→ 硬拦（回归，旧代码也是 E）",
             frozenset({5}), True, False, E),
            ("非 legacy 仓 → 一律硬拦（回归）",
             frozenset(), False, False, E),
            ("新建文件 → 不豁免（v8 G1 既有判据，与 changed_lines 无关）",
             None, True, True, E),
        ]
        bad = []
        for name, changed, legacy, new, want in cases:
            got = self._sev(changed, legacy, new).name
            if got != want:
                bad.append("%s：期望 %s，实得 %s" % (name, want, got))
        self.assertEqual(bad, [], "ENC 的 touched 判据不是三态：\n" + "\n".join(bad))


class TestL2ChangedLinesOnlyAdvancesOnRcZero(unittest.TestCase):
    """L2：`compute_changed_lines` 里"读到了 diff"的**唯一**进位条件是 `rc == 0`。

    原实现把 `got_info = True` / `saw_diff = True` 在 try 块末尾**又写了一份**，位置在
    `rc` 判断**之前** —— 于是"命令失败、但 stdout 里有东西"会被读成"读到了 diff"，
    并且把失败时打印的正文**当 diff 解析**进行号集合；注释写着"本变体调用成功"，
    而那个位置根本没有判断成功与否（注释在替一个没做的判断背书）。

    实测分歧（就是本锁的两个方向）：
      · rc≠0 + 有正文 → 旧实现当"读到了"：正文里以 `+` 开头的行会被算成本次新增；
        若正文只是普通文本（无 `+`），则得到 `frozenset()` = "本次一行没新增" ⇒
        定向口径 0 命中 ⇒ **该文件的存量违规整套豁免生效**。错得比"多报"更隐蔽。
      · rc≠0 + 空正文 → 两种实现都 None（所以这个洞不是靠"失败"触发的，是靠"失败还带正文"）。

    诚实边界：这类形态**不常见**（git 的失败信息主要走 stderr），但它一旦出现，
    旧实现给出的是**结论**而不是"读不到"。判据不该建立在"它很少发生"上。
    另外 rc=0 的两条 companion 是必需的：没有它们，这条锁可能在为一个**夹具根本没喂进
    解析器**的假象上锁（本仓反复记过的"判据空转"）。

    夹具在 `subprocess.run` 这一层替换（OS 边界），其余全走真代码：`_is_git_worktree`
    的工作树判定照真、hunk 解析器照真、三态收口照真。
    """

    BODY = "@@ -1,2 +1,2 @@\n-var a = 1;\n+var b = 2;\n"          # 有一条新增行 → {1}
    DEL_ONLY = "@@ -1,2 +0,0 @@\n-var a = 1;\n-var b = 2;\n"     # 正文非空但没有新增行 → frozenset()

    def _call(self, rc, out):
        import rule_base
        from unittest import mock
        real_cp = subprocess.CompletedProcess

        def fake(cmd, **kw):
            if "rev-parse" in cmd:              # 工作树判定：照真（否则会退整文件口径）
                return real_cp(cmd, 0, "true\n", "")
            return real_cp(cmd, rc, out, "fatal: 模拟的 git 失败\n")

        with mock.patch("subprocess.run", side_effect=fake):
            return rule_base.compute_changed_lines(ROOT, os.path.join("checks", "rule_base.py"))

    def test_nonzero_rc_is_never_read_as_diff_information(self):
        cases = [
            ("★rc≠0 + 有正文 → 必须 None（L2 本体：旧实现给 frozenset({1})）",
             1, self.BODY, None),
            ("★rc≠0 + 全是删除行的正文 → 必须 None（旧实现给 frozenset()＝存量整套豁免）",
             128, self.DEL_ONLY, None),
            ("rc=0 + 有正文 → {1}（companion：证明夹具真的喂进了解析器）",
             0, self.BODY, frozenset({1})),
            ("rc=0 + 只有删除行 → frozenset()（三态里「动过」那一态，与 L1 咬合）",
             0, self.DEL_ONLY, frozenset()),
            ("rc=0 + 空正文 → None（文件没动）",
             0, "", None),
            ("rc≠0 + 空正文 → None（这条两种实现相同，留作边界记录）",
             1, "", None),
        ]
        bad = []
        for name, rc, out, want in cases:
            got = self._call(rc, out)
            if got != want:
                bad.append("%s：期望 %r，实得 %r" % (name, want, got))
        self.assertEqual(bad, [], "\n".join(bad))


class TestL10ReadJsonlZeroMeansNoRecords(unittest.TestCase):
    """L10：`cli.py log/ops --last 0` 的正确答案是**一条都不要**，不是"全都要"。

    `lines[-last:]` 在 Python 里 `-0 == 0`，于是 `--last 0` 退化成 `lines[0:]` —— 把整本
    台账倒出来；负值更离谱（`--last -5` → `lines[5:]`，丢掉开头 5 条、把其余全部返回），
    两种既不是"最后 N 条"也不是"全部"。台账在本机可以几千条，这种"参数写 0、行为是全量"
    的错位在排查现场极难认出来（输出看着就是一份正常台账）。

    两向：N≤0 → 0 条（含 None）；N>0 → 仍取最后 N 条（含 N 超过总条数时取全部）。
    """

    def _load(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "l10_cli", os.path.join(ROOT, "guardrail_engine", "cli.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_zero_and_negative_last_yield_no_records(self):
        cli = self._load()
        tmp = tempfile.mkdtemp(prefix="l10jsonl_")
        self.addCleanup(shutil.rmtree, tmp, True)
        p = os.path.join(tmp, "gate_log.jsonl")
        with open(p, "w", encoding="utf-8", newline="") as f:
            for i in range(3):
                f.write(json.dumps({"n": i}) + "\n")
        self.assertEqual(cli._read_jsonl(p, 0), ([], 0),
                         "`--last 0` 把全本台账读出来了（`-0 == 0` 那条老坑）")
        self.assertEqual(cli._read_jsonl(p, -5), ([], 0), "负值应当是 0 条")
        self.assertEqual(cli._read_jsonl(p, None), ([], 0), "None 应当是 0 条（不该崩）")
        # 反向：正常档位照旧
        self.assertEqual([r["n"] for r in cli._read_jsonl(p, 2)[0]], [1, 2],
                         "`--last 2` 取最后两条这条口径被弄坏了")
        self.assertEqual([r["n"] for r in cli._read_jsonl(p, 99)[0]], [0, 1, 2],
                         "N 超过总条数 → 全部")
        self.assertEqual(cli._read_jsonl(os.path.join(tmp, "nope.jsonl"), 5), ([], 0),
                         "台账文件不存在时照旧返回空（不该崩）")


class TestL4WaiverMarkerMustBeInComment(unittest.TestCase):
    """L4 的判据地基：`comment_spans` 必须分得清注释与字符串。

    缺陷本体（PC/H5/后端三处）是「拿原始行文本去搜 `harness-ok:`」，于是字符串字面量里的
    标记会把这行自己豁免掉。修法落在一个共用函数 `rule_base.comment_spans` 上，这里直接钉
    它的矩阵：注释与字符串同现时的归属（`"http://x"`、模板串、HTML 注释、未收尾块注释）。
    """

    def test_comment_spans_matrix(self):
        """`comment_spans` 的直接矩阵：注释符与字符串同现时的归属。

        这几行是判据的地基（上面所有断言都经过它），单独钉一遍 —— 尤其
        `"http://x"` 这种"字符串里的 //"：把它当注释起点，标记就会从不该生效的地方生效。
        """
        from rule_base import comment_spans as cs

        def span_text(line):
            return [line[a:b] for a, b in cs(line)]

        cases = [
            ("  // harness-ok: X 理由", [" harness-ok: X 理由"], "行注释到行尾"),
            ("  x(); /* harness-ok: X 理由 */", [" harness-ok: X 理由 "], "尾随块注释"),
            ("  /* harness-ok: X 理由 */ x();", [" harness-ok: X 理由 "], "前导块注释"),
            ('  console.log("harness-ok: X 理由")', [], "字符串里没有注释区间"),
            ('  var u = "http://x"; // harness-ok: X 理由', [" harness-ok: X 理由"],
             "字符串里的 // 不算注释，后面那个才算"),
            ('  var u = "http://x";', [], "只有字符串里的 //"),
            ("  const s = `a // b`; // harness-ok: X 理由", [" harness-ok: X 理由"],
             "模板串里的 // 不算注释"),
            ("  // don't use console.log()", [" don't use console.log()"],
             "注释正文里的撇号不吞掉注释"),
            ("  /* 没在本行收尾", [" 没在本行收尾"], "块注释不在本行收尾 → 本行余下都是注释"),
            ("  <!-- harness-ok: X 理由 -->", [" harness-ok: X 理由 "], "HTML 注释（模板段规则的豁免出口）"),
            ('  <demo-widget title="harness-ok: X 理由" :hasLayer="false">', [],
             '属性字符串里的标记不是注释 → 不能解除对该行的检查（模板段规则的安全边界）'),
            ("  <div><!-- 没在本行收尾", [" 没在本行收尾"], "HTML 注释不在本行收尾 → 本行余下都是注释"),
            ("", [], "空行"),
        ]
        for line, want, why in cases:
            with self.subTest(why=why):
                self.assertEqual(span_text(line), want, "%s：%r" % (why, line))


class TestL11InterpreterMustExist(unittest.TestCase):
    """L11-a：体检器必须校验命令里的**解释器**是否存在（原先只看钩子脚本在不在）。

    缺陷现场：钩子命令是 `<解释器> <钩子脚本> <客户端> <事件>` 拼的，解释器那一段是
    **环境相关**的绝对路径（本机是 uv 装出来的 `…/uv/python/cpython-3.11-…/python.exe`）。
    uv 升级 / 重建环境 / 换 Python 版本之后那条路径就没了 —— 症状与"钩子被删"一模一样
    （宿主调不起命令、模型侧什么都收不到），而**体检器当时是全绿的**。

    三向都钉（与 Q4-④ 的三态一致）：
      · 绝对路径不存在 → ❌ 确定坏；
      · 绝对路径存在（含**带空格的引号路径**）→ 不许假红；
      · 裸命令名 → 至多 ⚠（体检器进程的 PATH ≠ 宿主执行钩子时的 PATH，判不了"确定坏"）；
      · **不引用门禁的命令**不许被这条判据牵连（别的 MCP/子进程命令写什么解释器与我们无关）。
    """

    def setUp(self):
        from _tmpclean import mkdtemp_cleanup, rmtree_force
        self._rmtree = rmtree_force
        self.dir = mkdtemp_cleanup("rev0923-l11-")
        self.home = self.dir
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ccc_l11", os.path.join(ROOT, "sandbox", "check_client_configs.py"))
        self.ccc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ccc)
        self.cfg = self.ccc._client("trae")
        self.gate = os.path.join(self.home, "agentguard-gate.py")   # 名字要带门禁关键字
        pathlib.Path(self.gate).write_text("# stub\n", encoding="utf-8")
        self.gate_f = self.gate.replace("\\", "/")

    def tearDown(self):
        self._rmtree(self.dir)

    # ---------- 工具 ----------
    def _judge(self, obj, client="trae"):
        p = os.path.join(self.home, "probe.json")
        pathlib.Path(p).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8", newline="")
        return self.ccc.judge_config_file(p, self.ccc._client(client), self.home)

    def _trae(self, cmd, events=("PostToolUse", "Stop")):
        return {"hooks": {e: [{"hooks": [{"type": "command", "command": cmd}]}] for e in events}}

    def _cmd(self, interp, tail="trae; exit $LASTEXITCODE"):
        return "%s %s %s" % (interp, self.gate_f, tail)

    GONE = "C:/definitely/not/here/python.exe"

    # ---------- 缺陷本体 ----------
    def test_missing_interpreter_is_bad(self):
        lvl, probs, _ = self._judge(self._trae(self._cmd(self.GONE)))
        self.assertEqual(lvl, "bad", probs)
        self.assertTrue(any("解释器不存在" in p for p in probs), probs)
        # 这条 ❌ 必须来自解释器，不能是"钩子脚本不存在"顺带报出来的（脚本明明存在）
        self.assertFalse(any("钩子脚本不存在" in p for p in probs), probs)

    def test_existing_interpreter_is_ok(self):
        """反向：真解释器 → ✅（判据不许把好配置判坏）。"""
        lvl, probs, _ = self._judge(self._trae(self._cmd(PY.replace("\\", "/"))))
        self.assertEqual(lvl, "ok", probs)

    def test_quoted_path_with_space_is_not_a_false_alarm(self):
        """**带空格的引号路径**不许被判不存在（`_tokens` 的锁）。

        朴素切法 `cmd.replace('"', " ").split()` 会把 `"C:/Program Files/…/python.exe"`
        切成 `C:/Program` + `Files/…`，于是**存在**的解释器被判成不存在 —— 假红。
        这里造一个真的带空格目录（`<tmp>/a b/python.exe`），走 claude（cmd 宿主，允许引号）。
        """
        d = os.path.join(self.home, "a b")
        os.makedirs(d, exist_ok=True)
        interp = os.path.join(d, "python.exe")
        pathlib.Path(interp).write_text("# stub\n", encoding="utf-8")
        cmd = '"%s" "%s" claude PostToolUse' % (interp.replace("\\", "/"), self.gate_f)
        lvl, probs, _ = self._judge({"hooks": {"PostToolUse": [
            {"hooks": [{"type": "command", "command": cmd}]}]}}, client="claude")
        # ★ 判据落在**结果**上（这条配置一切正常 ⇒ 必须 ✅）：朴素切词会把它读成
        # 「解释器 `a` / `b/python.exe` 不在 PATH」→ 降成 ⚠，这一行就红。
        self.assertEqual(lvl, "ok", probs)
        self.assertFalse(any("解释器不存在" in p for p in probs), probs)
        # 顺带钉住切词本身：引号内的空格不切
        self.assertEqual(self.ccc._tokens(cmd)[0], interp.replace("\\", "/"))

    def test_bare_command_name_is_warn_not_bad(self):
        """裸命令名（不在 PATH 里）→ ⚠ 而不是 ❌：体检器的 PATH ≠ 宿主的 PATH，判不了"确定坏"。"""
        lvl, probs, _ = self._judge(self._trae(self._cmd("zzz-no-such-interpreter-xyz")))
        self.assertEqual(lvl, "warn", probs)
        self.assertTrue(any("PATH" in p for p in probs), probs)

    def test_no_interpreter_segment_is_not_judged(self):
        """命令里没有解释器那一段（直接执行脚本，靠 shebang/文件关联）→ **不判**。

        原打算给它 ⚠，但本仓 `tests/test_client_config_check.py` 里就有一份这样的合法夹具
        （`<shim> trae; exit $LASTEXITCODE`，Windows 上靠 .py 文件关联照样跑得起来），
        老规矩：认不出就别猜 —— 判它"坏"就是假红，而假红毁的是体检器本身的信誉。
        """
        lvl, probs, _ = self._judge(self._trae("%s trae; exit $LASTEXITCODE" % self.gate_f))
        self.assertEqual(lvl, "ok", probs)

    def test_split_command_and_args_is_judged(self):
        """ZCode 的 `command`+`args` 拆开形态必须被看见（否则它一家永远漏检）。

        ZCode 把命令拆成 `command`=解释器 + `args`=[脚本, 客户端, 事件]。分开走时两半都判不了：
        `args` 那半看着就是脚本路径、`command` 那半又不含门禁关键字。合成一条再判才对。
        """
        bad = {"hooks": {"events": {
            e: [{"hooks": [{"type": "process", "command": self.GONE,
                            "args": [self.gate.replace("\\", "/"), "zcode", e]}]}]
            for e in ("PostToolUse", "Stop")}}}
        lvl, probs, _ = self._judge(bad, client="zcode")
        self.assertEqual(lvl, "bad", probs)
        hits = [p for p in probs if "解释器不存在" in p]
        # 两个事件 = 两条命令 ⇒ 恰好两条 ❌，且都落在**合成后**的键上（`command+args`）。
        # 若哪天 `args` 那半又被单独产出，这里会多出一条 `/args` 的 —— 同一条命令数两遍。
        wheres = sorted(p.split(" ")[0] for p in hits)
        self.assertEqual(len(hits), 2, probs)
        self.assertTrue(all(w.endswith("/command+args") for w in wheres), wheres)
        good = {"hooks": {"events": {
            e: [{"hooks": [{"type": "process", "command": PY,
                            "args": [self.gate.replace("\\", "/"), "zcode", e]}]}]
            for e in ("PostToolUse", "Stop")}}}
        self.assertEqual(self._judge(good, client="zcode")[0], "ok")

    def test_unrelated_command_is_not_judged(self):
        """配置里**不引用门禁**的命令（比如某条 MCP/子进程命令）不许被这条判据牵连。"""
        obj = self._trae(self._cmd(PY.replace("\\", "/")))
        obj["mcpServers"] = {"other": {"command": self.GONE, "args": ["serve.py", "--stdio"]}}
        lvl, probs, _ = self._judge(obj)
        self.assertEqual(lvl, "ok", probs)


# 子脚本：真 import 钩子模块 → 读一次 stdin → **睡过看门狗窗口** → 才写出结论。
# 为什么要有这一步（2026-09-23 反方向注入当场量出来的缺口）：上面那条"不撤看门狗"的注入
# （`finally: done.set()` 改成 `pass`）在**全套锁**下**一条都没红** —— 因为既有锁都在问
# "没看门狗会不会卡死"（M72 的方向），而没有一条问"看门狗**撤没撤**"。
# 可这一半更阴：钩子照常跑完，结论还没打印就被自己的看门狗 `os._exit(0)` 杀掉 —— 客户端
# 收到 exit 0 当成**放行**，用户看到的只是"门禁忽然不管用了"。卡死至少看得见，这个看不见。
# 判据落在**产品后果**上（进程活到能把结论写出来），而不是"Event 有没有被 set"这种实现细节：
# 换任何等价实现（Event / 标志位 / 取消计时器）都该绿。
_L11_WD_CHILD = r'''
import importlib.util, io, sys, time
_spec = importlib.util.spec_from_file_location("hookl11", sys.argv[1])
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)          # ★ 必须真执行：L11-b 的原缺陷就发生在 import 期
sys.stdin = io.StringIO('{"cwd": "x"}')
_m._read_stdin_text()
time.sleep(float(sys.argv[2]))        # 睡过看门狗窗口；被撤掉才可能活到这里
sys.stdout.write("SURVIVED\n")
'''


class TestL11HookMustNotHangOrCrash(unittest.TestCase):
    """L11-b/c：钩子自身两条"起不来/挂死"的失败面（都真起子进程验，不 grep 源码）。

    · `HARNESS_HOOK_TIMEOUT` 非数字原先是**模块级** `int()` → import 期 ValueError →
      七个客户端的编辑期门禁**全部失效**，而用户看到的现象只是"改了下配置就不响了"。
      现在回退默认值 + stderr 提示一次，照常跑。
    · `stdin.read()` 原先没有超时（那道 subprocess 超时只包得住"跑门禁"，包不住"等宿主
      写完 JSON"）→ 宿主把管道留着不关就整场会话挂在钩子上。现在有看门狗，超时按放行处理。

    ★ 这两例故意**不挂 `_slow`**：它们不跑 auto-gate（payload 里没有 file_path → 在读完之后
      就返回了），单例 0.3s / 1.3s，比"真起 node"那两组便宜得多，没理由在快跑档里被跳过。
    """

    def setUp(self):
        from _tmpclean import mkdtemp_cleanup, rmtree_force
        self._rmtree = rmtree_force
        self.dir = mkdtemp_cleanup("rev0923-l11hook-")

    def tearDown(self):
        self._rmtree(self.dir)

    def _env(self, **extra):
        env = dict(os.environ, PYTHONUTF8="1")
        env.update(extra)
        return env

    def _run_with_stdin_open(self, env, timeout=20):
        """起一个**不写也不关 stdin** 的钩子进程，等它自己退出 → `(rc, stderr, 用时秒)`。

        管道全部显式关闭：留着不关就是 ResourceWarning，而被测仓的 CI 有严格句柄门槛
        （本文件其它用例的 setUp 里也写着同一条纪律）。
        返回 rc=None 表示**没等到它退出**（= 缺陷本体：挂死）。
        """
        import time
        t0 = time.time()
        p = subprocess.Popen([PY, HOOK, "claude", "PostToolUse"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             encoding="utf-8", errors="replace", env=env)
        rc, err = None, ""
        try:
            p.wait(timeout=timeout)
            rc, err = p.returncode, (p.stderr.read() or "")
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
        finally:
            for f in (p.stdin, p.stdout, p.stderr):
                try:
                    f.close()
                except Exception:
                    pass
        return rc, err, time.time() - t0

    def test_garbage_timeout_does_not_crash_the_hook(self):
        payload = json.dumps({"cwd": self.dir.replace("\\", "/")})
        for bad in ("3m", "0", "-5", "180s"):
            with self.subTest(value=bad):
                r = subprocess.run([PY, HOOK, "claude", "PostToolUse"], input=payload,
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=120,
                                   env=self._env(HARNESS_HOOK_TIMEOUT=bad))
                err = r.stderr or ""
                self.assertNotIn("Traceback", err, "写坏 %s 让钩子崩了：%s" % (bad, err[-400:]))
                self.assertEqual(r.returncode, 0, "钩子不该因为环境变量写坏而返回非 0：%s" % err[-400:])
                self.assertIn("已回退默认值", err, "回退必须出声（否则用户不知道超时没生效）")

    def test_stdin_left_open_does_not_hang(self):
        """宿主不关 stdin → 看门狗到点放行（rc=0），不是把会话挂死。"""
        rc, err, _dt = self._run_with_stdin_open(self._env(HARNESS_HOOK_STDIN_TIMEOUT="1"))
        self.assertIsNotNone(rc, "stdin 不关时钩子挂死了（看门狗没生效）")
        self.assertEqual(rc, 0, "看门狗到点应放行（rc=0），实得 %s：%s" % (rc, err))
        self.assertIn("放行不检查", err, "超时放行必须出声一次：%r" % err)

    def test_stdin_timeout_is_really_wired(self):
        """反向：把看门狗时间改小，退出必须**跟着变快** —— 否则上面那条可能只是碰巧结束了。"""
        rc, _err, dt = self._run_with_stdin_open(self._env(HARNESS_HOOK_STDIN_TIMEOUT="3"))
        self.assertIsNotNone(rc, "看门狗设 3 秒仍未退出")
        self.assertGreaterEqual(dt, 2.0, "退出得比看门狗还早（%.1fs）—— 那测的不是看门狗" % dt)
        self.assertLess(dt, 9.0, "看门狗设 3 秒却等了 %.1fs（回退成默认 10 秒了？）" % dt)

    def test_watchdog_is_disarmed_after_a_normal_read(self):
        """反向的另一半：stdin **正常写完**时，看门狗必须被撤掉 —— 它只管"等不到"那种情形。

        （2026-09-23 反方向注入量出来的缺口：把 `finally: done.set()` 改成 `pass`，
        上面三条锁**一条都不红**。原因是它们全在问"没看门狗会不会卡死"，没有一条问
        "看门狗撤没撤"。而缺了撤销这一半，钩子会在**结论交付之前**被自己杀掉：
        rc=0、客户端当成放行 —— 编辑器里表现只是"门禁忽然不管用了"，没人会追到这条改动上。）

        做法：子进程真 import 钩子、读一次 stdin、**睡过看门狗窗口**，能写出结论才算活下来。
        判据落在产品后果上（结论有没有交付），不落在"Event 有没有 set"这种实现细节上。
        代价 ≈2.6s，故意不挂 `_slow`：它不跑 auto-gate，且这条缺口正是快跑档最该守住的那种。
        """
        child = os.path.join(self.dir, "_l11_wd_child.py")
        with open(child, "w", encoding="utf-8") as f:
            f.write(_L11_WD_CHILD)
        r = subprocess.run([PY, child, HOOK, "2.5"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60,
                           env=self._env(HARNESS_HOOK_STDIN_TIMEOUT="1"))
        self.assertIn("SURVIVED", r.stdout or "",
                      "stdin 正常读完，进程却在写结论前被看门狗杀掉了（rc=%s）→ "
                      "门禁结论整段丢失、客户端按放行处理：%s"
                      % (r.returncode, (r.stderr or "")[-300:]))
        self.assertEqual(r.returncode, 0)


class _McpInProcess(unittest.TestCase):
    """在进程内直调 MCP 壳层（不起 stdio 服务、不落台账）。

    判据落在 `handle_tools_call` / `tool_run_gate` 的返回值上 —— 与
    `tests/test_review_v13_batch3.py` 里那批同源同法（那边还额外起真子进程，这里不需要：
    L7/L9 要判的是「拒绝发生在什么时候、归成哪个码」，两件事都在壳层函数内部）。
    """

    def setUp(self):
        sys.path.insert(0, ROOT)
        from guardrail_engine import mcp
        self.mcp = mcp
        self.dir = tempfile.mkdtemp(prefix="l7l9-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _stub_run_cmd(self):
        """换掉「起子进程跑引擎」那一步，返回**调用记录**（用来证明"拒绝了"与"没起进程"是同一件事）。"""
        calls = []
        orig = self.mcp.run_cmd

        def fake(cmd, cwd=None, timeout=None):
            calls.append(list(cmd))
            return 0, "stub ok", ""

        self.mcp.run_cmd = fake
        self.addCleanup(setattr, self.mcp, "run_cmd", orig)
        return calls


class TestL7BadParamsAreInvalidParams(_McpInProcess):
    """L7：坏参数必须归成 -32602（调用方的错），不能兜成 -32603（服务端的错）。

    -32603 的语义是「服务端自己出了意外」—— 宿主对它的合理反应是重试/重连/停用这个 server；
    而这里真正发生的是宿主自己传了个列表。归错码的代价不是"报错难看"，是**把一次可以
    当场改对的调用，变成一条会被当成环境噪声淹没的诊断**。
    """

    def _code(self, params):
        r = self.mcp.handle_tools_call(params)
        err = r.get("__rpc_error__") if isinstance(r, dict) else None
        return (err or {}).get("code"), r

    def test_non_string_name_is_invalid_params(self):
        """实测原缺陷：`name: [1,2]` → `[1,2] not in _HANDLERS` 抛
        `TypeError: unhashable type: 'list'` → 兜成 -32603。"""
        for bad in ([1, 2], {"a": 1}, 5, True):
            with self.subTest(name=bad):
                code, r = self._code({"name": bad, "arguments": {}})
                self.assertEqual(code, -32602,
                                 "坏 `name` 归成了 %s（应是 -32602 Invalid params）：%r" % (code, r))

    def test_non_dict_params_is_invalid_params(self):
        for bad in ([1, 2], "tools/call", 5):
            with self.subTest(params=bad):
                code, r = self._code(bad)
                self.assertEqual(code, -32602, "坏 `params` 归成了 %s：%r" % (code, r))

    def test_missing_params_still_reaches_unknown_tool(self):
        """反向对照：`params` 在 JSON-RPC 里是**可选**的 —— 缺省必须照旧走到
        「名字不认识」那条路（否则这条类型校验会变成"凡不带参数一律拒"）。"""
        for bad in ({"arguments": {}}, {"name": None, "arguments": {}}):
            with self.subTest(params=bad):
                code, r = self._code(bad)
                self.assertEqual(code, -32602)
                self.assertIn("Unknown tool", r["__rpc_error__"]["message"],
                              "缺 `name` 被当成了「参数类型错」，而不是「没有这个工具」：%r" % (r,))

    def test_non_dict_arguments_is_invalid_params(self):
        """`arguments` 非空且不是对象时，每个 handler 的 `a.get(...)` 都会崩 ——
        原先崩在 handler 里，被兜成 `isError=True` 的**正文**（连 RPC 错误码都没有）。"""
        for bad in ("abc", [1], 5):
            with self.subTest(arguments=bad):
                code, r = self._code({"name": "harness_list_gates", "arguments": bad})
                self.assertEqual(code, -32602, "坏 `arguments` 归成了 %s：%r" % (code, r))

    def test_empty_arguments_forms_are_still_tolerated(self):
        """反向（判据收过头）：`null`/`{}`/`[]`/`""` 都是宿主常见的"无参数"写法，
        一律当"没传"照跑 —— 否则这条校验会把**能工作的宿主**也拒了。"""
        self.mcp._HANDLERS["l7_probe"] = lambda a: ("got=%r" % (a,), False)
        self.addCleanup(self.mcp._HANDLERS.pop, "l7_probe", None)
        for empty in (None, {}, [], "", 0):
            with self.subTest(arguments=empty):
                r = self.mcp.handle_tools_call({"name": "l7_probe", "arguments": empty})
                self.assertNotIn("__rpc_error__", r, "空值形态被拒了：%r" % (r,))
                self.assertIn("got={}", r["content"][0]["text"],
                              "空值没有被归一成 {}：%r" % (r,))


class TestL9RepoMustLookLikeARepository(_McpInProcess):
    """L9：`repo` 支原先只验 `isdir` —— inputSchema 的契约写的是「a repository directory」。

    ★ 判 `exists` 而不是 `isdir`：worktree 的 `.git` 是**文件**（本机受管仓里就有 12 个
    这种 worktree），判 isdir 会把它们整批误拒。所以这条锁必须成对：
    一个"不是仓库"的目录要拒，一个"worktree 形态"的目录要放。
    """

    def test_plain_directory_is_rejected_without_spawning_the_engine(self):
        calls = self._stub_run_cmd()
        text, is_err = self.mcp.tool_run_gate({"repo": self.dir})
        self.assertTrue(is_err, "普通目录被当成仓库放行了：%s" % text)
        self.assertIn(".git", text, "拒绝文案没说清「为什么不行」：%s" % text)
        self.assertEqual(calls, [],
                         "拒绝了却仍然起了引擎子进程 —— 任意目录树照样会被走一遍：%r" % (calls,))

    def test_worktree_style_dot_git_file_still_passes(self):
        """`.git` 是**文件**（worktree / submodule 形态）同样算仓库 —— 判 isdir 就成了假红。"""
        with open(os.path.join(self.dir, ".git"), "w", encoding="utf-8") as f:
            f.write("gitdir: D:/somewhere/.git/worktrees/wt\n")
        calls = self._stub_run_cmd()
        text, is_err = self.mcp.tool_run_gate({"repo": self.dir})
        self.assertFalse(is_err, "worktree 形态（.git 是文件）被误拒了：%s" % text)
        self.assertEqual(len(calls), 1, "没起引擎（拒绝了？）：%s" % text)
        self.assertIn("--repo", calls[0])
        self.assertIn(os.path.abspath(self.dir), calls[0],
                      "下传的应是绝对路径（auto-gate 按它找归属）：%r" % (calls[0],))


class TestM12GitSensorFailureIsNotLegacy(unittest.TestCase):
    """M12（中）：diff **读不到**时，legacy 仓的"存量降级"把新代码一起放过。

    `compute_changed_lines` 的三态把两件相反的事合并进了同一个 `None`：
      · 「两处 diff 都成功、正文为空」＝ 文件**本次没动** → 该降级（`cli.py check <文件>`
        这种存量体检验的就是这条）；
      · 「git 调用失败」＝ **传感器故障** → 此刻没有任何证据说明文件是存量，降级就是**放宽**。
    legacy 仓的降级判据恰好是「changed_lines 为空 ⇒ 存量」，于是故障静默落进降级分支 ——
    缺省档只出一次 stderr（stderr 不是结论通道，**rc 不变**），对只看 rc 的调用方就是 fail-open。

    修法两件：① 三态旁边多带一个**成因位**（`compute_changed_lines_ex` 的第二个返回值 →
    `ctx.git_sensor_failed`）；② 加 `HARNESS_GIT_REQUIRED=1` 档，与 node 侧的
    `HARNESS_NODE_REQUIRED` 对称 —— 开了档就不许降级（宁可按新代码拦，存量会更吵）。

    ★ 这一组锁的重心在**两个方向都不能错**：档没开时四个降级点必须逐字保持旧行为
    （否则等于借这次修复偷偷改了所有机器的判定），档开了也必须**只**对"读不到"生效 ——
    若对"文件确实没动"也生效，`cli.py check` 存量体检会把整个仓的历史违规全报成 ❌。
    """

    def setUp(self):
        import rule_base
        self.rb = rule_base
        self._saved = os.environ.pop("HARNESS_GIT_REQUIRED", None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._saved is None:
            os.environ.pop("HARNESS_GIT_REQUIRED", None)
        else:
            os.environ["HARNESS_GIT_REQUIRED"] = self._saved

    def _ctx(self, sensor_failed, changed_lines=None):
        """legacy 仓 + 定向口径的一份标准 ctx（各降级点的共同输入）。"""
        rel = "src/backend/HomeController.cs"
        return self.rb.CheckContext(path=pathlib.Path(rel), rel=rel, raw=b"x", content="x",
                                    mode="strict", repo="R", changed_lines=changed_lines,
                                    is_new_file=False, legacy_exempt=True, targeted=True,
                                    git_sensor_failed=sensor_failed)

    def _downgrade_sites(self, ctx):
        """各自实现降级判据的规则类（开源裁剪后剩下三个活的落点）。"""
        import encoding_ruleset as er
        return {
            "基类 Rule（行级规则）": self.rb.Rule().severity(ctx),
            "WholeFileRule（无行号结论）": self.rb.WholeFileRule().severity(ctx),
            "ENC 编码（只看是否动过）": er.EncodingRule().severity(ctx),
        }

    def test_default_tier_keeps_every_downgrade_site_unchanged(self):
        """缺省档：**带故障位也一样降级**（= 本次改动对不开档的机器零行为差异）。

        这条是"反向 companion"：少了它，日后有人把 `git_sensor_failed()` 写成恒真，
        所有 legacy 仓立刻天天被存量违规拦死，而其它锁只会因为"更严了"而全绿。"""
        got = self._downgrade_sites(self._ctx(sensor_failed=True))
        bad = {k: v for k, v in got.items() if v != self.rb.Severity.INFO}
        self.assertEqual(bad, {}, "缺省档下这些降级点被改严了（本次修复不该有这效果）：%r" % bad)

    def test_fail_closed_tier_stops_the_downgrade(self):
        os.environ["HARNESS_GIT_REQUIRED"] = "1"
        got = self._downgrade_sites(self._ctx(sensor_failed=True))
        bad = {k: v for k, v in got.items() if v == self.rb.Severity.INFO}
        self.assertEqual(bad, {},
                         "开了 fail-closed 档，这些点仍然把「读不到 diff」当存量降级了：%r" % bad)

    def test_fail_closed_tier_does_not_fire_when_the_file_is_simply_untouched(self):
        """反向：`changed_lines is None` 里"文件本次没动"那一半**不受档位影响**。

        这一半是合法的（存量体检 / legacy 观察项）。若故障位在这里也生效，
        `cli.py check <某文件>` 会把整仓历史违规一次性报成 ❌ —— 假红比原缺陷更难发现。"""
        os.environ["HARNESS_GIT_REQUIRED"] = "1"
        got = self._downgrade_sites(self._ctx(sensor_failed=False))
        bad = {k: v for k, v in got.items() if v != self.rb.Severity.INFO}
        self.assertEqual(bad, {}, "「文件本来就没动」被当成了传感器故障：%r" % bad)

    # ---- `compute_changed_lines_ex` 本身：两个 None 必须分得开 ----

    def _run(self, args, cwd=None):
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")

    def _real_repo(self, rel="a.py", body="x = 1\n"):
        """真 git 仓 + 一提交（**显式设身份**：不设时 Linux/CI 上 commit 直接失败，
        夹具就成了"只在开发机成立"的那种 —— 本仓为此复发过三次）。"""
        d = tempfile.mkdtemp(prefix="m12-")
        self.addCleanup(shutil.rmtree, d, True)
        self._run(["git", "init", "-q"], cwd=d)
        self._run(["git", "config", "user.email", "t@t"], cwd=d)
        self._run(["git", "config", "user.name", "t"], cwd=d)
        with open(os.path.join(d, rel), "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        self._run(["git", "add", "-A"], cwd=d)
        r = self._run(["git", "commit", "-qm", "init"], cwd=d)
        self.assertEqual(r.returncode, 0, "夹具仓建不起来：%s" % (r.stderr or ""))
        return d

    def _break_git(self, mode):
        """把 git 调用打断。`rule_base` 是函数内 `import subprocess`，拿到的是**同一个模块
        对象**，所以换 `subprocess.run` 这一属性对它是生效的（用完即还原）。"""
        real = subprocess.run

        def fake(cmd, *a, **k):
            if mode == "binary-missing":
                raise FileNotFoundError("git")
            if "--is-inside-work-tree" in cmd:      # 探针照常回答，diff 才失败
                return subprocess.CompletedProcess(cmd, 0, "true\n", "")
            raise FileNotFoundError("git")

        subprocess.run = fake
        self.addCleanup(setattr, subprocess, "run", real)

    def test_untouched_file_in_a_real_repo_is_not_a_sensor_failure(self):
        d = self._real_repo()
        changed, failed = self.rb.compute_changed_lines_ex(d, "a.py")
        self.assertIsNone(changed, "已提交且没改动的文件不该有 diff：%r" % (changed,))
        self.assertFalse(failed, "「文件没动」被误报成传感器故障 —— 整仓体检会被报成红海")
        # 三态入口一字未改：同一个调用在旧签名下仍是裸 None
        self.assertIsNone(self.rb.compute_changed_lines(d, "a.py"))

    def test_git_call_failure_is_reported_as_a_sensor_failure(self):
        d = self._real_repo()
        self._break_git("diff-fails")            # 探针成功、diff 抛 FileNotFoundError
        changed, failed = self.rb.compute_changed_lines_ex(d, "a.py")
        self.assertIsNone(changed)
        self.assertTrue(failed, "git diff 调用失败被当成了「文件没改动」：%s" % (changed,))

    def test_git_binary_missing_is_a_sensor_failure_not_a_plain_directory(self):
        """git 压根跑不起来时，探针也是失败 —— 别静默混进"这里不是 git 仓"那一档。"""
        import rule_base
        d = tempfile.mkdtemp(prefix="m12-")
        self.addCleanup(shutil.rmtree, d, True)
        rule_base._REPO_TRUE.discard(os.path.normcase(os.path.abspath(d)))   # 别吃缓存
        self._break_git("binary-missing")
        changed, failed = self.rb.compute_changed_lines_ex(d, "a.py")
        self.assertIsNone(changed)
        self.assertTrue(failed, "git 不在 PATH 时静默退成了「合法输入」")

    def test_a_plain_directory_is_still_legitimate_input(self):
        """★ 别把这一档做成误伤面：`cli.py check <任意目录>` 是**合法输入**（非 git 目录），
        git 明确回答"不是工作树"（rc≠0），那不是故障 —— fail-closed 档也不该在它上面开火。"""
        d = tempfile.mkdtemp(prefix="m12-")
        self.addCleanup(shutil.rmtree, d, True)
        os.environ["HARNESS_GIT_REQUIRED"] = "1"
        changed, failed = self.rb.compute_changed_lines_ex(d, "a.py")
        self.assertIsNone(changed)
        self.assertFalse(failed, "非 git 目录被当成了传感器故障：fail-closed 档会假红")

    def test_the_warning_names_the_switch(self):
        """"至少在文档里写明该情形"那一半：告警必须给出**出路**（与 node 侧告警同款），
        否则读到它的人只知道门在故障时是松的，不知道可以把它拧紧。"""
        import io as _io
        import contextlib
        import rule_base
        saved = rule_base._GIT_DIAG_WARNED
        rule_base._GIT_DIAG_WARNED = False
        buf = _io.StringIO()
        try:
            with contextlib.redirect_stderr(buf):
                rule_base._warn_git_once("测试用")
        finally:
            rule_base._GIT_DIAG_WARNED = saved
        self.assertIn("HARNESS_GIT_REQUIRED", buf.getvalue(),
                      "告警没写明 fail-closed 开关名：%r" % buf.getvalue())


class TestL14BatchFileIsNotHandedToAShell(unittest.TestCase):
    """L14（低）：`.cmd`/`.bat` 先经 `cmd /c` 再交给子进程 —— 命令行被 cmd **再解析一遍**。

    评审原话是"路径含空格/中文的引号双重解析风险（**未实测复现**）"。实测后的形态比它**窄**、
    但比它**具体**（本机 3.11.15 / Win11 26200；四种候选写法 × 四个格子，口径见台账）：

    | exe 路径 | 参数 | `[cmd,/c,exe]+args`（原写法） | `[exe]+args`（直跑） |
    |---|---|---|---|
    | 有空格 | 无空格 | ✅ | ✅ |
    | 有空格 | **有空格** | ❌ rc=1 且**输出为空** | ✅ |
    | 无空格 | 有空格 | ✅ | ✅ |

    即**要"路径有空格"和"参数有空格"同时成立才复现**：cmd 的 `/c` 规则是"命令行首字符与末字符
    都是引号就剥掉一对"，而只有在末尾**还有**含空格的参数时才会多出一个引号去凑成那一对 ——
    于是只有那一格会把引号剥错位。这既是它平时看不见的原因，也是评审只能标【推断】的原因。

    ★ 直跑的依据不是"碰巧能跑"：直接调 kernel32 的 `CreateProcessW`（**绕开 subprocess**）
      也能跑 `.cmd` 且退出码与夹具一致 ⇒ 是**操作系统本人**认 `.cmd`（Python 文档同此：
      "batch files may be launched by the operating system in a system shell"）。
      仓内 `agentguard/mcp.py` 的 `run_cmd` 从来没有这一支 —— 本仓自己早就在用直跑。

    ★ 三处同形实现现在**口径一致**（`check_contracts.py` / `upgrade.py` / `uninstall.py`），
      加上本来就没有这一支的 `agentguard/mcp.py`，四份都进了下面这张表。
      分两批落是**被一堵墙拦的**、不是分两次做：`check_contracts.py` 不随包分发（改它零牵连），
      另两处**随安装包分发**，而 `verify_release.py` ⑧ 是「包内工具 ↔ 仓库源码」**逐字节**对拍 ——
      实测把包内 `upgrade.py` 加一个字节即 rc=1「不是同一份」。改了就必须重跑 `pack_release.py`，
      而当时 VERSION `0.1.6-dev` **已经发过**（GitHub Release + 内网镜像，sha256 公开）：
      同版本号、不同内容的重打包，正是本仓第③条锁（`tests/test_release_artifact.py`）专门治过的事。

    ✅ **2026-09-23 已随 `v0.1.7-dev` 同批落地**（当时登记的七项、判据一处未改，逐项交代）：
        ① 两处 `run_cmd` 删掉 `.cmd/.bat` 分支 → `[exe] + list(args[1:])` 直跑；
        ② 两处 docstring 的假理由（"subprocess 不经 shell 执行不到"）改成实测结论；
        ③ `tests/test_upgrade.py::_fake_exe`、`tests/test_upgrade_path.py::_fake_exe` 的注释
           随之订正：**`.cmd` 后缀本身仍必需，但理由与 run_cmd 无关**（`CreateProcessW` 无扩展名
           时只补 `.exe`），别把"改了实现"读成"后缀可以省了"；
        ④ 本类表格补上那两份实现（见下面 test_④，直接驱动两文件的 `run_cmd`）；
        ⑤ `upgrade.py::main` 演练支不再丢 `fails`（读了 `fails` ⇒ 有未完成项就 rc=1）——
           锁在 L15 那一类（`TestL15RehearsalExitCodeAndItsDocsNote`，按语法树判、不按字面）；
        ⑥ `uninstall.py` 六处分母 `/5` → `/6`（五处步骤 docstring + `:87` 那句注释）；
        ⑦ 删掉 `docs/packaging/INSTALL.md` §5.1 那段临时 ⚠ —— **它与⑤是一对**，L15 的锁
           盯着两者同真同假，所以"改了代码忘删文档"会红、"删了文档代码照旧"也会红。
      ⚠ ⑤⑥⑦ 动的是**随包分发**的字节 ⇒ 随后必须重跑 `pack_release.py` + `verify_release.py` ⑧
        （新包内副本会随新版本号重新生成，§5.1 的说明文字也要跟着删）。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="l14-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    # ---- 夹具与驱动 ----

    def _load(self, rel, name):
        import importlib.util
        spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _batch_file(self):
        """一个"假客户端"：**放在含空格的目录里**（复现恰恰要靠这一点）。返回路径。"""
        d = os.path.join(self.tmp, "有 空 格")
        os.makedirs(d, exist_ok=True)
        if os.name == "nt":
            p = os.path.join(d, "fake-claude.cmd")
            with open(p, "w", encoding="ascii", newline="") as f:
                f.write("@echo off\r\necho FAKE-VERSION-1.2.3\r\nexit /b 0\r\n")
        else:
            p = os.path.join(d, "fake-claude")
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write("#!/bin/sh\necho FAKE-VERSION-1.2.3\nexit 0\n")
            os.chmod(p, 0o755)
        return p

    def _probe(self, mod, exe, on_run=None):
        """把 `check_contracts._version_of("claude")` 的解析链指到夹具上。

        只换 `shutil.which`（= 配置：哪个客户端在哪），**不换被测逻辑**；`on_run` 非空时
        连同 `subprocess.run` 一起换掉，用来观察**实际发给子进程的 argv**。
        """
        real_which = shutil.which
        shutil.which = lambda name: exe if name == "claude" else real_which(name)
        self.addCleanup(setattr, shutil, "which", real_which)
        if on_run is not None:
            real_run = subprocess.run
            subprocess.run = on_run
            self.addCleanup(setattr, subprocess, "run", real_run)
        return mod._version_of("claude")

    def _record(self, text=True):
        """换掉 subprocess.run，返回它收到的 argv 列表。

        `text` 要跟被锁调用方的口径对上：`check_contracts` 传 `text=True` 读 str，
        `mcp.py` 自己 `.decode()` 读 bytes —— 喂错类型会当场 TypeError，与 L14 无关。
        """
        seen = []

        def fake(cmd, *a, **k):
            seen.append(list(cmd))
            body = "FAKE-VERSION-1.2.3\n" if text else b"FAKE-VERSION-1.2.3\n"
            return subprocess.CompletedProcess(cmd, 0, body, "" if text else b"")

        return seen, fake

    # ---- ① 版本探针不许再套一层 shell（原缺陷的复现格） ----

    def test_version_probe_hands_the_batch_file_straight_to_subprocess(self):
        """`_version_of` 必须把 `.cmd` **直接**交给 subprocess —— 多一层 cmd 就是多一次引号解析。"""
        mod = self._load(os.path.join("sandbox", "check_contracts.py"), "_l14_cc")
        exe = self._batch_file()
        seen, fake = self._record()
        got = self._probe(mod, exe, on_run=fake)
        self.assertTrue(seen, "探针根本没起子进程（夹具没接上？）")
        self.assertEqual(seen[0][0], exe,
                         "版本探针把 .cmd 又交给了 shell（实际 argv=%r）—— cmd 会重解析引号，"
                         "路径与参数都含空格时会 rc=1 且输出为空" % (seen[0],))
        self.assertIn("--version", seen[0], "参数没原样传下去：%r" % (seen[0],))
        self.assertEqual(got, "FAKE-VERSION-1.2.3", "调用方没取到版本串：%r" % (got,))

    # ---- ② companion：mcp.py 那份本来就没有这一支，钉住它 ----

    def test_mcp_run_cmd_hands_them_straight_through_too(self):
        """`guardrail_engine/mcp.py::run_cmd` 从来没有 `.cmd` 分支 —— 别哪天有人"补"上它。

        P2-1 后 run_cmd 改走 `subprocess.Popen` + `_shell._communicate_tree`（为的是超时杀
        整棵进程树），不再是 `subprocess.run` —— 所以这里观察的是 **Popen** 收到的 argv。
        判据不变：argv[0] 必须就是那个含空格的 `.cmd` 本身，没有 `cmd /c` 那层壳。
        """
        sys.path.insert(0, ROOT)
        from guardrail_engine import mcp
        exe = self._batch_file()
        seen = []

        class _FakePopen(object):
            def __init__(self, cmd, *a, **k):
                seen.append(list(cmd))
                self.returncode = 0
                self.pid = 0

            def communicate(self, timeout=None):
                return (b"FAKE-VERSION-1.2.3\n", b"")

        real_popen = subprocess.Popen
        subprocess.Popen = _FakePopen
        self.addCleanup(setattr, subprocess, "Popen", real_popen)
        rc, out, _err = mcp.run_cmd([exe, "--version"], timeout=30)
        self.assertTrue(seen, "run_cmd 没起子进程")
        self.assertEqual(seen[0][0], exe, "mcp 的 run_cmd 也套了 shell：%r" % (seen[0],))
        self.assertEqual(rc, 0, "直跑 .cmd 的退出码应原样透传")
        self.assertIn("FAKE-VERSION-1.2.3", out, "没把子进程输出读回来")

    # ---- ③ 直跑真的能跑：**不加回退分支**的依据就落在这条上 ----

    def test_the_batch_file_really_runs_without_a_shell(self):
        """★ 这条是"直跑真的能跑"的**活体判据**，也就是当初决定*不加* `cmd /c` 回退的依据。

        若哪台机器上 CreateProcess 不认 `.cmd`（老系统会 `ERROR_BAD_EXE_FORMAT` / WinError 193），
        这条会**红**，而且届时有真复现可依 —— 那才是补回退分支的时候，不是凭担心先加上。
        （`run_cmd` 侧对应的失败形态是响亮的：rc=127 + "无法执行 …"，不会静默变成"跑过了"。）
        """
        mod = self._load(os.path.join("sandbox", "check_contracts.py"), "_l14_cc2")
        got = self._probe(mod, self._batch_file())
        self.assertEqual(got, "FAKE-VERSION-1.2.3",
                         "夹具没被真跑起来（拿到 %r）—— 这台机器上直跑 .cmd 不成立？" % (got,))

    # ---- ④ 两份**随包分发**的实现（L14 后半；2026-09-23 随 v0.1.7-dev 同批改） ----

    def test_the_two_package_tools_hand_them_straight_through_as_well(self):
        """`sandbox/upgrade.py::run_cmd` 与 `sandbox/uninstall.py::run_cmd` 也都得直跑 `.cmd`。

        这两份随安装包分发，所以当初被 `verify_release.py` ⑧ 的逐字节对拍挡在版本号后面
        （来龙去脉见本类 docstring）——已随 `v0.1.7-dev` 同批改完，这条锁就是防它们被改回去。

        ★ 驱动的是**这两个文件自己的** `run_cmd`（不是 `check_contracts` 的），所以三份实现里
          任何一份被单独改回 `cmd /c` 都会各自变红；喂 `text=False`（bytes）是因为这两份用
          `capture_output=True` 再自己 `_decode`，与 `mcp.py` 同口径。
        """
        for rel, name in (("upgrade.py", "_l14_up"), ("uninstall.py", "_l14_un")):
            with self.subTest(tool=rel):
                mod = self._load(os.path.join("sandbox", rel), name)
                exe = self._batch_file()
                seen, fake = self._record(text=False)
                real_run = subprocess.run
                subprocess.run = fake
                self.addCleanup(setattr, subprocess, "run", real_run)
                rc, out = mod.run_cmd([exe, "--version"], timeout=30)
                self.assertTrue(seen, "%s 的 run_cmd 没起子进程" % rel)
                self.assertEqual(seen[0][0], exe,
                                 "%s 的 run_cmd 把 .cmd 又交给了 shell（实际 argv=%r）—— "
                                 "cmd 会重解析引号，路径与参数都含空格时会 rc=1 且输出为空"
                                 % (rel, seen[0]))
                self.assertIn("--version", seen[0], "%s 没原样传参：%r" % (rel, seen[0]))
                self.assertEqual(rc, 0, "%s 的 run_cmd 没拿到 rc=0（拿到 %r）" % (rel, rc))
                self.assertIn("FAKE-VERSION", out, "%s 没把输出合并回来：%r" % (rel, out))


# ── L15：演练分支把失败清单丢了 —— 代码与《安装说明》那段临时 ⚠ 是一对（2026-09-23）──────
# 评审 L15（低）报的两件事都只在**随包分发**的文件里：
#   ① `sandbox/upgrade.py::main` 的演练支 `if not a.yes: … return 0` 把它自己刚攒下的
#      `fails` 丢了 —— 同一份失败清单，`--yes` 支判失败（rc=1）、演练支判成功（rc=0）；
#   ② `sandbox/uninstall.py` 五处步骤 docstring 的分母落后（写 `[1/5]`…`[5/5]`，而文件头
#      与步骤表是 `[x/6]`）。
#
# 这两处当初**都不能改**：它们随安装包分发，而 `verify_release.py` ⑧ 是「包内工具 ↔ 仓库源码」
# **逐字节**对拍；当时 `0.1.6-dev` 已经发过（GitHub Release + 内网镜像，sha256 公开）—— 同版本号、
# 不同内容的重打包正是第③条锁（`tests/test_release_artifact.py`）专门治过的事。
# ⇒ 已与下一次版本号变更**同批落地（2026-09-23，随 v0.1.7-dev）**，①的演练支改成读 `fails`
#   （有未完成项 → rc=1 并逐条打出来），②的六处分母改齐。活体判据（同一夹具、同一台机器，
#   只换脚本版本）：缺 `marketplace/` 的包演练 **before rc=0 → after rc=1**，且失败清单被列出来。
#
# 那本条锁现在锁什么？锁**代码与那段说明同真同假**（它是①的临时说明）：
# 说明写在 `docs/packaging/INSTALL.md` §5.1。代码若被改回"演练吞掉失败"，说明必须回来；
# 说明若还在而代码已经修好，它就是"文档在描述一个已经不存在的毛病"——正是本仓反复治的过期件。
# 判据取自 `upgrade.py` 的**语法树**（不是 grep 某一行字面）：只要那条分支不去读 `fails`，
# 它就不可能区分成功与失败 —— 这个结论对任何等价写法都成立。


def rehearsal_discards_fails(src):
    """`main()` 里有没有一条「演练就直接返回 0」的分支，且它**根本没读 `fails`**？

    返回 `True` / `False`；源码里找不到那条分支时返回 **`None`** —— 调用方必须显式处理，
    把它当 `False` 会让本锁在"函数被改名 / 分支被重构掉"时**静默通过**（那就成了空判据）。
    """
    found = None
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.If):
            continue
        t = node.test
        # 形状判据只认「Not + 某对象的 .yes 属性」——`a.yes` 这个名字可以换，形状不会
        if not (isinstance(t, ast.UnaryOp) and isinstance(t.op, ast.Not)
                and isinstance(t.operand, ast.Attribute) and t.operand.attr == "yes"):
            continue
        # 必须是一条**终止**分支：body 里直接 `return`（非终止的 `if not a.yes:` 不是它 ——
        # `main` 里那条"前置条件没给"的分支就只 print、不 return）。
        # 值刻意**不**限定成常量 0：修法可以是 `return 1 if fails else 0`（IfExp），
        # 也可以是 `if fails: … return 1` + `return 0` 两段 —— 只要读了 `fails` 就算修好。
        if not any(isinstance(s, ast.Return) for s in node.body):
            continue
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        found = "fails" not in names
    return found


# 那段临时说明的锚点：就是《安装说明》§5.1 里那一句。代码修好时就该连同整段一起消失
# （2026-09-23 实测：整段被换成"演练的退出码现在可信"那段，**这句哨兵已不在**）。
# ⚠ 它是个**否定**锚点（要求"不在"）—— 所以改《安装说明》时若把这句话原样搬回来，锁会红：
#   那不是误报，是说明又退回到"演练结果不可信"的口径了。
INSTALL_REHEARSAL_NOTE = "演练（不带 `--yes`）只告诉你"


class TestL15RehearsalExitCodeAndItsDocsNote(unittest.TestCase):
    """演练的退出码不可信这件事，代码与文档要么**都成立**、要么**都不成立**。"""

    def _text(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_the_code_and_the_install_note_agree(self):
        discards = rehearsal_discards_fails(self._text(os.path.join("sandbox", "upgrade.py")))
        self.assertIsNotNone(
            discards,
            "在 `sandbox/upgrade.py` 里找不到「演练就直接 return 0」那条分支了 —— "
            "是本锁的判据失效（形状改了），不是缺陷好了。先看 `main()` 尾部还在不在。")
        note = INSTALL_REHEARSAL_NOTE in self._text(os.path.join("docs", "packaging", "INSTALL.md"))
        if discards:
            self.assertTrue(
                note,
                "演练支仍然把 `fails` 丢掉（同一份失败清单，演练 rc=0、`--yes` rc=1），"
                "而《安装说明》§5.1 里那段 ⚠ 不见了 —— 同事会照着退出码判断演练结果。")
        else:
            self.assertFalse(
                note,
                "演练支已经不再丢 `fails` 了，但《安装说明》§5.1 里那段 ⚠ 还在 —— "
                "它已经变成「文档在描述一个不存在的毛病」。**把它整段删掉**（清单第⑦条）。")

    def test_the_detector_is_not_vacuous(self):
        """判据本体要能分辨两种写法 —— 拿合成源码喂它，不靠"现在恰好是哪种"。"""
        before = "def main(a):\n    if not a.yes:\n        print('预演')\n        return 0\n"
        after = ("def main(a):\n"
                 "    fails = []\n"
                 "    if not a.yes:\n"
                 "        print('预演')\n"
                 "        return 1 if fails else 0\n")
        self.assertIs(rehearsal_discards_fails(before), True,
                      "丢掉 fails 的写法没被判出来 —— 判据在空转")
        self.assertIs(rehearsal_discards_fails(after), False,
                      "已经读 fails 的写法被判成「仍在丢」 —— 那修完也不会变绿")
        self.assertIsNone(rehearsal_discards_fails("def main(a):\n    return 0\n"),
                          "找不到那条分支时应当返回 None（而不是 False）")


# ── L19：`bypass` 是「声明留痕入口」，不是权限闸（2026-09-23）────────────────────────
# 评审的定性建议本身没有缺陷可修（实现与 RUNBOOK 一致、从无虚假承诺），缺的只是**用法处那句定性**
# —— 而那句定性的缺席是有害的：把人引到「没声明就跑不了」的错觉上，而真相是「跑了没人知道」
# 才是原状（`--no-verify` 本来照样跑得通）。所以这条锁钉两件事：
#   ① 用法处真的说了这句（cli 输出 + README + RUNBOOK 三处，都是人照着做的地方）；
#   ② 它**真的只是**留痕：跑一次真 CLI，看它落了什么账、以及短理由会不会被拒。
# 第 ② 条不是重复 ①：一句"我不是权限闸"如果同一次调用里又开始拦提交，那句话就成了假话。


class TestL19BypassIsNotAPermissionGate(unittest.TestCase):
    """`bypass` 的定性要留在用法处，并且行为上真的只是留痕。"""

    def _run_bypass(self, reason):
        """真跑一次 CLI（子进程），把运维台账重定向到临时文件 —— 不脏本机 `reports/`。"""
        tmp = tempfile.mkdtemp(prefix="l19-")
        ops = os.path.join(tmp, "ops_log.jsonl")
        env = dict(os.environ, PYTHONUTF8="1", GUARDRAIL_OPS_LOG=ops)
        r = subprocess.run(
            [PY, os.path.join(ROOT, "guardrail_engine", "cli.py"), "bypass",
             "--reason", reason, "--repo", ROOT],
            cwd=ROOT, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180)
        return r.returncode, (r.stdout or "") + (r.stderr or ""), ops

    def test_it_says_so_where_people_read_it(self):
        """用法处三处都要有这句定性（少了任何一处，读的人就只看到"该声明"）。"""
        rc, out, _ops = self._run_bypass("L19 锁自检：紧急旁路的定性必须印在用法处")
        self.assertEqual(rc, 0, "旁路声明本身应当成功（rc=%s）：\n%s" % (rc, out))
        self.assertIn("不是权限闸", out,
                      "命令输出里没有那句定性 —— 有人会把它当成授权闸。")
        self.assertIn("任何人都能声明", out,
                      "没有说清「谁能声明」：这句话才是「它不是闸」的实际含义。")

    def test_it_really_only_ledgers_and_refuses_a_short_reason(self):
        """行为面：落了 `bypass_declared` 这条账；理由不足 10 字时**什么都不落**。"""
        long_reason = "L19 锁自检：这条够十个字了"
        rc, out, ops = self._run_bypass(long_reason)
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.isfile(ops), "旁路声明没有落运维台账：%s" % ops)
        with open(ops, encoding="utf-8") as fh:
            recs = [json.loads(x) for x in fh if x.strip()]
        self.assertEqual([r.get("op") for r in recs], ["bypass_declared"],
                         "台账里落的不是「声明」这条记录：%r" % recs)
        self.assertEqual(recs[0].get("result"), "declared", "台账记录了别的结论：%r" % recs[0])

        rc2, out2, ops2 = self._run_bypass("太短")
        self.assertEqual(rc2, 2, "理由不足 10 字应当被拒（rc=2），实际 rc=%s：\n%s" % (rc2, out2))
        self.assertIn("≥10 字", out2, "拒绝时没说清门槛：\n%s" % out2)
        self.assertFalse(os.path.isfile(ops2),
                         "被拒的那次不该留下任何账 —— 否则「没声明」与「声明了」就分不出来")

    def test_the_qualification_is_also_in_the_reader_facing_docs(self):
        """README 与 RUNBOOK 是人照着做事的地方，两处都点过名（cli docstring 里也写着这句的来源）。"""
        for rel in ("README.md", os.path.join("docs", "packaging", "RUNBOOK.md")):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                txt = fh.read()
            self.assertIn("不是权限闸", txt,
                          "%s 里没有那句定性 —— 那边是同事照着做事的地方" % rel)


class TestL6InfrastructureFailureIsNotABusinessVerdict(unittest.TestCase):
    """L6：超时 / 环境坏掉归 rc=2，不许冒充 1（1 的含义是「门禁拦截」）。"""

    def _gone(self, name):
        p = os.path.join(tempfile.gettempdir(), name)
        self.assertFalse(os.path.isdir(p), "夹具目录居然存在：%s" % p)
        return p

    def _as_a_file(self, name):
        """造一个**普通文件**（存在，但不是目录）—— 专门用来逼「坏 cwd」那一支。

        2026-09-24（这是 M92 在 CI 上变绿的根因）：`subprocess` 对坏 cwd 抛什么异常是
        **按平台选**的。传「不存在的目录」时 Windows 抛 `NotADirectoryError`，而
        **Linux 抛 `FileNotFoundError`** —— 后者会落进 `_git_out` 的**第一支**（"找不到 git"），
        第二支就再没有任何路径到达它。传「一个文件」时两平台都抛 `NotADirectoryError`
        ⇒ 这才是能跨平台打到第二支的逼出方式。
        """
        p = os.path.join(tempfile.gettempdir(), name)
        with open(p, "w", encoding="utf-8") as f:
            f.write("not a directory\n")
        self.addCleanup(lambda: os.path.exists(p) and os.remove(p))
        return p

    def test_a_timed_out_child_is_rc2_not_a_crash(self):
        cli = _load_cli()
        rc, out, err = cli._run([PY, "-c", "import time; time.sleep(30)"], timeout=1)
        self.assertNotEqual(1, rc,
                            "超时被归成了 1 —— 那是「门禁拦截」的码，会被读成「代码不合规」：%r" % err)
        self.assertEqual(2, rc, "超时该归 rc=2（配置/环境错误）：rc=%s err=%r" % (rc, err))
        self.assertIn("超时", err, "超时要**说出来**（只给个 rc=2 会让人以为门禁自己坏了）：%r" % err)

    def test_a_missing_cwd_is_rc2_not_a_crash(self):
        cli = _load_cli()
        rc, out, err = cli._run([PY, "-c", "print(1)"], cwd=self._gone("l6-no-such-dir"))
        self.assertEqual(2, rc, "cwd 不存在该归 rc=2：rc=%s err=%r" % (rc, err))
        self.assertIn("起不了子进程", err)

    def test_git_that_cannot_start_is_127_not_a_crash(self):
        """`_git_out` 的**两支**都要各有一条用例 —— 它们走的是不同的 except 分支。

        ★ 2026-09-24 订正（这条正是 M92 在 CI 上变绿的原因）：逼出方式必须**跨平台等价**。
        两平台实测 ——

            cwd = 不存在的目录 → Windows `NotADirectoryError`（第二支）
                                 Linux  **`FileNotFoundError`**（第一支）
            cwd = 一个文件     → Windows / Linux 都 `NotADirectoryError`（第二支）

        原先用的是前者，于是这条在 Linux 上跑的是**第一支**：被它守护的第二支没有任何路径
        到达得上 —— 注入把第二支的 `return 127` 改成 0，**本机 Windows 红、CI（Linux）全绿**。
        （更早那次「只写一条用例 ⇒ 全绿」是同一个病的另一张脸，只是那次连分支都还没分。）

        改用「文件当 cwd」，并**断言文案**：把"走的到底是哪一支"也变成判据 ——
        否则下次平台再漂移，这条只会静默变绿，而那恰恰是这类夹具唯一会留下的痕迹。
        """
        cli = _load_cli()
        as_file = self._as_a_file("l6-a-file-not-a-dir")
        rc, out, err = cli._git_out(["-C", as_file, "config", "--local", "--get", "core.hooksPath"],
                                    cwd=as_file)
        self.assertEqual(127, rc, "git 起不来该归 127（命令找不到的惯例）：rc=%s err=%r" % (rc, err))
        self.assertIn("起不来", err,
                      "这条用例该打到「git 起不来」那一支，err 里没有它说明**走岔了**"
                      "（平台异常映射一变，第一支就把第二支遮住）—— 夹具要跟着改：%r" % err)

    def test_git_absent_from_path_is_127_not_a_crash(self):
        """「机器上没装 git / 不在 PATH」—— 另一支。真把 PATH 清空跑一次，不靠打桩。

        `_git_out` 不传 `env=`，继承本进程环境 ⇒ 临时清空 PATH 就能走到
        `FileNotFoundError` 那一支。**同进程的其它用例**要保证拿回原 PATH（finally）。
        """
        cli = _load_cli()
        saved = os.environ.get("PATH")
        os.environ["PATH"] = ""
        try:
            rc, out, err = cli._git_out(["--version"])
        except FileNotFoundError:
            self.fail("没装 git 时把 `FileNotFoundError` 抛给了调用方 —— "
                      "调用方全是 hooks 的子命令，它们拿到的是裸回溯而不是「这说明什么」")
        finally:
            if saved is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = saved
        self.assertEqual(127, rc, "没装 git 该归 127：rc=%s err=%r" % (rc, err))
        self.assertIn("找不到 git", err, "要说清是「没装/不在 PATH」，不能只给个码：%r" % err)

    def test_hooks_install_refuses_a_missing_repo(self):
        """真起一次 CLI：`hooks install --repo <不存在>` → rc=2（**不是**回溯，也不是 1）。

        这条同时钉住「拒绝发生在起子进程**之前**」：`_record_op` 在 isdir 校验之后
        —— 所以这次拒绝不该往台账里落任何一行（夹具不脏真台账）。
        """
        gone = self._gone("l6-no-such-repo")
        r = subprocess.run([PY, CLI_PY, "hooks", "install", "--repo", gone],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(2, r.returncode, "该 rc=2：rc=%s out=%r" % (r.returncode, out[-400:]))
        self.assertIn("repo 不存在", out)


# ── L8：对宿主说的「单线程串行」必须与 `serve()` 的事实一致（2026-09-23）──────────────
MCP_PY = os.path.join(ROOT, "guardrail_engine", "mcp.py")
SERIAL_CLAIM_HOST = "SINGLE-THREADED and strictly serial"   # 宿主经 initialize 读到的那段
SERIAL_CLAIM_SELF = "单线程串行"                             # 模块 docstring（维护者读的那份）
_CONCURRENCY_ROOTS = ("threading", "asyncio", "concurrent", "multiprocessing")


def mcp_claims_serial(src):
    """源码里有没有那两句「串行」承诺（**宿主面** + **维护者面**，两处都要）。"""
    return SERIAL_CLAIM_HOST in src and SERIAL_CLAIM_SELF in src


def mcp_uses_concurrency(src):
    """真的用上了并发原语吗？—— 按 **import 与调用**判，不按注释里的字面词判。

    这正是本条判据的难点：`mcp.py` 里到处写着「单线程串行」「不许上线程」这些**字**（注释），
    拿字面 grep 判会永远为真。所以只看 AST 里的 import / 属性访问。
    """
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] in _CONCURRENCY_ROOTS for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in _CONCURRENCY_ROOTS:
                return True
        elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id in _CONCURRENCY_ROOTS):
            return True
    return False


class TestL8SerialClaimAndCodeAgree(unittest.TestCase):
    """L8：MCP 壳是**单线程串行**的，一次长调用会把宿主的 ping 一起堵在队列里。

    这条不是「要么改代码要么改文档」，而是**承诺与事实必须同时为真或同时为假**：
      · 代码串行、承诺被删掉 → 红（宿主把「排队」读成「服务死了」，而没人告诉它为什么）；
      · 代码并发化、承诺还留着 → 红（文档成了过期件，而它会让人**不去查**真因）；
      · 代码并发化**且**承诺改成并发描述 → 绿（那才是那次改动的完整形态）。
    真并发化还要连带证明「`_send` 不交错」「两个 gate 同跑不打架（台账 append / doctor.sh
    的副作用）」—— 本壳只有 stdio 一条通道、没有排队与取消机制，这不是一条文档锁能担保的事。
    """

    def setUp(self):
        with open(MCP_PY, encoding="utf-8") as fh:
            self.src = fh.read()

    def test_the_claim_and_the_code_are_same_true_same_false(self):
        claims, uses = mcp_claims_serial(self.src), mcp_uses_concurrency(self.src)
        self.assertEqual(
            claims, not uses,
            "「串行」的承诺与代码事实不一致：承诺=%s / 用并发原语=%s。\n"
            "  · 若刚把服务改成并发：请把 `INSTRUCTIONS` 里那句 %r 与模块 docstring 里的 %r\n"
            "    一起改成并发描述（并补上「`_send` 不交错」「两个 gate 同跑不打架」的验证）；\n"
            "  · 若刚删掉那句承诺：宿主就只能把「排队」读成「服务死了」—— 请恢复它。"
            % (claims, uses, SERIAL_CLAIM_HOST, SERIAL_CLAIM_SELF))

    def test_the_host_facing_copy_is_the_one_that_counts(self):
        """承诺必须出现在**宿主读得到**的那一段（`INSTRUCTIONS`），不能只写在模块 docstring 里。

        模块 docstring 是维护者读的；宿主只经 `initialize` 拿 `INSTRUCTIONS`。
        只写 docstring 等于没告诉当事人。
        """
        sys.path.insert(0, ROOT)
        from guardrail_engine import mcp
        self.assertIn(SERIAL_CLAIM_HOST, mcp.INSTRUCTIONS,
                      "宿主面的 `INSTRUCTIONS` 里没有串行说明")
        self.assertIn("harness_doctor", mcp.INSTRUCTIONS,
                      "要说清**哪两个工具**是长的 —— 否则等于没说清什么时候会卡")

    def test_the_detector_is_not_vacuous(self):
        """判据自检：真并发认得出来，注释里的字面词不会误认。"""
        self.assertTrue(mcp_uses_concurrency("import threading\nx = threading.Thread()\n"))
        self.assertTrue(mcp_uses_concurrency("import asyncio\n"))
        self.assertTrue(mcp_uses_concurrency("from concurrent.futures import ThreadPoolExecutor\n"))
        self.assertFalse(mcp_uses_concurrency(
            "# 我们**不**用 threading / asyncio；注释里写着「并发」两个字也只是在解释为什么不\n"
            "def serve():\n    pass\n"))

    def test_the_real_file_is_serial_today(self):
        """今天的事实：代码侧确实没有并发原语（否则上面那条等式会在**另一个方向**上失效）。"""
        self.assertFalse(mcp_uses_concurrency(self.src),
                         "`mcp.py` 里出现了并发原语 —— 上面那条等式锁现在是在为一份过期文档背书")


# ── L12：三处「旧契约」文档必须已被标作废（别照它验收）（2026-09-23）──────────────────
TRAE_README = os.path.join(ROOT, "integrations", "trae-cn", "README.md")
TRAE_EXAMPLE = os.path.join(ROOT, "integrations", "trae-cn", "hooks.example.json")
QODER_README = os.path.join(ROOT, "integrations", "qoder-cn", "README.md")
QODER_EXAMPLE = os.path.join(ROOT, "integrations", "qoder-cn", "settings.example.json")
VERIFY_SH = os.path.join(ROOT, "sandbox", "verify_integrations.sh")


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def doc_hook_commands(md_text):
    """从 README 的 ```json 代码块里抠出 {事件: [命令...]}（取**第一个**带 `hooks` 的块）。"""
    for m in re.finditer(r"```json\s*\n(.*?)```", md_text, re.S):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if not isinstance(d, dict) or "hooks" not in d:
            continue
        return {ev: [h["command"] for it in items for h in it.get("hooks", [])]
                for ev, items in d["hooks"].items()}
    return None


def example_hook_commands(path):
    d = json.loads(read_text(path))
    return {ev: [h["command"] for it in items for h in it.get("hooks", [])]
            for ev, items in d["hooks"].items()}


def integration_client_classes(sh_text):
    """从脚本里**分别按注释、按代码**读出「谁算强制型」—— 就是为了让两者能对拍。

    代码侧：`check_one "$t" yes|no` 那几行的 `case` 模式（`claude|opencode|codex)` 这种）。
    注释侧：文件头 `强制型：…` / `自觉型：…` 两段（**先剥掉括号里的解释**再取词，
    否则 `codex(hooks.json+信任)` 里那个 `json+信任` 会变成假客户端）。
    """
    hard, soft = set(), set()
    for m in re.finditer(r'^\s*([a-z][a-z0-9_-]*(?:\|[a-z][a-z0-9_-]*)*)\)\s+'
                         r'check_one\s+"\$t"\s+(yes|no)', sh_text, re.M):
        (hard if m.group(2) == "yes" else soft).update(m.group(1).split("|"))
    lines = sh_text.split("\nset -u", 1)[0].split("\n")
    notes = {}
    for label, key in (("强制型", "hard"), ("自觉型", "soft")):
        notes[key] = None
        for i, ln in enumerate(lines):
            m = re.match(r"^(\s*)#\s*%s：(.*)$" % label, ln)
            if not m:
                continue
            # 续行 = 紧随其后、`#` 缩进**比标签行更深**、且自己不带 `：`（带 `：` 的是下一段说明）。
            # ★ 这条缩进规则是被真实文件逼出来的：`自觉型：` 下面紧跟一行**同缩进**的
            #   `★ 2026-09-23 更正（…）`，而它正文里含着 `codebuddy(无原生 hook…)` ——
            #   按「读到下一个 `：` 为止」抠，会把那条更正并进分类名单，凭空多出一个客户端。
            # 量的是 `#` **之后**的空白宽度（`#` 本身都在行首，量它前面的缩进恒为 0）。
            sp = len(re.match(r"^\s*#(\s*)", ln).group(1))
            parts = [m.group(2)]
            for nxt in lines[i + 1:]:
                m2 = re.match(r"^\s*#(\s+)(.*)$", nxt)
                if not m2 or len(m2.group(1)) <= sp or "：" in m2.group(2):
                    break
                parts.append(m2.group(2))
            txt = re.sub(r"（[^）]*）|\([^)]*\)", " ", " ".join(parts))   # 剥掉解释性括号
            notes[key] = set(re.findall(r"[a-z][a-z0-9_-]+", txt))
            break
    return hard, soft, notes


class TestL12StaleContractsAreMarked(unittest.TestCase):
    """L12：三处文档还在讲**已被实测推翻**的旧契约 —— 照它验收会把「改对了」判成「不符合预期」。

    本锁不去 grep「作废」两个字（那种锁一改措辞就断），而是把文档**与它的正本对拍**：
      · README 里那段「可直接抄」的 JSON 必须**逐字等于**出货的样例文件；
      · `verify_integrations.sh` 头注释里的客户端分类必须**等于**下面 for 循环里的 yes/no。
    第二项正是原缺陷的形态：注释说 codebuddy「未跑只标 ⚠」、代码早把它归成强制型，
    **说反了两个月** —— 而写在注释里的那句「两处一起改」本身拦不住任何人。
    """

    def test_trae_readme_block_equals_the_shipped_example(self):
        doc = doc_hook_commands(read_text(TRAE_README))
        self.assertIsNotNone(doc, "Trae README 里找不到带 hooks 的 ```json 块（被改成散文了？）")
        self.assertEqual(example_hook_commands(TRAE_EXAMPLE), doc,
                         "Trae README 那段「可直接抄」的配置与出货样例不一致 —— 照它抄会踩"
                         " §5.6 记着的那两个坑（引号被 PowerShell 拼崩 / rc 被吞成 1）")

    def test_qoder_readme_block_equals_the_shipped_example(self):
        doc = doc_hook_commands(read_text(QODER_README))
        self.assertIsNotNone(doc, "Qoder README 里找不到带 hooks 的 ```json 块")
        self.assertEqual(example_hook_commands(QODER_EXAMPLE), doc,
                         "Qoder README 那段配置与出货样例不一致（§5.4/§7 已改判，别退回旧口径）")

    def test_verify_integrations_header_agrees_with_its_own_loop(self):
        hard, soft, notes = integration_client_classes(read_text(VERIFY_SH))
        self.assertTrue(hard, "没能从 for 循环里读出任何强制型客户端（解析器坏了？）")
        self.assertIsNotNone(notes["hard"], "头部没有 `强制型：` 那一段分类说明")
        self.assertIsNotNone(notes["soft"], "头部没有 `自觉型：` 那一段分类说明")
        self.assertEqual(hard, notes["hard"],
                         "头部「强制型」名单与循环里的 yes 不一致（原缺陷就是这个形态：注释说"
                         " codebuddy 未跑、代码早按强制型真跑）—— 两处要一起改")
        self.assertEqual(soft, notes["soft"], "头部「自觉型」名单与循环里的 no 不一致")

    def test_the_client_parser_is_not_vacuous(self):
        """判据自检：两侧都认得出，且括号里的解释不会被当成人名。"""
        sample = (
            "#!/bin/sh\n"
            "#   强制型：claude(PostToolUse 钩子) / codex(hooks.json+信任) / codebuddy\n"
            "#   自觉型：**当前为空**（机制保留：按 --hard no 走「只标 ⚠」）\n"
            "\nset -u\n"
            'for t in $TOOLS; do\n'
            '  case "$t" in\n'
            '    claude|codex) check_one "$t" yes ;;\n'
            '    codebuddy)     check_one "$t" yes ;;\n'
            '  esac\n'
            'done\n')
        hard, soft, notes = integration_client_classes(sample)
        self.assertEqual({"claude", "codex", "codebuddy"}, hard)
        self.assertEqual(set(), soft)
        self.assertEqual({"claude", "codex", "codebuddy"}, notes["hard"],
                         "括号里的 `hooks.json+信任` / `PostToolUse` 不该被当成人名")
        self.assertEqual(set(), notes["soft"], "「机制保留：按 --hard no 走…」里的词不该漏出来")


if __name__ == "__main__":
    unittest.main()

