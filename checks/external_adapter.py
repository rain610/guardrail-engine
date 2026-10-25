# -*- coding: utf-8 -*-
"""外部分析器适配层（架构 A：门禁是策略强制平面，分析交给成熟工具）。

为什么是「适配」而不是自研分析器（2026-10-25 拍板）
----------------------------------------------------
引擎层只用标准库（AGENTS.md 第 5 条），标准库里只有 Python 的 `ast`；Vue / JS / TS / C#
没有可用解析器。自研等于先写四套前端，再去和 ESLint / Semgrep 正面竞争 —— 而本项目的
价值在**门禁生命周期**（各客户端接线、fail-closed、台账、豁免、变异冒烟），那与分析器
强弱正交。README 的门面定位也早已写明「不是 lint 工具，而是策略强制平面」。

契约：外部工具 JSON → `rule_base.Finding` → 与内置规则**同一条**输出/豁免/退出码通道。
    · 行内豁免复用 `rule_base.inline_waiver`（按 rule_id + 行号），外部工具的规则 ID 同样可用；
    · `harness-ok:` 的理由照旧只计数不回显（注入边界同一条）；
    · 退出码 0/1/2 语义与三端脚本一致：1 = 有阻断级问题，2 = 环境/配置错（**不是**不通过）。

fail-closed 三条（本仓反复咬过的形状，逐条上锁见 tests/engine/test_external_adapters.py）
------------------------------------------------------------------------------
  ① 工具没装 / 起不来 / 超时          → rc=2，明说「本次未执行，不等于通过」；
  ② 输出不是 JSON / 工具以配置错退出  → rc=2（rc 不拿来当「有违规」的判据以外的东西）；
  ③ 分析不完整（Semgrep 的 errors 非空）→ rc=2，不把「没扫完」读成「没问题」。

已知边界（写在这里，不靠口头承诺）
----------------------------------
外部工具读的是**工作区**文件；提交期门禁对暂存区做的 overlay 只作用于豁免文本的读取
（`rule_base.read_bytes`），不会把暂存内容喂给 ESLint / Semgrep。要严格对齐暂存区，
应在 pre-commit 里先让工具读暂存副本（本适配层不代做，避免悄悄改动被检内容）。
因此默认 `run_on` **只挂 `post_edit`**：在 pre-commit 下，「暂存坏版本 + 工作区改回
好版本」可以绕过外部 gate —— 文档写了不等于机制拦住了（2026-10-25 评审 §4.3）。
要挂 pre_commit，先接受工作区语义，或在调用方实现暂存副本。
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import rule_base  # noqa: E402

DEFAULT_TIMEOUT = 300
TIMEOUT_ENV = "GUARDRAIL_ADAPTER_TIMEOUT"
# 迁移期兼容：改名前的环境变量名继续可用（v0.3 移除）。
LEGACY_ENV = {
    "GUARDRAIL_ESLINT": "AGENTGUARD_ESLINT",
    "GUARDRAIL_SEMGREP": "AGENTGUARD_SEMGREP",
    "GUARDRAIL_ADAPTER_TIMEOUT": "AGENTGUARD_ADAPTER_TIMEOUT",
}


def _env(name):
    """读环境变量：新名优先，旧名兜底（旧名在 v0.3 移除）。"""
    return os.environ.get(name) or (LEGACY_ENV.get(name) and os.environ.get(LEGACY_ENV[name])) or ""

_SEV_ESLINT = {1: rule_base.Severity.WARN, 2: rule_base.Severity.ERROR}

# 已知取值按语义映射；**未知取值一律当 error**（fail-closed：新版本冒出的级别不能默认放行）。
_SEV_SEMGREP = {
    "CRITICAL": rule_base.Severity.ERROR,
    "ERROR": rule_base.Severity.ERROR,
    "HIGH": rule_base.Severity.ERROR,
    "MEDIUM": rule_base.Severity.WARN,
    "WARNING": rule_base.Severity.WARN,
    "LOW": rule_base.Severity.INFO,
    "INFO": rule_base.Severity.INFO,
}


class AdapterError(Exception):
    """外部工具/环境层面的失败 —— 一律 rc=2，不得读成「代码不合规」或「通过」。"""


def _tool_argv(env_name, default_cmd):
    """外部命令：留空 → PATH 里的默认名；也可用 JSON 字符串数组（便于带参数/夹具）。"""
    raw = _env(env_name).strip()
    if not raw:
        return [default_cmd]
    if raw.startswith("["):
        try:
            val = json.loads(raw)
        except ValueError:
            val = None
        if not (isinstance(val, list) and val and all(isinstance(x, str) and x for x in val)):
            raise AdapterError("%s 必须是 JSON 字符串数组或普通命令：%r" % (env_name, raw))
        return val
    return shlex.split(raw, posix=(os.name != "nt"))


def _tool_missing(argv):
    exe = argv[0]
    if os.path.dirname(exe):
        return None if os.path.isfile(exe) else exe
    return None if shutil.which(exe) else exe


def _kill_tree(proc):
    """超时杀整棵进程树。

    与 `agentguard/cli.py` 的 HARNESS-KILLTREE 块同形，但**不共享代码**：引擎层脚本不能
    import 壳包（init 出来的 `~/.agentguard/engine` 里没有可执行包上下文）。两处行为漂移的
    风险由本文件的超时用例兜住（断言 rc=2、快速返回、孙进程已死）。
    """
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=30)
        else:
            import signal
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_tool(argv, cwd, timeout):
    """跑外部工具；超时/起不来 → AdapterError（不是 rc，更不是通过）。"""
    try:
        p = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=(os.name != "nt"))
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as e:
        raise AdapterError("外部工具起不来：%r" % (e,))
    try:
        out_b, err_b = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        try:
            p.communicate(timeout=30)      # 杀树后收尾：管道写端可能被孙进程持有
        except Exception:
            pass
        raise AdapterError("外部工具超时（>%ss，已连同子进程树一并终止）" % timeout)
    return (p.returncode,
            (out_b or b"").decode("utf-8", "replace"),
            (err_b or b"").decode("utf-8", "replace"))


def _timeout_value():
    raw = _env(TIMEOUT_ENV).strip()
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        val = int(raw)
    except ValueError:
        raise AdapterError("%s 不是整数：%r" % (TIMEOUT_ENV, raw))
    if val <= 0:
        raise AdapterError("%s 必须为正整数：%r" % (TIMEOUT_ENV, raw))
    return val


def _parse_json(out, tool):
    text = (out or "").strip()
    if not text:
        raise AdapterError("%s 没有输出（期望 JSON）—— 本次门禁未跑完" % tool)
    try:
        return json.loads(text)
    except ValueError as e:
        raise AdapterError("%s 输出不是可解析的 JSON（%s）—— 本次门禁未跑完" % (tool, e))


def _map_eslint(payload):
    """ESLint `--format json` → [(绝对路径, Finding)]。

    顶层是「每个文件一项」的数组；`messages[].severity` 1=warning / 2=error。
    `ruleId` 为 null 只出现在解析失败一类致命错误上（fatal=true），必须按 error 处理 ——
    语法都读不通的文件不能因为「没有规则 ID」就被跳过。
    """
    if not isinstance(payload, list):
        raise AdapterError("ESLint JSON 顶层不是数组（版本/参数不对？）")
    out = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        path = item.get("filePath") or ""
        for msg in item.get("messages") or []:
            if not isinstance(msg, dict):
                continue
            rid = msg.get("ruleId")
            if not rid:
                rid = "ESLINT-PARSE-ERROR" if msg.get("fatal") else "ESLINT-UNKNOWN"
            sev = _SEV_ESLINT.get(msg.get("severity"), rule_base.Severity.ERROR)
            out.append((path, rule_base.Finding(
                rule_id=str(rid),
                message=str(msg.get("message") or "").strip() or "(无消息)",
                severity=sev,
                line=msg.get("line"),
            )))
    return out


def _map_semgrep(payload):
    """Semgrep `--json` → ([(绝对路径, Finding)], 分析错误列表)。"""
    if not isinstance(payload, dict):
        raise AdapterError("Semgrep JSON 顶层不是对象（版本/参数不对？）")
    findings = []
    for res in payload.get("results") or []:
        if not isinstance(res, dict):
            continue
        extra = res.get("extra") or {}
        raw_sev = str(extra.get("severity") or "").upper()
        sev = _SEV_SEMGREP.get(raw_sev, rule_base.Severity.ERROR)
        start = res.get("start") or {}
        findings.append((res.get("path") or "", rule_base.Finding(
            rule_id=str(res.get("check_id") or "SEMGREP-UNKNOWN"),
            message=str(extra.get("message") or "").strip() or "(无消息)",
            severity=sev,
            line=start.get("line"),
            fix=str(extra.get("fix") or "").strip(),
        )))
    return findings, list(payload.get("errors") or [])


def _rel(repo, path):
    if not path:
        return "?"
    try:
        return os.path.relpath(path, repo) if os.path.isabs(path) else path
    except ValueError:
        return path


def _abs(repo, path):
    """工具返回的路径 → 本进程可读的绝对路径。

    2026-10-25 评审 §4.2：Semgrep 的 `path` 通常是相对仓库根的，而适配层进程的 cwd
    是工程根。直接 `read_bytes("app.js")` 找不到 `<repo>/app.js`，行内豁免静默失效。
    """
    return path if os.path.isabs(path) else os.path.join(repo, path)


def _lines_of(path, cache):
    """文件行（走 rule_base.read_bytes：提交期能读到暂存内容）。读不到 → 空。"""
    if path in cache:
        return cache[path]
    try:
        lines = rule_base.read_bytes(path).decode("utf-8", "replace").splitlines()
    except Exception:
        lines = []
    cache[path] = lines
    return lines


def run(kind, tool, env_name, default_cmd, extra_args):
    """适配器主流程。kind 只决定 JSON 形状；其余（输出/豁免/退出码）完全共用。"""
    ap = argparse.ArgumentParser(description="AgentGuard %s 适配门禁" % tool)
    ap.add_argument("--repo", required=True, help="目标仓库路径")
    ap.add_argument("--all", action="store_true", help="全量扫描（由工具按自身配置遍历）")
    ap.add_argument("--gate", default="", help="gate 名（用于台账统计归属）")
    ap.add_argument("--mode", default="", help="兼容 run_gate 的通用旗标；适配 gate 不区分 strict/loose")
    ap.add_argument("files", nargs="*", help="要检查的文件（相对仓库根）")
    a = ap.parse_args()

    repo = os.path.abspath(a.repo)
    if not os.path.isdir(repo):
        print("仓库路径不存在: %s" % repo, file=sys.stderr)
        return 2

    if a.all or not a.files:
        targets = None                      # 交给工具按自身配置遍历整仓
        scope = "all"
    else:
        # 点名了文件就必须查：路径写错/文件已删时不能退化成「无目标 → 通过」。
        pairs = [(f, f if os.path.isabs(f) else os.path.join(repo, f)) for f in a.files]
        missing = [f for f, p in pairs if not os.path.isfile(p)]
        if missing:
            for f in missing:
                print("❌ 目标文件不存在: %s" % f, file=sys.stderr)
            print("门禁未执行：调用方点名的文件必须存在且可读（拒绝静默放行）", file=sys.stderr)
            return 2
        targets = [p for _, p in pairs]
        scope = "targeted"

    try:
        argv = _tool_argv(env_name, default_cmd)
        missing_tool = _tool_missing(argv)
        if missing_tool:
            raise AdapterError(
                "%s 未安装或不在 PATH：%s\n"
                "  本门禁**未执行任何检查**，不等于通过（rc=2）。安装后重试，"
                "或用 %s 指定命令（也接受 JSON 字符串数组）。" % (tool, missing_tool, env_name))
        timeout = _timeout_value()
        cmd = argv + list(extra_args) + (targets if targets is not None else ["."])
        rc, out, err = _run_tool(cmd, repo, timeout)

        # 两个工具的 0/1 都可以承载业务结论；>=2 一律是配置/环境错误（Semgrep 同样适用）。
        # 2026-10-25 评审 §4.1：原先只给 ESLint 判 rc，Semgrep 用 rc=2 + 合法空 JSON
        # 就能被读成「没有违规」。
        if rc not in (0, 1):
            tail = (err or out or "").strip().splitlines()[-3:]
            raise AdapterError("%s 以 rc=%d 结束（配置/环境错误，不是「有违规」）%s"
                               % (tool, rc, ("：" + " / ".join(tail)) if tail else ""))
        payload = _parse_json(out, tool)
        if kind == "eslint":
            mapped = _map_eslint(payload)
        else:
            mapped, engine_errors = _map_semgrep(payload)
            if engine_errors:
                first = []
                for e in engine_errors[:3]:
                    if isinstance(e, dict):
                        first.append("%s: %s" % (e.get("type") or "error",
                                                 str(e.get("message") or e.get("path") or "").strip()))
                    else:
                        first.append(str(e))
                raise AdapterError(
                    "%s 报告 %d 个分析错误（%s）—— 结果不完整，不等于通过（rc=2）"
                    % (tool, len(engine_errors), " / ".join(first) or "无详情"))
    except AdapterError as e:
        print("✗ %s" % e, file=sys.stderr)
        print("门禁未执行/未跑完：环境或工具问题一律 rc=2，不能读成「通过」", file=sys.stderr)
        return 2

    lines_cache = {}
    blocking = warns = waived = 0
    for path, f in mapped:
        rel = _rel(repo, path)
        abs_path = _abs(repo, path)
        if f.line is not None:
            reason = rule_base.inline_waiver(f.rule_id, f.line, _lines_of(abs_path, lines_cache))
            if reason:
                rule_base.WAIVED_DETAILS.append((f.rule_id, f.line, reason))
                waived += 1
                continue
        loc = "%s:%s" % (rel, f.line) if f.line is not None else rel
        if f.severity == rule_base.Severity.ERROR:
            print("  ❌ %s [%s] %s" % (loc, f.rule_id, f.message))
            if f.fix:
                print("     FIX: %s" % f.fix)
            blocking += 1
        else:
            print("  ⚠️  %s [%s] %s" % (loc, f.rule_id, f.message))
            warns += 1

    summary = rule_base.waiver_summary()
    if summary:
        print(summary)
    rule_base.emit_stats(gate=a.gate, scope=scope,
                         files=len(targets) if targets is not None else 0)
    print("\n%s 检查完成（外部工具）" % tool, end="")
    print("：❌ %d 个阻断级问题" % blocking
          + ("，%d 条警告" % warns if warns else "")
          + ("，%d 条行内豁免" % waived if waived else ""))
    return 1 if blocking else 0
