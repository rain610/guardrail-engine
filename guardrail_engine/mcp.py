# -*- coding: utf-8 -*-
"""guardrail-engine MCP 薄服务（单文件 · 零第三方依赖 · stdio JSON-RPC 2.0）

把 guardrail-engine 门禁暴露为 MCP 工具：任何支持 MCP 的 agent 在宿主里加一条配置即可
直接调用，无需阅读任何文档——

  {"mcpServers": {"guardrail-engine": {"command": "python", "args": ["<ENGINE>/guardrail_engine/mcp.py"]}}}

或一键打印配置：  python guardrail_engine/mcp.py --print-config

设计约束（ADR-0001 五条铁律）：
- 本文件是「壳」：零第三方依赖；不重定义引擎符号；不做行号/坐标系运算；
- 判定内核 = 同一份引擎（run_gate.py / auto-gate.py / coverage.py / doctor.sh），
  与 git 钩子、CLI 共用，保证「不同入口判定一致」；
- stdout 只输出协议 JSON（每行一个对象）；所有诊断日志走 stderr；
- 只读工具集：不提供任何写操作（accept/改基线属于「人的动作」，永不进壳）。
- **单线程串行**（评审 2026-09-23 L8）：`serve()` 是「读一行→跑到完→再读下一行」，一次只处理一个
  请求。长调用（doctor 约 1-2 分钟、`run_gate` 全量扫描数分钟）期间**连 ping 都在排队**，
  宿主可能据此判"服务死了"甚至重启 server。现状是**文档标注 + 引导到 CLI**（宿主读得到的
  `INSTRUCTIONS`、两个长工具的 description、本段），**不是**并发化 —— 并发要同时证明
  「`_send` 不交错」「两个 gate 同跑不打架（台账 append / doctor.sh 的副作用）」，本壳只有
  stdio 一条通道、没有任何排队与取消机制，凭一句"JSON-RPC 允许乱序回包"就上线程，
  是拿这个壳的五条铁律去赌一个没人验过的假设。

自检：  python guardrail_engine/mcp.py --selftest
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

# UTF-8 兜底（评审 H1 教训：宿主控制台可能是 GBK/ASCII）——stderr 诊断保持可读。
# stdout 协议输出全程走 buffer 写字节 + ensure_ascii=True，双保险。
try:
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SERVER_NAME = "guardrail-engine"
PROTO_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_TIMEOUT = int(os.environ.get("GUARDRAIL_MCP_TIMEOUT")
                      or os.environ.get("AGENTGUARD_MCP_TIMEOUT")   # 旧名，v0.3 移除
                      or "180")
MAX_OUTPUT = int(os.environ.get("GUARDRAIL_MCP_MAX_OUTPUT")
                 or os.environ.get("AGENTGUARD_MCP_MAX_OUTPUT")     # 旧名，v0.3 移除
                 or "12000")


# ---------- 工程根定位 / 版本（转发壳层公用件 _shell.py，与 cli.py 同一份实现） ----------
# 双形态导入（三种运行形态都要成立，详见 _shell.py 文件头）：
#   ① 安装形态（包上下文）→ 相对导入；② 脚本直跑（脚本目录在 sys.path[0]）→ 裸导入；
#   ③ exec_module（tests/test_preflight.py 按路径 exec 本文件）→ 无包上下文，补 sys.path 后裸导入。
try:
    from . import _shell
except ImportError:      # 无包上下文；若是 _shell 自身的 ImportError，下面这句会原样再抛
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _shell

ROOT = _shell.find_root()


def _read_version():
    """本包版本 —— 转发公用件。

    N1 修复（剖析报告 A3）：此处原先**只**读 VERSION 文件，`ROOT` 为空或文件缺失就直接
    返回写死的 `"0.0.0-dev"`，而 cli.py / `__init__.py` 会继续回退到包元数据 ——
    轮子安装形态（wheel 里只有 .py，不带 VERSION）下同一个包报出两个版本号。
    现三处入口都走 `_shell.read_version`，回退链只有一份。
    """
    return _shell.read_version(ROOT)


VERSION = _read_version()


# ---------- 子进程执行（零 shell、有超时、输出截断） ----------

def _py():
    return _shell.py_exe()


def _child_env():
    e = dict(os.environ)
    e.setdefault("PYTHONUTF8", "1")
    e["HARNESS_ENTRY"] = "mcp"          # 台账「来源入口」（旧引擎忽略未知变量，无副作用）
    return e


def run_cmd(args, cwd=None, timeout=None):
    p = subprocess.run(args, cwd=cwd or ROOT or os.getcwd(), env=_child_env(),
                       capture_output=True, timeout=timeout or DEFAULT_TIMEOUT)
    out = (p.stdout or b"").decode("utf-8", "replace")
    err = (p.stderr or b"").decode("utf-8", "replace")
    return p.returncode, out, err


def _tail(out, err):
    text = (out or "").strip()
    e = (err or "").strip()
    if e:
        text = (text + "\n[stderr]\n" + e).strip()
    if len(text) > MAX_OUTPUT:
        text = "[…输出截断，仅保留末尾 %d 字符…]\n" % MAX_OUTPUT + text[-MAX_OUTPUT:]
    return text or "(no output)"


VERDICTS = {
    0: ("PASS", "门禁通过", "✅ rc=0 PASS"),
    1: ("BLOCKED", "门禁拦截", "❌ rc=1 BLOCKED — 按输出里的 ❌ 行修复后重试；"
                              "不要静默绕过（紧急须 git commit --no-verify，并向用户说明）"),
    2: ("CONFIG_ERROR", "配置/定位错误", "⚠️ rc=2 CONFIG_ERROR — 查 gate 登记/仓库路径；"
                                        "可先调 harness_list_gates，或跑 run_gate.py --validate"),
    3: ("NOT_MANAGED", "非受管仓库", "⏭ rc=3 NOT_MANAGED — 该仓库未纳管，跳过即可"),
}


def format_result(label, rc, out, err, extra=""):
    v = VERDICTS.get(rc, ("UNKNOWN", "未知退出码", "rc=%s" % rc))
    head = "[%s] %s\n%s" % (label, v[2], extra if extra else "")
    return (head + "\n" + _tail(out, err)).strip()


# ---------- 工具实现 ----------


def _parse_gates(text):
    return _shell.parse_gates(text)


def _list_gates_raw():
    rc, out, err = run_cmd([_py(), os.path.join(ROOT, "harness", "run_gate.py"), "--list"])
    return rc, out, err, _parse_gates(out)


# 严重 5（评审 v13）：`tool_run_gate` 的正则 `^[A-Za-z0-9_.\-]+$` 是「字符集」而不是「名单」，
# 于是一个**长得像旗标的名字**照样通过 —— `{"gate": "--list"}` 拼成 `run_gate.py --list`，
# 被 run_gate._dispatch 在查 gate 之前分流掉（打印 gate 清单、rc=0）→ MCP 渲染
# 「✅ rc=0 PASS」，一条规则都没跑，这条假 PASS 还以 rc=0 落进 reports/gate_log.jsonl 审计台账。
# 修法：正向白名单 —— 用 `run_gate.py --list` 解析出的名字集合校验（同一条命令也是
# harness_list_gates 的数据源，不另维护一份清单）。
# 缓存键含 HARNESS_CONFIG：同一进程内夹具/工具换配置时不能吃到上一份名单。
_GATE_CACHE = {}


def _gate_catalog():
    """返回 (名字→enabled 的 dict, 错误串)。拿不到时 dict 为空、错误串非空。"""
    key = (os.environ.get("HARNESS_CONFIG") or "", ROOT or "")
    hit = _GATE_CACHE.get(key)
    if hit is None:
        if not ROOT:
            hit = ({}, "guardrail-engine root not found (set GUARDRAIL_ENGINE_DIR)")
        else:
            rc, out, err, gates = _list_gates_raw()
            if rc != 0 or not gates:
                hit = ({}, "无法读取 gate 名单（rc=%s）：%s" % (rc, ((err or out or "").strip()[:200])))
            else:
                hit = ({g["name"]: bool(g["enabled"]) for g in gates}, "")
        _GATE_CACHE[key] = hit
    return hit


def tool_list_gates(_args):
    if not ROOT:
        return "guardrail-engine root not found (set GUARDRAIL_ENGINE_DIR).", True
    rc, out, err, gates = _list_gates_raw()
    if rc != 0:
        return format_result("list_gates", rc, out, err), True
    header = "guardrail-engine gates: %d (enabled=%d)\n" % (len(gates), sum(1 for g in gates if g["enabled"]))
    return header + (out.strip() or "(empty)"), False


def tool_run_gate(a):
    a = a or {}
    gate = str(a.get("gate") or "").strip()
    repo = str(a.get("repo") or "").strip()
    files = a.get("files") or []
    if isinstance(files, str):
        files = [files]
    files = [str(f) for f in files]
    if gate and not re.match(r"^[A-Za-z0-9_.\-]+$", gate):
        return "invalid gate name: %r" % gate, True
    # 严重 5 的同族（2026-09-23 评审报告 H1，实测）：`files` 是位置参数通道，而引擎把
    # `--mode <v>` 当自己的旗标消费 —— `files:["--mode","loose","<文件>"]` 实测把一次判定
    # 从 rc=1（PC-COMP 拦截）翻成 rc=0「全部通过」，台账那行还看不出任何区别。
    # 位置参数只收文件路径：以 `-` 开头的一律拒绝（fail-closed），不"悄悄丢掉再照跑"
    # —— 丢掉会让调用方以为它传的东西生效了，那是另一种谎。
    _bad = _shell.flag_like(files)
    if _bad:
        return ("invalid `files` entry: %s —— 以 '-' 开头的是命令行旗标，不是文件路径。"
                "本工具的 `files` 只收文件（绝对路径，或相对 gate 仓的路径）；"
                "判定口径由 gate 策略决定，调用方不能覆盖。" % ", ".join(repr(b) for b in _bad)), True
    if gate:
        # 严重 5：正则之后再加两道 —— ① 不得以 `-` 开头（旗标冒充 gate 名）；
        # ② 必须是 `run_gate.py --list` 里真有的名字。名单取不到时不冒充"未知 gate"，
        # 照旧下传（run_gate 自己对未登记名返回 rc=2，真结论仍归引擎）。
        _catalog, _cerr = _gate_catalog()
        if gate.startswith("-"):
            return ("invalid gate name: %r —— 以 '-' 开头的是命令行旗标，不是 gate 名"
                    "（例：--list 会被 run_gate 在查 gate 之前分流掉，渲染成假的 rc=0 PASS）。"
                    "用 harness_list_gates 查真实 gate 名。" % gate), True
        if _catalog and gate not in _catalog:
            return ("unknown gate: %r\n已知 gate（harness_list_gates 可查全量）：%s"
                    % (gate, ", ".join(sorted(_catalog)[:20]))), True
        if _catalog and not _catalog.get(gate, False):
            # 中 15（评审 v13）：已登记但 enabled=false —— run_gate 打印「未启用，跳过」并 rc=0。
            # 那不是「检查通过」（一条规则都没跑），不能渲染成 ✅ rc=0 PASS。
            return ("[run_gate %s] ⏭ SKIPPED — gate 已登记但 `enabled: false`，"
                    "本次**未执行任何规则**；这不是检查通过。\n"
                    "（台账里该次运行记为 scope=skipped；要真检查请先在 config 里启用它。）"
                    % gate), False
        cmd = [_py(), os.path.join(ROOT, "harness", "run_gate.py"), gate] + files
        # 2026-09-23（评审 L17 同批）：不传 files 时点名「这次是整仓全量」——同 cli.py
        # 的 cmd_run（那里有完整注释：端脚本 is_full 判据同源，补的只是让台账记 scope=all，
        # 而不是把一次全量扫描记成 targeted/files=0）。不补则 run_gate 对裸调用 rc=2。
        if a.get("all") or not files:
            cmd.append("--all")
        rc, out, err = run_cmd(cmd)
        return format_result("run_gate %s" % gate, rc, out, err), rc not in (0, 1, 3)
    if repo:
        rp = os.path.abspath(repo)
        if not os.path.isdir(rp):
            return "repo not found: %s" % repo, True
        # 2026-09-23 评审报告 L9：这一支原先**只验 isdir**，等于把"任意目录树"交给引擎去走 ——
        # 参数契约（本工具的 inputSchema）写的是「a repository directory」，那就按契约验：
        # 没有 `.git` 的不算仓库，壳层直接拒，不生成子进程。
        # ★ 判 `exists` 而不是 `isdir`：worktree 的 `.git` 是**文件**（本机受管仓里就有 12 个
        #   这种 worktree），判 isdir 会把它们全部误拒。
        # ★ 只验「是不是仓库」，**不**验「受不受管」：后者归引擎，`rc=3 = repository not managed`
        #   是本工具对外承诺的返回码之一，壳层提前拦掉等于把它从契约里删了。
        if not os.path.exists(os.path.join(rp, ".git")):
            return ("repo is not a git repository (no `.git` inside): %s\n"
                    "`repo` 的契约是「一个仓库目录」（worktree 的 `.git` 是文件，同样算数）。"
                    "要跑某个**已登记**的 gate 请改用 `gate` + `files`，名单见 harness_list_gates；"
                    "目录在受管清单里但没跑起来，用 harness_doctor 看环境。" % repo), True
        cmd = [_py(), os.path.join(ROOT, "harness", "agents", "auto-gate.py"), "--repo", rp]
        if a.get("all"):
            cmd.append("--all")
        rc, out, err = run_cmd(cmd, cwd=rp)
        return format_result("run_gate(auto) %s" % os.path.basename(rp.rstrip("/\\")), rc, out, err), rc not in (0, 1, 3)
    return ("Provide `gate` (+ optional `files`) for a targeted check, or `repo` for auto-detect. "
            "Call harness_list_gates first to discover gates."), True


def tool_check_file(a):
    a = a or {}
    path = str(a.get("path") or a.get("file") or "").strip()
    if not path:
        return "missing `path` (the file to check).", True
    ap = os.path.abspath(path)
    if not os.path.isfile(ap):
        return "file not found: %s" % path, True
    rc, out, err, gates = _list_gates_raw()
    if rc != 0:
        return format_result("check_file", rc, out, err), True
    g = _shell.gate_owning(ap, gates)     # 归属匹配算法收敛到公用件（原与 cli.py 各一份）
    if not g:
        return ("No enabled gate manages this file's repository. "
                "Call harness_list_gates to see managed repos, or use harness_run_gate with an explicit gate."), True
    cmd = [_py(), os.path.join(ROOT, "harness", "run_gate.py"), g["name"], ap]
    rc, out, err = run_cmd(cmd)
    return format_result("check_file %s (gate=%s)" % (os.path.basename(ap), g["name"]), rc, out, err), rc not in (0, 1, 3)


def tool_summary(a):
    a = a or {}
    last = a.get("last")
    cmd = [_py(), os.path.join(ROOT, "harness", "run_gate.py"), "--summary"]
    if last:
        try:
            cmd.append(str(int(last)))
        except Exception:
            pass
    rc, out, err = run_cmd(cmd)
    return format_result("summary", rc, out, err), rc != 0


def tool_coverage(a):
    a = a or {}
    cmd = [_py(), os.path.join(ROOT, "harness", "coverage.py")]
    last = a.get("last")
    if last:
        try:
            cmd += ["--last", str(int(last))]
        except Exception:
            pass
    if a.get("json"):
        cmd.append("--json")
    rc, out, err = run_cmd(cmd)
    return format_result("coverage", rc, out, err), rc != 0


def _bash():
    return _shell.find_bash()


def tool_doctor(_a):
    b = _bash()
    if not b:
        return ("bash unavailable (Windows: install Git-for-Windows; Linux/macOS: install bash). "
                "Alternative quick checks: harness_list_gates / harness_summary / harness_coverage."), True
    rc, out, err = run_cmd([b, os.path.join(ROOT, "sandbox", "doctor.sh")], timeout=360)
    return format_result("doctor", rc, out, err), rc != 0


# ---------- MCP 协议层 ----------

INSTRUCTIONS = (
    "guardrail-engine exposes the local code gate as tools. Typical flow: "
    "(1) harness_list_gates to see gates; (2) after editing, harness_check_file or harness_run_gate; "
    "(3) rc=1 means BLOCKED — fix the ❌ findings, never bypass silently; rc=2 config issue; rc=3 not managed. "
    "harness_summary / harness_coverage read the decision ledger (audit trail). "
    "Engine: shared code-gate used by local git hooks; verdicts are identical across entries. "
    # L8（评审 2026-09-23）：串行语义写进宿主读得到的这段——否则宿主把"排队"读成"服务死了"。
    "NOTE — this server is SINGLE-THREADED and strictly serial: it handles one request at a time, "
    "so a long call blocks every other request, including the host's own ping. harness_doctor "
    "(~1-2 min) and harness_run_gate with all=true (a full scan, minutes) are the two long ones. "
    "If your host times out or looks dead during them, run those from a terminal instead: "
    "python <ENGINE>/guardrail_engine/cli.py run <gate> --all, or sh <ENGINE>/sandbox/doctor.sh."
)

TOOLS = [
    {
        "name": "harness_list_gates",
        "description": ("List all guardrail-engine code gates (name / enabled / repository path). "
                        "Call this first to discover gate names for harness_run_gate."),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "harness_run_gate",
        "description": ("Run the guardrail-engine code gate — same engine as the local git hooks. "
                        "Targeted: pass `gate` (+ optional `files`). Auto-detect: pass `repo` (a repository directory). "
                        "Return codes: 0=PASS; 1=BLOCKED (fix the reported ❌ findings, do not bypass); "
                        "2=config/registry error; 3=repository not managed. "
                        "Example: {\"repo\": \"D:/work/myrepo\"} or {\"gate\": \"example\", \"files\": [\"src/a.vue\"]}."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "gate": {"type": "string", "description": "Gate name from harness_list_gates, e.g. example."},
                "files": {"type": "array", "items": {"type": "string"},
                          "description": "Files to check (absolute or relative to the gate repo). Optional when gate scans all."},
                "repo": {"type": "string", "description": "Repository directory for auto gate detection (alternative to gate)."},
                "all": {"type": "boolean",
                        "description": "Full scan instead of targeted files. ⚠️ Slow (minutes on a large "
                                       "repo) and it BLOCKS the server while it runs (single-threaded — "
                                       "even ping queues behind it); also bounded by GUARDRAIL_MCP_TIMEOUT "
                                       "(default 180s), so a very large repo can come back as a timeout. "
                                       "For full scans prefer the CLI: cli.py run <gate> --all."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "harness_check_file",
        "description": ("Check a single file with the gate that manages it (auto-matched by repository path). "
                        "Use right after editing a file. Same return-code semantics as harness_run_gate. "
                        "Example: {\"path\": \"D:/work/myrepo/src/a.vue\"}."),
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "File path to check."}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "harness_summary",
        "description": ("Show the gate decision ledger summary (recent runs: totals, block rate, per-gate stats). "
                        "Use to audit what the gate has been doing. Optional `last` = number of records (default 500)."),
        "inputSchema": {
            "type": "object",
            "properties": {"last": {"type": "integer", "minimum": 1, "description": "How many recent records to aggregate."}},
            "additionalProperties": False,
        },
    },
    {
        "name": "harness_coverage",
        "description": ("Show rule-level coverage from the gate ledger: which rules fire/are waived, and rules that "
                        "never fire (possible dead rules). Optional `last` (record window) and `json` (raw report)."),
        "inputSchema": {
            "type": "object",
            "properties": {"last": {"type": "integer", "minimum": 1},
                           "json": {"type": "boolean", "description": "Return the raw JSON report."}},
            "additionalProperties": False,
        },
    },
    {
        "name": "harness_doctor",
        "description": ("Run the environment integration self-check (python, UTF-8 entrypoints, end-to-end gate probe, "
                        "hook activity, skill links). Takes ~1-2 minutes. ⚠️ This is one of the two long calls: "
                        "the server is single-threaded, so everything else (including ping) queues behind it — "
                        "prefer the CLI (sh <HARNESS>/sandbox/doctor.sh) if your host times out."),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]

_HANDLERS = {
    "harness_list_gates": tool_list_gates,
    "harness_run_gate": tool_run_gate,
    "harness_check_file": tool_check_file,
    "harness_summary": tool_summary,
    "harness_coverage": tool_coverage,
    "harness_doctor": tool_doctor,
}


def handle_initialize(params):
    want = str((params or {}).get("protocolVersion") or "")
    proto = want if want in PROTO_VERSIONS else PROTO_VERSIONS[0]
    return {
        "protocolVersion": proto,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": VERSION},
        "instructions": INSTRUCTIONS,
    }


def handle_tools_call(params):
    # 2026-09-23 评审报告 L7（实测）：壳层不做参数类型校验，坏类型一路走到 handler 里才炸 ——
    # `name: [1,2]` 让下面 `name not in _HANDLERS` 抛 `TypeError: unhashable type: 'list'`，
    # 被外层的兜底 except 收成 **-32603 Internal error**。可那是「服务端自己的 bug」的意思，
    # 而这里的真相是「调用方传错了参数」= **-32602 Invalid params**。两者对宿主的含义相反：
    # -32603 会让宿主认为门禁服务坏了（可能触发重连/禁用），-32602 才会让调用方去改参数。
    # 类型对了但名字不认识，仍是 -32602（保持原判据），只是不再经过"会崩"的那条路。
    if params is None:
        params = {}      # JSON-RPC 里 `params` 本身是可选的 —— 缺省不是错
    if not isinstance(params, dict):
        return {"__rpc_error__": {"code": -32602,
                                  "message": "Invalid params: `params` must be an object, got %s"
                                             % type(params).__name__}}
    name = params.get("name")
    if name is not None and not isinstance(name, str):
        # ★ 只在"**给了** name、但类型不对"时插话：`name` 缺省（None）照旧落到下面那条
        #   「Unknown tool」—— 那是本壳对"没有这个工具"的既有说法，两处都是 -32602，
        #   没必要借这次修复改口（改口不会更对，只会让老宿主的日志对不上）。
        return {"__rpc_error__": {"code": -32602,
                                  "message": "Invalid params: `name` must be a string, got %s %r"
                                             % (type(name).__name__, name)}}
    args = params.get("arguments")
    if not args:
        # 空值一律当"没传"（`null`/`{}`/`[]`/`""` 都是宿主常见的"无参数"写法，行为不变）；
        # 只有**非空且不是对象**的才是错的 —— 那种值会让每个 handler 的 `.get` 崩。
        args = {}
    elif not isinstance(args, dict):
        return {"__rpc_error__": {"code": -32602,
                                  "message": "Invalid params: `arguments` must be an object, got %s"
                                             % type(args).__name__}}
    if name not in _HANDLERS:
        return {"__rpc_error__": {"code": -32602, "message": "Unknown tool: %s" % name}}
    t0 = time.time()
    try:
        text, is_err = _HANDLERS[name](args)
    except subprocess.TimeoutExpired:
        text, is_err = "tool timed out (engine call exceeded timeout)", True
    except Exception as e:  # 壳层绝不把异常变成"静默空结果"（报平安透镜）
        text, is_err = "internal error: %r" % e, True
    ms = int((time.time() - t0) * 1000)
    sys.stderr.write("[guardrail-engine-mcp] %s ms=%d isError=%s\n" % (name, ms, is_err))
    return {"content": [{"type": "text", "text": text}], "isError": bool(is_err)}


def dispatch(msg):
    if not isinstance(msg, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    mid = msg.get("id")
    method = msg.get("method")
    is_notification = ("id" not in msg)
    if method is None:
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32600, "message": "Invalid Request"}}
    if method == "initialize":
        result = handle_initialize(msg.get("params"))
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        result = handle_tools_call(msg.get("params"))
    elif isinstance(method, str) and method.startswith("notifications/"):
        return None
    else:
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found: %s" % method}}
    if isinstance(result, dict) and "__rpc_error__" in result:
        return {"jsonrpc": "2.0", "id": mid, "error": result["__rpc_error__"]}
    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _send(buf, obj):
    buf.write(json.dumps(obj, ensure_ascii=True, separators=(",", ":")).encode("utf-8") + b"\n")
    buf.flush()


def serve():
    if not ROOT:
        sys.stderr.write("[guardrail-engine-mcp] root not found; "
                         "set GUARDRAIL_ENGINE_DIR or keep this file under <root>/guardrail_engine/.\n")
        return 2
    sys.stderr.write("[guardrail-engine-mcp] serving root=%s version=%s python=%s\n"
                     % (ROOT, VERSION, _py()))
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    for raw in iter(stdin.readline, b""):
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            _send(stdout, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        try:
            resp = dispatch(msg)
        except Exception as e:
            mid = msg.get("id") if isinstance(msg, dict) else None
            resp = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": "Internal error: %r" % e}}
        if resp is not None:
            _send(stdout, resp)
    return 0


# ---------- 辅助 CLI（自检 / 打印配置） ----------

def _print_config():
    cfg = {"mcpServers": {SERVER_NAME: {"command": _py(), "args": [os.path.abspath(__file__)]}}}
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    print()
    print("# Claude Code 一键接入：")
    print('claude mcp add %s -- "%s" "%s"' % (SERVER_NAME, _py(), os.path.abspath(__file__)))
    return 0


def _selftest():
    ok = True
    print("root       :", ROOT or "(NOT FOUND)")
    print("version    :", VERSION)
    print("python     :", _py())
    if not ROOT:
        return 1
    rc, out, err, gates = _list_gates_raw()
    print("list_gates : rc=%s gates=%d enabled=%d" % (rc, len(gates), sum(1 for g in gates if g["enabled"])))
    if rc != 0:
        ok = False
        print("  stderr:", (err or "")[:200])
    print("tools      :", ", ".join(t["name"] for t in TOOLS))
    if len(TOOLS) < 5 or any(len(t.get("description", "")) < 40 for t in TOOLS):
        ok = False
        print("  !! tool descriptions too thin")
    print("SELFTEST", "OK" if ok else "FAILED")
    return 0 if ok else 1


def _require_python(vi=None):
    """前置检查：本工程需要 **Python 3.11+** —— 转发公用件（原四个入口各一份逐字相同的实现）。

    参数 vi 仅供测试注入（默认取 sys.version_info）。
    """
    return _shell.require_python(vi)


def main(argv=None):
    _require_python()   # 前置检查（Python 3.11+）
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return _selftest()
    if "--print-config" in argv:
        return _print_config()
    return serve()


if __name__ == "__main__":
    sys.exit(main())
