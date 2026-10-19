#!/bin/sh
# eaglermc_harness/sandbox/demo_gate.sh —— 门禁现场演示脚本（分享给同事用）
# ============================================================
# 演示内容（三步）：
#   1. 故意写一个违规 .cs 文件（状态魔法数字裸写 10，违反 R9 规则）→ 跑门禁 → 展示【拦截】
#   2. 修复成 SysConst 常量写法 → 再跑门禁 → 展示【放行】
#   3. 清理临时文件
# 用法：
#   sh <harness>/sandbox/demo_gate.sh [gate名]
#   gate 默认 hy_hzy_backend（hzy 后端仓），也可传 gxgk_backend 等。
# 前提：对应端仓库已 clone 到本机，且 config/harness.local.yaml 已配置该仓路径。
# 安全：只在临时文件上演示，跑完自动清理；不碰真实代码。
# 实现：直接调 run_gate.py 传单个文件（只检查 Demo 文件，不被全仓 diff 存量污染）。
set -e

GATE=${1:-hy_hzy_backend}
HARNESS=$(cd "$(dirname "$0")/.." && pwd)
if command -v cygpath >/dev/null 2>&1; then
    HARNESS_WIN=$(cygpath -m "$HARNESS")
else
    HARNESS_WIN="$HARNESS"
fi

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
    echo "  演示全程靠它解析 gate 仓路径 + 跑门禁，已按 fail-closed 中止。"
    echo "  修复：装 Python 3.11+；或显式指定解释器：export HARNESS_PYTHON=python3"
    exit 2
fi
# 调用处一律写 $HARNESS_PY 且**不加引号**（"py -3" 需要按空格拆成两个词）。
# ------------------------------------------------------------------------------

echo "============================================================"
echo " eaglermc_harness 门禁演示（gate=$GATE）"
echo "============================================================"

# 从 config 解析 gate 的仓库路径（含 local 覆盖层）
REPO=$($HARNESS_PY - "$HARNESS_WIN" "$GATE" <<'PYEOF'
import sys, os
harness, gate = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(harness, "harness"))
from _config import gate as get_gate
g = get_gate(gate)
print(g.get("_repo_path") or "")
PYEOF
)

if [ -z "$REPO" ] || [ ! -d "$REPO" ]; then
    echo "✗ gate=$GATE 的仓库路径无效：$REPO"
    echo "  请检查 config/harness.local.yaml 是否配置了该仓库路径。"
    exit 2
fi
echo "目标仓库：$REPO"
echo ""

DEMO_FILE="$REPO/RMCHY/Eagle.RMCHY.Entities/Demo_GateShow.cs"
cleanup() { rm -f "$DEMO_FILE"; }
trap cleanup EXIT

run_gate() {
    HARNESS_ENTRY=demo $HARNESS_PY "$HARNESS_WIN/harness/run_gate.py" "$GATE" "$DEMO_FILE"
}

# ---------- 第 1 步：写违规文件，展示拦截 ----------
echo "【第 1 步】故意写一个违规文件（状态魔法数字裸写 10，违反 R9 规则）…"
# 注意：必须写 utf-8-bom（仓约定），否则 ENC 规则先拦，演示不出 R9 的效果。
$HARNESS_PY - "$DEMO_FILE" <<'PYEOF'
import sys
path = sys.argv[1]
content = '''using System;

namespace Eagle.RMCHY.Entities
{
    /// <summary>门禁演示用：本文件故意违规，跑完即删</summary>
    public class Demo_GateShow
    {
        /// <summary>违规写法：状态与裸数字比较（应为 SysConst.NormalStatus）</summary>
        public bool IsNormal(int Status)
        {
            return Status == 10;
        }
    }
}
'''
with open(path, "w", encoding="utf-8-sig") as f:
    f.write(content)
print("已写入:", path)
PYEOF
echo ""
echo "---- 运行门禁（应被 R9 拦截）----"
run_gate || true
echo ""
echo "---- 上面显示了 R9 拦截？这就是机械规则 vs 靠 AI 自觉的区别 ----"
echo ""

# ---------- 第 2 步：修复，展示放行 ----------
echo "【第 2 步】修复违规：改用 SysConst 常量…"
$HARNESS_PY - "$DEMO_FILE" <<'PYEOF'
import sys
path = sys.argv[1]
content = '''using System;
using Eagle.Common;

namespace Eagle.RMCHY.Entities
{
    /// <summary>门禁演示用：修复后版本，跑完即删</summary>
    public class Demo_GateShow
    {
        /// <summary>合规写法：引用 SysConst 常量比较</summary>
        public bool IsNormal(int Status)
        {
            return Status == SysConst.NormalStatus;
        }
    }
}
'''
with open(path, "w", encoding="utf-8-sig") as f:
    f.write(content)
print("已修复为 SysConst.NormalStatus 写法")
PYEOF
echo ""
echo "---- 再次运行门禁（应通过）----"
run_gate || true
echo ""
echo "---- 通过了？修复一次即放行 ----"
echo ""

# ---------- 第 3 步：清理 ----------
echo "【第 3 步】清理演示文件…"
cleanup
echo "已删除 $DEMO_FILE"
echo ""
echo "============================================================"
echo " 演示结束。结论：门禁 = 提交前的硬性检查，AI 记不记得住不重要，"
echo " 门禁一定记得。"
echo "============================================================"
