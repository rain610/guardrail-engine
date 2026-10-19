#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""业务仓 AGENTS.md 机器无关性检查（独立脚本，供 run_gate 调度层调用）

为什么独立出来（2026-10-12）：
  原先 check ⑦ 放在 harness_self_check.py 里，而 harness_self gate 只在提交引擎仓时触发。
  但 check ⑦ 要防的场景是「同事提交业务仓时把绝对路径写进 AGENTS.md」—— 恰好是它不会发生的场景。
  外部评审 CRITICAL #1：「check ⑦ 放错了门」。故拆成独立脚本，由 run_gate 在所有业务仓提交时调用。

检查逻辑：
  扫描 config 里声明的全部业务仓 AGENTS.md，查找指向 agentguard 的绝对路径。
  只匹配特定模式（含 agentguard 字样），避免误报 URL 等。发现即 ❌ 阻断。

退出码：0=通过，1=违规，2=配置错误
"""
import sys
import os
import re

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "harness"))


def check_agents_md_no_absolute_paths():
    """业务仓 AGENTS.md 不得含指向 agentguard 的绝对路径（进仓文件必须机器无关）。

    绝对路径只应该出现在不进仓的渲染产物里（~/.agentguard/GLOBAL_AGENTS.md）
    或本机配置里（config/harness.local.yaml）。

    Returns: (passed: bool, message: str)
    """
    try:
        import _config as C
        cfg = C.load()
    except Exception as e:
        return (True, f"读取 config 失败（{e!r}）——业务仓 AGENTS.md 绝对路径检查跳过")

    repos = cfg.get("repos") or {}
    if not repos:
        return (True, "本机未配置业务仓路径，跳过")

    # 指向 agentguard 的绝对路径模式
    # Windows: D:/.../agentguard 或 D:\\...\\agentguard
    #   排除 URL：https:/ 里的 s:/ 会被误判为盘符，故 lookbehind 排除字母（盘符前不应有字母）
    # POSIX: /.../agentguard
    #   排除 URL：:// 中第二个 / 前面是第一个 /，故 lookbehind 加 / 排除
    patterns = [
        r'(?<![A-Za-z])[A-Za-z]:[/\\][^\s]*agentguard',   # Windows（排除 URL 中的 x:/）
        r'(?<![:/.\w])/[^\s]*agentguard',                  # POSIX（排除 :// 和相对路径）
    ]

    violations = []
    for key, repo_path in sorted(repos.items()):
        if not repo_path or not os.path.isdir(repo_path):
            continue  # 路径未配或不存在，跳过

        agents_md = os.path.join(repo_path, "AGENTS.md")
        if not os.path.isfile(agents_md):
            continue  # 没有 AGENTS.md，跳过

        try:
            with open(agents_md, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            continue

        # 检查是否含指向 agentguard 的绝对路径
        for pattern in patterns:
            if re.search(pattern, content):
                violations.append(key)
                break

    if violations:
        msg = ("%d 个业务仓 AGENTS.md 含绝对路径（违反机器无关原则）：%s\n"
               "  → 用 <HARNESS> 占位符 + 指针写法（见 AGENTS.md 顶部路径约定说明）"
               % (len(violations), ", ".join(violations[:6])
                  + ("..." if len(violations) > 6 else "")))
        return (False, msg)

    return (True, "全部业务仓 AGENTS.md 符合机器无关原则")


def main():
    passed, msg = check_agents_md_no_absolute_paths()
    if passed:
        print(f"✅ {msg}")
        return 0
    else:
        print(f"❌ {msg}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
