# -*- coding: utf-8 -*-
"""AgentGuard 示例规则集 —— 3 条通用规则，演示框架用法。

规则：
  NO-CONSOLE   禁止 console.log 残留（前端通用）
  NO-HARDIP    禁止硬编码 IP 地址（跨端通用）
  EOL-UNIFIED  行尾统一（CRLF 或 LF，按配置）

用法：被 example_code_check.py 调用，不直接运行。

`NO-CONSOLE` / `NO-HARDIP` 带 `see` 指针，演示「拦截时顺带指路」：命中输出会多一行
`📖 See: golden-patterns/patterns/...`，指向可照抄的代码骨架，而不是只给一句禁令。
"""
import os
import re


class Rule:
    """单条规则。"""
    def __init__(self, rule_id, severity, pattern, message, fix_hint="", heuristic=False,
                 see=""):
        self.rule_id = rule_id
        self.severity = severity       # "block" = 阻断 / "warn" = 提示
        self.pattern = re.compile(pattern)
        self.message = message
        self.fix_hint = fix_hint
        # 范式指路：仓库相对路径，指向一份已验收的写法（golden-patterns/）。
        # 留空 = 只拦不指路；由 example_code_check.py 在命中行后打印 `📖 See:`。
        self.see = see
        # 单行正则类规则只能挡住已知写法，不能保证覆盖多行/别名/生成代码 —— 标出来，
        # 让能力边界出现在自检报告里，而不是留给使用者自己猜。
        self.heuristic = heuristic

    def check(self, filepath, content):
        problems = []
        for i, line in enumerate(content.splitlines(), 1):
            if self.pattern.search(line):
                problems.append((i, line.strip(), self.message, self.fix_hint))
        return problems


EXAMPLE_RULES = [
    Rule(
        rule_id="NO-CONSOLE",
        severity="block",
        pattern=r'console\.log\s*\(',
        message="禁止 console.log 残留（调试代码不应进入提交）",
        fix_hint="删除 console.log 行，改用统一日志封装（见范式）",
        heuristic=True,
        see="golden-patterns/patterns/cross-cutting/logging.md",
    ),
    Rule(
        rule_id="NO-HARDIP",
        severity="block",
        pattern=r'\b(?:\d{1,3}\.){3}\d{1,3}\b',
        message="禁止硬编码 IP 地址（用配置或环境变量替代）",
        fix_hint="把地址移入 .env / appsettings / 配置中心，由统一配置入口读取",
        heuristic=True,
        see="golden-patterns/patterns/cross-cutting/config-and-env.md",
    ),
    Rule(
        rule_id="EOL-UNIFIED",
        severity="block",
        pattern=r'',  # 特殊处理：不用正则，在 check 里单独判
        message="行尾不统一（混用 CRLF 和 LF）",
        fix_hint="统一为 CRLF（Windows 项目）或 LF（Unix 项目）",
    ),
]


def check_eol(filepath, content):
    """EOL 检查单独处理：检测混用。"""
    has_crlf = "\r\n" in content
    has_lf_only = bool(re.search(r'(?<!\r)\n', content))
    if has_crlf and has_lf_only:
        return [(0, "", "行尾混用 CRLF 和 LF", "统一为一种行尾格式")]
    return []
