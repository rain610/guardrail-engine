#!/bin/sh
# guardrail-engine/sandbox/install_hooks.sh —— 端仓库接入 pre-commit 门禁
# ============================================================
# 用法（在端仓库/worktree 根执行）：
#   sh <harness工程>/sandbox/install_hooks.sh [gate名]
#   gate 名可省略——省略时由 pre-commit.py 按 cwd 自动识别（推荐）。
#   example: sh <HARNESS>/sandbox/install_hooks.sh
#            sh <HARNESS>/sandbox/install_hooks.sh example
#
# 2026-09-12 hooksPath 升级：
#   旧版把 hook 文件拷贝到各仓 .git/hooks/——gate 写死、改 hook 要逐仓重装。
#   新版用 git config core.hooksPath 指向 harness 工程的共享 hooks-dir：
#     - 改 hook 只改 harness 一处，所有接入仓即时生效
#     - worktree 天然共享主仓 config，一次配置全 worktree 生效
#   旧拷贝的 hook 会被本脚本清理（避免新旧双份执行）。
set -e
GATE=${1:-}
# 从脚本自身位置推导工程根（去硬编码，换机器/挪目录无需改本文件）
HARNESS=$(cd "$(dirname "$0")/.." && pwd)
# git-bash 下 pwd 是 /d/... 形态，写进 git config 后传给 Windows python 会被解析成
# D:\d\...（2026-09-06 实测踩坑）。统一用 cygpath 转成 D:/... 形态（sh 与 python 都认）。
if command -v cygpath >/dev/null 2>&1; then
    HARNESS=$(cygpath -m "$HARNESS")
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
# 为什么放在两条 `git rev-parse` **之前**：没有本段时 WSL 上的形状是自检那步用裸 python
# 直接 command not found（stderr 又被 2>/dev/null 丢掉）→ 打印「✗ 自检失败：python 无法打开
# 共享分发器」，把「没装解释器」误诊成「路径不对」；而真正执行钩子的是 sh、不是 python。
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
    echo "  本脚本装完钩子后要自检（且钩子本体也要用它）—— 已按 fail-closed 中止，未做任何改动。"
    echo "  修复：装 Python 3.11+；或显式指定解释器：export HARNESS_PYTHON=python3"
    exit 2
fi
# 调用处一律写 $HARNESS_PY 且**不加引号**（"py -3" 需要按空格拆成两个词）。
# ------------------------------------------------------------------------------

REPO_ROOT=$(git rev-parse --show-toplevel)
HOOKS_DIR=$(git rev-parse --git-path hooks)
SHARED_DIR="$HARNESS/harness/hooks-dir"

# 自检：共享分发器与 pre-commit.py 必须真实存在且 python 可打开
# （防 MSYS/Windows 路径形态差异导致的「配好了但永远执行不到」假安装）
$HARNESS_PY -c "open(r'$SHARED_DIR/pre-commit').close()" 2>/dev/null \
  || { echo "✗ 自检失败：python 无法打开共享分发器 $SHARED_DIR/pre-commit"; exit 1; }
$HARNESS_PY -c "open(r'$HARNESS/harness/hooks/pre-commit.py').close()" 2>/dev/null \
  || { echo "✗ 自检失败：python 无法打开 pre-commit.py（HARNESS=$HARNESS）"; exit 1; }

# 清理旧拷贝模式遗留的 hook（若存在且是 harness 生成过的——避免新旧双份执行）。
# 注意：设置 hooksPath 后 `git rev-parse --git-path hooks` 返回的是 hooksPath 目录本身
# （即共享分发器所在目录）——绝不能删那里，否则分发器自毁、后续所有仓安装全挂
# （2026-09-12 实测：批量迁移第一个仓之后全部失败，就是这里把分发器当旧 hook 删了）。
SHARED_TARGET="$SHARED_DIR/pre-commit"
OLD_TARGET="$HOOKS_DIR/pre-commit"
if [ -f "$OLD_TARGET" ] && [ "$OLD_TARGET" != "$SHARED_TARGET" ] \
   && [ "$(cd "$(dirname "$OLD_TARGET")" && pwd)" != "$(cd "$SHARED_DIR" && pwd)" ] \
   && grep -q "agentguard" "$OLD_TARGET" 2>/dev/null; then
    rm -f "$OLD_TARGET"
    echo "↺ 已清理旧拷贝模式 hook: $OLD_TARGET"
fi

# ---- 评审 v8（Codex P1）：接管前枚举并备份端仓原有 hook ----------------------------
# core.hooksPath 是"整目录接管"：git 只会执行 hooksPath 下的 hook，仓里原有的
# pre-commit / commit-msg / pre-push 等会**静默失效**。安装时把它们备份到
# <gitdir>/agentguard-original-hooks/，共享分发器会在跑门禁前**链式调用**同名备份，
# 做到"接管但不吞掉别人的质量流程"。
# 评审 v3 P1（2026-09-15）：「真正生效的 hooks 目录」与「备份根」的判定
#   ① 已有**自定义 core.hooksPath** → 那才是实际生效的目录（默认 .git/hooks 此时是死的，
#      只枚举默认目录 = 静默吞掉别人已有的 hook）；
#   ② worktree：真实共享 hooks 在 **common-dir** 下，而 absolute-git-dir 指向
#      .git/worktrees/<名>（那里没有别人的 hook）；备份也要落在 common-dir 才共享可见；
#   ③ git 在仓内会返回 ".git" 这类**相对**路径 → 必须按仓根解析，不能直接当绝对路径用。
COMMON=$(git rev-parse --git-common-dir 2>/dev/null || echo "")
case "$COMMON" in
    "" ) : ;;
    /* | [A-Za-z]:* ) : ;;
    * ) COMMON="$REPO_ROOT/$COMMON" ;;
esac
GITDIR=$(git rev-parse --absolute-git-dir 2>/dev/null || echo "")
EXISTING_HP=$(git config --get core.hooksPath 2>/dev/null || echo "")
if [ -n "$EXISTING_HP" ] && [ "$EXISTING_HP" != "$SHARED_DIR" ]; then
    REAL_HOOKS="$EXISTING_HP"
    case "$REAL_HOOKS" in
        /* | [A-Za-z]:* ) : ;;
        * ) REAL_HOOKS="$REPO_ROOT/$REAL_HOOKS" ;;
    esac
elif [ -n "$COMMON" ]; then
    REAL_HOOKS="$COMMON/hooks"
else
    REAL_HOOKS="$GITDIR/hooks"
fi
BACKUP_BASE="${COMMON:-$GITDIR}"
BACKUP="$BACKUP_BASE/agentguard-original-hooks"
BK_OK=0
BK_FAIL=0
# 评审 v9（V9-10）：把**安装前**的 core.hooksPath 值与它当时实际生效的目录落档。
# 此前只备份了文件、只打印了来源，值本身没有存档 → 事后无法知道"原来指的是哪、怎么还原"。
HP_ARCHIVE="$BACKUP/PREVIOUS-hooksPath.txt"
mkdir -p "$BACKUP"
# 评审 v13 中 17：**存档只在首装时写，重装只追加一行**。
# 原先每次安装都 `> "$HP_ARCHIVE"` 整体重写。二次安装时 EXISTING_HP 已经是我们自己上次
# 写进去的共享目录 —— 重写等于把「真正的原值」永久抹掉，卸载时再也回不到接入前的状态，
# 而卸载侧却照样打印「回滚完成」（实测：原值 .myhooks 未复原）。
# 存档是**历史事实**，不是当前快照：事实被覆盖，就再也查不出来了。
if [ -f "$HP_ARCHIVE" ]; then
    echo "再次安装：$(date '+%Y-%m-%d %H:%M:%S')（本次安装前 core.hooksPath：${EXISTING_HP:-<未设置>}）" >> "$HP_ARCHIVE"
    echo "ℹ 已存在安装存档，保留首装时记录的原值：$HP_ARCHIVE"
else
    {
        echo "记录时间：$(date '+%Y-%m-%d %H:%M:%S')"
        echo "安装前 core.hooksPath：${EXISTING_HP:-<未设置>}"
        echo "安装前实际生效的 hooks 目录：$REAL_HOOKS"
        echo "安装后 core.hooksPath：$SHARED_DIR"
        echo "还原方法：cli.py hooks uninstall --repo <本仓>（读本存档自动还原）"
        echo "          手工：git config core.hooksPath \"<上面那个安装前的值>\"（未设置则用 --unset）"
    } > "$HP_ARCHIVE"
fi
if [ -n "$REAL_HOOKS" ] && [ -d "$REAL_HOOKS" ]; then
    FOUND=""
    for f in "$REAL_HOOKS"/*; do
        [ -f "$f" ] || continue
        case "$f" in *.sample) continue ;; esac
        grep -q "agentguard" "$f" 2>/dev/null && continue   # 自己旧的拷贝模式 hook，不算"别人的"
        FOUND="$FOUND $(basename "$f")"
    done
    if [ -n "$FOUND" ]; then
        mkdir -p "$BACKUP"
        for n in $FOUND; do
            # 评审 v10 §1-S1：原先 `|| true` 吞错后**无条件**打印「已备份」= 谎报。
            # 现在逐个计数，结尾按实际结果分支（失败逐条 ✗ + 汇总）。
            if cp -f "$REAL_HOOKS/$n" "$BACKUP/$n" 2>/dev/null; then
                BK_OK=$((BK_OK + 1))
            else
                BK_FAIL=$((BK_FAIL + 1))
                echo "      ✗ 备份失败：$n（源文件被占用或不可读？）"
            fi
            chmod +x "$BACKUP/$n" 2>/dev/null || true
        done
        if [ "$BK_FAIL" -eq 0 ]; then
            echo "⚠ 本仓原有 hook 会被 core.hooksPath 接管 —— 已备份（$BK_OK 个），并会在跑门禁前链式调用："
        else
            echo "⚠ 本仓原有 hook 会被 core.hooksPath 接管 —— 备份 $BK_OK 个成功、$BK_FAIL 个**失败**："
        fi
        for n in $FOUND; do echo "      · $n"; done
        echo "      备份位置：$BACKUP"
        # 低 23（评审 v13）：$() 里的 \" 是**多余转义** —— 命令替换内部自成一套引用语境，
        # 反斜杠不会被吃掉，于是那对引号被原样打进输出：
        #   实测（修前）  枚举来源：/d/repo/.git/hooks"（本仓原有自定义 core.hooksPath）"
        # 而这一行正是用户唯一能看出"我原来那几个 hook 挂在哪儿"的地方。
        # 修：去掉 $() 内部的反斜杠。（下面那行的引号同理，但它只是把中文引号吃掉、
        # 文案本身仍可读，故按最小改动不动它。）
        echo "      枚举来源：$REAL_HOOKS$([ -n "$EXISTING_HP" ] && echo "（本仓原有自定义 core.hooksPath）")"
        echo "      （链式调用的前提是它们放在该目录；删除该目录即恢复"只跑门禁"）"
    fi
fi

# 配置 core.hooksPath 指向共享分发器目录（幂等）
git config core.hooksPath "$SHARED_DIR"
echo "✅ core.hooksPath → $SHARED_DIR (repo=$REPO_ROOT${GATE:+, gate提示=$GATE})"
if [ -n "$GATE" ]; then
    echo "   说明: gate 参数仅作提示。实际 gate 由 pre-commit.py 按提交时的 cwd 自动识别"
    echo "        （worktree 纠偏），未注册的仓会被 fail-closed 拦截并给出注册指引。"
fi
echo "   绕过(紧急): git commit --no-verify"
echo "   验证: 改一个 .vue/.cs 后 git commit，应看到 [pre-commit] 输出"
