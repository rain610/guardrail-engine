#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行尾归一 + 指纹口径：把**平台差异**挡在指纹之外（本仓只有这一处定义）。

问题（2026-09-18 发版复盘实测）
================================================================================
同一份提交，本机（Windows）与 CI（Linux）各打一份包，解开后**内容不同**，逐成员对下来
是 8 处 —— 其中 5 处纯粹是行尾，2 处是工具链版本，1 处是被前两类带跑的派生文件：

    LICENSE / OPENCLAW.md / marketplace/…/SKILL.md      外层，CRLF vs LF
    dist-info/licenses/LICENSE / dist-info/METADATA     轮子内，CRLF vs LF
    dist-info/WHEEL      `Generator: setuptools (79.0.1)` vs `(84.0.0)`
    dist-info/RECORD     它是上面那些成员的**字节哈希**，上面一变它必然跟着变

根因两条，都不是"谁写错了代码"：
  ① 本机 `core.autocrlf=true`（Git for Windows 的默认值）→ 工作树里是 CRLF、索引里是 LF。
     `sandbox/pack_release.py` 当年用 `shutil.copy2` 照搬**工作树字节**，setuptools 也从
     工作树读；CI 在 Linux 上检出（纯 LF）→ 两边自然不同。
  ② 轮子里的文本元数据由 setuptools 在**本平台**以文本模式写出：Windows 上连 METADATA 的
     头字段都带 `\\r`（`Metadata-Version: 2.4\\r`），而它读的 `README.md` 是纯 LF（911 行 0 个 CRLF）
     —— 这一处**不是**源码行尾造成的，是平台性的。

为什么值得治（它不是洁癖）
================================================================================
`PACK-INFO.json` 里的整包指纹是「**同版本号、内容不同**」那条告警（G3）的唯一判据，
而该告警要回答的是"同事手上那份包是不是我这次做的这份"。平台噪音让它**每次重打包都响**
—— 人很快学会无视它，真正该看见的那一次也就看不见了。**跟漏报是同一种失败。**

本模块的两个口径，一个函数一个
================================================================================
  · `align_to_index` —— 出货**源码**文件按什么写：以 **git 索引**为准，不猜。
    索引是 `lf` 而工作树是 CRLF → 折成 LF；其余（`crlf` / `mixed` / `-text` / 不在索引里）
    **一律原样**。我们不发明转换规则，只复现 git 自己会做的事 —— `i/lf` 的含义本来就是
    "提交时会被归一成 LF"，折它是复现、不是发明。
  · `canon_bytes` / `canon_sha1` —— **指纹**里一个成员按什么算：文本（前 8KB 无 NUL 字节）
    折成 LF，二进制原样。判据刻意与 `.gitattributes` 的 `text=auto` 一致（同样看 NUL），
    免得两处口径各说各话。

`index_eol_map()` 为什么读 `--eol` 而不是自己扫文件
================================================================================
因为要的答案是"**git 认为**这个文件该是什么行尾"，不是"它现在是什么行尾"。扫文件只能拿到后者
（那正是要修的东西），还会把二进制、未跟踪文件一起卷进来。`git ls-files --eol` 一次调用同时给出
索引侧与工作树侧，是本机唯一可用的权威。

它**不**做的事（写清楚，免得下次有人指望它）
================================================================================
  · **不改写轮子里的 METADATA**：那是 setuptools 在本平台写的，属于**产物本身**的平台差异。
    本模块只保证**指纹**（G3 的判据）跨平台一致，**不**保证轮子文件字节跨平台一致 ——
    后者要么把构建收敛到单一平台，要么事后重写产物，两件事都不该由一个归一模块偷偷做掉。
  · 不碰二进制：`.zip` / `.whl` / 图片一律原样。
  · 没有 git 时不假装知道索引：`index_eol_map()` 返回空表 + 一句 stderr 说明，调用方退回
    "逐字节照搬"（= 本模块出现之前的行为）。出货件的**可用性**不该挂在 git 上，
    而它带来的那点行尾差异由指纹口径（`canon_*`）兜住。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys

# UTF-8 兜底（本仓惯例，见 `sandbox/list_entries.py`）：本模块是库，但 `_warn()` 会往 stderr
# 写中文与 `⚠` —— 被一支**没做过兜底**的脚本导入时，GBK 控制台会整体崩在那一行上。
# 兜底是幂等的，重复做一次没有代价；漏做一次的代价是一次 UnicodeEncodeError。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

__all__ = ["align_to_index", "index_eol_map", "read_shipped", "copy_shipped",
           "looks_binary", "canon_bytes", "canon_sha1", "canon_wheel_member"]

_BINARY_SNIFF = 8192          # 与 git 的 `text=auto` 同一判据：前 8KB 里有没有 NUL

_warned = False


def _warn(msg):
    """一句就够 —— 同一次进程里重复喊同一件事只会让真正该看的那行淹掉。"""
    global _warned
    if _warned:
        return
    _warned = True
    try:
        sys.stderr.write("⚠ eol_text: %s\n" % msg)
        sys.stderr.flush()
    except Exception:
        pass


# --------------------------------------------------------------------- 出货源码：对齐索引

def index_eol_map(root, quiet=False):
    """`{仓库相对路径: 索引侧行尾标记}`（`lf` / `crlf` / `mixed` / `none` / `-text`）。

    取的是 **`i/` 那一列**（索引侧）—— 判据是"git 会把它存成什么"，不是"它现在躺在盘上是什么"。
    路径一律正斜杠，与本仓所有清单（SHA1SUMS / PACK-INFO / pack_files）同一种写法。

    **任何失败都返回 `{}`，绝不抛**：没有 git、不是仓库、输出读不懂 —— 一律当作"不知道"，
    由调用方退回逐字节照搬。打包这件事不该因为一个诊断用途的调用而失败。
    """
    try:
        r = subprocess.run(["git", "ls-files", "--eol"], cwd=root,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as ex:                       # 没有 git 可执行文件
        if not quiet:
            _warn("跑不了 `git ls-files --eol`（%r）→ 出货件按**逐字节照搬**处理，"
                  "行尾可能随检出平台而变（指纹不受影响）。" % (ex,))
        return {}
    if r.returncode != 0:
        if not quiet:
            _warn("`git ls-files --eol` rc=%s（%s 不是 git 仓库？）→ 出货件按逐字节照搬处理。"
                  % (r.returncode, root))
        return {}
    out = {}
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        head, tab, path = line.partition("\t")
        if not tab:
            continue                            # 认不出的行就跳过，不许猜
        toks = head.split()
        if len(toks) < 2 or not toks[0].startswith("i/"):
            continue
        out[path.replace("\\", "/")] = toks[0][2:]
    return out


def align_to_index(data, token):
    """索引侧标记是 `lf` → 折成 LF；**其余一律原样**。

    为什么只认 `lf` 这一个标记：它才有"提交时 git 会归一成 LF"这个确定含义，于是折它与
    git 的检出结果一致。`crlf` 是"这个人就要 CRLF"（原样）、`mixed`/`none` 是"说不清"
    （说不清就别动）、`-text` 是二进制（碰不得）。宁可少折，不可猜错。
    """
    if token != "lf":
        return data
    return data.replace(b"\r\n", b"\n")


def read_shipped(path, root, eol_map=None):
    """读出**该出货的字节**：先按索引对齐行尾，再原样返回。`eol_map` 由调用方复用以省一次 git。"""
    if eol_map is None:
        eol_map = index_eol_map(root)
    with open(path, "rb") as f:
        data = f.read()
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root)).replace("\\", "/")
    return align_to_index(data, eol_map.get(rel))


def copy_shipped(src, dst, root, eol_map=None):
    """`shutil.copy2` 的归一版（内容可能被折行尾，故先写内容再拷元数据）。"""
    data = read_shipped(src, root, eol_map)
    parent = os.path.dirname(dst)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dst, "wb") as f:
        f.write(data)
    try:
        shutil.copystat(src, dst)
    except OSError:
        pass                                    # 元数据是顺手，不是判据（内容才是）
    return data


# --------------------------------------------------------------------- 指纹口径

def looks_binary(data):
    """与 git `text=auto` 同一条判据：前 8KB 里出现 NUL 就是二进制。"""
    return b"\0" in data[:_BINARY_SNIFF]


def canon_bytes(data):
    """指纹口径下的成员规范字节：文本折 LF，二进制原样。"""
    return data if looks_binary(data) else data.replace(b"\r\n", b"\n")


def canon_sha1(data):
    return hashlib.sha1(canon_bytes(data)).hexdigest()


#: 轮子内**派生**的成员 —— 它的内容是别的成员字节的哈希，别的成员一变它必然跟着变。
#: 排除它不是因为"它不重要"，而是因为它**不携带独立信息**：留着只会让上面那些成员的
#: 每一处平台差异在指纹里被算两遍，且两遍都指向同一个根因。
WHEEL_DERIVED = ("RECORD",)

#: 轮子内由**构建工具**写下自己名字/版本的行（`Generator: setuptools (79.0.1)`）。
#: 它随构建机的 setuptools 版本变，而 setuptools 版本不在这份源码的管辖内 ——
#: 同一份源码在 79 与 84 上打出的轮子，功能上无从区分。留着它 = 把"构建机装了什么"
#: 混进"这份包是不是我做的"这个判断里。
_GENERATOR_PREFIX = b"Generator:"


def canon_wheel_member(name, data):
    """轮子内一个成员 → 指纹用的规范字节；**返回 `None` 表示这个成员不进指纹**。

    与 `canon_bytes` 是两层：先按 wheel 的成员语义处理（派生件剔除、工具版本行剔除），
    再按文本/二进制规范行尾。顺序不能颠倒 —— 先折 LF 才能安全按 `\\n` 切行。
    """
    norm = name.replace("\\", "/")
    if norm.endswith(".dist-info/" + WHEEL_DERIVED[0]):
        return None
    data = canon_bytes(data)
    if norm.endswith(".dist-info/WHEEL"):
        data = b"\n".join(ln for ln in data.split(b"\n")
                          if not ln.startswith(_GENERATOR_PREFIX))
    return data
