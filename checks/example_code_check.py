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
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from example_ruleset import EXAMPLE_RULES
import rule_base

# 本脚本只按文件内容判定；mode 由 run_gate 透传进来，脚本按引擎契约接收（示例不区分口径）。
# 显式声明，让 run_gate --validate 能把「写了但没人读」的键当场拦下（返回 None 会跳过校验）。
CONFIG_KEYS = frozenset({"mode"})


def _read_content(filepath):
    """读待查内容：走 `rule_base.read_bytes`，提交期才会读到**暂存区**而不是工作区。

    直接 `open()` 会绕过 pre-commit 展开的 overlay：暂存坏版本 + 工作区改回好版本时，
    门禁看的是工作区 → rc=0 放过。返回原始字节解出的文本（不经 universal newlines），
    EOL 规则因此仍能看见 CRLF。
    """
    return rule_base.read_bytes(filepath).decode("utf-8", errors="replace")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="AgentGuard 示例门禁")
    ap.add_argument("--repo", required=True, help="目标仓库路径")
    ap.add_argument("--all", action="store_true", help="全量扫描")
    # run_gate 的 `--mode` 会原样透传给脚本；示例规则不区分口径，接受它是为了
    # 让 run_gate <gate> <文件> --mode loose 不至于在 argparse 处 rc=2。
    ap.add_argument("--mode", choices=("strict", "loose"),
                    help="判定口径（示例规则不区分，接受以保持引擎契约）")
    # run_gate 对已登记的端脚本会注入 --gate；示例脚本也接受，供直跑时把统计记到正确 gate 名下。
    ap.add_argument("--gate", default="", help="gate 名（仅用于覆盖度统计的归属）")
    ap.add_argument("files", nargs="*", help="要检查的文件（相对仓库根）")
    a = ap.parse_args()

    repo = os.path.abspath(a.repo)
    if not os.path.isdir(repo):
        print(f"仓库路径不存在: {repo}", file=sys.stderr)
        return 2

    # 收集目标文件
    # 「无位置参数 = 整仓全量」是 run_gate 三条分支共用的判据（L17 守卫也按它记账），
    # 所以 --repo <目录> 单独出现时必须照此执行，不能退回"缺参数"错误。
    if a.all or not a.files:
        targets = []
        for dirpath, _, filenames in os.walk(repo):
            for f in filenames:
                if f.endswith((".js", ".ts", ".vue", ".jsx", ".tsx", ".py", ".cs")):
                    targets.append(os.path.join(dirpath, f))
    else:
        # 点名了文件就必须查：路径写错/文件已删时**不能**悄悄退化成"无目标文件 → 通过"。
        # run_gate 把提交范围解析成位置参数传进来，这里一漏就是整次提交没查。
        pairs = [(f, f if os.path.isabs(f) else os.path.join(repo, f)) for f in a.files]
        missing = [f for f, p in pairs if not os.path.isfile(p)]
        if missing:
            for f in missing:
                print(f"❌ 目标文件不存在: {f}", file=sys.stderr)
            print("门禁未执行：调用方点名的文件必须存在且可读（拒绝静默放行）", file=sys.stderr)
            return 2
        targets = [p for _, p in pairs]

    if not targets:
        print("无目标文件")
        return 0

    total_problems = 0
    total_warns = 0
    scope = "all" if (a.all or not a.files) else "targeted"

    for filepath in targets:
        rel = os.path.relpath(filepath, repo)
        try:
            content = _read_content(filepath)
        except Exception as exc:
            # 读不到 ≠ 没问题：吞掉异常会让"权限不足 / 文件被占 / 编码炸了"一律变成通过。
            print(f"❌ 无法读取 {rel}: {exc}", file=sys.stderr)
            print("门禁未执行：文件读取失败不能当作通过（拒绝静默放行）", file=sys.stderr)
            return 2

        lines = content.splitlines()
        ctx = rule_base.CheckContext(path=Path(filepath), rel=rel, content=content,
                                     repo=repo, mode=a.mode or "strict")

        # 全部规则走统一入口：applicable + check + 规则级 severity 盖章。
        for rule in EXAMPLE_RULES:
            for f in rule.run(ctx):
                reason = (rule_base.inline_waiver(f.rule_id, f.line, lines)
                          if f.line is not None else None)
                if reason:
                    rule_base.WAIVED_DETAILS.append((f.rule_id, f.line, reason))
                    rule_base.bump(f.rule_id, "waived_inline")
                    continue
                loc = f"{rel}:{f.line}" if f.line is not None else rel
                if f.severity == rule_base.Severity.ERROR:
                    print(f"  ❌ {loc} [{f.rule_id}] {f.message}")
                    if f.fix:
                        print(f"     FIX: {f.fix}")
                    if f.see:
                        print(f"     📖 See: {f.see}")
                    total_problems += 1
                else:
                    print(f"  ⚠️  {loc} [{f.rule_id}] {f.message}")
                    if f.see:
                        print(f"     📖 See: {f.see}")
                    total_warns += 1
                rule_base.bump(f.rule_id, {rule_base.Severity.ERROR: "error",
                                           rule_base.Severity.WARN: "warn"}.get(
                                               f.severity, "info"))

    waived = rule_base.waiver_summary()
    if waived:
        print(waived)

    rule_base.emit_stats(gate=a.gate or "", scope=scope, files=len(targets))
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
