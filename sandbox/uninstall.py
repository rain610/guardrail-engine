#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键卸载（整机退出）：把「离线安装包」在这台机器上装过的东西逐项撤回。

对照包内「安装说明.md」的安装面，处理六项：
  [1/6] 技能副本（skill install 装入宿主目录）→ 改名归档（可逆，可随时改回）
  [2/6] 提交期钩子（core.hooksPath）          → 只对你 --unhook 点名的仓库撤回；不删文件
  [3/6] Claude Code 插件                      → plugin uninstall + marketplace remove
  [4/6] OpenClaw 扩展                         → 移出 extensions 归档（重启桌面端生效）
  [5/6] 人用 CLI（pip 包）                    → pip uninstall agentguard
  [6/6] 编辑期 shim（~/.agentguard/）       → 改名归档（可逆）；7 家客户端的编辑期门禁入口

用法（默认只预演，不改任何东西；确认无误后加 --yes 执行）：

    python uninstall.py                                   # 预演：列出每一项将做什么
    python uninstall.py --yes                             # 执行
    python uninstall.py --yes --unhook <端仓>              # 顺带撤回该仓库的提交期钩子（可多次）
    python uninstall.py --yes --skip openclaw,pip         # 想保留某几项时

[6/6] 是 2026-09-17 剖析报告 G5 补的：shim 与指针此前在安装说明、本脚本里**都没有名字**，
于是"整机退出"之后 ~/.agentguard/ 下会原样留着它们、指针还指着别人的引擎仓。
本步转发 <HARNESS>/sandbox/install_shim.py --uninstall（单一实现）；没有引擎仓时按同口径兜底归档。

原则：能自动做的自动做；够不着的打印现成命令；防误删（特征校验）；全程留痕（台账）。
退出码：0 = 完成 / 本来就没有可卸的；1 = 有未完成项或参数问题（见输出）。
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import time

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SKILL_NAMES = ("guardrail-engine", "agentguard")   # 新名优先；旧名卷进迁移期
ARCHIVE_SUFFIX = ".disabled-"      # 与 cli.py 同口径（见 archive_name 的说明）
# 与 agentguard/cli.py 的 SKILL_PRESETS 保持一致（壳层常量镜像，别单边改）
SKILL_PRESETS = {
    "claude": os.path.join(os.path.expanduser("~"), ".claude", "skills"),
    "opencode": os.path.join(os.path.expanduser("~"), ".config", "opencode", "skills"),
    "codebuddy": os.path.join(os.path.expanduser("~"), ".codebuddy", "skills"),
}
SKIP_KEYS = ("skills", "hooks", "claude", "openclaw", "pip", "shim")
CLAUDE_PLUGIN_CMDS = (
    ["claude", "plugin", "uninstall", "agentguard@agentguard-tools"],
    ["claude", "plugin", "marketplace", "remove", "agentguard-tools"],
)


def norm(p):
    return os.path.abspath(p).replace(os.sep, "/")


def stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def archive_name(target, suffix=ARCHIVE_SUFFIX):
    """挑一个没被占用的归档名：`<target><suffix><戳>`，撞车就加序号 `-2`、`-3`…

    **跨文件同口径**：agentguard/cli.py:archive_name 是主实现，sandbox/install_shim.py
    是第二份镜像（三份都随包/随仓分发，谁也不能 import 谁）。改动任一侧都要同步，
    tests/packaging/test_uninstall.py::TestArchiveNameDoesNotDrift 拿同一输入喂三份实现做对拍。

    秒级戳不够用：脚本化的「卸载 → 重装 → 再卸载」会落在同一秒里，Windows 上 os.rename
    撞到已存在的目录直接抛 OSError —— 归档失败、而且报出来是"可能被占用"（剖析报告 G8）。
    """
    base = "%s%s%s" % (target, suffix, stamp())
    if not os.path.exists(base):
        return base
    n = 2
    while os.path.exists("%s-%d" % (base, n)):
        n += 1
    return "%s-%d" % (base, n)


# ---- 「安装前 core.hooksPath」存档（评审 v13 中 17）----------------------------------
# 格式与 agentguard/cli.py:read_prev_hookspath **必须一致**（那边是主实现，
# 这里是"CLI 转发不了"时的兜底镜像）。本脚本随安装包分发，不能 import 包内模块：
# [5/6] 那步正是把包卸掉，而 [2/6] 可能在此之前跑。改动任一侧都要同步另一侧，有对拍锁。
BACKUP_DIRNAME = "agentguard-original-hooks"
PREV_HP_NAME = "PREVIOUS-hooksPath.txt"
PREV_HP_PREFIX = "安装前 core.hooksPath："
PREV_HP_UNSET = "<未设置>"


def _abs_git_path(repo, p):
    p = (p or "").strip().replace("\\", "/")
    if not p:
        return None
    if p.startswith("/") or (len(p) > 2 and p[1] == ":" and p[2] == "/"):
        return os.path.abspath(p)
    return os.path.abspath(os.path.join(repo, p))


def prev_hookspath(repo):
    """读安装存档 → (状态, 原值)：ok / unset / none / selfref（语义见 cli.py 同名函数）。"""
    rc, out = run_cmd(["git", "-C", repo, "rev-parse", "--git-common-dir"])
    common = _abs_git_path(repo, out) if rc == 0 else None
    if not common:
        return "none", None
    arch = os.path.join(common, BACKUP_DIRNAME, PREV_HP_NAME)
    if not os.path.isfile(arch):
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
    # 存档指向门禁自己的共享分发器 = 旧版"重装覆盖存档"留下的坏档（结构判据，无需知道工程根）
    if val.replace("\\", "/").rstrip("/").endswith("/harness/hooks-dir"):
        return "selfref", val
    return "ok", val


def _decode(b):
    """子进程字节输出解码：UTF-8 严格失败回退 GB18030（Windows CLI 兼容）。"""
    if b is None:
        return ""
    if isinstance(b, str):
        return b
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("gb18030", "replace")


def run_cmd(args, timeout=300):
    """跑一个外部命令；返回 (rc, 合并输出)。`.cmd/.bat` 在 Windows 上**直跑**，不经 `cmd /c`。

    2026-09-23（评审 L14）：那层 `cmd /c` 的理由（"subprocess 不经 shell 执行不到"）**实测是假的**
    —— 直接调 kernel32 的 `CreateProcessW`（绕开 subprocess）也能跑 `.cmd` ⇒ 是**操作系统本人**认它
    （Python 文档同此）。而它会在**路径与参数都含空格**时按 cmd 自己的规则再剥一遍引号 ⇒ 引号错位、
    rc=1 且输出为空。与 `upgrade.py` 同实现、判据同源；锁见
    `tests/review/test_review_2026_09_23.py::TestL14BatchFileIsNotHandedToAShell`。
    """
    exe = shutil.which(args[0]) or args[0]
    cmd = [exe] + list(args[1:])
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.returncode, (_decode(p.stdout) + _decode(p.stderr))
    except Exception as ex:
        return 127, "无法执行 %s：%r" % (args[0], ex)


def record(a, step, result, reason=""):
    """运维台账：与 cli.py 同文件同风格（entry=uninstall）；失败提示但不阻断。"""
    p = (os.environ.get("GUARDRAIL_OPS_LOG") or os.environ.get("AGENTGUARD_OPS_LOG")
         or (
        os.path.join(a.harness_norm, "reports", "ops_log.jsonl") if a.harness_norm else ""))
    if not p:
        return
    try:
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "entry": "uninstall",
               "op": "machine_uninstall", "step": step, "result": result, "reason": reason}
        with io.open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as ex:
        print("  [ops] 台账写入失败（不阻断）：%r" % ex)


def indented(text, pad="      "):
    return "\n".join(pad + ln for ln in text.strip().splitlines() if ln.strip())


def step_skills(a):
    """[1/6] 技能副本：优先转发 cli.py（单一实现 + 台账）；无 cli 时按同规则归档。"""
    fails = []
    targets = []
    for name, base in sorted(SKILL_PRESETS.items()):
        for skill in SKILL_NAMES:
            t = os.path.join(base, skill)
            if os.path.isdir(t):
                targets.append((name, t))
    for d in a.skill_dir:
        d = os.path.abspath(d)
        hit = None
        if os.path.basename(d) in SKILL_NAMES:
            hit = d
        else:
            for skill in SKILL_NAMES:
                if os.path.isdir(os.path.join(d, skill)):
                    hit = os.path.join(d, skill)
                    break
        if hit is None:
            print("  · 自定义目录未见 %s/：%s（跳过）" % (" 或 ".join(SKILL_NAMES), norm(d)))
            continue
        if os.path.isdir(hit):
            targets.append(("custom", hit))
    if not targets:
        print("  · 未发现技能副本（无需处理）")
        return fails
    for name, t in targets:
        if not a.yes:
            print("  · 技能副本[%s]：将改名归档（可逆）→ %s.disabled-<时间戳>" % (name, norm(t)))
            continue
        if a.harness_cli:
            rc, out = run_cmd([a.python, a.harness_cli, "skill", "uninstall", "--dir", t])
            print("  · 技能副本[%s]：转发 cli.py skill uninstall → rc=%s" % (name, rc))
            if out.strip():
                print(indented(out))
            if rc != 0:
                fails.append("技能副本[%s] 归档失败" % name)
        else:
            arch = archive_name(t)
            try:
                os.rename(t, arch)
                print("  · 技能副本[%s]：已归档（可逆）→ %s" % (name, norm(arch)))
                record(a, "skills", "archived", norm(arch))
            except OSError as ex:
                print("  · 技能副本[%s]：✗ 归档失败：%r" % (name, ex))
                fails.append("技能副本[%s] 归档失败" % name)
                record(a, "skills", "fail", repr(ex))
    return fails


def step_hooks(a):
    """[2/6] 提交期钩子：仅 --unhook 点名的仓库；撤回 core.hooksPath（不删文件）。"""
    fails = []
    if not a.unhook:
        print("  · 未指定仓库：如需撤回，重跑并加 --unhook <端仓>（每个仓库执行一次；不删任何文件）")
        return fails
    for repo in a.unhook:
        repo = os.path.abspath(repo)
        if not os.path.isdir(repo):
            print("  · [%s]：✗ 仓库不存在" % norm(repo))
            fails.append("--unhook 仓库不存在：%s" % norm(repo))
            continue
        if not a.yes:
            print("  · [%s]：将撤回 core.hooksPath（不删文件）" % norm(repo))
            continue
        if a.harness_cli:
            rc, out = run_cmd([a.python, a.harness_cli, "hooks", "uninstall", "--repo", repo])
            print("  · [%s]：转发 cli.py hooks uninstall → rc=%s" % (norm(repo), rc))
            if out.strip():
                print(indented(out))
            if rc != 0:
                fails.append("提交期钩子撤回失败：%s" % norm(repo))
        else:
            # 评审 v13 中 17：兜底分支同样要读安装存档（这条路径恰恰是"pip 包已被卸掉、
            # CLI 转发不了"时走的），否则同一次卸载会因为「CLI 在不在」而给出两种结果：
            # 有 CLI → 还原原值；没 CLI → 一律清空。判定与 agentguard/cli.py:read_prev_hookspath
            # 同源（tests/review/test_review_v13_batch6.py 有对拍锁）。
            state, prev = prev_hookspath(repo)
            if state == "ok":
                rc, out = run_cmd(["git", "-C", repo, "config", "--local", "core.hooksPath", prev])
                if rc != 0:
                    print("  · [%s]：✗ 还原安装前的 core.hooksPath 失败：%s"
                          % (norm(repo), (out or "").strip()[:160]))
                    fails.append("提交期钩子还原失败：%s" % norm(repo))
                    record(a, "hooks", "fail", (out or "").strip()[:160])
                else:
                    print("  · [%s]：已还原安装前的 core.hooksPath → %s" % (norm(repo), prev))
                    record(a, "hooks", "ok", "hooksPath restored to %s" % prev)
            else:
                rc, out = run_cmd(["git", "-C", repo, "config", "--local", "--unset", "core.hooksPath"])
                if rc == 5:
                    print("  · [%s]：core.hooksPath 本来就未设置（无需卸载）" % norm(repo))
                elif rc != 0:
                    print("  · [%s]：✗ git config --unset 失败：%s" % (norm(repo), (out or "").strip()[:160]))
                    fails.append("提交期钩子撤回失败：%s" % norm(repo))
                    record(a, "hooks", "fail", (out or "").strip()[:160])
                else:
                    print("  · [%s]：已撤回 core.hooksPath" % norm(repo))
                    if state == "none":
                        print("      注意：没有安装存档 → 只能清空，无法还原接入前的自定义目录")
                    elif state == "selfref":
                        print("      注意：存档记的是门禁自己的目录（旧版重装覆盖留的坏档）→ 按清空处理")
                    record(a, "hooks", "ok", "hooksPath cleared (archive=%s)" % state)
    return fails


def step_claude(a):
    """[3/6] Claude Code 插件：检测到 claude CLI 才自动；否则打印手动命令。"""
    if not shutil.which("claude"):
        print("  · 未检测到 claude CLI：若你装过 Claude Code 插件，手动执行：")
        print("      claude plugin uninstall agentguard@agentguard-tools")
        print("      claude plugin marketplace remove agentguard-tools")
        return []
    if not a.yes:
        for c in CLAUDE_PLUGIN_CMDS:
            print("  · 将执行：%s" % " ".join(c))
        return []
    results = []
    for c in CLAUDE_PLUGIN_CMDS:
        rc, out = run_cmd(c, timeout=180)
        tail = " ｜ ".join(ln.strip() for ln in out.strip().splitlines()[-2:] if ln.strip())
        print("  · %s → rc=%s%s" % (" ".join(c), rc, ("：" + tail[:160]) if tail else ""))
        results.append(rc == 0)
    ok = all(results)
    if not ok:
        print("      （提示：报 not installed / not found 多为本来就没装，可忽略；确有问题请手动跑上面两条）")
    record(a, "claude-plugin", "ok" if ok else "warn", "")
    return []


def step_openclaw(a):
    """[4/6] OpenClaw 扩展：移出 extensions 归档（防误删：特征校验不符则拒绝）。"""
    fails = []
    state = os.path.abspath(a.openclaw_state or os.environ.get("OPENCLAW_STATE_DIR")
                            or os.path.join(os.path.expanduser("~"), ".openclaw-autoclaw"))
    ext = os.path.join(state, "extensions", "agentguard")
    if not os.path.isdir(ext):
        print("  · %s 下未发现 agentguard 扩展（无需处理）" % norm(state))
        return fails
    markers = (os.path.join(ext, ".claude-plugin", "plugin.json"),
               os.path.join(ext, ".mcp.json"))
    if not any(os.path.isfile(m) for m in markers):
        print("  · ⚠️ 目录存在但缺少 agentguard 特征文件，拒绝自动处理（防误删同名目录）：")
        print("      %s" % norm(ext))
        print("      确认确为门禁扩展且不再需要时，请手动删除该目录。")
        fails.append("OpenClaw 扩展需人工确认：%s" % norm(ext))
        return fails
    # 同一个撞车问题（同秒内两次卸载 → 目录已存在 → rename 抛错）：走同一个挑选器；
    # 后缀沿用原来的 `.uninstalled-`（磁盘上已有这种名字，不动它）
    arch = archive_name(os.path.join(state, "agentguard"), ".uninstalled-")
    if not a.yes:
        print("  · 将移出归档：%s" % norm(ext))
        print("                    → %s（重启桌面端生效）" % norm(arch))
        return fails
    try:
        os.rename(ext, arch)
        print("  · 已移出归档 → %s" % norm(arch))
        print("      重启 OpenClaw 桌面端后生效；确认稳定后可删归档目录。")
        record(a, "openclaw", "archived", norm(arch))
    except OSError as ex:
        print("  · ✗ 移动失败（桌面端可能正占用）：%r" % ex)
        print("      处置：退出桌面端后重跑本脚本，或手动移动该目录。")
        fails.append("OpenClaw 扩展移动失败")
        record(a, "openclaw", "fail", repr(ex))
    return fails


def step_pip(a):
    """[5/6] 人用 CLI：pip uninstall guardrail-engine / 旧 agentguard（-y；用 --python 指定解释器）。"""
    fails = []
    found = [d for d in ("guardrail-engine", "agentguard")
             if run_cmd([a.python, "-m", "pip", "show", d])[0] == 0]
    if not found:
        print("  · pip 未见 guardrail-engine / agentguard（无需处理）")
        return fails
    if not a.yes:
        for d in found:
            print("  · 将执行：%s -m pip uninstall -y %s" % (a.python, d))
        return fails
    for d in found:
        rc, out = run_cmd([a.python, "-m", "pip", "uninstall", "-y", d], timeout=600)
        ok = rc == 0
        tail = " ｜ ".join(ln.strip() for ln in out.strip().splitlines()[-2:] if ln.strip())
        print("  · pip uninstall %s → rc=%s%s" % (d, rc, ("：" + tail[:160]) if tail else ""))
        if not ok:
            fails.append("pip 卸载失败（%s，解释器：%s）" % (d, a.python))
        record(a, "pip", "ok" if ok else "fail", "%s @ %s" % (d, a.python))
    return fails


def step_shim(a):
    """[6/6] 编辑期 shim：转发 install_shim.py --uninstall（单一实现）；够不着时按同口径兜底。

    shim 与指针是 7 家客户端**编辑期门禁的唯一入口**（剖析报告 G5：此前安装说明、本脚本
    里都没有它们的名字）。转发优先：生成它们的脚本才是懂它们的地方；引擎仓不在场
    （本脚本随包分发，正是"包还在、仓联系不上"的场景）时按同一命名口径归档。
    """
    fails = []
    home = os.path.expanduser("~")
    agents = None
    have = []
    for d in (os.path.join(home, ".guardrail-engine"), os.path.join(home, ".agentguard")):
        got = [p for p in (os.path.join(d, "hooks", "agentguard-gate.py"),
                           os.path.join(d, "harness.path")) if os.path.isfile(p)]
        if got and agents is None:
            agents = d
        have += got
    if not have:
        print("  · 未装编辑期 shim（无需处理）")
        return fails
    script = os.path.join(a.harness_norm, "sandbox", "install_shim.py") if a.harness_norm else ""
    if not a.yes:
        print("  · 将归档编辑期 shim 与指针（可逆，不删文件）：")
        for p in have:
            print("      %s.disabled-<时间戳>" % norm(p))
        print("      注意：客户端的 hook 配置仍写着这个路径，退出后要到各宿主里删掉该 hook。")
        return fails
    if script and os.path.isfile(script):
        rc, out = run_cmd([a.python, script, "--uninstall"])
        print("  · 转发 install_shim.py --uninstall → rc=%s" % rc)
        if out.strip():
            print(indented(out))
        if rc != 0:
            fails.append("编辑期 shim 撤回失败")
        record(a, "shim", "ok" if rc == 0 else "fail", norm(agents))
        return fails
    print("  · 引擎仓不在场（%s）→ 按同口径兜底归档" % (norm(a.harness_norm) if a.harness_norm else "未指定 --harness"))
    for p in have:
        dst = archive_name(p)
        try:
            os.rename(p, dst)
            print("  · 已归档（可逆）→ %s" % norm(dst))
            record(a, "shim", "archived", norm(dst))
        except OSError as ex:
            print("  · ✗ 归档失败（未移动，仍在原处）：%r" % ex)
            fails.append("编辑期 shim 归档失败")
            record(a, "shim", "fail", repr(ex))
    return fails


def _require_python(vi=None):
    """前置检查：本工程需要 **Python 3.11+**（与《安装说明》和包元数据一致）。

    为什么要有：包元数据曾写 >=3.8，而引擎用到 3.9+ 的 API（`pathlib.is_relative_to`），
    3.11 以下从未测试过 —— 版本不够时应当**人话报错并退出**，而不是让用户撞
    AttributeError / 语法错（那种报错没人看得懂，还会被当成"包坏了"）。
    参数 vi 仅供测试注入（默认取 sys.version_info）。
    """
    import sys
    v = vi if vi is not None else sys.version_info
    if tuple(v[:2]) < (3, 11):
        sys.stderr.write(
            "✗ 需要 Python 3.11 或更高版本（当前 %d.%d）。\n"
            "  · 安装：https://www.python.org/downloads/（Windows 勾选 Add python.exe to PATH）\n"
            "  · 装好后重跑本命令；3.8/3.9/3.10 不受支持（引擎用到 3.9+ 的 API，且 3.11 以下从未测试）\n"
            % (v[0], v[1]))
        raise SystemExit(2)


def main(argv=None):
    _require_python()   # 前置检查（Python 3.11+）
    ap = argparse.ArgumentParser(description="一键卸载：把「离线安装包」装过的东西逐项撤回（默认预演）")
    ap.add_argument("--harness", default=None,
                    help="你的 guardrail-engine 仓库路径（用于转发 cli.py / 写台账；缺省读 GUARDRAIL_ENGINE_DIR、AGENTGUARD_DIR、HARNESS_DIR）")
    ap.add_argument("--python", default=None, help="pip 卸载用的解释器（默认：当前解释器）")
    ap.add_argument("--yes", action="store_true", help="确认执行（缺省只预演，不改任何东西）")
    ap.add_argument("--dry-run", action="store_true", help="与缺省相同的显式预演开关")
    ap.add_argument("--skip", default="", help="跳过组件：%s（逗号分隔）" % ",".join(SKIP_KEYS))
    ap.add_argument("--unhook", action="append", default=[], metavar="REPO",
                    help="撤回该端仓的提交期钩子（可多次）")
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

    harness = (a.harness or os.environ.get("GUARDRAIL_ENGINE_DIR")
               or os.environ.get("AGENTGUARD_DIR")
               or os.environ.get("HARNESS_DIR") or "").strip()
    a.harness_norm = norm(harness) if harness else ""
    a.harness_cli = ""
    if a.harness_norm:
        for rel in (("guardrail_engine", "cli.py"), ("agentguard", "cli.py")):
            c = os.path.join(a.harness_norm, *rel)
            if os.path.isfile(c):
                a.harness_cli = c
                break
        if not a.harness_cli:
            print("提示：%s 下没找到 guardrail_engine/cli.py —— 技能/钩子将走内置同规则处理" % a.harness_norm)

    state_show = (a.openclaw_state or os.environ.get("OPENCLAW_STATE_DIR")
                  or os.path.join(os.path.expanduser("~"), ".openclaw-autoclaw"))
    print("== guardrail-engine 一键卸载 =============================")
    print("   模式    : %s" % ("执行（--yes）" if a.yes else "预演（不改任何东西；确认后加 --yes 执行）"))
    print("   引擎库  : %s" % (a.harness_norm or "(未指定——技能/钩子走内置处理)"))
    print("   解释器  : %s（pip 卸载目标）" % a.python)
    print("   OpenClaw: %s" % norm(state_show))
    print("")

    fails = []
    steps = (
        ("skills", "[1/6] 技能副本（skill install 装入宿主）", step_skills),
        ("hooks", "[2/6] 提交期钩子（core.hooksPath）", step_hooks),
        ("claude", "[3/6] Claude Code 插件", step_claude),
        ("openclaw", "[4/6] OpenClaw 扩展", step_openclaw),
        ("pip", "[5/6] 人用 CLI（pip 包）", step_pip),
        ("shim", "[6/6] 编辑期 shim（~/.guardrail-engine/ 或旧 ~/.agentguard/）", step_shim),
    )
    for key, title, fn in steps:
        if key in skip:
            print("%s —— 跳过（--skip %s）" % (title, key))
            continue
        print(title)
        fails += fn(a)

    print("")
    print("── 收尾提示 ──────────────────────────────")
    print(" · 重启 OpenClaw 桌面端：让扩展卸载生效（重启后 plugins list 应查无 agentguard）。")
    print(" · 手工注册过 MCP 的客户端（claude mcp add / codex mcp add 等）：到对应宿主 mcp remove（见 RUNBOOK「4. 卸载与回滚」）。")
    print(" · 安装包目录：不再需要时整目录删除即可（含本脚本与 marketplace/）。")
    print(" · 你的引擎仓库：本工具全程未改动%s；去留由你决定。" % (("（%s）" % a.harness_norm) if a.harness_norm else ""))
    print(" · 客户端 hook 配置：编辑期 shim 已归档，但各家 hook 配置里还写着它 → 到宿主配置里删掉该 hook，")
    print("   否则编辑期门禁会静默失效（见 RUNBOOK「4. 卸载与回滚」）。")
    print(" · 验证查无：pip show guardrail-engine ｜ claude plugin list ｜ OpenClaw plugins list ｜ cli.py skill status"
          "（会列归档残骸）｜ python <HARNESS>/sandbox/install_shim.py --check。")
    if not a.yes:
        print("")
        print("以上为预演；确认后加 --yes 执行。")
        return 0
    if fails:
        print("")
        print("⚠️ 有 %d 项未完成，请按上方提示处理后重跑本脚本：" % len(fails))
        for f in fails:
            print("   - %s" % f)
        record(a, "summary", "partial", "; ".join(fails)[:200])
        return 1
    print("")
    print("✅ 卸载完成（台账已记录）。想回来时，重新解压安装包按「安装说明」装一遍即可。")
    record(a, "summary", "done", "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
