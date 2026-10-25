#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""引导：把门禁引擎仓（guardrail-engine）clone 到本机。

为什么包里要带这支
================================================================================
安装包内**不含引擎**，这是**有意**的：提交钩子 / MCP 注册 / 编辑期 shim 全都
**指向仓库**，引擎一更新你这边就是新的，不用重装包（见《安装说明》「五」那张表的头四行）。
代价是"先 clone 引擎仓"成了拿到包的人要做的**第一件事** —— 而它此前只是《安装说明》
「先决条件」里括号中的一句、不是一个可执行的步骤。于是首次接入最常见的第一个坑长这样：
跑到「二」的第 0 步（路径适配）被 fail-fast 拦下，报错说"没找到 mcp.py"，
而真正缺的是"还没 clone"。

本脚本只做一件事：**把引擎 clone 到本机**（或如实报告它已经在了），再把后面两步打出来。
它不装任何东西、不写任何配置、不碰 `~/.guardrail-engine/` —— 所以它**不在 `uninstall.py`
的五项清理里**：clone 出来的仓库是你自己的仓库，与"装了什么"是两回事
（《安装说明》「四」也写着"本工具不动你的 `<HARNESS>` 仓库"）。

用法
================================================================================
    python bootstrap.py                              # clone 到 ~/guardrail-engine
    python bootstrap.py --dir D:/dev/guardrail-engine
    python bootstrap.py --url git@...:xxx.git        # 换了地址 / 走 SSH
    python bootstrap.py --check                      # 只体检（git 在不在 / 目标什么状态），不动手

退出码：0 = 成功（含"已经在了"）；1 = 失败（clone 出错 / 目标目录占着且不是引擎仓）；
        2 = 前置不足（Python < 3.11 / 没有 git）。

三条刻意的"不做"
================================================================================
· **不覆盖已存在的目录**：目标目录在、但里面不是引擎仓（没有 `guardrail_engine/mcp.py`）
  → 拒绝并退出，不删不并。别人的目录不该由安装包来处置。
· **不动已有的引擎仓**：目标已经是引擎仓 → 只说"已经在了"，不 pull、不更新。更新是
  那个仓库自己的事（`git pull`），安装包没有资格替你决定升到哪个提交。
· **没有 `--yes`**：本脚本不撤回、不覆盖、不删任何东西，动作是**纯增量**的（新建一个
  目录），所以不需要"先预演再确认"那一套；`--check` 提供的是"先看一眼"的等价物。
  （`uninstall.py` / `upgrade.py` 要 `--yes`，是因为它们会动你已经装好的东西 —— 两回事。）
"""
import argparse
import os
import shutil
import subprocess
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

#: 引擎仓地址（公开源）。私有镜像 / 内网部署用 `--url` 覆盖。
DEFAULT_URL = "https://github.com/rain610/guardrail-engine.git"

#: 引擎仓的判据。**与 `fix_paths.py` 的 fail-fast 用同一条**（它查的就是这个文件）：
#: 两处判据不一致的话，会出现"本脚本说好了、下一步照样 ✗"的鬼故事。
ENGINE_MARKER = os.path.join("guardrail_engine", "mcp.py")
LEGACY_ENGINE_MARKER = os.path.join("agentguard", "mcp.py")


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


def norm(p):
    return os.path.abspath(p).replace(os.sep, "/")


def is_engine(d):
    """这个目录是不是引擎仓 —— 判据见 `ENGINE_MARKER` 上面的说明。"""
    return any(os.path.isfile(os.path.join(d, *m.split("/")))
               for m in (ENGINE_MARKER, LEGACY_ENGINE_MARKER))


def _self_dir():
    return norm(os.path.dirname(os.path.abspath(__file__)))


def _next_steps(harness, pack_dir):
    """把"clone 完还剩哪两步"打出来 —— 本脚本的产出里，这一段才是重点。

    为什么必须打：原来的坑不只是"不知道要 clone"，还有"clone 完了也不知道接下来干嘛"
    （《安装说明》后面两节都在讲别的）。这里直接给可复制的命令，不要求读者回去翻文档。
    """
    print("")
    print("下一步（都在这份安装包里，顺序照做即可）：")
    print("  1. 路径适配 —— 把包里的占位符填成本机路径（**每次装/升级都要做一次**）")
    print("       python \"%s/fix_paths.py\" --harness \"%s\"" % (pack_dir, harness))
    print("  2. 生成你机器的仓库覆盖层（首次会提示你编辑它生成的 config/harness.local.toml，改完重跑）")
    print("       sh \"%s/sandbox/setup_machine.sh\"" % harness)
    print("  3. 按《安装说明》装入口：人用 CLI / Claude 插件 / OpenClaw（任选，可都装）")
    print("")
    print("  说明：%s 就是文档里那个 <HARNESS>。门禁脚本内部一律用相对路径定位资源，" % harness)
    print("        所以 clone 到哪都行；只有本机的客户端钩子配置需要写这个绝对路径。")


def main(argv=None):
    _require_python()   # 前置检查（Python 3.11+）
    ap = argparse.ArgumentParser(
        description="把门禁引擎仓 clone 到本机（安装包内不含引擎，这是接入的第一步）")
    ap.add_argument("--dir", default=None,
                    help="clone 到哪里（默认 ~/guardrail-engine）")
    ap.add_argument("--url", default=DEFAULT_URL,
                    help="引擎仓地址（默认公开源；私有镜像或走 SSH 时用）")
    ap.add_argument("--check", action="store_true",
                    help="只体检：git 在不在、目标目录什么状态，不动手")
    a = ap.parse_args(argv)

    target = norm(a.dir or os.path.join(os.path.expanduser("~"), "guardrail-engine"))
    here = _self_dir()

    print("== guardrail-engine 引擎引导 =============================")
    print("   目标目录: %s" % target)
    print("   仓库地址: %s" % a.url)
    print("")

    # ---- 前置：git（clone 的必要条件）----
    git = shutil.which("git")
    if not git:
        print("✗ 没找到 git —— clone 引擎仓要先装它。")
        print("  · Windows 一条命令（在 PowerShell / CMD 里跑，装完**重开终端**）：")
        print("      winget install --id Git.Git -e --source winget")
        print("  · 或从 https://git-scm.com/downloads 下载安装（它会一并带上 git-bash 的 sh）")
        print("  · 装好后重跑本命令")
        return 2
    print("   git     : %s" % git)

    # ---- 目标目录体检（三种状态，各不相同）----
    if is_engine(target):
        print("  状态    : ✓ 引擎已经在了")
        print("")
        print("✓ 什么都没做 —— 本脚本只负责 clone，**不覆盖、也不更新**已有的引擎仓。")
        print("  （要更新它：cd \"%s\" && git pull —— 那是那个仓库自己的事）" % target)
        _next_steps(target, here)
        return 0

    if os.path.isdir(target):
        print("  状态    : ✗ 目录已存在，但它不是引擎仓")
        print("")
        print("✗ 拒绝在 %s 上动手。" % target)
        print("  判据：该目录里没有 %s。" % ENGINE_MARKER.replace("/", os.sep))
        print("  本脚本不会删、不会并、不会覆盖别人的目录。两条路：")
        print("    · 换个位置：python bootstrap.py --dir <另一个路径>")
        print("    · 或先自己看一眼那个目录，确认它没用之后再手工处理")
        return 1

    print("  状态    : 目录不存在，将新建")
    parent = os.path.dirname(target.rstrip("/")) or target
    made_parent = not os.path.isdir(parent)
    if made_parent:
        print("             （父目录也不存在，会先建：%s）" % parent)

    if a.check:
        print("")
        print("✓ 体检通过（--check：只体检，没动手）。去掉 --check 即执行 clone。")
        return 0

    if made_parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as ex:
            print("✗ 建不出父目录 %s：%r" % (parent, ex))
            return 1

    print("")
    print("正在 clone（视网速几十秒到几分钟）...")
    try:
        r = subprocess.run([git, "clone", a.url, target],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError as ex:
        print("✗ 起不了 git 进程：%r" % (ex,))
        return 1
    if r.returncode != 0:
        print("✗ clone 失败（git rc=%s）：" % r.returncode)
        for ln in (r.stderr or r.stdout or "").strip().splitlines()[-8:]:
            print("    %s" % ln)
        print("  最常见的原因：仓库地址不对，或没有该仓库的访问权限。")
        print("  公开源：https://github.com/rain610/guardrail-engine；私有镜像用 --url 指定（走 SSH 就给 SSH 地址）。")
        return 1

    # clone 成功 ≠ 拿到的是引擎仓：域名/路径敲错时 git 可能克隆到**别的**仓。
    # fix_paths.py 下一步查的就是这个文件，所以在这里先查一次，别把失败留给下一步。
    if not is_engine(target):
        print("✗ clone 完了，但 %s 里没有 %s。" % (target, ENGINE_MARKER.replace("/", os.sep)))
        print("  拿到的多半不是引擎仓 —— 核对一下 --url，或到 %s 里看一眼。" % target)
        return 1

    print("")
    print("✓ 引擎已就位：%s" % target)
    _next_steps(target, here)
    return 0


if __name__ == "__main__":
    sys.exit(main())
