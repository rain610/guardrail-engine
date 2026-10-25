#!/usr/bin/env python
"""改码后自动跑 harness 门禁 —— Claude Code / Codex 通用 hook。

用途：让 CLI agent（Claude Code、Codex）**改完代码立即过一遍 harness 门禁**，
不合格就把问题回灌给模型让它自己修（不用等提交时才发现）。

用法（由各自的 hook 配置调用，不要手动跑）：
    python agentguard-gate.py claude     # Claude Code 的 PostToolUse
    python agentguard-gate.py codex      # Codex 的 PostToolUse / Stop

行为：
  - 从 hook 的 stdin JSON 取 cwd（拿不到就用进程 cwd）
  - 跑 `harness/agents/auto-gate.py`（按目录自动识别受管仓/端 gate）
  - rc=0 通过 / rc=3 非受管仓库 → 静默 exit 0
  - 拦截 → Claude Code: stderr + exit 2（Claude 会把 stderr 当反馈紧接着改）
            Codex: 输出 {"hookSpecificOutput":{...additionalContext...}} 并 exit 0
  - 任何异常/超时 → 静默放行（hook 绝不能卡死或误拦 agent）

设计取舍：
  - 只在"改动文件所属的受管仓库"里跑（auto-gate 自己判定），未纳管目录零开销。
  - 超时默认 180s（HARNESS_HOOK_TIMEOUT 可调）；宁可漏报也不拖死会话。
    **读 stdin 也有看门狗**（HARNESS_HOOK_STDIN_TIMEOUT，默认 10s）—— 那道 subprocess 超时
    只包得住"跑门禁"这一步，包不住"等宿主把 JSON 写完"。
  - 不做阻塞式重试循环——是否继续修由模型自己决定，避免 agent 死循环。
  - 2026-10-15 批次 4（可见性）：每次真实调用更新 `~/.agentguard/hook-activity-<client>.json`
    （心跳，覆盖式、不刷屏）；写入型事件提取不到文件 → `hook-skips-YYYY-MM.jsonl` 落一行；
    payload 捕获由 `~/.agentguard/hook-dump.on` 开关（默认脱敏）。目录可用
    `HARNESS_HOOK_ACTIVITY_DIR` 覆盖（测试隔离）。缘由：IDE 形态下「钩子没被调」与
    「调了提取失败被静默放行」在台账上**不可区分**（CodeBuddy 对照实验实测）。
"""
import json
import os
import subprocess
import sys
import threading
import time

# 宿主给我们的 stdio 可能是 Windows 中文控制台的 **GBK**（cp936）——那时打印中文/✗/✅ 会抛
# UnicodeEncodeError，钩子整体崩掉、结论彻底丢失（Trae CN 2026-09-13 实测踩到：
# Exit Code 1 + `'gbk' codec can't encode character '\u2717'`，模型侧表现为"未遇到任何检查"）。
# 这里一律切成 UTF-8 且 errors=replace —— 编码再怪也只是个别字符变问号，绝不因此丢结论。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_RESOLVE_WARNED = [False]   # G3/Q3：**每进程**只喊一次；GUI 客户端的每个钩子事件都是新进程，
                            # 因此「找不到工程根」时会每个事件可见一次 —— 刻意持续可见，不是 bug。

_ENV_WARNED = set()         # 同类：环境变量写坏时每进程只提示一次（见 _env_positive_int）


def _env_positive_int(name, default):
    """读一个**正整数**环境变量；写坏了就回退默认值 + stderr 提示一次，**绝不因此崩**。

    评审 2026-09-23 L11：原先写作 `TIMEOUT = int(os.environ.get("HARNESS_HOOK_TIMEOUT", "180"))`
    —— 这是**模块级**求值，于是 `HARNESS_HOOK_TIMEOUT=3m`（手滑多打一个字母）会让整个钩子
    import 期就抛 ValueError。后果不是"超时配错了"，而是七个客户端的编辑期门禁**全部失效**，
    而用户看到的现象只是"改了下配置，门禁就不响了" —— 这类"坏得没人知道"的失败正是本工程
    最想消灭的。`0` / 负数同样回退（subprocess 的 timeout=0 会立刻超时 ⇒ 等于永不检查）。
    """
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except ValueError:
        val = None
    if val is None or val <= 0:
        if name not in _ENV_WARNED:
            _ENV_WARNED.add(name)
            try:
                sys.stderr.write("[guardrail-engine] 环境变量 %s=%r 不是正整数，已回退默认值 %d"
                                 "（本次仍照常跑，不会因此失效）\n" % (name, raw, default))
            except Exception:
                pass
        return default
    return val


def _resolve_harness():
    """定位 agentguard 工程根（**可移植**：换机器/换 clone 目录都不用改本文件）。

    顺序：① 环境变量 AGENTGUARD_DIR（旧名 HARNESS_DIR 同样可用）；② 指针文件 ~/.agentguard/harness.path（内容=工程根）；
    ③ 常见位置；④ 从当前目录向上找 agentguard/。
    全都找不到 → 返回 None，调用方**静默放行**——宁可不过门禁，也绝不因为"没装 harness"
    把同事的编辑器/agent 卡住（这条是分享给同事时的硬要求）。
    """
    cands = []
    # G3（评审 v5）：先加"脚本自身位置派生根" —— 本文件就住在 <harness>/integrations/hooks/ 下，
    # 上溯 3 层即工程根（shim 用 runpy 转发时 __file__ 同样指向正本）。静态可得、零配置，
    # 让"全链失败"从常见情况降级为真正的异常情况。
    try:
        cands.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    except Exception:
        pass
    env = (os.environ.get("GUARDRAIL_ENGINE_DIR") or os.environ.get("AGENTGUARD_DIR") or os.environ.get("HARNESS_DIR") or "").strip()
    if env:
        cands.append(env)
    for ptr in (os.path.join(os.path.expanduser("~"), ".guardrail-engine", "harness.path"),
                os.path.join(os.path.expanduser("~"), ".agentguard", "harness.path"),
                os.path.join(os.path.expanduser("~"), ".agentguard.path")):
        try:
            with open(ptr, encoding="utf-8") as f:
                v = f.read().strip()
            if v:
                cands.append(v)
        except OSError:
            pass
    cands.append(os.path.expanduser("~/guardrail-engine"))
    d = os.path.abspath(os.getcwd())
    for _ in range(6):
        cands.append(os.path.join(d, "guardrail-engine"))
        cands.append(os.path.join(d, "agentguard"))   # 旧 clone 目录名
        cands.append(d)
        d = os.path.dirname(d)
    partial = None    # K8：像工程根、但缺 auto-gate.py（clone 半残/被误清理）
    for c in cands:
        try:
            if os.path.isfile(os.path.join(c, "harness", "agents", "auto-gate.py")):
                return os.path.abspath(c)
            # K8（评审 v6）：本脚本自己就住在 <harness>/integrations/hooks/ 下 —— 该目录存在
            # 说明"这就是工程仓"，但 auto-gate.py 不在 → 真异常（不是"没装 harness"），
            # 不能和"未安装"一样静默（那会让门禁半残却无人知晓）。
            if partial is None and os.path.isfile(os.path.join(c, "integrations", "hooks",
                                                              "agentguard-gate.py")):
                partial = os.path.abspath(c)
        except OSError:
            continue
    if partial and not _RESOLVE_WARNED[0]:
        _RESOLVE_WARNED[0] = True
        sys.stderr.write("[guardrail-engine] 工程根看起来是 %s，但缺 harness/agents/auto-gate.py："
                         "本次放行不阻塞，但门禁**没有生效**，请检查工程仓完整性\n" % partial)
    # G3：全链失败仍**不阻塞**（"没装 harness 不卡同事"是硬承诺），但也不再完全静默 ——
    # 出声一次（stderr），把"门禁这次没生效"讲明白，避免同事以为一直在被检查。
    if not _RESOLVE_WARNED[0]:
        _RESOLVE_WARNED[0] = True
        sys.stderr.write(
            "[guardrail-engine] 未找到工程根（GUARDRAIL_ENGINE_DIR / AGENTGUARD_DIR / HARNESS_DIR / 指针 / 常见位置 / 向上搜索 / 脚本自身位置均未命中）："
            "本次**放行不阻塞**；如需门禁请设置 GUARDRAIL_ENGINE_DIR（旧名 AGENTGUARD_DIR / HARNESS_DIR 兼容）或写 ~/.guardrail-engine/harness.path\n")
    return None


HARNESS = _resolve_harness()
GATE = os.path.join(HARNESS, "harness", "agents", "auto-gate.py") if HARNESS else None
TIMEOUT = _env_positive_int("HARNESS_HOOK_TIMEOUT", 180)
STDIN_TIMEOUT = _env_positive_int("HARNESS_HOOK_STDIN_TIMEOUT", 10)
MAX_LINES = 40          # 回灌给模型的输出上限，避免把上下文撑爆


def _stdin_watchdog(seconds):
    """起一个看门狗线程：`seconds` 秒内没读完 stdin 就**静默放行**（exit 0），并把事件返回给调用方。

    评审 2026-09-23 L11：`_read_payload` 原先是一次裸的 `sys.stdin.read()`。契约上调用方写完
    JSON 就关掉 stdin，但这条**不是我们能保证的** —— 宿主把管道留在打开状态时（GUI 客户端异常
    退出、Trae 那层 PowerShell 包装、或者干脆有人手跑），read 会一直等下去，会话就卡在这里。
    而上面那个 TIMEOUT **管不到这一步**：它只包住 subprocess 那一段（跑门禁），读 stdin 发生在
    它之前。宁可漏一次检查，也不能把 agent 卡死 —— 这是本钩子的第一承诺。
    用线程而不是 select：Windows 上 select 只对套接字有效（stdin 不是）。
    """
    done = threading.Event()

    def _fire():
        if not done.wait(seconds):
            try:
                sys.stderr.write("[guardrail-engine] 等宿主写完 hook 的 stdin 超过 %d 秒，"
                                 "本次按「放行不检查」处理（避免把会话卡死）\n" % seconds)
                sys.stderr.flush()
            except Exception:
                pass
            os._exit(0)          # 直接从看门狗线程结束进程（不跑 finally、不等别的线程）

    threading.Thread(target=_fire, name="stdin-watchdog", daemon=True).start()
    return done


def _read_stdin_text():
    """把 stdin 读成字符串（带看门狗）；读不到 / 超时被看门狗接管时返回 ""。"""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return ""            # 交互终端：不读，避免等人输入而挂住
    except (ValueError, OSError):
        return ""
    done = _stdin_watchdog(STDIN_TIMEOUT)
    try:
        return sys.stdin.read() or ""
    except Exception:
        return ""
    finally:
        done.set()               # 读完了就撤掉看门狗（后面跑门禁有自己的 TIMEOUT）


def _read_payload():
    """读 hook 的 stdin JSON；读不到就返回空 dict（不阻塞）。

    第三轮 N-ζ：契约上调用方（Claude Code / Codex）会写一段 JSON 并**关闭** stdin；
    但工程自己在 harness_guard 上踩过「stdin 非 EOF → 静默挂死」的 P0，所以加三道保护：
    ① 交互终端（人工直接跑脚本）不读；② 读失败/无数据一律当空 payload；③ 读带看门狗（L11）。
    """
    raw = _read_stdin_text()
    if not raw.strip():
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _compact(text):
    """把回灌给模型的内容压进 MAX_LINES 行。

    ⚠ **头尾都要留**（2026-09-15 CI 红，4 条钩子契约锁同时红）：原先只留末尾
    `lines[-MAX_LINES:]`，而调用处拼的是 `stdout + "\\n" + stderr` —— 诊断
    （`[config-warn]` / `[config-policy]`）全在**末尾**，检查结论
    （`✗ <文件>: <问题>`）在最前面。同事机器/CI 上没 clone 各客户端仓时，
    `_config._sanity_check` 会为每个 gate 各发一条"仓库路径不存在"，30+ 条诊断把 40 行
    的窗口**整段占满** → 第 3 行的真实命中被当成"中间"省略 → 模型只收到"门禁未通过"，
    却看不到到底哪一行要改（= 门禁白跑）。头尾对半留，结论与诊断都保得住。
    """
    lines = [l.rstrip() for l in (text or "").splitlines() if l.strip()]
    if len(lines) > MAX_LINES:
        head_n = MAX_LINES // 2                  # 省略标记占 1 行，其余头尾对半
        tail_n = MAX_LINES - 1 - head_n
        elided = len(lines) - head_n - tail_n
        lines = lines[:head_n] + ["…（省略中间 %d 行）…" % elided] + lines[-tail_n:]
    return "\n".join(lines)


# 从工具调用里尽力取出"这次到底改了哪个文件"（编辑期精确口径要用它）。
# 结构化客户端（Claude/OpenCode）把参数放在 tool_input 里；**ZCode 是扁平的**
# （2026-09-13 实证：它整个 bundle 里 "tool_input" 出现 0 次，而 "file_path" 12 次，
#  其读写工具的 zod 参数就是顶层 file_path/content）——所以嵌套与顶层都要看，
# 否则接 ZCode 会"钩子跑了但取不到文件"→ 静默不检查。
# Codex 在 Windows 上只给 PowerShell 命令，按常见写法从命令里提取；
# 全都取不到就交回 Stop 兜底（不是静默放松）。
_PATH_KEYS = ("file_path", "filePath", "filepath", "path", "target")
_NEST_KEYS = ("tool_input", "toolInput", "input", "arguments", "args", "params", "tool_arguments")
# 显式"文件参数"型 cmdlet 旗标 —— 这几个旗标本身就是"在写一个文件"的信号，全客户端通用。
_CMD_PATH_PATTERNS = (
    r"""(?:-LiteralPath|-Path|--file|-File|-FilePath)\s+['"]([^'"]+)['"]""",
    r"""(?:-LiteralPath|-Path|--file|-File|-FilePath)\s+([^\s'";|>&]+)""",
)
# 重定向目标（`> out.txt` / `>> log`）**只在"已判定像写操作"的语境里**才算被编辑文件，
# 也就是只给 Codex（见下）。
# ★ 2026-09-30 修（`/dev/null` 事故）：这条原先**对所有客户端**生效，于是
#   `python x.py -v > /tmp/t.log 2>&1`、`... 2>/dev/null` 这类**只读命令**里的重定向目标
#   会被当成"被编辑的文件" → `--only /tmp/t.log`（或 `/dev/null`）→ auto-gate 一个都匹配
#   不上 → rc=2 fail-closed → **每一个带重定向的命令**都往模型回灌一条
#   "harness 门禁调用出错 rc=2（多半是配置问题）"。模型于是被反复教"门禁坏了"，
#   而真正的毛病在**提取**这一步、不在配置（2026-09-30 实测复现，见 tests/engine/test_entry_level.py）。
# 为什么只给 Codex：它的写入就是 shell 命令（Windows 上只有 PowerShell），且在调用本函数
# 之前已经过了 `WRITE_HINTS`（`> ` / `>>` 都在表里）—— 即"重定向出现在**已判定像写操作**
# 的命令里"才是它的语境。ZCode 的写入走 Write/Edit 工具（带 file_path、**没有 command**），
# 根本不需要从命令里捞路径；它只复用 Codex 的**输出契约**，不复用命令判据（同 main() 那段）。
# ★ 同批修掉的第二个缺陷（同日实测）：原写作 `(?:>|>>)\s*['"]?([^\s'";|&]+)` ——
#   ① 字符类没排除 `>`，而正则里 `>` 先于 `>>` 匹配，于是 `echo x >> src/a.vue` 里
#      第一个 `>` 被当成操作符、剩下的 `>` 被当成**文件名**，`target_file` 返回 `'>'`
#      （实测三例全中：`>>` / `Add-Content >>` / `2>>`）→ `--only <仓>/>` → 一个都匹配
#      不上 → 同一套 rc=2 假警报。② 顺序也反了（长的在前才对）。
_CMD_REDIRECT_PATTERN = r"""(?:>>|>)\s*['"]?([^\s'";|&>]+)"""


def target_file(payload, client=""):
    """尽力从 hook payload 里取出被编辑的文件路径（取不到返回 ""）。

    兼容两类形态：嵌套（tool_input.file_path…）与扁平（file_path 直接在顶层，如 ZCode）。
    `client` 只影响命令兜底那一档的宽严：只有 codex 允许把**重定向目标**当成被编辑文件。
    """
    import re as _re

    dicts = []
    for k in _NEST_KEYS:
        v = payload.get(k)
        if isinstance(v, dict):
            dicts.append(v)
    dicts.append(payload)          # 扁平形态兜底（ZCode 等）

    for d in dicts:
        for k in _PATH_KEYS:
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()

    pats = list(_CMD_PATH_PATTERNS)
    if client == "codex":
        pats.append(_CMD_REDIRECT_PATTERN)
    for d in dicts:
        cmd = d.get("command") or d.get("cmd")
        if isinstance(cmd, str) and cmd:
            for pat in pats:
                m = _re.search(pat, cmd)
                if m:
                    return m.group(1).strip()
    return ""


def in_tree(path, root):
    """`path` 是否落在 `root` 目录树内（Windows 大小写不敏感；两边先绝对化 + normcase）。

    ★ 2026-09-30 修（`/dev/null` 事故的机制层）：`--only` 的值最终是交给
    `auto-gate --repo <cwd>` 用的，所以**指到仓外的目标必然"一个都匹配不上"** →
    rc=2 fail-closed → 回灌"门禁调用出错 rc=2（多半是配置问题）"。不管上游是哪种
    提取规则出了问题，这里都该按"取不到文件"处理：编辑期放行，交给回合末 Stop
    全仓兜底（与"取不到就交回 Stop"同一口径 —— 不是放松，是**别报假警**）。
    """
    try:
        p = os.path.normcase(os.path.abspath(path))
        r = os.path.normcase(os.path.abspath(root))
    except (OSError, ValueError):
        return False
    if p == r:
        return True
    return p.startswith(r.rstrip(os.sep + (os.altsep or "")) + os.sep)


def _repo_root_for(path):
    """从**文件路径**反推它所属的 git 工作树根（先问 git，再向上找 `.git`）。

    为什么需要（批次 6，CodeBuddy IDE 抓包实测）：部分客户端的 payload `cwd` 是**自己的
    安装目录**、不是工作区（实测 `D:\\...\\Programs\\CodeBuddy CN`）—— 拿它当仓根，
    `in_tree` 与 `--repo` 两条全错，编辑期门禁 100% 静默放行；而被编辑文件的路径是
    **绝对路径、两种工具（Write/Edit）都能取到**。返回 None = 文件不在任何 git 仓内
    （例：重定向到 /tmp 的目标），调用方按旧判据兜底。
    """
    d = os.path.dirname(os.path.abspath(path))
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY")}
    if os.path.isdir(d):
        try:
            r = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=15, env=env)
            if r.returncode == 0 and (r.stdout or "").strip():
                return os.path.abspath((r.stdout or "").strip())
        except Exception:
            pass
    cur = d
    for _ in range(40):        # 兜底：向上找 .git（目录或文件 —— worktree/submodule 是文件）
        if os.path.isdir(os.path.join(cur, ".git")) or os.path.isfile(os.path.join(cur, ".git")):
            return cur
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt
    return None


# ── 批次 4（2026-10-15）：钩子可见性（心跳 / 跳过记录 / payload 捕获）──────────────
# 病灶（CodeBuddy 对照实验，台账实证）：IDE 形态 PostToolUse 零记录 —— 而「钩子没被调」
# 与「调了但提取失败被静默放行」在台账里长得一模一样（两者都不写台账），漏跑无法定位。
# 三件小机制（全部 best-effort，绝不阻塞/绝不抛）：
#   ① 心跳：每次真实调用**覆盖写** ~/.agentguard/hook-activity-<client>.json（文件极小）
#   ② 跳过：写入型事件提取不到文件 → hook-skips-YYYY-MM.jsonl 追加一行（60s/客户端节流）
#   ③ payload 捕获：开关文件存在时存每客户端最近一份（长字符串截断，不记全文）
# 隐私：活动/跳过只记决策与路径；dump 默认脱敏、开关关掉即完全不写盘。

_ACTIVITY_DIR = (os.environ.get("HARNESS_HOOK_ACTIVITY_DIR")
                 or os.path.join(os.path.expanduser("~"), ".guardrail-engine"))


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _activity(client, event, decision, target="", tool=""):
    """心跳：每次真实调用覆盖写一份每客户端状态（不刷屏，不记代码内容）。

    `tool`（批次 6 增）：记下工具名 —— 复核跳过记录时用它区分「Read 类噪音（本就没有
    文件）」与「Write/Edit 真漏」（CodeBuddy 复核时点名要的区分）。
    """
    try:
        os.makedirs(_ACTIVITY_DIR, exist_ok=True)
        p = os.path.join(_ACTIVITY_DIR, "hook-activity-%s.json" % client)
        prev = {}
        try:
            with open(p, encoding="utf-8") as f:
                prev = json.load(f) or {}
        except Exception:
            pass
        today = _now()[:10]
        rec = {"day": today,
               "count": (int(prev.get("count", 0)) + 1) if prev.get("day") == today else 1,
               "last_ts": _now(), "last_event": event, "last_decision": decision}
        if target:
            rec["last_target"] = str(target)[:300]
        if tool:
            rec["last_tool"] = str(tool)[:40]
        with open(p, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
    except Exception:
        pass


def _skip_note(client, event, reason, detail="", tool=""):
    """写入型事件提取不到文件 → 落一行跳过记录（同一客户端 60s 内只记一条，防刷屏）。"""
    try:
        os.makedirs(_ACTIVITY_DIR, exist_ok=True)
        stamp = os.path.join(_ACTIVITY_DIR, ".hook-skip-%s.stamp" % client)
        try:
            if time.time() - os.path.getmtime(stamp) < 60:
                return
        except OSError:
            pass
        with open(stamp, "w") as f:
            f.write("1")
        line = {"ts": _now(), "client": client, "event": event, "decision": reason}
        if tool:
            line["tool"] = str(tool)[:40]
        if detail:
            line["detail"] = str(detail)[:200]
        with open(os.path.join(_ACTIVITY_DIR, "hook-skips-%s.jsonl" % _now()[:7]),
                  "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass


# 写类工具名提示（大小写不敏感的子串）。判不准的一律**当写**（宁可多记）：诊断价值最高的
# 正是「未知形态」，而 60s/客户端 的节流把噪声封顶。
_WRITE_TOOL_HINTS = ("write", "edit", "patch", "notebook", "create", "replace", "save",
                    "insert", "append", "delete", "remove", "rename", "move",
                    "bash", "shell", "cmd", "powershell", "command", "terminal")


def _tool_name(payload):
    """尽力取工具名（嵌套/扁平两种形态都认），取不到返回空串。"""
    ti = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    return str(payload.get("tool_name") or payload.get("toolName")
               or (ti.get("tool_name") if isinstance(ti, dict) else "") or "")


def _looks_like_write(payload):
    """本事件是否像一次写操作（tool_name 含写类动词）；取不到工具名 → 当写（记下来）。"""
    tn = _tool_name(payload)
    if not tn:
        return True
    low = tn.lower()
    return any(k in low for k in _WRITE_TOOL_HINTS)


def _dump_payload(client, event, payload):
    """payload 捕获（仅当开关文件存在）：每客户端**最近一份**，默认脱敏（长串截断）。"""
    try:
        if not os.path.isfile(os.path.join(_ACTIVITY_DIR, "hook-dump.on")):
            return

        def _redact(v, depth=0):
            if isinstance(v, dict):
                return {k: _redact(x, depth + 1) for k, x in v.items()}
            if isinstance(v, list):
                return [_redact(x, depth + 1) for x in v[:20]]
            if isinstance(v, str) and len(v) > 200:
                return "<len %d>" % len(v)
            return v

        os.makedirs(_ACTIVITY_DIR, exist_ok=True)
        with open(os.path.join(_ACTIVITY_DIR, "hook-payload-last-%s.json" % client),
                  "w", encoding="utf-8") as f:
            json.dump({"ts": _now(), "event": event, "argv": sys.argv[1:],
                       "payload": _redact(payload)}, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


# HARNESS-KILLTREE-BEGIN（跨文件契约：本块与另一端**逐字节相同**；对拍锁 tests/engine/test_agentguard.py）
# 2026-10-15 批次 7（实测病灶）：`subprocess.run(timeout=...)` 超时只 kill **直接子进程** ——
# 孙进程（例：cli→run_gate→检查脚本里的最后那个）变孤儿继续跑：一次「300s 超时」的全量扫描
# 实测孤儿又跑了约 100 分钟，父进程阻塞在管道 EOF 上，最终输出全丢。
def _kill_tree(proc):
    """杀掉 proc 及其**整棵进程树**（超时路径专用）。

    Windows: `taskkill /F /T`（按父子链杀整棵）；POSIX: 子进程须自成进程组
    （spawn 时 start_new_session=True），再 killpg。
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


def _communicate_tree(p, timeout):
    """带「超时杀整树」的 communicate → (out_bytes, err_bytes)；超时抛 TimeoutExpired。

    ★ 杀树之后必须**再 communicate 一次**收尾：管道写端被孙进程持有时，只杀不读仍会
    阻塞在读端（旧实现卡死 100 分钟的直接机制）。二次收尾给 30s 上限。
    """
    try:
        return p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        try:
            p.communicate(timeout=30)
        except Exception:
            pass
        raise
# HARNESS-KILLTREE-END


class _GResult(object):
    """hook 内部给 (rc, out, err) 一个与 `subprocess.run` 同形的壳（下游按属性取用；
    批次 7 从 run 换成 Popen+杀树后，调用点零改动）。"""
    __slots__ = ("returncode", "stdout", "stderr")

    def __init__(self, rc, out, err):
        self.returncode, self.stdout, self.stderr = rc, out, err


def main():
    client = sys.argv[1] if len(sys.argv) > 1 else "claude"
    event = sys.argv[2] if len(sys.argv) > 2 else "PostToolUse"   # PostToolUse / Stop
    payload = _read_payload()
    cwd = payload.get("cwd") or os.getcwd()
    _dump_payload(client, event, payload)   # 批次 4：开关开启时留证（默认不写盘）
    if not GATE or not os.path.isdir(cwd) or not os.path.isfile(GATE):
        _activity(client, event, "skip-no-harness")
        return 0          # 找不到 harness 工程 / 环境不符 → 静默放行（不拦同事）
    # 批次 3（2026-10-15）：来源归因 —— 编辑器钩子原先一律塌缩成 entry=auto-gate，
    # 「哪家客户端、哪个事件」在台账里分不出来（CodeBuddy 漏跑排查现场正卡在这）。
    # 硬赋值（不是 setdefault）：外层残留值不许冒充本次来源；auto-gate 会记成
    # entry=auto-gate + via=hook:codebuddy:PostToolUse（测试：TestHookEntryAttribution）。
    os.environ["HARNESS_ENTRY"] = "hook:%s:%s" % (client, event)
    # Codex 侧的省流判据（2026-09-12 实测定型）：Codex 在 Windows 上把 shell 工具统一叫
    # `Bash`，但**命令内容其实是 PowerShell**（实测见到 Write-、here-string、> 重定向）。
    # 若每条 shell 命令都跑门禁（读目录也跑），一个会话会白等几十秒；所以按命令特征判断
    # "这条命令是否真会改文件"，不像写操作就直接放行。
    # ⚠️ 这段"省流判据"**只适用于 Codex**：Codex 在 Windows 上写入都走 shell 命令，
    # 所以"没有 command 就不是写操作"成立。ZCode 不是这样——它的写入是 Write/Edit 工具
    # （带 file_path、没有 command），套进来会被一律放行（2026-09-13 实测踩到：
    # 测试里 flat/nested 两种 payload 都静默 exit 0，门禁根本没跑）。
    # ZCode 只复用 Codex 的**输出契约**（下方 hookSpecificOutput），不复用它的命令判据。
    if client == "codex" and event != "Stop":
        ti = payload.get("tool_input") or {}
        cmd = str((ti.get("command") if isinstance(ti, dict) else "") or "")
        if not cmd:
            _activity(client, event, "skip-codex-no-cmd")
            return 0                       # 拿不到命令（非 shell 工具）→ 放行
        # 判据按"实测见过的真实写入形态"维护（漏判的代价是这次编辑不过门禁，由回合结束的
        # Stop 钩子兜底；误判的代价只是多跑一次门禁——所以宁可写宽一点）。
        # 2026-09-12 实测漏过一例：Codex 用 .NET API 直写
        # `[System.IO.File]::WriteAllText(...)`，此前没有任何关键词命中 → 已补。
        WRITE_HINTS = ("Begin Patch", "apply_patch", ">>", "> ", "Set-Content", "Add-Content",
                       "Out-File", "New-Item", "Write-", "@'", "tee ", "sed -i",
                       "python -c", "node -e", "git apply", "Remove-Item", "Copy-Item",
                       "Move-Item", "ni ",
                       # PowerShell / .NET 直写文件（Codex 在 Windows 上的常见形态）
                       "[System.IO.File]", "WriteAll", "AppendAll", "WriteAllText",
                       "::WriteAllLines", "New-Object System.IO", "Clear-Content",
                       "Set-Item", "Rename-Item")
        if not any(k in cmd for k in WRITE_HINTS):
            _activity(client, event, "skip-codex-not-write")
            return 0
    # 编辑期精确口径（2026-09-13 用户拍板）：PostToolUse 只检查被编辑的那个文件。
    #   · 拿到文件 → auto-gate --only <file>（不再把整仓未提交改动算进来）
    #   · 拿不到文件（Codex 命令形态没命中提取规则）→ 编辑期直接放行，由本回合结束的
    #     Stop 钩子做**全仓**兜底检查（兜底不放松，只是推迟到回合末）
    #   · Stop（回合结束）→ 保持全仓收集
    #   · PreToolUse + 文件**尚不存在** → 放行（新建场景，见下）
    gate_cmd = [sys.executable, GATE]
    tf = ""                     # 批次 4：Stop 分支不赋值；提取失败/降级时保持空（供 ran 心跳用）
    repo_root = cwd             # 仓根：有文件时按文件路径反推（批次 6），否则沿用 payload cwd
    if event != "Stop":
        tf = target_file(payload, client)
        if not tf:
            # 批次 4（2026-10-15）：不再纯静默 —— 心跳记决策 + 写入型事件落跳过记录。
            # （“写入型”由 _looks_like_write 近似；判不准当写 → 记下来，读类过滤掉防刷屏。）
            _activity(client, event, "skip-no-file", tool=_tool_name(payload))
            if _looks_like_write(payload):
                _skip_note(client, event, "no-file", tool=_tool_name(payload))
            if os.environ.get("HARNESS_HOOK_FALLBACK_SCAN") == "1":
                # 降级：全仓扫（默认关。理由：会话里提取失败若频繁，每次 ~8s 会拖慢 IDE；
                # 先在实验里量清楚 miss 频率，再决定要不要默认打开）。
                gate_cmd += ["--repo", cwd]
            else:
                return 0
        else:
            # 评审 2026-09-23 M11：`--only` 的值在钩子侧**归一成绝对路径**再交出去。
            # 它是宿主给的原始字符串，可能是相对路径；而 auto-gate 用 `os.path.isfile()` 判定，
            # 基准是**子进程 cwd**（= 下面的 payload cwd）。opencode 插件实测就踩在这上面：
            # 它把 cwd 设成"被编辑文件的父目录"、把 filePath 原样转发，于是 `src/a.vue`
            # 被解析成 `<仓>/src/src/a.vue` → 一个都匹配不上 → rc=2，编辑期门禁整条失效，
            # 回灌给模型的还是"门禁调用出错（多半是配置问题）"。
            # 「相对谁」的答案就是 payload cwd（与下面 subprocess 的 cwd 同值），所以拿它归一。
            # 这一步同时是下面那条 PreToolUse 判据的前提：否则要在**钩子进程**的 cwd
            # （宿主启动目录，可能与 payload cwd 不同）上判存在性 —— 存在却判成不存在就会
            # **静默放行**，比不判还坏。
            if not os.path.isabs(tf):
                tf = os.path.join(cwd, tf)
            # 批次 6（2026-10-15，CodeBuddy IDE 抓包实测）：仓根**优先从文件路径反推** ——
            # 有些客户端的 payload.cwd 是**自己的安装目录**而不是工作区（CodeBuddy IDE
            # 实测 `D:\...\Programs\CodeBuddy CN`），旧实现拿 cwd 当仓根：① `in_tree` 判
            # “不在树内” ② `--repo cwd` 指向安装目录 ⇒ **编辑期门禁 100% 静默放行**。
            # 被编辑文件是绝对路径、两种工具都能取到，反推仓根才是稳的那个。
            root = _repo_root_for(tf)
            if root:
                repo_root = root
            elif not in_tree(tf, cwd):
                # ★ 2026-09-30 修：仓外的目标按"取不到文件"处理（理由见 in_tree 的 docstring）。
                # 反推不到仓根（文件不在任何 git 仓内，例：重定向到 /tmp 的目标）+ 不在
                # payload cwd 树内 → 维持旧判据跳过。注意这**不**掩盖 M11 那类"路径形态
                # 不对"的毛病：`<仓>/src/src/a.vue` 是**仓内**的错路径，仍然照吵。
                _activity(client, event, "skip-out-of-tree", target=tf,
                          tool=_tool_name(payload))
                return 0
            # 评审 2026-09-23 M10：PreToolUse 阶段"文件不存在"= **新建场景**（写之前它当然不在），
            # 放行。不放行的话 `--only` 一个都匹配不上 → auto-gate rc=2 → qoder / trae / codebuddy
            # 的 PreToolUse deny 分支会把**新建任何文件**都拒掉，理由还写成"门禁调用出错
            # rc=2（多半是配置问题）"—— 拦得不对，理由也是错的（实测三种客户端都是 deny）。
            # 为什么是放行而不是"给个专门的错"：这一步本来也无内容可查（文件还不存在），
            # 内容门禁的职责在写完之后的 PostToolUse、以及回合末的 Stop（兜底不放松）。
            # 只在这一个事件上放行：PostToolUse 撞到不存在的文件仍按 rc=2 出声（见 M11 那条 ——
            # 那种"路径形态不对"的毛病必须吵出来，不许被这里顺手掩盖）。
            if event == "PreToolUse" and not os.path.isfile(tf):
                _activity(client, event, "skip-new-file-pretooluse", target=tf,
                          tool=_tool_name(payload))
                return 0
            gate_cmd += ["--repo", repo_root, "--only", tf]
    try:
        _p = subprocess.Popen(gate_cmd, cwd=cwd, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              start_new_session=(os.name != "nt"))
        _ob, _eb = _communicate_tree(_p, TIMEOUT)
        r = _GResult(_p.returncode,
                     (_ob or b"").decode("utf-8", "replace"),
                     (_eb or b"").decode("utf-8", "replace"))
    except subprocess.TimeoutExpired:
        _activity(client, event, "skip-hook-timeout")  # 批次 7：超时单独一档（≠崩溃）
        return 0                                   # 超时一律放行（hook 不拦路）
    except Exception:
        _activity(client, event, "skip-hook-error")   # 批次 4：异常也要留痕
        return 0                                   # 异常一律放行（hook 不拦路）
    _activity(client, event, "ran:rc=%d" % r.returncode,
              target=(repo_root if (event == "Stop" or not tf) else tf),
              tool=_tool_name(payload))
    out = _compact((r.stdout or "") + "\n" + (r.stderr or ""))
    if r.returncode in (0, 3):
        return 0                                   # 0=通过 3=非受管仓库
    head = "harness 门禁未通过（自动跑于你本次改动之后）" if r.returncode == 1 \
        else f"harness 门禁调用出错 rc={r.returncode}（多半是配置问题，别当业务错）"
    # ★ 2026-09-30 有意**不再**在这里教「行内豁免」（用户的安全关切，别"好心"加回来）：
    # 本段文案是**每一次失败都出现、7 家客户端通用**的那一句 —— 最像"门禁自己的口吻"。
    # 注入通道若可被伪造，攻击者就能借这句话诱导 agent 把**任意**命中行合法豁免。
    # 豁免语法因此只住在**可信文档**：工程根 `AGENTS.md` 的「行内豁免」一节
    # （它本来就在 agent 的上下文里，正常用法一点没少）。门禁输出里不再复述语法。
    msg = f"{head}\n{out}\n\n按上面的 ✅ FIX 提示改到通过。"
    if client == "qoder":
        # Qoder 契约（2026-09-13 实证：它自带的 better-harness 插件
        # scripts/agent-guardrails/platforms.mjs 里的 renderQoderBlock 就是官方适配层）：
        #   · PreToolUse → stdout JSON {"hookSpecificOutput":{"hookEventName":"PreToolUse",
        #                   "permissionDecision":"deny","permissionDecisionReason":…}} + **exit 0**
        #   · 其它事件（PostToolUse/Stop）→ stdout JSON {…"additionalContext":…} + stderr 明文 + **exit 0**
        #     （★ 原写「exit 2 + 消息打到 stdout」—— 那是**改判前**的口径，见下面那段实测；
        #      2026-09-23 更正本注释：注释停在旧口径上，下一个人会照它"修"回去。）
        # 配置位置：用户级 ~/.qoder/settings.json（本机 ~/.qoder-cn/settings.json）
        #          或项目级 <repo>/.qoder/settings.json，根键 "hooks"。
        if event == "PreToolUse":
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": msg}}, ensure_ascii=False))
            return 0
        # 非 PreToolUse（PostToolUse/Stop）：**实测改判为 JSON 契约 + exit 0**。
        # 原先按"官方文档 exit 2 → stderr 回灌"实现（stdout+stderr 双写 + exit 2），
        # 2026-09-13 在 Qoder CLI 实测发现它把 exit 2 当**钩子失败**：
        #   Qoder 日志原文：
        #     "Blocking hook failure(s) [...gate.py qoder PostToolUse: unknown error, exit code 2,
        #      stderr: harness 门禁未通过（自动跑于你本次改动之后）…]"
        #   且此时钩子其实**已经跑过门禁、结论也确实进了 stdout/stderr**，但模型侧
        #   会话段（~/.qoder-cn/logs/sessions/…/segments/*.jsonl）里搜不到任何门禁结论
        #   → 结论没回灌（判定器说"未见回灌证据"是对的）。
        # 所以改走 CodeBuddy/Codex 同族的 JSON 通道 + exit 0；stderr 明文保留（给人看/留档，无副作用）。
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": event, "additionalContext": msg}}, ensure_ascii=False))
        print(msg, file=sys.stderr)
        return 0
    if client == "trae":
        # Trae CN 契约（2026-09-13 实测）：宿主在 **PowerShell** 里执行钩子命令，
        # 官方口径是 exit 2 = 阻断 + stderr 作为反馈回灌 Agent；但它包的那层 PowerShell
        # 管道会把非 0 退出码**吞成 1**（实测：正本直跑 rc=2，经 PowerShell 管道变 rc=1），
        # 于是 Trae 只当"非阻断错误"，模型什么都看不到（会话记忆原话："未遇到任何自动检查"）。
        # 所以这里三路齐发：stderr 明文（官方口径）+ stdout JSON（hookSpecificOutput，
        # 与 CodeBuddy/Codex 同族，Trae 若认 JSON 也能拿到）+ exit 2（命令里另补
        # `; exit $LASTEXITCODE` 保证它在 PowerShell 下仍是 2）。
        if event == "PreToolUse":
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": msg}}, ensure_ascii=False))
            return 0
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": event, "additionalContext": msg}}, ensure_ascii=False))
        print(msg, file=sys.stderr)
        return 2
    if client == "codebuddy":
        # CodeBuddy 契约（2026-09-13 从它的 dist 实证）：**非 0 退出码只把 stderr 给用户看，
        # 不回灌模型**（原文：Other exit codes - show stderr to user only）；回灌模型要走
        # stdout 的 JSON。实测确认：加钩子后门禁确实被触发（日志 rc=1），但模型看不到结论——
        # 根因就是这个。所以这里照它的契约回 JSON（exit 0）：
        #   · PreToolUse → permissionDecision=deny（真阻断，用于"危险命令"类前置判断）
        #   · 其它事件 → additionalContext（回灌模型，编辑后自修）
        if event == "PreToolUse":
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": msg}}, ensure_ascii=False))
        else:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": event, "additionalContext": msg}}, ensure_ascii=False))
        return 0
    if client == "opencode":
        # OpenCode 插件契约（2026-09-13 实测补）：插件需要**真实的门禁退出码**才能分清
        # "门禁未通过"与"门禁调用出错"——而 hook 自身的退出码恒为 2（表"阻断"），两者都盖成 2。
        # 所以这里回一段 JSON（含 gate_rc），插件据此决定怎么回灌模型。
        print(json.dumps({"agentguard": {"gate_rc": r.returncode, "message": msg}},
                         ensure_ascii=False))
        return 0
    if client in ("codex", "zcode"):
        # Codex 输出契约（官方文档）：PostToolUse 支持 hookSpecificOutput.additionalContext；
        # 而 Stop 只支持通用字段（continue/stopReason/systemMessage）——给 Stop 回
        # additionalContext 会被判 "hook run failed"（实测踩过：Stop Failed）。
        if event == "Stop":
            print(json.dumps({"systemMessage": msg}, ensure_ascii=False))
        else:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": event, "additionalContext": msg}}, ensure_ascii=False))
        return 0
    # Claude Code: exit 2 → stderr。**只走这一路**：曾试过"再打一份 stdout JSON"以便被
    # 「从 Claude 导入」到别家时兜底，但实测破坏了 Claude Code 自身的回灌（端到端从 ✅ 变 no），
    # 而 Claude 是主力客户端 → 不为假想的导入场景牺牲它（导入场景改为关掉 Trae 的导入开关解决）。
    print(msg, file=sys.stderr)
    return 2


def _trace_contract(argv, raw_stdin, rc, out_text, err_text):
    """可选：把"这次钩子实际收到的 payload 结构 + 走的输出契约"记一行指纹。

    Q3（2026-09-13 外部评审 v3）：7 家 × 三端的钩子契约原先靠"人工读过 + 合成 payload 锁"，
    客户端一升级就可能漂移而无人知。`HARNESS_CONTRACT_TRACE=1`（或给一个文件路径）时记一行：
      {ts, client, event, rc, payload_keys, stdout: {kind, json_keys}, stderr: {kind}}
    **只记结构，不记内容/消息文本** —— 指纹要能跨机器、跨消息文案稳定。
    """
    import io as _io
    import time as _time
    tgt = (os.environ.get("HARNESS_CONTRACT_TRACE") or "").strip()
    if not tgt:
        return
    rec = {"ts": _time.strftime("%Y-%m-%dT%H:%M:%S"), "client": argv[1] if len(argv) > 1 else "?",
           "event": (argv[2] if len(argv) > 2 else "PostToolUse"), "rc": rc,
           "payload_keys": [], "stdout": _shape(out_text), "stderr": _shape(err_text)}
    try:
        obj = json.loads(raw_stdin or "{}")
        if isinstance(obj, dict):
            rec["payload_keys"] = sorted(obj.keys())
            ti = obj.get("tool_input")
            if isinstance(ti, dict):
                rec["payload_keys"] += ["tool_input." + k for k in sorted(ti.keys())]
    except Exception:
        rec["payload_keys"] = ["<unparsable>"]
    # F6（评审 v4）：默认写进工程仓的 reports/（已 gitignore）——原先写在脚本旁，被 git add -A 带进仓过。
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "reports", "contract_trace.jsonl")
    if tgt not in ("1", "true", "yes"):
        path = tgt
    try:
        with _io.open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _shape(text):
    """输出形态：空 / 纯文本 / JSON（给出键路径），不记消息正文。"""
    t = (text or "").strip()
    if not t:
        return {"kind": "empty"}
    try:
        obj = json.loads(t)
    except Exception:
        return {"kind": "text"}
    keys = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.append(k)
            if isinstance(v, dict):
                keys += ["%s.%s" % (k, kk) for kk in sorted(v.keys())]
    return {"kind": "json", "json_keys": sorted(keys)}


def _traced_main():
    """包一层：把 stdin 读成字符串（喂给 main 再喂一份给 trace），并把 stdout/stderr 探下来。"""
    import io as _io
    if not (os.environ.get("HARNESS_CONTRACT_TRACE") or "").strip():
        return main()
    raw = _read_stdin_text()      # 同上：带看门狗，别在这里把会话挂住
    buf_o, buf_e = _io.StringIO(), _io.StringIO()
    real_o, real_e, real_i = sys.stdout, sys.stderr, sys.stdin
    try:
        sys.stdin = _io.StringIO(raw)
        sys.stdout, sys.stderr = buf_o, buf_e
        rc = main()
    finally:
        sys.stdout, sys.stderr, sys.stdin = real_o, real_e, real_i
    o, e = buf_o.getvalue(), buf_e.getvalue()
    try:                                   # 原样透传给客户端（行为不变）
        sys.stdout.write(o); sys.stdout.flush()
        sys.stderr.write(e); sys.stderr.flush()
    except Exception:
        pass
    _trace_contract(sys.argv, raw, rc, o, e)
    return rc


if __name__ == "__main__":
    sys.exit(_traced_main())
