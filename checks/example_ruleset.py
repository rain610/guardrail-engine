# -*- coding: utf-8 -*-
"""AgentGuard 示例规则集 —— 3 条通用规则，演示框架用法。

规则：
  NO-CONSOLE   禁止 console.log 残留（前端通用）
  NO-HARDIP    禁止硬编码 IP 地址（跨端通用）
  EOL-UNIFIED  行尾统一（CRLF 或 LF，按配置）

用法：被 example_code_check.py 调用，不直接运行。
"""
import os
import re


class Rule:
    """单条规则。"""
    def __init__(self, rule_id, severity, pattern, message, fix_hint=""):
        self.rule_id = rule_id
        self.severity = severity       # "block" = 阻断 / "warn" = 提示
        self.pattern = re.compile(pattern)
        self.message = message
        self.fix_hint = fix_hint

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
        fix_hint="删除 console.log 行，或用条件日志封装",
    ),
    Rule(
        rule_id="NO-HARDIP",
        severity="block",
        pattern=r'\b(?:\d{1,3}\.){3}\d{1,3}\b',
        message="禁止硬编码 IP 地址（用配置或环境变量替代）",
        fix_hint="将 IP 提取到配置文件或环境变量",
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
