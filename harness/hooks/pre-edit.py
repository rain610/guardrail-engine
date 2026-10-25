# -*- coding: utf-8 -*-
"""
guardrail-engine/hooks/pre-edit.py —— 改码前门禁（编辑期提示点）
================================================================================
职责：校验「方案已确认」（工单系统走 confirm_items；对话驱动模式仅提示，不阻断）
用法：python harness/hooks/pre-edit.py <gate名>
退出码：0=放行，1=阻断（当前恒 0：本钩子是提示点，硬约束不在这儿）
"""
import sys
import os

# 台账「来源入口」（评审 v13 中 16）：四个入口对同一个变量的处理必须一致 ——
# 少一个，"入口分布"就有一类记录是空的。
os.environ["HARNESS_ENTRY"] = "pre-edit"

# UTF-8 兜底（2026-09-13 评审 v3 H1 系统性修法）：宿主管道可能是 GBK/ASCII，
# 打印中文会抛 UnicodeEncodeError；errors=replace 最坏只是个别字符变问号。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main():
    gate_name = sys.argv[1] if len(sys.argv) > 1 else "example"
    print(f"[pre-edit] 改码前检查（{gate_name}）：确保方案已确认（工单 confirm_items 全勾 / 对话已对齐改动清单）。")
    print("[pre-edit] 放行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
