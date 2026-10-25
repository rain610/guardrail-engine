# -*- coding: utf-8 -*-
"""旧版 harness.yaml 的兼容解析器（主格式已是 TOML，见 `_config._load_toml`）。

从 `_config.py` 拆出：这里只做词法/缩进解析，不做文件读取、不持有告警状态；
超纲语法经注入的 `note` 回调出声（只出声、不改语义 —— 与拆分前逐字相同）。
"""

from _errors import ConfigError


# ---------- 超纲语法探测（2026-09-16 剖析报告第六节「mini-yaml 能力边界」） ----------
# 背景：本解析器只支持 harness.yaml 用到的那一小撮子集。超出子集的写法**不报错**，
# 而是静默产出**看起来正常**的错误数据 —— 这比报错危险：写 `pwd: "a # b"` 得到 `"a`，
# 门禁照跑、路径照错、结论照出，没有任何一处会红。
# 处置：探测到超纲写法就出声（复用 _warn，stderr + 去重）。**只出声不改语义** ——
# 修语义等于在没需求的情况下实现 YAML，与「静默失效改成出声」的目标不同。
_QUOTES = ('"', "'")


def _open_quote_at(s, pos):
    """返回 `pos` 处所处**未闭合引号**的引号字符，没有则 None（从行首顺序扫描）。

    故意不用"数引号奇偶"那种写法：`notes: it's a # c` 里那个撇号会被奇偶法当成开引号。
    """
    q = None
    for ch in s[:pos]:
        if q is None:
            if ch in _QUOTES:
                q = ch
        elif ch == q:
            q = None
    return q


def _strip_comment(s, lineno=None, note=None):
    """去行内注释。顺带探测两处「与真 YAML 不一致」的静默截断（2026-09-16）。"""
    i = s.find("#")
    if i < 0:
        return s.rstrip()
    q = _open_quote_at(s, i)
    if q and q in s[i:]:
        # 只有当 `#` 之后**确实存在配对的闭引号**时才算"引号内" —— 否则
        # `notes: it's a # comment` 这种值里带撇号的合法写法会被误报（实测过）。
        # 实测真阳性 `pwd: "a # b"` → `"a`：既截断又留下半个引号。
        note(s, "`#` 在引号内（会被静默截断并残留引号）", lineno)
    elif i > 0 and not s[i - 1].isspace():
        # YAML 里 `#` 必须**前面是空白或行首**才算注释；`http://x/y#frag` 是字面量。
        # 实测 `u: http://x/y#frag` → `http://x/y`：含 # 的 URL / 口令尾巴被静默砍掉。
        note(s, "`#` 前无空白，真 YAML 里不是注释（会被静默截断）", lineno)
    return s[:i].rstrip()


def _parse_scalar(s, lineno=None, note=None):
    s = _strip_comment(s.strip(), lineno, note)
    if s == "" or s == "true":
        return True if s == "true" else (s if s else "")
    if s == "false":
        return False
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        # 实测 `run_on: [[a, b], c]` → ['[a', 'b]', 'c']：内层方括号被当普通字符平铺切开
        if "[" in inner or "]" in inner:
            note(s, "嵌套 flow 列表（本解析器只按逗号平铺切分）", lineno)
        items = [x.strip() for x in inner.split(",") if x.strip()]
        # 实测 `lst: ["a", "b"]` → ['"a"', '"b"']：引号原样留在值里
        if any(len(x) > 1 and x[0] in _QUOTES and x[-1] == x[0] for x in items):
            note(s, "flow 列表内的带引号元素（引号不会被剥掉）", lineno)
        return items if inner else []
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    return s


def _is_block_scalar(v):
    """`|` / `>` 及其缩进指示符（`|-` / `>+` / `|2-`…）。"""
    return bool(v) and v[0] in ("|", ">") and all(c in "-+0123456789" for c in v[1:])


def _parse_block(lines, idx, indent, note=None):
    result = None
    i = idx
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.strip().startswith("#"):
            i += 1
            continue
        cur = len(line) - len(line.lstrip())
        if cur <= indent:
            break
        content = _strip_comment(line.strip(), i + 1, note)
        if content.startswith("- "):
            # 同级"先列表后映射"：旧实现直接 `result[k] = …` →
            # TypeError: list indices must be integers（实测）。文件本身就不合法，转 ConfigError。
            if isinstance(result, dict):
                raise ConfigError("第 %d 行：本层已按**映射**解析，这里又出现列表项"
                                  "（同名层级列表与映射混排，真 YAML 也不允许）：%s"
                                  % (i + 1, line.strip()[:80]))
            if result is None:
                result = []
            item = content[2:].strip()
            if ":" in item and not item.startswith(("'", '"')):
                k, v = item.split(":", 1)
                # 实测 `- C:/x/y` → {'C': '/x/y'}：盘符冒号被当成 `键: 值`。
                # 判据必须带"冒号后无空格"—— 否则会把合法的行内映射 `- a: /path` 一起误报
                # （`a: /path` 的冒号后**有**空格，`C:/x/y` 没有，这是两者的分界）。
                if (len(k.strip()) == 1 and k.strip().isalpha()
                        and v[:1] and not v[:1].isspace() and v.strip()[:1] in ("/", "\\")):
                    note(line, "列表项里的盘符路径被当成 `键: 值` 映射", i + 1)
                m = {}
                m[k.strip()] = _parse_scalar(v, i + 1, note)
                result.append(m)
            else:
                result.append(_parse_scalar(item, i + 1, note))
            i += 1
        else:
            # 同级"先映射后列表"：旧实现 `result.append` → AttributeError（实测）
            if isinstance(result, list):
                raise ConfigError("第 %d 行：本层已按**列表**解析，这里又出现映射键"
                                  "（同级列表与映射混排，真 YAML 也不允许）：%s"
                                  % (i + 1, line.strip()[:80]))
            if result is None:
                result = {}
            if content == "-":
                # 实测裸 `-` → {'-': None}：被当成一个名叫 "-" 的键
                note(line, "裸 `-`（空列表项）被当成 `键: 值`", i + 1)
            k, _, v = content.partition(":")
            k = k.strip()
            v = v.strip()
            if v == "":
                child, i = _parse_block(lines, i + 1, cur, note)
                result[k] = child
            else:
                # 实测 `notes: |` + 缩进续行 → 续行的每一行都变成**同级新键**
                if _is_block_scalar(v):
                    note(line, "块标量（多行字符串 `|` / `>`）", i + 1)
                result[k] = _parse_scalar(v, i + 1, note)
                i += 1
    return result, i

def parse(lines, note):
    """把 YAML 行列表解析成 dict / list；超纲写法经 `note(line, why, lineno)` 出声。

    `note` 由调用方注入（`_config._note_unsupported` → `_warn`）：解析器搬出去之后，
    同进程告警去重集合仍然只有 `_config._warned` 一份，行为与拆分前一致。
    """
    return _parse_block(lines, 0, -1, note)[0]
