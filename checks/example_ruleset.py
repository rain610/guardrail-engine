# -*- coding: utf-8 -*-
"""AgentGuard 示例规则集 —— 3 条通用规则，演示框架用法。

规则：
  NO-CONSOLE   禁止 console.log 残留（前端通用）
  NO-HARDIP    禁止硬编码 IP 地址（跨端通用）
  EOL-UNIFIED  行尾统一（CRLF 或 LF，按配置）

用法：被 example_code_check.py 调用，不直接运行。

`NO-CONSOLE` / `NO-HARDIP` 带 `see` 指针，演示「拦截时顺带指路」：命中输出会多一行
`📖 See: golden-patterns/patterns/...`，指向可照抄的代码骨架，而不是只给一句禁令。

误报边界（示例规则是启发式，不是生产规则集）：
  · 注释里的内容不算命中（复用 `rule_base.comment_spans`，另补 Python 的 `#`）；
  · 字符串里的 `console.log` 降级为 warn —— 它可能是文档/日志文案，不是调试残留；
  · `1.2.3.4` 这类版本号不判 IP（看 `version=` / `v=` 等上下文），且每段必须 ≤255；
  · 多行块注释的**续行**、跨行字符串不做状态跟踪（单行启发式）。
"""
import re

import rule_base


_STR_QUOTES = ('"', "'", "`")

# `version: "1.2.3.4"` / `const v = "1.2.3.4"` / `appVersion = "..."` 的**前缀**上下文。
# 只看命中点之前的文本：命中后紧跟的说明文字不改变它是版本号的事实。
_VERSION_CTX = re.compile(
    r'(?i)(?:(?:version|semver)\b|(?<![A-Za-z])ver\b|(?<![A-Za-z])v)'
    r'["\']?\s*[=:]?\s*["\']?\s*$'
)


def _string_spans(line):
    """本行字符串字面量区间（含引号）；未在本行收尾的串算到行尾。"""
    spans, i, n, quote, start, esc = [], 0, len(line), "", 0, False
    while i < n:
        c = line[i]
        if quote:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                spans.append((start, i + 1))
                quote = ""
            i += 1
            continue
        if c in _STR_QUOTES:
            quote, start = c, i
        i += 1
    if quote:
        spans.append((start, n))
    return spans


def _comment_spans(line, filepath):
    """注释区间：`//` `/* */` `<!-- -->` 交给 rule_base，Python 的 `#` 在这里补。"""
    spans = list(rule_base.comment_spans(line))
    if str(filepath).lower().endswith(".py"):
        strings = _string_spans(line)
        i = 0
        while i < len(line):
            inside = next(((s, e) for s, e in strings if s <= i < e), None)
            if inside:
                i = inside[1]
                continue
            if line[i] == "#":
                spans.append((i, len(line)))
                break
            i += 1
    return spans


def _code_only(line, filepath):
    """去掉注释、保留列位置（长度不变，命中下标仍可对回原行）。"""
    spans = _comment_spans(line, filepath)
    if not spans:
        return line
    chars = list(line)
    for start, end in spans:
        for i in range(max(0, start), min(len(chars), end)):
            chars[i] = " "
    return "".join(chars)


def _valid_ipv4(text):
    parts = text.split(".")
    return len(parts) == 4 and all(p.isdigit() and int(p) <= 255 for p in parts)


class RegexRule(rule_base.Rule):
    """单行正则规则 —— 统一 Rule 协议的适配器（与生产规则同一套字段与返回类型）。

    规则集里只有这一种规则：`check(ctx)` 产出 `Finding`，严重级走 `Severity`；
    字符串上下文降级用 Finding.severity 单条覆盖，其余交给 `Rule.run` 的规则级判定。
    """
    def __init__(self, id, pattern, message, fix_hint="", heuristic=False,
                 see="", string_severity=None, version_exempt=False, validator=None):
        self.id = id
        self.name = message
        self.pattern = re.compile(pattern)
        self.message = message
        self.fix_hint = fix_hint
        # 范式指路：仓库相对路径，指向一份已验收的写法（golden-patterns/）。
        # 留空 = 只拦不指路；Rule.run 会把它盖到每条 Finding 上。
        self.see = see
        # 单行正则类规则只能挡住已知写法，不能保证覆盖多行/别名/生成代码 —— 标出来，
        # 让能力边界出现在自检报告里，而不是留给使用者自己猜。
        self.heuristic = heuristic
        # 命中落在字符串字面量里时改用这个级别（None = 与规则级相同）。
        self.string_severity = string_severity
        # 命中点前面出现 version/ver/v = 时视为版本号（只对 IP 这类"长得像版本"的规则启用）。
        self.version_exempt = version_exempt
        # 命中文本的额外合法性判定（返回 False 的命中直接忽略）。
        self.validator = validator

    def check(self, ctx):
        problems = []
        for i, line in enumerate(ctx.content.splitlines(), 1):
            code = _code_only(line, ctx.path)
            if not code or not self.pattern.search(code):
                continue
            strings = _string_spans(code)
            for m in self.pattern.finditer(code):
                if self.validator and not self.validator(m.group(0)):
                    continue
                if self.version_exempt and _VERSION_CTX.search(code[:m.start()]):
                    continue
                severity = None
                if self.string_severity and any(s <= m.start() < e for s, e in strings):
                    severity = self.string_severity
                problems.append(rule_base.Finding(
                    self.id, self.message, severity=severity, line=i, fix=self.fix_hint))
        return problems


class EolUnifiedRule(rule_base.Rule):
    """行尾混用：整个文件的属性，没有可归因的行号（file_level 免于按行过滤）。"""
    id = "EOL-UNIFIED"
    name = "行尾不统一（混用 CRLF 和 LF）"
    file_level = True

    def check(self, ctx):
        has_crlf = "\r\n" in ctx.content
        has_lf_only = bool(re.search(r'(?<!\r)\n', ctx.content))
        if has_crlf and has_lf_only:
            return [rule_base.Finding(self.id, "行尾混用 CRLF 和 LF", line=None,
                                      fix="统一为一种行尾格式")]
        return []


EXAMPLE_RULES = [
    RegexRule(
        id="NO-CONSOLE",
        pattern=r'console\.log\s*\(',
        message="禁止 console.log 残留（调试代码不应进入提交）",
        fix_hint="删除 console.log 行，改用统一日志封装（见范式）",
        heuristic=True,
        see="golden-patterns/patterns/cross-cutting/logging.md",
        # 可能只是文档/日志文案：先提示，不阻断。
        string_severity=rule_base.Severity.WARN,
    ),
    RegexRule(
        id="NO-HARDIP",
        pattern=r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\.?\d)',
        message="禁止硬编码 IP 地址（用配置或环境变量替代）",
        fix_hint="把地址移入 .env / appsettings / 配置中心，由统一配置入口读取",
        heuristic=True,
        see="golden-patterns/patterns/cross-cutting/config-and-env.md",
        version_exempt=True,
        validator=_valid_ipv4,
    ),
    EolUnifiedRule(),
]
