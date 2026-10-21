#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""受管仓 `AGENTS.md` 瘦身体检 —— **只读**。给"知识分层"的落地出判据。

为什么是这个形状（2026-09-19）
--------------------------------------------------------------------------------
「33 个受管仓瘦身」最初被当成一道**按端分类**的判断题（"PC 端模板缺归宿所以先别动，
后端/H5 全有归宿所以能删"）。那个判断**会过期**：PC 模板补上归宿的那天它就不成立，
后端/H5 冒出无归宿新行的那天它也不成立 —— 而过期的判据没人知道该重跑什么。

所以这里的判据是**按行，不按端**，提问只有一个：

    这一行，删了以后读者去哪儿找它？那个地方**当下**真的有这条东西吗？

  求得到 + 锚点当下求证得到  → 可删（diff 里逐行标注归宿）
  求不到（或锚点指空）        → **不删**，原样留在「本仓独有」，并进「待迁清单」

于是「PC 现在不能删 / 后端现在全能删」不是两种模式，而是同一张表在两个端的当前取值。
往 `sink_table.txt` 里加一组 `[归宿]/[行]`，重跑，那一行自动从「待迁」挪进「可删」——
本文件一行都不用改。而「待迁清单」反过来就是**端级模板的待办**（现在缺的正是 PC 那几条）。

判据的两条安全侧（详见 sink_table.txt 头注）
--------------------------------------------------------------------------------
  ① 归一化后**精确相等**，不是子串 —— 差一个标点就落到「待迁」= 留而不删。
  ② 锚点要能在归宿文件里**逐字求证到**，否则报「指针指空」并按「待迁」处理。

本文件**没有任何写入路径**：不开 write 模式、不建目录、不碰受管仓。
落盘由人过目 diff 之后自己做（§六 ①：这类知识删了不可恢复）。

用法
--------------------------------------------------------------------------------
    python sandbox/agents_audit.py                    # 汇总 + 待迁清单
    python sandbox/agents_audit.py --diff demo_backend # 出某个仓的拟改 diff（只打印）
    python sandbox/agents_audit.py --json             # 机读
    python sandbox/agents_audit.py --sink <路径>       # 换一张归宿表（测试用）

退出码：0 = 正常；1 = 归宿表有问题（指空 / 解析失败）；2 = 用法错。
"""
import argparse
import collections
import difflib
import io
import json
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "harness"))
import _config  # noqa: E402  （零依赖 mini-yaml + 本机覆盖层）

SINK_DEFAULT = os.path.join(ROOT, "sandbox", "sink_table.txt")
TEMPLATE_DIR = os.path.join(ROOT, "golden-patterns", "templates")
ENDS = ("_pc", "_h5", "_backend")

# 结构行：目标形态自己会重排的骨架（小节标题、表格分隔行），不是知识，不需要归宿。
# 清单来自 2026-09-19 对 29 份实档的枚举；**没登记的结构行走「待迁」**（安全侧：留而不删）。
SCAFFOLD = frozenset([
    "## 项目", "## 门禁（机械强制）", "## 门禁（机械强制，无需记细节）",
    "## 核心约束", "## 核心约束（分层）", "## 核心约束（门禁管不到的判断层）",
    "## 文档", "## 详细文档（按需展开）", "## 详细文档（按需展开，别一次全读）",
    "## 验证", "## 目录（速记）",
    "| 需要什么 | 打开 |", "|---|---|",
])

# 随目标形态**搬迁**：有归宿，但归宿是目标形态自己的槽位（不是别的文件）——
# 所以既不该算「可删」（它的内容要留下），也不该进「待迁」（它不是缺口）。
MOVE_PREFIXES = ("知识库：",)

# 知识库根：维护机上的绝对路径 → `<VAULT>` 占位（目标形态见
# `golden-patterns/index.md` 的仓库结构约定）。
# 为什么必须换：它是**维护机**的路径，同事 clone 受管仓后这行在他机器上是死的 ——
# 与 `auto-gate.py` 那条本机路径同源，只是换了目录。2026-09-22 实测：
# 22 份产物**全部**还带着它（`<HARNESS>` 已换、这一类漏了）。
# 实测 29/29 个受管仓的 AGENTS.md 里只有这一种写法，所以单前缀替换是安全的。
VAULT_ROOTS = ("D:/team-vault",)


def norm(text):
    """把「本机事实」抹掉再比对：引擎仓绝对路径（两种斜杠写法）+ 空白。

    这一步不做，判据就只写在表的那台机器上成立 —— 同事 clone 到 `D:/dev/`，
    同一个仓的同一行会整片落进「待迁」。这正是本工程反复在治的
    「本机能跑、换机器就废」。
    """
    # 两种写法都要试：ROOT 由 os.path.abspath 得来，在 Windows 上是**反斜杠**形态；
    # 而受管仓 AGENTS.md 里写的是正斜杠。只替换其中一种 = 这条判据只在这台机器上成立，
    # 受管仓那 11 行会整片落进「待迁」（2026-09-19 首跑实测，就是这么发现的）。
    for form in (ROOT, ROOT.replace("\\", "/")):
        text = text.replace(form, "<H>")
    return " ".join(text.split())


def portable(line):
    """把知识库根的本机绝对路径换成 `<VAULT>` 占位。

    ⚠ 只做**前缀替换**、其余整段**逐字不动** —— 初版曾按反引号抠出路径再填，
    把 `（工单必读 `00_工单必读.md`）` 那半句静默丢了（见 `_target_lines` 头注），
    那才是真正的内容损失。这次只碰路径那一段，括号那句跟着原样留下。
    """
    for root in VAULT_ROOTS:
        line = line.replace(root, "<VAULT>")
    return line


def product_line(line):
    """**源文件里的一行**在产物里的形态 —— 唯一出处。

    两件事，缺一不可：
      · 摘掉已被新标题取代的 `知识库：` 标签（只对带标签的行）；
      · 把知识库根的本机路径换成 `<VAULT>` 占位（`portable()`）。

    为什么要收成一个函数（2026-10-05）
    --------------------------------------------------------------------------
    在此之前，`portable()` 只挂在 `_target_lines` 处理**搬迁行**的那一个分支上，
    首行标题 / `## 项目` 块 / 留守行三处都只做 `rstrip()` —— 同一条知识库路径，
    写在搬迁行里变成 `<VAULT>`，写在留守行里**逐字进产物**。7 个受管仓
    （多个客户端的端仓）是头一回踩到，而当时那把锁
    （夹具是 `知识库：` 开头的**搬迁**行）照旧全绿 —— 判据看着覆盖了"知识库路径"，
    实际放过了另一半。

    生成器的逐行自检（`agents_scaffold.lost_lines`）**也必须问这一个函数**：
    它此前手工镜像了这段式子（`_moved`），两边一漂就成了「自己给自己发合格证」。
    """
    tag = MOVE_PREFIXES[0]
    s = line.rstrip()[len(tag):] if line.strip().startswith(tag) else line.rstrip()
    return portable(s)


def _read_lines(path):
    with io.open(path, encoding="utf-8", errors="replace", newline="") as f:
        return f.read().replace("\r\n", "\n").split("\n")


# ---------------------------------------------------------------- 归宿表
def load_sink(path):
    """读归宿表 → (组列表, 错误列表)。

    组 = {"file", "anchor", "why", "lines"}。解析失败**必须出声**（不阻塞，但也不静默）：
    静默的后果是"整表当空"= 一个都不删，方向安全，可使用者会以为"审下来就是没得删"。
    那条教训在 `checks/h5_ruleset.py::_load_allowlist` 里写着（评审 v8 Codex P1）。
    """
    groups, errs = [], []
    if not os.path.isfile(path):
        return [], ["归宿表不存在：%s" % path]
    cur = None
    for n, raw in enumerate(_read_lines(path), 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("[归宿] "):
            cur = {"file": s[len("[归宿] "):].strip(), "anchor": None, "why": "", "lines": []}
            groups.append(cur)
        elif s.startswith("[锚点] "):
            if cur is None:
                errs.append("%s:%d [锚点] 出现在 [归宿] 之前" % (path, n))
                continue
            cur["anchor"] = s[len("[锚点] "):].strip()
        elif s.startswith("[为什么] "):
            if cur is not None:
                cur["why"] = s[len("[为什么] "):].strip()
        elif s.startswith("[行] "):
            if cur is None:
                errs.append("%s:%d [行] 出现在 [归宿] 之前" % (path, n))
                continue
            cur["lines"].append(norm(s[len("[行] "):]))
        else:
            errs.append("%s:%d 不认识的行（前缀只能是 [归宿]/[锚点]/[为什么]/[行]）：%s"
                        % (path, n, s[:60]))
    for g in groups:
        if not g["anchor"]:
            errs.append("[归宿] %s 缺 [锚点]（没锚点就无从求证）" % g["file"])
        if not g["lines"]:
            errs.append("[归宿] %s 一条 [行] 都没有（这组是死的）" % g["file"])
    return groups, errs


def verify(groups, errs):
    """归宿表自检：每条锚点**当下**能不能在它指的那个文件里逐字找到。

    这是防「指针指空」的那一手 —— 和 `global_agents.stale_note` 同类：
    改了模板措辞却忘了同步本表，就要有个声音，而不是"审下来没得删"。
    """
    checked = []
    for g in groups:
        p = os.path.join(ROOT, g["file"])
        if not os.path.isfile(p):
            checked.append((g, False, "归宿文件不存在"))
            continue
        with io.open(p, encoding="utf-8", errors="replace") as f:
            ok = g["anchor"] in f.read()
        checked.append((g, ok, "" if ok else "指空：在 %s 里找不到「%s」" % (g["file"], g["anchor"])))
    for g, ok, why in checked:
        if not ok:
            errs.append(why)
    return checked


# ---------------------------------------------------------------- 逐仓判定
def _customer(key):
    for e in ENDS:
        if key.endswith(e):
            return key[:-len(e)]
    return key


def _end(key):
    for e in ENDS:
        if key.endswith(e):
            return e[1:]
    return "?"


def split_blocks(lines):
    """按小节切块 → [(标题或 None, [内容行]), ...]。标题归属它**下面**的那些行。

    为什么要分块：一个标题只在它下面**所有**内容行都能走时才能跟着删。
    否则会出现「`## 核心约束（门禁管不到的判断层）` 没了，里面那几条被挪进
    `## 本仓独有` 成了无主孤行」—— 那是提案自己制造的知识损失。
    """
    blocks, head, body = [], None, []
    for i, l in enumerate(lines):
        if i == 0:                       # 首行是标题，另算（永远逐字保留）
            continue
        if l.lstrip().startswith("#"):
            if head is not None or body:
                blocks.append((head, body))
            head, body = l, []
        else:
            body.append(l)
    if head is not None or body:
        blocks.append((head, body))
    return blocks


def judge(key, path, sink, bad_files):
    """判一个仓 → {"drop": [...], "stay": [...], "move": [...], "keep_lines": [...]}"""
    lines = _read_lines(path)
    drop, stay, move, scaf, keep = [], [], [], [], []
    for head, body in split_blocks(lines):
        h_norm = norm(head) if head is not None else None
        b_drop, b_stay, b_move, b_scaf = [], [], [], []
        for l in body:
            s = norm(l)
            if not s:
                continue
            if s in SCAFFOLD:
                b_scaf.append(l)          # 结构行：目标形态自己会重排，无所谓「归宿」
            elif s.startswith(MOVE_PREFIXES):
                b_move.append(l)          # 有归宿，归宿是目标形态自己的槽位
            elif s in sink and sink[s]["file"] not in bad_files:
                b_drop.append(l)
            else:
                b_stay.append(l)
        # 标题只在它下面**所有**内容行都能走时才跟着删；块里还剩留守行就留下它，
        # 否则会出现「标题没了、里面那几条被挪进本仓独有成了无主孤行」——
        # 那是提案自己制造的知识损失。
        if h_norm is not None and (h_norm not in SCAFFOLD or b_stay):
            keep.append(head)
        keep += b_stay
        drop += b_drop
        stay += b_stay
        move += b_move
        scaf += b_scaf
    return {"key": key, "path": path, "end": _end(key), "customer": _customer(key),
            "drop": drop, "stay": stay, "move": move, "scaf": scaf, "keep_lines": keep}


def target_form(lines, end, keep_lines, move_lines):
    """§三 目标形态：标题 + 项目 + 端级规则（指针）+ 本仓独有 + 项目知识库。

    为什么 `## 项目` 要单独提到前面（2026-09-21）
    --------------------------------------------------------------------------
    `## 项目` 在 `SCAFFOLD` 里（它是骨架标题），但它下面那句「EHS 综合管理平台 PC 端，
    Vue3 + …。712 = 712 所」是**留守行** —— 于是 `judge()` 按「块里还有留守行，标题必须
    留下」把标题留了下来。原样拼进目标形态，就成了：

        ## 本仓独有（同事的机器上不会有的知识）
        ## 项目
        EHS 综合管理平台 PC 端，… 712 = 712 所。

    两句读起来是「这条项目身份**只有这个仓有**」—— 意思反了。这类仓（A 组 22 个）的
    唯一留守内容就是这句项目身份，它不是"本仓独有"，不该挂在那顶帽子底下。
    所以按标题把 `## 项目` 那一块**整块**提到 `## 端级规则` 之前；块里逐字不动。
    """
    proj, rest, in_proj = [], [], False
    for l in keep_lines:
        if l.lstrip().startswith("#"):
            in_proj = (norm(l) == "## 项目")
        (proj if in_proj else rest).append(l)
    return _target_lines(lines, end, proj, rest, move_lines)


def _target_lines(lines, end, proj, rest, move_lines):
    """拼装本体（`proj` = 已摘出的 `## 项目` 块，`rest` = 其余留守行）。

    `keep_lines` / `move_lines` **逐字保留**（逐字保留：551 行业务独有必须原样保住）——
    只做 rstrip 与"去掉已被新标题取代的 `知识库：` 标签"，不重排、不加前缀、
    不从行里再抠任何片段：`1. …` / `| a | b |` / `## …` 混在一起，任何"统一成 - 列表"
    都是提案自己对原文的改写。

    （初版在这里从 `知识库：` 行里按反引号抠出路径填进去，于是
     `` 知识库：`…/acme/`（工单必读 `00_工单必读.md`） `` 的**括号那句被静默丢了** ——
    正是 §六 ① 说的那类损失。现在整行留，只摘标签。）

    ★ 2026-10-05：出自源文件的行**一律在末尾过 `product_line()` 收口一次**，不再逐处调用。
    两个理由：① 原先 `portable()` 只挂在**搬迁行**那个分支上，首行标题 / `## 项目` /
    留守行三处会**漏网**（知识库根原样出货，见 `product_line` 头注）；② 收口一处之后，
    "将来新加一段输出"没法绕过它。骨架行（`## 端级规则` 等）本身不含知识库根，过一遍是空操作。
    """
    out = [lines[0], ""]
    if proj:
        out += list(proj) + [""]
    out += ["## 端级规则",
            "见 `<HARNESS>/golden-patterns/templates/AGENTS_%s_slim.md`"
            "（入口地图 + 端级规则 + 规则 ID 指针；本端通用）" % end, "",
            "## 本仓独有（同事的机器上不会有的知识）", ""]
    out += list(rest) or ["（待补 —— 目前没有）"]
    out += ["", "## 项目知识库", ""]
    out += list(move_lines) or ["`<VAULT>/concepts/projects/<项目>/`"]
    while out and not out[-1].strip():
        out.pop()
    # 唯一收口处：出自源文件的行在这里统一变成产物形态（摘标签 + `<VAULT>` 占位）。
    return [product_line(l) for l in out]


# ---------------------------------------------------------------- 报告
def collect(sink_path=None):
    groups, errs = load_sink(sink_path or SINK_DEFAULT)
    checked = verify(groups, errs)
    bad = {g["file"] for g, ok, _ in checked if not ok}
    sink = {}
    for g in groups:
        for l in g["lines"]:
            sink.setdefault(l, g)

    cfg = _config.load() or {}
    repos = {k: v for k, v in (cfg.get("repos") or {}).items() if isinstance(v, str)}
    here = {k: v for k, v in repos.items() if v}
    unset = sorted(set(repos) - set(here))

    present, missing = {}, []
    for k, base in sorted(here.items()):
        p = os.path.join(base, "AGENTS.md")
        (present.__setitem__(k, p) if os.path.isfile(p) else missing.append(k))

    judged = [judge(k, p, sink, bad) for k, p in present.items()]
    return {"groups": groups, "checked": checked, "errors": errs, "sink": sink,
            "judged": judged, "missing": sorted(missing), "unset": unset,
            "total_repos": len(repos)}


def print_report(a, reps):
    print("=" * 78)
    print("① 归宿表自检（每条归宿的锚点都要能在它指的文件里逐字找到）")
    print("=" * 78)
    for g, ok, why in reps["checked"]:
        print("   %s %-44s ← 「%s」" % ("✅" if ok else "❌", g["file"], g["anchor"]))
    n_bad = sum(1 for _, ok, _ in reps["checked"] if not ok)
    print("   → %d 条归宿，%d 条指空（指空的那几行按「待迁」处理，不会误删）"
          % (len(reps["checked"]), n_bad))
    for e in reps["errors"]:
        if "指空" not in e:
            print("   ⚠ %s" % e)

    tot = collections.Counter()
    for r in reps["judged"]:
        for b in ("drop", "stay", "move", "scaf"):
            tot[b] += len(r[b])
    print()
    print("=" * 78)
    print("② 汇总：%d 个受管仓（本机配了路径的 %d 个，其中 %d 个有 AGENTS.md、%d 个没有）"
          % (reps["total_repos"], reps["total_repos"] - len(reps["unset"]),
             len(reps["judged"]), len(reps["missing"])))
    print("=" * 78)
    print("   可删行（归宿求证到了）              %4d" % tot["drop"])
    print("   搬迁行（进目标形态的 `## 项目知识库`） %4d" % tot["move"])
    print("   结构行（目标形态自己会重排）          %4d" % tot["scaf"])
    print("   待迁行（没有归宿 → 原样留下）        %4d" % tot["stay"])
    if reps["missing"]:
        print("   ⚠ 没有 AGENTS.md（先接入再谈瘦身）：%s" % "、".join(reps["missing"]))
    if reps["unset"]:
        print("   · 本机没配路径（未参与）：%s" % "、".join(reps["unset"]))

    owner = collections.defaultdict(set)
    for r in reps["judged"]:
        for l in r["stay"]:
            owner[norm(l)].add(r["customer"])
    shared = {k: v for k, v in owner.items() if len(v) >= 2}
    print()
    print("=" * 78)
    print("③ 待迁清单 —— 没有归宿的行（按影响仓数降序；这就是**端级模板的待办**）")
    print("=" * 78)
    for s, ks in sorted(shared.items(), key=lambda x: (-len(x[1]), x[0])):
        print("   [%2d 仓] %s" % (len(ks), s[:100]))
    print("   （另有 %d 条只出现在一个受管仓里 = 真·本仓独有，本来就该留下）"
          % (len(owner) - len(shared)))
    print()
    print("   补归宿的方法：往 sandbox/sink_table.txt 加一组")
    print("     [归宿] <端级模板> / [锚点] <要能求证到的串> / [为什么] … / [行] <上面某一行>")
    print("   重跑本命令即可 —— 那一行会自动从③挪进②的「可删行」。")


def print_diff(a, reps, key):
    hit = [r for r in reps["judged"] if r["key"] == key]
    if not hit:
        sys.stderr.write("没有这个仓（或它本机没配路径 / 没有 AGENTS.md）：%s\n" % key)
        return 2
    r = hit[0]
    lines = _read_lines(r["path"])
    new = target_form(lines, r["end"], r["keep_lines"], r["move"])
    print("=" * 78)
    print("④ `--diff %s` —— 拟改成（**未落盘**；§六 ①：逐仓出 diff 给人过目）" % key)
    print("=" * 78)
    # 补回行尾：`_read_lines` 是 split("\n")，直接喂给 unified_diff 会拼成一整行
    sys.stdout.writelines(difflib.unified_diff(
        [l + "\n" for l in lines], [l + "\n" for l in new],
        fromfile="a/%s/AGENTS.md" % key, tofile="b/%s/AGENTS.md" % key, n=1))
    print()
    print("   删掉的每一行去哪儿找：")
    seen = set()
    for l in lines:
        s = norm(l)
        if s in reps["sink"] and s not in seen:
            seen.add(s)
            g = reps["sink"][s]
            print("   « %-56s → %s（「%s」）" % (s[:56], g["file"].split("/")[-1], g["anchor"]))
    if not seen:
        print("   （这个仓一行可删的都没有 —— 看③里它缺什么）")
    return 0


def scan_unresolved_harness(reps):
    """扫仓级 AGENTS.md 里残留的 `<HARNESS>` 占位符（未渲染）。

    统一解析链收口后，仓级 AGENTS.md 应该由渲染器替换掉 `<HARNESS>`。
    如果还有残留，说明渲染没跑过或跑失败了。
    """
    print()
    print("=" * 78)
    print("⑤ 未渲染的 `<HARNESS>` 占位符（统一解析链收口后不应出现）")
    print("=" * 78)

    found = []
    for r in reps["judged"]:
        path = r["path"]
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue

        # 找 <HARNESS> 出现的位置
        lines_with_placeholder = []
        for i, line in enumerate(content.split("\n"), 1):
            if "<HARNESS>" in line:
                lines_with_placeholder.append((i, line.strip()[:80]))

        if lines_with_placeholder:
            found.append((r["key"], r["customer"], path, lines_with_placeholder))

    if not found:
        print("   ✅ 无残留（所有仓级 AGENTS.md 的 `<HARNESS>` 都已渲染）")
        return

    for key, customer, path, lines in found:
        print("    %s (%s) — %s" % (key, customer, path))
        for lineno, snippet in lines:
            print("      L%d: %s" % (lineno, snippet))

    print()
    print("   修复：重跑渲染（`python sandbox/agents_scaffold.py --emit <目录>`）")
    print("   或手动把 `<HARNESS>` 替换成实际路径（从 config/harness.local.toml 的 harness: 键取）")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="受管仓 AGENTS.md 瘦身体检（只读；按「这一行有没有归宿」判定，不按端）")
    ap.add_argument("--diff", metavar="仓键名", help="出某个仓的拟改 diff（只打印，不落盘）")
    ap.add_argument("--sink", metavar="路径", help="归宿表路径（默认 sandbox/sink_table.txt）")
    ap.add_argument("--json", action="store_true", help="机读输出")
    a = ap.parse_args(argv)

    reps = collect(a.sink)

    if a.json:
        print(json.dumps({
            "sink_errors": reps["errors"],
            "sinks": [{"file": g["file"], "anchor": g["anchor"], "why": g["why"],
                       "lines": len(g["lines"])} for g in reps["groups"]],
            "repos": [{"key": r["key"], "customer": r["customer"], "end": r["end"],
                       "drop": len(r["drop"]), "stay": len(r["stay"]), "move": len(r["move"])}
                      for r in reps["judged"]],
            "missing_agents_md": reps["missing"],
        }, ensure_ascii=False, indent=2))
    elif a.diff:
        rc = print_diff(a, reps, a.diff)
        if rc:
            return rc
    else:
        print_report(a, reps)
        scan_unresolved_harness(reps)  # ⑤ 扫未渲染的 <HARNESS>

    return 1 if reps["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
