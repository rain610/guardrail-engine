# -*- coding: utf-8 -*-
"""外部工具适配门禁的锁（架构 A，2026-10-25）。

被锁的东西只有一个：**外部工具接进来之后，三种「没跑成」不许被读成「通过」**。
本仓在别处已经为同一个病付过两次学费（`r.status ?? 0` 把解释器起不来读成放行；
git 读不到 diff 被读成存量），所以这里三条各有一条用例，且都跑真脚本子进程：

  ① 工具没装/命令配置无效/超时            → rc=2，且输出明说「未执行 ≠ 通过」；
  ② 输出不是 JSON / 工具以配置错退出      → rc=2（rc 不冒充「有违规」）；
  ③ Semgrep 报告分析错误（结果不完整）    → rc=2（不能当「没问题」）。

另有正向锁：error 阻断、warn 不阻断、外部规则 ID 能用同一个 `harness-ok:` 豁免、
`--all` 把「.」交给工具而不是自己遍历（规则范围归工具管）、以及经 run_gate 的端到端接线。

夹具是一段真 Python 脚本（`AGENTGUARD_ESLINT` / `AGENTGUARD_SEMGREP` 接受 JSON 数组），
不依赖本机装没装 ESLint / Semgrep —— CI 与开发机结论一致。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _tmpclean import rmtree_force as _rmtree_force  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HARNESS = os.path.join(ROOT, "harness")
ESLINT_SCRIPT = os.path.join(ROOT, "checks", "eslint_check.py")
SEMGREP_SCRIPT = os.path.join(ROOT, "checks", "semgrep_check.py")

_ENV_STRIP = ("HARNESS_CONFIG", "HARNESS_LOG_FILE", "HARNESS_STATS_FILE",
              "AGENTGUARD_ESLINT", "AGENTGUARD_SEMGREP", "AGENTGUARD_ADAPTER_TIMEOUT",
              "FAKE_PAYLOAD", "FAKE_RC", "FAKE_ARGV_OUT", "FAKE_PID")

_FAKE_TOOL = '''# -*- coding: utf-8 -*-
import json, os, sys
argv = sys.argv[1:]
out = os.environ.get("FAKE_ARGV_OUT")
if out:
    with open(out, "w", encoding="utf-8") as f:
        json.dump(argv, f)
payload = os.environ.get("FAKE_PAYLOAD")
if payload:
    with open(payload, encoding="utf-8") as f:
        sys.stdout.write(f.read())
sys.exit(int(os.environ.get("FAKE_RC", "0")))
'''

# 超时用例：子进程持有继承来的 stdout 管道，模拟「只杀直接子进程」会一直等到它醒来。
_SLOW_TOOL = '''# -*- coding: utf-8 -*-
import os, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
with open(os.environ["FAKE_PID"], "w", encoding="utf-8") as f:
    f.write(str(child.pid))
time.sleep(60)
'''


def _pid_alive(pid):
    if os.name == "nt":
        r = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return ("%d" % pid) in (r.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ext-adapter-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.fake = os.path.join(self.tmp, "fake_tool.py")
        with open(self.fake, "w", encoding="utf-8", newline="\n") as f:
            f.write(_FAKE_TOOL)
        self.source = os.path.join(self.repo, "app.js")
        with open(self.source, "w", encoding="utf-8", newline="\n") as f:
            f.write('console.log("debug")\n')
        self.addCleanup(_rmtree_force, self.tmp)

    def _payload(self, obj, rc=0):
        p = os.path.join(self.tmp, "payload.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj))
        return p

    def _env(self, **kw):
        env = dict(os.environ)
        for k in _ENV_STRIP:
            env.pop(k, None)
        env.update(kw)
        return env

    def _tool_env(self, var, payload=None, rc=0, **extra):
        env = {var: json.dumps([sys.executable, self.fake])}
        if payload is not None:
            env["FAKE_PAYLOAD"] = self._payload(payload, rc)
        env["FAKE_RC"] = str(rc)
        env.update(extra)
        return env

    def _run(self, script, args, env):
        r = subprocess.run([sys.executable, script, "--repo", self.repo] + list(args),
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=180, env=self._env(**env), cwd=ROOT)
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def _eslint(self, payload, args=None, rc=0, **extra):
        return self._run(ESLINT_SCRIPT, args if args is not None else ["app.js"],
                         self._tool_env("AGENTGUARD_ESLINT", payload, rc, **extra))

    def _semgrep(self, payload, args=None, rc=0, **extra):
        return self._run(SEMGREP_SCRIPT, args if args is not None else ["app.js"],
                         self._tool_env("AGENTGUARD_SEMGREP", payload, rc, **extra))

    # ── ① 没装 / 命令配置无效 / 超时 ──────────────────────────────────────
    def test_missing_tool_is_not_a_pass(self):
        rc, out = self._run(ESLINT_SCRIPT, ["app.js"],
                            {"AGENTGUARD_ESLINT": "definitely-not-an-eslint-binary"})
        self.assertEqual(rc, 2, "外部工具没装却被当成通过：\n" + out)
        self.assertIn("未执行", out, "没装时没说清「本次没查」：\n" + out)

    def test_invalid_tool_override_is_not_a_pass(self):
        rc, out = self._run(ESLINT_SCRIPT, ["app.js"], {"AGENTGUARD_ESLINT": "[1, 2]"})
        self.assertEqual(rc, 2, "命令配置无效却没有 rc=2：\n" + out)
        self.assertIn("JSON 字符串数组", out, "没有指出配置格式：\n" + out)

    def test_timeout_kills_tree_and_is_not_a_pass(self):
        slow = os.path.join(self.tmp, "slow_tool.py")
        with open(slow, "w", encoding="utf-8", newline="\n") as f:
            f.write(_SLOW_TOOL)
        pid_file = os.path.join(self.tmp, "grandchild.pid")
        env = {"AGENTGUARD_ESLINT": json.dumps([sys.executable, slow]),
               "AGENTGUARD_ADAPTER_TIMEOUT": "1",
               "FAKE_PID": pid_file}
        t0 = time.time()
        rc, out = self._run(ESLINT_SCRIPT, ["app.js"], env)
        elapsed = time.time() - t0
        self.assertEqual(rc, 2, "超时没有归 rc=2：\n" + out)
        self.assertLess(elapsed, 30, "超时后仍被孙进程拖住（%.1fs）—— 只杀了直接子进程？\n%s"
                        % (elapsed, out))
        self.assertIn("超时", out, "超时原因没写出来：\n" + out)
        pid = None
        deadline = time.time() + 10
        while time.time() < deadline and not pid:
            if os.path.exists(pid_file):
                try:
                    with open(pid_file, encoding="utf-8") as f:
                        pid = int(f.read().strip() or 0) or None
                except ValueError:
                    pid = None
            if not pid:
                time.sleep(0.2)
        self.assertTrue(pid, "夹具没造出孙进程 PID")
        self.assertFalse(_pid_alive(pid), "孙进程 %d 还活着 —— 没杀整棵" % pid)

    # ── ② 输出不可信 / 工具配置错 ─────────────────────────────────────────
    def test_bad_json_is_not_a_pass(self):
        rc, out = self._eslint("this is not json")
        self.assertEqual(rc, 2, "坏 JSON 被当成通过：\n" + out)
        self.assertIn("不是可解析的 JSON", out, "没有点名输出不可信：\n" + out)

    def test_eslint_config_error_exit_is_not_a_pass(self):
        rc, out = self._eslint([], rc=2)
        self.assertEqual(rc, 2, "ESLint 以配置错退出却被读成业务结论：\n" + out)
        self.assertIn("配置/环境错误", out, "没有区分「配置错」与「有违规」：\n" + out)

    def test_named_missing_file_is_not_a_pass(self):
        rc, out = self._eslint([], args=["nope.js"])
        self.assertEqual(rc, 2, "点名的文件不存在却放行：\n" + out)
        self.assertIn("目标文件不存在", out, "没有点名缺失文件：\n" + out)

    # ── 正向：severity 映射与统一输出/豁免 ────────────────────────────────
    def _one(self, rule_id="no-console", severity=2, line=1):
        return [{"filePath": self.source,
                 "messages": [{"ruleId": rule_id, "severity": severity,
                               "message": "Unexpected console statement", "line": line}]}]

    def test_eslint_error_blocks_with_rule_id_and_line(self):
        rc, out = self._eslint(self._one())
        self.assertEqual(rc, 1, "error 级命中没有拦：\n" + out)
        self.assertIn("[no-console]", out, "输出没带规则 ID（豁免要靠它，agent 只能猜）：\n" + out)
        self.assertIn("app.js:1", out, "输出没带文件:行号：\n" + out)

    def test_eslint_warning_does_not_block(self):
        rc, out = self._eslint(self._one(severity=1))
        self.assertEqual(rc, 0, "warning 不该阻断：\n" + out)
        self.assertIn("[no-console]", out, "警告也要显示规则 ID：\n" + out)

    def test_inline_waiver_suppresses_external_rule(self):
        with open(self.source, "w", encoding="utf-8", newline="\n") as f:
            f.write("// harness-ok: no-console 调试日志已用条件封装\n"
                    'console.log("debug")\n')
        rc, out = self._eslint(self._one(line=2))
        self.assertEqual(rc, 0, "外部规则 ID 的行内豁免没生效：\n" + out)
        self.assertIn("行内豁免", out, "用掉的豁免没汇报：\n" + out)
        self.assertNotIn("条件封装", out,
                         "豁免理由被回显 —— 注入边界（理由不回显）在适配层被绕开了：\n" + out)

    # ── 范围归工具管：--all 交「.」，定向交点名文件 ────────────────────────
    def test_all_scan_passes_dot_to_tool(self):
        argv_out = os.path.join(self.tmp, "argv.json")
        rc, out = self._eslint([], args=["--all"], FAKE_ARGV_OUT=argv_out)
        self.assertEqual(rc, 0, out)
        with open(argv_out, encoding="utf-8") as f:
            argv = json.load(f)
        self.assertEqual(argv[-1], ".", "全量扫描没有把「.」交给工具（自己遍历会漏掉工具级范围）：%r" % argv)
        self.assertFalse([x for x in argv if x.endswith(".js")],
                         "--all 却把具体文件塞给了工具：%r" % argv)

    def test_targeted_scan_passes_explicit_files(self):
        argv_out = os.path.join(self.tmp, "argv.json")
        rc, out = self._eslint([], args=["app.js"], FAKE_ARGV_OUT=argv_out)
        self.assertEqual(rc, 0, out)
        with open(argv_out, encoding="utf-8") as f:
            argv = json.load(f)
        self.assertIn(os.path.abspath(self.source), argv,
                      "定向扫描没把点名文件交给工具：%r" % argv)

    def test_mode_flag_is_accepted_and_ignored(self):
        """`run_gate <gate> <file> --mode loose` 是通用 CLI 形态。

        适配 gate 本来就不区分 strict/loose（规则取舍归工具配置），但**参数必须收下**：
        否则 argparse 以 rc=2 退出，用户看到的是「参数错误」而不是「不适用」。
        """
        rc, out = self._eslint([], args=["--mode", "loose", "app.js"])
        self.assertEqual(rc, 0, "--mode 被适配层当成非法参数：\n" + out)
        self.assertNotIn("unrecognized arguments", out, "argparse 又把它拒了：\n" + out)

    # ── Semgrep：severity 映射 + 分析不完整 ──────────────────────────────
    def _sg(self, severity="ERROR", line=1, results=True):
        return {"results": [{"check_id": "python.lang.security.audit.eval",
                             "path": self.source,
                             "start": {"line": line},
                             "extra": {"severity": severity, "message": "eval is dangerous"}}]
                if results else [],
                "errors": []}

    def test_semgrep_error_blocks_and_info_does_not(self):
        rc, out = self._semgrep(self._sg("ERROR"))
        self.assertEqual(rc, 1, "Semgrep ERROR 没有拦：\n" + out)
        self.assertIn("[python.lang.security.audit.eval]", out, "规则 ID 丢了：\n" + out)
        rc, out = self._semgrep(self._sg("INFO"))
        self.assertEqual(rc, 0, "Semgrep INFO 不该阻断：\n" + out)

    def test_semgrep_unknown_severity_blocks(self):
        rc, out = self._semgrep(self._sg("BRAND_NEW_LEVEL"))
        self.assertEqual(rc, 1, "未知 severity 被默认放行（新版本出现新级别就会静默漏）：\n" + out)

    def test_semgrep_engine_errors_are_not_a_pass(self):
        payload = self._sg("INFO")
        payload["errors"] = [{"type": "Syntax error", "path": self.source,
                              "message": "could not parse"}]
        rc, out = self._semgrep(payload)
        self.assertEqual(rc, 2, "分析不完整却被当成通过：\n" + out)
        self.assertIn("结果不完整", out, "没有说清「没扫完」：\n" + out)

    def test_semgrep_config_error_exit_is_not_a_pass(self):
        """Semgrep 以 rc>=2 退出时，合法 JSON 骨架也不能把结论洗白。

        `{"results":[],"errors":[]}` 是最容易骗过「只看 errors」的形状：配置写坏、
        插件加载失败都可能输出它。工具的退出码是环境层事实，不能由 payload 覆盖。
        """
        payload = {"results": [], "errors": []}
        rc, out = self._semgrep(payload, rc=2)
        self.assertEqual(rc, 2, "Semgrep 以配置错退出却被读成「没有违规」：\n" + out)
        self.assertIn("配置/环境错误", out, "没有区分「配置错」与「有违规」：\n" + out)

    def test_semgrep_relative_path_waiver_works(self):
        """Semgrep 的 `path` 通常是相对仓库根的，豁免读取必须按 repo 拼接。

        原先 `_lines_of("app.js")` 按适配层进程的 cwd（工程根）去找文件 → 找不到 →
        `inline_waiver` 拿到空行集 → 豁免静默失效。用户看到的是假阳性，只能关掉 gate。
        """
        with open(self.source, "w", encoding="utf-8", newline="\n") as f:
            f.write("// harness-ok: no-console 已确认仅为调试输出\n"
                    'console.log("debug")\n')
        payload = {"results": [{"check_id": "no-console", "path": "app.js",
                                "start": {"line": 2},
                                "extra": {"severity": "ERROR", "message": "console"}}],
                   "errors": []}
        rc, out = self._semgrep(payload)
        self.assertEqual(rc, 0, "Semgrep 相对路径让行内豁免失效了：\n" + out)
        self.assertIn("行内豁免", out, "用掉的豁免没汇报：\n" + out)

    # ── 端到端：经 run_gate 的真实接线（注册表 / --gate / 覆盖层） ─────────
    def test_run_gate_end_to_end_uses_the_adapter(self):
        cfg = os.path.join(self.tmp, "local.toml")
        with open(cfg, "w", encoding="utf-8", newline="\n") as f:
            f.write('[repos]\nexample_repo = %s\n\n'
                    '[gates.eslint]\nenabled = true\nrepo = "example_repo"\n'
                    'script = "checks/eslint_check.py"\nrun_on = ["pre_commit"]\n'
                    % json.dumps(self.repo))
        env = self._env(**self._tool_env("AGENTGUARD_ESLINT", self._one()))
        env["HARNESS_CONFIG"] = cfg
        # 覆盖层默认只允许改 repos 路径（共享策略不许被本机文件改写），测试要临时开户 gate。
        env["HARNESS_ALLOW_POLICY_OVERRIDE"] = "1"
        env["HARNESS_LOG_FILE"] = os.path.join(self.tmp, "gate_log.jsonl")
        env["HARNESS_STATS_FILE"] = os.path.join(self.tmp, "stats.json")
        r = subprocess.run([sys.executable, os.path.join(HARNESS, "run_gate.py"),
                            "eslint", "app.js"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180, env=env, cwd=ROOT)
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 1, "经 run_gate 的适配 gate 没拦住：\n" + out)
        self.assertIn("--gate eslint", out, "run_gate 没把本次归属记成 eslint：\n" + out)
        self.assertIn("[no-console]", out, "端到端输出丢了规则 ID：\n" + out)

    def test_run_gate_mode_on_adapter_leaves_no_fake_weakened_record(self):
        """`--mode loose` 对适配 gate 没有语义，台账里不能留下「被放宽」的假记录。

        适配层收下旗标只是为了兼容通用 CLI 形态；若 run_gate 照旧按内置 gate 的规则
        写 `mode_override` / `weakened`，审计时会把一次普通判定读成「口径被放宽」——
        台账说假话比不记更坏。
        """
        cfg = os.path.join(self.tmp, "local.toml")
        with open(cfg, "w", encoding="utf-8", newline="\n") as f:
            f.write('[repos]\nexample_repo = %s\n\n'
                    '[gates.eslint]\nenabled = true\nrepo = "example_repo"\n'
                    'script = "checks/eslint_check.py"\nrun_on = ["post_edit"]\n'
                    % json.dumps(self.repo))
        ledger = os.path.join(self.tmp, "gate_log.jsonl")
        env = self._env(**self._tool_env("AGENTGUARD_ESLINT", []))
        env["HARNESS_CONFIG"] = cfg
        env["HARNESS_ALLOW_POLICY_OVERRIDE"] = "1"
        env["HARNESS_LOG_FILE"] = ledger
        env["HARNESS_STATS_FILE"] = os.path.join(self.tmp, "stats.json")
        r = subprocess.run([sys.executable, os.path.join(HARNESS, "run_gate.py"),
                            "eslint", "app.js", "--mode", "loose"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180, env=env, cwd=ROOT)
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("不区分 strict/loose", out,
                      "--mode 在适配 gate 上被静默忽略，没告诉调用方：\n" + out)
        with open(ledger, encoding="utf-8") as f:
            recs = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(recs), 1, "台账条数不对：%r" % recs)
        self.assertNotIn("mode_override", recs[0],
                         "适配 gate 没有口径覆盖，台账却记了 mode_override：%r" % recs[0])
        marks = [m for m in recs[0].get("weakened") or [] if m.startswith("mode_override:")]
        self.assertEqual(marks, [],
                         "适配 gate 没有 loose 语义，台账却标了 mode_override 削弱：%r" % recs[0])

    def test_external_adapter_emits_per_rule_stats(self):
        """P2-10：外部适配器的每条 finding 必须 `rule_base.bump`，否则 HARNESS_STATS_FILE
        的 `rules` 段为空 —— coverage.py 读不到外部工具规则的触发记录，会误判「从未生效」。

        旧实现漏了 bump 调用：error/warn/waived_inline 三种事件都没累计，emit_stats 落盘时
        stats_snapshot() 返回空 dict。修复后：stats.json 的 rules 段必须含规则 ID 作为键，
        值里有 `error` / `warn` / `waived_inline` 之一且计数 >= 1。
        """
        stats_path = os.path.join(self.tmp, "stats.json")
        env = self._env(**self._tool_env("AGENTGUARD_ESLINT", self._one()))
        env["HARNESS_STATS_FILE"] = stats_path
        rc, out = self._run(ESLINT_SCRIPT, ["app.js"], env)
        self.assertEqual(rc, 1, "error 级 finding 必须阻断：\n" + out)
        self.assertTrue(os.path.isfile(stats_path), "HARNESS_STATS_FILE 没被写出：\n" + out)
        with open(stats_path, encoding="utf-8") as f:
            stats = json.load(f)
        rules = stats.get("rules") or {}
        self.assertIn("no-console", rules,
                      "stats.json 的 rules 段缺少规则 ID（bump 没被调用）：%r" % rules)
        self.assertGreaterEqual(rules["no-console"].get("error", 0), 1,
                                "error 级 finding 必须累计到 stats：no-console=%r" % rules["no-console"])


if __name__ == "__main__":
    unittest.main()
