# -*- coding: utf-8 -*-
"""回滚 render_harness_placeholders.py 的渲染结果。

把业务仓 AGENTS.md 从绝对路径恢复为 <HARNESS> 占位符（从 .bak-YYYYMMDD 备份还原）。
"""
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "agentguard"))
sys.path.insert(0, os.path.join(ROOT, "harness"))

from _config import load as load_config


def main():
    cfg = load_config()
    repos = (cfg.get("repos") or {})
    if not repos:
        print("✗ config/harness.local.toml 里没有 repos 段", file=sys.stderr)
        return 2

    today = time.strftime("%Y%m%d")
    bak_suffix = f".bak-{today}"

    restored, skipped, missing_bak = [], [], []

    for key, path in sorted(repos.items()):
        if not path or not os.path.isdir(path):
            skipped.append(key)
            continue

        agents_md = os.path.join(path, "AGENTS.md")
        backup = agents_md + bak_suffix

        if not os.path.isfile(agents_md):
            skipped.append(key)
            continue

        if not os.path.isfile(backup):
            missing_bak.append(key)
            continue

        # 从备份还原
        try:
            with open(backup, encoding="utf-8") as f:
                original = f.read()
            with open(agents_md, "w", encoding="utf-8", newline="\n") as f:
                f.write(original)
            os.remove(backup)
            restored.append(key)
        except Exception as e:
            print(f"✗ {key} 还原失败: {e}", file=sys.stderr)
            missing_bak.append(key)

    print(f"=== 回滚完成 ===")
    print(f"已还原: {len(restored)} 仓")
    if restored:
        print(f"  {', '.join(restored)}")
    if skipped:
        print(f"跳过（无 AGENTS.md 或未配路径）: {len(skipped)} 仓")
        print(f"  {', '.join(skipped[:6])}{'...' if len(skipped) > 6 else ''}")
    if missing_bak:
        print(f"缺备份 {bak_suffix}: {len(missing_bak)} 仓")
        print(f"  {', '.join(missing_bak)}")

    return 0 if not missing_bak else 1


if __name__ == "__main__":
    sys.exit(main())
