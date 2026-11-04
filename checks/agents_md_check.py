#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""业务仓 AGENTS.md 机器无关性检查（独立脚本，供 run_gate 调度层调用）

为什么独立出来（2026-10-12）：
  原先 check ⑦ 放在 harness_self_check.py 里，而 harness_self gate 只在提交引擎仓时触发。
  但 check ⑦ 要防的场景是「同事提交业务仓时把绝对路径写进 AGENTS.md」—— 恰好是它不会发生的场景。
  外部评审 CRITICAL #1：「check ⑦ 放错了门」。故拆成独立脚本，由 run_gate 在所有业务仓提交时调用。

两种模式（2026-10-28，第六轮评审 P1-2①）：
  --repo <目录>  只查这一个仓（提交期用）。不读 config —— 目标仓由 run_gate 解析好后传进来，
                 与"本机还配了哪些仓"无关，别的仓 AGENTS.md 坏了不该拦这次提交。
  无参数         舰队级扫描：config 里声明的全部业务仓（`run_gate --validate` 用）。

检查逻辑：
  查找 AGENTS.md 里指向本引擎（agentguard / guardrail-engine）的绝对路径。
  只匹配特定模式（含引擎名），避免误报 URL 等。发现即 ❌ 阻断。

已知边界（P3-6，评审 2026-11-04）：
  绝对路径匹配要求路径里含引擎名（agentguard / guardrail-engine）。如果引擎被克隆到
  不含这两个名字的目录（如 `/home/user/my-project/`），AGENTS.md 里写的绝对路径
  不会被本检查捕获。这是刻意取舍：放宽匹配会误报 URL 和文档示例；收紧匹配会漏掉
  改名场景。当前选择"宁可漏、不可误报"。

退出码：0=通过，1=违规，2=配置错误（判都没判成，不能读成通过）
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

# 引擎名两种写法都认（2026-10-28 第六轮评审 P1-2②）：改名后 POSIX 上的
# /opt/guardrail-engine 这类路径原先**一个字符都匹配不上**，检查形同虚设。
_ENGINE_NAME = r"(?:agentguard|guardrail[-_]engine)"
# P3-6（评审 2026-11-04）：检查面窄的已知边界 —— 绝对路径匹配要求路径里含引擎名
# （agentguard / guardrail-engine）。如果用户把引擎克隆到不含这两个名字的目录
# （如 `/home/user/my-project/`），AGENTS.md 里写 `D:/my-project/harness/...` 不会被
# 本检查捕获。这是刻意取舍：放宽匹配会误报 URL（`https://github.com/.../agentguard`）
# 和文档示例路径；收紧匹配会漏掉改名场景。当前选择"宁可漏、不可误报"，文档在此。

# Windows: D:/.../agentguard 或 D:\\...\\guardrail-engine
#   排除 URL：https:/ 里的 s:/ 会被误判为盘符，故 lookbehind 排除字母（盘符前不应有字母）
# POSIX: /.../guardrail-engine
#   排除 URL：:// 中第二个 / 前面是第一个 /，故 lookbehind 加 /
#   排除家园相对指针：`~/…`、`$HOME/…`、`${HOME}/…`、`%USERPROFILE%/…` 是机器无关写法，
#   而本仓文档（integrations/agent-rule-snippet.md）就是教业务仓写它们 —— 判成违规等于
#   一装上就拦掉按文档来的仓。
PATTERNS = [
    r'(?<![A-Za-z])[A-Za-z]:[/\\][^\s]*' + _ENGINE_NAME,
    r'(?<![:/.\w~%}])/[^\s]*' + _ENGINE_NAME,
]


def _scan(repo_path, label):
    """扫单个仓的 AGENTS.md。返回 ("ok"|"violation"|"unreadable", 说明)。"""
    agents_md = os.path.join(repo_path, "AGENTS.md")
    if not os.path.isfile(agents_md):
        return ("ok", "%s 无 AGENTS.md，跳过" % label)
    try:
        with open(agents_md, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return ("unreadable", "%s（%s）" % (label, e))
    for pattern in PATTERNS:
        if re.search(pattern, content):
            return ("violation", label)
    return ("ok", label)


def _verdict(results, ok_message):
    """把若干 _scan 结果收口成 (passed, message)。任何一项读不到 → None（rc=2）。"""
    unreadable = [msg for state, msg in results if state == "unreadable"]
    if unreadable:
        print("❌ %d 个业务仓 AGENTS.md 无法读取 —— 检查未执行：%s"
              % (len(unreadable), ", ".join(unreadable[:6])), file=sys.stderr)
        return (None, "AGENTS.md 读取失败")
    violations = [msg for state, msg in results if state == "violation"]
    if violations:
        msg = ("%d 个业务仓 AGENTS.md 含绝对路径（违反机器无关原则）：%s\n"
               "  → 用 <HARNESS> 占位符 + 指针写法（见 AGENTS.md 顶部路径约定说明）"
               % (len(violations), ", ".join(violations[:6])
                  + ("..." if len(violations) > 6 else "")))
        return (False, msg)
    return (True, ok_message)


def check_repo(repo_path):
    """单仓模式（提交期）：只查这一个仓，不碰 config。"""
    if not os.path.isdir(repo_path):
        print("❌ 目标仓目录不存在：%s —— AGENTS.md 检查无法执行" % repo_path, file=sys.stderr)
        return (None, "目标仓不存在")
    return _verdict([_scan(repo_path, repo_path)],
                    "目标仓 AGENTS.md 符合机器无关原则：%s" % repo_path)


def check_agents_md_no_absolute_paths():
    """舰队级（默认）：config 里声明的全部业务仓。

    绝对路径只应该出现在不进仓的渲染产物里（~/.guardrail-engine/GLOBAL_AGENTS.md）
    或本机配置里（config/harness.local.toml）。

    Returns: (passed, message) —— True=通过，False=违规，None=无法检查（rc=2）
    """
    try:
        import _config as C
        cfg = C.load()
    except Exception as e:
        print("❌ 读取 config 失败：%r —— 业务仓 AGENTS.md 绝对路径检查无法执行"
              % (e,), file=sys.stderr)
        return (None, "读取 config 失败")

    repos = cfg.get("repos") or {}
    if not isinstance(repos, dict):
        print("❌ config 的 repos 段不是键值映射 —— 业务仓 AGENTS.md 绝对路径检查无法执行",
              file=sys.stderr)
        return (None, "repos 段格式错误")
    if not repos:
        return (True, "本机未配置业务仓路径，跳过")

    results = []
    for key, repo_path in sorted(repos.items()):
        if not repo_path or not os.path.isdir(repo_path):
            continue  # 路径未配或不存在，跳过
        results.append(_scan(repo_path, key))
    return _verdict(results, "全部业务仓 AGENTS.md 符合机器无关原则")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    repo = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--repo":
            if i + 1 >= len(argv):
                print("❌ --repo 缺参数值 —— AGENTS.md 检查无法执行", file=sys.stderr)
                return 2
            repo = argv[i + 1]
            i += 2
            continue
        # 不认识的参数不做猜测（与 run_gate 的未知旗标口径一致）：静默忽略会让
        # 调用方以为单仓模式生效，实际却跑了舰队级扫描。
        print("❌ 未知参数：%s —— AGENTS.md 检查无法执行" % a, file=sys.stderr)
        return 2

    passed, msg = (check_repo(repo) if repo is not None
                   else check_agents_md_no_absolute_paths())
    if passed is True:
        print(f"✅ {msg}")
        return 0
    if passed is False:
        print(f"❌ {msg}")
        return 1
    print(f"❌ {msg}（无法检查 ≠ 通过，按配置错误处理）")
    return 2


if __name__ == "__main__":
    sys.exit(main())
