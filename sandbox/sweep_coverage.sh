#!/bin/sh
# 一次性：给若干个真实仓跑全量扫描，为覆盖度自评产出规则级数据（追加进 reports/gate_log.jsonl）。
# 用途是"让传感器响一遍"——回答哪些规则真实触发过、哪些一次没响。

# ---- 解释器解析（2026-09-18 跨平台：Windows / WSL / Linux 共用同一约定）----
# 判据不是「命令在不在」，而是「**真的能跑起来**」：--version 退出码 0 且输出 Python 3。
# 同时挡掉三种真实情形：Ubuntu 上只有 python3（裸 python 直接 command not found）/
# Windows 的 Microsoft Store python3.exe 别名（command -v 能命中、一跑就失败）/ 系统里是 Python 2。
# HARNESS-PY-CANDIDATES: python3 python py -3
#   ↑ 机器可读的单一事实来源（正本在 harness/hooks-dir/pre-commit）：
#     tests/test_interpreter_convention.py 拿本行与其余 sh 入口的同名标记对拍 ——
#     候选顺序只允许在标记行改，改完各处必须同步。
# 为什么内联、不 source 一个同级 _python.sh：本仓多条锁会搭**只拷单个脚本**的最小布局真跑
# （如 tests/test_hooks_chain.py:238/261），同级新文件不会被拷过去 → 那些锁当场失效。
# 为什么放在 cd 之前：本脚本通篇都在跑门禁（下面逐个 gate --all 全量扫描）与覆盖度自评（末行），
# 没有解释器时应当**先出声再退出**，而不是跑起来一半、在某个 gate 上以 command not found 崩掉。
HARNESS_PY=""
for _c in "${HARNESS_PYTHON:-}" python3 python; do
    [ -n "$_c" ] || continue
    command -v "$_c" >/dev/null 2>&1 || continue
    _v=$("$_c" --version 2>&1) || continue
    case "$_v" in
        Python\ 3*) HARNESS_PY="$_c"; break ;;
    esac
done
if [ -z "$HARNESS_PY" ] && command -v py >/dev/null 2>&1; then
    _v=$(py -3 --version 2>&1)
    case "$_v" in
        Python\ 3*) HARNESS_PY="py -3" ;;
    esac
fi
if [ -z "$HARNESS_PY" ]; then
    echo "✗ 找不到可用的 Python 3 解释器（已试：HARNESS_PYTHON / python3 / python / py -3）"
    echo "  本脚本靠它逐个 gate 跑全量扫描、并产出覆盖度自评 —— 已按 fail-closed 中止，未做任何事。"
    echo "  修复：装 Python 3.11+；或显式指定解释器：export HARNESS_PYTHON=python3"
    exit 2
fi
# 调用处一律写 $HARNESS_PY 且**不加引号**（"py -3" 需要按空格拆成两个词）。
# ------------------------------------------------------------------------------

cd "$(dirname "$0")/.." || exit 1
GATES="hy_pc hy_h5 hy_hzy_pc hy_hzy_h5 gxgk_pc gxgk_h5 gxgk_backend"
for g in $GATES; do
    printf '=== %s ===\n' "$g"
    # N-3（第四轮）：先取退出码再 tail —— 直接 `... | tail -2` 后取 $? 拿到的是 tail 的码（恒 0），
    # 门禁真被拦也显示 rc=0，会误导"传感器响没响"的判断。
    out=$(timeout 300 $HARNESS_PY harness/run_gate.py "$g" --all 2>&1)
    rc=$?
    printf '  rc=%s\n' "$rc"
    printf '%s\n' "$out" | tail -2
done
printf '\n=== 覆盖度自评（本轮数据）===\n'
$HARNESS_PY harness/coverage.py --last 12
