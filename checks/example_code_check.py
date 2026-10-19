#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AgentGuard 示例门禁 —— 演示框架用法（3 条通用规则）。

用法：
    python checks/example_code_check.py --repo <仓库路径> [文件...]
    python checks/example_code_check.py --repo <仓库路径> --all

退出码：0=通过，1=有阻断级问题
"""
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from example_ruleset import EXAMPLE_RULES, check_eol


def main():
    import argparse
    ap = argparse.ArgumentParser(description="AgentGuard 示例门禁")
    ap.add_argument("--repo", required=True, help="目标仓库路径")
    ap.add_argument("--all", action="store_true", help="全量扫描")
    ap.add_argument("files", nargs="*", help="要检查的文件（相对仓库根）")
    a = ap.parse_args()

    repo = os.path.abspath(a.repo)
    if not os.path.isdir(repo):
        print(f"仓库路径不存在: {repo}", file=sys.stderr)
        return 2

    # 收集目标文件
    if a.all:
        targets = []
        for dirpath, _, filenames in os.walk(repo):
            for f in filenames:
                if f.endswith((".js", ".ts", ".vue", ".jsx", ".tsx", ".py", ".cs")):
                    targets.append(os.path.join(dirpath, f))
    elif a.files:
        targets = [os.path.join(repo, f) for f in a.files if os.path.isfile(os.path.join(repo, f))]
    else:
        print("无文件参数，加 --all 全量扫描或传入文件路径", file=sys.stderr)
        return 2

    if not targets:
        print("无目标文件")
        return 0

    total_problems = 0
    total_warns = 0

    for filepath in targets:
        try:
            with open(filepath, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            continue

        rel = os.path.relpath(filepath, repo)

        # 正则规则
        for rule in EXAMPLE_RULES:
            if not rule.pattern.pattern:  # EOL 规则跳过
                continue
            for lineno, line, msg, fix in rule.check(filepath, content):
                if rule.severity == "block":
                    print(f"  ❌ {rel}:{lineno} [{rule.rule_id}] {msg}")
                    if fix:
                        print(f"     FIX: {fix}")
                    total_problems += 1
                else:
                    print(f"  ⚠️  {rel}:{lineno} [{rule.rule_id}] {msg}")
                    total_warns += 1

        # EOL 检查
        for lineno, line, msg, fix in check_eol(filepath, content):
            print(f"  ❌ {rel} [EOL-UNIFIED] {msg}")
            if fix:
                print(f"     FIX: {fix}")
            total_problems += 1

    print(f"\n检查 {len(targets)} 个文件", end="")
    if total_problems:
        print(f"，❌ {total_problems} 个阻断级问题" + (f"，{total_warns} 条警告" if total_warns else ""))
        return 1
    elif total_warns:
        print(f"，✅ 无阻断级问题（{total_warns} 条警告）")
        return 0
    else:
        print("，✅ 全部通过")
        return 0


if __name__ == "__main__":
    sys.exit(main())
