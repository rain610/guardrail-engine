#!/bin/sh
# eaglermc_harness/sandbox/install_engine_hooks.sh
# 给独立工程仓库 eaglermc_harness 自身安装 hooks（**拷贝模式**）。
# 保护循环（防 AI 为过门禁而改门禁）：
#   pre-commit  → 语法门禁：所有 checks/*.py 必须能编译（防改坏门禁脚本）
#   commit-msg  → 保护：改 checks/*.py / config/ 但提交消息无 [harness] 标记则拦截
#
# 用法：
#   sh sandbox/install_engine_hooks.sh              # 安装
#   sh sandbox/install_engine_hooks.sh --status     # 看当前装在哪、生效的是哪一份
#   sh sandbox/install_engine_hooks.sh --uninstall  # 卸载（只删本脚本装的副本，认指纹）
#
# ⚠ 与 core.hooksPath 的关系（评审 v13 中 17 附带查出的**自毁**路径，2026-09-16）
# ------------------------------------------------------------------------------
# 本脚本原先写 `HOOKS_DIR=$(git rev-parse --git-path hooks)`。而 git 的 `--git-path hooks`
# **尊重 core.hooksPath**：本仓一旦设了 `core.hooksPath = <工程>/harness/hooks-dir`
# （sandbox/install_hooks.sh 就是这么设的），它返回的是**共享分发器目录本身**。于是旧版会：
#   ① `cat > "$HOOKS_DIR/pre-commit"` 把 33 个端仓共用的分发器整个盖成 6 行 py_compile 循环；
#   ② `cat "$CM_SOURCE" > "$TARGET_MSG"` 两边是**同一个文件** —— sh 先截断再读，
#      共享 commit-msg 被清成 0 字节，[harness] 保护循环静默消失。
# 实测（2026-09-16）：`cat x > x` 之后文件大小 = 0；`git rev-parse --git-path hooks` 在本仓
# 返回 `D:/Rain/project/eaglermc_harness/harness/hooks-dir`。
# 同一条坑 install_hooks.sh 里记过（2026-09-12 批量迁移时把分发器当旧 hook 删掉，后续
# 所有仓安装全挂），本脚本没跟上。现在：改用 `--absolute-git-dir` + `/hooks` 定位默认目录，
# 并在 hooksPath 已接管时**拒绝写入**（写进 .git/hooks 是死安装，git 根本不执行那里）。
set -e
ENGROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ENGROOT"
SHARED="$ENGROOT/harness/hooks-dir"
GITDIR=$(git rev-parse --absolute-git-dir)
# 注意：这里**不能**用 `git rev-parse --git-path hooks`（理由见文件头）
HOOKS_DIR="$GITDIR/hooks"
TARGET_PRE="$HOOKS_DIR/pre-commit"
TARGET_MSG="$HOOKS_DIR/commit-msg"
CM_SOURCE="$SHARED/commit-msg"

# 指纹：只动"确实是我们装的"文件（覆盖与卸载都要认它，防误删别人的 hook）
FP_PRE="eaglermc_harness 工程 pre-commit"
FP_MSG="eaglermc-harness dispatcher"

ACTION="install"
case "${1:-}" in
    --uninstall) ACTION="uninstall" ;;
    --status)    ACTION="status" ;;
    "")          : ;;
    *) echo "✗ 未知参数：$1（可用：--status / --uninstall，或不带参数安装）"; exit 2 ;;
esac

HP=$(git config --get core.hooksPath 2>/dev/null || echo "")

if [ "$ACTION" = "status" ]; then
    echo "工程根:          $ENGROOT"
    echo "默认 hooks 目录:  $HOOKS_DIR"
    echo "core.hooksPath:  ${HP:-<未设置>}"
    for f in "$TARGET_PRE" "$TARGET_MSG"; do
        if [ -f "$f" ]; then echo "  拷贝副本存在:   $f"; else echo "  拷贝副本不存在: $f"; fi
    done
    if [ -n "$HP" ]; then
        echo "⚠ core.hooksPath 已设置 → git **不会**执行 $HOOKS_DIR 下的任何 hook（副本是死的）"
    fi
    exit 0
fi

if [ "$ACTION" = "uninstall" ]; then
    n=0
    if [ -f "$TARGET_PRE" ] && head -5 "$TARGET_PRE" | grep -q "$FP_PRE"; then
        rm -f "$TARGET_PRE"; echo "↺ 已删副本: $TARGET_PRE"; n=$((n + 1))
    fi
    if [ -f "$TARGET_MSG" ] && head -5 "$TARGET_MSG" | grep -q "$FP_MSG"; then
        rm -f "$TARGET_MSG"; echo "↺ 已删副本: $TARGET_MSG"; n=$((n + 1))
    fi
    echo "✅ 卸载完成（删除 $n 个本脚本装的副本；未动 core.hooksPath，也未动共享分发器）"
    echo "   说明：本脚本装的是拷贝模式副本。若当前生效的是 core.hooksPath 共享分发器，"
    echo "        撤回它请用：python \"$ENGROOT/eaglermc_gate/cli.py\" hooks uninstall --repo \"$ENGROOT\""
    exit 0
fi

# ---- 安装 ----------------------------------------------------------------
if [ -n "$HP" ]; then
    echo "ℹ 本仓已由 core.hooksPath 接管：$HP"
    if [ "$(cd "$HP" 2>/dev/null && pwd)" = "$(cd "$SHARED" 2>/dev/null && pwd)" ]; then
        echo "  正是本工程共享分发器 → 提交已经过它（工程仓按 self 模式把关），无需拷贝安装。"
        echo "  做任何写入都是多余的；旧版本在这里会覆盖分发器、并把它清成空文件（评审 v13 中 17）。"
    else
        echo "  ⚠ 但它指向的不是本工程分发器：写进 $HOOKS_DIR 的副本是**死安装**（git 不执行那里）。"
        echo "  处置二选一：① 改用共享分发器（推荐）："
        echo "                python \"$ENGROOT/eaglermc_gate/cli.py\" hooks install --repo \"$ENGROOT\""
        echo "             ② 若那个目录是你自己的钩子，就把本脚本要装的两件事并进那边，别用拷贝模式。"
    fi
    exit 0
fi

# ---------- pre-commit：语法门禁 ----------
cat > "$TARGET_PRE" <<'EOF'
#!/bin/sh
# eaglermc_harness 工程 pre-commit：语法门禁（防改坏门禁脚本）
set -e
ENGROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ENGROOT"
# ---- 解释器解析（2026-09-18 跨平台：Windows / WSL / Linux 共用同一约定）----
# 本文件由 sandbox/install_engine_hooks.sh **生成**（这里写什么，装下去的就是什么）。
# 判据不是「命令在不在」，而是「**真的能跑起来**」：--version 退出码 0 且输出 Python 3。
# 同时挡掉三种真实情形：Ubuntu 上只有 python3（裸 python 直接 command not found）/
# Windows 的 Microsoft Store python3.exe 别名（command -v 能命中、一跑就失败）/ 系统里是 Python 2。
# HARNESS-PY-CANDIDATES: python3 python py -3
#   ↑ 机器可读的单一事实来源（正本在 harness/hooks-dir/pre-commit）：
#     tests/test_interpreter_convention.py 拿本行与其余 sh 入口的同名标记对拍 ——
#     候选顺序只允许在标记行改，改完各处必须同步。
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
    echo "  本钩子的全部工作就是 py_compile 一遍门禁脚本，没有它等于门禁形同虚设 → 阻断本次提交。"
    echo "  修复：装 Python 3.11+；或显式指定解释器：export HARNESS_PYTHON=python3"
    exit 2
fi
# 调用处一律写 $HARNESS_PY 且**不加引号**（"py -3" 需要按空格拆成两个词）。
for f in checks/*.py harness/*.py harness/hooks/*.py; do
    $HARNESS_PY -m py_compile "$f" || { echo "✗ 语法错误: $f"; exit 1; }
done
echo "✅ checks 语法通过"
exit 0
EOF

# ---------- commit-msg：保护循环（单一来源，从共享版复制） ----------
if [ ! -f "$CM_SOURCE" ]; then
    echo "✗ 共享版 commit-msg 缺失: $CM_SOURCE"
    exit 1
fi
# 同源守卫：`cat x > x` 会先把 x 截断成 0 字节（实测），这里硬挡住
if [ "$(cd "$(dirname "$CM_SOURCE")" && pwd)/$(basename "$CM_SOURCE")" \
   = "$(cd "$(dirname "$TARGET_MSG")" && pwd)/$(basename "$TARGET_MSG")" ]; then
    echo "✗ 源与目标是同一个文件（$CM_SOURCE）—— 复制会把共享 commit-msg 清空。已中止。"
    exit 2
fi
cat "$CM_SOURCE" > "$TARGET_MSG"

chmod +x "$TARGET_PRE" "$TARGET_MSG"
echo "✅ 工程仓库 hooks 已安装：pre-commit(语法门禁) + commit-msg(保护循环,单一来源复制)"
echo "   改动受保护文件必须带 [harness] 标记"
echo "   卸载：sh sandbox/install_engine_hooks.sh --uninstall"
