# -*- coding: utf-8 -*-
"""构建 Claude Code 插件原型（agentguard）——按机器打戳 + 技能同源复制。

用法（在工程根或任意位置）：
  python integrations/plugins/build_plugin.py --harness <HARNESS> --out <OUT_DIR> [--python <PY>]
  python integrations/plugins/build_plugin.py --harness <HARNESS> --out <OUT_DIR> --keep-placeholders

产物（OUT_DIR，可直接用于 claude --plugin-dir / marketplace 形态）：
  .claude-plugin/plugin.json   插件清单（版本取自 <HARNESS>/VERSION，单一来源）
  .mcp.json                    把 agentguard MCP 薄服务声明为插件自带 MCP server
  skills/agentguard/...  技能包（从 integrations/agentguard-skill 复制，同源）
  README.md                    插件层使用说明

两种打戳模式（`--keep-placeholders`，2026-09-17 起出货件走第二种）
================================================================================
  ① 默认（就地打戳）：把模板里的 `__PY__` / `__MCP__` / `__HARNESS__` 换成**本机**的
     python 与工程路径。适合"我就在这台机器上用" —— `claude --plugin-dir` 直接加载。
  ② `--keep-placeholders`（出货）：原样保留三个占位符，只填 `__VERSION__`。
     **内部安装包必须走这条**（`sandbox/pack_release.py` 已固定这么调）。

为什么出货件不能就地打戳：打包机把**自己的**绝对路径烧进了 `.mcp.json`。同事照着
安装说明装、却漏跑 `fix_paths.py` 时（尤其是"原地升级、机器没变"这句话最容易让人跳过），
那份文件会被换成打包机的路径 —— MCP 服务起不来，**编辑期门禁整体无声失效**，而全程
没有任何一步报错。留占位符则同一个漏步会以 `__PY__` 找不到解释器的方式当场暴露，
且"这份包还没适配"从此**肉眼可辨**（`sandbox/verify_release.py` 也据此设了锁）。
版本号两种模式都照实写：它不随机器变，而插件缓存目录正是按版本号分键的。

原则：构建产物是「打包副本」，源永远只有一份（壳只转发；ADR-0001）。
本脚本只做「写/覆盖」，不做任何删除。
"""
from __future__ import annotations

import argparse
import io
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "agentguard-plugin")


def _eol_text(harness):
    """把 `sandbox/` 挂上 sys.path 并返回 `eol_text`（行尾口径的唯一一处定义）。

    为什么让**这个**脚本也走同一口径：出货件里 `marketplace/plugins/…/SKILL.md` 的 CRLF
    正是从这里来的 —— 本函数此前用 `open(..., "rb")` 原样照搬工作树字节，而同一份
    `integrations/agentguard-skill/SKILL.md` 在打包机上是 CRLF、在 CI 上是 LF，
    于是同一个包在外层就已经不同了（2026-09-18 发版复盘实测的第 3 处）。
    """
    sandbox = os.path.join(os.path.abspath(harness), "sandbox")
    if sandbox not in sys.path:
        sys.path.insert(0, sandbox)
    import eol_text
    return eol_text


def copy_tree_if_changed(src, dst, harness, eol_map=None):
    eol_text = _eol_text(harness)
    if eol_map is None:
        eol_map = eol_text.index_eol_map(harness)
    created = updated = same = 0
    for dp, dn, fn in os.walk(src):
        rel = os.path.relpath(dp, src)
        tdir = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(tdir, exist_ok=True)
        for name in fn:
            s = os.path.join(dp, name)
            t = os.path.join(tdir, name)
            data = eol_text.read_shipped(s, harness, eol_map)
            if os.path.exists(t):
                try:
                    with open(t, "rb") as f:
                        if f.read() == data:
                            same += 1
                            continue
                except OSError:
                    pass
                with open(t, "wb") as f:
                    f.write(data)
                updated += 1
            else:
                with open(t, "wb") as f:
                    f.write(data)
                created += 1
    return created, updated, same


def main(argv=None):
    ap = argparse.ArgumentParser(description="构建 agentguard Claude Code 插件原型")
    ap.add_argument("--harness", required=True, help="agentguard 工程根（含 harness/run_gate.py）")
    ap.add_argument("--out", required=True, help="输出目录（不存在则创建）")
    ap.add_argument("--python", default=sys.executable or "python", help="写入配置的 python 解释器")
    ap.add_argument("--keep-placeholders", action="store_true",
                    help="保留 __PY__/__MCP__/__HARNESS__ 占位符（出货件用：由包内 fix_paths.py "
                         "现场填充；版本号仍照实写入）")
    a = ap.parse_args(argv)

    harness = os.path.abspath(a.harness)
    out = os.path.abspath(a.out)
    if not os.path.isfile(os.path.join(harness, "harness", "run_gate.py")):
        print("✗ 不是有效的 harness 根: %s" % harness)
        return 2
    mcp = os.path.join(harness, "agentguard", "mcp.py")
    if not os.path.isfile(mcp):
        print("✗ 缺少 MCP 薄服务: %s" % mcp)
        return 2
    try:
        with io.open(os.path.join(harness, "VERSION"), encoding="utf-8") as f:
            version = f.read().strip() or "0.0.0-dev"
    except OSError:
        version = "0.0.0-dev"

    sub = {"__VERSION__": version}
    if not a.keep_placeholders:
        sub.update({"__PY__": a.python.replace("\\", "/"),
                    "__MCP__": mcp.replace("\\", "/"),
                    "__HARNESS__": harness.replace("\\", "/")})

    for rel in (".claude-plugin/plugin.json", ".mcp.json", "README.md"):
        src = os.path.join(TEMPLATE, rel)
        if not os.path.isfile(src):
            print("✗ 模板缺失: %s" % src)
            return 2
        with io.open(src, encoding="utf-8") as f:
            text = f.read()
        for k, v in sub.items():
            text = text.replace(k, v)
        dst = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with io.open(dst, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    skill_src = os.path.join(harness, "integrations", "agentguard-skill")
    skill_dst = os.path.join(out, "skills", "agentguard")
    c = u = s = 0
    if os.path.isdir(skill_src):
        c, u, s = copy_tree_if_changed(skill_src, skill_dst, harness)

    print("✅ 插件已构建 → %s" % out)
    print("   version=%s  python=%s" % (version, a.python))
    print("   MCP : %s" % mcp)
    print("   打戳: %s" % ("保留占位符（出货件：装机时必须跑一次包内 fix_paths.py）"
                          if a.keep_placeholders else "就地打戳（本机自用）"))
    print("   skill 复制: 新增 %d / 更新 %d / 未变 %d" % (c, u, s))
    print()
    print("下一步（任选）：")
    print("  1) 离线校验: claude plugin validate \"%s\" --json" % out)
    if a.keep_placeholders:
        print("     注意：占位符未填时校验**不会**报错（它看不出机器归属），所以这一条不算适配证明；")
        print("     适配证明是：跑 fix_paths.py 后 .mcp.json 里三个字段都是本机真实路径。")
    print("  2) 会话加载: claude --plugin-dir \"%s\"" % out)
    print("  3) 端到端自证: python integrations/plugins/verify_plugin.py --plugin \"%s\"" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
