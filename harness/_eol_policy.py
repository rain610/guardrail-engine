#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""期望行尾的三态解析：`CRLF` / `LF` / `auto`（= 不声明）—— 本仓只有这一处定义。

要解决的问题（2026-09-18 跨平台批次 3）
================================================================================
每个 PC/H5 gate 的 `line_ending:` 原先写的是**维护机当年检出时的快照**（22 处：11 条 LF +
11 条 CRLF）。它是"这份代码应该是什么行尾"这个**从未被复量的假设**，而它的优先级高于事实：

    # 本仓检出的实际行尾（维护者自己的 Windows 机器上）
    git -C .../agentguard_mobile_net-gxgk ls-files --eol -- src | awk '{print $2}' | sort | uniq -c
    #   → 1732 w/crlf / 27 w/lf          ← 而 gate gxgk_h5 声明的是 line_ending: LF
    # 台账里那 1489 条 H5-CRLF 错误就是它的产物：**纯行尾误报，今天就在 Windows 上发生**。

同事换盘符 / 换用户名 / 在 WSL 上 clone，只会让它更严重（Linux 检出是 LF，而 PC gate 声明
CRLF → 每一个文件都被判"行尾异常"）。所以修法不是换个新值，是把"期望什么行尾"从**共享配置里
的常量**改成**按本机本仓的检出事实判定**。

三态语义
================================================================================
  · 显式 `CRLF` / `LF`  —— 语义**完全不变**，优先级最高。写它的 gate 一个字节的行为都不动。
  · `auto`（新默认 = 不写这一行）—— 按本机本仓的检出判定，判不出来才落端默认。
  · 端默认 —— PC=CRLF / H5=LF，**逐字节等于今天的行为**，所以"判不出来"不是回归。

判据是 git 的检出配置，**不是 `os.name`**
================================================================================
按平台判有个镜像洞：Windows + `core.autocrlf=false` 的人，工作树里就是 LF，按 OS 判会全仓误报
—— 本机就有这样一个仓（见下）。而 git 的配置说的正是"这台机器上检出成什么样"。

`auto` 的判定顺序（**每一步都有实测支撑，不是推演**）
================================================================================
  ① 仓库根有 `.gitattributes` → 跑 `git ls-files --eol` 取 **`w/`（工作树侧）** 多数。
     为什么必须最优先：本仓自己就是反例 —— `core.autocrlf=true` 而 `.gitattributes` 写了
     `eol=lf`，实际检出是 `i/lf w/lf`。先看 autocrlf 会判成 CRLF，**当场判错**。
  ② 否则读 `core.autocrlf`：`true` → CRLF，`input` → LF。这两个值是 git 的**保证**
     （`true` 时文本文件检出必转 CRLF；`input` 时工作树必是 LF），不是近似。
  ③ 否则（`false` / 未设）→ 还是跑 `ls-files --eol`。这条分支是**实测逼出来的**：
     计划里原本让这一支直接落端默认，而实测某 H5 仓
     `core.autocrlf=false` 而工作树是 **1072 w/crlf / 0 w/lf** —— autocrlf=false 的含义是
     "git 不做任何转换"，于是工作树 = 索引内容，只有索引答得上来。落端默认就是 LF，
     **正好是那个仓的假红**（它的 22 个 H5 gate 里就有它）。
  ④ 一条都拿不到（没有 git / 不是仓库 / 空索引 / 信息不足）→ 返回 None，由调用方落端默认。

第 0 步：仓里**一个受管文件都没有**时不作任何断言
================================================================================
在②之前先查一次索引（`git ls-files`，实测 0.21s —— 它不读文件内容，与 `--eol` 的 1.5s 不同量级）。
空仓直接返回"没有信号"，落端默认。**这一步是拿来防什么的，说清楚**：

`core.autocrlf=true` 是 git 对"**被检出的文本文件**"的保证 —— 一个文件都没有的仓里没有可保证的
对象，可那时它照样返回 true（`git config --get` 会读全局配置），于是**任何空仓都会得到"CRLF"**。
本仓的测试夹具（`tests/test_entry_level.py::_Fixture` 等）正是这种形态：`git init` + 写文件，
但从不 `git add` → 索引为空。没有这一步，那些 **H5 夹具会在 Windows 开发机上解析成 CRLF、
在 Linux CI 上解析成 LF** —— 同一个 fixture 随机器变，是本批最该消灭的那类差异。
有这一步，它们的判据一律落端默认，与今天逐字节相同，因而**不需要逐个去钉 `line_ending`**。

多数的门槛与它防的事
================================================================================
`w/crlf` 或 `w/lf` 须占**文本成员**（`crlf` + `lf` + `mixed`；二进制 `-text` 与已删除的 `none`
不计）的 **≥ 90%**，否则视为"本仓没有明确多数" → 落端默认。防的是：整仓被批量转换
（`git add --renormalize .`，或改了 autocrlf 后全量重检出）→ 多数翻转 → 门禁**静默跟着翻转**。
批量转换只会让比例变接近，正好触发这条。

为什么不 import `sandbox/eol_text.py`（两个不同的问题，不是重复实现）
================================================================================
  · `eol_text.index_eol_map()` 取的是 **`i/`（索引侧）**，回答"git 会把它**存**成什么"；
    本模块要 **`w/`（工作树侧）**，回答"它现在**躺**在盘上是什么"。行尾门禁读的是磁盘字节，
    所以必须比工作树侧 —— 拿索引侧比，gxgk_h5 那 1732 个 CRLF 照样全红（它 `i/lf w/crlf`）。
  · 位置也是硬约束：不能放 `sandbox/`（`tests/_engine_copy.py` 只拷 `checks/ harness/ config/`
    三棵子树，"工程副本"里根本没有 sandbox/），也不能 `import eol_text`
    （`tests/test_stdlib_only.py` 只收引擎层模块 → 会走 find_spec 失败分支判"疑似第三方"）。
  · 两份实现对"同一份 `--eol` 输出"的解析必须不漂移：`tests/test_review_v13_batch9.py`
    里有一条对拍锁，造一个「索引 LF / 工作树 CRLF」的真临时仓，断言两边各自与
    `git ls-files --eol` 的对应列**逐字相符**。
"""
from __future__ import annotations

import os
import subprocess
import sys

# UTF-8 兜底（本仓惯例）：本模块会往 stderr 写中文与 `←`，被一支没做过兜底的脚本导入时
# GBK 控制台会崩在那一行上。兜底幂等，重复做一次没有代价。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

__all__ = ["resolve", "worktree_eol_map", "eol_majority", "has_tracked", "MAJORITY_THRESHOLD"]

#: 判"本仓有明确多数"的门槛（见模块 docstring 末节）
MAJORITY_THRESHOLD = 0.90

#: `w/` 列里算"文本成员"的取值 —— 其余（`-text` 二进制 / `none` 不在工作树 / 认不出的）不计。
#: 口径**只写在 `eol_majority()` 的 `counts` 键上**（那里是唯一被消费的地方），不另立常量：
#: 先前这里有一个 `_TEXT_TOKENS` 元组，与 `counts` 的键重复且全仓零引用 —— 同一个口径两处
#: 定义，改一处另一处不跟，正是本仓反复出现过的形状（`_config` 的 `_POLICY_KEYS` docstring
#: 为同一件事写过一遍）。

#: 进程内缓存：一次 run 里同一个仓只探一次（--all 会逐 gate 调 resolve，34 个 gate 里 PC/H5
#: 有 22 个，不缓存就是 22 次子进程）。
_CACHE = {}
_GIT_TIMEOUT = 60


def _git(root, args):
    """跑一次 git，返回 stdout 文本；**任何失败都返回 None，绝不抛**。

    "探不出来"是正常结局（没有 git / 不是仓库 / 仓里还没有索引），不是一个异常 —— 调用方
    对它的处理就是落端默认，与今天的行为逐字节相同。让探测失败把门禁打挂是本末倒置。
    """
    try:
        r = subprocess.run(["git"] + list(args), cwd=root, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=_GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace")


def worktree_eol_map(root):
    """`{仓库相对路径: 工作树侧行尾标记}`（`ls-files --eol` 的 **`w/`** 列）。

    取值与 git 逐字相同：`lf` / `crlf` / `mixed` / `none` / `-text`。失败返回 `{}`。
    """
    key = os.path.abspath(root)
    if key in _CACHE:
        return _CACHE[key]
    txt = _git(root, ["ls-files", "--eol"])
    out = {}
    if txt:
        for line in txt.splitlines():
            head, tab, path = line.partition("\t")
            if not tab:
                continue                        # 认不出的行就跳过，不许猜
            toks = head.split()
            # git 的列序固定是 `i/<eol>` `w/<eol>` `attr/...`，所以工作树侧是 toks[1]。
            if len(toks) < 2 or not toks[1].startswith("w/"):
                continue
            out[path.replace("\\", "/")] = toks[1][2:]
    _CACHE[key] = out
    return out


def eol_majority(root):
    """→ `("CRLF" | "LF" | None, {"crlf": n, "lf": n, "mixed": n}, 文本成员总数)`。

    第三个返回值是**门槛的分母**（文本成员数），报给用户看的那句"2930/2950"用的就是它。
    """
    counts = {"crlf": 0, "lf": 0, "mixed": 0}
    for tok in worktree_eol_map(root).values():
        if tok in counts:
            counts[tok] += 1
    total = sum(counts.values())
    if total <= 0:
        return None, counts, 0
    for tok, val in (("crlf", "CRLF"), ("lf", "LF")):
        if counts[tok] / float(total) >= MAJORITY_THRESHOLD:
            return val, counts, total
    return None, counts, total


def has_tracked(root):
    """仓里有没有**受管文件**（查索引，不读文件内容）。空仓 / 不是仓库都返回 False。"""
    txt = _git(root, ["ls-files"])
    return bool(txt and txt.strip())


def _auto(repo):
    """`auto` 的判定（见模块 docstring）。→ `(值|None, 来源, 说明)`。"""
    if not repo or not os.path.isdir(repo):
        return None, "no-repo", ""
    try:
        # 第 0 步：空仓不作任何断言（见 docstring 末节 —— 防的是夹具随机器变）
        if not has_tracked(repo):
            return None, "no-tracked", ""
        # ① `.gitattributes` 存在 → 只有 ls-files 答得上来（逐路径的 eol 规则 + 实测反例）
        if os.path.isfile(os.path.join(repo, ".gitattributes")):
            val, c, n = eol_majority(repo)
            if val:
                return val, "checkout-majority", "%d/%d" % (c[val.lower()], n)
            return None, "no-majority", ""
        # ② core.autocrlf 的两个**保证**（不是近似）：true→检出必 CRLF，input→工作树必 LF
        ac = (_git(repo, ["config", "--get", "core.autocrlf"]) or "").strip().lower()
        if ac == "true":
            return "CRLF", "git-config", "core.autocrlf=true"
        if ac == "input":
            return "LF", "git-config", "core.autocrlf=input"
        # ③ false / 未设 → git 不做转换 ⇒ 工作树 = 索引内容，还是只能问 ls-files。
        #    实测反例：某 H5 仓（autocrlf=false 而工作树 1072 w/crlf / 0 w/lf）。
        val, c, n = eol_majority(repo)
        if val:
            return val, "checkout-majority", "%d/%d" % (c[val.lower()], n)
        return None, "no-majority", ""
    except Exception:
        return None, "probe-failed", ""


def _notice(gate_name, value, source, detail):
    """把结论与来源写 stderr —— **这里只管"怎么发"，"发不发"的判据在 `resolve()`**。

    判据留在 `resolve` 里、与它解释的那两条分支相邻，读的人不必在两个函数之间来回跳。

    去重沿用 `_config._policy_notice_once`：指纹（sha1 前 16 位）写进环境变量
    `HARNESS_POLICY_NOTICE_SHOWN`，run_gate 起的检查子进程继承后不重复；key 里带**值**，
    所以翻转（CRLF→LF）会重新报一次，重复则压掉。失败兜底直接写 stderr —— 提示发不出去
    绝不能把门禁打挂。
    """
    text = "[config-eol] 期望行尾 %s ← %s%s（gate %s）\n" % (
        value, source, (" " + detail) if detail else "", gate_name or "?")
    try:
        from _config import _policy_notice_once
        _policy_notice_once(("eol", gate_name, value, source), text)
    except Exception:
        try:
            sys.stderr.write(text)
        except Exception:
            pass


def resolve(repo, gate_cfg, endpoint_default, gate_name=""):
    """→ `(期望行尾, 来源)`。**这是本模块唯一的对外入口。**

    `endpoint_default` 由调用方给（PC="CRLF" / H5="LF"），它同时是"判不出来"时的兜底 ——
    刻意**不**按 `os.name` 判：夹具（`tests/test_entry_level.py::_Fixture`）只 `git init`
    从不 `git add`，`ls-files --eol` 输出为空 → 判据必然落兜底；按 OS 判会让 CI（ubuntu）
    与 Windows 上同一份夹具得出不同答案，而它们本来是完全一致的。端默认让两端都逐字节不变。

    来源取值：`declared` / `checkout-majority` / `git-config` / `no-majority` /
    `no-repo` / `no-tracked` / `probe-failed` / `endpoint-default`。
    """
    declared = (gate_cfg or {}).get("line_ending")
    if isinstance(declared, str):
        d = declared.strip().upper()
        if d in ("CRLF", "LF"):
            return d, "declared"                 # 显式值：语义完全不变，且不探不打
    value, source, detail = _auto(repo)
    if value:
        # 出声的判据：**结论与端默认不一致** —— 它恰好等于"这个 gate 的期望值因你这台机器
        # 而**变了**"（`auto` 的失效形态是**静默翻转**：整仓重检出之后门禁跟着换了期望值，
        # 不报错、不提示，看起来一切正常）：
        #   · Windows 上的 PC 仓 → 检出 CRLF == 端默认 CRLF → 静默（日常，占绝大多数）；
        #   · Windows 上的 H5 仓 → 检出 CRLF ≠ 端默认 LF → **出声**（gxgk_h5 那 1489 条误报的成因）；
        #   · Linux/WSL 上的 PC 仓 → 检出 LF ≠ 端默认 CRLF → **出声**（同事在 WSL 上最需要的那句）。
        # 为什么不"每次解析都报一条"：日常形态永远相同，每提交一行不变的话换来的不是"知道"
        # 而是**不再读** —— 那与把它删掉是同一种失败（`_config._policy_notice_once` 的
        # docstring 为同一件事写过一遍）。翻转依然会报，因为判据里有值。
        if value.upper() != endpoint_default.upper():
            _notice(gate_name, value, source, detail)
        return value, source
    # 落端默认时**只有两种情况值得出声**：探测到了东西却判不出多数（`no-majority`）、
    # 或探测本身炸了（`probe-failed`）。这两条**尽管结论 == 端默认，照样出声** ——
    # 它们报的正是"探不出来"这件事本身，那与"探出来正好等于端默认"是两回事。
    # 而 `no-repo`（本机没配这个 gate 的路径）与 `no-tracked`（空仓）不出声：
    # 它们不是"行尾这件事有异常"，天天报只会把人训成不看 —— 这一行存在的全部意义
    # 就是"看见它时该反应过来"。这两条另有各自的信号（[config-warn] / 夹具本身）。
    if source in ("no-majority", "probe-failed"):
        _notice(gate_name, endpoint_default.upper(), "端默认（" + source + "）", "")
    return endpoint_default.upper(), "endpoint-default"
