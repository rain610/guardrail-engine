#!/bin/sh
# guardrail-engine/sandbox/demo_gate.sh —— 60 秒门禁现场演示（不需要配置任何业务仓）
# ============================================================
# 演示内容（三步）：
#   1. 在临时目录写一个含 console.log 的 .js → 跑示例门禁 → 必须 rc=1 且点名 NO-CONSOLE
#   2. 改成合规写法 → 再跑 → 必须 rc=0
#   3. 清理临时目录（跑完即删，不碰任何真实代码）
# 用法：
#   sh <harness>/sandbox/demo_gate.sh
# 说明：演示固定走 `example` gate（开源版唯一开箱即用的规则集），用 `--repo <临时目录>`
#       覆盖仓库路径，所以**不依赖** config/harness.local.toml 里配过任何受管仓。
set -u

GATE=${1:-example}
if [ "$GATE" != "example" ]; then
    echo "✗ 本演示的探针内容只对应 example gate 的规则（console.log）。"
    echo "  想看其它 gate 的现场拦截，请在对应业务仓里按 README 的接入流程跑。"
    exit 2
fi

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
    echo "  演示全程靠它跑门禁，已按 fail-closed 中止。"
    echo "  修复：装 Python 3.11+；或显式指定解释器：export HARNESS_PYTHON=python3"
    exit 2
fi
# 调用处一律写 $HARNESS_PY 且**不加引号**（"py -3" 需要按空格拆成两个词）。
# ------------------------------------------------------------------------------

DEMO_DIR=$(mktemp -d "${TMPDIR:-/tmp}/guardrail-engine-demo.XXXXXX") || {
    echo "✗ 无法创建临时目录（mktemp -d 失败）"
    exit 2
}
if command -v cygpath >/dev/null 2>&1; then
    DEMO_DIR_WIN=$(cygpath -m "$DEMO_DIR")
else
    DEMO_DIR_WIN="$DEMO_DIR"
fi
DEMO_FILE_POSIX="$DEMO_DIR/demo.js"
DEMO_FILE_WIN="$DEMO_DIR_WIN/demo.js"
cleanup() { rm -rf "$DEMO_DIR"; }
trap cleanup EXIT

run_demo() {
    HARNESS_ENTRY=demo $HARNESS_PY "$HARNESS_WIN/harness/run_gate.py" \
        "$GATE" --repo "$DEMO_DIR_WIN" "$DEMO_FILE_WIN"
}

echo "============================================================"
echo " guardrail-engine 门禁演示（gate=$GATE，临时仓=$DEMO_DIR_WIN）"
echo "============================================================"
echo ""

# ---------- 第 1 步：写违规文件，展示拦截 ----------
echo "【第 1 步】写一个含 console.log 的临时文件（模拟调试代码忘了删）…"
cat > "$DEMO_FILE_POSIX" <<'EOF'
// 演示：调试残留，提交前应被门禁拦下
console.log("debug");
EOF
echo "已写入: $DEMO_FILE_WIN"
echo ""
echo "---- 运行门禁（期望 rc=1，点名 NO-CONSOLE）----"
if OUT=$(run_demo 2>&1); then
    RC=0
else
    RC=$?
fi
printf '%s\n' "$OUT"
echo ""
if [ "$RC" != "1" ]; then
    echo "❌ 演示失败：期望 rc=1（拦截），实得 rc=$RC"
    exit 1
fi
if ! printf '%s' "$OUT" | grep -q "\[NO-CONSOLE\]"; then
    echo "❌ 演示失败：rc=1 但输出里没有 [NO-CONSOLE]（拦的可能不是这条规则）"
    exit 1
fi
echo "✅ 拦截成功：这条改动提交不上去。"
echo ""

# ---------- 第 2 步：修复，展示放行 ----------
echo "【第 2 步】删掉调试日志，改成合规写法…"
cat > "$DEMO_FILE_POSIX" <<'EOF'
const answer = 42;
EOF
echo ""
echo "---- 再次运行门禁（期望 rc=0）----"
if OUT2=$(run_demo 2>&1); then
    RC2=0
else
    RC2=$?
fi
printf '%s\n' "$OUT2"
echo ""
if [ "$RC2" != "0" ]; then
    echo "❌ 演示失败：修复后期望 rc=0（放行），实得 rc=$RC2"
    exit 1
fi
echo "✅ 放行成功：同样的门禁，改对了就能过。"
echo ""

# ---------- 第 3 步：清理 ----------
echo "【第 3 步】清理临时文件…"
cleanup
trap - EXIT
echo "已删除 $DEMO_DIR"
echo ""
echo "============================================================"
echo " 演示结束。结论：门禁 = 提交前的硬性检查，AI 记不记得住不重要，"
echo " 门禁一定记得。"
echo "============================================================"
