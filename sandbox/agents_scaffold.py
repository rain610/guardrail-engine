#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A 组客户仓 `AGENTS.md` 骨架**生成器** —— 与体检器分居两个文件，落盘只落 `--emit` 指的目录。

为什么不并进 `agents_audit.py`
--------------------------------------------------------------------------------
`agents_audit.py` 的头注里有一句是它的**契约**：「本文件没有任何写入路径」。
那是 §六 ① 的机械保证 —— 审阅工具一旦能写，就没人敢在客户仓旁边跑它了。
所以生成器另开一个文件，**只 import 它的判定**（`judge` / `target_form` / `load_sink` /
`product_line`），一条判据都不重写：判据漂移的代价是「审下来能删的行」和「真删的行」
是两张不同的表。
★ `product_line` 是 2026-10-05 从本文件搬过去的：逐行自检原先在本地**镜像**了一份
"源行 → 产物行"的换算式子（`_moved`），镜像漂了就成了「自己给自己发合格证」。

A 组是什么（2026-09-21 实测）
--------------------------------------------------------------------------------
29 份受管仓 `AGENTS.md` 天然分成两簇，中间**没有中间态**：

    stay=1, move=1   22 个仓   ← 本文件处理的对象
    stay>=11, move=0  7 个仓   ← 真有本仓知识（各客户端的三端系列）

22 个仓的正文**逐字节只差三处客户字段**（首行标题 / `## 项目` 那句 / `知识库：` 路径），
骨架完全同形。所以这是**一件生成器活，不是 22 份手改 diff**。
但它仍然服从 §六 ①：**本程序绝不写客户仓**，只往 `--emit` 指的目录里产出，
逐仓 diff 由人过目后自己决定落不落盘。

三道不许越的线
--------------------------------------------------------------------------------
  ① **不写客户仓**：`--emit` 的目录若落在任一受管仓内 → 直接拒绝（连同子目录）。
  ② **不许丢知识**：落盘前逐行自检 —— `keep_lines` 与 `move_lines` 的每一行（搬迁行去掉
     已被新标题取代的 `知识库：` 标签后）都必须在产物里**逐字**找得到；差一行这一仓就不写。
     这是 §六 ①「这类知识删了不可恢复」的机械执行。
  ③ **不动表格行**：产物里 `## 本仓独有` 是个纯段落槽位，而目标形态会把 `|---|---|` 这类
     结构行丢掉。所以 `keep_lines` 里只要**还有表格行**，这一仓就判为不合格（见 `_table_row`）——
     否则会产出「表体还在、表头没了」的孤儿表。拒绝比产出好看。

产物凭什么可信（2026-10-04）
--------------------------------------------------------------------------------
上面三道线管的是**生成的那一刻**。它管不到另一件事，而那件事真发生过（2026-10-02 实测）：

    `D:/Rain/agents_scaffold_out/` 那 22 份产物，是**对着更早的源**生成的 ——
    源文件后来补的那半句，产物里没有。而**没有任何东西会报出来**：
    产物看上去完全正常，② 的逐行自检也照样过 —— 因为自检比的是「记录下来的那几行」，
    而那份记录本身就是旧的。判据再准也救不了：它只看**当下**的源，不看产物是哪来的。

所以每次 `--emit` 落盘时，同目录写一份 `_sources.json`（**出生证明**），逐仓记下
源的字节哈希、产物自己的字节哈希，外加一份**引擎自指纹**（生成器 / 判据模块 / 归宿表）——
产物也可能是对着更早的**引擎**生成的。`--verify <目录>` 拿它复核，回答的只有一个问题：

    这份产物，还是照着这台机器上**现在**那份源生成的吗？

判据分两层，**缺一层都答不了**：源指纹对上 → 源没换过；产物指纹对上 → 产物没被人手改过。
只记源不记产物，产物被手改就查不出来；只记产物不记源，源换了产物没重生成也查不出来。

两条取舍写在这里，免得下次被当成 bug：

  · **fail-closed**：但凡有一条没法确认是「当下的一致」，就当它不一致（源缺失 / 键在本机
    配置里找不到 / 目录里冒出没登记的仓，全算不合格）。同变异冒烟把 SKIP 当失败那一套。
  · **不存本机路径**：出生证明里只有哈希、字节数和**引擎仓相对路径**。存了绝对路径，
    这张证明就只在生成它的那台机器上读得懂（同 `agents_audit.py` 把维护机路径换成 `<VAULT>`
    的那个理由）。所以复核时仓路径现从本机覆盖层（`config/harness.local.yaml`）按**键名**重取。
  · **合并写，不覆盖**（2026-10-04 补）：`_sources.json` 与 `_manifest.md` 都是**逐仓**的文件，
    而一次 `--only k1` 只产出 k1 那一份。整写会把别的仓整片抹掉 —— 目录里它们的产物还在，
    于是两个文件互相打脸，读的人以为其余 21 个仓没了。**假警比漏报更贵**（它教人不再相信清单），
    所以两个文件都按「读进来 → 换掉本轮的段 → 写回」合并。

用法
--------------------------------------------------------------------------------
    python sandbox/agents_scaffold.py                     # = --plan：列候选与判据，不写任何东西
    python sandbox/agents_scaffold.py --plan --json        # 机读
    python sandbox/agents_scaffold.py --emit <目录> [--only k1,k2] [--force]
    python sandbox/agents_scaffold.py --emit <目录> --check # 只跑自检、不落盘（干跑；出生证明与逐行清单都不写）
    python sandbox/agents_scaffold.py --verify <目录>       # 复核产物是否还对着当下的源（只读）

退出码：0 = 正常；1 = 有仓被拒（越线 / 自检不过）、或复核有对不上的；2 = 用法错，
        或 `--verify` 指到一个没有出生证明的目录（**无凭可核也是错**，不许报绿 —— 2026-10-04
        之前生成的目录正属此类，重跑一次 `--emit` 即可补上）。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import os
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "harness"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _config          # noqa: E402
import agents_audit     # noqa: E402  （判定一律借它的，本文件不重写判据）

MARKER = ".agents_scaffold"     # 目录占用标记：只往自己造过的目录里写
SOURCES = "_sources.json"       # 出生证明：产物的「是哪份源、哪版引擎生的」（见头注）
MANIFEST = "_manifest.md"       # 逐行归宿清单：逐仓一份段，**合并写**（见 merge_manifest）
SCHEMA = 1                      # 出生证明的版本；改字段就+1，老证明按「不认识的形状」出声


# ---------------------------------------------------------------- 编码形态
def read_source(path):
    """→ (文本, 行尾, 有无 BOM)。**必须逐仓读**：29 份里 CRLF 是多数，
    但有 4 份是 LF（2026-09-21 实测），
    统一写 CRLF 会让那 4 个仓的 diff 变成「整文件重写」—— 审阅的人第一眼就看不下去。
    """
    with open(path, "rb") as f:
        raw = f.read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig", errors="replace")
    return text, ("\r\n" if "\r\n" in text else "\n"), bom


def write_like(path, lines, eol, bom):
    """按源文件的行尾与 BOM 落盘（产物一律以换行结尾，与 29 份源文件一致）。"""
    body = ("\n".join(lines) + "\n").replace("\n", eol)
    data = body.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    with open(path, "wb") as f:
        f.write(data)


# ---------------------------------------------------------------- 出生证明
def fingerprint(path):
    """→ 文件**原始字节**的 sha256（十六进制）。源与产物都用它，两边必须是同一个函数 ——
    分成两套哈希的话，"比对"看着还在，其实永远不相等（或者更糟：永远相等）。

    记字节而不是"内容"：产物要跟着源文件的行尾与 BOM 走（见 `write_like`），
    所以判"这份源还是不是当初那份"也必须按字节判 —— 只把 CRLF 换成 LF、正文一字未改，
    一样得报出来。
    """
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _engine_hashes(sink_path=None):
    """引擎自指纹：生成器 / 判据模块 / 归宿表。

    归宿表记成**相对引擎仓的路径 + 哈希**（`--sink` 指到仓外时 `sink_rel` 留 None，
    复核那一步会说明"无法复核"而不是假装对上了）—— 出生证明里一个本机路径都不许有。
    """
    sink = sink_path or agents_audit.SINK_DEFAULT
    rel = None
    try:
        r = os.path.relpath(sink, ROOT).replace("\\", "/")
        rel = None if r.startswith("..") else r
    except ValueError:          # 不同盘符 —— relpath 抛，不是"没有相对路径"
        pass
    return {"scaffold": fingerprint(os.path.abspath(__file__)),
            "audit": fingerprint(os.path.abspath(agents_audit.__file__)),
            "sink_rel": rel,
            "sink": fingerprint(sink) if os.path.isfile(sink) else None}


def _load_registry(outdir):
    """读已有的出生证明 → 逐仓记录（没有 / 读不出来 / 形状不认识 → 空表，**且出声**）。

    为什么要**合并**而不是每次重写：`--only k1` 重发一个仓时，别的仓的产物还躺在目录里
    却把它们的记录抹掉，复核就会把它们全报成"未登记" —— 那等于把一次正常的局部重发
    变成一片假警。（同一条理由，`_manifest.md` 原先也是整写 —— 那个毛病 2026-10-04
    一并修掉了，见 `merge_manifest`；两个文件现在都是合并写。）
    """
    p = os.path.join(outdir, SOURCES)
    if not os.path.isfile(p):
        return {}
    try:
        with io.open(p, encoding="utf-8") as f:
            d = json.load(f)
        src = d["sources"]
        if d["schema"] != SCHEMA or not isinstance(src, dict):
            raise ValueError("schema=%r" % d.get("schema"))
        return dict(src)
    except Exception as e:
        sys.stderr.write("⚠ 已有的 %s 读不出来（%s）—— 当作空表重记；"
                         "它记过的那些仓这一轮复核会报「未登记」\n" % (p, e))
        return {}


def _write_registry(outdir, sources, sink_path=None):
    d = {"schema": SCHEMA,
         "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
         "engine": _engine_hashes(sink_path),
         "sources": {k: sources[k] for k in sorted(sources)}}
    p = os.path.join(outdir, SOURCES)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
    return p


# ---------------------------------------------------------------- 候选判据
def _table_row(line):
    s = line.strip()
    return s.startswith("|") and s.endswith("|")


def lost_lines(new_lines, keep_lines, move_lines):
    """③ 不许丢知识：返回产物里**找不到**的那些行（非空才算）。

    ★ 比对的**两侧**都要换算成产物形态：产物那一侧（`new_lines`）本来就是，
    源那一侧（`keep_lines` + `move_lines`）必须换算过去再比。

    2026-10-05 之前，源那一侧**只换算搬迁行**（本地镜像的 `_moved`）。而 `target_form`
    自同日起对**每一条**出自源文件的行做换算（知识库根 → `<VAULT>` 占位，见
    `agents_audit.product_line`）—— 少换这一侧，留守行里带知识库根的仓会被**误判成
    丢知识**而整仓不写。那是**假警**：产物本来是对的，工具却拒绝出货。

    ★ 换算**只问 `agents_audit.product_line()` 这一个函数**，不再在本地镜像一式 ——
    自检若用另一套写法，就成了「自己给自己发合格证」（`_moved` 头注当年就是这么写的，
    而 2026-10-05 的留守行缺陷正撞在这条纪律上：两边都对，但只有一边覆盖了那一半）。
    """
    have = {l.rstrip() for l in new_lines}
    lost = []
    for l in list(keep_lines) + list(move_lines):
        s = agents_audit.product_line(l)
        if s and s not in have:
            lost.append(s)
    return lost


def candidates(reps, only=None):
    """→ [{"key", "path", "end", "customer", "drop", "stay", "move", "why"}...]

    判据两条（合起来正好圈出那 22 个，中间没有灰区）：
      · `stay <= 1` —— 本仓独有最多一行（就是那句客户身份），没有需要人判读的留守知识；
      · `keep_lines` 里没有表格行 —— 见头注③（表体留下、表头被结构行规则吃掉 = 孤儿表）。
    """
    out = []
    for r in reps["judged"]:
        if only is not None and r["key"] not in only:
            continue
        why = []
        if len(r["stay"]) > 1:
            why.append("本仓独有 %d 行（>1）—— 有真知识，走人判读，不自动生成" % len(r["stay"]))
        if any(_table_row(l) for l in r["keep_lines"]):
            why.append("留守行里有表格行 —— 目标形态会丢掉表头，会产出孤儿表")
        out.append({"key": r["key"], "path": r["path"], "end": r["end"],
                    "customer": r["customer"], "drop": r["drop"], "stay": r["stay"],
                    "move": r["move"], "keep_lines": r["keep_lines"], "why": why})
    return out


# ---------------------------------------------------------------- 越线检查
def managed_roots():
    """受管仓的本机路径（本机覆盖层）。换机器/没配全 → 空，检查自然通过。"""
    cfg = _config.load() or {}
    out = []
    for v in (cfg.get("repos") or {}).values():
        if isinstance(v, str) and v:
            out.append(os.path.normcase(os.path.realpath(v)))
    return sorted(set(out))


def inside_managed_repo(outdir):
    """→ 命中的那个仓路径，或 None。"""
    o = os.path.normcase(os.path.realpath(outdir))
    for r in managed_roots():
        if o == r or o.startswith(r.rstrip("\\/") + os.sep):
            return r
    return None


def safe_outdir(outdir, force=False):
    """① 不写客户仓 + 不写别人造过的目录。→ (ok, 原因)。"""
    hit = inside_managed_repo(outdir)
    if hit:
        return False, ("`--emit` 指到了受管仓里面（%s）—— §六 ①：本程序绝不写客户仓，"
                       "产物一律落在仓外的目录，逐仓 diff 由人过目后自己落盘" % hit)
    if os.path.isfile(outdir):
        return False, "`--emit` 指到了一个**文件**：%s" % outdir
    if os.path.isdir(outdir):
        left = [n for n in os.listdir(outdir) if n != MARKER]
        if left and not os.path.isfile(os.path.join(outdir, MARKER)) and not force:
            return False, ("目录非空且不是本程序造的（缺 %s 标记）：%s —— 换个空目录，"
                           "或确认后加 --force" % (MARKER, outdir))
    return True, ""


# ---------------------------------------------------------------- 产出
def build(r):
    """一个仓 → (产物行, 自检不过的行, 源行数)。"""
    with io.open(r["path"], encoding="utf-8", errors="replace", newline="") as f:
        lines = f.read().replace("\r\n", "\n").split("\n")
    new = agents_audit.target_form(lines, r["end"], r["keep_lines"], r["move"])
    # `_read_lines` 是 split("\n")：文件以换行结尾时最后会多出一个空元素，不算一行。
    n = len(lines) - (1 if lines and lines[-1] == "" else 0)
    return new, lost_lines(new, r["keep_lines"], r["move"]), n


def manifest(r, new, src_lines, sink_entries):
    """逐仓的「删了什么、去哪找」—— §六 ① 要求 diff 里逐行标注归宿。"""
    o = []
    o.append("## %s（%s）" % (r["key"], r["customer"]))
    o.append("")
    o.append("- 可删 %d 行 ｜ 搬迁 %d 行 ｜ 待迁 %d 行 ｜ 源 %d 行 → 产物 %d 行"
             % (len(r["drop"]), len(r["move"]), len(r["stay"]), src_lines, len(new)))
    o.append("")
    if r["drop"]:
        o.append("删掉的每一行去哪儿找：")
        o.append("")
        for l in r["drop"]:
            g = sink_entries.get(agents_audit.norm(l))
            if g:
                o.append("- `%s` → %s（「%s」）" % (l.strip()[:100], g["file"], g["anchor"]))
            else:
                o.append("- `%s` → （归宿表里查不到 —— 不该发生）" % l.strip()[:100])
        o.append("")
    if r["move"]:
        o.append("搬进 `## 项目知识库` 的行（逐字保留，只摘 `知识库：` 标签）：")
        o.append("")
        for l in r["move"]:
            o.append("- `%s`" % l.strip()[:160])
        o.append("")
    if r["stay"]:
        # 槽位取决于它属于哪一块：`## 项目` 那句进 `## 项目`，其余进 `## 本仓独有`。
        # （措辞别写死成"留在 `## 本仓独有`" —— A 组这 22 个仓的待迁行**全部**是项目身份，
        # 一个都不进 `## 本仓独有`，写死了这条清单就是错的。）
        o.append("原样保留的行（**没有归宿 → 不许删**；落进 `## 项目` 还是 `## 本仓独有` "
                 "取决于它属于哪一块）：")
        o.append("")
        for l in r["stay"]:
            o.append("- `%s`" % l.strip()[:160])
        o.append("")
    return o


# ---- 逐行清单：合并写
# 段头长这样：`## t_pc（712 所）`。**只认键名那一段**，其余一个字都不解释 ——
# 这是合并，不是解析重建。
_MF_HEAD = re.compile(r"^## ([A-Za-z0-9_.\-]+)（")


def _split_manifest(text):
    """`_manifest.md` → (表头行, [{"key": 键, "lines": 本段的行}])。

    第一段之前的所有行都算表头（含人在上面加的备注）。认不出的行不丢也不改：
    它落在哪一段里就跟着那一段走。
    """
    head, secs, cur = [], [], None
    for ln in text.split("\n"):
        m = _MF_HEAD.match(ln)
        if m:
            cur = {"key": m.group(1), "lines": [ln]}
            secs.append(cur)
        elif cur is None:
            head.append(ln)
        else:
            cur["lines"].append(ln)
    return head, secs


def _render_manifest(head, sections):
    out = list(head)
    for sec in sections:
        s = list(sec)
        while s and not s[-1].strip():   # 段尾空行由这里统一补，免得攒出两个
            s.pop()
        out.extend(s)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def merge_manifest(old_text, sections, head_lines):
    """旧清单 + 本轮生成的段 → 合并后的清单全文。

    为什么必须**合并**：清单是**逐仓**的文件，而一次 `--only k1` 只产出 k1 那一段。
    整写（`open(..., "w")` 一把覆盖）会把别的仓的段整片抹掉 —— 而目录里它们的产物还在、
    出生证明也还在（那边早就是合并的），唯独清单说「只有 k1」：两份文件互相打脸，
    读的人只会以为其余 21 个仓没了。**假警比漏报更贵** —— 它教人不再相信清单。

    认不出旧形状（一段 `## <键>（` 都没有）就**重写一份完整的**：宁可覆盖一张认不出来的
    清单，也不要把半个文件拼成一份看着完整、其实少了几段的文件。
    表头**用旧的那份**（人加的备注要留住）；旧表头为空时才用我们自己的常量。
    """
    head, secs = _split_manifest(old_text)
    if not secs:
        return _render_manifest(head_lines, list(sections.values()))
    out, used = [], set()
    for s in secs:
        if s["key"] in sections:
            out.append(sections[s["key"]])   # 本轮的段：换掉，位置不动
            used.add(s["key"])
        else:
            out.append(s["lines"])           # 本轮没碰它 —— 原样留着
    for k, v in sections.items():            # 本轮新出现的仓：接在末尾
        if k not in used:
            out.append(v)
    return _render_manifest(head if head else head_lines, out)


def emit(outdir, cands, reps, force=False, check_only=False, sink_path=None):
    ok, why = safe_outdir(outdir, force)
    if not ok:
        sys.stderr.write("拒绝：%s\n" % why)
        return 2
    good = [r for r in cands if not r["why"]]
    refused = [r for r in cands if r["why"]]

    registry = {} if check_only else _load_registry(outdir)
    if not check_only:
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, MARKER), "w", encoding="utf-8") as f:
            f.write("本目录由 sandbox/agents_scaffold.py 生成 —— 里面的 AGENTS.md 是**拟改产物**，\n"
                    "不是客户仓现状。§六 ①：落盘由人过目 diff 后自己决定。\n")

    head = ["# A 组 AGENTS.md 拟改产物清单", "",
            "由 `sandbox/agents_scaffold.py --emit` 生成。**未写入任何客户仓**。",
            "判据来自 `sandbox/agents_audit.py`（本程序不重写判据，只复用）。", ""]
    sections = {}                # 键 → 本轮的段（含末行「产物：…」）
    n_ok, n_lost, n_written = 0, [], 0
    for r in good:
        # ★ 先记哈希、**再**生成：万一有人在这两步中间改了源，最坏结果是复核报「源已改」
        #   （吵一句），而不是"产物是按旧源生成的、证明却是按新源签发的"（静默）。反向的
        #   顺序没有这个性质 —— 那是本功能唯一会骗人的写法。
        src_sha = fingerprint(r["path"])
        new, lost, src_lines = build(r)
        if lost:
            # ② 不许丢知识：差一行就整仓不写（安全侧：宁可不产出，不可少一行）
            #    连同上一轮的出生证明一起撤掉：产物没重生成，那张证明已经不描述任何东西了。
            registry.pop(r["key"], None)
            n_lost.append((r["key"], lost))
            continue
        text, eol, bom = read_source(r["path"])
        if not check_only:
            d = os.path.join(outdir, r["key"])
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, "AGENTS.md")
            write_like(p, new, eol, bom)
            registry[r["key"]] = {"source_sha256": src_sha,
                                  "source_bytes": os.path.getsize(r["path"]),
                                  "product_sha256": fingerprint(p),
                                  "product_bytes": os.path.getsize(p)}
            n_written += 1
        n_ok += 1
        sec = manifest(r, new, src_lines, reps["sink"])
        sec.append("产物：`%s/AGENTS.md`（行尾 %s%s）"
                   % (r["key"], "CRLF" if eol == "\r\n" else "LF", " + BOM" if bom else ""))
        sections[r["key"]] = sec

    if check_only:
        print("干跑（--check）：只跑自检，没有落盘。")
    else:
        print("产物目录：%s" % outdir)
    print("  写出 %d 个仓的 AGENTS.md ｜ 自检不过 %d 个 ｜ 判据不合格 %d 个"
          % (n_ok, len(n_lost), len(refused)))
    for k, lost in n_lost:
        print("  ❌ %s 自检不过（产物里找不到这些行，**没有落盘**）：" % k)
        for l in lost:
            print("       - %s" % l[:120])
    for r in refused:
        print("  ⏭ %s 判据不合格：%s" % (r["key"], "；".join(r["why"])))
    if not check_only:
        # ★ 合并写，不是覆盖（见 `merge_manifest`）：本轮只产出 `sections` 里那几个仓的段，
        #   目录里其余的仓照样还躺在那里 —— 整写会把它们的段抹掉，清单就成了假话。
        p = os.path.join(outdir, MANIFEST)
        old = ""
        if os.path.isfile(p):
            with io.open(p, encoding="utf-8") as f:
                old = f.read()
        text = merge_manifest(old, sections, head)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        print("  逐行归宿清单：%s ｜ 共 %d 段（本轮 %d 段）"
              % (p, len(_split_manifest(text)[1]), len(sections)))
        # 本轮一个产物都没写（全被判据拒了 / 全自检不过）就别重签证明 —— 那张纸
        # `generated_at` 会变成"一个仓都没重生成"的时刻，等于把旧产物冒充成新签发的。
        if n_written and registry:
            q = _write_registry(outdir, registry, sink_path)
            print("  出生证明（源/产物指纹 + 引擎自指纹）：%s ｜ 共 %d 个仓（本轮 %d 个）"
                  % (q, len(registry), n_written))
            print("  复核：python sandbox/agents_scaffold.py --verify %s" % outdir)
    # ⚠ 退出码只对**意外**作响：判据不合格那 7 个（B 组）是**每次都在**的正常结果，
    # 它们一起让 rc=1 的话，这个退出码就永远没有信息量了（同 `main()` 里那句"⚠ 不算失败"）。
    if n_lost or not n_ok:
        return 1
    return 0


# ---------------------------------------------------------------- 复核（只读）
def _config_paths():
    """key → 本机那份 `AGENTS.md` 的绝对路径（从本机覆盖层的 `repos` 派生）。

    路径现取而**不写在出生证明里**：写进去那张证明就只在生成它的机器上读得懂
    （同 `agents_audit.py` 把维护机路径换成 `<VAULT>` 的理由）。代价是"本机没配这个仓"
    也会报不合格 —— 那正是 fail-closed 想说的：**没法确认**不等于"没事"。
    """
    cfg = _config.load() or {}
    out = {}
    for k, v in (cfg.get("repos") or {}).items():
        if isinstance(v, str) and v:
            out[k] = os.path.join(v, "AGENTS.md")
    return out


def verify_products(outdir, paths=None):
    """复核 `outdir` 里的产物是否还对着**当下的**源 → 退出码。

    只读：一个字节都不写（同 `agents_audit.py` 的契约 —— 复核工具一旦能写，
    就没人敢在产物旁边跑它了）。
    """
    p = os.path.join(outdir, SOURCES)
    if not os.path.isfile(p):
        sys.stderr.write(
            "无凭可核：%s 里没有出生证明（%s）。\n"
            "  这个目录是 2026-10-04 之前那份生成器造的，或者根本不是它造的。\n"
            "  重跑一次 `--emit <目录>` 会补上（产物内容不一定变，变的是从此有了指纹）。\n"
            % (outdir, SOURCES))
        return 2
    try:
        with io.open(p, encoding="utf-8") as f:
            d = json.load(f)
        rec = d["sources"]
        if d["schema"] != SCHEMA or not isinstance(rec, dict):
            raise ValueError("schema=%r" % d.get("schema"))
    except Exception as e:
        sys.stderr.write("出生证明读不出来（%s）：%s\n" % (e, p))
        return 2

    if paths is None:
        paths = _config_paths()

    live = _engine_hashes()
    eng = d.get("engine") or {}
    drift = []
    for k in ("scaffold", "audit"):
        if eng.get(k) != live.get(k):
            drift.append(k)
    if "sink_rel" not in eng:
        drift.append("sink")                       # 老证明没有这一段：不假装它对得上
    elif eng["sink_rel"] is None:
        print("  ⚠ 生成时用的是引擎仓外的归宿表，本机无法复核（记下的哈希只作留档）")
    elif eng["sink_rel"] != live["sink_rel"] or eng.get("sink") != live.get("sink"):
        drift.append("sink")

    print("=" * 78)
    print("产物出生证明复核：%s" % outdir)
    print("=" * 78)
    bad = []
    for k in sorted(rec):
        v = rec[k] or {}
        src = paths.get(k)
        prod = os.path.join(outdir, k, "AGENTS.md")
        if src is None:
            why = "无法定位（本机覆盖层里没有这个仓的路径 —— 没法确认，就不算通过）"
        elif not os.path.isfile(src):
            why = "源缺失：%s" % src
        elif fingerprint(src) != v.get("source_sha256"):
            why = ("源已改（产物是对着更早的源生成的）：记录 %s B，现在 %s B"
                   % (v.get("source_bytes"), os.path.getsize(src)))
        elif not os.path.isfile(prod):
            why = "产物缺失：%s" % prod
        elif fingerprint(prod) != v.get("product_sha256"):
            why = "产物被改（不再等于生成时的那串字节）"
        else:
            print("  ✅ %-24s FRESH" % k)
            continue
        bad.append(k)
        print("  ❌ %-24s %s" % (k, why))

    # 目录里冒出来的、证明里没有的仓：多半是 `--only` 重发时把别人漏在外面。
    # 这不是"多出来的垃圾"，而是**这批产物整体不再有据可查**，所以算不合格。
    if os.path.isdir(outdir):
        for n in sorted(os.listdir(outdir)):
            if os.path.isdir(os.path.join(outdir, n)) and n not in rec:
                bad.append(n)
                print("  ❌ %-24s 未登记（目录在，出生证明里没有）" % n)

    print()
    n_rec = len(rec)
    if drift:
        print("  ⚠ 引擎已变：%s —— 产物可能是对着更早的**引擎**生成的，重跑 `--emit` 才作数。"
              % "、".join(sorted(drift)))
    if bad:
        print("  → %d 个仓有 %d 个对不上（证明记了 %d 个）" % (len(set(bad)), len(bad), n_rec))
    elif drift:
        print("  → %d 个仓的源与产物都对得上，但引擎变过（见上）" % n_rec)
    else:
        print("  → %d 个仓全部 FRESH" % n_rec)
    if not n_rec:
        print("  ⚠ 出生证明里一个仓都没有 —— 这份目录等于没被记过（`--only` 全被拒？）")
        return 1
    return 1 if (bad or drift) else 0


# ---------------------------------------------------------------- 报告
def print_plan(cands):
    good = [r for r in cands if not r["why"]]
    refused = [r for r in cands if r["why"]]
    print("=" * 78)
    print("A 组候选 —— 合适自动生成的仓（判据：本仓独有 ≤1 行，且留守行里没有表格行）")
    print("=" * 78)
    for r in good:
        print("   ✅ %-24s %-5s 可删 %d ｜ 搬迁 %d ｜ 待迁 %d"
              % (r["key"], r["end"], len(r["drop"]), len(r["move"]), len(r["stay"])))
        for l in r["stay"]:
            print("        留下的那行：%s" % l.strip()[:96])
    print()
    print("   → %d 个合格" % len(good))
    if refused:
        print()
        print("=" * 78)
        print("不合格（**不自动生成**，走人判读）")
        print("=" * 78)
        for r in refused:
            print("   ⏭ %-24s %s" % (r["key"], "；".join(r["why"])))
    print()
    print("  落盘：python sandbox/agents_scaffold.py --emit <仓外目录>   # 绝不写客户仓")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="A 组客户仓 AGENTS.md 骨架生成器（**不写客户仓**；产物落 --emit 指的目录）")
    ap.add_argument("--plan", action="store_true", help="只列候选与判据（默认行为）")
    ap.add_argument("--emit", metavar="目录", help="往这个**仓外**目录产出拟改 AGENTS.md")
    ap.add_argument("--only", metavar="k1,k2", help="只处理这些仓键名")
    ap.add_argument("--check", action="store_true", help="配合 --emit：只跑自检，不落盘")
    ap.add_argument("--force", action="store_true", help="允许写进非空且无标记的目录")
    ap.add_argument("--sink", metavar="路径", help="归宿表路径（默认 sandbox/sink_table.txt）")
    ap.add_argument("--verify", metavar="目录", help="复核产物是否还对着当下的源（只读）")
    ap.add_argument("--json", action="store_true", help="机读（配合 --plan）")
    a = ap.parse_args(argv)

    if a.verify:
        # ★ 摆在 collect() 之前：复核只需"键 → 本机源路径"，不需要归宿表。
        #   归宿表坏了照样该能回答「产物还是不是对着当下的源生成的」。
        return verify_products(a.verify)

    reps = agents_audit.collect(a.sink)
    if reps["errors"]:
        sys.stderr.write("归宿表有问题，先修它（判据依赖它）：\n")
        for e in reps["errors"]:
            sys.stderr.write("   %s\n" % e)
        return 1

    only = set(x.strip() for x in a.only.split(",") if x.strip()) if a.only else None
    cands = candidates(reps, only)

    if a.emit:
        return emit(a.emit, cands, reps, force=a.force, check_only=a.check, sink_path=a.sink)
    if a.json:
        print(json.dumps([{k: r[k] for k in ("key", "end", "customer", "why")}
                          | {"drop": len(r["drop"]), "stay": len(r["stay"]),
                             "move": len(r["move"])} for r in cands],
                         ensure_ascii=False, indent=2))
        return 0
    print_plan(cands)
    return 0


if __name__ == "__main__":
    sys.exit(main())
