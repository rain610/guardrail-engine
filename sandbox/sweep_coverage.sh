#!/bin/sh
# 一次性：给若干个真实仓跑全量扫描，为覆盖度自评产出规则级数据（追加进 reports/gate_log.jsonl）。
# 用途是"让传感器响一遍"——回答哪些规则真实触发过、哪些一次没响。

# ---- 解释器解析（2026-09-18 跨平台：Windows / WSL / Linux 共用同一约定）----
# 判据不是「命令在不在」，而是「**真的能跑起来**」：--version 退出码 0 且输出 Python 3。
# 同时挡掉三种真实情形：Ubuntu 上只有 python3（裸 python 直接 command not found）/
# Windows 的 Microsoft Store python3.exe 别名（command -v 能命中、一跑就失败）/ 系统里是 Python 2。
# HARNESS-PY-CANDIDATES: python3 python py -3
#   ↑ 机器可读的单一事实来源（正本在 harness/hooks-dir/pre-commit）：
#     tests/packaging/test_interpreter_convention.py 拿本行与其余 sh 入口的同名标记对拍 ——
#     候选顺序只允许在标记行改，改完各处必须同步。
# 为什么内联、不 source 一个同级 _python.sh：本仓多条锁会搭**只拷单个脚本**的最小布局真跑
# （如 tests/engine/test_hooks_chain.py:238/261），同级新文件不会被拷过去 → 那些锁当场失效。
# 为什么放在 cd 之前：本脚本通篇都在跑门禁（下面逐个 gate --all 全量扫描）与覆盖度自评（末行），
# 没有解释器时应当**先出声再退出**，而不是跑起来一半、在某个 gate 上以 command not found 崩掉。
HARNESS_PY=""
# 判据不是「像不像 Python 3」，而是「是不是 3.11+」（评审 2026-11-04 P2-4）：3.10 会被
# 选中，引擎 import 直接失败，而提示只在「找不到任何 Python 3」时才出现。_py_ok 从
# --version 输出取 major.minor 做数值比较，只有 3.11+ 回 0。
_py_ok() {
    # 纯参数展开（不 fork sed/awk）：这些 .sh 会在最小环境里跑，少一个外部命令少一种失效。
    _s=${1##*[Pp]ython }              # 去掉到最后一个 "Python "/"python " 的前缀 → 3.11.4
    _maj=${_s%%.*}                    # 3
    _min=${_s#*.}; _min=${_min%%.*}   # 11
    case "$_maj$_min" in
        '' | *[!0-9]*) return 1 ;;    # 解析不出版本号 → 不采用（判据落在输出上，不是名字上）
    esac
    [ "$_maj" -eq 3 ] && [ "$_min" -ge 11 ]
}
for _c in "${HARNESS_PYTHON:-}" python3 python; do
    [ -n "$_c" ] || continue
    command -v "$_c" >/dev/null 2>&1 || continue
    _v=$("$_c" --version 2>&1) || continue
    if _py_ok "$_v"; then HARNESS_PY="$_c"; break; fi
done
if [ -z "$HARNESS_PY" ] && command -v py >/dev/null 2>&1; then
    _v=$(py -3 --version 2>&1)
    if _py_ok "$_v"; then HARNESS_PY="py -3"; fi
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
# gate 清单从配置读，不硬编码受管仓名：启用即纳入，新增 gate 不用改本脚本。
GATES=$($HARNESS_PY - 2>/dev/null <<'PYEOF'
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "harness"))
from _config import all_gates
# 仓库级自检 gate（harness_self）不吃 --all，扫的是整个工程仓，排除。
print(" ".join(n for n, g in sorted(all_gates().items())
               if g.get("enabled")
               and os.path.basename(g.get("script") or "") != "harness_self_check.py"))
PYEOF
)
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
