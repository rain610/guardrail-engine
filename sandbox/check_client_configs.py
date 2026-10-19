#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""静态体检：各客户端的钩子配置（**不需要 CLI**，GUI-only 客户端也查得出来）。

为什么需要它：Trae CN 2026-09-13 实测踩到两个「配置形态」问题，表现为
「钩子日志里有执行、模型却什么都没收到」——
  1) 命令带引号 → 宿主用 **PowerShell** 执行时被拼崩（钩子压根没跑起来）；
  2) PowerShell **管道**把非 0 退出码吞成 1 → 客户端不认 exit 2，等于没拦。
这类问题不跑端到端也能静态抓出来，所以做成体检：给同事的机器也能一条命令查。

规则
  · 文件存在 + JSON 可解析（Trae「从 Claude 导入」曾产出**非法 JSON**，客户端会整份忽略）
  · 必须引用 integrations/hooks/agentguard-gate.py，且该文件真实存在（被删/挪走=静默失效）
  · 命令里的**解释器**必须真实存在（uv 升级/重建环境后那条绝对路径会消失 = 同样静默失效，L11）
  · 客户端参数必须匹配（claude/codebuddy/codex/zcode/qoder/trae），否则按别家契约输出、结论进不了模型
  · **宿主 shell 规则**：宿主是 PowerShell 的客户端（目前 trae）命令不得含引号，且必须含 `exit $LASTEXITCODE`
  · 关键事件（PostToolUse/Stop）必须在

用法
  python sandbox/check_client_configs.py [--home <目录>] [--json]
退出码：0 全通过；1 有 ❌（必坏）
"""
from __future__ import annotations

import pathlib

import argparse
import io
import json
import os
import re
import shutil
import sys

# UTF-8 兜底（2026-09-13 评审 v3 H1 的系统性修法）：宿主管道可能是 GBK/ASCII 控制台，
# 打印中文/✗ 会抛 UnicodeEncodeError；errors=replace 保证最坏也只是个别字符变问号。
# 锁：tests/test_entry_level.py::TestAllEntriesHaveUtf8Fallback（机器推导清单，别手删）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HARNESS_HINT = "agentguard-gate"

# 判据知识截止日（评审 v4 Q4-④）：每条硬判据都标"基于哪个客户端版本 + 哪天实测"，
# 客户端升级后用户知道先复核哪条，而不是直接不信任体检器。
KNOWLEDGE_DATE = "2026-09-13"

CLIENTS = [
    # claude：**只要 PostToolUse**。2026-09-13 实测：给它加 Stop（exit 2）会把 PostToolUse 的回灌
    # 整个破坏掉（端到端从 ✅ 变 no、留档里 0 次门禁结论），撤回；兜底交给 git pre-commit（工具无关）。
    dict(name="claude",    rel=".claude/settings.json",    host="cmd",        cli="claude",    events=("PostToolUse",),
         basis="claude 2.1.269 @2026-09-13"),
    dict(name="codebuddy", rel=".codebuddy/settings.json", host="cmd",        cli="codebuddy", events=("PostToolUse", "Stop"),
         basis="codebuddy（版本未测）@2026-09-13"),
    dict(name="codex",     rel=".codex/hooks.json",        host="cmd",        cli="codex",     events=("PostToolUse", "Stop"),
         basis="codex-cli 0.154.0 @2026-09-13"),
    dict(name="qoder",     rel=".qoder-cn/settings.json",  host="cmd",        cli="qoder",     events=("PostToolUse", "Stop"),
         basis="qoder CLI 1.1.51 @2026-09-13"),
    dict(name="zcode",     rel=".zcode/cli/config.json",   host="cmd",        cli="zcode",     events=("PostToolUse", "Stop"),
         basis="ZCode（版本未测）@2026-09-13"),
    # Trae 在 PowerShell 里执行钩子命令 → 形态要求最严
    dict(name="trae",      rel=".trae-cn/hooks.json",      host="powershell", cli="trae",      events=("PostToolUse", "Stop"),
         basis="Trae CN 3.3.95 @2026-09-13"),
]


def _walk_commands(node, out, path=""):
    """把配置里所有“会被执行的命令”找出来：command 字符串 或 args 数组。

    一个节点**同时**有 `command`（字符串）与 `args`（数组）时（ZCode 的写法：命令被拆成
    `command`=解释器 + `args`=[脚本, 客户端, 事件]），先合成一条完整命令行再交出去 ——
    分开看时两半都判不出"这条命令整体能不能起来"（L11）：`args` 那半看着就是脚本路径、
    `command` 那半又不含门禁关键字而进不了判据。合成后 `args`/`command` 两个键不再单独产出，
    免得同一条命令被数两遍（门禁引用重复报问题）。
    """
    if isinstance(node, dict):
        cmd, args = node.get("command"), node.get("args")
        combined = isinstance(cmd, str) and isinstance(args, list)
        if combined:
            out.append((path + "/command+args",
                        cmd + " " + " ".join(str(x) for x in args)))
        for k, v in node.items():
            if combined and k in ("command", "args"):
                continue
            if k == "command" and isinstance(v, str):
                out.append((path + "/command", v))
            elif k == "args" and isinstance(v, list):
                out.append((path + "/args", " ".join(str(x) for x in v)))
            else:
                _walk_commands(v, out, path + "/" + k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_commands(v, out, "%s[%d]" % (path, i))


def _tokens(cmd):
    """按空白切词，但**双引号内的整段算一个词**（路径含空格时只能这么写）。

    不能简单 `cmd.replace('"', " ").split()`：那样 `"C:/Program Files/…/python.exe"`
    会被切成 `C:/Program` + `Files/…`，判据就会把一个**存在**的路径判成不存在（假红）。
    （`_gate_py` 原先用的就是朴素切法 —— 工程仓装在含空格的目录下时同样会误报"脚本不存在"。）
    未闭合的引号按"引到行尾"处理（宁可少切一刀，也不凭空造出一个不存在的路径）。
    """
    out, cur, q = [], "", False
    for ch in cmd:
        if ch == '"':
            q = not q
            continue
        if ch.isspace() and not q:
            if cur:
                out.append(cur)
                cur = ""
            continue
        cur += ch
    if cur:
        out.append(cur)
    return out


def _gate_py(cmd):
    """从命令里抽出钩子脚本路径（用于确认它真实存在）。"""
    for tok in _tokens(cmd):
        if HARNESS_HINT in tok:
            return tok
    return None


# --------------------------------------------------------- 解释器存在性（L11）
# 评审 2026-09-23 L11：体检器原先只校验「配置里写的钩子脚本在不在」，**没看解释器**。
# 而钩子命令是 `<解释器> <钩子脚本> <客户端> <事件>` 拼出来的，解释器那一段是**环境相关**的
# 绝对路径（本机是 uv 装出来的 `…/uv/python/cpython-3.11-windows-x86_64-none/python.exe`）。
# uv 升级 / 重建环境 / 换 Python 版本之后那条路径会消失 —— 症状与"钩子被删"一模一样：
# 宿主调不起命令，模型侧什么都收不到，而**体检器当时是全绿的**（这正是本项目最想消灭的
# 那类失败：不是坏了，而是坏得没人知道）。
# 判据分两档（与 Q4-④ 的三态一致，不制造假红）：
#   · 绝对路径不存在 → ❌ 确定坏。命令根本起不来，且这一点**本机就能判定**，不依赖任何假设。
#   · 裸命令名（`python` / `uv` / `sh`）→ 只在本机 PATH 里也找不到时给 ⚠。
#     体检器进程的 PATH 未必等于宿主执行钩子时的 PATH，判不了"确定坏"，只能请人复核。
# **有意不判**的两种形态（认不出就别猜，宁可漏报）：命令里没有解释器那一段（直接执行脚本，
# 靠 shebang / Windows 文件关联照样跑得起来，判"坏"就是假红）；解释器与脚本之间夹着开关
# （`python -X utf8 x.py` —— 开关后面那个词未必是解释器，认错方向比不认更糟）。
_INTERP_ABS_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|//|/|~[\\/])")


def _interpreter_finding(cmd):
    """看这条命令的**解释器**（门禁脚本前一个 token）在本机是否真的存在。

    返回 `(level, 文案)`：level ∈ "bad"（确定坏）/ "warn"（判不了，请复核）；判不了时返回 None。
    """
    toks = _tokens(cmd)
    gi = next((i for i, t in enumerate(toks) if HARNESS_HINT in t), None)
    if gi is None or gi == 0:
        return None                    # 不引用门禁 / 没有解释器那一段 → 不归这条判据管
    interp = toks[gi - 1]
    if interp.startswith("-"):
        return None                    # 夹着开关，认不出真正的解释器 → 放行（宁可漏报）
    if not _INTERP_ABS_RE.match(interp.replace("\\", "/")):
        if shutil.which(interp) is None:
            return ("warn", "解释器 `%s` 在本机 PATH 里找不到（宿主执行钩子时的 PATH 可能不同，请复核）"
                    % interp)
        return None
    path = os.path.expanduser(interp)
    if os.path.isfile(path):
        return None
    return ("bad", "解释器不存在：%s —— 钩子命令根本起不来，**编辑期门禁会静默失效**"
                   "（客户端那侧连一行日志都不会有）。uv 升级 / 重建环境 / 换 Python 版本后"
                   "这条路径就会变；重跑 sandbox/install_shim.py 可修复" % interp)



# 本机 shim 的目录特征：客户端配置里写的是 ~/.agentguard/hooks/…（转发到工程仓里的正本）
_SHIM_DIR_HINT = ".agentguard"


def _shim_target_problem(shim_path, home):
    """若配置引用的是本机 shim，检查它的**转发目标**是否真实存在。

    2026-09-13 外部评审 G2：体检器原先只校验"配置里写的脚本文件存在"，
    不校验 shim 里那条转发目标 —— 换机器/挪目录后，shim 会静默 exit 0，
    7 家客户端的编辑期门禁**整体无声失效**（正是本项目最想消灭的那类失败）。
    返回问题字符串或 None。
    """
    # v3 修订（H5b）：**按内容特征识别**（含 GEN_PATH + runpy.run_path），不再只看目录名
    # —— 原先 shim 放在自定义目录时会被静默跳过校验。
    try:
        src = __import__('pathlib').Path(shim_path).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return "shim 读不了（%s）" % e
    if "GEN_PATH" not in src or "runpy.run_path" not in src:
        return None                      # 直接指向工程仓正本，无需再解析
    pats = [r"GEN_PATH\s*=\s*r?[\"']([^\"']+)[\"']"]
    roots = []
    for pat in pats:
        for m in re.finditer(pat, src):
            roots.append(m.group(1))
    ptr = os.path.join(home, ".agentguard", "harness.path")   # 必须用传入的 home（证伪测试抓到过：用真实 ~ 会假绿）
    if os.path.isfile(ptr):
        try:
            roots.append(pathlib.Path(ptr).read_text(encoding="utf-8").strip())
        except Exception:
            pass
    env = (os.environ.get("AGENTGUARD_DIR") or os.environ.get("HARNESS_DIR") or "").strip()
    if env:
        roots.append(env)
    for r in roots:
        if not r:
            continue
        tgt = os.path.join(r.replace("\\", "/"), "integrations", "hooks", "agentguard-gate.py")
        if os.path.isfile(tgt):
            return None
    return ("shim 的转发目标全部失效（GEN_PATH/指针/环境变量都解析不到正本）→ "
            "换机器或挪目录后编辑期门禁会静默失效；重跑 sandbox/install_shim.py 可修复")


def check_client(cfg, home):
    """返回 (level, problems, commands)；level ∈ ok / bad / skip"""
    path = os.path.join(home, cfg["rel"].replace("/", os.sep))
    if not os.path.isfile(path):
        return "skip", ["未配置（%s 不存在）" % cfg["rel"]], []
    return judge_config_file(path, cfg, home)


def judge_config_file(path, cfg, home):
    """对**一份具体的配置文件**跑全部判据 —— 用户级与项目级共用这一套，避免两处判据漂移。

    （2026-09-21 从 `check_client` 里抽出来：项目级那 32 份 `zcode.json` 之所以烂到今天没人管，
    根因就是体检器只认 `--home` 下的用户级路径，项目级**从来没有被任何判据看过**。）
    """
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8-sig", errors="replace"))
    except Exception as e:
        return "bad", ["JSON 非法（%s）—— 客户端会整份忽略，钩子静默失效" % e], []

    cmds = []
    _walk_commands(data, cmds)
    gate = [(w, c) for w, c in cmds if HARNESS_HINT in c]

    # Q4-④ 三态分级（评审 v4）：❌ 只留给"确定坏"；"没见过/不识别"给 ⚠ 让人复核，避免假红毁信任。
    hooks = data.get("hooks")
    events_node = hooks.get("events") if isinstance(hooks, dict) and isinstance(hooks.get("events"), dict) else hooks
    schema_known = isinstance(events_node, dict) and len(events_node) > 0
    if not schema_known:
        return "warn", ["schema 不识别：既没有 hooks/events 键、也没有可解析的事件表"
                        "（客户端可能升级改了配置结构）—— 请人工核对（基于 %s）" % cfg["basis"]], []
    if not gate:
        return "bad", ["未引用门禁钩子（%s）—— 配置区里扫不到任何 gate 引用" % HARNESS_HINT], []

    problems = []
    warns = []                     # ⚠ 级问题（三态里"待复核"那档；不算失败）
    for where, c in gate:
        # 客户端参数：必须标成本客户端，否则按别家契约输出
        if cfg["cli"] not in [t for t in re.split(r"[;\s]+", c.replace("\\", "/")) if t]:
            problems.append("%s 未标 client=%s（会按别家契约输出，结论可能进不了模型）" % (where, cfg["cli"]))
        # 钩子脚本必须真实存在（被删/挪走 = 静默放行）
        gp = _gate_py(c)
        if gp and not os.path.isfile(gp.replace("\\", "/")):
            problems.append("%s 指向的钩子脚本不存在：%s" % (where, gp))
        elif gp:
            sp = _shim_target_problem(gp, home)
            if sp:
                problems.append("%s %s" % (where, sp))
        # 解释器必须真实存在（L11）：脚本在、解释器没了 = 命令照样起不来，一样静默失效
        ifg = _interpreter_finding(c)
        if ifg:
            (problems if ifg[0] == "bad" else warns).append("%s %s" % (where, ifg[1]))
        # 宿主 shell 规则
        if cfg["host"] == "powershell":
            if '"' in c:
                problems.append("%s 含引号：该客户端在 PowerShell 里执行命令，引号会被拼崩（历史上钩子压根没跑起来）" % where)
            if "exit $LASTEXITCODE" not in c:
                problems.append("%s 缺 `; exit $LASTEXITCODE`：PowerShell 管道把非 0 退出码吞成 1，客户端不认，等于没拦" % where)

    hooks = data.get("hooks")
    events = hooks.get("events") if isinstance(hooks, dict) and isinstance(hooks.get("events"), dict) else hooks
    have = set(events.keys()) if isinstance(events, dict) else set()
    miss = [e for e in cfg["events"] if e not in have]
    if miss:
        # 事件集合非空但与期望不符 → ⚠（疑似改名/升级），不是"确定坏"
        warns.append("缺关键事件: %s（事件集合与基线不符 —— 疑似客户端改名；基于 %s）"
                     % (", ".join(miss), cfg["basis"]))

    if problems:
        return "bad", problems, [c for _, c in gate]
    if warns:
        return "warn", warns, [c for _, c in gate]
    return "ok", [], [c for _, c in gate]


# --------------------------------------------------------------- 项目级配置
# 为什么要单列（2026-09-21）：`~/.zcode/cli/config.json` 这份**用户级**配置早就装好了，
# 但 33 个受管仓里一度有 32 份**项目级** `zcode.json`。它们是同一段 JSON 被复制进每个仓，
# 于是带上了复制那台机器的绝对路径 —— 且**从来没能工作过**：
#   ① 32/32 连 JSON 都不合法（`"C:\Users\..."` 写了单反斜杠，`\U`/`\u` 非法转义）；
#   ② 就算补成双反斜杠，schema 也是 Claude/Codex 的扁平形态，与 ZCode 的 `.strict()` 对不上；
#   ③ 客户端参数写的是 `codex`，按本仓 `integrations/zcode/README.md` §2 必须是 `zcode`。
# 而**没有任何判据看过它们** —— 体检器只扫 `--home` 下的用户级路径。这一节补的就是这个盲区。
#
# 两级的判据**故意不同**（不是复制一份检查）：
#   用户级 = 本机事实，绝对路径是**正常的**（它就是给这台机器的）；
#   项目级 = 随仓分发，同事 clone 走以后必须仍然可用 ⇒ 写死本机路径即失效，判 ❌。
PROJECT_RELS = ("zcode.json", ".zcode/config.json")

_MACHINE_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|(?<![\w])~[\\/])[^\s\"';|]*")


def machine_paths_in(text):
    """从配置原文里挑出**写死的本机绝对路径**（Windows 盘符 / 家目录 ~ 两种形态）。

    按原文扫而不是按解析后的 dict 扫：JSON 不合法时也得能报出"它写死了哪台机器的路径"
    —— 那 32 份正是这个状态，解析路径上什么都拿不到。
    """
    return sorted({m.group(0).rstrip("\\/") for m in _MACHINE_PATH_RE.finditer(text)})


def _client(name):
    for c in CLIENTS:
        if c["name"] == name:
            return c
    raise KeyError(name)


def managed_repo_paths():
    """受管仓的**本机**路径（来自本机覆盖层 `config/harness.local.yaml`）。

    换台机器 / 同事 clone：本机覆盖层可能不存在或没配全 → 自动缩到 0 条，本节静默为空，
    **不会**因为"我这儿没有客户仓"而报假红。这正是 `_config` 的设计（共享层只声明键名）。
    """
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, os.path.join(root, "harness"))
        import _config
        cfg = _config.load() or {}
    except Exception:
        return []
    repos = cfg.get("repos") or {}
    return sorted(v for v in repos.values() if isinstance(v, str) and v and os.path.isdir(v))


def project_level_findings(repo_paths, home=None):
    """→ [{"config", "path", "level", "problems"}]，level ∈ ok / bad。

    跑的是**同一套** `judge_config_file`（非法 JSON / 客户端参数 / 钩子存在 / 关键事件），
    再叠一条项目级专属规则：写死本机绝对路径 ⇒ ❌（理由随消息一起打出来）。
    """
    home = home or os.path.expanduser("~")
    zcfg = _client("zcode")
    findings = []
    for base in repo_paths:
        for rel in PROJECT_RELS:
            p = os.path.join(base, rel.replace("/", os.sep))
            if not os.path.isfile(p):
                continue
            label = "%s/%s" % (os.path.basename(base.rstrip("/\\")) or base, rel)
            try:
                text = pathlib.Path(p).read_text(encoding="utf-8-sig", errors="replace")
            except Exception as e:
                findings.append({"config": label, "path": p, "level": "bad",
                                 "problems": ["读不了（%s）" % e]})
                continue
            lvl, probs, _ = judge_config_file(p, zcfg, home)
            if lvl == "skip":
                lvl, probs = "ok", []
            paths = machine_paths_in(text)
            if paths:
                lvl = "bad"
                probs.append("写死了本机绝对路径（%s …，共 %d 处）—— 项目级配置随仓分发，"
                             "同事 clone 走后必然失效。本项目的形态是**用户级** "
                             "~/.zcode/cli/config.json（由 sandbox/install_shim.py 渲染）"
                             % (paths[0][:48], len(paths)))
            findings.append({"config": label, "path": p, "level": lvl, "problems": probs})
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser(description="客户端钩子配置静态体检（无需 CLI）")
    ap.add_argument("--home", default=os.path.expanduser("~"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    rows = [(c["name"], c["host"]) + check_client(c, a.home) for c in CLIENTS]
    proj = project_level_findings(managed_repo_paths(), a.home)
    if a.json:
        out = [{"client": n, "host": h, "level": l, "problems": p} for n, h, l, p, _ in rows]
        # 保持顶层是**数组**（既有消费者按列表解析）；项目级条目用 host="project" 区分。
        out += [{"client": f["config"], "host": "project", "level": f["level"],
                 "problems": f["problems"]} for f in proj]
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        icon = {"ok": "✅", "warn": "⚠️", "bad": "❌", "skip": "⏸"}
        print("客户端钩子配置静态体检（不需要装 CLI，GUI-only 客户端也能查）")
        print("判据知识基线：%s（逐家出处见下）" % KNOWLEDGE_DATE)
        for name, host, lvl, probs, _ in rows:
            print("  %s %-10s [宿主 %s]" % (icon[lvl], name, host))
            for p in probs:
                print("       - %s" % p)
        if proj:
            print("\n  受管仓里的**项目级**配置（%d 份；判据与用户级不同 —— 它随仓分发，"
                  "写死本机路径即失效）" % len(proj))
            for f in proj:
                print("  %s %s" % (icon[f["level"]], f["config"]))
                for p in f["problems"]:
                    print("       - %s" % p)
        nbad = len([r for r in rows if r[2] == "bad"])
        nbad_proj = len([f for f in proj if f["level"] == "bad"])
        nskip = len([r for r in rows if r[2] == "skip"])
        nwarn = len([r for r in rows if r[2] == "warn"])
        # G4（评审 v5）：⚠ 必须单列 —— 原先算进 ✅，"待复核"在数字行里消失（假安心）。
        print("\n  合计 %d 家：✅ %d ｜ ⚠️ %d ｜ ❌ %d ｜ ⏸ 未配置 %d"
              % (len(rows), len(rows) - nbad - nskip - nwarn, nwarn, nbad, nskip))
        if proj:
            print("  另有项目级配置 %d 份（❌ %d）：撤掉即可 —— 用户级那份已生效，不需要替代品"
                  % (len(proj), nbad_proj))
    bad = any(r[2] == "bad" for r in rows) or any(f["level"] == "bad" for f in proj)
    return 1 if bad else 0    # ⚠ 不算失败（避免假红变成噪声）


if __name__ == "__main__":
    sys.exit(main())
