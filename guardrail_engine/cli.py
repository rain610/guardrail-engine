#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""guardrail-engine CLI —— guardrail-engine 的统一命令行薄壳（ADR-0001：壳只转发）。

子命令
  quickstart / setup    一条命令接入（打印 MCP 配置 / 装钩子 / 装技能）
  installed             本机装出来的副本各自是哪一版（引擎 / pip / 插件 / 技能 / shim）
  list run check summary coverage doctor   —— 转发同一份引擎（与 git 钩子、MCP 共用）
  done                  一步收尾：跑门禁 + 打一行**可验证凭证**（h 可对台账复核，人/agent 皆可读）
  log ops               判定台账 / 运维台账 的查看与导出
  skill hooks           技能包安装与钩子安装/卸载/状态（防呆、幂等、可回滚）
  bypass                紧急旁路的"声明留痕"入口（记录理由，打印真实跳过命令）
  mcp                   转发到单文件 MCP 薄服务

原则（与引擎一致）：
- 本壳不做任何"删除"操作：卸载 = git config 撤回 / 目录改名归档（可逆），不销毁文件；
- 台账写入失败不阻断本命令（观察性输出绝不反向拦人），但会提示；
- 一切检查转发引擎；本文件不重定义引擎符号、不做行号运算。
"""
from __future__ import annotations

import argparse
import csv
import errno
import glob
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# 壳层公用件（同目录 _shell.py）：根定位 / 版本 / 解释器 / 前置检查 / gate 归属匹配。
# 双形态导入（三种运行形态都要成立，详见 _shell.py 文件头）：
#   ① 安装形态 `guardrail_engine.cli:main`（entry_points）→ 包上下文，必须走相对导入；
#   ② 脚本直跑 `python guardrail_engine/cli.py` → 脚本目录已在 sys.path[0]，裸导入即可；
#   ③ exec_module（tests/engine/test_preflight.py 按路径 exec 本文件）→ **没有**包上下文，
#      相对导入抛 ImportError → 补 sys.path 后裸导入（-P/PYTHONSAFEPATH 不自动加脚本目录）。
try:
    from . import _shell
except ImportError:      # 无包上下文；若是 _shell 自身的 ImportError，下面这句会原样再抛
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _shell

ROOT = _shell.find_root()
# MCP 服务跟着**包**走，不跟引擎根走：轮子形态下 mcp.py 在 site-packages/guardrail_engine/，
# 而引擎根是 `guardrail-engine init` 复制出来的 `~/.guardrail-engine/engine`
# （里面没有 guardrail_engine/ 子目录）。跟着 ROOT 拼会在 init 之后指向一个不存在的路径。
MCP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp.py")
SKILL_SRC = os.path.join(ROOT, "integrations", "guardrail-engine-skill") if ROOT else ""
SKILL_PRESETS = {
    "claude": os.path.join(os.path.expanduser("~"), ".claude", "skills"),
    "opencode": os.path.join(os.path.expanduser("~"), ".config", "opencode", "skills"),
    "codebuddy": os.path.join(os.path.expanduser("~"), ".codebuddy", "skills"),
}
SKILL_NAME = "guardrail-engine"
LEGACY_SKILL_NAME = "agentguard"
# MCP 服务键（宿主配置里的名字）。老配置里的 "agentguard" 键仍然有效（同一个脚本）。
SERVER_NAME = "guardrail-engine"
LEGACY_SERVER_NAME = "agentguard"
# 卸载归档的后缀。**跨文件契约**：sandbox/install_shim.py 与 sandbox/uninstall.py 各有一份
# 同口径的 archive_name()（那两个脚本随包分发，不能 import 本模块），改动任一侧都要同步，
# tests/packaging/test_uninstall.py::TestArchiveNameDoesNotDrift 拿同一个输入喂三份实现做对拍。
ARCHIVE_SUFFIX = ".disabled-"


def archive_name(target):
    """挑一个没被占用的归档名：`<target>.disabled-<戳>`，撞车就加序号 `-2`、`-3`…

    秒级时间戳不够用：脚本化的「卸载 → 重装 → 再卸载」两次会落在同一秒里，而 Windows 上
    `os.rename` 撞到已存在的目录会直接抛 OSError —— 报出来却是"可能被占用"，运维会去查
    一个根本不存在的文件锁（剖析报告 G8）。挑名字是**根因修**：撞车这件事从此不发生，
    下面 `_rename_hint` 就只需要如实分级，不必再猜。
    """
    base = "%s%s%s" % (target, ARCHIVE_SUFFIX, time.strftime("%Y%m%d-%H%M%S"))
    if not os.path.exists(base):
        return base
    n = 2
    while os.path.exists("%s-%d" % (base, n)):
        n += 1
    return "%s-%d" % (base, n)


def _rename_hint(e):
    """把改名失败分成人话，**重名 / 占用 / 权限分开说**（剖析报告 G8）。

    原先一律打印"改名归档失败（可能被占用）"：当时最常见的成因其实是重名（秒级戳、
    同一秒两次卸载），却把运维指去查一个不存在的锁。这里按操作系统给的真实错误码分级，
    不再猜；三个分支都可单测（拿合成 OSError 直接喂本函数）。
    """
    we = getattr(e, "winerror", None)
    en = getattr(e, "errno", None)
    if we == 183 or en == errno.EEXIST:                     # ERROR_ALREADY_EXISTS
        return "归档名已被占用（同名目录已存在）"
    if we == 32 or en in (errno.EBUSY, errno.EACCES, errno.EPERM):
        return "目录被占用或没有权限——关掉正在使用该技能目录的编辑器/终端后重试"
    return "系统错误 %s（%s）" % (en if en is not None else we, e)


def _skill_archives(base):
    """某宿主 skills 目录下的卸载残骸（`<SKILL_NAME>.disabled-*`）→ 路径列表（排序）。

    归档"可逆"的代价：每卸载一次就永久留下一份**完整技能副本**。此前没有任何地方数它、
    列它（status 只 stat 了一个路径），文档也把归档说成纯收益
    —— 反复「卸载+重装」只涨不落，谁都不知道（剖析报告 G8）。

    改名迁移期同时数旧名 `agentguard.disabled-*`：那些残骸同样是本工具留下的。
    """
    out = []
    try:
        names = os.listdir(base)
    except OSError:
        return out
    prefixes = (SKILL_NAME + ARCHIVE_SUFFIX, LEGACY_SKILL_NAME + ARCHIVE_SUFFIX)
    for n in sorted(names):
        p = os.path.join(base, n)
        if n.startswith(prefixes) and os.path.isdir(p):
            out.append(p)
    return out


def _version():
    """本包版本 —— 转发公用件（ADR-0001 铁律 3：版本单一来源，三入口同一份实现）。

    N1 修复（剖析报告 A3）：此处原先是自备实现（VERSION → 包元数据），而 mcp.py 的
    `_read_version` 只读 VERSION、缺失即回退写死的 `0.0.0-dev` —— 轮子安装形态下
    （包内不带 VERSION 文件）同一个包会报出两个版本号。现三处都走 `_shell.read_version`。
    """
    return _shell.read_version(ROOT)


def _py():
    return _shell.py_exe()


def _env():
    e = dict(os.environ)
    e.setdefault("PYTHONUTF8", "1")
    e["HARNESS_ENTRY"] = "cli"
    return e


def _run(args, cwd=None, timeout=None):
    """跑子进程 → (rc, stdout, stderr)。

    2026-09-23 评审报告 H2/L6：原先既没捕获 `FileNotFoundError`/`NotADirectoryError`
    （cwd 不存在 → 裸回溯崩溃），也没捕获 `TimeoutExpired`（超 300s → 裸回溯，且回溯的
    退出码是 1 —— **与"门禁拦截"同码**，超时被读成业务不通过）。两者都归成 rc=2
    （配置/环境错误），并把原因打在 stderr 上：退出码契约 0/1/2/3 是跨三入口的，
    基础设施问题不许冒充 1。
    2026-10-15 批次 7：超时改为**杀掉整棵进程树**（见 _shell._kill_tree 的注释）；超时文案
    同步点名新出路（--timeout / 全量默认 3600）。
    """
    try:
        p = subprocess.Popen(args, cwd=cwd or ROOT or os.getcwd(), env=_env(),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=(os.name != "nt"))
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as e:
        return (2, "", "✗ 起不了子进程（rc=2，属环境/配置问题，**不是**代码不合规）：%r" % (e,))
    try:
        out_b, err_b = _shell._communicate_tree(p, timeout or 300)
    except subprocess.TimeoutExpired:
        return (2, "", "✗ 子进程超时（>%ss，已连同其子进程树一并终止；`cli.py run` 可用"
                       " --timeout 加大、全量扫描默认 3600s 或直调 harness/run_gate.py）：%s"
                % (timeout or 300, " ".join(str(x) for x in args[:3])))
    return (p.returncode,
            (out_b or b"").decode("utf-8", "replace"),
            (err_b or b"").decode("utf-8", "replace"))


def _print_out(rc, out, err):
    if out.strip():
        print(out.rstrip())
    if err.strip():
        print("[stderr]", err.rstrip())
    return rc


def _ledger_path():
    return os.environ.get("HARNESS_LOG_FILE") or os.path.join(ROOT, "reports", "gate_log.jsonl")


def _ledger_paths():
    """台账片全集（时间序）—— 月度分片读侧（2026-09-29）。

    判据与 harness/run_gate.py 的 _ledger_files() **逐字同源**（跨文件契约：壳不 import
    引擎，两份实现靠 tests/engine/test_gate_log_anchor.py 的对拍锁咬住，漂移即红）：
    env 密闭 → 只有 HARNESS_LOG_FILE 那本；否则旧 gate_log.jsonl（若有，排最前）
    + 月度片 gate_log-YYYY-MM.jsonl（字典序=月序）。HARNESS_LOG_DIR 与引擎侧同源。
    """
    env = os.environ.get("HARNESS_LOG_FILE")
    if env:
        return [env]
    base = os.environ.get("HARNESS_LOG_DIR") or os.path.join(ROOT, "reports")
    paths = []
    legacy = os.path.join(base, "gate_log.jsonl")
    if os.path.exists(legacy):
        paths.append(legacy)
    paths.extend(sorted(glob.glob(os.path.join(base, "gate_log-*.jsonl"))))
    return paths


def _anchor_path():
    """锚点文件（唯一**入库**的台账衍生件）：台账本体 git 忽略，锚点钉进 git 历史。"""
    return (os.environ.get("HARNESS_ANCHOR_FILE")
            or os.path.join(ROOT, "reports", "gate_log_anchor.jsonl"))


def _ops_path():
    return (os.environ.get("GUARDRAIL_OPS_LOG")
            or os.environ.get("AGENTGUARD_OPS_LOG")   # 旧名，v0.3 移除
            or os.path.join(ROOT, "reports", "ops_log.jsonl"))


def _record_op(rec):
    """运维台账（安装/卸载/旁路声明）：追加写；失败提示但不阻断。"""
    try:
        os.makedirs(os.path.dirname(_ops_path()), exist_ok=True)
        rec = dict(rec)
        rec.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
        rec.setdefault("entry", "cli")
        with open(_ops_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        print("[ops] 注意：运维台账写入失败（不阻断）：%r" % e)
        return False


def _bash():
    return _shell.find_bash()


def _parse_gates(text):
    return _shell.parse_gates(text)


def _gates():
    rc, out, err = _run([_py(), os.path.join(ROOT, "harness", "run_gate.py"), "--list"])
    return rc, out, err, _parse_gates(out)


def _gate_for_file(path):
    rc, out, err, gates = _gates()
    if rc != 0:
        return None, "list-gates failed: %s" % (err or out)[:200]
    g = _shell.gate_owning(path, gates)      # 归属匹配算法收敛到公用件（原与 mcp.py 各一份）
    if not g:
        return None, "no enabled gate manages this file's repository (see: guardrail-engine list)"
    return g, None


def _copy_tree_if_changed(src, dst):
    """无删除、幂等的目录复制：只写新增/变化文件；返回 (created, updated, same)。"""
    created = updated = same = 0
    for dp, dn, fn in os.walk(src):
        rel = os.path.relpath(dp, src)
        tdir = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(tdir, exist_ok=True)
        for name in fn:
            s = os.path.join(dp, name)
            t = os.path.join(tdir, name)
            with open(s, "rb") as f:
                data = f.read()
            if os.path.exists(t):
                try:
                    with open(t, "rb") as f:
                        old = f.read()
                    if old == data:
                        same += 1
                        continue
                except OSError:
                    pass
                with open(t, "wb") as f:
                    f.write(data)
                updated += 1
            else:
                with open(t, "wb") as f:
                    f.write(data)
                created += 1
    return created, updated, same


# ---------------- 随包引擎：`guardrail-engine init` ----------------
# 轮子自带 `guardrail_engine/_engine/`（见 setup.py）。它只是**源**：引擎根同时是台账
# （reports/gate_log-*.jsonl）的默认落点，不能直接指向 site-packages —— 升级即丢，
# 系统级安装还可能只读。init 把它复制到可写目录并写 `~/.guardrail-engine/harness.path`。

def _cmd_prefix():
    """打印给用户复制的命令前缀：装出来的控制台脚本 vs 仓库里的脚本直跑。"""
    exe = os.path.basename(sys.argv[0] or "").lower()
    if exe in ("guardrail-engine", "guardrail-engine.exe", "guardrail-engine.cmd"):
        return "guardrail-engine"
    if exe in ("agentguard", "agentguard.exe", "agentguard.cmd"):
        return "agentguard"          # 迁移期别名（v0.3 移除）
    return "python guardrail_engine/cli.py"


def _engine_source():
    """init 的源目录 → (路径, 说明)；都没有时 (None, "")。"""
    p = _shell.packaged_engine()
    if p:
        return p, "随包引擎（wheel）"
    # 源码形态：本文件在 `<源>/guardrail_engine/` 下，上一级就是引擎根。
    # 不能只看 ROOT —— init 写完指针后 find_root() 会解析到刚装出来的副本，
    # 第二次 init 就变成「源=目标」并按重叠拒绝（干净环境必现；本机有
    # config/harness.local.toml 时会先命中配置，所以本地看不出来）。
    cand = os.path.dirname(_shell.package_dir())
    if os.path.isfile(os.path.join(cand, "harness", "run_gate.py")):
        return cand, "当前工程根"
    if ROOT:
        return ROOT, "当前工程根"
    return None, ""


def _within(child, parent):
    """child 是否等于 parent 或在其子树内（用于拒绝「复制进自己」）。"""
    c = os.path.normcase(os.path.abspath(child)).rstrip(os.sep)
    p = os.path.normcase(os.path.abspath(parent)).rstrip(os.sep)
    return c == p or c.startswith(p + os.sep)


def _engine_entries(src):
    """引擎源的 (相对路径, 绝对路径) 清单（与 setup.py 的打包清单同源）。"""
    out = []
    for name in _shell.ENGINE_DIRS:
        root = os.path.join(src, name)
        if not os.path.isdir(root):
            continue
        for dp, dns, fns in os.walk(root):
            dns[:] = sorted(d for d in dns if d not in ("__pycache__", ".pytest_cache", ".git"))
            for fn in sorted(fns):
                if fn.endswith((".pyc", ".pyo")):
                    continue
                p = os.path.join(dp, fn)
                rel = os.path.relpath(p, src).replace("\\", "/")
                if rel in _shell.ENGINE_EXCLUDE:
                    continue
                out.append((rel, p))
    for name in _shell.ENGINE_FILES:
        p = os.path.join(src, name)
        if os.path.isfile(p):
            out.append((name, p))
    # 壳文件也要进引擎根：MCP 插件与 fix_paths.py 认新布局 `<ENGINE>/guardrail_engine/mcp.py`；
    # 迁移期同时生成 `<ENGINE>/agentguard/*.py` 兼容副本，老注册（含旧版生成的配置）不必改。
    pkg = _shell.package_dir()
    legacy = os.path.join(os.path.dirname(pkg), "agentguard")
    for fn in sorted(os.listdir(pkg)):
        if fn.endswith(".py"):
            out.append(("guardrail_engine/" + fn, os.path.join(pkg, fn)))
    if os.path.isdir(legacy):
        for fn in sorted(os.listdir(legacy)):
            if fn.endswith(".py"):
                out.append(("agentguard/" + fn, os.path.join(legacy, fn)))
    return out


def _files_equal(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                x, y = fa.read(65536), fb.read(65536)
                if x != y:
                    return False
                if not x:
                    return True
    except OSError:
        return False


def _same_engine(src, dst):
    """目标目录是不是**已经就是**这份引擎（幂等重跑用）。

    逐文件比内容，不只看 VERSION：同版本号下也可以有本地改动（改了规则再 init），
    那种情况不该被当成"已是最新"，而应要求 --force（旧目录归档，可逆）。
    """
    if _shell.read_version(src) != _shell.read_version(dst):
        return False
    entries = _engine_entries(src)
    if not entries:
        return False
    for rel, p in entries:
        if not _files_equal(p, os.path.join(dst, rel)):
            return False
    return True


def _copy_engine(src, dst):
    entries = _engine_entries(src)
    if not entries:
        raise OSError("引擎源里没有任何文件：%s" % src)
    for rel, p in entries:
        target = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(p, target)
    return len(entries)


def _write_engine_pointer(dest):
    """写 `harness.path`（`_shell.find_root` 的解析链之一）。

    目录走 `_shell.config_home()`：老机器已有 `~/.agentguard` 时继续写在那里，
    新机器落 `~/.guardrail-engine` —— 不迁移、不复制，避免两套指针互相盖。
    """
    base = _shell.config_home()
    os.makedirs(base, exist_ok=True)
    p = os.path.join(base, "harness.path")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(dest + "\n")
    return p


# 引擎副本的**所有权标记**。为什么需要：`~/.agentguard/harness.path` 也会被
# install_shim / setup_machine 写成**仓库**路径，光看指针分不清「init 复制出来的副本」
# 与「用户自己的仓库」。upgrade.py 只刷新带这个标记的目录 —— 对仓库跑 `init --force`
# 会把源改名归档，那不是升级。常量在 sandbox/upgrade.py 里也有一份（那个脚本随包分发，
# 不能 import 本模块），两处漂移由 tests/packaging/test_upgrade.py 用 AST 对拍。
INIT_MARKER = _shell.INIT_MARKER
INIT_MARKER_KIND = "guardrail-engine-init"


def _engine_sha_of(dest):
    """算**副本 dest** 的 engine_sha（跑它自己的 run_gate --engine-sha，cwd=dest）→ 16-hex 或 ""。

    与 `_engine_sha_now()`（算 ROOT 的）同口径，只是目标换成刚复制出来的副本：信任锚必须描述
    "这份副本装好时的判定代码"，而不是源工程的当前状态。算不出返回 ""（调用方据此出声告警）。
    """
    rc, out, _err = _run([_py(), os.path.join(dest, "harness", "run_gate.py"), "--engine-sha"],
                         cwd=dest, timeout=120)
    lines = (out or "").strip().splitlines()
    cand = lines[-1].strip() if (rc == 0 and lines) else ""
    return cand if re.fullmatch(r"[0-9a-f]{16}", cand) else ""


def _write_init_marker(dest):
    """标记「这个目录归 agentguard init 管」；只写所有权，不写源路径（副本可能被打包带走）。

    2026-11-04（评审 P0）：补 `engine_sha` 信任锚。安装副本**不是 git 工作树**，harness_guard 无法
    用"未提交改动"判它有没有被改弱（changed_paths_ex 对无 .git 的副本返回 err）——旧版一律
    fail-closed rc=2，于是 README 主推的 pip install→init→hooks install 路径第一次 commit 就被拦，
    用户退到 --no-verify，门禁整体关闭（复现 A）。guard 在副本形态下重算 engine_sha 与本锚比对
    （见 checks/harness_guard.py::_install_copy_verdict）；`gate log --verify --engine` 在无 git 历史时
    也拿它当已知基线（见 _engine_known_shas）。
    """
    data = {"kind": INIT_MARKER_KIND, "version": _shell.read_version(dest),
            "source": "wheel" if _shell.packaged_engine() else "repo"}
    sha = _engine_sha_of(dest)
    # 算不出也写**空串**而非省略键：让 guard 明确区分"有标记但无可用锚"→ fail-closed，而不是误读成
    # "旧版标记"（旧标记没有这个键）。空锚是异常状态，出声提示便于排查（_mark_init_copy 会接着打路径）。
    data["engine_sha"] = sha
    if not sha:
        print("⚠ 未能算出副本 engine_sha（信任锚缺失）：guard 在该副本上将 fail-closed 阻断提交，"
              "请检查这份副本能否跑 `harness/run_gate.py --engine-sha`。")
    p = os.path.join(dest, INIT_MARKER)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return p


def _mark_init_copy(dest):
    """写标记；失败只提示不阻断 —— 引擎已经装好，丢的是「下次升级能自动认出它」。"""
    try:
        p = _write_init_marker(dest)
    except OSError as e:
        print("⚠ 所有权标记写不进去（%s）：upgrade.py 将无法自动刷新这份引擎副本" % e)
        return
    print("init marker  : %s" % p)


def _init_selfcheck(dest):
    """装完立刻验一次：引擎能起、gate 表读得出来。"""
    rc, out, err = _run([_py(), os.path.join(dest, "harness", "run_gate.py"), "--list"],
                        cwd=dest, timeout=120)
    return rc, len(_parse_gates(out)), (err or out)


def _has_init_marker(d):
    """目录是不是 init 装出来的副本（新标记优先，旧标记迁移期继续认）。"""
    for name in (INIT_MARKER, _shell.LEGACY_INIT_MARKER):
        if os.path.isfile(os.path.join(d, name)):
            return True
    return False


def cmd_init(a):
    """把随包引擎落到可写目录，并写指针 —— 「装完就能跑」的那一步。"""
    print("== guardrail-engine init ==")
    src, source_desc = _engine_source()
    if not src:
        print("✗ 找不到可安装的引擎：本包没有 guardrail_engine/_engine/，当前也不在引擎工程根。",
              file=sys.stderr)
        print("  · wheel 安装：包不完整，请重装（正常 wheel 自带引擎）；", file=sys.stderr)
        print("  · 源码使用：在有 harness/run_gate.py 的工程根下运行本命令。", file=sys.stderr)
        return 2

    default_dest = os.path.join(_shell.config_home(), "engine")
    dest = os.path.abspath(os.path.expanduser(a.dir or default_dest))

    # 拒绝「复制进自己」：--dir 指到工程根 / 工程根的子目录时，归档会把源本身搬走。
    if (os.path.normcase(os.path.abspath(dest)) == os.path.normcase(os.path.abspath(src))
            and _has_init_marker(src)):
        # 从**副本内部**跑 init（`<dest>/guardrail_engine/cli.py init`）：没有独立源可复制，
        # 但这个目录本身就是 init 装出来的引擎 —— 认它、写指针，别报「源=目标」。
        # 只认带所有权标记的副本：把工程根当目标仍是错误用法（归档会搬走源本身）。
        ptr = _write_engine_pointer(dest)
        print("引擎已是最新：%s（version %s）" % (dest, _shell.read_version(dest)))
        print("pointer      : %s" % ptr)
        _mark_init_copy(dest)
        print("下一步: %s quickstart" % _cmd_prefix())
        return 0
    if _within(dest, src) or _within(src, dest):
        print("✗ 目标目录与引擎源重叠：源=%s 目标=%s（会把源复制进自己或归档掉源）"
              % (src, dest), file=sys.stderr)
        return 2

    archived = None
    if os.path.exists(dest):
        if _same_engine(src, dest):
            ptr = _write_engine_pointer(dest)
            print("引擎已是最新：%s（version %s）" % (dest, _shell.read_version(dest)))
            print("pointer      : %s" % ptr)
            # 旧版 init 装出来的副本没有标记；幂等重跑顺手补上（upgrade.py 靠它识别）。
            _mark_init_copy(dest)
            print("下一步: %s quickstart" % _cmd_prefix())
            return 0
        if not a.force:
            print("✗ 目标目录已存在且内容不同：%s" % dest, file=sys.stderr)
            print("  重跑并加 --force：旧目录会**改名归档**（%s.disabled-<戳>），不删除任何文件。"
                  % dest, file=sys.stderr)
            return 2
        archived = archive_name(dest)
        try:
            os.rename(dest, archived)
        except OSError as e:
            print("✗ 旧引擎归档失败：%s（%s）" % (archived, _rename_hint(e)), file=sys.stderr)
            return 2
        print("旧引擎已归档：%s" % archived)

    os.makedirs(dest, exist_ok=True)
    try:
        n = _copy_engine(src, dest)
    except OSError as e:
        # 可逆：本次新建的半份副本删掉，归档的旧引擎放回原位（指针因此仍然有效）。
        shutil.rmtree(dest, ignore_errors=True)
        if archived:
            try:
                os.replace(archived, dest)
            except OSError:
                pass
        print("✗ 复制引擎失败：%s（已回滚到复制前状态）" % e, file=sys.stderr)
        return 2

    ptr = _write_engine_pointer(dest)
    print("engine source: %s（%s）" % (src, source_desc))
    print("engine root  : %s（%d 个文件）" % (dest, n))
    print("version      : %s" % _shell.read_version(dest))
    print("pointer      : %s" % ptr)

    rc, ngates, detail = _init_selfcheck(dest)
    print("self-check   : list-gates rc=%s gates=%d" % (rc, ngates))
    if rc != 0:
        print("✗ 引擎自检失败 —— 文件已复制，但这份引擎跑不起来：%s"
              % (detail or "").strip()[:400], file=sys.stderr)
        return 2
    # 自检通过才打所有权标记：upgrade.py 会把这个目录整目录归档重放，标记不能贴在坏副本上。
    _mark_init_copy(dest)
    print("")
    print("下一步:")
    print("  %s quickstart" % _cmd_prefix())
    print("  %s setup --repo <你的仓库> [--target claude]" % _cmd_prefix())
    return 0


# ---------------- 本机安装矩阵（G4：装出来的副本各自读各自的地方） ----------------
# 为什么需要（升级链路剖析报告 G4）：`version` 读的是**引擎仓**的 VERSION，而升级要升的
# 是「装出来的副本」（pip 包 / 插件缓存 / 技能副本）。两台机器装了两版插件、引擎仓却是同一份时，
# `version` 打出**同一个数字** —— 「未升级的机器一眼可辨」对副本不成立。这里把四路副本各自
# 的版本与位置读回来，版本号不一致就当场出声。

def _claude_home():
    """宿主配置根：`CLAUDE_CONFIG_DIR` 优先（换配置目录/隔离测试时都靠它）。"""
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def _installed_plugins():
    """读宿主的插件登记 → [(插件全名, 版本, 安装路径, 路径是否存在)]；读不到返回 None。

    读的是 `installed_plugins.json`（宿主自己写的账），不猜缓存目录布局：
    缓存路径由宿主按「市场/插件/版本」三段拼，猜出来的路径迟早会漂。
    """
    p = os.path.join(_claude_home(), "plugins", "installed_plugins.json")
    try:
        with io.open(p, encoding="utf-8", errors="replace") as f:
            data = json.loads(f.read())
        plugs = data.get("plugins") or {}
    except (OSError, ValueError):
        return None
    out = []
    for name in sorted(plugs):
        for rec in (plugs.get(name) or []):
            if not isinstance(rec, dict):
                continue
            ip = rec.get("installPath") or ""
            out.append((name, rec.get("version") or "?", ip, bool(ip) and os.path.isdir(ip)))
    return out


def _pip_copy():
    """pip 装出来的那份副本 → (版本, 位置)；没装或读不到返回 (None, 原因)。"""
    from importlib import metadata
    last = "NotFound"
    for dist in (_shell.BRAND, "agentguard"):
        try:
            d = metadata.distribution(dist)
            loc = ""
            try:
                loc = str(d.locate_file(""))
            except Exception:
                loc = ""
            return (d.version, loc)
        except Exception as ex:
            last = type(ex).__name__
    return (None, last)


def _tree_diff(src, dst):
    """源→副本的差异 (缺, 旧, 同) —— 判据与 `_copy_tree_if_changed` **同一套**（逐字节比）。

    刻意不另立"版本号"概念：技能副本里没有版本文件，硬造一个只会多一处要同步的东西。
    「副本和源差了几个文件」是读完就能核的事实，比一个自报的数字可靠。
    """
    missing = stale = same = 0
    for dp, _dn, fn in os.walk(src):
        rel = os.path.relpath(dp, src)
        tdir = os.path.join(dst, rel) if rel != "." else dst
        for name in fn:
            s = os.path.join(dp, name)
            t = os.path.join(tdir, name)
            if not os.path.isfile(t):
                missing += 1
                continue
            try:
                with open(s, "rb") as f:
                    a = f.read()
                with open(t, "rb") as f:
                    b = f.read()
            except OSError:
                stale += 1
                continue
            if a == b:
                same += 1
            else:
                stale += 1
    return missing, stale, same


def _skill_copies():
    """宿主里的技能副本 → [(宿主名, 路径, 差异描述, 是否偏差)]。

    第四个返回值是显式布尔，理由同 `_shim_state`：判据不许依附于文案。
    「这个宿主没装技能」不在这张表里（下面按"目录在不在"过滤掉了）—— 未装不是偏差。
    """
    out = []
    for name, base in sorted(SKILL_PRESETS.items()):
        # 新名优先；迁移期也检查旧名，让老安装仍出现在矩阵里（不迁移、只报告）。
        t = os.path.join(base, SKILL_NAME)
        if not os.path.isdir(t):
            t = os.path.join(base, LEGACY_SKILL_NAME)
        if not os.path.isdir(t):
            continue
        if not os.path.isdir(SKILL_SRC):
            out.append((name, t, "源不可读（引擎仓缺 integrations/guardrail-engine-skill）", True))
            continue
        missing, stale, same = _tree_diff(SKILL_SRC, t)
        if missing or stale:
            out.append((name, t, "落后：缺 %d / 旧 %d（同 %d）" % (missing, stale, same), True))
        else:
            out.append((name, t, "与引擎仓一致（%d 个文件）" % same, False))
    return out


def _shim_state():
    """编辑期 shim 的状态 → (描述, shim 路径, 指针值, 是否偏差)。

    第四个返回值是**显式布尔**，不是"拿描述文案去比"：文案会改，改一次判据就静默失效
    （本仓的老毛病）。语义在这里定死：**「没装」不是偏差** —— 偏差指"装出来的副本与引擎仓
    对不上"或"装了但实际不起作用"，不包括"这台机器还没装它"。
    """
    agents = _shell.config_home()
    shim = os.path.join(agents, "hooks", "agentguard-gate.py")   # 文件名迁移期保持稳定（已装 shim 引用它）
    ptr = os.path.join(agents, "harness.path")
    val = ""
    if os.path.isfile(ptr):
        try:
            with io.open(ptr, encoding="utf-8", errors="replace") as f:
                val = f.read().strip()
        except OSError:
            val = ""
    if not os.path.isfile(shim):
        return ("未装（7 家客户端的编辑期门禁要靠它；装法见《安装说明》二）", shim, val, False)
    if val and not os.path.isfile(os.path.join(val, "integrations", "hooks",
                                              "agentguard-gate.py")):
        return ("已装，但指针指向的仓库不存在（编辑期门禁静默失效）", shim, val, True)
    return ("已装", shim, val, False)


def install_matrix():
    """本机安装矩阵（读只读，不写任何东西）→ 打印。返回 True=有需要人看的偏差。"""
    print("== 本机安装矩阵（各自读各自的地方）=================================")
    drift = False
    engine = _version() if ROOT else "?"
    print("  引擎仓      : %-12s %s" % (engine, ROOT or "(未定位到——设 GUARDRAIL_ENGINE_DIR 或在本仓内跑)"))
    print("                · 指向仓库的三路（MCP 注册 / 提交钩子 / 编辑期 shim）**仓库更新即生效**，不用动")

    ver, loc = _pip_copy()
    if ver:
        print("  pip 包      : %-12s %s" % (ver, loc or "(位置读不到)"))
        if ROOT and ver != engine:
            drift = True
    else:
        print("  pip 包      : %-12s %s" % ("未安装", "（%s；只用插件/CLI 脚本时不装也行）" % loc))

    plugs = _installed_plugins()
    if plugs is None:
        print("  Claude 插件 : 读不到登记（未装 / 换过 CLAUDE_CONFIG_DIR）：%s"
              % os.path.join(_claude_home(), "plugins", "installed_plugins.json"))
    elif not plugs:
        print("  Claude 插件 : 未安装")
    else:
        for name, pv, ip, ok in plugs:
            print("  Claude 插件 : %-12s %s%s" % (pv, ip or "(无 installPath)",
                                                 "" if ok else "   ⚠ 目录不存在（登记与实物不符）"))
            if not ok:
                drift = True
            if ROOT and pv != engine:
                drift = True

    copies = _skill_copies()
    if not copies:
        print("  技能副本    : 未安装（可选通道；装法：cli.py skill install --target claude）")
    for name, t, desc, bad in copies:
        print("  技能副本[%s]: %-12s %s" % (name, desc, t))
        if bad:
            drift = True

    desc, shim, val, bad = _shim_state()
    print("  编辑期 shim : %-12s %s" % (desc, shim))
    if val:
        print("                指针 harness.path → %s" % val)
    # 「没装」不是偏差（2026-09-17 实测空 HOME：上一行刚说"技能副本：未安装"，
    # 这一行却报了 ⚠"有副本与引擎仓不同版/不同内容" —— 一台什么都没装的机器上，
    # 那句警告纯属吓人，而它的文本讲的又全是"重装不换内容"，跟现状毫无关系）。
    if bad:
        drift = True

    if drift:
        print("")
        print("  ⚠ 有副本与引擎仓不同版/不同内容。副本按**版本号**判等：同版本号时重装不会换内容")
        print("    （`claude plugin update` 会打印绿色「已是最新」然后什么都不做）——")
        print("    升级请走包内 upgrade.py（逐通道幂等刷新 + 前后对照），别只改引擎仓。")
    return drift


def cmd_installed(_a):
    install_matrix()
    return 0


def cmd_version(_a):
    print("guardrail-engine %s (engine root: %s)" % (_version(), ROOT or "(not found)"))
    return 0 if ROOT else 1


def cmd_where(_a):
    """打印 harness 工程根的解析结果与解析路径（P2#5 用户级指针脚本化）。

    用途：
      - 用户验证安装是否正确
      - AI 客户端获取 harness 路径
      - 移动仓库后确认解析链是否还通
    """
    if not ROOT:
        print("✗ 找不到 harness 工程根", file=sys.stderr)
        home = _shell.config_home()
        print("  解析链：$GUARDRAIL_ENGINE_DIR / $AGENTGUARD_DIR(旧) / $HARNESS_DIR(更旧)"
              " → config/harness.local.toml → 模块所在目录向上查找"
              " → %s/harness.path（旧 %s/harness.path 也读，仅 pip/wheel 形态需要）"
              % (home, _shell.LEGACY_CONFIG_DIRNAME))
        print("  修复：设环境变量或写 %s/harness.path（内容 = 引擎根绝对路径）" % home)
        if _shell.packaged_engine():
            print("  本包自带引擎：%s init 会自动落到 %s/engine 并写指针。"
                  % (_cmd_prefix(), home), file=sys.stderr)
        return 1

    # 反向推导解析来源（便于调试）—— 顺序必须与 find_root 的优先级一致，否则标签会误导：
    # 源码树里跑却被标成"指针"（本机 local.toml / 自定位 / 指针同指一个目录时尤其如此）。
    source = "unknown"
    if (os.environ.get("GUARDRAIL_ENGINE_DIR") or os.environ.get("AGENTGUARD_DIR")
            or os.environ.get("HARNESS_DIR")):
        source = "env ($GUARDRAIL_ENGINE_DIR / $AGENTGUARD_DIR / $HARNESS_DIR)"
    else:
        mod_dir = os.path.dirname(os.path.abspath(_shell.__file__))
        # ② config/harness.local.toml 的 harness 键（与 find_root 用同一个读法）
        d = mod_dir
        for _ in range(5):
            cfg = os.path.join(d, "config", "harness.local.toml")
            if os.path.isfile(cfg):
                val = _shell._read_config_harness(cfg)
                if val and os.path.realpath(val) == os.path.realpath(ROOT):
                    source = cfg
                break
            d = os.path.dirname(d)
        # ③ 模块所在目录向上自定位（ROOT 是模块目录的祖先或自身，且带 harness/run_gate.py）
        if source == "unknown":
            d = mod_dir
            for _ in range(5):
                if (os.path.realpath(d) == os.path.realpath(ROOT)
                        and os.path.isfile(os.path.join(d, "harness", "run_gate.py"))):
                    source = "模块所在目录向上自定位（%s）" % d
                    break
                d = os.path.dirname(d)
        # ④ 指针文件 —— 只服务包不在引擎树内的 pip/wheel 形态
        if source == "unknown":
            home = _shell.config_home()
            for path_file in (os.path.join(home, "harness.path"),
                              os.path.join(os.path.expanduser("~"), ".agentguard", "harness.path")):
                if not os.path.isfile(path_file):
                    continue
                try:
                    with open(path_file, encoding="utf-8") as f:
                        stored = f.read().strip()
                    if os.path.realpath(stored) == os.path.realpath(ROOT):
                        source = path_file
                        break
                except Exception:
                    pass
        if source == "unknown":
            source = "（未能反推；见 find_root 解析链）"

    print(ROOT)
    if getattr(_a, "verbose", False):
        print(f"# 解析来源: {source}")
        print(f"# 版本: {_version()}")
    return 0


def cmd_list(_a):
    rc, out, err, gates = _gates()
    print("gates: %d (enabled=%d)" % (len(gates), sum(1 for g in gates if g["enabled"])))
    return _print_out(rc, out, err)


def cmd_run(a):
    files = list(a.files or [])
    # 2026-09-23 评审报告 H1（实测）：`files` 是位置参数通道，而引擎把 `--mode <v>` 当
    # 自己的旗标消费（pc_code_check.py:483-487 会据此覆盖 MODE）—— `run <gate> -- --mode loose`
    # 实测把一次 PC-COMP 拦截（rc=1）翻成放行（rc=0），台账那行与真通过逐字相同。
    # 与 2026-09-16 修掉的 `--gate` 冒充同类，故同样 fail-closed：位置参数只收文件。
    # 要指定口径就用真旗标 `--mode`（argparse 已单列，不会与本检查打架）。
    _bad = _shell.flag_like(files)
    if _bad:
        print("✗ 位置参数只收文件路径，不接受旗标：%s\n"
              "  （以 '-' 开头的项会被引擎当命令行参数消费，等于让被检查方自己改判定口径。\n"
              "   口径由 config 的 gate 策略决定；确要临时覆盖用真旗标 `--mode <strict|loose>`。）"
              % ", ".join(repr(b) for b in _bad))
        return 2
    cmd = [_py(), os.path.join(ROOT, "harness", "run_gate.py"), a.gate] + files
    # 2026-09-23（评审 L17 同批）：不传文件时**点名「这次是整仓全量」**。
    # 行为与以前逐字相同 —— 端脚本的 is_full 判据本就是「有 --all 或没有位置参数」
    # （pc_code_check.py:512 / h5_code_check.py:340 / backend 的 target_files 为空即全量，
    #  见 run_gate.py 那段同源注释），这里补的只是让台账照实记 scope=all：同一次全量扫描
    # 以前被记成 targeted/files=0。不点名就没法记账 —— run_gate 对"三样都没有"的裸调用
    # fail-closed（rc=2），所以 `run <gate>` 若在这里不补，用户会从"能跑"变成"rc=2"。
    if a.all or not files:
        cmd.append("--all")
    if a.mode:
        cmd += ["--mode", a.mode]
    # 批次 7（2026-10-15）：超时按口径分档 —— 全量扫描 300s 不够用（实测大仓全扫可达小时级
    # wall），默认放宽到 3600s；--timeout 显式覆盖；定向检查沿用 _run 的默认 300s。
    timeout = None
    if getattr(a, "timeout", None):
        timeout = int(a.timeout)
    elif a.all or not files:
        timeout = 3600
    return _print_out(*_run(cmd, timeout=timeout))


def cmd_check(a):
    if not os.path.isfile(a.file):
        print("file not found: %s" % a.file)
        return 2
    g, err = _gate_for_file(a.file)
    if not g:
        print(err)
        return 2
    print("[check] gate=%s file=%s" % (g["name"], a.file))
    return _print_out(*_run([_py(), os.path.join(ROOT, "harness", "run_gate.py"), g["name"], os.path.abspath(a.file)]))


def cmd_done(a):
    """一步收尾：跑门禁 → 打一行**可验证凭证**（批次 3，评审建议 #2「三步合成一步」）。

    原先「改码 → 跑 auto-gate → 查台账」三步必漏一步；本命令把前两步合成一条，
    并从**台账回读**本次记录，打出一行 `[harness] gate=… rc=… files=… ts=… h=…`——
    直接粘进回复/工单，人扫一眼就知道跑没跑；h 是该台账行的**链哈希前 8 位**
    （`gate log --gate <名> --last 1` 可复核），agent 编不出对得上的 h——
    「粘贴的凭证」从此可被抽查，不是自说自话（凭证可伪造正是这类机制最大风险）。

    退出码 = auto-gate 的退出码（0/1/2/3）；rc=3（非受管）不产凭证。
    凭证只含机器生成的字段（gate/repo/rc/files/ts/h），不含任何自由文本 —— 注入边界不变。
    不带 --repo 时用**调用方 cwd** 识别仓库（在端仓根目录直接跑即可）。
    ★ 2026-10-15 修（CodeBuddy 实测）：旧实现子进程默认 cwd=工程仓，在端仓里跑会被误判成
    harness_self 并打出 rc=0 **假凭证** —— 挑错仓比不产凭证更危险。
    """
    cmd = [_py(), os.path.join(ROOT, "harness", "agents", "auto-gate.py")]
    if getattr(a, "repo", None):
        cmd += ["--repo", a.repo]
    for f in (getattr(a, "only", None) or []):
        cmd += ["--only", f]
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    caller_cwd = os.getcwd()   # 不带 --repo 时 auto-gate 用子进程 cwd 识别仓库 —— 必须传调用方目录
    rc, out, err = _run(cmd, cwd=caller_cwd)
    if out.strip():
        print(out.rstrip())
    if err.strip():
        print("[stderr]", err.rstrip())
    m = (re.search(r"\[auto-gate\]\s+(\S+)\s+@", out)
         or re.search(r"\[auto-gate\]\s+gate\s+(\S+)\s+未启用", out)
         or re.search(r"\[auto-gate\]\s+(\S+)[:：]", out))
    if rc == 3:
        print("[harness] 非受管仓库（跳过门禁），无凭证。")
        return rc
    if not m:
        print("[harness] 未解析到 gate 名 —— 不产凭证（看上面的输出）。", file=sys.stderr)
        return rc
    gate = m.group(1)
    recs, _bad = _read_ledger(50)
    rec = None
    for r in reversed(recs):
        if r.get("gate") == gate and str(r.get("ts") or "") >= started:
            rec = r
            break
    if rec is None:
        # rc=0 且「无改动文件」是常见正路：auto-gate 直接早退，没有台账记录。
        print("[harness] gate=%s rc=%d（本次未产生台账记录：无改动文件？）—— 无凭证。" % (gate, rc))
        return rc
    h8 = str(rec.get("h") or "")[:8] or "-"
    m2 = re.search(r"\[auto-gate\]\s+\S+\s+@\s+(.+?)：", out)
    repo_name = os.path.basename((m2.group(1) if m2 else "").replace("\\", "/").rstrip("/")) or "-"
    print("[harness] gate=%s repo=%s rc=%s files=%s ts=%s h=%s" % (
        rec.get("gate"), repo_name, rec.get("rc"), rec.get("files"), rec.get("ts"), h8))
    print("  复核：%s log --gate %s --last 1" % (os.path.join(ROOT, "guardrail_engine", "cli.py"), gate))
    return rc


def cmd_summary(a):
    cmd = [_py(), os.path.join(ROOT, "harness", "run_gate.py"), "--summary"]
    if a.last:
        cmd.append(str(int(a.last)))
    return _print_out(*_run(cmd))


def cmd_coverage(a):
    cmd = [_py(), os.path.join(ROOT, "harness", "coverage.py")]
    if a.last:
        cmd += ["--last", str(int(a.last))]
    if a.json:
        cmd.append("--json")
    return _print_out(*_run(cmd))


def cmd_doctor(_a):
    b = _bash()
    if not b:
        print("bash unavailable — install Git-for-Windows / bash; or use: guardrail-engine list|summary|coverage")
        return 2
    return _print_out(*_run([b, os.path.join(ROOT, "sandbox", "doctor.sh")], timeout=360))


def cmd_mcp(a):
    return subprocess.run([_py(), MCP_PATH] + list(a.rest or []), cwd=ROOT).returncode


def _read_jsonl(path, last):
    """读台账 jsonl 的**最后 N 条**。

    评审 2026-09-23 L10：`last` 为 0 或负数时旧写法会读成**全量** —— `lines[-0:]` 在 Python 里
    等价于 `lines[0:]`（`-0 == 0`），于是 `cli.py log --last 0` 把整本台账倒出来，而不是
    "一条都不要"；负值更离谱（`lines[5:]` = 丢掉开头 5 条、把其余全部返回），两种都既不是
    "最后 N 条"也不是"全部"。台账在本机可以是几千条，这类"参数写着 0、行为是全量"的错位
    在排查现场很难被认出来（输出看着就是一份正常台账）。显式挡住：N≤0 就是 0 条。
    """
    recs, bad = [], 0
    if not os.path.exists(path):
        return recs, bad
    if last is None or last <= 0:
        return recs, bad
    with io.open(path, encoding="utf-8", errors="replace") as f:
        lines = [l for l in f.read().splitlines() if l.strip()]
    for l in lines[-last:]:
        try:
            recs.append(json.loads(l))
        except Exception:
            bad += 1
    return recs, bad


def _mark_of(r):
    """把「这一条不能当普通通过读」的事实渲染成一列（评审 v13 中 15 / 复查遗留项②）。

    本轮往台账里补了 skipped / weakened 两个字段，但**读台账的人只看这张表** ——
    字段进了 jsonl 而表里看不见，等于还得去 tail 原始文件才知道，那正是这些修复
    要消灭的处境（本项目自称"出问题先看台账"）。
    用 ASCII 标签：控制台可能是 GBK（见 checks/harness_guard.py 文件头的实测记录）。
    """
    m = []
    if r.get("skipped"):
        m.append("skip")
    for w in (r.get("weakened") or []):
        m.append("weak:" + str(w))
    return ",".join(m) or "-"


def _canon_rec(rec):
    """台账记录的规范化形（链哈希与整本摘要共用）：去 h + sort_keys + 紧凑分隔符。

    与 harness/run_gate.py 的 _chain_h 规范化是**同一公式**（壳不 import 引擎，
    tests/engine/test_gate_log_anchor.py 用同一批记录喂两份实现对拍 —— 漂移即红）。
    """
    payload = dict(rec)
    payload.pop("h", None)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _chain_h(prev_h, rec):
    """行内链哈希（与 run_gate._chain_h 同公式）：h = sha256(上一行h + 规范化JSON) 截 16 hex。"""
    return hashlib.sha256(((prev_h or "") + _canon_rec(rec)).encode("utf-8")).hexdigest()[:16]


def _read_ledger(last=None):
    """跨片读台账 → (recs, bad)。last=N：从最新片往回收集（片有界，日常只碰尾部几片）。

    读序 = 时间序（_ledger_paths）；坏行计数不抛（观测不能反过来炸查看，同 _read_jsonl）。
    last<=0 → 0 条（2026-09-23 L10 同一条判据：「参数写 0、行为全量」的错位不许复活）。
    """
    paths = [p for p in _ledger_paths() if os.path.exists(p)]
    if not paths:
        return [], 0
    if last is not None and last <= 0:
        return [], 0
    shards = []
    for p in paths:
        try:
            with io.open(p, encoding="utf-8", errors="replace") as f:
                shards.append([l for l in f.read().splitlines() if l.strip()])
        except OSError:
            shards.append([])
    if last:
        picked, got = [], 0
        for lines in reversed(shards):
            if got >= last:
                break
            take = lines[-(last - got):]
            picked.insert(0, take)
            got += len(take)
    else:
        picked = shards
    recs, bad = [], 0
    for chunk in picked:
        for l in chunk:
            try:
                recs.append(json.loads(l))
            except Exception:
                bad += 1
    return recs, bad


def _anchor_probe():
    """锚点文件尾行 → `(记录 or None, 状态)`。状态三态：`ok` / `absent` / `corrupt`。

    ★ 2026-09-30 修（原缺陷）：这里原先写作
        `try: … return json.loads(last) if last else None` / `except (OSError, ValueError): return None`
    —— 把「**没有**锚点文件」与「有文件但尾行解析不了」合并成同一个 `None`。后果是
    **往锚点文件尾部追加一个字节，`gate log --verify` 就退回"尚无锚点"并 rc=0**：
    保留链合法的前缀、砍掉台账尾巴、再弄坏锚点尾行，防线整个失效且报绿 ——
    而"抓截断/重写"正是锚点存在的唯一理由。

    所以三态必须显式：只有 `absent`（文件不在 / 空文件）才允许说"尚无锚点"；
    `corrupt` 必须 rc=1 点名（见 `_cmd_verify` / `_cmd_anchor`）。
    """
    path = _anchor_path()
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            last = None
            for line in f:
                if line.strip():
                    last = line
    except OSError:
        return None, "absent"          # 读不到 = 与"还没有"同义（真读不了也只会少拦，不会假绿）
    if last is None:
        return None, "absent"          # 空文件 / 全空行：还没锚过，属正常
    try:
        rec = json.loads(last)
    except ValueError:
        return None, "corrupt"         # ★ 有内容但尾行不是 JSON —— 不许当成"没有锚点"
    if not isinstance(rec, dict):
        return None, "corrupt"
    return rec, "ok"


def _rollup(recs, from_n):
    """自上一锚点（第 from_n 条）以来的聚合 —— 「规则从未跑到」类分析以后可只读这里，
    不必每次穿全量台账（月度 rollup 的消费口：coverage --quick）。

    rules 字段兼容 dict（规则→计数或计数对象）与 list（触发过的规则 id）两种历史形状，
    出现即计数 —— 形状漂移时宁可粗、不可假（台账说假话比不说话更坏）。
    """
    win = recs[from_n:] if from_n else recs
    rc, gates, rules = {}, {}, {}
    weak = skip = 0
    for r in win:
        rc[str(r.get("rc"))] = rc.get(str(r.get("rc")), 0) + 1
        g = str(r.get("gate", "?"))
        gates[g] = gates.get(g, 0) + 1
        if r.get("weakened"):
            weak += 1
        if r.get("skipped"):
            skip += 1
        rs = r.get("rules")
        if isinstance(rs, dict):
            for k, v in rs.items():
                if isinstance(v, dict):
                    n = sum(x for x in v.values() if isinstance(x, int))
                elif isinstance(v, int):
                    n = v
                else:
                    n = 1
                rules[str(k)] = rules.get(str(k), 0) + int(n)
        elif isinstance(rs, list):
            for k in rs:
                rules[str(k)] = rules.get(str(k), 0) + 1
    return {"from_n": from_n or 0, "n": len(win), "rc": rc, "weakened": weak,
            "skipped": skip, "gates": gates, "rules_fired": rules}


def _cmd_anchor():
    """把台账钉进 git：追加一条锚点记录 + 打印现成的 git 命令（**不自动 commit —— 人类掌舵**）。

    锚点 = 全量统计（n / 首尾 ts / 链尾 h / 整本 sha256）+ 自上一锚点以来的 rollup。
    台账本体继续 git 忽略（本机私有、只涨），只把锚点文件入库：
      · 篡改台账任何一行 → 与锚点失配（--verify 点名）
      · 截断锚定段 → n / sha256 失配
      · 想连锚点一起改 → 得改本仓 git 历史（可见，且在本仓钩子注视下）
    物理上限（诚实声明）：两次锚定之间的「尾部截断」要等下一次锚定比对才现形 ——
    再往上就是把权威放 CI（已与用户对齐的方向，不在本轮）。
    """
    recs, bad = _read_ledger(None)
    if not recs:
        print("(台账为空，无物可锚)")
        return 1
    tail_h = ""
    for r in reversed(recs):
        if r.get("h"):
            tail_h = str(r["h"])
            break
    body = "".join(_canon_rec(r) + "\n" for r in recs)
    set_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    prev, pstate = _anchor_probe()
    if pstate == "corrupt":
        # 尾行坏了还往上接，等于把新锚点的 `prev` 记成"无前锚" —— 锚点成链的那一头断了，
        # 而且断得无声。宁可这次不锚，先把尾行的事说清楚（人类掌舵）。
        print("✗ 锚点文件尾行无法解析（%s）：不在此之上追加新锚点。" % _anchor_path())
        print("  否则新锚点会把 `prev` 记成空 —— 锚点之间的接续断在这里且不留痕。")
        print("  处置：先核对尾行是不是被人动过；确认是误伤就删掉那一行，再重跑 --anchor。")
        return 1
    prev_n = int(prev.get("n") or 0) if prev else 0
    anchor = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "kind": "anchor", "sv": 1,
        "n": len(recs),
        "first_ts": recs[0].get("ts", ""), "last_ts": recs[-1].get("ts", ""),
        "tail_h": tail_h, "set_sha256": set_sha, "bad_lines": bad,
        "rollup": _rollup(recs, prev_n),
        "prev": ({"n": prev.get("n"), "tail_h": prev.get("tail_h", ""),
                  "set_sha256": prev.get("set_sha256", "")} if prev else None),
    }
    try:
        os.makedirs(os.path.dirname(_anchor_path()), exist_ok=True)
        with open(_anchor_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(anchor, ensure_ascii=False) + "\n")
    except OSError as e:
        print("[anchor] 锚点写入失败：%r" % e)
        return 2
    print("锚点已追加 → %s" % _anchor_path())
    print("  n=%d  tail_h=%s  set_sha256=%s%s" % (
        len(recs), tail_h or "-", set_sha[:16], ("  坏行=%d" % bad) if bad else ""))
    print("接着把锚点入库（台账本体保持忽略 —— 只锚定，不入账）：")
    print("  git add reports/gate_log_anchor.jsonl")
    print('  git commit -m "[harness] 台账锚定: n=%d tail=%s sha=%s"' % (
        len(recs), (tail_h or "-")[:16], set_sha[:16]))
    return 0


_ENGINE_SHA_TRAILER = re.compile(r"(?m)^Engine-Sha:[ \t]*([0-9a-f]{16})[ \t]*$")


def _engine_sha_now():
    """当前引擎内容哈希（转发 run_gate --engine-sha）→ (sha, 错误)。"""
    rc, out, err = _run([_py(), os.path.join(ROOT or "", "harness", "run_gate.py"),
                         "--engine-sha"])
    lines = (out or "").strip().splitlines()
    if rc == 0 and lines:
        return lines[-1].strip(), ""
    return None, ((err or out or "").strip() or ("rc=%s" % rc))


def _init_marker_engine_sha(root):
    """读 init 标记里的 engine_sha 信任锚（安装副本形态）→ 16-hex 或 ""。

    与 `harness/_init_marker.read_engine_sha` 同口径，但 cli 不 import harness/（壳层一律 shell out），
    故就地读 JSON；标记文件名复用 `_shell.INIT_MARKER` / `LEGACY_INIT_MARKER`（写侧用的同一组常量）。
    """
    for name in (_shell.INIT_MARKER, _shell.LEGACY_INIT_MARKER):
        p = os.path.join(root or "", name)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        sha = str((data or {}).get("engine_sha") or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{16}", sha):
            return sha
    return ""


def _engine_known_shas(repo=None):
    """已提交的引擎内容哈希 → (set, 核对基线 ts)。

    来源是 git 历史里的 `Engine-Sha:` 尾注。只认**同时带 `[harness]`** 的提交：
    普通提交里写一行尾注就等于能凭空把一个 sha 加进"已提交"集合，那这条对账线自己
    就先失效了。带 `[harness]` 的提交由 CI 重算比对（见 .github/workflows/ci.yml）。
    `HARNESS_ENGINE_SHAS` / `HARNESS_ENGINE_SINCE` 只给测试与离线审计注入用 ——
    本函数是读侧，不参与任何拦截判定。

    2026-11-04（评审 P0 修复 #3）：**安装副本没有 git 历史**（无 .git），git 路径必然空手而归。
    此时把 init 标记里的 engine_sha 当已知基线，否则副本跑出的台账永远"无基线可归因"，
    `gate log --verify --engine` 形同跳过（而副本恰恰是最需要对账的部署形态）。
    """
    injected = os.environ.get("HARNESS_ENGINE_SHAS")
    if injected is not None:
        return set(re.findall(r"[0-9a-f]{16}", injected)), os.environ.get("HARNESS_ENGINE_SINCE", "")
    shas, oldest = set(), None
    try:
        r = subprocess.run(["git", "-C", repo or ROOT or ".", "log", "--format=%ct%x1f%B%x1e"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        git_ok, out = (r.returncode == 0), (r.stdout or "")
    except (OSError, subprocess.SubprocessError):
        git_ok, out = False, ""
    if git_ok:
        for chunk in out.split("\x1e"):
            chunk = chunk.strip("\n")
            if not chunk:
                continue
            ts, _, body = chunk.partition("\x1f")
            if "[harness]" not in body:
                continue
            found = _ENGINE_SHA_TRAILER.findall(body)
            if not found:
                continue
            shas.update(found)
            try:
                epoch = int(ts)
            except ValueError:
                continue
            oldest = epoch if oldest is None else min(oldest, epoch)
    if not shas:
        # 无 git 历史（安装副本）或历史里没有带 [harness] 的尾注 → 用 init 标记的 engine_sha 兜底。
        marker_sha = _init_marker_engine_sha(repo or ROOT or ".")
        if marker_sha:
            shas.add(marker_sha)
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(oldest)) if oldest else ""
    return shas, since


def _engine_report(recs):
    """核对台账里的 engine_sha 能否归因到**已提交**的引擎内容（读侧，rc=1 只影响本命令）。

    为什么不能直接比「记录 sha == 当前引擎」：正常升级、甚至改一行测试锁都会让当前哈希变，
    那样每次升级后全本都红，等于逼人忽略它。这里用的是**提交历史**：一条判定要么用
    某一版已提交的引擎跑出来的，要么就是「跑的时候引擎内容从未被提交」—— 后者正是
    「瞬时改弱 → 跑通过 → 改回 → 提交」在台账里留下的形状。

    基线 = 最早的 `Engine-Sha` 尾注所在提交时间；更早的历史不追溯（那时还没有这个约定）。
    开发期用未提交引擎跑出的记录会被点名：这是有意的审计口径，重跑一次已提交引擎即可。
    """
    cur, err = _engine_sha_now()
    if cur is None:
        print("⚠ 引擎内容核对跳过：算不出当前 engine_sha（%s）" % err[:200])
        return 0
    known, since = _engine_known_shas()
    print("=== 引擎内容核对（--engine）===")
    print("  当前引擎 sha: %s" % cur)
    if not known:
        print("  尚无 Engine-Sha 尾注 —— 本功能启用前的历史不追溯。")
        print("  今后带 [harness] 的提交需加一行 `Engine-Sha: <sha>`，CI 会重算校验。")
        return 0
    print("  已提交的引擎内容: %d 个（核对基线 %s）" % (len(known), since or "—"))
    checked = missing = 0
    bad = []
    for i, rec in enumerate(recs, 1):
        ts = str(rec.get("ts") or "")
        if since and ts and ts < since:
            continue
        sha = str(rec.get("engine_sha") or "")
        if not sha:
            missing += 1
            continue
        checked += 1
        if sha not in known:
            bad.append((i, ts, rec.get("gate"), sha))
    if missing:
        print("  △ %s有 %d 条记录没有 engine_sha（旧格式），无法核对。"
              % ("基线之后" if since else "全本", missing))
    if bad:
        print("  ✗ %d 条记录的 engine_sha 不属于任何已提交的引擎内容：" % len(bad))
        for i, ts, gate, sha in bad[:10]:
            print("      第 %d 行 %s gate=%s sha=%s" % (i, ts, gate, sha))
        if len(bad) > 10:
            print("      …另有 %d 条" % (len(bad) - 10))
        print("  说明：开发期用未提交的引擎跑门禁也会落到这里。把那份引擎提交并带")
        print("  `Engine-Sha` 尾注，或重跑一次当前已提交引擎的门禁；审阅时按此清单追。")
        return 1
    if checked == 0:
        print("  · 核对范围内没有可对账的记录（0 条带 engine_sha）。")
        return 0
    print("  ✓ 基线之后的 %d 条记录都对应已提交的引擎内容" % checked)
    return 0


def _cmd_verify(engine=False):
    """重算行内链 + 与最后一条锚点对账（**读侧命令**：进不了门禁路径，绝不反向拦人）。

    四态：链完整且锚点吻合 / 链完整但锚点之后有未锚定新增（仅链保护，出声）/
    断裂或失配（行内链断点行号、锚定段 sha、截断、坏行数变化，逐项点名）/
    **锚点尾行无法解析**（rc=1，点名 —— 2026-09-30 修：这一态原先被并进"尚无锚点"，
    于是弄坏锚点尾行 = 防线静默消失且报绿）。

    engine=True（--engine）：在链/锚点**通过之后**再核对 engine_sha 的归因（见
    `_engine_report`）。链本身红了就先修链 —— 那时连记录是否原样都还不确定。
    """
    recs, bad = _read_ledger(None)
    if not recs:
        print("(台账为空: %s)" % ", ".join(_ledger_paths()))
        return 1
    prev_h, chain_start = "", None
    for i, r in enumerate(recs, 1):
        h = r.get("h")
        if not h:
            if chain_start is not None:
                print("✗ 链断裂：第 %d 行缺 h（链起于第 %d 行，之后每行都必须有）" % (i, chain_start))
                return 1
            continue                       # 旧格式行（无链保护），链从第一条带 h 的行起头
        if chain_start is None:
            chain_start = i
        expect = _chain_h(prev_h, r)
        if h != expect:
            print("✗ 链断裂：第 %d 行 h 不匹配（期望 %s / 实际 %s）" % (i, expect, h))
            return 1
        prev_h = h
    if chain_start is None:
        print("△ 全本 %d 条都是旧格式（无行内链）—— 先跑一次 `gate log --anchor` 建链。" % len(recs))
        return 1
    anchor, astate = _anchor_probe()
    if astate == "corrupt":
        # ★ 2026-09-30 修：锚点尾行坏掉 ≠ 没有锚点。这里原先落到下面的 `anchor is None`
        # 分支、报"尚无锚点"并 rc=0 —— 于是一个垃圾字节就能让锚点防线消失，且是绿的。
        print("✗ 锚点文件尾行无法解析（%s）：锚点已失效，本命令**不能**判台账完整。"
              % _anchor_path())
        print("  说明：行内链本身已通过校验，但「截断 / 重写」那一半保护已随锚点一起失效。")
        print("  处置：先核对尾行是不是被人动过；确认是误伤就删掉那一行，再重跑 --verify。")
        return 1
    if anchor is None:
        print("✓ 链完整（%d 条，起于第 %d 行）—— 尚无锚点；`gate log --anchor` 后按提示入库。"
              % (len(recs), chain_start))
        return _engine_report(recs) if engine else 0
    n_a = int(anchor.get("n") or 0)
    if len(recs) < n_a:
        print("✗ 台账被截断：锚点记 n=%d，现在只剩 %d 条" % (n_a, len(recs)))
        return 1
    body = "".join(_canon_rec(r) + "\n" for r in recs[:n_a])
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if sha != str(anchor.get("set_sha256") or ""):
        print("✗ 锚定段失配：前 %d 条 sha256 与锚点不符（锚点 %s… / 现算 %s…）" % (
            n_a, str(anchor.get("set_sha256"))[:16], sha[:16]))
        return 1
    a_bad = int(anchor.get("bad_lines") or 0)
    extra = len(recs) - n_a
    if extra == 0 and bad != a_bad:
        print("✗ 坏行数变化：锚点记 %d，现在 %d —— 有行被改坏（或被修复），先核对" % (a_bad, bad))
        return 1
    if extra == 0:
        print("✓ 链完整且锚点吻合：n=%d，链起于第 %d 行，尾 h=%s" % (len(recs), chain_start, prev_h))
        return _engine_report(recs) if engine else 0
    print("✓ 链完整；锚点吻合（n=%d）；其后有 %d 条未锚定新增（仅链保护）" % (n_a, extra))
    return _engine_report(recs) if engine else 0


def cmd_log(a):
    if getattr(a, "engine", False) and not getattr(a, "verify", False):
        # 静默忽略 = 用户以为核对过了。这个开关没有独立含义，只有和 --verify 一起才成立。
        print("✗ --engine 只与 --verify 合用（它核对台账归因，本身不产出记录视图）")
        return 2
    if getattr(a, "anchor", False):
        return _cmd_anchor()
    if getattr(a, "verify", False):
        return _cmd_verify(engine=getattr(a, "engine", False))
    if a.log:                       # 显式指文件：单文件旧路径（密闭 / 对账某一片）
        src = a.log
        recs, bad = _read_jsonl(a.log, a.last)
    else:
        recs, bad = _read_ledger(a.last)
        paths = _ledger_paths()
        src = "%s（%d 片）" % (os.path.dirname(paths[0]) if paths else os.path.join(ROOT, "reports"),
                              len(paths))
    if getattr(a, "gate", None):
        # 批次 2（2026-10-15）：--gate 过滤 —— 给 Hermes 这类**无编辑器钩子**的 agent 做
        # 「任务完成前自查」用：查台账里有没有本仓 gate 的本次判定记录（与 --last 组合）。
        # 过滤在读取之后、导出/展示之前：--export 导出的也是过滤后的视图。
        # 窗口内无该 gate 记录 → rc=1 + 稳定标记 NO_RECORD：自查命令要能机读区分
        # 「查到了」与「压根没跑过」（后者若同样 rc=0，自查就形同虚设）。
        recs = [r for r in recs if r.get("gate") == a.gate]
        if not recs:
            print("NO_RECORD gate=%s window=%d（台账 %s 窗口内没有该 gate 的记录 —— gate 名对不上？还是根本没跑过门禁？）"
                  % (a.gate, a.last, src))
            return 1
    if not recs and not bad:
        print("(台账为空或不存在: %s)" % src)
        return 0
    if a.export:
        if a.format == "json":
            with open(a.export, "w", encoding="utf-8") as f:
                json.dump(recs, f, ensure_ascii=False, indent=2)
        else:
            keys = ["ts", "entry", "via", "ver", "gate", "scope", "files", "rc", "ms",
                    "mark", "skipped", "weakened"]
            with open(a.export, "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
                w.writeheader()
                for r in recs:
                    row = {k: r.get(k, "") for k in keys}
                    row["mark"] = _mark_of(r)
                    if isinstance(row.get("weakened"), list):
                        row["weakened"] = ";".join(str(x) for x in row["weakened"])
                    w.writerow(row)
        print("已导出 %d 条 → %s (%s)" % (len(recs), a.export, a.format))
        return 0
    print("ts                  entry      ver      gate              scope     rc  files  ms    via                mark")
    for r in recs:
        print("%-19s %-10s %-8s %-17s %-9s %-3s %-6s %-5s %-18s %s" % (
            str(r.get("ts", ""))[:19], str(r.get("entry", "-"))[:10], str(r.get("ver", "-"))[:8],
            str(r.get("gate", "?"))[:17], str(r.get("scope", ""))[:9], r.get("rc", "?"),
            r.get("files", ""), r.get("ms", ""),
            str(r.get("via", "-"))[:18], _mark_of(r)))
    by_rc, by_entry = {}, {}
    for r in recs:
        by_rc[r.get("rc")] = by_rc.get(r.get("rc"), 0) + 1
        by_entry[r.get("entry", "-")] = by_entry.get(r.get("entry", "-"), 0) + 1
    print("共 %d 条 | rc 分布: %s | 入口分布: %s | 不可解析: %d" % (
        len(recs), dict(sorted(by_rc.items(), key=lambda x: str(x[0]))), by_entry, bad))
    print("(台账: %s)" % src)
    return 0


def cmd_ops(a):
    path = _ops_path()
    recs, bad = _read_jsonl(path, a.last)
    if not recs:
        print("(运维台账为空: %s)" % path)
        return 0
    for r in recs:
        print("%-19s %-16s %-8s %s" % (str(r.get("ts", ""))[:19], str(r.get("op", "?"))[:16],
                                       str(r.get("result", ""))[:8], r.get("reason", r.get("repo", ""))))
    print("共 %d 条（不可解析 %d）| 台账: %s" % (len(recs), bad, path))
    return 0


def _skill_target(a):
    if a.dir:
        return os.path.abspath(a.dir)
    if a.target in SKILL_PRESETS:
        base = SKILL_PRESETS[a.target]
        new = os.path.join(base, SKILL_NAME)
        legacy = os.path.join(base, LEGACY_SKILL_NAME)
        if not os.path.isdir(new) and os.path.isdir(legacy):
            return legacy          # 迁移期：老安装用老名字，卸载要落到它身上
        return new
    return None


def cmd_skill(a):
    if a.action == "status":
        for name, base in SKILL_PRESETS.items():
            t = os.path.join(base, SKILL_NAME)
            if not os.path.isdir(t):
                legacy = os.path.join(base, LEGACY_SKILL_NAME)
                if os.path.isdir(legacy):
                    t = legacy
            state = "已安装" if os.path.isdir(t) else "未安装"
            arch = _skill_archives(base)
            if arch:
                state += "（另有卸载残骸 %d 份，见下）" % len(arch)
            print("  %-10s %-55s %s" % (name, t, state))
            # 残骸不滚动列出：这里只列前几份 + 总数，避免"卸载过很多次"时刷屏（真要看全，
            # 目录就在上一行的 base 里）。列出来是为了让人**知道有这么个东西**并知道怎么清。
            for p in arch[:3]:
                print("            残骸: %s" % p)
            if len(arch) > 3:
                print("            …还有 %d 份（同上目录）" % (len(arch) - 3))
        return 0
    target = _skill_target(a)
    if not target:
        print("请指定 --target {claude|opencode|codebuddy} 或 --dir <目录>")
        return 2
    if a.action == "install":
        if not os.path.isdir(SKILL_SRC):
            print("技能源不存在: %s" % SKILL_SRC)
            return 2
        c, u, s = _copy_tree_if_changed(SKILL_SRC, target)
        print("技能安装 → %s（新增 %d / 更新 %d / 未变 %d）" % (target, c, u, s))
        _record_op({"op": "skill_install", "target": target, "result": "ok",
                    "reason": "created=%d updated=%d same=%d" % (c, u, s)})
        return 0
    if a.action == "uninstall":
        if not os.path.isdir(target):
            print("未找到已安装技能: %s（无需卸载）" % target)
            return 0
        arch = archive_name(target)
        try:
            os.rename(target, arch)
        except OSError as e:
            # 说清"什么没发生"：此前只印一行失败原因，看不出技能到底动没动
            print("改名归档失败：%s" % _rename_hint(e))
            print("  （技能**未**被移动，%s 仍在原处——本次卸载没有生效，装着的还是它）" % target)
            return 2
        print("已卸载（改名归档，可逆）：%s → %s" % (target, arch))
        _record_op({"op": "skill_uninstall", "target": target, "result": "archived", "reason": arch})
        return 0
    return 2


def _git_out(args, cwd=None):
    """跑 git → (rc, stdout, stderr)。**git 不可用不算"查到了"**（评审报告 L6）。

    原先裸 `subprocess.run(["git"] ...)`：没装 git / cwd 不存在时抛 `FileNotFoundError`
    /`NotADirectoryError`，调用方（hooks status/install/uninstall、read_prev_hookspath）
    全都没接 —— 一个没装 git 的机器上跑 `setup` 得到的是裸回溯，而不是"这说明什么"。
    归成 rc=127（shell 的"命令找不到"惯例）+ stderr 说明，调用方按已有的 rc!=0 分支处理。
    """
    try:
        p = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return 127, "", "找不到 git（未安装或不在 PATH）——本次无法读写仓库配置"
    except (NotADirectoryError, PermissionError, OSError) as e:
        return 127, "", "git 起不来（%r）" % (e,)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


# ---- 「安装前 core.hooksPath」存档（评审 v13 中 17）----------------------------------
# 存档由 sandbox/install_hooks.sh 写、本壳读。路径与行格式是**跨脚本契约**：
# sandbox/uninstall.py 的兜底分支（没有 CLI 可用时）按同样格式读，tests 有对拍锁。
BACKUP_DIRNAME = "agentguard-original-hooks"
PREV_HP_NAME = "PREVIOUS-hooksPath.txt"
PREV_HP_PREFIX = "安装前 core.hooksPath："
PREV_HP_UNSET = "<未设置>"


def _abs_git_path(repo, p):
    """git 在仓内会返回**相对**路径（`.git` / `.git/worktrees/<名>`）→ 按仓根解析。

    与 sandbox/install_hooks.sh 的 COMMON 处理同源（那边记着 2026-09-12 的踩坑）。
    """
    p = (p or "").strip().replace("\\", "/")
    if not p:
        return None
    if p.startswith("/") or (len(p) > 2 and p[1] == ":" and p[2] == "/"):
        return os.path.abspath(p)
    return os.path.abspath(os.path.join(repo, p))


def _prev_hookspath_archive(repo):
    """存档位置：**common-dir** 下的备份目录（worktree 里真实共享 hooks 在那边）。

    只查 common-dir，不退回 absolute-git-dir：安装侧就只写这里，
    多查一处只会让"存档到底在哪"变成两个答案。
    """
    rc, out, _ = _git_out(["-C", repo, "rev-parse", "--git-common-dir"])
    if rc != 0:
        return None
    common = _abs_git_path(repo, out)
    return os.path.join(common, BACKUP_DIRNAME, PREV_HP_NAME) if common else None


def read_prev_hookspath(repo):
    """读安装时留下的原值 → (状态, 值)。

    状态：
      `ok`      有可用原值（卸载时还原到它）
      `unset`   安装前本来就没设置（卸载时 --unset）
      `none`    没有存档 —— 只能清空，**不能**假装恢复原状
      `selfref` 存档记的是门禁自己的共享目录：旧版安装脚本每次重写存档留下的坏档
                （评审 v13 中 17），照它还原等于原地打转 —— 按清空处理并说明。
    """
    arch = _prev_hookspath_archive(repo)
    if not arch or not os.path.isfile(arch):
        return "none", None
    val = None
    try:
        with io.open(arch, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith(PREV_HP_PREFIX):
                    val = line[len(PREV_HP_PREFIX):].strip()
                    break
    except OSError:
        return "none", None
    if not val:
        return "none", None
    if val == PREV_HP_UNSET:
        return "unset", None
    shared = os.path.join(ROOT, "harness", "hooks-dir") if ROOT else ""
    if shared and os.path.normcase(os.path.abspath(val)) == os.path.normcase(os.path.abspath(shared)):
        return "selfref", val
    return "ok", val


def cmd_hooks(a):
    repo = os.path.abspath(a.repo or os.getcwd())
    if a.action == "status":
        rc, out, err = _git_out(["-C", repo, "config", "--local", "--get", "core.hooksPath"])
        print("repo: %s" % repo)
        print("core.hooksPath: %s%s" % (out or "(未设置)", ("  [%s]" % err) if err else ""))
        if rc in (0, 1):     # 是 git 仓（1 = 未设置）；非仓时下面这行会误导人
            state, prev = read_prev_hookspath(repo)
            print("安装前 core.hooksPath（存档）: %s" % {
                "ok": "%s   ← 卸载时会还原到这个值" % prev,
                "unset": "(未设置)   ← 卸载时会清空",
                "selfref": "存档已坏（记的是门禁自己的目录）—— 卸载只能清空",
                "none": "(无存档)   ← 卸载只能清空，无法还原接入前的自定义目录",
            }[state])
        legacy = os.path.join(repo, ".git", "hooks", "pre-commit")
        if os.path.isfile(legacy):
            print("注意: .git/hooks/pre-commit 仍存在（旧拷贝模式遗留）——如非受管请人工确认")
        return 0
    if a.action == "install":
        # 评审报告 H2 的现场之一：uninstall 分支早有这道校验，install 没有 ——
        # `setup --repo <不存在的目录>` 会一路走到 subprocess 的 cwd 才崩。
        if not os.path.isdir(repo):
            print("repo 不存在: %s" % repo)
            return 2
        b = _bash()
        if not b:
            print("需要 bash（git-bash）来运行 sandbox/install_hooks.sh；请安装 Git-for-Windows 后重试")
            return 2
        cmd = [b, os.path.join(ROOT, "sandbox", "install_hooks.sh")]
        if a.gate:
            cmd.append(a.gate)
        rc, out, err = _run(cmd, cwd=repo)
        _print_out(rc, out, err)
        _record_op({"op": "hooks_install", "repo": repo, "gate": a.gate or "", "result": "ok" if rc == 0 else "fail",
                    "reason": (out or err)[:160]})
        return rc
    if a.action == "uninstall":
        if not os.path.isdir(repo):
            print("repo 不存在: %s" % repo)
            return 2
        # 评审 v13 中 17：先看安装时留下的原值，再决定是「还原」还是「清空」。
        # 原先一律 --unset 就打印「回滚完成」—— 而接入前指向自定义 hooks 目录的仓
        # （实测 .myhooks）根本没被复原：那不是回滚，是把人家的目录从配置里抹掉了。
        state, prev = read_prev_hookspath(repo)
        if state == "ok":
            rc, out, err = _git_out(["-C", repo, "config", "--local", "core.hooksPath", prev])
            if rc != 0:
                print("✗ 还原安装前的 core.hooksPath 失败: %s" % (err or out))
                return 2
            print("已还原安装前的 core.hooksPath → %s" % prev)
            why = "hooksPath restored to %s" % prev
        else:
            rc, out, err = _git_out(["-C", repo, "config", "--local", "--unset", "core.hooksPath"])
            if rc == 5:
                print("core.hooksPath 本来就未设置（无需卸载）")
            elif rc != 0:
                print("git config --unset 失败: %s" % (err or out))
                return 2
            print("已卸载：core.hooksPath 已清空")
            if state == "none":
                print("注意: 本仓没有安装时的原值存档（%s 不存在）——"
                      "只能清空，**无法**还原接入前指向的自定义 hooks 目录。"
                      % (PREV_HP_NAME,))
            elif state == "selfref":
                print("注意: 存档里的原值指向门禁自己的共享目录（旧版安装脚本每次重装都会覆盖"
                      "存档，这是那种坏档）——照它还原等于原地打转，故按清空处理。")
            why = "hooksPath cleared (archive=%s)" % state
        rc2, out2, _ = _git_out(["-C", repo, "config", "--local", "--get", "core.hooksPath"])
        print("当前 core.hooksPath = %s" % (out2 or "(未设置)"))
        legacy = os.path.join(repo, ".git", "hooks", "pre-commit")
        if os.path.isfile(legacy):
            print("注意: .git/hooks/pre-commit 仍存在 —— 它会导致默认 hooks 目录继续执行旧钩子；"
                  "本工具不做删除，请人工确认后处理（改名或移走均可）。")
        if state in ("ok", "unset"):
            print("回滚完成：该仓库已恢复到接入提交期门禁之前的状态。")
        else:
            # 不谎报：清空 ≠ 恢复原状（见上面那条 note）
            print("已撤回接入：core.hooksPath 已清空，但**不等于恢复原状**（见上面的存档说明）。")
        _record_op({"op": "hooks_uninstall", "repo": repo, "result": "ok", "reason": why})
        return 0
    return 2


def cmd_bypass(a):
    """声明一次紧急旁路 —— **这不是权限闸**（评审 2026-09-23 L19）。

    本命令不做任何拦截、也不改变门禁判定：任何调用方都能声明它，`--no-verify` 本来也照样跑得通。
    它唯一的作用是**把这个动作写进运维台账**（人员 / 时间 / 理由 / 真实命令），让一次绕过
    「可见、可审计」。把它误当成授权闸会得到一个危险的错觉 —— 以为"没声明就跑不了"，
    而实际上是"跑了没人知道"才是原状。实现与 RUNBOOK 一致、从无虚假承诺，缺的只是这句定性，
    现补在用法处（本命令输出 + README + RUNBOOK）。
    """
    repo = os.path.abspath(a.repo or os.getcwd())
    reason = (a.reason or "").strip()
    if len(reason) < 10:
        print("✗ 拒绝：--reason 必填且 ≥10 字（旁路必须留痕：写清为什么、给谁看）")
        return 2
    who = "%s@%s" % (os.environ.get("USERNAME") or os.environ.get("USER") or "?",
                     os.environ.get("COMPUTERNAME") or "?")
    _record_op({"op": "bypass_declared", "repo": repo, "actor": who, "reason": reason, "result": "declared"})
    print("⚠️  紧急旁路 —— 已记录台账（可见提示 + 留痕）")
    print("    定性: 这是**声明留痕入口**，不是权限闸 —— 它不拦任何提交、也不改变门禁判定；")
    print("          任何人都能声明它（也可以不声明直接 --no-verify，区别只是那样没人知道）。")
    print("    仓库: %s" % repo)
    print("    人员: %s    时间: %s" % (who, time.strftime("%Y-%m-%d %H:%M:%S")))
    print("    理由: %s" % reason)
    print("    跳过命令: git -C \"%s\" commit --no-verify -m \"<提交说明>\"" % repo)
    print("    注意: 如需绕过请说明原因；台账记录本次声明旁路。")
    print("    提示: 旁路仅限紧急；事后请向负责人说明。")
    return 0


def _print_mcp_config():
    cfg = {"mcpServers": {SERVER_NAME: {"command": _py(), "args": [MCP_PATH]}}}
    print("① MCP 接入（任何 MCP 宿主通用；一行配置）:")
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    print('   Claude Code:  claude mcp add %s -- "%s" "%s"' % (SERVER_NAME, _py(), MCP_PATH))
    print("   OpenClaw:     见 docs/packaging/RUNBOOK.md（openclaw mcp add 一段）")


def cmd_quickstart(_a):
    print("== guardrail-engine quickstart ==")
    print("engine root : %s" % ROOT)
    print("version     : %s" % _version())
    rc, out, err, gates = _gates()
    print("engine 自检 : list-gates rc=%s, gates=%d (enabled=%d)" % (rc, len(gates), sum(1 for g in gates if g["enabled"])))
    print()
    _print_mcp_config()
    print()
    p = _cmd_prefix()
    print("② 提交期硬兜底（每个端仓一次）:")
    print("   %s hooks install --repo <端仓路径> [--gate <gate>]" % p)
    print("③ 技能包（可选，agent 侧说明书）:")
    print("   %s skill install --target claude" % p)
    print("④ 第一次门禁判定:")
    print("   %s check <你改过的文件>" % p)
    print("⑤ 或一键全做: %s setup --repo <端仓> --target claude" % p)
    print()
    # G4：装出来的副本各自是哪一版 —— 升级前先看清"现在跑的是什么"
    install_matrix()
    return 0


def cmd_setup(a):
    """一键接入。**任一步失败必须让最终退出码非零**（评审报告 H2）。

    2026-09-23 实测两种形态：`setup --repo <存在的非 git 目录>` 里钩子安装失败
    （git `fatal: not a git repository`）、后续步骤照跑，**最终 exit 0**；`--repo <不存在>`
    直接以 NotADirectoryError 裸回溯崩掉。README/quickstart 把 setup 当一键入口，
    而 CI/agent 是按退出码判成败的 —— 那就是"提交期门禁其实没装上"却报成功。
    同文件里 `hooks install` / `skill install` 单跑时都正确回传 rc，只有此处被吞。
    """
    print("== guardrail-engine setup ==")
    rcs = []                       # 每步的 rc；非零的取第一个作为最终退出码（仍跑完剩余步骤）
    rc, out, err, gates = _gates()
    print("[1/4] 引擎自检: list-gates rc=%s gates=%d" % (rc, len(gates)))
    rcs.append(rc)
    if a.repo:
        print("[2/4] 安装提交期钩子 → %s" % a.repo)
        ns = argparse.Namespace(action="install", repo=a.repo, gate=a.gate)
        _rc = cmd_hooks(ns)
        rcs.append(_rc)
        if _rc != 0:
            print("[2/4] ✗ 钩子安装未成功（rc=%s）—— 提交期门禁**没有**装上。" % _rc)
    else:
        print("[2/4] 跳过钩子安装（未给 --repo；可后跑 hooks install）")
    if a.target:
        print("[3/4] 安装技能 → %s" % a.target)
        ns2 = argparse.Namespace(action="install", target=a.target, dir=None)
        _rc = cmd_skill(ns2)
        rcs.append(_rc)
        if _rc != 0:
            print("[3/4] ✗ 技能安装未成功（rc=%s）" % _rc)
    else:
        print("[3/4] 跳过技能安装（未给 --target）")
    print("[4/4] MCP 配置（复制到你宿主的 MCP 配置里）:")
    _print_mcp_config()
    # 退出码取**第一个非零**（不是最后一个）：报的是"最先坏在哪一步"，顺序信息不丢。
    bad = next((r for r in rcs if r), 0)
    if bad:
        print("\n== setup 完成，但**有步骤失败**（rc=%s）—— 上面的失败步骤没有生效，"
              "别按'已经接好'处理。==" % bad)
    return bad


# ---------------- 入口 ----------------

def build_parser():
    ap = argparse.ArgumentParser(prog="guardrail-engine",
                                 description="guardrail-engine 统一门禁薄壳 CLI")
    ap.add_argument("--version", action="version", version=_version())
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("version")
    p_where = sub.add_parser("where", help="打印 harness 工程根路径（指针解析结果）")
    p_where.add_argument("-v", "--verbose", action="store_true", help="显示解析来源与版本")
    sub.add_parser("list")
    sub.add_parser("quickstart")
    sub.add_parser("installed")

    p_init = sub.add_parser("init",
                            help="把随包引擎落到可写目录（默认 ~/.guardrail-engine/engine）并写指针")
    p_init.add_argument("--dir", help="安装目录（默认 ~/.guardrail-engine/engine）")
    p_init.add_argument("--force", action="store_true",
                        help="目标已存在且内容不同：旧目录**改名归档**后重装（不删除）")

    p_run = sub.add_parser("run")
    p_run.add_argument("gate")
    p_run.add_argument("files", nargs="*")
    p_run.add_argument("--all", action="store_true")
    p_run.add_argument("--mode")
    p_run.add_argument("--timeout", type=int,
                       help="子进程超时秒数（默认：全量 3600 / 定向 300）")

    p_check = sub.add_parser("check")
    p_check.add_argument("file")

    p_done = sub.add_parser("done", help="一步收尾：跑门禁 + 打一行可验证凭证（h 可对台账复核）")
    p_done.add_argument("--repo")
    p_done.add_argument("--only", action="append", default=[],
                        help="只检查指定文件（可重复；不传则用 auto-gate 的改动收集）")

    p_sum = sub.add_parser("summary")
    p_sum.add_argument("last", nargs="?", type=int)

    p_cov = sub.add_parser("coverage")
    p_cov.add_argument("--last", type=int)
    p_cov.add_argument("--json", action="store_true")

    sub.add_parser("doctor")

    p_log = sub.add_parser("log")
    p_log.add_argument("--last", type=int, default=50)
    p_log.add_argument("--gate", help="只看某个 gate 的记录（与 --last 组合）；窗口内无记录时 rc=1 + NO_RECORD，供 agent「任务完成前自查」机读")
    p_log.add_argument("--log")
    p_log.add_argument("--export")
    p_log.add_argument("--format", choices=["csv", "json"], default="csv")
    p_log.add_argument("--anchor", action="store_true",
                       help="追加锚点记录并打印入库命令（台账本体保持 git 忽略）")
    p_log.add_argument("--verify", action="store_true",
                       help="重算行内链并与最后一条锚点对账")
    p_log.add_argument("--engine", action="store_true",
                       help="与 --verify 合用：再核对台账 engine_sha 能否归因到已提交的引擎内容（Engine-Sha 尾注）")

    p_ops = sub.add_parser("ops")
    p_ops.add_argument("--last", type=int, default=50)

    p_mcp = sub.add_parser("mcp")
    # REMAINDER：转发器要把 `--selftest` / `--print-config` 原样交给 mcp.py。
    # `nargs="*"` 会把它们当成本层未知选项而 rc=2（RUNBOOK 里那条 `mcp --print-config` 就撞过）。
    p_mcp.add_argument("rest", nargs=argparse.REMAINDER)

    p_skill = sub.add_parser("skill")
    p_skill.add_argument("action", choices=["install", "status", "uninstall"])
    p_skill.add_argument("--target", choices=sorted(SKILL_PRESETS.keys()))
    p_skill.add_argument("--dir")

    p_hooks = sub.add_parser("hooks")
    p_hooks.add_argument("action", choices=["install", "uninstall", "status"])
    p_hooks.add_argument("--repo")
    p_hooks.add_argument("--gate")

    p_by = sub.add_parser("bypass")
    p_by.add_argument("--reason", required=False, default="")
    p_by.add_argument("--repo")

    p_setup = sub.add_parser("setup")
    p_setup.add_argument("--repo")
    p_setup.add_argument("--gate")
    p_setup.add_argument("--target", choices=sorted(SKILL_PRESETS.keys()))
    return ap


def _require_python(vi=None):
    """前置检查：本工程需要 **Python 3.11+** —— 转发公用件（原四个入口各一份逐字相同的实现）。

    参数 vi 仅供测试注入（默认取 sys.version_info）。
    """
    return _shell.require_python(vi)


def main(argv=None):
    _require_python()   # 前置检查（Python 3.11+）
    # 先解析再判 ROOT：`init` 的职责就是把「没有引擎根」变成「有」，它必须在守卫之前跑；
    # `version` / `where` 也必须能在未初始化时回答"我是谁"（--version 走 argparse，本来就与 ROOT 无关）。
    ap = build_parser()
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    if a.cmd == "init":
        return cmd_init(a)
    if not ROOT:
        if a.cmd == "version":
            return cmd_version(a)
        if a.cmd == "where":
            return cmd_where(a)
        if _shell.packaged_engine():
            print("✗ 引擎尚未初始化：本包自带引擎，但还没落到可写目录（台账不能写进 site-packages）。")
            print("  先跑：%s init" % _cmd_prefix())
            print("  然后：%s quickstart" % _cmd_prefix())
        else:
            print("guardrail-engine root not found"
                  "（设 GUARDRAIL_ENGINE_DIR 或把本文件保留在 <root>/guardrail_engine/ 下）")
        return 2
    if not a.cmd:
        return cmd_quickstart(a)
    fn = {
        "version": cmd_version, "where": cmd_where, "list": cmd_list, "quickstart": cmd_quickstart,
        "installed": cmd_installed, "init": cmd_init,
        "run": cmd_run, "check": cmd_check, "done": cmd_done,
        "summary": cmd_summary, "coverage": cmd_coverage,
        "doctor": cmd_doctor, "log": cmd_log, "ops": cmd_ops, "mcp": cmd_mcp,
        "skill": cmd_skill, "hooks": cmd_hooks, "bypass": cmd_bypass, "setup": cmd_setup,
    }[a.cmd]
    return fn(a)


if __name__ == "__main__":
    sys.exit(main())
