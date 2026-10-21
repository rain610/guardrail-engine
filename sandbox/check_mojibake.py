# -*- coding: utf-8 -*-
"""防乱码检查：扫描交付文本中的编码损伤字符（U+FFFD、PUA 私用区、典型双层编码 token）。

背景（2026-09-14）：Windows 下 PowerShell 管道按 cp936 解码 UTF-8 输出、以及
Windows CLI（GBK）输出被按 UTF-8 采集时，会向证据/文档里引入乱码字符。此脚本
用于提交前的自检（也是对该问题的回归锁）。

用法:
    python sandbox/check_mojibake.py [目录 ...]
未指定目录时默认扫描仓库的 docs/packaging；可额外传入目录（例如交付包"证据"目录）。

退出码: 0 = 干净；1 = 发现可疑字符。

行内豁免：行内含 "mojibake-ok" 时跳过该行（用于文档中的说明性示例，同 harness-ok 惯例）。
"""
import io
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TOKENS = ["\ufffd", "锟斤拷", "锛", "銆", "鈥", "ï¿½", "â€", "ã€"]
EXTS = {".md", ".txt", ".json", ".jsonl", ".html", ".toml", ".yaml", ".yml"}


def suspicious(ch):
    o = ord(ch)
    return 0xE000 <= o <= 0xF8FF  # Unicode 私用区（mojibake 常见残留）


def scan(path):
    hits = []
    try:
        with io.open(path, encoding="utf-8") as f:
            text = f.read()
    except (UnicodeDecodeError, OSError) as e:
        # 评审 v11 §6-4（S2，升档 P2）：原先 `return hits`（空）= **读不到 → 报干净**，
        # 而该文件仍计入 "scanned N" —— 用作交付前自检时，GBK/只读文件会被整体略过且无人知。
        # 现在记为一条 unreadable 命中（元组形状与其它命中一致，打印端无需改），并保留 docstring 语义：
        # 它是"我读不到"，不是"内容有问题"。
        return ([("unreadable", type(e).__name__, 1, str(path))]
                + hits) if path else hits
    for i, line in enumerate(text.split("\n"), 1):
        if "mojibake-ok" in line:
            continue  # 行内豁免（说明性示例）
        for t in TOKENS:
            if t in line:
                hits.append(("token", repr(t), i, line.strip()[:120]))
        for ch in line:
            if suspicious(ch):
                hits.append(("pua", hex(ord(ch)), i, line.strip()[:120]))
                break
    return hits


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dirs = sys.argv[1:] or [os.path.join(root, "docs", "packaging")]
    total = 0
    scanned = 0
    for d in dirs:
        if not os.path.isdir(d):
            print("skip (not a dir):", d)
            continue
        for dp, _, fns in os.walk(d):
            for fn in sorted(fns):
                if os.path.splitext(fn)[1].lower() not in EXTS:
                    continue
                fp = os.path.join(dp, fn)
                scanned += 1
                for kind, tok, ln, ctx in scan(fp):
                    total += 1
                    print("MOJIBAKE %s %s:%s [%s] %s" % (kind, fp, ln, tok, ctx))
    print("scanned %d files, %d hits" % (scanned, total))
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
