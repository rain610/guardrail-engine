#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机器推导"门禁入口脚本"清单（doctor.sh 与测试共用同一来源，避免手工清单漂移）。

背景（2026-09-13 外部评审 v3 H1）：入口清单原先**手工列举**，于是新增入口（钩子 shim）
天然不在清单里 —— 同一个"GBK 控制台打印中文崩掉/乱码"的坑已经出现三次：
auto-gate（第三轮）→ 钩子正本（第七轮）→ shim（本轮）。手工清单就是这三次的共同形状。

判据（可机械执行）：
  候选 = checks/*.py + harness/*.py + harness/**/*.py + integrations/hooks/*.py + sandbox/*.py + tests/*.py
  需要兜底 = 候选里**会输出**（print / sys.stdout.write / sys.stderr.write）**且含非 ASCII 字面量**的文件
  这些文件必须含 `reconfigure`（UTF-8 兜底），否则 GBK/ASCII 宿主管道下会崩或乱码。

用法：
    python sandbox/list_entries.py            # 打印清单
    python sandbox/list_entries.py --check    # 体检；缺失 → 退出码 1
    python sandbox/list_entries.py --json
"""
import argparse
import glob
import io
import pathlib
import json
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GLOBS = ("checks/*.py", "harness/*.py", "harness/**/*.py",
         "integrations/hooks/*.py", "sandbox/*.py", "tests/*.py",
         "agentguard/*.py")   # 打包路线壳（2026-09-14）：CLI/MCP 入口同受 UTF-8 兜底检查


def candidates():
    files = []
    for pat in GLOBS:
        files += glob.glob(os.path.join(ROOT, pat), recursive=True)
    return sorted({f for f in files if os.path.isfile(f) and "__pycache__" not in f})


def _read_text(path):
    """读一个**已经枚举到**的文件；它在枚举之后消失时返回 None，而不是抛异常。

    为什么需要（2026-09-16 实测）：本模块与 tests/test_source_hygiene.py 都是
    「先枚举目录、再逐个打开」的形状。枚举与打开之间树可能被别的进程改动 —— 实测
    撞到的正是 `sandbox/acceptance_check.py` 的验收探针：它当时在**真实的工程树**里建一个
    `sandbox/_accept_probe_selfcheck.py`、跑完门禁立刻删掉（约 1 秒窗口），并行套件里
    别的锁枚举到它之后再去读就 FileNotFoundError，整条锁以一个与它无关的崩溃收场。
    （那个探针同日已改为密闭夹具，不再进工程树；但本兜底不撤 —— 窗口来自"活着的工程树
    不会被冻结"，编辑器 / git 切分支 / 另一个 worktree 都在同一棵树上动手。）

    返回 None 是**正确**的答案而不是"报平安"：文件已经不在了，它没有可判定的内容；
    下次运行若它还在，照常会进清单。真正的危险是"文件在、却没检查"，
    那种情况本函数不会掩盖 —— 读成功就是读成功。
    """
    try:
        return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def needs_fallback(path):
    src = _read_text(path)
    if src is None:
        return False                      # 枚举后被删：本轮无从判定，按"不是入口"处理
    prints = ("print(" in src) or ("sys.stdout.write" in src) or ("sys.stderr.write" in src)
    if not prints:
        return False
    return any(ord(ch) > 127 for ch in src)


def entries():
    return [os.path.relpath(f, ROOT).replace("\\", "/") for f in candidates() if needs_fallback(f)]


def missing():
    out = []
    for rel in entries():
        src = _read_text(os.path.join(ROOT, rel))
        if src is None:
            continue                      # 同上：文件已消失，谈不上"缺兜底"
        if "reconfigure" not in src:
            out.append(rel)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.json:
        print(json.dumps({"entries": entries(), "missing_reconfigure": missing()},
                         ensure_ascii=False, indent=2))
    elif a.check:
        m = missing()
        print("需要 UTF-8 兜底的入口：%d 个" % len(entries()))
        if m:
            print("❌ 以下入口缺 reconfigure（GBK/ASCII 宿主管道下会崩或乱码）：")
            for x in m:
                print("   -", x)
            return 1
        print("✅ 全部带 UTF-8 兜底")
    else:
        for x in entries():
            print(x)
    return 0


if __name__ == "__main__":
    sys.exit(main())
