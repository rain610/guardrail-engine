# -*- coding: utf-8 -*-
"""
eaglermc_harness/hooks/pre-edit.py —— 改码前门禁（Codex hooks 的 pre-edit 点）
================================================================================
职责：① 校验「方案已确认」（工单系统走 confirm_items；对话驱动模式仅提示，不阻断）
      ② 打印该端的**范式指针**（M3，2026-09-19）——「动手前该读哪几份」
用法：python harness/hooks/pre-edit.py <gate名>
退出码：0=放行，1=阻断（当前恒 0：本钩子是提示点，硬约束不在这儿）
"""
import sys
import os

# 台账「来源入口」（评审 v13 中 16）：本文件原先不设 HARNESS_ENTRY。当前它只做确认清单提示、
# 不落台账，但四个入口对同一个变量的处理必须一致 —— 少一个，"入口分布"就有一类记录是空的，
# 而且下次有人在这里接上引擎时，缺的这行没人会想起来补。
os.environ["HARNESS_ENTRY"] = "pre-edit"

# UTF-8 兜底（2026-09-13 评审 v3 H1 系统性修法）：宿主管道可能是 GBK/ASCII，
# 打印中文/✗ 会抛 UnicodeEncodeError；errors=replace 最坏只是个别字符变问号。
# 清单由 sandbox/list_entries.py 机器推导（tests/test_entry_level.py 有对应锁）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # = <root>/harness
GOLDEN = os.path.join(os.path.dirname(HARNESS), "golden-patterns")      # = <root>/golden-patterns


def _kind_of(gate_name):
    """gate 名 → 端（pc / h5 / backend），认不出返回 ""。

    复用 checks/file_types.py 的 kind_of() —— 那是全仓「gate 名→端」的**既有单一来源**
    （post-edit / auto-gate 按它决定收哪些扩展名，tests/test_review_v8.py 有锁）。
    这里**绝不再写一份映射**：两份映射必然漂，而「哪个端」这种事实一旦两处不一致，
    症状是钩子提示你去读 H5 范式、门禁却按 PC 规则拦你 —— 比不给提示更坏。

    整段包 try/except 且失败即返回 ""：pre-edit 是**永不阻断**的提示点，不能因为
    import 不到（最小布局里只拷单个钩子脚本是真会发生的）就把编辑期卡死。
    """
    try:
        checks = os.path.join(os.path.dirname(HARNESS), "checks")
        if checks not in sys.path:
            sys.path.insert(0, checks)
        import file_types
        return file_types.kind_of(gate_name)
    except Exception:
        return ""


def _paradigm_rows(kind):
    """该端的入口三行：(标签, 路径, 是否存在)。

    路径**全部由 kind 推导**，不写死清单 —— 清单就是副本，副本就会漂
    （同理见 golden-patterns/index.md：它那份「按端速查」表漏登记了 6 份新风范，
     因为它是手维的；golden-patterns 目录里现在有什么，本函数就报什么）。
    """
    rows = [
        ("端级规则地图", os.path.join(GOLDEN, "templates", "AGENTS_%s_slim.md" % kind), True),
        ("端范式目录", os.path.join(GOLDEN, kind), False),
        ("端到端范本", os.path.join(GOLDEN, "endtoend"), False),
    ]
    # 存在性在这里判（第三项 True=按文件判，False=按目录判）：核对的是**磁盘实况**，
    # 所以新同事的安装缺了范式库时这行会直接显示 ✗ —— 但**绝不阻断**（本钩子恒 rc=0）。
    return [(label, p, (os.path.isfile(p) if is_file else os.path.isdir(p)))
            for label, p, is_file in rows]


def main():
    gate_name = sys.argv[1] if len(sys.argv) > 1 else "gxgk_pc"
    print(f"[pre-edit] 改码前检查（{gate_name}）：确保方案已确认（工单 confirm_items 全勾 / 对话已对齐改动清单）。")

    kind = _kind_of(gate_name)
    if kind:
        print(f"[pre-edit] 动手前先加载知识（{kind} 端）—— 按任务选，不要全读：")
        for label, path, ok in _paradigm_rows(kind):
            # 不按字符数补位对齐：中文是双宽，`%-12s` 数的是字符数，补出来反而参差
            # （端级规则地图 6 字 vs 端范式目录 5 字，都是 12 个空格位但视觉差一格）。
            p = path.replace("\\", "/")          # 正斜杠两种 shell 里都能直接粘
            print("  %s %s：%s" % ("·" if ok else "✗", label, p))
        print("  ↑ 端级地图里有「按任务 → 必读哪份范式」表；改存量业务再读端到端范本。")
        print("    被门禁拦时它会直接把**规则 ID** 打给你，对着端级地图里的 ID 速查表认。")
    else:
        # 认不出端（自定义 gate 命名）：退回到总索引，别猜。
        # 注意 index.md 是手维目录，可能落后于目录实况 —— 它只是「找路」的兜底，不是权威清单。
        idx = os.path.join(GOLDEN, "index.md")
        print("[pre-edit] 认不出端点（gate 名不是 <客户>_pc / _h5 / _backend），范式总索引：%s"
              % idx.replace("\\", "/"))

    print("[pre-edit] 放行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
