#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[已退役] 渲染仓级 AGENTS.md 里的 `<HARNESS>` 占位符 → 实际路径。

2026-10-12 退役原因：
  指针写法落地后（P1#3），<HARNESS> 占位符 + 路径约定说明 就是**终态**——
  AI 客户端从约定说明里自行解析（$EAGLERMC_HARNESS_DIR / 旧名 $HARNESS_DIR，或 ~/.eagle-agents/harness.path）。
  渲染成绝对路径反而违反机器无关原则（check ⑦ 会拦）。

  --check 保留，语义改为：只查「路径过期」（已渲染成旧绝对路径的情况），
  占位符视为合规（rc=0）。渲染模式不再使用。

用法：
    python sandbox/render_harness_placeholders.py --check  # 检查路径是否过期

退出码：0=合规（含占位符或路径正确）/ 1=有过期路径 / 2=参数错
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

# UTF-8 兜底（与其他入口一致：GBK/ASCII 控制台防崩溃）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 引擎根定位（统一解析链）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "eaglermc_gate"))
try:
    from _shell import find_root
except ImportError:
    # fallback: 从本文件位置向上找
    def find_root():
        d = os.path.dirname(os.path.abspath(__file__))
        for _ in range(5):
            if os.path.isfile(os.path.join(d, "harness", "run_gate.py")):
                return d
            d = os.path.dirname(d)
        return None


def read_repos(harness_root):
    """从 config/harness.local.yaml 读 repos 段。"""
    cfg = os.path.join(harness_root, "config", "harness.local.yaml")
    if not os.path.isfile(cfg):
        return {}
    repos = {}
    with open(cfg, "r", encoding="utf-8") as f:
        in_repos = False
        for line in f:
            stripped = line.strip()
            if stripped.startswith("repos:"):
                in_repos = True
                continue
            if in_repos:
                if stripped and not stripped.startswith("#") and ":" in stripped:
                    key, val = stripped.split(":", 1)
                    key = key.strip()
                    val = val.strip()
                    if key and val:
                        repos[key] = val
                elif stripped and not stripped.startswith("#") and not stripped.startswith(" "):
                    # 新段开始了
                    break
    return repos


def check_file(path, harness_path):
    """检查单个 AGENTS.md 文件的状态。

    返回 (status, detail):
      - ("ok", None): 无占位符，路径正确（或无需路径）
      - ("placeholder", None): 含 <HARNESS> 占位符，需要渲染
      - ("outdated", old_path): 已渲染但路径过期，需要重新渲染
    """
    if not os.path.isfile(path):
        return ("missing", None)

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # 检查 1：是否有占位符
    if "<HARNESS>" in content:
        return ("placeholder", None)

    # 检查 2：路径是否过期（找常见的路径模式）
    # 业务仓 AGENTS.md 里 harness 路径通常出现在 `<HARNESS>/...` 渲染后的形态
    # 例如：`D:/Rain/project/eaglermc_harness/golden-patterns/...`
    # 用正则找所有类似 "盘符:/...eaglermc_harness" 或 "/.../eaglermc_harness" 的路径
    import re
    # Windows 形态：D:/.../eaglermc_harness 或 D:\...\eaglermc_harness
    #   排除 URL：https:/ 里的 s:/ 会被误判为盘符，故 lookbehind 排除字母
    # POSIX 形态：/.../eaglermc_harness
    #   排除 URL：:// 中第二个 / 前面是第一个 /，故 lookbehind 加 / 排除
    patterns = [
        r'(?<![A-Za-z])[A-Za-z]:[/\\][^\s`]*?eaglermc_harness(?=[/\s`"\']|$)',  # Windows（排除 URL）
        r'(?<![:/.\w])/[^\s`]*?eaglermc_harness(?=[/\s`"\']|$)',                 # POSIX（排除 URL）
    ]

    found_paths = set()
    for pattern in patterns:
        matches = re.findall(pattern, content)
        for m in matches:
            # 归一化为正斜杠形式
            normalized = m.replace("\\", "/").rstrip("/")
            found_paths.add(normalized)

    # 如果有找到路径，检查是否与当前 harness_path 一致
    if found_paths:
        for found in found_paths:
            if found != harness_path:
                return ("outdated", found)

    return ("ok", None)


def render_file(path, harness_path, check_only=False):
    """渲染单个 AGENTS.md 文件。返回 (changed, backup_path_or_None)。"""
    if not os.path.isfile(path):
        return (False, None)

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        original = f.read()

    if "<HARNESS>" not in original:
        return (False, None)

    # 替换
    rendered = original.replace("<HARNESS>", harness_path)

    if check_only:
        return (True, None)

    # 备份
    backup = path + ".bak-" + datetime.now().strftime("%Y%m%d")
    if not os.path.isfile(backup):
        with open(backup, "w", encoding="utf-8", newline="") as f:
            f.write(original)

    # 写回
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(rendered)

    return (True, backup)


def main():
    ap = argparse.ArgumentParser(description="渲染仓级 AGENTS.md 里的 `<HARNESS>` 占位符")
    ap.add_argument("--check", action="store_true", help="只检查，不写文件")
    a = ap.parse_args()

    harness = find_root()
    if not harness:
        print("✗ 找不到 harness 工程根", file=sys.stderr)
        return 2

    harness_path = harness.replace("\\", "/").rstrip("/")
    repos = read_repos(harness)

    if not repos:
        print("  config/harness.local.yaml 里没有 repos 段", file=sys.stderr)
        return 2

    print("=== 渲染仓级 AGENTS.md 的 `<HARNESS>` 占位符 ===")
    print("harness 路径：", harness_path)
    print("客户仓数量：", len(repos))
    print()

    changed = []
    unchanged = []
    missing = []
    outdated = []

    for key, repo_path in sorted(repos.items()):
        agents_md = os.path.join(repo_path, "AGENTS.md")

        if a.check:
            # --check 模式：用增强的检查逻辑
            status, detail = check_file(agents_md, harness_path)
            if status == "placeholder":
                changed.append((key, agents_md, None))
            elif status == "outdated":
                outdated.append((key, agents_md, detail))
            elif status == "missing":
                missing.append(key)
            else:  # ok
                unchanged.append(key)
        else:
            # 渲染模式：已退役，打印警告后退出
            print("⚠ 渲染模式已退役（2026-10-12）。", file=sys.stderr)
            print("  指针写法落地后，<HARNESS> 占位符就是终态。", file=sys.stderr)
            print("  渲染成绝对路径反而违反机器无关原则（check ⑦ 会拦）。", file=sys.stderr)
            print("  如需检查路径是否过期：python sandbox/render_harness_placeholders.py --check", file=sys.stderr)
            return 2

    # 报告
    if changed:
        print("需要渲染的仓 (%d):" % len(changed))
        for key, path, backup in changed:
            if a.check:
                print("  %s: %s（含占位符）" % (key, path))
            else:
                print("  %s: %s → 备份 %s" % (key, path, os.path.basename(backup)))
        print()

    if outdated:
        print("路径过期的仓 (%d):" % len(outdated))
        for key, path, old_path in outdated:
            print("  %s: %s" % (key, path))
            print("    当前: %s" % old_path)
            print("    期望: %s" % harness_path)
        print()

    if unchanged:
        print("已渲染 / 无占位符的仓 (%d): %s" % (len(unchanged), ", ".join(unchanged)))
        print()

    if missing:
        print("缺 AGENTS.md 的仓 (%d): %s" % (len(missing), ", ".join(missing)))
        print()

    if a.check:
        # 2026-10-12 退役后：--check 只查「路径过期」，占位符视为合规（指针写法终态）
        total_issues = len(outdated)
        if total_issues:
            print("结论：有 %d 个仓路径过期（已渲染成旧绝对路径，需回滚到占位符）"
                  % len(outdated))
            print("  回滚：python sandbox/rollback_harness_placeholders.py")
            return 1
        else:
            n_placeholder = len(changed)  # changed 在 --check 模式下收集的是含占位符的仓
            print("结论：全部合规（%d 个占位符终态，%d 个已正确，%d 个路径过期）"
                  % (n_placeholder, len(unchanged), len(outdated)))
            return 0
    else:
        if changed:
            print("结论：已渲染 %d 个仓" % len(changed))
        else:
            print("结论：无需渲染")
        return 0


if __name__ == "__main__":
    sys.exit(main())
