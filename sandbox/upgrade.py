#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键升级：拿**新包**把本机装过的那几路副本逐项刷新（默认只预演）。

为什么会有这个脚本（升级链路剖析报告 G2）
==========================================
升级面原先是一条**纯人工的对照表**：同事得自己判断装过哪几路、各跑哪条命令、哪几路不用动。
而机器上唯一能被一键跑完的整机级脚本是 `uninstall.py` —— 它看起来最彻底。于是"不确定的人"
会选那条路，而它**撤回一切**：卸载再装不仅有中间空窗，还会让 MCP / 钩子在这段时间里整体失效。
补这个脚本是为了让"刷新"也有一条一键可跑、且**不做任何撤回动作**的路。

原则（与 uninstall.py 同源）
============================
  · **不做任何撤回、不删任何文件**：没有 rmtree / os.remove / plugin uninstall / marketplace remove。
    旧文件不再需要时只**列出来**交给人判断（本仓纪律：能自动做的自动做，不该自动的不许猜）。
  · **升级 ≠ 新增通道**：只刷新**已经装过**的那几路；没装的不替你装（那是「安装」，见《安装说明》二）。
  · 默认预演，`--yes` 才动手；每一步都留痕（运维台账）；够不着的打印现成命令。
  · 与目录布局无关：无论同事把包解压到"每版一个新目录"还是"固定目录"，本脚本都走
    `marketplace add <本包>/marketplace`（实测：同名市场会被**静默改指**到新路径，无需先 remove）。

升级七步（前一步不过，后面不做）
================================
  [1/7] 路径适配（**必须第一步**，且不可跳过）
        新包里的 `.mcp.json` 是占位符（`__PY__` / `__MCP__` / `__HARNESS__`），
        由包内 `fix_paths.py` 现场填本机路径。**每一步通道刷新都建立在它之上**：
        漏跑 = 装出来的副本指向不存在的解释器，MCP 起不来、编辑期门禁整体无声失效。
  [2/7] Claude Code 插件：`marketplace add <本包>/marketplace` → `plugin update`
  [3/7] OpenClaw 扩展：就地把新 bundle 覆盖进 `extensions/agentguard`（重启桌面端生效）
  [4/7] 人用 CLI（pip 包）：**只在装过时** `pip install -U <新轮子>`，装完对账产物是否真的换了
  [5/7] 引擎副本：**只刷新 init 复制出来的那份**（默认 `~/.agentguard/engine`），
        源是本包轮子自带的引擎；仓库（引擎源）一律不碰
  [6/7] 技能副本：转发 `<HARNESS>/agentguard/cli.py skill install --dir <宿主技能目录>`
  [7/7] 编辑期 shim：**只在装过时**刷新（`<HARNESS>/sandbox/install_shim.py`）

用法（默认只预演，不改任何东西；确认无误后加 --yes 执行）
==========================================================
    python upgrade.py --harness <你的 agentguard 路径>                  # 预演
    python upgrade.py --harness <...> --yes                                  # 执行
    python upgrade.py --harness <...> --yes --skip pip,openclaw              # 想保留某几项时
    python upgrade.py --harness <...> --pack <包目录>                         # 包不在本脚本所在目录时

退出码：0 = 完成 / 本来就没有可刷新的；1 = 有未完成项或参数问题（见输出）。
"""
import argparse
import glob
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SKILL_NAME = "agentguard"
PLUGIN_NAME = "agentguard"
PLUGIN_FULL = "agentguard@agentguard-tools"
MARKETPLACE_NAME = "agentguard-tools"
# 与 agentguard/cli.py 的 SKILL_PRESETS 保持一致（壳层常量镜像，别单边改）
SKILL_PRESETS = {
    "claude": os.path.join(os.path.expanduser("~"), ".claude", "skills"),
    "opencode": os.path.join(os.path.expanduser("~"), ".config", "opencode", "skills"),
    "codebuddy": os.path.join(os.path.expanduser("~"), ".codebuddy", "skills"),
}
SKIP_KEYS = ("claude", "openclaw", "pip", "engine", "skills", "shim")
# 与 agentguard/cli.py 的 INIT_MARKER / INIT_MARKER_KIND 逐字相同（壳层常量镜像，别单边改）。
# 本脚本随包分发、可能在没有装 agentguard 的机器上跑，不能 import 那个包。
INIT_MARKER = ".agentguard-init.json"
INIT_MARKER_KIND = "agentguard-init"
# 装机时由 fix_paths.py 现场填充的三个字段（与安装说明、verify_release.py 同一份定义）
FILLED_FIELDS = (("command", "__PY__"), ("args[0]", "__MCP__"),
                 ("env.AGENTGUARD_DIR", "__HARNESS__"))
# 内容对账时**排除**的文件：`.mcp.json` 本来就是"每台机器一份"（fix_paths 现场填），
# 把它算进去会让"换了台机器"表现成"内容不同"—— 那是假警报，不是发现。
HASH_SKIP = (".mcp.json",)


def norm(p):
    return os.path.abspath(p).replace(os.sep, "/")


def stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def _decode(b):
    if b is None:
        return ""
    if isinstance(b, str):
        return b
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("gb18030", "replace")


def run_cmd(args, timeout=600):
    """跑一个外部命令；返回 (rc, 合并输出)。与 uninstall.py 同实现（`.cmd/.bat` **直跑**）。

    2026-09-23（评审 L14）：原先对 `.cmd/.bat` 套一层 `cmd /c`，理由写的是"subprocess 不经 shell
    执行不到"。**这句实测是假的**：直接调 kernel32 的 `CreateProcessW`（绕开 subprocess）也能跑
    `.cmd` ⇒ 是**操作系统本人**认它，不需要谁替它转交（Python 文档同此："batch files may be
    launched by the operating system in a system shell"）。而那层 cmd 会在**路径与参数都含空格**时
    按 cmd 自己的规则再剥一遍引号（`cmd /c "<含空格路径>" "<含空格参数>"`）⇒ 引号错位、rc=1 且输出为空。
    判据与 `sandbox/check_contracts.py::_client_version`、`agentguard/mcp.py::run_cmd` 同源；
    锁见 `tests/test_review_2026_09_23.py::TestL14BatchFileIsNotHandedToAShell`。
    """
    exe = shutil.which(args[0]) or args[0]
    cmd = [exe] + list(args[1:])
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.returncode, (_decode(p.stdout) + _decode(p.stderr))
    except Exception as ex:
        return 127, "无法执行 %s：%r" % (args[0], ex)


def record(a, step, result, reason=""):
    """运维台账：与 cli.py / uninstall.py 同文件同风格（entry=upgrade）；失败提示但不阻断。"""
    p = os.environ.get("AGENTGUARD_OPS_LOG") or (
        os.path.join(a.harness_norm, "reports", "ops_log.jsonl") if a.harness_norm else "")
    if not p:
        return
    try:
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "entry": "upgrade",
               "op": "machine_upgrade", "step": step, "result": result, "reason": reason}
        with io.open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as ex:
        print("  [ops] 台账写入失败（不阻断）：%r" % ex)


def indented(text, pad="      "):
    return "\n".join(pad + ln for ln in (text or "").strip().splitlines() if ln.strip())


# ---- 读本机现状（用于"升级前后对照"）------------------------------------------------

def read_matrix(a):
    """转发引擎仓的 `cli.py installed`（G4 的单一实现）→ (rc, 输出)。

    为什么转发而不是自己算一遍：本机安装矩阵有四个读法（引擎 VERSION / 包元数据 /
    宿主插件登记 / 技能目录），各写一遍必然漂 —— 与 uninstall.py 转发 cli.py 同一条理由。
    引擎仓太旧、还不认识 `installed` 时 rc != 0，调用方只提示、不当失败：升级本身不该被
    一个"看得更清楚的辅助输出"挡住。
    """
    if not a.harness_cli:
        return 127, ""
    return run_cmd([a.python, a.harness_cli, "installed"])


# ---- [2/7] Claude Code 插件 ---------------------------------------------------------

def _claude_home():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def installed_plugin_record():
    """从宿主登记里取本插件的记录 → (installPath, version)；没有则 (None, None)。"""
    p = os.path.join(_claude_home(), "plugins", "installed_plugins.json")
    try:
        with io.open(p, encoding="utf-8", errors="replace") as f:
            plugs = (json.loads(f.read()).get("plugins") or {})
    except (OSError, ValueError):
        return None, None
    recs = plugs.get(PLUGIN_FULL) or []
    for r in recs:
        if isinstance(r, dict) and r.get("installPath"):
            return r["installPath"], r.get("version")
    return None, None


def manifest_version(plugin_dir):
    """读插件清单里的 version（这份文件就是"这次升级会不会发生"的开关）。"""
    p = os.path.join(plugin_dir, ".claude-plugin", "plugin.json")
    try:
        with io.open(p, encoding="utf-8", errors="replace") as f:
            return json.loads(f.read()).get("version") or "?"
    except (OSError, ValueError):
        return None


def tree_hash(root):
    """目录内容指纹：`<相对路径> <单文件 sha1>` 排序后取 sha1（排除 HASH_SKIP）。

    为什么这不是"改了个无关紧要的东西也报警"：排除掉的只有 `.mcp.json`（唯一按机器变的文件），
    剩下的都是随包出货的正文。判据用在"版本号相同、内容却不同"这个死角上 ——
    那正是 `plugin update` 会打印绿色「已是最新」然后**什么都不换**的情形。
    """
    lines = []
    for dp, dns, fns in os.walk(root):
        dns.sort()
        for fn in sorted(fns):
            fp = os.path.join(dp, fn)
            rel = os.path.relpath(fp, root).replace("\\", "/")
            if rel in HASH_SKIP:
                continue
            h = hashlib.sha1()
            try:
                with open(fp, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
            except OSError:
                continue
            lines.append("%s  %s" % (h.hexdigest(), rel))
    lines.sort(key=lambda x: x.split("  ", 1)[1])
    return hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()[:12], len(lines)


def _mcp_fields(mj):
    """读 `.mcp.json` 里那三个机器相关字段的现值（读不到返回 None）。"""
    try:
        with io.open(mj, encoding="utf-8", errors="replace") as f:
            data = json.loads(f.read())
        srv = (data.get("mcpServers") or {}).get(PLUGIN_NAME) or {}
        return (srv.get("command"),
                (srv.get("args") or [None])[0],
                (srv.get("env") or {}).get("AGENTGUARD_DIR"))
    except (OSError, ValueError, AttributeError):
        return None


def check_installed_mcp(plugin_dir, harness, python):
    """查**装出来的副本**里那份 `.mcp.json` 能不能用 → [(级别, 一句话)]，级别为 bad / note。

    为什么值得单独查：包出货时留的是占位符，靠装机那一步的 `fix_paths.py` 填。装完之后
    真正被 MCP 客户端读的是**副本里**这一份 —— 漏跑那一步时它是 `__PY__` 字面值，
    服务起不来、编辑期门禁**整体无声失效**，而当时没有任何一步会报错。这里把它变成一句人话。

    判据刻意只认**硬事实**（占位符 / 路径在本机不存在），不认"它和我这次用的解释器不一样"：
    同事的副本指向另一个本机可用解释器（例：宿主自带的 python）完全能跑，
    把那种情形报成 ⚠ 会让这条提示迅速贬值 —— 假警报比漏报更磨损可信度（本仓纪律）。
    """
    mj = os.path.join(plugin_dir, ".mcp.json")
    if not os.path.isfile(mj):
        return [("bad", "副本里没有 .mcp.json（插件不完整？）")]
    got = _mcp_fields(mj)
    if got is None:
        return [("bad", "副本里的 .mcp.json 读不出来（不是合法 JSON？）")]
    want = (norm(python), norm(os.path.join(harness, "agentguard", "mcp.py")), norm(harness))
    exists = (os.path.isfile, os.path.isfile, os.path.isdir)
    out = []
    for (label, ph), cur, exp, ok in zip(FILLED_FIELDS, got, want, exists):
        if cur == ph:
            out.append(("bad", "%s 还是占位符 %s —— 装机时漏跑 fix_paths.py，MCP 起不来" % (label, ph)))
        elif not cur:
            out.append(("bad", "%s 是空的" % label))
        elif not ok(str(cur)):
            out.append(("bad", "%s 指向本机不存在的位置：%s（MCP 起不来）" % (label, cur)))
        elif norm(str(cur)) != exp:
            out.append(("note", "%s 用的是 %s（本机存在、能跑；与本次升级的解释器不同而已）"
                        % (label, cur)))
    return out


def step_claude(a):
    """[2/7] Claude 插件：marketplace add（同名会静默改指到新路径）→ plugin update。"""
    fails = []
    mkt = os.path.join(a.pack, "marketplace")
    bundle = os.path.join(mkt, "plugins", PLUGIN_NAME)
    if not os.path.isdir(bundle):
        print("  · ✗ 本包里没有市场：%s（--pack 指错目录了？）" % norm(mkt))
        return ["本包缺 marketplace（不是安装包目录？）"]
    want_ver = manifest_version(bundle)
    want_hash, want_n = tree_hash(bundle)
    inst_dir, inst_ver = installed_plugin_record()
    had_hash = tree_hash(inst_dir)[0] if inst_dir and os.path.isdir(inst_dir) else None
    cli = a.claude or shutil.which("claude")
    # `--claude` 既是给"宿主 CLI 不在 PATH 上"的人用的，也是测试的注入点（不许去动真机配置）
    if not cli:
        cli = "claude"
    print("  · 本包      ：版本 %s  内容指纹 %s（%d 个文件）" % (want_ver, want_hash, want_n))
    print("  · 本机现有  ：%s" % (("版本 %s  内容指纹 %s" % (inst_ver, had_hash))
                                  if had_hash else "未装（升级不替你新增通道；要装见《安装说明》二）"))
    if inst_dir:
        for level, line in check_installed_mcp(inst_dir, a.harness_norm, a.python):
            print("      %s %s" % ("⚠" if level == "bad" else "·", line))
    cmds = [[cli, "plugin", "marketplace", "add", mkt],
            [cli, "plugin", "update", PLUGIN_FULL]]
    if not shutil.which(cli) and not os.path.isfile(cli):
        print("  · 未检测到 claude CLI：请手动执行（或先在装了宿主 CLI 的机器上跑本脚本）")
        for c in cmds:
            print("      %s" % " ".join(c))
        record(a, "claude-plugin", "manual", "no claude CLI")
        return fails
    if not a.yes:
        for c in cmds:
            print("  · 将执行：%s" % " ".join(c))
        return fails
    for c in cmds:
        rc, out = run_cmd(c, timeout=300)
        tail = " ｜ ".join(ln.strip() for ln in out.strip().splitlines()[-2:] if ln.strip())
        print("  · %s → rc=%s%s" % (" ".join(c), rc, ("：" + tail[:160]) if tail else ""))
        record(a, "claude-plugin", "ok" if rc == 0 else "warn", " ".join(c[:3]))
    new_dir, new_ver = installed_plugin_record()
    new_hash = tree_hash(new_dir)[0] if new_dir and os.path.isdir(new_dir) else None
    print("  · 升级后    ：版本 %s  内容指纹 %s" % (new_ver, new_hash))
    if new_dir and os.path.isdir(new_dir):
        # 升级**之后**这份检查才算数：副本里那份 .mcp.json 是不是本机路径，只有装完才知道
        for level, line in check_installed_mcp(new_dir, a.harness_norm, a.python):
            print("      %s %s" % ("⚠" if level == "bad" else "·", line))
            if level == "bad":
                fails.append("Claude 插件副本不可用：%s" % line)
    if new_hash and want_hash and new_hash != want_hash:
        # 只有"目标内容确实不同"时才算问题：同版本重建的包也可能内容一致（那才是真没事）
        print("      ⚠ 副本内容与本包**仍不一致**")
        if new_ver == want_ver:
            print("        版本号相同（%s）→ `plugin update` 判了「已是最新」而**什么都没换**；" % want_ver)
        print("        处置：把 VERSION 升到新版本号后重新打包（`python sandbox/pack_release.py`），")
        print("              再用新包跑一次本脚本。现在这台机器上跑的仍是旧内容。")
        fails.append("Claude 插件内容未刷新（版本号没变 → plugin update 不会搬运内容）")
        record(a, "claude-plugin", "stale", "version=%s hash %s->%s" % (new_ver, had_hash, new_hash))
    elif new_hash:
        print("      ✓ 副本内容与本包一致 —— 这台机器上跑的就是本包这一版。")
    return fails


# ---- [3/7] OpenClaw 扩展 ------------------------------------------------------------

def step_openclaw(a):
    """[3/7] 就地覆盖 `extensions/agentguard`（**不删**；旧文件只列出来给人判断）。"""
    fails = []
    state = os.path.abspath(a.openclaw_state or os.environ.get("OPENCLAW_STATE_DIR")
                            or os.path.join(os.path.expanduser("~"), ".openclaw-autoclaw"))
    ext = os.path.join(state, "extensions", PLUGIN_NAME)
    if not os.path.isdir(ext):
        print("  · %s 下未发现 agentguard 扩展（升级不替你新增通道）" % norm(state))
        return fails
    markers = (os.path.join(ext, ".claude-plugin", "plugin.json"), os.path.join(ext, ".mcp.json"))
    if not any(os.path.isfile(m) for m in markers):
        print("  · ⚠️ 目录存在但缺少 agentguard 特征文件，拒绝自动覆盖（防误伤同名目录）：")
        print("      %s" % norm(ext))
        fails.append("OpenClaw 扩展需人工确认：%s" % norm(ext))
        return fails
    src = os.path.join(a.pack, "marketplace", "plugins", PLUGIN_NAME)
    if not os.path.isdir(src):
        print("  · ✗ 本包里没有 bundle：%s" % norm(src))
        return ["本包缺 marketplace/plugins/agentguard"]
    extra = []
    for dp, _dns, fns in os.walk(ext):
        for fn in fns:
            rel = os.path.relpath(os.path.join(dp, fn), ext).replace("\\", "/")
            if not os.path.isfile(os.path.join(src, rel)):
                extra.append(rel)
    if not a.yes:
        print("  · 将就地把新 bundle 覆盖进：%s" % norm(ext))
        print("                    ← %s（重启桌面端生效）" % norm(src))
        if extra:
            print("      注意：扩展目录里这些文件新包里没有，覆盖**不会删**它们：%s" % ", ".join(sorted(extra)))
        return fails
    bad = []
    for dp, _dns, fns in os.walk(src):
        rel = os.path.relpath(dp, src)
        tdir = os.path.join(ext, rel) if rel != "." else ext
        try:
            os.makedirs(tdir, exist_ok=True)
        except OSError as ex:
            print("  · ✗ 建目录失败：%r" % ex)
            record(a, "openclaw", "fail", repr(ex))
            return ["OpenClaw 扩展覆盖失败"]
        for fn in fns:
            try:
                shutil.copy2(os.path.join(dp, fn), os.path.join(tdir, fn))
            except OSError as ex:
                bad.append("%s：%r" % (fn, ex))
    if bad:
        print("  · ✗ 有文件没覆盖成功（桌面端可能正占用%s）" % ("；退出桌面端后重跑" if os.name == "nt" else ""))
        for b in bad[:5]:
            print("      %s" % b)
        fails.append("OpenClaw 扩展覆盖失败")
        record(a, "openclaw", "fail", "; ".join(bad)[:200])
    else:
        print("  · 已覆盖 → %s" % norm(ext))
        print("      重启 OpenClaw 桌面端后生效。")
        if extra:
            print("      注意：这些旧文件新包里没有，本脚本**不删**（确认无用请自行清理）：%s"
                  % ", ".join(sorted(extra)))
        record(a, "openclaw", "ok", "%d extra kept" % len(extra))
    return fails


# ---- [4/7] 人用 CLI（pip 包）---------------------------------------------------------
# 为什么这一格的判据是"装完对账产物"而不是"看 pip 的退出码"（2026-09-17 实测，隔离 venv）：
#   `pip install -U <轮子>` 在**同版本号**下是 rc=0 + **一个字节都不换** —— pip 的原话是
#   「is already installed with the same version as the provided wheel. Use --force-reinstall
#   to force an installation of the wheel.」。也就是说：同版本号、内容不同的重打包包，
#   在这条通道上"升"完了、rc=0、什么也没发生。这正是 G3 在插件通道修掉的那个形状，
#   而本格当时只留了一句**挂在 `rc != 0` 分支上**的提示 —— 同版本时 rc 本来就是 0，
#   那句提示永远不会执行。所以改成与插件通道同一套判据：**跑完去读产物本身**
#   （site-packages 里 `agentguard/**.py` 逐字节比轮子里的同名文件），一致才算刷新成功。
#   刻意不比版本号大小：`0.1.2-dev`（VERSION 文件）与 `0.1.2.dev0`（包元数据）本就不是同一个
#   字符串，为比大小再抄一份 PEP440 归一化进来，只会多出一处要跟着同步的东西。

def wheel_pkg_files(wheel):
    """轮子里 `agentguard/**.py` → {路径: sha1}；读不出来返回 None（不是空字典）。"""
    out = {}
    try:
        with zipfile.ZipFile(wheel) as z:
            for n in z.namelist():
                if n.startswith("agentguard/") and n.endswith(".py"):
                    out[n] = hashlib.sha1(z.read(n)).hexdigest()
    except Exception:
        return None
    return out


def installed_pkg_files(location):
    """site-packages 里 `agentguard/**.py` → {路径: sha1}；目录不在/读不了返回 None。

    None 与 {} 必须分开：前者是"读不到，无法对账"，后者是"读到了、包是空的"。
    混成一个值，这条对账就会在一次 OSError 之后静默变成"一致"。
    """
    if not location:
        return None
    root = os.path.join(location, "agentguard")
    if not os.path.isdir(root):
        return None
    out = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d != "__pycache__"]
        for fn in sorted(fns):
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dp, fn)
            h = hashlib.sha1()
            try:
                with open(fp, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
            except OSError:
                return None
            out["agentguard/" + os.path.relpath(fp, root).replace("\\", "/")] = h.hexdigest()
    return out


def files_fingerprint(files):
    """{路径: sha1} → 12 位短指纹（只为打印出来给人对比，不参与判据）。"""
    lines = ["%s  %s" % (h, n) for n, h in sorted(files.items())]
    return hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()[:12]


def wheel_version(path):
    """从轮子文件名取版本段（`<名>-<版本>-<py>-<abi>-<平台>.whl`）；取不到返回 ""。"""
    base = os.path.basename(path)
    parts = base[:-4].split("-") if base.endswith(".whl") else []
    return parts[1] if len(parts) >= 5 else ""


def pip_show_fields(text):
    """`pip show` 的输出 → (版本, 安装位置)；字段缺失是空串（调用方自己决定怎么办）。"""
    ver = loc = ""
    for ln in (text or "").splitlines():
        if ln.startswith("Version:"):
            ver = ln.split(":", 1)[1].strip()
        elif ln.startswith("Location:"):
            loc = ln.split(":", 1)[1].strip()
    return ver, loc


def pack_wheel(a):
    """包内唯一的轮子 → (路径, 错误说明)。pip 与引擎副本两格共用同一个来源。

    引擎副本的刷新源**不能**是 site-packages 里已装的那份：`--skip pip`（或本机没装过 pip）
    时它还是旧版，会把旧引擎原样再铺一遍。轮子是本包的出货件，与 `pip install` 搬运的是
    同一批字节。
    """
    wheels = sorted(glob.glob(os.path.join(a.pack, "*.whl")))
    if len(wheels) != 1:
        return None, "包内轮子数量异常（%d 个）：%s" % (
            len(wheels), [os.path.basename(w) for w in wheels])
    return wheels[0], ""


def pip_install(a, wheel, flag):
    """跑一次 pip 装包 → (rc, 合并输出, 结尾两行摘要)。`-U` 与 `--force-reinstall` 共用。

    抽出来是因为同版本号那条路要跑**两条**命令（先 `-U`，对账没过再代跑 `--force-reinstall`）：
    两条必须共用同一套超时与输出解码，否则第二次的报错呈现会与第一次不一样 ——
    而那正是"同版本号"这个场景里唯一看得见的东西。
    """
    rc, out = run_cmd([a.python, "-m", "pip", "install", flag, wheel], timeout=900)
    tail = " ｜ ".join(ln.strip() for ln in out.strip().splitlines()[-2:] if ln.strip())
    return rc, out, tail


def step_pip(a):
    """[4/7] pip 包：**只在装过时** `pip install -U <新轮子>`，装完对账产物是否真的换了。

    退出语义与插件通道同源：**只有"产物与本包逐字节一致"才算刷新成功**。
    rc=0 不是判据（同版本号时 pip 就是 rc=0 且什么都不做）。

    对账没过时**接着代跑一次 `--force-reinstall`** 再对一次账（用户 2026-09-17 拍板）。
    理由：实测同版本号下它确实能换内容，而"打印一句提示让人自己去跑"在多台机器上等于没人跑。
    代跑只有这一条命令、不递归重试 —— 装/卸之外一律由人确认这条边界仍然成立。
    """
    fails = []
    rc, out = run_cmd([a.python, "-m", "pip", "show", "agentguard"])
    if rc != 0:
        print("  · pip 未见 agentguard（未装过 → 升级不动它；只装 CLI 见《安装说明》一）")
        return fails
    ver, loc = pip_show_fields(out)
    wheel, err = pack_wheel(a)
    if not wheel:
        print("  · ✗ %s" % err)
        fails.append("包内轮子数量异常（期望 1 个）")
        return fails
    want = wheel_pkg_files(wheel)
    if want is None:
        print("  · ✗ 读不出轮子内容（包坏了？）：%s" % norm(wheel))
        fails.append("轮子读不出来")
        return fails
    had = installed_pkg_files(loc)
    print("  · 本机现有  ：版本 %s  内容指纹 %s" % (ver or "?", files_fingerprint(had) if had is not None else "读不到"))
    print("  · 本包      ：版本 %s  内容指纹 %s（%d 个文件）"
          % (wheel_version(wheel) or "?", files_fingerprint(want), len(want)))
    if not a.yes:
        print("  · 将执行：%s -m pip install -U %s" % (a.python, norm(wheel)))
        if had is not None and had == want:
            print("      预判：本机这份**已经与本包一致** —— pip 会照常报成功（那不算「没升」）")
        elif had is not None and ver and ver == wheel_version(wheel):
            print("      预判：两边版本号相同（%s）→ pip 会**拒绝换内容**（rc 仍是 0、什么都不做）；" % ver)
            print("            届时本脚本**接着代跑** %s -m pip install --force-reinstall <本包轮子>" % a.python)
            print("            （实测同版本号下它能换内容；连它也换不动才算真失败。）")
        return fails
    rc, _out, tail = pip_install(a, wheel, "-U")
    print("  · pip install -U → rc=%s%s" % (rc, ("：" + tail[:200]) if tail else ""))
    if rc != 0:
        print("      提示：pip 升级失败与「同版本号」无关（那种情况 rc 是 0）—— 先看上面的报错原文：")
        print("            解释器选错 / 权限不足 / 轮子坏了都会落到这里。")
        fails.append("pip 升级失败（解释器：%s）" % a.python)
        record(a, "pip", "fail", a.python)
        return fails
    # 升级**之后**这份对账才算数 —— 与插件通道同一条纪律：rc 不算数，产物才算数
    now = installed_pkg_files(loc)
    if now is None:
        print("  · ✗ 读不到安装位置里的包内容（Location=%r）→ **无法确认这次到底换没换**。" % loc)
        print("      （`pip install -e .` 这类可编辑安装就是这种：登记在、包却不在 Location 下）")
        print("      处置：%s -m pip show -f agentguard 与包内轮子手工对一遍。" % a.python)
        fails.append("pip 装完读不到产物（无法确认是否刷新）")
        record(a, "pip", "unverified", norm(loc))
        return fails
    if now == want:
        print("      ✓ 本机这份与本包逐字节一致（%d 个文件）—— 这台机器上跑的确实是本包这一版。"
              % len(now))
        record(a, "pip", "ok", a.python)
        return fails
    print("      ⚠ 本机这份与本包**仍不一致**（本包 %d 个文件 / 本机 %d 个）" % (len(want), len(now)))
    if ver and ver == wheel_version(wheel):
        print("        版本号相同（%s）→ pip 判了「already installed with the same version as the" % ver)
        print("        provided wheel」：**rc 仍是 0、内容一个字都没换**（2026-09-17 隔离 venv 实测）。")
    # 代跑 --force-reinstall（用户 2026-09-17 拍板）：实测它能在同版本号下换内容，而"给句提示
    # 让人自己去跑"在多台机器上等于没人跑。**只补这一条命令**、不递归重试：装/卸之外一律由人
    # 确认这条边界仍然成立 —— 这里多出来的动作，全部内容就是"把本包的轮子完整铺一遍"。
    # 它同时会强制重装依赖，但本包按合同**零依赖**（tests/test_stdlib_only.py 守着），
    # 所以这条命令碰不到本包之外的任何东西。
    print("        → 接着**代跑**：%s -m pip install --force-reinstall <本包轮子>" % a.python)
    rc2, _o2, tail2 = pip_install(a, wheel, "--force-reinstall")
    print("        pip install --force-reinstall → rc=%s%s" % (rc2, ("：" + tail2[:200]) if tail2 else ""))
    if rc2 == 0:
        # 代跑**之后**这份对账才算数 —— 与第一次同一条纪律：rc 不算数，产物才算数
        after = installed_pkg_files(loc)
        if after is None:
            print("        ✗ 代跑之后仍读不到安装位置里的包内容（Location=%r）→ **无法确认到底换没换**。" % loc)
            print("          处置：%s -m pip show -f agentguard 与包内轮子手工对一遍。" % a.python)
            fails.append("pip 代跑 --force-reinstall 后读不到产物（无法确认是否刷新）")
            record(a, "pip", "unverified", norm(loc))
            return fails
        if after == want:
            print("        ✓ 代跑之后与本包逐字节一致（%d 个文件）—— 这台机器上现在跑的确实是本包这一版。"
                  % len(after))
            record(a, "pip", "ok", "同版本 no-op → 已代跑 --force-reinstall；%s" % a.python)
            return fails
        now = after
    else:
        print("        （代跑本身 rc=%s 就失败了 —— 根因在上面的原文里，不是「同版本号」这条）" % rc2)
    print("        ✗ 代跑 --force-reinstall 之后，本机这份**仍与本包不同**。")
    print("          处置（二选一）：")
    print("            · 手工再跑一次看完整原文：%s -m pip install --force-reinstall \"%s\""
          % (a.python, norm(wheel)))
    print("            · 走常规升级：在 <HARNESS>/VERSION 升号后重新打包，再用新包跑本脚本。")
    print("          现在这台机器上跑的仍是旧内容。")
    fails.append("pip 包内容未刷新（同版本号时 pip 不搬运内容；代跑 --force-reinstall 后仍不一致）")
    record(a, "pip", "stale", "ver=%s hash %s->%s%s"
           % (ver, files_fingerprint(had) if had is not None else "?", files_fingerprint(now),
              "" if rc2 == 0 else " force-reinstall rc=%s" % rc2))
    return fails


# ---- [5/7] 引擎副本（agentguard init 复制出来的引擎根）-------------------------------
# 为什么这一格必须存在：`agentguard init` 把引擎复制到可写目录（默认 ~/.agentguard/engine），
# MCP / 钩子 / shim 全指向它。pip 那一路刷新的是 site-packages 里的壳，**不会**动这份副本；
# 漏刷的表现是「CLI 是新的、门禁跑的还是旧规则」—— 没有任何一步会报错。
#
# 为什么不直接对 --harness 跑 init：--harness 也可能是用户自己的仓库（引擎源）。对仓库跑
# `init --force` 会把它改名归档再复制一份 —— 那是把源搬走，不是升级。所以只认 init 写下的
# 所有权标记（`.agentguard-init.json`）；旧版 init 没有标记，只对默认路径且**没有 .git**
# 的目录做兼容识别。宁可不刷，也不猜。

def engine_version(root):
    try:
        with io.open(os.path.join(root, "VERSION"), encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def init_marker(root):
    """读 init 的所有权标记 → dict；不是 init 副本返回 None。"""
    try:
        with io.open(os.path.join(root, INIT_MARKER), encoding="utf-8", errors="replace") as f:
            data = json.loads(f.read())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("kind") == INIT_MARKER_KIND else None


def looks_like_engine(root):
    return os.path.isfile(os.path.join(root, "harness", "run_gate.py"))


def engine_pointer():
    """`~/.agentguard/harness.path` 的内容（也可能指向仓库；所有权由调用方再判）。"""
    p = os.path.join(os.path.expanduser("~"), ".agentguard", "harness.path")
    try:
        with io.open(p, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def engine_copy_targets(a):
    """要刷新的 init 副本 → [(路径, 来源说明, 识别依据)]；仓库与无法确认的目录一律不碰。

    候选只有三个**有出处**的位置：--harness、指针文件、默认路径。判据是 init 写下的标记；
    旧版 init 没有标记，只对默认路径且没有 `.git` 的目录做兼容识别 —— 这三条都是"宁可
    少刷一份"的方向：`init --force` 会把目标目录改名归档，对用户自己的仓库就是搬走源。
    """
    default = os.path.abspath(os.path.join(os.path.expanduser("~"), ".agentguard", "engine"))
    cands = []

    def add(p, label):
        if not p:
            return
        p = os.path.abspath(os.path.expanduser(p))
        if all(norm(p) != norm(q) for q, _ in cands):
            cands.append((p, label))

    add(a.harness_norm, "--harness 指向的引擎")
    add(engine_pointer(), "指针指向的引擎")
    add(default, "默认引擎目录")

    out = []
    for path, label in cands:
        if not os.path.isdir(path) or not looks_like_engine(path):
            continue
        if init_marker(path):
            out.append((path, label, ""))
        elif norm(path) == norm(default) and not os.path.exists(os.path.join(path, ".git")):
            out.append((path, label,
                        "旧版 init 没有标记；按「默认路径 + 没有 .git」识别"))
    return out


def _inside(path, root):
    p = os.path.normcase(os.path.abspath(path)).rstrip(os.sep)
    r = os.path.normcase(os.path.abspath(root)).rstrip(os.sep)
    return p == r or p.startswith(r + os.sep)


def extract_wheel_cli(wheel, root):
    """把轮子里的 `agentguard/` 解到 root → (init 用的 cli.py, 错误说明)。

    为什么解压而不是 `python -m agentguard.cli`：后者用的是**已装**的那份；`--skip pip`
    或本机没装过 pip 时它是旧版，会把旧引擎再铺一遍。解压的是本包这只轮子 —— 与
    `pip install` 搬运的是同一批字节，来源不随本机的安装状态而变。
    """
    try:
        with zipfile.ZipFile(wheel) as z:
            for n in z.namelist():
                if not n.startswith("agentguard/") or n.endswith("/"):
                    continue
                target = os.path.join(root, n.replace("/", os.sep))
                if not _inside(target, root):
                    return None, "轮子条目越界：%s" % n
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(n) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    except Exception as ex:
        return None, "解压轮子失败：%r" % (ex,)
    cli = os.path.join(root, "agentguard", "cli.py")
    if not os.path.isfile(cli):
        return None, "轮子里没有 agentguard/cli.py"
    if not os.path.isfile(os.path.join(root, "agentguard", "_engine", "harness", "run_gate.py")):
        return None, "轮子未自带引擎（旧版包？）—— 无法刷新引擎副本"
    return cli, ""


def wheel_has_engine(wheel):
    """轮子是否自带 `agentguard/_engine/`（旧包没有；那种包刷不了引擎副本）。"""
    try:
        with zipfile.ZipFile(wheel) as z:
            return "agentguard/_engine/harness/run_gate.py" in z.namelist()
    except Exception:
        return False


def wheel_engine_files(wheel):
    """轮子 → 引擎副本应有的 {相对路径: sha1}；读不出来返回 None（不是空字典）。

    两段来源正是 init 的复制清单：`agentguard/_engine/**`（铺到引擎根）与
    `agentguard/*.py`（壳文件，铺到 `<root>/agentguard/`）。
    """
    out = {}
    try:
        with zipfile.ZipFile(wheel) as z:
            for n in z.namelist():
                if n.endswith("/"):
                    continue
                if n.startswith("agentguard/_engine/"):
                    rel = n[len("agentguard/_engine/"):]
                elif n.startswith("agentguard/") and n.count("/") == 1 and n.endswith(".py"):
                    rel = n
                else:
                    continue
                out[rel] = hashlib.sha1(z.read(n)).hexdigest()
    except Exception:
        return None
    return out


def engine_copy_diff(want, root):
    """引擎副本与本包轮子的逐文件对账 → (缺失/读不到, 内容不同, 对上的文件数)。

    判据是**产物字节**，不是 init 的退出码 —— 与 pip / 插件两格同一条纪律。
    """
    missing, differ, ok = [], [], 0
    for rel, h in sorted(want.items()):
        sha = hashlib.sha1()
        try:
            with open(os.path.join(root, rel), "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    sha.update(chunk)
        except OSError:
            missing.append(rel)
            continue
        if sha.hexdigest() == h:
            ok += 1
        else:
            differ.append(rel)
    return missing, differ, ok


def step_engine(a):
    """[5/7] 引擎副本：只刷新 init 复制出来的那份，源是本包轮子自带的引擎。"""
    fails = []
    targets = engine_copy_targets(a)
    if not targets:
        h = a.harness_norm
        if h and looks_like_engine(h) and os.path.exists(os.path.join(h, ".git")):
            print("  · %s 是引擎源（仓库），不是 init 副本 —— 不刷新（仓库更新即生效）" % norm(h))
        elif h and looks_like_engine(h):
            print("  · %s 没有 init 所有权标记，无法确认是可归档的副本 → 不自动刷新" % norm(h))
            print("      确认它是 init 副本时，装好新 wheel 后手动跑一次：")
            print("      agentguard init --force --dir \"%s\"" % norm(h))
        else:
            print("  · 未发现 agentguard init 复制出来的引擎副本（升级不替你新增通道）")
        return fails

    wheel, err = pack_wheel(a)
    if not wheel:
        print("  · ✗ %s" % err)
        fails.append("引擎副本刷新失败：包内轮子数量异常（期望 1 个）")
        record(a, "engine", "fail", err)
        return fails
    want = wheel_engine_files(wheel)
    if want is None:
        print("  · ✗ 读不出轮子内容（包坏了？）：%s" % norm(wheel))
        fails.append("引擎副本刷新失败：轮子读不出来")
        record(a, "engine", "fail", "wheel unreadable")
        return fails
    if not wheel_has_engine(wheel):
        print("  · ✗ 本包轮子未自带引擎（旧版包？）—— 无法刷新引擎副本")
        fails.append("引擎副本刷新失败：轮子未自带引擎")
        record(a, "engine", "fail", "wheel has no _engine")
        return fails

    if not a.yes:
        for dest, label, note in targets:
            print("  · %s：将刷新 %s" % (label, norm(dest)))
            if note:
                print("      识别依据：%s" % note)
            print("      现有 version %s；源：本包轮子 %s（version %s）"
                  % (engine_version(dest) or "?", os.path.basename(wheel),
                     wheel_version(wheel) or "?"))
            print("      将执行：解包轮子 → %s <轮子内 cli.py> init --force --dir \"%s\""
                  % (a.python, norm(dest)))
            missing, differ, _ok = engine_copy_diff(want, dest)
            if not missing and not differ:
                print("      预判：内容与本包一致 —— init 会幂等返回「引擎已是最新」")
        return fails

    with tempfile.TemporaryDirectory(prefix="agentguard-engine-") as tmp:
        cli, err = extract_wheel_cli(wheel, tmp)
        if not cli:
            print("  · ✗ %s" % err)
            fails.append("引擎副本刷新失败：%s" % err)
            record(a, "engine", "fail", err)
            return fails
        for dest, label, note in targets:
            print("  · %s：刷新 %s" % (label, norm(dest)))
            if note:
                print("      识别依据：%s" % note)
            before = engine_version(dest) or "?"
            rc, out = run_cmd([a.python, cli, "init", "--force", "--dir", dest], timeout=600)
            print("      init --force → rc=%s" % rc)
            if out.strip():
                print(indented(out, "        "))
            if rc != 0:
                fails.append("引擎副本刷新失败（init rc=%s）：%s" % (rc, norm(dest)))
                record(a, "engine", "fail", "rc=%s" % rc)
                continue
            # 升级**之后**这份对账才算数 —— 与 pip / 插件同一条纪律：rc 不是判据。
            missing, differ, ok = engine_copy_diff(want, dest)
            if missing or differ:
                print("      ✗ init 报成功，但产物与本包轮子**仍不一致** —— 判失败：")
                if missing:
                    print("        缺失/读不到 %d 个：%s%s"
                          % (len(missing), ", ".join(missing[:5]), " …" if len(missing) > 5 else ""))
                if differ:
                    print("        内容不同 %d 个：%s%s"
                          % (len(differ), ", ".join(differ[:5]), " …" if len(differ) > 5 else ""))
                print("        现在这台机器上跑的仍是旧引擎（version %s）。" % before)
                fails.append("引擎副本未刷新（init rc=0，但产物与本包不一致）：%s" % norm(dest))
                record(a, "engine", "stale",
                       "missing=%d differ=%d" % (len(missing), len(differ)))
                continue
            if not init_marker(dest):
                print("      ✗ 产物已刷新，但 init 没写所有权标记 %s → 下一次升级认不出它。" % INIT_MARKER)
                fails.append("引擎副本缺少 init 所有权标记：%s" % norm(dest))
                record(a, "engine", "stale", "marker missing")
                continue
            print("      ✓ 与本包逐字节一致（%d 个文件）→ version %s"
                  % (ok, engine_version(dest) or "?"))
            record(a, "engine", "ok", norm(dest))
    return fails


# ---- [6/7] 技能副本 ----------------------------------------------------------------

def skill_targets(a):
    """已存在的宿主技能副本 → [(宿主名, 技能目录)]（没装过的不列：升级 ≠ 新增通道）。"""
    out = []
    for name, base in sorted(SKILL_PRESETS.items()):
        t = os.path.join(base, SKILL_NAME)
        if os.path.isdir(t):
            out.append((name, t))
    for d in a.skill_dir:
        d = os.path.abspath(d)
        t = d if os.path.basename(d) == SKILL_NAME else os.path.join(d, SKILL_NAME)
        if os.path.isdir(t) and all(t != x[1] for x in out):
            out.append(("custom", t))
    return out


def step_skills(a):
    """[6/7] 技能副本：转发 cli.py（单一实现 + 台账）；无 cli 时打印现成命令。"""
    fails = []
    targets = skill_targets(a)
    if not targets:
        print("  · 未发现技能副本（升级不替你新增通道；要装见《安装说明》二）")
        return fails
    for name, t in targets:
        if not a.harness_cli:
            print("  · 技能副本[%s]：未找到引擎仓的 cli.py → 请手动执行：" % name)
            print("      python <HARNESS>/agentguard/cli.py skill install --dir \"%s\"" % norm(t))
            continue
        if not a.yes:
            print("  · 技能副本[%s]：将刷新 %s" % (name, norm(t)))
            continue
        rc, out = run_cmd([a.python, a.harness_cli, "skill", "install", "--dir", t])
        print("  · 技能副本[%s]：转发 cli.py skill install → rc=%s" % (name, rc))
        if out.strip():
            print(indented(out))
        if rc != 0:
            fails.append("技能副本[%s] 刷新失败" % name)
        record(a, "skills", "ok" if rc == 0 else "fail", norm(t))
    return fails


# ---- [7/7] 编辑期 shim -------------------------------------------------------------

def step_shim(a):
    """[7/7] 编辑期 shim：**只在装过时**刷新（它指向仓库，所以多半不用动；换过路径才需要）。"""
    fails = []
    shim = os.path.join(os.path.expanduser("~"), ".agentguard", "hooks",
                        "agentguard-gate.py")
    script = os.path.join(a.harness_norm, "sandbox", "install_shim.py") if a.harness_norm else ""
    if not os.path.isfile(shim):
        print("  · 未装编辑期 shim（升级不替你新增通道）。它是 7 家客户端编辑期门禁的唯一入口，")
        print("    要装请照《安装说明》二：python \"<HARNESS>/sandbox/install_shim.py\"")
        return fails
    if not script or not os.path.isfile(script):
        print("  · 引擎仓里没找到 %s → 无法刷新 shim（它指向仓库：仓库没挪过就不用动）" % script)
        return fails
    if not a.yes:
        print("  · 将刷新 shim：%s -m install_shim（重写 %s + 指针）" % (a.python, norm(shim)))
        return fails
    rc, out = run_cmd([a.python, script], timeout=300)
    print("  · %s → rc=%s" % ("install_shim.py", rc))
    if out.strip():
        print(indented(out))
    if rc != 0:
        fails.append("编辑期 shim 刷新失败（编辑器侧门禁可能仍指向旧路径）")
    record(a, "shim", "ok" if rc == 0 else "fail", norm(shim))
    return fails


# ---- 前置检查 ---------------------------------------------------------------------

def _require_python(vi=None):
    """前置检查：本工程需要 **Python 3.11+**（与《安装说明》和包元数据一致）。"""
    v = vi if vi is not None else sys.version_info
    if tuple(v[:2]) < (3, 11):
        sys.stderr.write(
            "✗ 需要 Python 3.11 或更高版本（当前 %d.%d）。\n"
            "  · 安装：https://www.python.org/downloads/（Windows 勾选 Add python.exe to PATH）\n"
            "  · 装好后重跑本命令；3.8/3.9/3.10 不受支持（引擎用到 3.9+ 的 API，且 3.11 以下从未测试）\n"
            % (v[0], v[1]))
        raise SystemExit(2)


def main(argv=None):
    _require_python()
    ap = argparse.ArgumentParser(description="一键升级：用新包刷新本机装过的副本（默认预演）")
    ap.add_argument("--harness", default=None,
                    help="你的 agentguard 仓库路径（缺省读 AGENTGUARD_DIR、HARNESS_DIR）")
    ap.add_argument("--python", default=None, help="跑 pip / cli.py 用的解释器（默认：当前解释器）")
    ap.add_argument("--pack", default=None,
                    help="安装包目录（默认：本脚本所在目录 —— 解压出来的包里就有本脚本）")
    ap.add_argument("--yes", action="store_true", help="确认执行（缺省只预演，不改任何东西）")
    ap.add_argument("--dry-run", action="store_true", help="与缺省相同的显式预演开关")
    ap.add_argument("--skip", default="",
                    help="跳过通道：claude,openclaw,pip,engine,skills,shim（逗号分隔）")
    ap.add_argument("--claude", default=None, help="claude CLI 路径（默认从 PATH 找）")
    ap.add_argument("--openclaw-state", default=None, help="OpenClaw 状态目录（默认 ~/.openclaw-autoclaw）")
    ap.add_argument("--skill-dir", action="append", default=[], metavar="DIR",
                    help="额外技能目录（宿主 skills 目录或技能目录本体，可多次）")
    a = ap.parse_args(argv)
    a.python = a.python or sys.executable or "python"
    if a.dry_run:
        a.yes = False

    skip = set(x.strip() for x in (a.skip or "").split(",") if x.strip())
    unknown = skip - set(SKIP_KEYS)
    if unknown:
        print("✗ --skip 不认识这几项：%s（可选：%s）" % (",".join(sorted(unknown)), ",".join(SKIP_KEYS)))
        return 1

    a.pack = os.path.abspath(a.pack or os.path.dirname(os.path.abspath(__file__)))
    harness = (a.harness or os.environ.get("AGENTGUARD_DIR")
               or os.environ.get("HARNESS_DIR") or "").strip()
    a.harness_norm = norm(harness) if harness else ""
    a.harness_cli = ""
    if a.harness_norm:
        c = os.path.join(a.harness_norm, "agentguard", "cli.py")
        if os.path.isfile(c):
            a.harness_cli = c

    print("== agentguard 一键升级 ==================================")
    print("   模式    : %s" % ("执行（--yes）" if a.yes else "预演（不改任何东西；确认后加 --yes 执行）"))
    print("   包目录  : %s" % norm(a.pack))
    print("   引擎库  : %s" % (a.harness_norm or "(未指定 —— 路径适配与技能/矩阵刷新都做不了)"))
    print("   解释器  : %s" % a.python)
    if not a.harness_norm:
        print("")
        print("✗ 请用 --harness 指定 agentguard 路径（或设 AGENTGUARD_DIR）。")
        print("  为什么必须给：新包里的 .mcp.json 是占位符，要靠引擎仓的 fix_paths.py 填成本机路径；")
        print("  漏了这一步，装出来的副本会指向一个不存在的解释器，而**没有任何一步会报错**。")
        return 1
    print("")

    before_rc, before = read_matrix(a)
    if before_rc == 0 and before.strip():
        print("── 升级前（本机装出来的副本各自是哪一版）─────────────")
        print(indented(before))
        print("")

    # [1/7] 路径适配：**永远第一步、不可跳过**（跳过的后果是"装完不报错、门禁整体失效"）
    print("[1/7] 路径适配（fix_paths.py；这一步不能跳）")
    fixer = os.path.join(a.pack, "fix_paths.py")
    fails = []
    if not os.path.isfile(fixer):
        print("  · ✗ 本包里没有 fix_paths.py：%s（--pack 指错目录了？）" % norm(fixer))
        record(a, "paths", "fail", "no fix_paths.py")
        return 1
    if not a.yes:
        print("  · 将执行：%s \"%s\" --harness \"%s\"" % (a.python, norm(fixer), a.harness_norm))
    else:
        rc, out = run_cmd([a.python, fixer, "--harness", a.harness_norm], timeout=300)
        print(indented(out) if out.strip() else "  · （无输出）")
        if rc != 0:
            print("  · ✗ 路径适配失败（rc=%s）—— **后面的通道一律不做**：" % rc)
            print("      适配不过 = 装出来的副本会指向不存在的解释器，MCP 起不来、编辑期门禁无声失效。")
            record(a, "paths", "fail", "rc=%d" % rc)
            return 1
        record(a, "paths", "ok", a.harness_norm)
    print("")

    steps = (
        ("claude", "[2/7] Claude Code 插件（marketplace add + plugin update）", step_claude),
        ("openclaw", "[3/7] OpenClaw 扩展（就地覆盖 bundle，重启桌面端生效）", step_openclaw),
        ("pip", "[4/7] 人用 CLI（pip 包）", step_pip),
        ("engine", "[5/7] 引擎副本（只刷新 init 复制出来的那份）", step_engine),
        ("skills", "[6/7] 技能副本", step_skills),
        ("shim", "[7/7] 编辑期 shim", step_shim),
    )
    for key, title, fn in steps:
        if key in skip:
            print("%s —— 跳过（--skip %s）" % (title, key))
            print("")
            continue
        print(title)
        fails += fn(a)
        print("")

    if a.yes:
        after_rc, after = read_matrix(a)
        if after_rc == 0 and after.strip():
            print("── 升级后（本机装出来的副本各自是哪一版）─────────────")
            print(indented(after))
            print("")

    if not a.yes:
        print("以上为预演；确认后加 --yes 执行。")
        print("提示：升级**不做任何撤回**（不卸载、不删文件）；要退出整套安装请用同包内 uninstall.py。")
        # 2026-09-23（评审 L15）：演练支原先**直接 return 0**，把上面刚收集的 fails 丢了 ⇒
        # 预演明明有步骤没做成，退出码却是 0，"回放绿灯"与"真跑绿灯"长得一模一样，
        # 自动化（或人看 $?）会据此放行。判据与 --yes 支同源：有未完成项就 rc=1 且逐条打出来。
        # 锁见 tests/test_review_2026_09_23.py::TestL15RehearsalExitCodeAndItsDocsNote。
        if fails:
            print("")
            print("⚠️ 预演里有 %d 项未完成（清单与 --yes 支同判据）：" % len(fails))
            for f in fails:
                print("   - %s" % f)
            print("   ⇒ 预演结论：**照现在这个环境跑 --yes 也不会成功**，请先按上方提示处理。")
            return 1
        return 0
    if fails:
        print("⚠️ 有 %d 项未完成，请按上方提示处理后重跑本脚本：" % len(fails))
        for f in fails:
            print("   - %s" % f)
        record(a, "summary", "partial", "; ".join(fails)[:200])
        return 1
    print("✅ 升级完成（台账已记录）。")
    print(" · 重启宿主（Claude Code / OpenClaw 桌面端）让插件与扩展重新加载。")
    print(" · 复核：python <HARNESS>/agentguard/cli.py installed")
    record(a, "summary", "done", "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
