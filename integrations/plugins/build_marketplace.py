# -*- coding: utf-8 -*-
"""构建 Claude 兼容 marketplace（内含 eaglermc-gate 插件）——Claude Code 与 OpenClaw 双消费。

用法：
  python integrations/plugins/build_marketplace.py --harness <HARNESS> --out <DIR> [--python <PY>]
  python integrations/plugins/build_marketplace.py --harness <HARNESS> --out <DIR> --keep-placeholders

产物（OUT，可直接被 claude / openclaw 消费）：
  .claude-plugin/marketplace.json   市场清单（声明 eaglermc-gate）
  plugins/eaglermc-gate/…           由 build_plugin.py 按机器打戳生成（同源复制 + 技能）
  README.md                         市场说明

`--keep-placeholders` 原样透传给 build_plugin.py：出货件（内部安装包）走这条，
`.mcp.json` 里留 `__PY__`/`__MCP__`/`__HARNESS__` 由装机时的 `fix_paths.py` 现场填。
理由与代价写在 build_plugin.py 的模块 docstring 里。

原则（ADR-0001）：壳只转发；构建产物是打包副本，源永远只有一份。本脚本只写/覆盖，不删除。
"""
from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "eaglermc-gate-marketplace")
PLUGIN_NAME = "eaglermc-gate"
MARKET_NAME = "eaglermc-tools"


def _copy_text(src, dst):
    with io.open(src, encoding="utf-8") as f:
        text = f.read()
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with io.open(dst, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description="构建 eaglermc-tools 插件市场（Claude 兼容）")
    ap.add_argument("--harness", required=True, help="eaglermc_harness 工程根")
    ap.add_argument("--out", required=True, help="输出目录（不存在则创建）")
    ap.add_argument("--python", default=sys.executable or "python", help="写入配置的 python 解释器")
    ap.add_argument("--keep-placeholders", action="store_true",
                    help="透传给 build_plugin.py：保留 __PY__/__MCP__/__HARNESS__（出货件用）")
    a = ap.parse_args(argv)

    harness = os.path.abspath(a.harness)
    out = os.path.abspath(a.out)
    if not os.path.isfile(os.path.join(harness, "harness", "run_gate.py")):
        print("✗ 不是有效的 harness 根: %s" % harness)
        return 2

    # 1) 构建插件（同源：build_plugin.py 打戳 + 技能复制）
    plugin_out = os.path.join(out, "plugins", PLUGIN_NAME)
    cmd = [sys.executable, os.path.join(HERE, "build_plugin.py"),
           "--harness", harness, "--out", plugin_out, "--python", a.python]
    if a.keep_placeholders:
        cmd.append("--keep-placeholders")
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        print("✗ 插件构建失败（rc=%s）" % rc)
        return rc

    # 2) 市场清单 + 说明（模板直写；清单本身不含版本字段，版本在插件 manifest 内）
    _copy_text(os.path.join(TEMPLATE, ".claude-plugin", "marketplace.json"),
               os.path.join(out, ".claude-plugin", "marketplace.json"))
    _copy_text(os.path.join(TEMPLATE, "README.md"), os.path.join(out, "README.md"))

    print("✅ 市场已构建 → %s" % out)
    print("   市场名: %s ｜ 插件: %s ｜ 源: %s" % (MARKET_NAME, PLUGIN_NAME, plugin_out))
    print("   打戳: %s" % ("保留占位符（出货件）" if a.keep_placeholders else "就地打戳（本机自用）"))
    print()
    print("下一步（任选，均可离线执行）：")
    print('  1) Claude Code 校验: claude plugin validate "%s" --strict --json' % out)
    if a.keep_placeholders:
        print("     ⚠ 这一步在占位符未填时**照样通过**（校验器看不出机器归属），别把它当适配证明；")
        print("       适配证明 = fix_paths.py 跑完后 .mcp.json 三个字段都是本机真实路径。")
    print('  2) Claude Code 接入: claude plugin marketplace add "%s" && claude plugin install %s@%s' % (out, PLUGIN_NAME, MARKET_NAME))
    print('  3) OpenClaw 读取:   openclaw plugins marketplace list "%s" --json' % out)
    print('  4) 端到端自证:      python integrations/plugins/verify_plugin.py --plugin "%s"' % plugin_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
