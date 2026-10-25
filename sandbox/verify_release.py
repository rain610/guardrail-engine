#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出货件契约校验 —— 打开**真实产物**逐项对账，而不是对着源码自我印证。

背景（评审 v13 高 6 / 低 25）
================================================================================
`dist/install-package/` 下三个版本的 wheel 里**都没有 `_shell.py`**，元数据还写着
`Requires-Python: >=3.8`（而 `pyproject.toml` 早已是 `>=3.11`，注释里专门警告过
"别再写 >=3.8"）。根因不是构建配置坏 —— 是**产物陈旧**（wheel 建于 2026-09-14 22:16，
`_shell.py` 2026-09-16 才加、pyproject 2026-09-15 才改），而**全流程没有任何一步
打开过产物看一眼**：`release.yml` 只校验了 zip 的 SHA1/大小/文件数，`pack_release.py`
打印 ✅ 时也没看过 wheel 里面。于是"修好了但没出货"这件事没有任何地方会红。

同类问题在同一次评审里还有一条（§07 ③）：`tests/test_agentguard.py::_wheel_form()`
是把**当前工作树**的 .py 拷进临时目录再扮成轮子 —— 它证明的是"源码是对的"，
不是"出货的那个轮子是对的"。自我印证看起来很像验收，但它不问那个真问题。

因此本脚本只做一件事：**把产物当成第三方交付物来验**，每一项都对着"单一来源"比：
  · 轮子里有没有该有的模块   ← 对照 `guardrail_engine/` 目录（**不写死清单**：写死清单
    正是本次漂移的成因；将来加模块时会自动纳入检查）
  · 轮子里有没有自带引擎     ← `guardrail_engine/_engine/harness/run_gate.py`（否则 `pip install`
    之后仍要 clone 引擎，§5.2 的采用障碍）；且 `config/harness.local.toml` / 旧版 `.yaml` 不许进包
    （那是打包机的本机路径）
  · `Requires-Python`        ← 对照 `pyproject.toml` 的 `requires-python`
  · 入口点 / 包名 / 版本      ← 对照 `pyproject.toml` 与 `VERSION` 文件
  · 安装包完整性              ← 对照 `SHA1SUMS.txt` 与实际文件（双向：多列/漏列/哈希不符）
  · 出货件身份                ← 对照包实际内容重算指纹（`pack_sha1` 整包 / `marketplace_sha1`
    市场子树；**包内自洽**，答的是"与上一次那份是不是同一份"）
  · 包内工具与仓库同源        ← 对照 `sandbox/*.py`（**包 ↔ 源**，与上一条问的不是同一个问题）
  · 换机器可用性              ← 真跑一遍 `fix_paths.py`，断言 `.mcp.json` 里**全部**
    机器相关字段都被改写（新增一个它不认识的字段 → 这里红，而不是等同事装不上）

**不**断言"`.mcp.json` 不含绝对路径"：插件机制要求绝对路径，随包带适配器是设计
（`fix_paths.py` 的 docstring 与《安装说明》都写了）。断言"没有绝对路径"会把设计判成缺陷。
真正要防的是"有了适配器、但适配器盖不住那个字段"—— 那才是同事装不上的形状。

用法
================================================================================
    python sandbox/verify_release.py                    # 默认验当前 VERSION 对应的包
    python sandbox/verify_release.py <包目录> [更多路径]  # 目录 / .whl / .zip 均可
退出码：0 = 通过；1 = 产物有问题；2 = 用法/环境错误（找不到产物等）
"""
import argparse
import hashlib
import io
import json
import os
import re
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
SRC_PKG = os.path.join(ROOT, "guardrail_engine")    # 包源码单一来源
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import eol_text                                        # noqa: E402  （行尾口径的唯一一处定义）


class Problems(object):
    """收集问题（不抛异常）：一次跑完把所有不符项报全，而不是让人改一条跑一次。"""

    def __init__(self):
        self.items = []

    def add(self, where, msg):
        self.items.append("%s：%s" % (where, msg))

    def ok(self, n=1):
        pass


def _sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _read(p, **kw):
    with io.open(p, encoding=kw.pop("encoding", "utf-8"), errors=kw.pop("errors", "replace")) as f:
        return f.read()


def _pep440(v):
    """把本工程的版本写法折算成 PEP 440 形态（**只覆盖本工程用到的形状**）。

    为什么需要：`VERSION` 文件写 `0.1.2-dev`，而 wheel 元数据里必然是 `0.1.2.dev0`
    —— setuptools 做 PEP 440 归一化，这是**正确行为**，不是缺陷。首版直接字面比较，
    于是对合格产物报了"版本不符"（假警报）。它同时也是本轮 高 6 的旁证：
    不该把"我没见过这个形状"当成"产物错了"。

    认不出的形状**原样返回** —— 那时按字面比较并报错：宁可报错，也不猜。
    """
    s = (v or "").strip().lower()
    if s.startswith("v"):
        s = s[1:]
    m = re.match(r"^(\d+(?:\.\d+)*)[-_.]?(dev|a|b|c|rc|alpha|beta|pre|post)(\d*)$", s)
    if not m:
        return s
    num, tag, n = m.group(1), m.group(2), m.group(3)
    tag = {"alpha": "a", "beta": "b", "c": "rc", "pre": "rc"}.get(tag, tag)
    return "%s.%s%s" % (num, tag, n or "0")


def _facts():
    """取"单一来源"事实：pyproject / VERSION / 包源码模块清单。"""
    try:
        import tomllib                      # 3.11+ 标准库（本工程门槛就是 3.11）
    except Exception as ex:                 # pragma: no cover
        raise SystemExit("✗ 需要 Python 3.11+（tomllib）：%r" % (ex,))
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        proj = tomllib.load(f)["project"]
    version = _read(os.path.join(ROOT, "VERSION")).strip()
    modules = sorted(n for n in os.listdir(SRC_PKG) if n.endswith(".py"))
    return proj, version, modules


# --------------------------------------------------------------------------- 轮子
def check_wheel(wheel, p, proj, version, modules, label="wheel"):
    """验一个 .whl：模块齐全 / Requires-Python / 入口点 / 包名版本。"""
    try:
        z = zipfile.ZipFile(wheel)
    except Exception as ex:
        p.add(label, "打不开（%r）：%s" % (ex, wheel))
        return
    with z:
        names = z.namelist()
        # ① 源码里每一个 .py 都必须在轮子里
        # 路径形态：wheel 条目是相对路径，包内文件就是 `guardrail_engine/x.py`
        # （**没有**前导目录）—— 同时认带前导目录的形态，别把合法的放置判成缺失。
        missing = [m for m in modules
                   if not any(n == "guardrail_engine/" + m
                              or n.endswith("/guardrail_engine/" + m)
                              for n in names)]
        if missing:
            p.add(label, "轮子里缺模块 %s（源码 %s 下一共 %d 个 .py）—— "
                         "「修好了但没出货」就是这个形状"
                  % ("、".join(missing), "guardrail_engine/", len(modules)))
        # ①a2 迁移期兼容壳：老用户的 `agentguard` 命令与 `<engine>/agentguard/mcp.py`
        #     注册在 v0.3 之前必须继续可用。缺了它，升级轮子等于把老接入当场断掉。
        shim_missing = [n for n in ("agentguard/__init__.py", "agentguard/cli.py",
                                    "agentguard/mcp.py", "agentguard/_shell.py")
                        if n not in names]
        if shim_missing:
            p.add(label, "轮子里缺迁移期兼容壳：%s —— 旧命令 / 旧 MCP 注册会当场断掉"
                  % "、".join(shim_missing))
        # ①b 自带引擎（评审 2026-10-21 §5.2）：shell-only 的 wheel 装完仍要 clone 引擎，
        #     "装完就能跑"是断的。只查入口文件：它们是引擎可运行的充要条件，且不写死清单。
        engine_required = ("guardrail_engine/_engine/harness/run_gate.py",
                           "guardrail_engine/_engine/checks/example_code_check.py",
                           "guardrail_engine/_engine/config/harness.toml")
        missing_engine = [n for n in engine_required if n not in names]
        if missing_engine:
            p.add(label, "轮子里没有自带引擎：缺 %s —— `pip install` 之后仍要 clone 引擎并设 "
                         "GUARDRAIL_ENGINE_DIR（§5.2 的采用障碍）；检查 setup.py 的 build_py 是否把 "
                         "引擎复制进了 guardrail_engine/_engine/" % "、".join(missing_engine))
        # ①c 打包机的本机事实 / 缓存不得进包
        # 判据按前缀而不是两个精确文件名：本机真身 local.toml / 旧版 local.yaml 都在
        # 排除清单里，但「改名归档」的副本（如 local.yaml.disabled-20261021）曾真被
        # 打进过 wheel —— 归档者的本意是留个备份，出货却把打包机路径发给了所有人。
        # `.example.` 例外：模板是给所有人抄的，必须出货。
        leaks = [n for n in names
                 if ("/config/harness.local." in n and "harness.local.example." not in n)
                 or "__pycache__/" in n or n.endswith((".pyc", ".pyo"))]
        if leaks:
            p.add(label, "轮子里混进了本机配置/缓存：%s —— harness.local.* 是打包机的仓库路径，"
                         "发出去等于把打包机的目录结构交给所有人" % "、".join(leaks[:5]))
        # ② Requires-Python 必须等于 pyproject
        metas = [n for n in names if n.endswith(".dist-info/METADATA")]
        if not metas:
            p.add(label, "没有 .dist-info/METADATA —— 这不是一个合法 wheel")
            return
        text = z.read(metas[0]).decode("utf-8", "replace")
        want_py = str(proj.get("requires-python") or "").strip()
        got_py = ""
        got_name = got_ver = ""
        for line in text.splitlines():
            if line.startswith("Requires-Python:"):
                got_py = line.split(":", 1)[1].strip()
            elif line.startswith("Name:"):
                got_name = line.split(":", 1)[1].strip()
            elif line.startswith("Version:"):
                got_ver = line.split(":", 1)[1].strip()
        if got_py != want_py:
            p.add(label, "Requires-Python 是 %r，pyproject 是 %r —— "
                         "pip 会按前者决定装不装，装上了才在运行时炸"
                  % (got_py, want_py))
        if got_name != proj.get("name"):
            p.add(label, "包名 %r ≠ pyproject %r" % (got_name, proj.get("name")))
        if _pep440(got_ver) != _pep440(version):
            p.add(label, "版本 %r ≠ VERSION 文件 %r（按 PEP 440 折算后仍不等）"
                  % (got_ver, version))
        # ③ 入口点必须与 [project.scripts] 一致（少一个 = 装完没有命令）
        eps = [n for n in names if n.endswith(".dist-info/entry_points.txt")]
        got_eps = {}
        if eps:
            for line in z.read(eps[0]).decode("utf-8", "replace").splitlines():
                if "=" in line and not line.strip().startswith("["):
                    k, v = line.split("=", 1)
                    got_eps[k.strip()] = v.strip()
        for name, target in (proj.get("scripts") or {}).items():
            if got_eps.get(name) != target:
                p.add(label, "入口点 %s 是 %r，pyproject 要求 %r"
                      % (name, got_eps.get(name), target))


# --------------------------------------------------------------------- 安装包目录
#: 包内工具 → 仓库里的单一来源。打包器是 `shutil.copy2` 过去的，所以"包里那份 === 仓库那份"
#: 本该是恒等式 —— 这条检查就是来盯这个恒等式的（它破了的时候，别的地方全都是绿的）。
SHIPPED_TOOLS = {"bootstrap.py": os.path.join("sandbox", "bootstrap.py"),
                 "fix_paths.py": os.path.join("sandbox", "fix_paths.py"),
                 "uninstall.py": os.path.join("sandbox", "uninstall.py"),
                 "upgrade.py": os.path.join("sandbox", "upgrade.py")}


def _check_shipped_tools(stage, p):
    """⑧ 包内工具必须与仓库那份逐字节相同 —— 否则同事跑的是**另一支脚本**。

    为什么值得单列（2026-09-17，升级链路剖析报告 U-5 现场）：**改了工具、忘了重打包**。
    这个形态的可怕之处是"每一处检查都绿"：
      · 校验器原先只查包内**有没有** upgrade.py（存在性）→ 有；
      · SHA1SUMS.txt 是**包内自洽**（包里那份的哈希，与仓库无关）→ 对得上；
      · PACK-INFO 的指纹是**包内自洽**的（包内容算出来的，与仓库无关）→ 纹丝不动；
      · 打包器只在**同版本重打包**时与上一次的包比指纹 —— "上一次"若本来就是旧的，
        比出来的结论是"没变"，照样不出声。
    四处都绿，而 `dist/` 里那份升级脚本还是上一版 —— 同事照文档跑的是另一支脚本。
    实测现场：改完 `sandbox/upgrade.py` 之后，`python sandbox/verify_release.py` 照样 rc=0。

    ★ 2026-09-17 把指纹扩到整包之后（G3），**本条仍然不可省**：指纹回答的是"这次这份包与
    上一次那份是不是同一份内容"（包 ↔ 包），本条回答的是"包里这份与**仓库源码**是不是同一份"
    （包 ↔ 源）。扩指纹不会、也不可能让包里多出一份"我当时是从哪个源码打的"的记录 ——
    两个问题不同，别把它们合并成一条。

    只比 `SHIPPED_TOOLS` 里那几支 `.py`（同事要动手跑的东西；名单在常量里，不在注释里
    —— 写死"三支/四支"的注释都会在下一次加工具时烂掉），不比对**派生**出来的 `安装说明.md`：那份由
    `__VERSION__` / `__WHEEL__` 现场替换而成，正文会被频繁编辑，逐字节比会变成"每改一句话
    就要重打包"的噪音 —— 它该验的是"替换有没有真的发生"，那由 ① 管（轮子名必须在文档里出现）。
    """
    for name, rel in sorted(SHIPPED_TOOLS.items()):
        got_p = os.path.join(stage, name)
        want_p = os.path.join(ROOT, rel)
        if not os.path.isfile(got_p) or not os.path.isfile(want_p):
            continue        # 缺文件由 check_package 的「缺 X」那条报，这里不重复报
        try:
            with open(got_p, "rb") as f:
                got = f.read()
            # 「仓库那份」按**该出货的字节**读（= 打包器看到的那份，见 sandbox/eol_text.py）。
            # 不这么读的话，本条会在**检出平台**上假红：包内是打包时对齐过的 LF，仓库工作树
            # 在 Windows 上是 CRLF —— 而这两份"看起来不一样"的文件，恰恰是**同一份**。
            # 判断必须与打包器同口径，否则锁在盯一个它自己造出来的差异。
            want = eol_text.read_shipped(want_p, ROOT)
        except OSError as ex:
            p.add(got_p, "读不出来（%r）" % (ex,))
            continue
        if got != want:
            p.add(got_p, "包内 %s 与仓库 %s 不是同一份（包内 %d 字节 / 仓库 %d 字节）—— "
                         "同事跑的是另一支脚本；重跑 `python sandbox/pack_release.py` 重打包"
                  % (name, rel, len(got), len(want)))


def check_package(stage, p, proj, version, modules):
    """验一个「离线安装包」目录：文件齐 / SHA1SUMS 双向对账 / 适配器盖得住 .mcp.json。"""
    wheels = [n for n in os.listdir(stage) if n.endswith(".whl")]
    if len(wheels) != 1:
        p.add(stage, "应有且仅有 1 个 wheel，实际 %d 个：%s" % (len(wheels), wheels))
    for w in wheels:
        check_wheel(os.path.join(stage, w), p, proj, version, modules,
                    label="%s/%s" % (os.path.basename(stage), w))

    # ① 安装说明里点名的轮子名必须真的在包里（用户是照着那份文档敲命令的）
    note = os.path.join(stage, "安装说明.md")
    if not os.path.isfile(note):
        p.add(stage, "缺「安装说明.md」")
    elif wheels:
        txt = _read(note)
        for w in wheels:
            if w not in txt:
                p.add(note, "安装说明里没出现包内实际轮子名 %r —— 用户照文档敲会找不到文件" % w)

    # ② SHA1SUMS.txt 双向对账（漏列 / 多列 / 哈希不符）
    sums = os.path.join(stage, "SHA1SUMS.txt")
    if not os.path.isfile(sums):
        p.add(stage, "缺 SHA1SUMS.txt")
    else:
        listed = {}
        for line in _read(sums).splitlines():
            if "  " in line:
                h, rel = line.split("  ", 1)
                listed[rel.strip()] = h.strip()
        actual = {}
        for dp, dns, fns in os.walk(stage):
            dns.sort()
            for fn in sorted(fns):
                fp = os.path.join(dp, fn)
                rel = os.path.relpath(fp, stage).replace("\\", "/")
                if rel != "SHA1SUMS.txt":
                    actual[rel] = _sha1(fp)
        for rel in sorted(set(actual) - set(listed)):
            p.add(sums, "漏列：%s" % rel)
        for rel in sorted(set(listed) - set(actual)):
            p.add(sums, "列了但包里没有：%s" % rel)
        for rel in sorted(set(listed) & set(actual)):
            if listed[rel] != actual[rel]:
                p.add(sums, "哈希不符：%s（包内 %s，清单 %s）" % (rel, actual[rel], listed[rel]))

    # ③ 换机器可用性：真跑 fix_paths.py 到临时副本，断言**机器相关字段全被改写**
    fixer = os.path.join(stage, "fix_paths.py")
    if not os.path.isfile(fixer):
        p.add(stage, "缺 fix_paths.py —— 包内 .mcp.json 写的是打包机的绝对路径，"
                     "没有适配器同事就装不上")
    else:
        _check_adapter(stage, fixer, p)

    # ④ 出货件本身必须与机器无关（占位符还在）：③ 验的是"适配器补得上"，
    #    ④ 验的是"包本身没带别人的路径"。两件事缺一不可 —— 只有 ③ 时，
    #    "包已经烧进打包机路径"这件事永远是绿的。
    for mj in _mcp_jsons(stage):
        _check_machine_independent(mj, p)

    # ⑤ 插件清单的版本必须与 VERSION 一致（插件缓存按它分键，同事也按它认版本）
    _check_plugin_version(stage, p, version)

    # ⑥ 出货件身份指纹必须对得上（一个没人核对过的指纹等于装饰）
    _check_pack_info(stage, p, version)

    for req in ("bootstrap.py", "uninstall.py", "upgrade.py", "OPENCLAW.md", "LICENSE"):
        if not os.path.isfile(os.path.join(stage, req)):
            p.add(stage, "缺 %s" % req)

    # ⑧ 包内那几支"动手用"的工具必须与仓库那份**逐字节相同**
    _check_shipped_tools(stage, p)

    # ⑦ 包内每一支顶层工具都要在安装说明里被点名（否则它是一支**没写进文档的工具**）
    #    为什么值得单列一条：包里的 .py 是同事要**照着文档敲**的东西。少写一段文档，
    #    工具就只有在人把包目录整个翻一遍时才会被发现 —— 而"包里有、文档里没有"这件事
    #    没有任何一步会报错（打包器照样 ✅，SHA1SUMS 照样对得上，因为它确实在包里）。
    #    只查文档提没提到，不查提到得对不对：措辞会变，工具名不会。
    if os.path.isfile(note):
        doc = _read(note)
        for name in sorted(n for n in os.listdir(stage) if n.endswith(".py")):
            if name not in doc:
                p.add(note, "包里带了 %s，但安装说明里一次都没提到它（同事不知道有这么个工具）"
                      % name)


# 装机时由 fix_paths.py 现场填充的三个字段（模板里的写法）
_PLACEHOLDERS = {"command": "__PY__",
                 "args[0]": "__MCP__",
                 "env.GUARDRAIL_ENGINE_DIR": "__HARNESS__"}


def _srv_fields(srv):
    """从 `mcpServers.guardrail-engine` 里取三个机器相关字段的现值（缺项取 None）。"""
    return {"command": (srv or {}).get("command"),
            "args[0]": ((srv or {}).get("args") or [None])[0],
            "env.GUARDRAIL_ENGINE_DIR": ((srv or {}).get("env") or {}).get("GUARDRAIL_ENGINE_DIR")}


def _check_machine_independent(mj, p):
    """出货件的 `.mcp.json` 必须仍是占位符 —— 这是"与机器无关"唯一的可判定形式。

    缺陷形状（2026-09-17 实测定性）：打包时 `build_plugin.py` 把**打包机的** python 与
    工程路径烧进 `.mcp.json`，同事漏跑 `fix_paths.py` 时（安装说明里"原地升级、机器没变
    可跳过"那句话最容易让人跳过）那份文件就变成指向别人的机器：MCP 服务起不来，
    **编辑期门禁整体无声失效**，而全程没有任何一步报错。

    留占位符后，同一个漏步以 `__PY__` 找不到解释器的方式当场失败，且"这份包还没适配"
    肉眼可辨。判据只针对 fix_paths.py 会填的那三个字段：别的字段本来就该是常量。
    """
    try:
        servers = json.loads(_read(mj))["mcpServers"]
        srv = servers["guardrail-engine"] if "guardrail-engine" in servers else servers["agentguard"]
    except Exception as ex:
        p.add(mj, "解析不出 mcpServers.guardrail-engine：%r" % (ex,))
        return
    got = _srv_fields(srv)
    for k, want in sorted(_PLACEHOLDERS.items()):
        if got[k] == want:
            continue
        p.add(mj, "出货件的 %s 是 %r，不是占位符 %r —— 这个包把**打包机**的路径烧进去了："
                  "同事漏跑 fix_paths.py 时会拿到别人的路径，MCP 起不来、编辑期门禁无声失效。"
                  "请用 pack_release.py 重新出包（它固定走 --keep-placeholders）。"
              % (k, got[k], want))


def _plugin_manifests(stage):
    """marketplace 下所有插件的 `.claude-plugin/plugin.json`（按目录名排序）。"""
    base = os.path.join(stage, "marketplace", "plugins")
    out = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            man = os.path.join(base, name, ".claude-plugin", "plugin.json")
            if os.path.isfile(man):
                out.append(man)
    return out


def _check_plugin_version(stage, p, version):
    """插件清单里的 `version` 必须与 `VERSION` 一致（按 PEP 440 折算）。

    为什么单独查：`.claude-plugin/plugin.json` 的 version 是插件缓存目录的**分键**，
    也是同事判断"我装的是哪一版"的唯一依据。它和 VERSION 脱钩时，包名 / 文档 /
    wheel 元数据**全都还是对的**，只有"看着像装上了、其实还是旧的"这一件事发生 ——
    正是最难发现、也最容易被归因成"同事没装对"的那一类。
    """
    mans = _plugin_manifests(stage)
    if not mans:
        p.add(stage, "marketplace/plugins 下找不到任何 .claude-plugin/plugin.json")
        return
    for man in mans:
        try:
            got = json.loads(_read(man)).get("version")
        except Exception as ex:
            p.add(man, "解析不了：%r" % (ex,))
            continue
        if _pep440(got) != _pep440(version):
            p.add(man, "插件清单 version=%r，VERSION 文件=%r（PEP 440 折算后仍不等）"
                  % (got, version))


_PACKER = []


def _packer():
    """把 `sandbox/pack_release.py` 当模块加载，只为拿**它算指纹的那一个函数**。

    刻意不在这里抄一遍算法：抄一遍就是两套定义，将来改了一处、另一处照样绿 ——
    那正是"给副本设锁"。指纹的定义只能有一份。
    """
    if not _PACKER:
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pack_release.py")
        spec = importlib.util.spec_from_file_location("_pack_release_fp", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _PACKER.append(mod)
    return _PACKER[0]


def _check_pack_info(stage, p, version):
    """`PACK-INFO.json` 必须在，且它记的指纹要**真的等于**包的当前内容。

    为什么不只看"文件在不在"：指纹的全部价值在于它是包的身份（"同事手上那个是不是
    我这次做的那个"）。一个没人核对过的指纹会被当成身份用，却可以和包内容毫无关系。
    所以这里重算一遍对账 —— 与 SHA1SUMS 的双向对账同一个立场。

    两个字段都核，口径不同、都要核：
      · `pack_sha1`（整包，除 PACK-INFO.json / SHA1SUMS.txt 两个自指文件）—— 2026-09-17 扩的。
        此前只覆盖 marketplace/，于是"只改了工具或文档的同版本重打包"指纹不变、G3 不响；
      · `marketplace_sha1`（只有市场子树）—— 插件那条通道按版本号取内容，市场那一份单独有名字
        更好读，且旧包的 PACK-INFO 里只有它。

    旧格式的包（没有 `pack_sha1`）**判失败**，不是放行：能核的少了一半却一个字都不说，读的人
    会把这次的绿理解成"整包身份已核"—— 那正是这道对账要防的东西。所以点名说清它缺什么、
    并给出重打包这条命令。旧包是真会遇到的（同事手里、`dist/` 里），所以处置必须是**可执行的**，
    而不是一句"格式不对"。这与"核不出来就当没事"是两种相反的立场，别写成后者。
    """
    info_p = os.path.join(stage, "PACK-INFO.json")
    if not os.path.isfile(info_p):
        p.add(stage, "缺 PACK-INFO.json（出货件身份：版本 / 构建时刻 / 整包指纹 + 市场子树指纹）")
        return
    try:
        info = json.loads(_read(info_p))
    except Exception as ex:
        p.add(info_p, "解析不了：%r" % (ex,))
        return
    if _pep440(info.get("version")) != _pep440(version):
        p.add(info_p, "PACK-INFO.version=%r ≠ VERSION 文件 %r" % (info.get("version"), version))
    stage_abs = os.path.abspath(stage)
    want_pack = _packer().pack_fingerprint(stage_abs)
    if "pack_sha1" not in info:
        p.add(info_p, "PACK-INFO 是**旧格式**（没有 pack_sha1，只记了 marketplace_sha1）—— "
                      "本次只核了市场子树，整包身份无从核对；重跑 "
                      "`python sandbox/pack_release.py` 生成新格式")
    elif info.get("pack_sha1") != want_pack:
        p.add(info_p, "整包指纹与包实际内容不符（记录 %r，实算 %r）—— "
                      "包被改动过，或指纹记错了" % (info.get("pack_sha1"), want_pack))
    want_mkt = _packer().market_fingerprint(stage_abs)
    if info.get("marketplace_sha1") != want_mkt:
        p.add(info_p, "市场子树指纹与 marketplace/ 实际内容不符（记录 %r，实算 %r）—— "
                      "包被改动过，或指纹记错了" % (info.get("marketplace_sha1"), want_mkt))


_MACHINE_FIELDS = ("command", "args[0]", "env.GUARDRAIL_ENGINE_DIR")


def _mcp_jsons(stage):
    out = []
    for dp, dns, fns in os.walk(os.path.join(stage, "marketplace")):
        dns.sort()
        for fn in sorted(fns):
            if fn == ".mcp.json":
                out.append(os.path.join(dp, fn))
    return out


def _check_adapter(stage, fixer, p):
    """把包内 .mcp.json 抄到临时目录，用注入的假路径跑 fix_paths.py，看三个字段是否都变了。

    注入值刻意取"不可能与打包机相同"的字符串 —— 否则打包机恰好就是本机时，
    "没改写"看起来会像"改写成功"（假绿）。
    """
    mjs = _mcp_jsons(stage)
    if not mjs:
        p.add(stage, "marketplace 下找不到 .mcp.json")
        return
    tmp = tempfile.mkdtemp(prefix="vrel-")
    try:
        fake_harness = os.path.join(tmp, "fake-harness")
        os.makedirs(os.path.join(fake_harness, "guardrail_engine"))
        with io.open(os.path.join(fake_harness, "guardrail_engine", "mcp.py"),
                     "w", encoding="utf-8") as f:
            f.write("# 只为让 fix_paths 的 --harness 校验通过\n")
        fake_python = os.path.join(tmp, "fake-python.exe")
        with io.open(fake_python, "w", encoding="utf-8") as f:
            f.write("")
        for mj in mjs:
            copy = os.path.join(tmp, os.path.basename(os.path.dirname(mj)) + ".mcp.json")
            shutil.copyfile(mj, copy)
            r = subprocess.run([sys.executable, fixer, "--mcp-json", copy,
                                "--harness", fake_harness, "--python", fake_python],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=120, cwd=os.path.dirname(fixer))
            if r.returncode != 0:
                p.add(mj, "fix_paths.py 跑不通（rc=%s）：%s"
                      % (r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()[:200]))
                continue
            try:
                data = json.loads(_read(copy))
                servers = data["mcpServers"]
                srv = servers["guardrail-engine"] if "guardrail-engine" in servers \
                    else servers["agentguard"]
            except Exception as ex:
                p.add(mj, "适配后反而解析不了：%r" % (ex,))
                continue
            want = {
                "command": fake_python.replace(os.sep, "/"),
                "args[0]": (fake_harness + "/guardrail_engine/mcp.py").replace(os.sep, "/"),
                "env.GUARDRAIL_ENGINE_DIR": fake_harness.replace(os.sep, "/"),
            }
            got = {
                "command": srv.get("command"),
                "args[0]": (srv.get("args") or [None])[0],
                "env.GUARDRAIL_ENGINE_DIR": (srv.get("env") or {}).get("GUARDRAIL_ENGINE_DIR"),
            }
            # 先看打包时的原值里有没有"适配器不管"的机器相关字段：
            # 判据 = 原值不是本机可解析路径，或干脆形如绝对路径，而 fix_paths 没碰它。
            servers = json.loads(_read(mj))["mcpServers"]
            orig = servers["guardrail-engine"] if "guardrail-engine" in servers else servers["agentguard"]
            extra = _uncovered_machine_fields(orig)
            if extra:
                p.add(mj, "有机器相关字段 %s 不在 fix_paths.py 的改写范围内 —— "
                          "同事装上会用打包机的路径" % "、".join(extra))
            for k in _MACHINE_FIELDS:
                if got[k] != want[k]:
                    p.add(mj, "fix_paths.py 没把 %s 改成注入值（得到 %r，期望 %r）"
                          % (k, got[k], want[k]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _uncovered_machine_fields(srv):
    """列出 .mcp.json 里"看起来是打包机绝对路径、但 fix_paths 不会改"的字段。

    只报**同层级、同形状**的字段：顶层除了 command 之外还有别的绝对路径值，
    或 args 里第 1 个之后的绝对路径，或 env 里除 GUARDRAIL_ENGINE_DIR 外指向
    Windows/Unix 绝对路径的值。判据故意保守 —— 假警报的代价是下次真问题没人看。
    """
    out = []
    for k, v in (srv or {}).items():
        if k == "command":
            continue
        if k == "args":
            for i, a in enumerate(v or []):
                if i > 0 and isinstance(a, str) and _looks_absolute(a):
                    out.append("args[%d]" % i)
            continue
        if k == "env":
            for ek, ev in (v or {}).items():
                if ek != "GUARDRAIL_ENGINE_DIR" and isinstance(ev, str) and _looks_absolute(ev):
                    out.append("env.%s" % ek)
            continue
        if isinstance(v, str) and _looks_absolute(v):
            out.append(k)
    return out


def _looks_absolute(s):
    s = s.replace("\\", "/")
    return bool(re.match(r"^[A-Za-z]:/", s) or s.startswith("/"))


# ---------------------------------------------------------------------------- zip
def check_zip(zpath, stage, p):
    """zip 里每一个条目都要与包目录逐字节一致（附件名/内容被人动过要看得见）。"""
    if not os.path.isfile(zpath):
        p.add(zpath, "zip 不存在")
        return
    try:
        z = zipfile.ZipFile(zpath)
    except Exception as ex:
        p.add(zpath, "打不开（%r）" % (ex,))
        return
    base = os.path.basename(stage)
    with z:
        entries = [n for n in z.namelist() if not n.endswith("/")]
        inside = {}
        for e in entries:
            rel = e[len(base) + 1:] if e.startswith(base + "/") else None
            if rel is None:
                p.add(zpath, "条目不在包目录前缀下：%s" % e)
                continue
            inside[rel] = z.read(e)
        disk = {}
        for dp, dns, fns in os.walk(stage):
            for fn in fns:
                fp = os.path.join(dp, fn)
                disk[os.path.relpath(fp, stage).replace("\\", "/")] = fp
        for rel in sorted(set(disk) - set(inside)):
            p.add(zpath, "zip 里缺文件：%s" % rel)
        for rel in sorted(set(inside) - set(disk)):
            p.add(zpath, "zip 里有包目录中没有的文件：%s" % rel)
        for rel in sorted(set(inside) & set(disk)):
            with open(disk[rel], "rb") as f:
                if f.read() != inside[rel]:
                    p.add(zpath, "内容与包目录不一致：%s" % rel)


# --------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="出货件契约校验（打开真实产物逐项对账）")
    ap.add_argument("paths", nargs="*", help="包目录 / .whl / .zip（缺省：当前 VERSION 的安装包）")
    a = ap.parse_args(argv)

    proj, version, modules = _facts()
    targets = list(a.paths)
    if not targets:
        stage = os.path.join(ROOT, "dist", "install-package",
                             "guardrail-engine-package-v%s" % version)
        if not os.path.isdir(stage):
            print("✗ 找不到 %s" % stage)
            print("  说明：本仓库还没有 v%s 的安装包产物 —— 先跑 "
                  "`python sandbox/pack_release.py`。" % version)
            return 2
        targets = [stage]

    p = Problems()
    for t in targets:
        t = os.path.abspath(t)
        if not os.path.exists(t):
            print("✗ 路径不存在：%s" % t)
            return 2
        if t.endswith(".whl"):
            check_wheel(t, p, proj, version, modules, label=os.path.basename(t))
        elif t.endswith(".zip"):
            # zip 单独给定时，配套的包目录按同名找；找不到就只验"能打开 + 非空"
            stage = t[:-4]
            if os.path.isdir(stage):
                check_zip(t, stage, p)
            else:
                try:
                    with zipfile.ZipFile(t) as z:
                        if not [n for n in z.namelist() if not n.endswith("/")]:
                            p.add(t, "zip 是空的")
                except Exception as ex:
                    p.add(t, "打不开（%r）" % (ex,))
        elif os.path.isdir(t):
            check_package(t, p, proj, version, modules)
            zw = t + ".zip"
            if os.path.isfile(zw):
                check_zip(zw, t, p)
        else:
            print("✗ 不认识的产物类型：%s（要目录 / .whl / .zip）" % t)
            return 2

    print("== 出货件契约校验 ==")
    print("   单一来源：pyproject requires-python=%r ｜ VERSION=%s ｜ 包内模块 %d 个"
          % (proj.get("requires-python"), version, len(modules)))
    for t in targets:
        print("   产物：%s" % t)
    if p.items:
        print("")
        for it in p.items:
            print("❌ %s" % it)
        print("")
        print("❌ %d 项不符 —— 出货件与源码对不上。修法：重跑 "
              "`python sandbox/pack_release.py`；若是设计变了，先改单一来源。" % len(p.items))
        return 1
    print("")
    print("✅ 出货件与源码一致（模块齐全 / 元数据一致 / 清单双向对账 / 适配器盖得住 .mcp.json）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
