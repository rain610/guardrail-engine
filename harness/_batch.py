# -*- coding: utf-8 -*-
"""命令行长度防护：把文件列表切成子进程能接受的批次。

背景（2026-11-03 实测）：Windows CreateProcess 的命令行上限是 32767 个字符，
`subprocess.run([...] + files)` 超限时抛 `OSError [WinError 206] 文件名或扩展名太长`。
端仓一次大 merge / 批量格式化可产生数百个 staged 文件，整串拼进 argv 直接崩；
崩溃发生在门禁跑起来之前，外层分发器却把它当成"门禁未通过（rc=1）"，把环境/参数
问题误报成业务代码问题（实测 400 个约 100 字符的路径 = 38071 字符必现）。

预算取 24000（Windows）/ 100000（POSIX）：给解释器路径、gate 名、--repo 等固定参数
以及 CreateProcess 的引号转义留出余量。批次在**调用 run_gate 之前**切好 —— run_gate
再把这些文件传给检查脚本，命令行只会多出几十字节，同一预算仍然安全。

单个路径本身超预算时不做进一步拆分（路径不能再分），让它单独成批，
由底层报出真实错误，而不是被这里静默丢弃。
"""
import os

_CMD_BUDGET = 24000 if os.name == "nt" else 100000


def _arg_len(arg):
    # Windows 命令行按 UTF-16 code unit 计数（astral 字符占 2）；
    # +3 = 分隔空格与可能的引号/转义余量。
    if os.name == "nt":
        return len(arg.encode("utf-16-le")) // 2 + 3
    return len(arg) + 3


def split(files, fixed_args=(), budget=None):
    """按估算的命令行长度切批，保持原顺序；fixed_args 是每批都会带上的固定参数。"""
    budget = _CMD_BUDGET if budget is None else budget
    base = sum(_arg_len(a) for a in fixed_args)
    cur, size = [], base
    for f in files:
        n = _arg_len(f)
        if cur and size + n > budget:
            yield cur
            cur, size = [], base
        cur.append(f)
        size += n
    if cur:
        yield cur
