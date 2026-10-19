#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内部安装包一键重建：wheel + marketplace + 文档 + SHA1SUMS + zip。

用法（一条命令，可从任意目录执行）：

    python sandbox/pack_release.py [--out DIR] [--python PY] [--wheel PATH]

产物（默认 DIR = <仓库根>/dist/install-package）：

    eaglermc-gate-内部安装包-v<版本>/        ← 文件包（stage）
        eaglermc_gate-*.whl                  ← 由本仓库现场构建（或用 --wheel 复用已有轮子）
        marketplace/                         ← Claude 兼容插件市场（build_marketplace.py 现场构建）
        OPENCLAW.md / LICENSE / 安装说明.md   ← 文档（单一来源：仓库 docs/ 与 LICENSE）
        fix_paths.py / uninstall.py / upgrade.py
                                             ← 换机器路径适配 + 一键卸载 + 一键升级（随包分发）
        PACK-INFO.json                       ← 出货件身份：版本 / 构建时刻 / **整包指纹**（+ 市场子树指纹）
        SHA1SUMS.txt                         ← 文件包内全部文件的 SHA1
    eaglermc-gate-内部安装包-v<版本>.zip     ← 上述文件包的 zip（zip 内根目录同名）

出货件与机器无关（2026-09-17 起）
================================================================================
包内 `.mcp.json` **刻意保留** `__PY__` / `__MCP__` / `__HARNESS__` 占位符，装机时由包内
`fix_paths.py` 现场填本机路径。此前是打包时就把打包机的绝对路径烧进去：同事漏跑适配
（"原地升级、机器没变"那句话最容易让人跳过）时会拿到**打包机的**路径 —— MCP 服务起不来，
编辑期门禁整体无声失效，且没有任何一步报错。留占位符让同一个漏步当场失败，且
"这份包还没适配"肉眼可辨（`sandbox/verify_release.py` 对此设了锁）。

同一份提交在哪里打都该是同一个包（2026-09-18 起）
================================================================================
行尾不再随**检出平台**变。随包源码一律经 `sandbox/eol_text.py` 按 **git 索引**对齐
（索引 `lf` + 工作树 CRLF → 折成 LF），市场子树那份由 `build_plugin.py` 用同一口径复制。
不加这一步的后果实测过：本机 `core.autocrlf=true` 时，同一份提交打出的包与 CI 的包有
**8 处**内容不同（外层 3 个成员 + 轮子内 3 个成员 + 工具链版本 + 被带跑的 RECORD），
于是一个**没被动过**的包也会亮 G3 那条"同版本号、内容不同"——人很快学会无视它。
判断依据与不做什么，写在 `sandbox/eol_text.py` 的模块头（口径只那一处）。

原则（ADR-0001）：本脚本只做组装——wheel / 市场 / 文档全部取自仓库单一来源，
不复制任何门禁逻辑；幂等，可重复执行。

退出码：0 = 成功；1 = 失败（缺构建工具、构建器失败等）。
"""
import argparse
import datetime
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HERE = os.path.dirname(os.path.abspath(__file__))
# `sandbox/` 不是包，而本文件会被 `verify_release._packer()` 用 importlib 从路径加载 ——
# 那条路上 `sandbox/` 不在 sys.path 上，所以这里自己挂一次（幂等）。
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import eol_text                                        # noqa: E402  （行尾/指纹口径，全仓只此一处）


def log(msg=""):
    print(msg)
    sys.stdout.flush()


def read_text(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def clear_dir(path):
    """尽力清空目录；**被占用时返回失败清单，绝不把异常抛给调用方**。

    评审 v13 高 6 的现场实测：`_wheel/` 里 2026-09-14 那个 wheel 被别的进程占着，
    `shutil.rmtree` 直接抛 PermissionError —— 于是**整个发布脚本以一个 Python traceback
    收场**：没有 ❌、没有处置建议，人只看到一段栈。构建脚本撞上陈旧产物是常态
    （上次没清干净、杀毒在扫、另一份 pack_release 还在跑），不该是这种形状。

    两个调用方对"清不掉"的处置**刻意不同**，见各自调用点：
      · stage 清不掉 → 真做不下去（要往里写同名文件）→ 报清楚是哪几个文件 + 怎么处理，rc=1；
      · 构建目录清不掉 → **不失败**：陈旧产物不该挡住新产物，换个全新目录继续构建。
    """
    if not os.path.isdir(path):
        return []
    try:
        shutil.rmtree(path)
        return []
    except Exception:
        bad = []
        for dp, _dns, fns in os.walk(path):
            for fn in fns:
                fp = os.path.join(dp, fn)
                try:
                    os.remove(fp)
                except Exception:
                    bad.append(fp)
        for dp, _dns, _fns in os.walk(path, topdown=False):
            try:
                os.rmdir(dp)
            except Exception:
                bad.append(dp)
        return bad


def sha1_file(path):
    """**逐字节**哈希 —— 给 `SHA1SUMS.txt` 与"这份 zip 的 SHA1"用，必须保持原样。

    刻意**不**做行尾归一：那是给同事下载核对的完整性清单，它回答的是"我手上这串字节
    是不是你发的那串字节"。若按归一后的口径算，一个行尾被改动过的包也会核过 ——
    那就不是完整性校验了。（指纹是另一回事，见 `content_sha1`。）
    """
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def content_sha1(path):
    """**指纹口径**的文件内容哈希：文本折 LF、二进制原样（见 `eol_text` 模块头）。

    与 `sha1_file` 的分工是刻意的、也是本批的核心：同一个文件可以有两种正确的哈希 ——
    完整性清单要**字节**，身份指纹要**内容**。此前两者共用一个函数，于是"平台换了行尾"
    这件事在指纹里表现为"内容变了"。
    """
    with open(path, "rb") as f:
        return eol_text.canon_sha1(f.read())


BUILD_POLLUTION = ("build", "eaglermc_gate.egg-info")  # setuptools 就地构建的副产物


def build_wheel(out_dir):
    """现场构建 wheel：按可用工具逐个尝试（python -m build → uv build → pip wheel）。

    注：走 setuptools 的构建器会**就地**在仓库根生成 build/ 与 *.egg-info/；
    本函数结束后清理「本次新建」的这两个目录，保证脚本不污染工作树。
    """
    os.makedirs(out_dir, exist_ok=True)
    before = {name: os.path.isdir(os.path.join(ROOT, name)) for name in BUILD_POLLUTION}
    result = None
    attempts = [
        [sys.executable, "-m", "build", "--wheel", "--outdir", out_dir],
        ["uv", "build", "--wheel", "--out-dir", out_dir],
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", out_dir],
    ]
    for cmd in attempts:
        if cmd[0] == "uv" and shutil.which("uv") is None:
            log("  - 跳过（未安装 uv）")
            continue
        log("  - 尝试：%s ..." % " ".join(cmd))
        try:
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=600)
        except Exception as ex:
            log("    失败（%r），换下一种" % ex)
            continue
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
            log("    rc=%s，换下一种：%s" % (r.returncode, " | ".join(tail)[:300]))
            continue
        wheels = []
        for dp, _, fns in os.walk(out_dir):
            wheels += [os.path.join(dp, x) for x in fns if x.endswith(".whl")]
        if wheels:
            wheels.sort(key=os.path.getmtime)
            result = wheels[-1]
            break
        log("    构建命令成功但未发现 .whl，换下一种")
    for name, existed in before.items():
        pth = os.path.join(ROOT, name)
        if not existed and os.path.isdir(pth):
            shutil.rmtree(pth, ignore_errors=True)
            log("  - 已清理构建副产物：%s/" % name)
    return result


SELF_FILES = ("PACK-INFO.json", "SHA1SUMS.txt")   # 自指：指纹/清单自己写在里面，不能算进指纹


def wheel_content_sha1(src):
    """`<.whl>` 的**内容**指纹（不是文件字节指纹）：按 (成员路径, 成员内容 SHA1) 整体哈希。

    为什么不能直接用文件字节：wheel 是 zip，条目时间戳由构建时刻决定 —— 同一份源码连打两次，
    文件字节不同，而里面的**内容**一模一样。实测（2026-09-17，连打两次同一份源码）：
      eaglermc_gate-0.1.2.dev0-py3-none-any.whl  abc2802cb6… / d603c169bd…
    而同一对包的 `marketplace_sha1` 完全相同、安装说明也完全相同 —— 差别只在轮子这个 zip 的
    时间戳。所以整包指纹里轮子必须按内容算：按字节算的话，G3 那句"同版本号、内容不同"会变成
    **每次重打包都响**的噪音，人很快学会无视它，真正该看见的那一次也就看不见了 ——
    这跟漏报是同一种失败。

    ★ 2026-09-18 起成员内容先过 `eol_text.canon_wheel_member`（口径在那一个函数里）：
      · 文本成员折 LF —— Windows 上 setuptools 以文本模式写 METADATA，连**头字段**都带 `\\r`
        （`Metadata-Version: 2.4\\r`），而它读的 `README.md` 是纯 LF；CI 上没有这一步。
        不折的话，同一份源码在两地上打出的轮子指纹永远不同，G3 那条告警逢包必响。
      · `dist-info/RECORD` 不进指纹 —— 它是别的成员的字节哈希，上面一变它必然跟着变，
        留着只会把同一个根因算两遍。
      · `dist-info/WHEEL` 去掉 `Generator:` 行 —— 那行写的是构建机的 setuptools 版本
        （实测 79.0.1 vs 84.0.0），不在这份源码的管辖内。

    `src` 可以是磁盘路径，也可以是字节（上一次那份包在 zip 里，轮子是嵌在内存里的）。
    """
    with zipfile.ZipFile(src if isinstance(src, str) else io.BytesIO(src)) as z:
        files = {}
        for n in z.namelist():
            if n.endswith("/"):
                continue
            d = eol_text.canon_wheel_member(n, z.read(n))
            if d is None:
                continue
            files[n] = hashlib.sha1(d).hexdigest()
    return fingerprint(files)


def pack_files(source):
    """出货件的"内容清单"：{相对路径: sha1}，**不含 PACK-INFO.json / SHA1SUMS.txt**。

    两个输入都吃，且必须算出**同一个清单**：
      · `source` 是包目录（stage）→ 本次这份包；
      · `source` 是**上一次的 zip** → 上一次那份包。
    为什么非得能算旧包：G3 要回答的唯一问题是"同事手上那个包是不是我这次做的那个"，
    而旧包里只躺着当时的 PACK-INFO.json，没有留清单 —— 若只能算新包，这个比较就无从做起。
    （旧包的 PACK-INFO 里没有 `pack_sha1` 字段，但清单可以从 zip 内容现算，所以**旧格式的包
    同样可比**：这一点是 2026-09-17 扩指纹时特意保住的，别改成"只读 PACK-INFO 里的字段"。）

    为什么排除那两个文件：指纹要写进 PACK-INFO.json，而它自己在包里 —— 算进去就成了自指，
    永远对不上；SHA1SUMS.txt 同理（它列的正是这份清单的哈希）。两个名字之外的**任何**文件
    都在指纹里：轮子（按**内容**，见 `wheel_content_sha1`）、marketplace/、`sandbox/` 里
    那几支随包分发的工具、安装说明.md、OPENCLAW.md、LICENSE（工具名单以打包器实际
    `copy2` 的为准 —— 写死"三支"的注释会在加第四支那天烂掉，2026-09-18 加 `bootstrap.py` 时正是如此）。
    这正是 2026-09-17 扩它的原因：此前只覆盖 marketplace/，于是"只改了工具的同版本重打包"
    在指纹上纹丝不动，G3 那句提醒在最该响的一次上没有响（现场见 reports/_falsify_v1_lock.txt §⑥）。

    ★ 两个支路都必须按**同一种口径**算（`content_sha1` / `eol_text.canon_sha1`，文本折 LF）。
    只归一"本次这份"（磁盘支路）是不够的、而且会把事情弄反：磁盘上是本机检出的字节，
    zip 里是**别人（CI）**打的那份。若只折一边，同一份源码在两地上算出的清单仍然不同 ——
    而两边都折之后，"行尾变了但内容没变"这件事在两个支路上都不再算成变化，
    正是 G3 该有的语义（它问的是"内容是不是我这次做的这份"，不是"字节从哪台机器出来的"）。
    """
    out = {}
    if os.path.isdir(source):
        for dp, dns, fns in os.walk(source):
            dns.sort()
            for fn in sorted(fns):
                fp = os.path.join(dp, fn)
                rel = os.path.relpath(fp, source).replace("\\", "/")
                if rel in SELF_FILES:
                    continue
                out[rel] = wheel_content_sha1(fp) if rel.endswith(".whl") else content_sha1(fp)
        return out
    with zipfile.ZipFile(source) as z:
        for n in z.namelist():
            if n.endswith("/"):
                continue
            rel = n.split("/", 1)[1] if "/" in n else n     # zip 内根目录是 <包名>/
            if rel in SELF_FILES:
                continue
            out[rel] = (wheel_content_sha1(z.read(n)) if rel.endswith(".whl")
                        else eol_text.canon_sha1(z.read(n)))
    return out


def fingerprint(files):
    """内容清单 → 指纹：按相对路径排序后整体哈希（空清单也有指纹 —— 它是哨兵，不是"没有"）。"""
    body = "\n".join("%s  %s" % (files[k], k) for k in sorted(files))
    return hashlib.sha1((body + "\n").encode("utf-8")).hexdigest()


def pack_fingerprint(stage):
    """**整包**指纹（G3 判"同版本号、内容不同"用它）。"""
    return fingerprint(pack_files(stage))


def market_fingerprint(stage):
    """`marketplace/` 那棵子树的指纹（PACK-INFO 的 `marketplace_sha1` 字段用它）。

    为什么还留着这个窄口径：插件的"版本"是缓存目录的分键，而 `claude plugin update` 只在
    版本号变时才搬运内容 —— 市场那一份单独有个名字，读包的人一眼能看到"市场变没变"。
    但**裁决**不靠它：同版本号内容不同这件事由 `pack_sha1` 判（它覆盖整包）。
    """
    files = pack_files(stage)
    sub = dict((k, v) for k, v in files.items()
               if k == "marketplace" or k.startswith("marketplace/"))
    return fingerprint(sub)


def diff_files(old, new):
    """两份内容清单的差异 → 人话列表（`上一次那份` / `本次这份` 的前缀区分增删改）。"""
    out = []
    for k in sorted(set(old) | set(new)):
        a, b = old.get(k), new.get(k)
        if a == b:
            continue
        if a is None:
            out.append("+ %s（本次新增）" % k)
        elif b is None:
            out.append("- %s（上一次有、本次没有）" % k)
        else:
            out.append("~ %s（两边内容不同）" % k)
    return out


def read_pack_info(zip_path):
    """从**上一次**的同名 zip 里读回 PACK-INFO.json；读不到一律返回 None（绝不抛）。

    边界上的取舍与 `harness/run_gate._weaken_marks` 同一条：这是**只读的诊断信息**，
    读不出来就当"没有可比对象"，不许因此把一次正常的重建弄成失败 —— 但也不能假装
    比对过（调用方只在拿到真的 dict 时才说"指纹变了"）。
    """
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = [n for n in z.namelist() if n.endswith("/PACK-INFO.json")]
            if not names:
                return None
            with z.open(names[0]) as f:
                return json.loads(f.read().decode("utf-8"))
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="内部安装包一键重建（wheel + marketplace + 文档 + SHA1SUMS + zip）")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "install-package"),
                    help="输出目录（默认 <仓库根>/dist/install-package）")
    ap.add_argument("--python", default=sys.executable or "python",
                    help="写入插件/市场配置的 python 解释器（默认当前解释器）")
    ap.add_argument("--wheel", default=None, help="复用已有 wheel（跳过现场构建）")
    a = ap.parse_args(argv)

    version = read_text(os.path.join(ROOT, "VERSION")).strip()
    if not version:
        log("✗ VERSION 文件为空")
        return 1
    pkg_name = "eaglermc-gate-内部安装包-v%s" % version
    out = os.path.abspath(a.out)
    stage = os.path.join(out, pkg_name)
    zip_path = os.path.join(out, pkg_name + ".zip")
    # 上一次同版本的包（如果有）——在覆盖它之前先记下它的身份，用来判断"同版本不同内容"。
    prev_info = read_pack_info(zip_path) if os.path.exists(zip_path) else None
    # 上一次那份包的**内容清单**（从 zip 现算，不依赖它 PACK-INFO 里有没有记指纹 —— 旧格式
    # 的包只有 marketplace_sha1；只读字段的话，"扩指纹"这件事会在跨格式那一次上静默失效）。
    # 读不动不在这里出声（下面报告身份时一并说），也不阻断：重建一个包是完全正当的动作。
    prev_files, prev_err = None, ""
    if os.path.exists(zip_path):
        try:
            prev_files = pack_files(zip_path)
        except Exception as ex:
            prev_err = "%r" % (ex,)

    # 行尾口径：**一次** `git ls-files --eol`，下面所有随包源码都按它对齐（见 eol_text）。
    # 为什么不在各复制点各查一次：那既慢，又会让"同一份包里的文件按不同口径产出"成为可能。
    eol_map = eol_text.index_eol_map(ROOT)

    log("== eaglermc-gate 内部安装包 · 一键重建 ==")
    log("   仓库: %s" % ROOT)
    log("   版本: %s" % version)
    log("   输出: %s" % out)
    if eol_map:
        log("   行尾: 随包源码按 git 索引对齐（%d 个已跟踪文件可比对）" % len(eol_map))

    # 1) stage（幂等：整体重建）
    # 清不掉 = 真做不下去（下面要往同一个目录写同名文件，残留会把新包污染成混合体），
    # 所以这里 fail-closed，但要说人话：哪几个文件、谁可能占着、绕开的办法。
    stale = clear_dir(stage)
    if stale:
        log("✗ 上一次的安装包目录清不掉（有 %d 个文件被别的进程占用）：" % len(stale))
        for s in stale[:5]:
            log("    %s" % s)
        log("  处置：关掉可能占着它的程序（编辑器预览 / 杀毒扫描 / 另一份 pack_release 仍在跑）后重试；")
        log("        或换一个输出目录：python sandbox/pack_release.py --out <别的目录>")
        return 1
    os.makedirs(stage, exist_ok=True)

    # 2) wheel
    if a.wheel:
        if not os.path.isfile(a.wheel):
            log("✗ --wheel 指定的文件不存在：%s" % a.wheel)
            return 1
        wheel_name = os.path.basename(a.wheel)
        shutil.copy2(a.wheel, os.path.join(stage, wheel_name))
        log("[1/5] wheel：复用 %s" % wheel_name)
    else:
        log("[1/5] 构建 wheel ...")
        wheel_dir = os.path.join(out, "_wheel")
        locked = clear_dir(wheel_dir)
        if locked:
            # 与 stage 的处置相反：这里**不失败**。构建目录只是一次性的中转站，
            # 陈旧的轮子（比如上次留下、又被某进程占住的）没有任何理由挡住新产物 ——
            # 换一个全新的临时子目录即可，那堆占用文件就地不动。
            log("  - 注意：%s 里有 %d 个文件被占用、清不掉，本次改用新目录继续构建"
                % (wheel_dir, len(locked)))
            wheel_dir = tempfile.mkdtemp(prefix="_wheel-", dir=out)
        whl = build_wheel(wheel_dir)
        if not whl:
            log("✗ wheel 构建失败：python -m build / uv / pip wheel 均不可用或失败。")
            log("  处置：装 uv（或 pip install build）后重试，或 --wheel 指定已有轮子。")
            return 1
        wheel_name = os.path.basename(whl)
        shutil.copy2(whl, os.path.join(stage, wheel_name))
        log("      → %s" % wheel_name)

    # 3) marketplace
    log("[2/5] 构建 marketplace ...")
    builder = os.path.join(ROOT, "integrations", "plugins", "build_marketplace.py")
    if not os.path.isfile(builder):
        log("✗ 缺少构建器：%s" % builder)
        return 1
    mkt_dst = os.path.join(stage, "marketplace")
    # `--keep-placeholders` **不设开关**：出货件与机器无关是包的定义，不是构建选项。
    # 就地打戳（打上打包机的 python/工程路径）正是"漏跑 fix_paths 就用别人的路径"的源头，
    # 所以这里连一条能把它打回来的路都不留；要本机自用请直接跑 build_marketplace.py。
    r = subprocess.run([sys.executable, builder, "--harness", ROOT, "--out", mkt_dst,
                        "--python", a.python, "--keep-placeholders"], cwd=ROOT)
    if r.returncode != 0:
        log("✗ marketplace 构建失败（rc=%s）" % r.returncode)
        return 1
    if not os.path.isfile(os.path.join(mkt_dst, ".claude-plugin", "marketplace.json")):
        log("✗ marketplace 产物不完整（缺 .claude-plugin/marketplace.json）")
        return 1

    # 4) 文档
    log("[3/5] 组装文档 ...")
    eol_text.copy_shipped(os.path.join(ROOT, "docs", "packaging", "OPENCLAW.md"),
                          os.path.join(stage, "OPENCLAW.md"), ROOT, eol_map)
    eol_text.copy_shipped(os.path.join(ROOT, "LICENSE"),
                          os.path.join(stage, "LICENSE"), ROOT, eol_map)
    note_src = os.path.join(ROOT, "docs", "packaging", "INSTALL.md")
    if not os.path.isfile(note_src):
        log("✗ 缺少安装说明来源：%s" % note_src)
        return 1
    note = read_text(note_src).replace("__VERSION__", version).replace("__WHEEL__", wheel_name)
    write_text(os.path.join(stage, "安装说明.md"), note)
    boot_src = os.path.join(ROOT, "sandbox", "bootstrap.py")
    if not os.path.isfile(boot_src):
        log("✗ 缺少引擎引导工具：%s" % boot_src)
        return 1
    eol_text.copy_shipped(boot_src, os.path.join(stage, "bootstrap.py"), ROOT, eol_map)
    log("      + bootstrap.py（引擎引导工具：clone 引擎仓）")
    fixer_src = os.path.join(ROOT, "sandbox", "fix_paths.py")
    if not os.path.isfile(fixer_src):
        log("✗ 缺少路径适配工具：%s" % fixer_src)
        return 1
    eol_text.copy_shipped(fixer_src, os.path.join(stage, "fix_paths.py"), ROOT, eol_map)
    log("      + fix_paths.py（换机器路径适配工具）")
    uninstaller_src = os.path.join(ROOT, "sandbox", "uninstall.py")
    if not os.path.isfile(uninstaller_src):
        log("✗ 缺少卸载工具：%s" % uninstaller_src)
        return 1
    eol_text.copy_shipped(uninstaller_src, os.path.join(stage, "uninstall.py"), ROOT, eol_map)
    log("      + uninstall.py（一键卸载工具）")
    upgrader_src = os.path.join(ROOT, "sandbox", "upgrade.py")
    if not os.path.isfile(upgrader_src):
        log("✗ 缺少升级工具：%s" % upgrader_src)
        return 1
    eol_text.copy_shipped(upgrader_src, os.path.join(stage, "upgrade.py"), ROOT, eol_map)
    log("      + upgrade.py（一键升级工具）")

    # 4.5) PACK-INFO.json —— 出货件的身份（版本 + 构建时刻 + 整包指纹 + 市场子树指纹）
    #     为什么需要：插件的"版本"是缓存目录的分键，而 `claude plugin update` 只在
    #     **版本号变了**时才搬运内容。实测（2026-09-17，隔离 CLAUDE_CONFIG_DIR）：
    #     同版本号、内容不同的两个包，update 会打印绿色的"already at the latest version"
    #     然后**什么都没换** —— 同事以为升级成功了，其实还在跑旧包，且没有任何一步报错。
    #     所以同版本重复出包必须让人看见，而不是让它静静发生。
    #     口径（2026-09-17 扩，用户拍板）：指纹覆盖**整包**（除 PACK-INFO.json / SHA1SUMS.txt
    #     两个自指文件）。此前只覆盖 marketplace/ —— 于是"只改了工具/文档的同版本重打包"
    #     指纹纹丝不动，最该响的那一次不响（现场：reports/_falsify_v1_lock.txt §⑥）。
    files = pack_files(stage)
    # 整包指纹**只在这里定义一处**（`pack_fingerprint`）：`main()` 不另算一遍。
    # 曾经 `main()` 里写的是内联的 `fingerprint(files)`，同值但两处定义 —— 于是
    # "把口径退回只覆盖 marketplace/"这种改动可以只落在一边，两边不一致却没人看得出来
    # （变异 M34 第一次就是这么被拒的：打包器记的是宽口径、校验器算的是窄口径，
    # 于是一个**没被动过**的包也会被判"指纹不符"）。口径只有一处，改动才只有一种后果。
    pack_fp = pack_fingerprint(stage)
    mkt_fp = market_fingerprint(stage)
    pack_id = pack_fp[:12]
    write_text(os.path.join(stage, "PACK-INFO.json"), json.dumps({
        "version": version,
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "pack_sha1": pack_fp,
        "marketplace_sha1": mkt_fp,
        "pack_id": pack_id,
    }, ensure_ascii=False, indent=2) + "\n")
    log("[3.5/5] PACK-INFO.json  pack_id=%s  version=%s" % (pack_id, version))

    if prev_err:
        log("  · 上一次的 zip 读不动（%s）→ 本次没法与它比对身份。" % prev_err)
    elif prev_files is not None and fingerprint(prev_files) != pack_fp:
        # 刻意**不失败**：重建一个包是完全正当的动作（补丁、重跑），构建脚本没资格因此拦住人；
        # 但这件事必须吵到人看得见 —— 它是"同事装不上新东西"的**唯一**前兆。
        log("")
        log("⚠ 同一个版本号下已经存在过一份**内容不同**的包（%s）：" % version)
        log("    上一次里记的 pack_id=%s   本次 pack_id=%s"
            % (str((prev_info or {}).get("pack_id") or "（旧格式，没记）"), pack_id))
        diffs = diff_files(prev_files, files)
        for ln in diffs[:8]:
            log("      %s" % ln)
        if len(diffs) > 8:
            log("      …另有 %d 处（想看全：把两份包都解开，逐文件对 SHA1SUMS.txt）" % (len(diffs) - 8))
        log("  危害：插件按版本号分键取内容，`claude plugin update` 会认为"
            "「已是最新」而**什么都不换**——")
        log("        同事拿到新包却仍在跑旧包，而且那一步是**绿的**，没有任何报错。")
        log("  处置：确认要发新版就先把 VERSION 提到新版本号（例如 %s → 下一版），再打一次包；"
            % version)
        log("        只是本机重跑/补丁、不分发，可以忽略这条。")
        log("")

    # 5) SHA1SUMS
    log("[4/5] 生成 SHA1SUMS.txt ...")
    lines = []
    for dp, dns, fns in os.walk(stage):
        dns.sort()
        for fn in sorted(fns):
            fp = os.path.join(dp, fn)
            rel = os.path.relpath(fp, stage).replace("\\", "/")
            if rel == "SHA1SUMS.txt":
                continue
            lines.append("%s  %s" % (sha1_file(fp), rel))
    lines.sort(key=lambda x: x.split("  ", 1)[1])
    write_text(os.path.join(stage, "SHA1SUMS.txt"), "\n".join(lines) + "\n")
    log("      %d 条" % len(lines))

    # 6) zip
    log("[5/5] 打包 zip ...")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    n = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, dns, fns in os.walk(stage):
            dns.sort()
            for fn in sorted(fns):
                fp = os.path.join(dp, fn)
                arc = pkg_name + "/" + os.path.relpath(fp, stage).replace("\\", "/")
                z.write(fp, arc)
                n += 1
    # 7) 出货件契约校验（评审 v13 高 6）：**在打印 ✅ 之前**打开真实产物逐项对账。
    # 这一步就是本缺陷的补丁：脚本此前从没看过 wheel 里面，于是"源码改好了、轮子里还是旧的"
    # 能被一路 ✅ 到底。校验不过就不许说"完成" —— 打包脚本的价值在于产物可信，不是文件变多。
    verifier = os.path.join(ROOT, "sandbox", "verify_release.py")
    if os.path.isfile(verifier):
        log("")
        log("[校验] 出货件契约（打开真实产物对账）...")
        rv = subprocess.run([sys.executable, verifier, stage], cwd=ROOT)
        if rv.returncode != 0:
            log("")
            log("✗ 产物未通过契约校验（rc=%s）——**不要分发这个包**。" % rv.returncode)
            log("  常见原因：构建工具产出的元数据与 pyproject 不一致、模块没被打进去。")
            return 1
    else:
        log("")
        log("⚠ 未找到 sandbox/verify_release.py，跳过出货件契约校验（产物内容未经对账）")

    log("")
    log("✅ 完成：%s" % zip_path)
    log("   %d 个文件 ｜ %.1f KB ｜ zip SHA1: %s" % (n, os.path.getsize(zip_path) / 1024.0, sha1_file(zip_path)))
    log("   文件包（stage）: %s" % stage)
    log("   安装：见包内「安装说明.md」；卸载：包内 uninstall.py（预演 → --yes）；校验：解压后对照 SHA1SUMS.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
