#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""guardrail_engine 壳层公用件 —— 只做「环境定位 / 转发」，不含任何门禁判定逻辑。

为什么会有这个文件（不是为好看，是因为真出过一次问题）
=====================================================
2026-09-16 剖析报告 N1 实测：`cli.py` 与 `mcp.py` 各自实现了一遍「取版本号」，
而两遍**语义不同** —— mcp 那份在找不到 VERSION 文件时直接返回 `0.0.0-dev`，
cli 那份会继续回退到包元数据 `importlib.metadata`。

在本仓库里跑看不出差别（两者都能取到 VERSION 文件）；差别在**轮子安装形态**下才暴露：
wheel 里只有 `agentguard/{__init__,cli,mcp}.py`（已用 zipfile 核对 v0.1.0/0.1.1/0.1.2
三个产物，**没有 VERSION 文件**），此时同一个包会出现两种自我描述 ——
`agentguard version` 说 `0.1.2`，而 MCP 的 `initialize` 说 `0.0.0-dev`。

ADR-0001 铁律 3 写着「版本单一来源（四处一致，测试锁定）」，但**没有任何测试覆盖
轮子形态**，所以这个分叉一直没被发现。重复本身不是问题，「两份实现悄悄语义不同」
才是问题 —— 故把根定位 / 版本 / 解释器 / 版本前置检查 / 归属匹配收敛到这里一份。

纪律
====
本模块仍属**壳层**：只做环境与转发，不做行号运算、不重定义引擎符号。它登记在
`tests/packaging/test_packaging_contract.py` 的 `SHELL_FILES` 里，接受与其它壳完全相同的禁令检查。

导入方式（三种运行形态都要成立，改这里前先读这三行）
==================================================
  · 脚本形态 `python guardrail_engine/cli.py` → 脚本目录本就在 sys.path[0]，`import _shell` 可；
  · 安装形态 `guardrail_engine.cli:main`（entry_points）→ 包已装进 site-packages，**必须**
    用相对导入 `from . import _shell`（裸 `import _shell` 会 ModuleNotFoundError）；
  · exec_module 形态（tests/engine/test_preflight.py 直接按路径 exec 壳文件）→ 没有包上下文，
    相对导入抛 ImportError，回落到裸导入。
故各壳里用 `try: from . import _shell / except ImportError: import _shell` 双形态导入，
并在裸导入前保证本目录已在 sys.path（safe-path 解释器 -P 不会自动加）。
"""
from __future__ import annotations

import os
import re
import sys
import tomllib

# UTF-8 兜底：本模块的 `require_python` 会往 stderr 写中文 —— 宿主控制台可能是 GBK/ASCII
# （同一坑在 auto-gate/shims 上踩过三次）。各壳也各自兜了一遍，这里是**独立**的一份：
# `sandbox/list_entries.py` 的判据是「会输出 + 含非 ASCII 就必须要 reconfigure」，本模块
# 两条都命中，且它自己也确实需要（不能假设调用方先兜过）。
try:
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

__all__ = ["find_root", "read_version", "py_exe", "require_python", "find_bash",
           "gate_owning", "GATE_RE", "flag_like", "packaged_engine", "package_dir",
           "ENGINE_DIRS", "ENGINE_FILES", "ENGINE_EXCLUDE",
           "BRAND", "CONFIG_DIRNAME", "LEGACY_CONFIG_DIRNAME", "config_home",
           "INIT_MARKER", "LEGACY_INIT_MARKER", "init_marker_names"]

# ---------- 品牌与迁移期兼容（2026-10-25 改名 guardrail-engine） ----------
# 新名为主、旧名双读：配置目录、环境变量、init 标记都要让 v0.1/v0.2 的安装继续可用。
# 旧名在 v0.3 移除；`harness/`、`checks/`、`config/harness.toml` 这些**磁盘布局**不改 ——
# 它们是受管仓里的结构契约，不是品牌。
BRAND = "guardrail-engine"
CONFIG_DIRNAME = ".guardrail-engine"
LEGACY_CONFIG_DIRNAME = ".agentguard"
INIT_MARKER = ".guardrail-engine-init.json"
LEGACY_INIT_MARKER = ".agentguard-init.json"


def config_home():
    """本机配置目录：新目录优先；不存在而旧目录在 → 继续用旧目录（不迁移、不丢配置）。"""
    home = os.path.expanduser("~")
    new = os.path.join(home, CONFIG_DIRNAME)
    old = os.path.join(home, LEGACY_CONFIG_DIRNAME)
    if os.path.isdir(new):
        return new
    if os.path.isdir(old):
        return old
    return new


def init_marker_names():
    """(当前写出的标记名, 迁移期仍要认的旧标记名)。"""
    return INIT_MARKER, LEGACY_INIT_MARKER


# 随 wheel 出厂的引擎内容（相对工程根）。**单一来源**：`setup.py` 里有逐字相同的一份
# （构建脚本不能 import 包，构建阶段包还没进 sys.path），由
# tests/packaging/test_init_bundle.py 用 AST 对拍 —— 两处漂移会当场红。
#
# 为什么不把 `config/harness.local.toml`（及旧版 `.yaml`）打进去：那是**本机事实**（各仓磁盘路径），
# 打进去等于把打包机的目录结构发给所有人；`init` 复制时也会跳过它。
ENGINE_DIRS = ("checks", "harness", "config", "sandbox", "golden-patterns",
               "integrations", "docs")
ENGINE_FILES = ("VERSION", "LICENSE", "README.md", "AGENTS.md")
ENGINE_EXCLUDE = ("config/harness.local.toml", "config/harness.local.yaml")


def package_dir():
    """壳模块所在目录（开发树 = `<repo>/guardrail_engine/`，wheel = `site-packages/guardrail_engine/`）。

    `init` 用它把 `mcp.py` / `cli.py` / `_shell.py` 一并复制进引擎根：插件与
    `fix_paths.py` 认的布局是 `<ENGINE>/guardrail_engine/mcp.py`（旧路径
    `<ENGINE>/agentguard/mcp.py` 在迁移期同时生成兼容副本）。
    """
    return os.path.dirname(os.path.abspath(__file__))


def packaged_engine():
    """轮子自带的引擎目录（`guardrail_engine/_engine`）；开发树里返回 None。

    刻意**不**并入 `find_root()`：引擎根也是台账（`reports/gate_log-*.jsonl`）的默认落点，
    让 `find_root()` 直接返回 site-packages 会把台账写进包的安装目录 —— 升级即丢，
    系统级安装还可能只读。所以随包引擎只是 `guardrail-engine init` 的**源**；
    真正生效要等 init 把它复制到可写目录并写指针。
    """
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_engine")
    if os.path.isfile(os.path.join(d, "harness", "run_gate.py")):
        return d
    return None


# ---------- 工程根定位（统一解析链） ----------

def _read_config_harness(path):
    """从配置文件**顶层**取 `harness` 键：`.toml` 用标准库 tomllib，旧版 `.yaml` 走极简行读。

    只读这一个键，不解析整份配置：这里是壳层的"工程根定位"，不该因为配置里别处写坏
    而拿不到根（真正的配置校验在 `harness/_config.py`，那边会把错误报成 rc=2）。
    """
    if path.lower().endswith(".toml"):
        try:
            with open(path, "rb") as f:
                val = tomllib.load(f).get("harness")
            return val if isinstance(val, str) else None
        except Exception:
            return None
    return _read_yaml_simple(path)


def _read_yaml_simple(path):
    """旧版 YAML 兼容：只取顶层 `harness:` 键的值。

    为什么不用 PyYAML：本仓零第三方依赖（pyproject.toml 无 dependencies 键，
    tests/engine/test_stdlib_only.py 用 AST 扫描锁定）。`harness.local.yaml` 的 `harness:`
    值是简单字符串，不需要完整 YAML 解析。新配置请用 TOML（见 `_read_config_harness`）。
    """
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("harness:") and not line.startswith("#"):
                    val = line[len("harness:"):].strip()
                    # 去掉引号（单/双）
                    if val and val[0] in ('"', "'") and val[-1] == val[0]:
                        val = val[1:-1]
                    return val
    except OSError:
        pass
    return None


def find_root():
    """定位 guardrail-engine 工程根（统一解析链，优先级从高到低）。

    解析链：
      ① 环境变量 `GUARDRAIL_ENGINE_DIR` / `AGENTGUARD_DIR`（旧名）/ `HARNESS_DIR`（更旧）
      ② `config/harness.local.toml` → `harness` 键（本机配置，gitignored；
         旧版 `harness.local.yaml` 的 `harness:` 键仍可读）
      ③ 从**本模块所在目录**（`__file__`）向上找（源码树 / init 安装副本据此自定位；
         排在指针之前，免得陈旧指针把本地检出劫持到别的引擎 —— 评审 P1-4）
      ④ 指针文件 `~/.guardrail-engine/harness.path`，其次旧目录
         `~/.agentguard/harness.path`（迁移期双读）—— 只服务「包不在任何引擎树内」的
         pip/wheel 形态：随包没有引擎，靠 init 写下的指针定位

    判据是「该目录下有 harness/run_gate.py」——认文件、不认目录名，
    这样 clone 到任何路径、改成任何目录名都能跑（分享给同事的前提）。
    找不到返回 None（安装形态下确实没有引擎，由调用方决定怎么退化）。
    """
    cands = []

    # ① 环境变量（新名优先，旧名迁移期继续可用）
    env = (os.environ.get("GUARDRAIL_ENGINE_DIR")
           or os.environ.get("AGENTGUARD_DIR")
           or os.environ.get("HARNESS_DIR") or "").strip()
    if env:
        cands.append(env)

    # ② 本机配置文件（config/harness.local.toml → harness 键；旧版 .yaml 兼容）
    # 从本文件位置向上找引擎根，再拼 config 路径
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):
        found = False
        for name in ("harness.local.toml", "harness.local.yaml"):
            cfg = os.path.join(d, "config", name)
            if os.path.isfile(cfg):
                val = _read_config_harness(cfg)
                if val:
                    cands.append(val)
                found = True
                break
        if found:
            break
        d = os.path.dirname(d)

    # ③ 从**本模块所在目录**（__file__）向上找：源码树 / init 安装副本据此自定位。
    #    为什么排在指针之前 —— 包就躺在某棵引擎树里时，那棵树才是本次真正要用的引擎；
    #    若让用户级指针抢先，一个陈旧/外来指针会把本地检出「劫持」到别的目录，跑的是
    #    别的仓的门禁（评审 P1-4）。pip/wheel 形态下这里找不到 harness/run_gate.py，
    #    自然落到 ④ 指针。注意是从 __file__ 而非 cwd 向上：源码树里的 CLI 无论从哪个
    #    工作目录被调用都能自定位（cwd 兜不住 `python <绝对路径>/cli.py` 这种用法）。
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):
        cands.append(d)
        d = os.path.dirname(d)

    # ④ 指针文件（新目录优先，旧目录迁移期继续认）—— 只服务「包不在任何引擎树内」的
    #    pip/wheel 形态：随包没有引擎，靠 init 写下的指针定位。
    home = os.path.expanduser("~")
    for ptr in (os.path.join(home, CONFIG_DIRNAME, "harness.path"),
                os.path.join(home, LEGACY_CONFIG_DIRNAME, "harness.path"),
                os.path.join(home, ".agentguard.path")):
        try:
            with open(ptr, encoding="utf-8") as f:
                val = f.read().strip()
                if val:
                    cands.append(val)
        except OSError:
            pass

    for c in cands:
        try:
            if c and os.path.isfile(os.path.join(c, "harness", "run_gate.py")):
                return os.path.abspath(c)
        except OSError:
            continue
    return None


def read_version(root=None, fallback="0.0.0-dev"):
    """版本号的**单一来源**：VERSION 文件 → 包元数据 → `0.0.0-dev`。

    N1 的修法所在：两条回退路径必须**每个入口都走同一遍**。
    只读 VERSION 的实现（原 mcp）在轮子形态下会退化成写死的 `0.0.0-dev`，
    而只回退元数据的实现（原 cli / __init__）在开发树里读不到 VERSION 时也能自愈 ——
    两者在「VERSION 缺失」这一个场景下给出不同答案，这就是分叉。
    """
    r = root if root is not None else find_root()
    if r:
        try:
            with open(os.path.join(r, "VERSION"), encoding="utf-8") as f:
                v = f.read().strip()
                if v:
                    return v
        except OSError:
            pass
    try:  # 轮子安装形态（无 VERSION 文件）：回退到包元数据（新名 → 旧名）
        from importlib import metadata
        for dist in (BRAND, "agentguard"):
            try:
                return metadata.version(dist)
            except Exception:
                continue
        return fallback
    except Exception:
        return fallback


# ---------- 解释器 / 子进程环境 ----------

def py_exe():
    """跑子进程用的解释器：就用当前解释器（保证「同一份引擎、同一个 Python」）。"""
    return sys.executable or "python"


def require_python(vi=None):
    """前置检查：本工程需要 **Python 3.11+**（与《安装说明》和包元数据一致）。

    为什么：包元数据曾写 >=3.8，而引擎用到 3.9+ 的 API（`pathlib.is_relative_to`），
    版本不够时的真实报错是 `AttributeError`/语法错，用户只会当成「包坏了」。
    `vi` 仅供测试注入（默认取 `sys.version_info`）。
    """
    v = vi if vi is not None else sys.version_info
    if tuple(v[:2]) < (3, 11):
        sys.stderr.write(
            "✗ 需要 Python 3.11 或更高版本（当前 %d.%d）。\n"
            "  · 安装：https://www.python.org/downloads/（Windows 勾选 Add python.exe to PATH）\n"
            "  · 装好后重跑本命令；3.8/3.9/3.10 不受支持（引擎用到 3.9+ 的 API，且 3.11 以下从未测试）\n"
            % (v[0], v[1]))
        raise SystemExit(2)


def find_bash():
    """Git Bash 路径（Windows 上 doctor 等要跑 .sh）；找不到返回 None。

    Windows 上刻意排除 `system32` 下的 bash：那是 WSL 的转发壳，
    在 MSYS 语义的脚本里会出现路径/换行行为不一致（宁可不跑，也不跑错的）。
    """
    import shutil
    if os.name == "nt":
        for c in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
            if os.path.isfile(c):
                return c
        b = shutil.which("bash")
        if b and "system32" not in b.lower():
            return b
        return None
    return shutil.which("bash")


# ---------- `run_gate.py --list` 的输出解析与「文件归属哪个 gate」 ----------
# 这两件事原先在 cli.py 与 mcp.py 里各写了一遍（算法逐字相同），
# 归属匹配的算法一旦分叉，同一个文件在 CLI 与 MCP 下会判给不同的 gate —— 结果不一致就是 bug。

# 大小写不敏感是**必须的**，不是风格问题：`run_gate.py --list` 打的是 `[ON ]` / `[off]`，
# 而这里原先只认大写 `ON|OFF` —— 于是**所有 enabled: false 的 gate 都解析不出来**，
# 直接从 `harness_list_gates` 的结果里消失（不是标成 off，是整行不见），
# 「有哪些 gate」这个问题拿到的是残缺答案。（评审 v13 中 15 落地时实测。）
GATE_RE = re.compile(r"^\s*\[(ON|OFF)[^\]]*\]\s*(\S+):\s+\S+\s+@\s+(.+?)\s*$", re.I)


def parse_gates(text):
    """解析 `run_gate.py --list` 的表格行 → [{enabled, name, repo}]（解析不出就跳过该行）。"""
    gates = []
    for line in (text or "").splitlines():
        m = GATE_RE.match(line)
        if m:
            gates.append({"enabled": m.group(1).upper() == "ON", "name": m.group(2),
                          "repo": m.group(3).strip()})
    return gates


def gate_owning(path, gates):
    """返回管理 `path` 的 gate（多个命中时取**仓库路径最长**的那个）。

    取最长前缀是必须的：仓库路径可能互相嵌套（例：`.../agentguard_back_net-712` 与
    `.../agentguard_back_net-712_v3`），取最短会把 v3 的文件判给 v1。
    无归属返回 None —— 错误文案由各壳自己写（CLI 中文 / MCP 英文），本函数不掺和。
    """
    tgt = os.path.normcase(os.path.abspath(path))
    best = None
    for g in gates:
        if not g.get("enabled") or not g.get("repo"):
            continue
        r = os.path.normcase(os.path.abspath(g["repo"]))
        if tgt == r or tgt.startswith(r + os.sep):
            if best is None or len(r) > len(best[0]):
                best = (r, g)
    return best[1] if best else None


# ---------- 位置参数契约：`files` 列表里不许出现「看着像旗标」的项 ----------
# 2026-09-23 评审报告 H1（实测）：两个壳把调用方给的 `files` 原样拼在 gate 名之后
# （`[py, run_gate.py, gate] + files`），而 run_gate 把 `--mode <v>` 当**自己的**参数消费 ——
# 于是一个内容为 `--mode` 的"文件名"能把一次判定从拦截（rc=1）翻成放行（rc=0），
# 且台账里那一行与"真检查过且通过"逐字不可区分。
# 这与 2026-09-16 修掉的 `--gate` 冒充**同类**（`run_gate.py` 里那段 fail-closed 注释写着
# 「被检查方可以自己把门禁放宽，对门禁是硬伤」），当时只摘掉了 `--gate`。
#
# 判据只认前缀，不做 `os.path.isfile`：`files` 的契约本来就是「绝对路径或相对 gate 仓的路径」
# （MCP 工具描述如此），做存在性校验会**误拒**合法相对路径；而真实路径无论相对还是绝对都
# 不会以 `-` 开头。调用方一律 fail-closed 拒绝，不做"悄悄丢掉再照跑"——那会把一次调用
# 变成另一次语义不同的调用。

def flag_like(entries):
    """挑出 `entries` 里以 `-` 开头的项（= 不是文件路径、是命令行旗标）→ 列表。

    返回原始字符串，好让错误文案把它们逐字打出来（调用方要给的是"哪一项不合法"）。
    """
    out = []
    for e in (entries or []):
        s = str(e)
        if s.startswith("-"):
            out.append(s)
    return out

