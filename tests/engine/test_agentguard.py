# -*- coding: utf-8 -*-
"""agentguard 打包壳测试：MCP 协议 / 工具行为 / 三入口一致性（2026-09-14）

覆盖（对应打包 Goal Brief 判据）：
- MCP 协议层：initialize 版本协商、tools/list 六工具、stdout 协议纯净（逐行 JSON-RPC）
- MCP 工具行为：harness_run_gate 拦/放、harness_check_file 自动匹配 gate、
  未知 gate → CONFIG_ERROR（isError=True）、未知工具 → JSON-RPC error
- 三入口一致性：同一夹具上 CLI 与 MCP 结论一致（1/1、0/0）；git 提交钩子拦截 + 卸载回滚（真提交）
- 台账：MCP 入口落账 entry='mcp'，ver 与 VERSION 一致
- 版本单一来源（A3/N1）：**轮子安装形态**（包内无 VERSION 文件）下
  `__version__` / `cli._version()` / `mcp.VERSION` 三处必须报同一个版本

沙箱安全约定：夹具用 tempfile.mkdtemp 生成并保留（本文件不调用任何删除类 API）。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MCP = os.path.join(ROOT, "guardrail_engine", "mcp.py")
CLI = os.path.join(ROOT, "guardrail_engine", "cli.py")
AUTO = os.path.join(ROOT, "harness", "agents", "auto-gate.py")
PY = sys.executable

TOOL_NAMES = {
    "harness_list_gates", "harness_run_gate", "harness_check_file",
    "harness_summary", "harness_coverage", "harness_doctor",
}


def _version():
    try:
        with io.open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _run(args, env=None, cwd=None, timeout=240):
    e = dict(os.environ)
    e.setdefault("PYTHONUTF8", "1")
    if env:
        e.update(env)
    p = subprocess.run(args, cwd=cwd or ROOT, env=e, capture_output=True, timeout=timeout)
    return p.returncode, (p.stdout or b"").decode("utf-8", "replace"), (p.stderr or b"").decode("utf-8", "replace")


# 快跑（HARNESS_FAST=1，2026-09-17 接线）：本文件的慢在**起子进程真跑 MCP / CLI / 钩子**
# （实测墙钟 25.8s，11 例里 6 例 ≥1s，合计 23.5s：钩子入口 11.4 / 三入口一致 5.0 /
# MCP 工具 2.4+1.6+1.3 / 台账 1.9）。
# **不清空**：TestMcpProtocol（3 例共 1.1s，协议握手/取工具单，最慢 0.57s）与
# TestVersionSingleSourceInWheelForm（0.47s）照跑 —— 后者正是变异冒烟 M13/M14
# （版本分叉 / 集体退化）的判据所在（见 sandbox/mutation_smoke.py），
# 接掉它等于把那两条变异在快跑档里变成没人守。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


def _mk_fixture():
    d = tempfile.mkdtemp(prefix="egtest-")
    repo = os.path.join(d, "repo")
    os.makedirs(os.path.join(repo, "src"))
    _run(["git", "init", "-q"], cwd=repo)
    _run(["git", "config", "user.email", "t@t"], cwd=repo)
    _run(["git", "config", "user.name", "t"], cwd=repo)
    cfg = os.path.join(d, "cfg.yaml")
    with io.open(cfg, "w", encoding="utf-8", newline="") as f:
        f.write("repos:\n  t: %s\ngates:\n  t:\n    enabled: true\n    repo: t\n"
                "    script: checks/example_code_check.py\n    run_on: [pre_commit, post_edit]\n"
                % repo.replace("\\", "/"))
    log = os.path.join(d, "gate_log.jsonl")
    tpl = "<template>\r\n  <div>h</div>\r\n</template>\r\n<script setup>\r\n%s\r\n</script>\r\n"
    bad = os.path.join(repo, "src", "bad.vue")
    good = os.path.join(repo, "src", "good.vue")
    with io.open(bad, "w", encoding="utf-8", newline="") as f:
        f.write(tpl % "console.log(1);")
    with io.open(good, "w", encoding="utf-8", newline="") as f:
        f.write(tpl % "const a = 1;")
    env = dict(HARNESS_CONFIG=cfg, HARNESS_LOG_FILE=log, PYTHONUTF8="1")
    return d, repo, cfg, log, bad, good, env


class _McpSession(object):
    """极简 MCP stdio 会话。"""

    def __init__(self, env=None):
        e = dict(os.environ)
        e.setdefault("PYTHONUTF8", "1")
        if env:
            e.update(env)
        self.lines = []
        self.p = subprocess.Popen([PY, MCP], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=e, cwd=ROOT)
        self.mid = 0

    def req(self, method, params=None, timeout=120):
        self.mid += 1
        msg = {"jsonrpc": "2.0", "id": self.mid, "method": method, "params": params or {}}
        self.p.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.p.stdin.flush()
        box = {}

        def rd():
            box["line"] = self.p.stdout.readline()

        t = threading.Thread(target=rd, daemon=True)
        t.start()
        t.join(timeout)
        assert not t.is_alive(), "MCP 响应超时：%s" % method
        line = box["line"]
        self.lines.append(line)
        return json.loads(line.decode("utf-8"))

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        self.p.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.p.stdin.flush()

    def close(self):
        try:
            self.p.stdin.close()
        except OSError:
            pass
        try:
            self.p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.p.kill()
        for f in (self.p.stdout, self.p.stderr):
            try:
                if f:
                    f.close()
            except OSError:
                pass

    def call_tool(self, name, arguments):
        r = self.req("tools/call", {"name": name, "arguments": arguments})
        return r["result"]["content"][0]["text"], r["result"].get("isError")


class TestMcpProtocol(unittest.TestCase):
    def test_initialize_negotiates_version(self):
        s = _McpSession()
        try:
            r = s.req("initialize", {"protocolVersion": "2025-03-26"})
            res = r["result"]
            self.assertEqual(res["protocolVersion"], "2025-03-26")
            self.assertEqual(res["serverInfo"]["name"], "guardrail-engine")
            self.assertIn("tools", res.get("capabilities", {}))
            r2 = s.req("initialize", {"protocolVersion": "1999-01-01"})
            self.assertEqual(r2["result"]["protocolVersion"], "2025-06-18")  # 不支持则回退首选版本
        finally:
            s.close()

    def test_tools_list_six_tools_with_descriptions(self):
        s = _McpSession()
        try:
            s.req("initialize", {"protocolVersion": "2025-06-18"})
            s.notify("notifications/initialized")
            r = s.req("tools/list")
            names = {t["name"] for t in r["result"]["tools"]}
            self.assertEqual(names, TOOL_NAMES)
            for t in r["result"]["tools"]:
                self.assertGreaterEqual(len(t.get("description", "")), 40, t["name"])
                self.assertEqual(t["inputSchema"]["type"], "object")
        finally:
            s.close()

    def test_stdout_is_pure_jsonrpc(self):
        s = _McpSession()
        try:
            s.req("initialize", {"protocolVersion": "2025-06-18"})
            s.notify("notifications/initialized")
            s.req("tools/list")
            text, is_err = s.call_tool("harness_list_gates", {})
            self.assertFalse(is_err)
            for line in s.lines:
                obj = json.loads(line.decode("utf-8"))  # stdout 不纯时此处直接失败
                self.assertEqual(obj["jsonrpc"], "2.0")
        finally:
            s.close()


@_slow("每次 setUpClass 现建 git 工程仓 + 起 MCP 子进程（每例 1.3~2.4s）")
class TestMcpTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.repo, cls.cfg, cls.log, cls.bad, cls.good, cls.env = _mk_fixture()

    def test_run_gate_blocked_and_pass(self):
        s = _McpSession(self.env)
        try:
            s.req("initialize", {"protocolVersion": "2025-06-18"})
            s.notify("notifications/initialized")
            t_bad, e_bad = s.call_tool("harness_run_gate", {"gate": "t", "files": [self.bad]})
            self.assertIn("rc=1", t_bad)
            self.assertIn("BLOCKED", t_bad)
            self.assertFalse(e_bad)
            t_good, e_good = s.call_tool("harness_run_gate", {"gate": "t", "files": [self.good]})
            self.assertIn("rc=0", t_good)
            self.assertFalse(e_good)
        finally:
            s.close()

    def test_check_file_auto_matches_gate(self):
        s = _McpSession(self.env)
        try:
            s.req("initialize", {"protocolVersion": "2025-06-18"})
            t, e = s.call_tool("harness_check_file", {"path": self.bad})
            self.assertIn("(gate=t)", t)
            self.assertIn("rc=1", t)
            self.assertFalse(e)
        finally:
            s.close()

    def test_config_error_when_gate_repo_missing(self):
        d, repo, cfg, log, bad, good, env = _mk_fixture()
        cfg2 = os.path.join(d, "cfg-repo-missing.yaml")
        with io.open(cfg2, "w", encoding="utf-8", newline="") as f:
            f.write("repos:\n  t: %s\ngates:\n  t:\n    enabled: true\n    repo: t\n"
                    "    script: checks/example_code_check.py\n    run_on: [pre_commit]\n"
                    % os.path.join(d, "no_such_repo").replace("\\", "/"))
        env2 = dict(env)
        env2["HARNESS_CONFIG"] = cfg2
        s = _McpSession(env2)
        try:
            s.req("initialize", {"protocolVersion": "2025-06-18"})
            t, e = s.call_tool("harness_run_gate", {"gate": "t", "files": [bad]})
            self.assertIn("rc=2", t)
            self.assertIn("CONFIG_ERROR", t)
            self.assertTrue(e)
        finally:
            s.close()

    def test_unknown_tool_rpc_error(self):
        s = _McpSession()
        try:
            s.req("initialize", {"protocolVersion": "2025-06-18"})
            r = s.req("tools/call", {"name": "nope", "arguments": {}})
            self.assertEqual(r["error"]["code"], -32602)
        finally:
            s.close()


@_slow("每例现建 git 工程仓 + 起 MCP 子进程（1.9s）")
class TestLedger(unittest.TestCase):
    def test_mcp_entry_recorded_with_version(self):
        d, repo, cfg, log, bad, good, env = _mk_fixture()
        s = _McpSession(env)
        try:
            s.req("initialize", {"protocolVersion": "2025-06-18"})
            s.call_tool("harness_run_gate", {"gate": "t", "files": [bad]})
        finally:
            s.close()
        recs = []
        if os.path.isfile(log):
            with io.open(log, encoding="utf-8") as f:
                recs = [json.loads(l) for l in f.read().splitlines() if l.strip()]
        self.assertTrue(recs, "MCP 调用后应产生台账记录")
        self.assertTrue(all(r.get("entry") == "mcp" for r in recs))
        v = _version()
        if v:
            self.assertTrue(all(r.get("ver") == v for r in recs))


@_slow("每例现建 git 工程仓 + 真跑 CLI 与 MCP 两个入口（5.0s）")
class TestThreeEntryConsistency(unittest.TestCase):
    def test_cli_and_mcp_agree(self):
        d, repo, cfg, log, bad, good, env = _mk_fixture()
        rcb, ob, eb = _run([PY, CLI, "run", "t", bad], env=env)
        rcg, og, eg = _run([PY, CLI, "run", "t", good], env=env)
        s = _McpSession(env)
        try:
            s.req("initialize", {"protocolVersion": "2025-06-18"})
            tb, _ = s.call_tool("harness_run_gate", {"gate": "t", "files": [bad]})
            tg, _ = s.call_tool("harness_run_gate", {"gate": "t", "files": [good]})
        finally:
            s.close()
        mrb = 1 if ("rc=1" in tb and "BLOCKED" in tb) else -1
        mrg = 0 if "rc=0" in tg else -1
        self.assertEqual(rcb, 1)
        self.assertEqual(rcg, 0)
        self.assertEqual((rcb, rcg), (mrb, mrg))


@_slow("每例真装钩子 + 真 git 提交（11.4s，本文件最贵的一条）")
class TestHookEntry(unittest.TestCase):
    def test_hook_blocks_then_uninstall_restores(self):
        d, repo, cfg, log, bad, good, env = _mk_fixture()
        rc, out, err = _run([PY, CLI, "hooks", "install", "--repo", repo], env=env)
        self.assertEqual(rc, 0, err)
        _run(["git", "add", "src/bad.vue"], cwd=repo)
        rc1, o1, e1 = _run(["git", "commit", "-m", "try bad"], cwd=repo, env=env)
        self.assertNotEqual(rc1, 0, "违规提交应被提交钩子拦截")
        _run(["git", "reset", "-q"], cwd=repo)
        rc2, o2, e2 = _run([PY, CLI, "hooks", "uninstall", "--repo", repo], env=env)
        self.assertEqual(rc2, 0, e2)
        rcs, outs, _ = _run([PY, CLI, "hooks", "status", "--repo", repo], env=env)
        self.assertIn("未设置", outs)
        _run(["git", "add", "src/bad.vue"], cwd=repo)
        rc3, o3, e3 = _run(["git", "commit", "-m", "after uninstall"], cwd=repo, env=env)
        self.assertEqual(rc3, 0, "卸载后提交应恢复（不再受门禁影响）")


class TestFlagLikePredicate(unittest.TestCase):
    """H1 的判据本体（**纯函数、不起子进程** —— 快跑档也跑，见 HARNESS_FAST）。

    三个入口（MCP / CLI / 编辑期钩子）共用同一份前缀判据：`files` / `--only` 是位置参数通道，
    而以 `-` 开头的东西会被 `run_gate` 当**自己的**旗标消费（`--mode loose` 实测把一次拦截
    翻成放行）。只认前缀、**不做 `os.path.isfile`** —— `files` 的契约含"相对 gate 仓的路径"，
    查存在性会误拒合法相对路径；而真路径无论相对绝对都不以 `-` 开头。
    """

    def setUp(self):
        sys.path.insert(0, ROOT)
        import guardrail_engine._shell as _shell
        self.shell = _shell
        self.addCleanup(sys.path.remove, ROOT)

    def test_picks_only_dash_prefixed_entries(self):
        self.assertEqual(self.shell.flag_like(["--mode", "loose", "src/a.vue"]), ["--mode"])
        self.assertEqual(self.shell.flag_like(["-"]), ["-"])
        self.assertEqual(self.shell.flag_like(["--"]), ["--"])

    def test_leaves_legitimate_paths_alone(self):
        """反向锁：正常路径（含相对路径）一律不许被误判。"""
        for ok in ("src/a.vue", "./src/a.vue", "C:/x/src/a.vue", "C:\\x\\src\\a.vue",
                   "src/my-file.vue", "src/my-file-2.vue"):
            self.assertEqual(self.shell.flag_like([ok]), [], "合法路径被当成旗标：%r" % ok)
        self.assertEqual(self.shell.flag_like(["src/-weird.vue"]), [],
                         "只有**开头**的 `-` 才算旗标（`src/-x.vue` 是能存在的真文件名）")

    def test_empty_and_none_are_safe(self):
        """MCP 可能不传 files（`all`/`repo` 形态）；None/空列表不许炸。"""
        self.assertEqual(self.shell.flag_like(None), [])
        self.assertEqual(self.shell.flag_like([]), [])


@_slow("每例现建 git 工程仓 + 起 CLI/MCP/auto-gate 子进程（约 7s）")
class TestFlagSmugglingClosedAtEveryEntry(unittest.TestCase):
    """H1 回归锁（2026-09-23 评审报告）：**位置参数通道不许被当成旗标通道**。

    现场（2026-09-23 评审报告，当时的 PC/H5 规则集读 mode）：`run_gate.py t <文件> --mode loose`
    可把一次拦截（rc=1）翻成放行（rc=0），而两个壳（MCP / CLI）是把调用方给的 `files`
    **原样拼在 gate 名之后**的 —— 于是一个内容为 `--mode` 的"文件名"就能改判定口径；
    `auto-gate` 的 `--only` 同理（它的值来自**编辑器宿主的 payload**，是外部输入）。
    与 2026-09-16 修掉的 `--gate` 冒充同类（"被检查方可以自己把门禁放宽，对门禁是硬伤"）。

    开源裁剪后只剩 example 规则（不区分 strict/loose），"判定被翻动"的现场已无规则可复现；
    本类因此钉两件事：**三个入口必须拒绝旗标形态的位置参数**，以及引擎侧对口径覆盖**必须留痕**。
    """

    @classmethod
    def setUpClass(cls):
        cls.d, cls.repo, cls.cfg, cls.log, cls.bad, cls.good, cls.env = _mk_fixture()

    def _ledger(self):
        if not os.path.isfile(self.log):
            return []
        with io.open(self.log, encoding="utf-8") as f:
            return [json.loads(l) for l in f.read().splitlines() if l.strip()]

    # ---------------- CLI ----------------
    def test_cli_rejects_flag_in_positionals(self):
        """`run t -- --mode loose <文件>`：`--` 之后全归位置参数 → 必须 fail-closed。"""
        before = len(self._ledger())
        rc, out, err = _run([PY, CLI, "run", "t", "--", "--mode", "loose", self.bad], env=self.env)
        self.assertEqual(rc, 2, "旗标形态的位置参数必须 rc=2（配置/用法错），实得 %s\n%s%s" % (rc, out, err))
        self.assertIn("旗标", out)
        self.assertIn("--mode", out, "错误文案要逐字打出**是哪一项**不合法")
        self.assertEqual(len(self._ledger()), before,
                         "拒绝时不许已经把门禁跑过一遍 —— 那等于换个口径的真判定")

    def test_cli_still_takes_an_honest_file(self):
        """反向锁：真文件照常走（别把整条路封死）。"""
        rc, out, _ = _run([PY, CLI, "run", "t", self.bad], env=self.env)
        self.assertEqual(rc, 1, out)

    def test_cli_real_mode_flag_still_works(self):
        """反向锁：**真旗标** `--mode`（argparse 单列的那个）不许被这条检查打架打掉。"""
        rc, out, _ = _run([PY, CLI, "run", "t", self.good, "--mode", "loose"], env=self.env)
        self.assertEqual(rc, 0, out)

    # ---------------- MCP ----------------
    def test_mcp_rejects_flag_in_files(self):
        s = _McpSession(self.env)
        try:
            s.req("initialize", {"protocolVersion": "2025-06-18"})
            t, e = s.call_tool("harness_run_gate", {"gate": "t", "files": ["--mode", "loose", self.bad]})
            self.assertTrue(e, "MCP 侧必须报错（isError），不能静默照跑：%s" % t)
            self.assertIn("invalid `files` entry", t)
            self.assertIn("--mode", t)
            # 控制组：同一会话里正常用法仍然是 rc=1（证明上一条不是"会话坏了"导致的假红）
            t2, e2 = s.call_tool("harness_run_gate", {"gate": "t", "files": [self.bad]})
            self.assertFalse(e2)
            self.assertIn("rc=1", t2)
        finally:
            s.close()

    # ---------------- 编辑期钩子（auto-gate --only） ----------------
    def test_auto_gate_rejects_flag_in_only(self):
        rc, out, err = _run([PY, AUTO, "--repo", self.repo, "--only", "--mode"], env=self.env)
        self.assertEqual(rc, 2, "`--only --mode` 必须 fail-closed，实得 rc=%s\n%s%s" % (rc, out, err))
        self.assertIn("旗标形态", err)

    def test_auto_gate_still_takes_an_honest_only(self):
        rc, out, _ = _run([PY, AUTO, "--repo", self.repo, "--only", self.bad], env=self.env)
        self.assertEqual(rc, 1, out)

    # ---------------- 引擎侧：跑掉了也要留痕（H1 的后半） ----------------
    def test_mode_override_is_recorded_in_the_ledger(self):
        """口径被覆盖时**必须留痕**（`mode_override` + `weakened`）。

        开源裁剪后的 example 规则不区分 strict/loose，所以这里不断言"判定被翻动"
        （两跑都应是 rc=1）—— 只钉引擎侧的兜底：调用方改了口径这件事本身要逐字落账，
        否则那一行与"真检查过且通过"不可区分（评审报告的原话）。
        """
        before = len(self._ledger())
        rc1, o1, _ = _run([PY, os.path.join(ROOT, "harness", "run_gate.py"), "t", self.bad],
                          env=self.env)
        self.assertEqual(rc1, 1, "基线：bad.vue 应被 example 规则拦截（不拦这条锁就没意义）\n%s" % o1)
        rc2, o2, _ = _run([PY, os.path.join(ROOT, "harness", "run_gate.py"), "t", self.bad,
                           "--mode", "loose"], env=self.env)
        self.assertEqual(rc2, 1,
                         "example 规则不读 mode，判定不该变；若这里变了说明新规则开始读 mode\n%s" % o2)
        rows = self._ledger()[before:]
        self.assertEqual(len(rows), 2, rows)
        self.assertNotIn("mode_override", rows[0], "没覆盖就不许落这个键（否则标记没有信息量）")
        self.assertNotIn("weakened", rows[0])
        self.assertEqual(rows[1].get("mode_override"), "loose(config=strict)",
                         "口径被覆盖必须逐字落进台账：%s" % rows[1])
        self.assertIn("mode_override:loose(config=strict)", rows[1].get("weakened") or [],
                      "放宽方向还要进 weakened（'谁把它放宽了'）：%s" % rows[1])

    def test_same_mode_is_not_an_override(self):
        """反向锁：`--mode` 与 gate 配置同值 → **不算覆盖**，不落键（别让每行都挂标记）。"""
        before = len(self._ledger())
        rc, o, _ = _run([PY, os.path.join(ROOT, "harness", "run_gate.py"), "t", self.bad,
                         "--mode", "strict"], env=self.env)
        self.assertEqual(rc, 1, o)
        rows = self._ledger()[before:]
        self.assertEqual(len(rows), 1, rows)
        self.assertNotIn("mode_override", rows[0])


@_slow("每例现建 git 工程仓并起 run_gate 子进程真跑（约 6s）")
class TestBareGateCallIsRefused(unittest.TestCase):
    """L17 回归锁（2026-09-23 评审报告）：**裸调用不许伪装成定向检查**。

    现场（本类逐条跑一遍，不引用报告结论）：检查脚本的约定是「无参 = 全量」，所以
    `run_gate <gate>`（无文件、无 `--all`）会**扫一整仓**（台账里那两条 21 分钟记录就是这个
    形态），却记成 `scope=targeted, files=0` —— 台账那一行看着像"只查了 0 个文件的定向检查"。
    这与本项目"可审计"的承诺直接冲突：**慢只是代价，记错才是伤**。故 fail-closed（rc=2）。
    ★ 判据的另一半是"**什么才算判过**"：rc=2 属于"判都没判"（同未登记 gate、配置读不出来两条
    既有前置），所以**不落台账**；本类把这个不落账也钉住 —— 否则下次改成"先记一行再说"，
    台账里就会多出一批 rc=2 的幽灵记录，而 L17 治的正是台账说谎。

    两向都要在（本仓规矩：锁有两向、其中一向恒绿不算证据）：
      · 该拒的拒：裸调用 / 只带 `--mode` → rc=2，不进子进程（`[run_gate] <gate>: ` 这行是
        起子进程**之前**那句 print，用它的有无当"到底跑没跑"的判据），台账零新增；
      · 该放的放：显式 `--all`、真文件参数一律照旧 —— 并且台账的 `scope` 必须与实际跑法**同源**
        （这正是 L17 的病根：口径由分支链各自写，`--all --mode loose` 那条分支漏写，
        真全量 + 记 targeted；2026-09-23 实测）。

    夹具取本文件既有的 `_mk_fixture()`（含一份两文件的小仓 / 一个 gate `t` / 独立台账路径），
    不另造一套：裸调用在该夹具上的代价就是"扫 2 个文件"，真退化了也看不出来 —— 所以本类的
    判据不落在耗时上，落在**台账那一行**与"进没进子进程"上。
    """

    @classmethod
    def setUpClass(cls):
        cls.d, cls.repo, cls.cfg, cls.log, cls.bad, cls.good, cls.env = _mk_fixture()

    def _ledger(self):
        if not os.path.isfile(self.log):
            return []
        with io.open(self.log, encoding="utf-8") as f:
            return [json.loads(l) for l in f.read().splitlines() if l.strip()]

    def _gate(self, *args):
        """跑一次 run_gate，返回 (rc, out, err, 本次新增的台账行)。"""
        before = len(self._ledger())
        rc, out, err = _run([PY, os.path.join(ROOT, "harness", "run_gate.py"), "t"] + list(args),
                            env=self.env)
        return rc, out, err, self._ledger()[before:]

    # ---- 该拒的拒 ----
    def test_bare_call_is_refused_without_touching_the_ledger(self):
        rc, out, err, rows = self._gate()
        self.assertEqual(rc, 2, "裸 `run_gate t` 必须 fail-closed（否则静默退化成整仓全量）："
                                "\n%s%s" % (out, err))
        self.assertNotIn("[run_gate] t: ", out,
                         "rc=2 之前就起了子进程 —— 守卫必须在**跑之前**拦住：\n%s" % out)
        self.assertEqual(rows, [], "判都没判却落了台账（rc=2 的前置约定是**不记账**）：%s" % rows)
        self.assertIn("--all", err, "错误文案要给出全量扫描的正确写法")
        self.assertIn("文件路径", err, "错误文案要给出定向检查的正确写法")
        self.assertIn("--repo", err,
                      "错误文案必须把**第三条出路**（点名目标仓 `--repo <目录>`）一起说出来 —— "
                      "判据收窄后这是合法形态之一，少了它读者只会以为「要么 --all、要么传文件」：\n%s" % err)

    def test_mode_only_call_does_not_promise_a_ledger_row(self):
        """裸调用 + `--mode`：同一个退化形态（`--mode` 分支同样有"无文件"这条路）。

        ★ 这条单独列，是因为它踩过一个更隐蔽的坑（2026-09-23 实测）：口径覆盖那句提示原先
        打印在守卫**之前** —— 于是输出里写着"→ 已记入台账 mode_override"，紧接着 fail-closed
        返回、一行都没记。**输出里的承诺与实际做的事相反**，与 L17 同一种病（不可信）。
        判据直接钉在这一点上：既然没记账，就不许说记了。
        """
        rc, out, err, rows = self._gate("--mode", "loose")
        self.assertEqual(rc, 2, "只带 --mode 也是无文件全量形态，必须同样 fail-closed：\n%s%s" % (out, err))
        self.assertNotIn("[run_gate] t: ", out, "rc=2 之前就起了子进程：\n%s" % out)
        self.assertEqual(rows, [], "判都没判却落了台账：%s" % rows)
        self.assertNotIn("已记入台账", out,
                         "输出了'已记入台账'而实际一行没记 —— 宁可不说，不许说反：\n%s" % out)

    # ---- 该放的放（反向：别把整条路封死） ----
    def test_explicit_all_scans_and_the_ledger_says_all(self):
        rc, out, err, rows = self._gate("--all")
        self.assertEqual(rc, 1, "`--all` 必须照旧真扫（夹具里 bad.vue 该被拦）：\n%s%s" % (out, err))
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0].get("scope"), "all",
                         "显式全量扫描必须记成 scope=all：%s" % rows[0])

    def test_all_with_mode_override_is_still_recorded_as_all(self):
        """`--all --mode loose`：**同一次漏水在另一条分支上的现场**（L17 的病根，实测量出）。

        修 `scope` 之前，这一形态真扫了一整仓、台账却写 `scope=targeted, files=0` ——
        `--mode` 分支先命中，而只有 `--all` 那条分支会设 scope。把口径收到一处（`--all` 在不在
        cmd 里）之后，两个消费者读同一个值。这条同时钉住"口径覆盖仍然照记"，
        免得修 scope 时把 mode_override 一起弄丢。
        """
        rc, out, err, rows = self._gate("--all", "--mode", "loose")
        self.assertEqual(rc, 1, "`--all --mode loose` 必须照旧真扫：\n%s%s" % (out, err))
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0].get("scope"), "all",
                         "真全量扫描记成 targeted = 台账说谎（本条要治的正是它）：%s" % rows[0])
        self.assertEqual(rows[0].get("mode_override"), "loose(config=strict)",
                         "口径覆盖仍须照记：%s" % rows[0])

    def test_honest_file_argument_still_works(self):
        """反向配平：带真文件 → 定向检查照旧，且台账 `scope/files` 与实际一致。

        与上面 `--all` 那条成对：两条一起才说明 `scope` 是**按实际形态**写的，
        而不是被改成某个常量。
        """
        rc, out, err, rows = self._gate(self.bad)
        self.assertEqual(rc, 1, out)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual((rows[0].get("scope"), rows[0].get("files")), ("targeted", 1),
                         "定向检查的台账行必须记 targeted/1：%s" % rows[0])

    # ---- 判据收窄的那一向：点了名的形态必须照跑 ----
    def test_repo_only_call_runs_and_is_recorded_as_the_scan_it_really_is(self):
        """`--repo <目录>` 单独出现 = 调用方**点名了整仓** ⇒ 放行，台账记 scope=all。

        ★ 这条锁的是一个**越界**（2026-09-23 当场撞红 6 条既有锁才发现）：第一版守卫的判据是
        "无文件且无 --all" 就 fail-closed，于是 `run_gate <gate> --repo <端仓>` 被一起拦掉 ——
        可目录不是文件，谈不上"定向查这几个文件"，这一形态**就是**一次整仓扫描。
        走它的是 `test_review_v13_batch8`（4 条）、`test_repo_key_registry`（2 条）两个既有夹具，
        以及本仓更早的 v9 P1-1 接线（调用方指定实际目标仓，worktree 场景的正解）。

        收窄后的判据是"文件参数 / `--all` / `--repo <目录>` 三样都没有才拦"，所以这里钉两件事：
        ① 照旧真扫（夹具里 bad.vue 该被拦）；② 台账写的是它**真实的样子**（scope=all, files=0）——
        不是 targeted/files=0（L17 的病根），也不是被漏记。
        """
        rc, out, err, rows = self._gate("--repo", self.repo)
        self.assertEqual(rc, 1,
                         "`--repo <目录>` 单独出现必须照旧真扫（判据收窄后它是合法形态）：\n%s%s"
                         % (out, err))
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual((rows[0].get("scope"), rows[0].get("files")), ("all", 0),
                         "点名整仓的调用被记成 targeted/files=0 —— 正是 L17 要治的记账：%s" % rows[0])

    def test_repo_only_and_all_land_the_same_ledger_shape(self):
        """反向：`--repo <目录>` 与 `--all` 的台账形状必须**相同**（同源判据的证据）。

        它俩在端脚本里本就是同一件事（`is_full = "--all" in sys.argv or not clean`），
        所以台账也不该把它们分成两种。若哪天有人给 `--repo` 那一路单独记个新 scope
        （譬如 "repo"），这条会红 —— 那说明口径又分叉成两处，而那正是 L17 的病根。
        """
        _rc1, _o1, _e1, r1 = self._gate("--all")
        _rc2, _o2, _e2, r2 = self._gate("--repo", self.repo)
        shape = [lambda r: (r.get("scope"), r.get("files"), r.get("rc")) for r in (r1, r2)]
        self.assertEqual([shape[0](r1[0])], [shape[1](r2[0])],
                         "两种全量形态的台账形状必须一致（判据同源）：\n--all: %s\n--repo: %s"
                         % (r1, r2))

    def test_entry_points_declare_the_full_scan_they_always_did(self):
        """CLI / MCP 不传文件时，由**入口自己**点名 `--all` ⇒ 台账记 scope=all，而不是 targeted。

        ★ 为什么修入口而不是放宽引擎：这两个入口不传文件时**以前也是整仓全量**
        （端脚本无位置参数即 is_full），引擎侧收严后若不点名，它们会从"能跑"变成 rc=2。
        点名只是把**已经在发生的事**写进台账（argv 语义不变、扫描范围不变）——
        行为零变化，去掉的是一条谎。两条各钉一次，`entry` 也要对得上（否则记账来源又成谜）。
        """
        before = len(self._ledger())
        rc, out, err = _run([PY, CLI, "run", "t"], env=self.env)
        rows = self._ledger()[before:]
        self.assertEqual(rc, 1, "`cli run t` 必须照旧真扫：\n%s%s" % (out, err))
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual((rows[0].get("scope"), rows[0].get("files"), rows[0].get("entry")),
                         ("all", 0, "cli"), rows[0])

        before = len(self._ledger())
        s = _McpSession(self.env)
        try:
            text, is_err = s.call_tool("harness_run_gate", {"gate": "t"})
        finally:
            s.close()
        rows = self._ledger()[before:]
        self.assertFalse(is_err, text)
        self.assertIn("BLOCKED", text, text)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual((rows[0].get("scope"), rows[0].get("files"), rows[0].get("entry")),
                         ("all", 0, "mcp"), rows[0])


@_slow("每例现建 git 工程仓 + 真跑 setup 的钩子安装（bash + git，约 4s）")
class TestSetupExitCodeIsHonest(unittest.TestCase):
    """H2 回归锁（2026-09-23 评审报告）：`setup` **任一步失败，最终退出码必须非零**。

    README / quickstart 把 `setup` 当一键入口，CI 与 agent 都按退出码判成败 ——
    原先它把子步骤的 rc 全吞掉、一律 exit 0：`setup --repo <非 git 目录>` 里钩子安装
    明明失败（git `fatal: not a git repository`）、后续步骤照跑、最后报成功。
    那就是"提交期门禁其实没装上"却告诉人已经接好了 —— 静默失效里最坏的一种。
    （同文件里 `hooks install` / `skill install` 单跑时都正确回传 rc，只有 setup 被吞。）
    """

    def test_setup_fails_when_hook_install_fails(self):
        d = tempfile.mkdtemp(prefix="egsetup-")
        notgit = os.path.join(d, "notgit")
        os.makedirs(notgit)                       # 存在的目录、但**不是** git 仓 → 装钩子必失败
        rc, out, err = _run([PY, CLI, "setup", "--repo", notgit])
        self.assertNotEqual(rc, 0, "钩子安装失败却报成功：\n%s%s" % (out, err))
        self.assertIn("钩子安装未成功", out)
        self.assertIn("有步骤失败", out, "结论要说清'别按已经接好处理'")

    def test_nonexistent_repo_is_a_config_error_not_a_crash(self):
        """`--repo` 指向不存在的路径 → rc=2（配置错），不许裸回溯。

        裸回溯的退出码是 1 —— **与"门禁拦截"同码**，脚本化调用方会把"路径写错了"
        读成"代码不合规"（同 _run 超时那条的归因）。
        """
        d = tempfile.mkdtemp(prefix="egsetup-")
        rc, out, err = _run([PY, CLI, "setup", "--repo", os.path.join(d, "nope", "deeper")])
        self.assertEqual(rc, 2, "实得 rc=%s\n%s%s" % (rc, out, err))
        self.assertNotIn("Traceback", out + err)

    def test_hooks_install_on_missing_repo_is_config_error(self):
        """同族的单跑入口：`hooks install --repo <不存在>` 也必须 rc=2（install 分支此前没有这道校验）。"""
        d = tempfile.mkdtemp(prefix="egsetup-")
        rc, out, err = _run([PY, CLI, "hooks", "install", "--repo", os.path.join(d, "nope")])
        self.assertEqual(rc, 2, "实得 rc=%s\n%s%s" % (rc, out, err))
        self.assertIn("repo 不存在", out)
        self.assertNotIn("Traceback", out + err)


class TestVersionSingleSourceInWheelForm(unittest.TestCase):
    """N1 回归锁（2026-09-16 剖析报告 A3）：**轮子安装形态**下三个入口必须报同一个版本。

    为什么单独立一条：ADR-0001 铁律 3 写着「版本单一来源（四处一致，测试锁定）」，但此前
    **没有任何测试覆盖轮子形态** —— 开发树里三处实现都能读到 VERSION 文件，看不出差别。
    差别只在安装形态暴露：wheel 里只有 `agentguard/*.py`（已用 zipfile 核对 v0.1.0/0.1.1/0.1.2
    三个产物，**不含 VERSION 文件**），此时 `mcp.py` 的旧实现（只读 VERSION、缺失即写死
    `0.0.0-dev`）与 `cli.py`/`__init__.py`（会回退包元数据）**报出两个版本号** ——
    同一个包自我描述不一致，任何「按版本判断能力」的调用方都会踩坑。

    夹具刻意做成**真实的安装形态**：临时目录里只有包 .py + 一个 dist-info（提供包元数据），
    没有 VERSION 文件、没有 harness/（`find_root()` 因此返回 None）。子进程里先断言
    `find_root() is None` —— 否则夹具没造对，这条锁就是假绿。
    """
    WANT = "0.9.9"          # dist-info 里写的版本；与仓库 VERSION 取不同的值，免得两边混淆

    def _wheel_form(self):
        d = tempfile.mkdtemp(prefix="egwheel-")
        # 轮子里是**两个**包：主包 + 迁移期兼容壳（后者 import 前者）。缺一个都装不起来。
        for name in ("guardrail_engine", "agentguard"):
            pkg = os.path.join(d, name)
            os.makedirs(pkg)
            src = os.path.join(ROOT, name)
            for n in os.listdir(src):
                if n.endswith(".py"):              # 轮子里就这些（不含 VERSION / sandbox）
                    shutil.copyfile(os.path.join(src, n), os.path.join(pkg, n))
        di = os.path.join(d, "guardrail_engine-%s.dist-info" % self.WANT)
        os.makedirs(di)
        with io.open(os.path.join(di, "METADATA"), "w", encoding="utf-8", newline="") as f:
            f.write("Metadata-Version: 2.1\nName: guardrail-engine\nVersion: %s\n" % self.WANT)
        return d

    def test_all_entries_agree_without_version_file(self):
        d = self._wheel_form()
        env = dict(os.environ)
        # 兜底（2026-09-23 评审报告 M1 第 1 例）：老写法只靠 `env["PYTHONPATH"] = d` 让子进程
        # 找到包目录 —— 而**隔离形态的解释器按设计忽略 PYTHONPATH**（embeddable python 的
        # `python3xx._pth`、`ignore_environment=1`；某些桌面端内置 python 就是这种），于是这条锁
        # 在那种环境下以 ModuleNotFoundError **假红**，它要验的「三个入口版本一致」跟"包怎么被
        # 找到"毫无关系。改成让**夹具自己把目录写进子进程的 sys.path**（见下面 code 的第一句）：
        # 与解释器是否读环境变量无关，夹具自足。PYTHONPATH 一并撤掉 —— 留着会掩盖
        # "有人把 path.insert 删了"这种回退。
        # 自查（真跑过）：`python -I -c "import agentguard"` + PYTHONPATH → ModuleNotFoundError；
        #                  同一条命令带上 sys.path.insert 那句 → 正常导入。
        for k in ("GUARDRAIL_ENGINE_DIR", "AGENTGUARD_DIR", "HARNESS_DIR"):   # 不能漏回真实仓库
            env.pop(k, None)
        # 子进程用 `json.dump(..., sys.stdout)` 而不是内置打印函数：`sandbox/list_entries.py`
        # 的「入口必须带 UTF-8 兜底」清单是**子串判据**（源码里出现打印调用 + 含非 ASCII 即入选），
        # 本文件含中文，一旦把打印调用的字面量写进来就会被当成控制台入口，从而要求一个
        # 本文件根本用不到的 reconfigure。这里只是把代码交给子进程，本文件自己不输出。
        code = ("import sys; sys.path.insert(0, %r);"        # ← 夹具自足：不吃 PYTHONPATH（见上）
                " import json, guardrail_engine as pkg, guardrail_engine.cli as cli,"
                " guardrail_engine.mcp as mcp, guardrail_engine._shell as shell;"
                " json.dump({'root': shell.find_root(), 'pkg': pkg.__version__,"
                " 'cli': cli._version(), 'mcp': mcp.VERSION}, sys.stdout)" % d)
        rc, out, err = _run([PY, "-c", code], env=env, cwd=d)
        self.assertEqual(rc, 0, "子进程加载安装形态的包失败：%s" % (err or out)[-400:])
        got = json.loads(out.splitlines()[-1])
        self.assertIsNone(got["root"],
                          "夹具没造对：临时目录里仍能定位到工程根 → 这条锁不会真的覆盖轮子形态")
        self.assertEqual(sorted({got["pkg"], got["cli"], got["mcp"]}), [self.WANT],
                         "轮子形态（无 VERSION 文件）下三个入口报出的版本必须一致且取包元数据：%s"
                         % got)


class TestLogGateFilter(unittest.TestCase):
    """批次 2（2026-10-15）：cli log --gate 过滤 —— Hermes 自查入口的机读契约。

    不走真台账：--log 指临时文件（cmd_log 对显式文件走 _read_jsonl，无环境依赖），
    两条不同 gate 的记录，断言：①过滤生效；②窗口内无该 gate → rc=1 + NO_RECORD
    （「查到了」与「压根没跑过」必须机读可分，否则自查形同虚设）。
    """

    def test_gate_filter_and_no_record(self):
        d = tempfile.mkdtemp(prefix="hlog_gate")
        try:
            log = os.path.join(d, "gate_log.jsonl")
            with io.open(log, "w", encoding="utf-8", newline="") as f:
                f.write(json.dumps({"ts": "2026-10-15T10:00:00", "gate": "old_pc",
                                    "rc": 0, "entry": "auto-gate", "ver": "0.1.7-dev"}) + "\n")
                f.write(json.dumps({"ts": "2026-10-15T10:01:00", "gate": "demo_pc",
                                    "rc": 1, "entry": "cli", "ver": "0.1.7-dev"}) + "\n")
            rc, out, err = _run([PY, CLI, "log", "--log", log, "--gate", "old_pc", "--last", "10"])
            self.assertEqual(rc, 0, out + err)
            self.assertIn("old_pc", out)
            self.assertNotIn("demo_pc", out, "--gate 过滤后别的 gate 的记录不得出现")
            rc2, out2, _ = _run([PY, CLI, "log", "--log", log, "--gate", "nope_pc", "--last", "10"])
            self.assertEqual(rc2, 1, "窗口内无该 gate 记录必须 rc=1：自查靠退出码区分「没查到」与「没跑过」")
            self.assertIn("NO_RECORD gate=nope_pc", out2)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestDoneCommand(unittest.TestCase):
    """批次 3（2026-10-15）：`cli.py done` —— 一条命令 + 一行**可验证凭证**（评审建议 #2/#3）。

    凭证的 h 是台账行的链哈希前 8 位：这里同时断言「打印的 h == 台账最后一条的 h」——
    凭证可伪造正是这类机制最大风险，必须锁死（agent 编不出对得上的 h，对人则可一眼复核）。
    """

    @classmethod
    def setUpClass(cls):
        cls.d, cls.repo, cls.cfg, cls.log, cls.bad, cls.good, cls.env = _mk_fixture()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    @_slow("现建 git 工程仓 + 真跑 CLI done（~3s）")
    def test_done_emits_credential_matching_ledger(self):
        rc, out, err = _run([PY, CLI, "done", "--repo", self.repo, "--only", self.bad],
                            env=self.env)
        self.assertEqual(rc, 1, "bad.vue 应被拦（rc=1）\n" + (out + err)[-600:])
        line = [l for l in out.splitlines() if l.startswith("[harness] gate=t repo=")]
        self.assertTrue(line, "凭证行缺失或格式不对：\n" + out[-600:])
        self.assertIn(" rc=1 ", line[0], "凭证应带 rc=1：%s" % line[0])
        h8 = line[0].rsplit("h=", 1)[1].strip()
        with io.open(self.log, encoding="utf-8") as f:
            recs = [json.loads(l) for l in f.read().splitlines() if l.strip()]
        self.assertTrue(recs, "台账应有本次记录")
        self.assertEqual(recs[-1].get("h", "")[:8], h8,
                         "凭证 h 与台账最后一条不一致 —— 凭证必须可复核")
        self.assertEqual(recs[-1].get("entry"), "auto-gate")
        self.assertEqual(recs[-1].get("via"), "cli", "done 的上游标应为 cli（复核链完整）")

    @_slow("现建 git 工程仓 + 真跑 CLI done（~3s）")
    def test_done_uses_caller_cwd_when_no_repo_flag(self):
        """回归（CodeBuddy 2026-10-15 实测抓到）：不带 --repo 时必须以**调用方 cwd** 识别仓库。

        旧实现子进程默认 cwd=工程仓 —— 在端仓目录里跑 done，被误判成 harness_self
        并打出一张 rc=0 的**假凭证**（挑错仓比不产凭证更危险：它让“没查端仓”
        看着像“查过且通过”）。
        """
        rc, out, err = _run([PY, CLI, "done", "--only", self.bad], env=self.env, cwd=self.repo)
        self.assertEqual(rc, 1, "应识别到 t 仓并拦住 bad.vue\n" + (out + err)[-600:])
        self.assertIn("[harness] gate=t repo=", out)
        self.assertNotIn("gate=harness_self", out, "又挑错仓了（M141）")

    @_slow("现建 git 工程仓 + 真跑 CLI done（~3s）")
    def test_done_unmanaged_repo_has_no_credential(self):
        other = tempfile.mkdtemp(prefix="done-unmanaged-")
        try:
            rc, out, err = _run([PY, CLI, "done", "--repo", other], env=self.env)
            self.assertEqual(rc, 3, "非受管仓库应 rc=3\n" + (out + err)[-400:])
            self.assertIn("非受管", out)
            self.assertNotIn("[harness] gate=", out,
                             "非受管不得产凭证（否则凭证机制没有意义）")
        finally:
            shutil.rmtree(other, ignore_errors=True)


def _pid_alive(pid):
    """进程是否还活着（跨平台；Windows 用 tasklist —— os.kill(pid, 0) 在 nt 上会真的杀）。"""
    if os.name == "nt":
        r = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return ("%d" % pid) in (r.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _load_cli_module():
    spec = importlib.util.spec_from_file_location(
        "killtree_cli_under_test", os.path.join(ROOT, "guardrail_engine", "cli.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestKillTree(unittest.TestCase):
    """批次 7（2026-10-15）：超时必须杀掉**整棵进程树**（实测病灶见 _kill_tree 注释：
    300s 超时后孤儿扫描又跑了 ~100 分钟、父进程阻塞在管道 EOF、输出全丢）。

    三条锁：① 行为 —— 孙进程必须被杀死 + 调用必须快速返回（旧的“只杀直接子进程”
    会等到孙进程自己醒来，本测试会变红）；② 两端实现逐字节同源（对拍）；③ `run --all`
    的默认超时必须是放宽后的 3600（300 会再撞全量扫描）。
    """

    @classmethod
    def setUpClass(cls):
        cls.cli = _load_cli_module()

    @_slow("真起 三层进程链（~12s）")
    def test_timeout_kills_grandchild_and_returns_fast(self):
        d = tempfile.mkdtemp(prefix="killtree-")
        try:
            pid_file = os.path.join(d, "grandchild.pid")
            script = os.path.join(d, "spawner.py")
            with io.open(script, "w", encoding="utf-8", newline="\n") as f:
                f.write("import subprocess, sys, time\n"
                        "child = subprocess.Popen([sys.executable, '-c',"
                        " 'import time; time.sleep(120)'])\n"
                        "open(%r, 'w').write(str(child.pid))\n"
                        "time.sleep(120)\n" % pid_file)
            # 温机：先跑一次立刻退出的 python —— 首次冷启动（AV 扫描 python.exe）实测
            # 能把 spawner 的写盘拖出超时窗，造成夹具假红（2026-10-15 遇到过一次）。
            subprocess.run([sys.executable, "-c", "pass"], timeout=30)
            t0 = time.time()
            rc, out, err = self.cli._run([sys.executable, script], timeout=10)
            elapsed = time.time() - t0
            self.assertEqual(rc, 2, "超时必须归 rc=2（基础设施错误不许冒充业务拦截 1）")
            self.assertLess(elapsed, 30,
                            "超时后仍被阻塞在管道上（只杀了直接子进程、孙进程还占着写端）："
                            "%.1fs" % elapsed)
            pid = None
            deadline = time.time() + 10
            while time.time() < deadline and not pid:
                if os.path.exists(pid_file):
                    try:
                        with io.open(pid_file) as fh:
                            pid = int(fh.read().strip() or 0) or None
                    except ValueError:
                        pid = None
                if not pid:
                    time.sleep(0.2)
            self.assertTrue(pid, "夹具没造出来：孙进程 PID 未落盘；dir=%r script_exists=%r"
                            % (sorted(os.listdir(d)), os.path.exists(script)))
            alive = True
            deadline = time.time() + 10
            while time.time() < deadline:
                alive = _pid_alive(pid)
                if not alive:
                    break
                time.sleep(0.3)
            self.assertFalse(alive, "孙进程 %d 还活着 —— 没杀整棵树（taskkill 缺 /T？）" % pid)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_killtree_block_is_identical_across_the_two_files(self):
        def block(rel):
            with io.open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                src = f.read().replace("\r\n", "\n")
            m = re.search(r"# HARNESS-KILLTREE-BEGIN.*?# HARNESS-KILLTREE-END", src, re.S)
            return m.group(0) if m else ""
        a = block("guardrail_engine/cli.py")
        b = block("integrations/hooks/agentguard-gate.py")
        self.assertTrue(a, "cli.py 里找不到 KILLTREE 块（标记被拆了？）")
        self.assertEqual(a, b, "杀树块两份实现漂移了（跨文件契约，改一处必须同步另一处）")

    def test_run_all_gets_the_longer_default_timeout(self):
        cli, seen = self.cli, {}
        orig = cli._run

        def fake(cmd, cwd=None, timeout=None):
            seen["timeout"] = timeout
            return (0, "", "")
        cli._run = fake
        try:
            def ns(**kw):
                d = dict(gate="t", files=[], all=False, mode=None, timeout=None)
                d.update(kw)
                return type("A", (), d)
            cli.cmd_run(ns(all=True))
            self.assertEqual(seen["timeout"], 3600, "全量扫描默认超时必须放宽（300 会再撞全扫）")
            cli.cmd_run(ns(timeout=99))
            self.assertEqual(seen["timeout"], 99, "--timeout 必须显式胜出")
            cli.cmd_run(ns(all=False, files=["a.vue"]))
            self.assertIsNone(seen["timeout"], "定向检查沿用 _run 的默认 300s（别在这里放大）")
        finally:
            cli._run = orig


if __name__ == "__main__":
    unittest.main()
