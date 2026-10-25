#!/bin/sh
# agentguard/sandbox/setup_machine.sh —— 新机器一键接入（2026-09-06）
# ============================================================
# 做三件事：
#   1. 检测 config/harness.local.toml 缺失 → 从 example 复制生成，提示编辑后重跑
#   2. 枚举所有「启用 且 仓库路径存在」的 gate，逐仓安装 pre-commit hook（复用 install_hooks.sh，带自检）
#   3. 汇总报告：成功/跳过明细 + 手动补装命令
# 用法（任意目录）：
#   sh <harness位置>/sandbox/setup_machine.sh
# 重复运行安全（幂等）：hook 重装覆盖为最新版；已通过的可 --dry-run 预览。
set -e
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
#     tests/packaging/test_interpreter_convention.py 拿本行与其余 sh 入口的同名标记对拍 ——
#     候选顺序只允许在标记行改，改完各处必须同步。
# 为什么内联、不 source 一个同级 _python.sh：本仓多条锁会搭**只拷单个脚本**的最小布局真跑
# （tests/engine/test_hooks_chain.py:238/261 只拷 setup_machine.sh），同级新文件不会被拷过去 → 那些锁当场失效。
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
    echo "  本脚本要靠它读生效配置（含 local 覆盖层）才能枚举 gate，已按 fail-closed 中止。"
    echo "  修复：装 Python 3.11+；或显式指定解释器：export HARNESS_PYTHON=python3"
    exit 2
fi
# 调用处一律写 $HARNESS_PY 且**不加引号**（"py -3" 需要按空格拆成两个词）。
# ------------------------------------------------------------------------------

DRY_RUN=0
[ "$1" = "--dry-run" ] && DRY_RUN=1

LOCAL="$HARNESS/config/harness.local.toml"
LEGACY_LOCAL="$HARNESS/config/harness.local.yaml"

# ---------- 第 1 步：local 覆盖层 ----------
# 旧版 YAML 已存在时沿用它（不强行改名，升级当天不打断现有机器）；否则生成 TOML。
if [ ! -f "$LOCAL" ] && [ -f "$LEGACY_LOCAL" ]; then
    LOCAL="$LEGACY_LOCAL"
fi
if [ ! -f "$LOCAL" ]; then
    cp "$HARNESS/config/harness.local.example.toml" "$LOCAL"
    echo "✅ 已生成 config/harness.local.toml（从模板复制）"
    echo ""
    echo "⚠ 下一步（必做）：用编辑器打开该文件，把 [repos] 段改成你机器的实际仓库路径。"
    echo "   · **只写你机器上确实存在的那几个仓**；没 clone 的留着空值（= 本机不受管，不报错、不影响别人）"
    echo "   · **别动键名**：漏写 / 拼错 = 真配置错，会阻断（模板里有一节专门讲这两种的区别）"
    echo "   然后重新运行本脚本完成 hook 安装："
    echo "     sh $HARNESS_WIN/sandbox/setup_machine.sh"
    echo ""
    exit 0
fi

case "$LOCAL" in
    *.toml) LOCAL_FMT="toml" ;;
    *)      LOCAL_FMT="yaml" ;;
esac
LOCAL_REL="${LOCAL#$HARNESS/}"

echo "=== guardrail-engine 新机器接入（engine=$HARNESS_WIN）==="
echo ""

# ---------- 第 1.5 步：确保 local 覆盖层里有 harness 键 ----------
# 统一解析链第 ② 环：让各 AI 客户端（GLOBAL_AGENTS.md、仓级 AGENTS.md 渲染）都能从这取路径。
# 幂等：键已存在且值对就不改；值不对或没键就补/修。
if [ "$LOCAL_FMT" = "toml" ]; then
    KEY_RE='^harness[[:space:]]*='
    if ! grep -q "$KEY_RE" "$LOCAL" 2>/dev/null; then
        cp "$LOCAL" "$LOCAL.bak-$(date +%Y%m%d)"
        if grep -q '^\[repos\]' "$LOCAL.bak-$(date +%Y%m%d)" 2>/dev/null; then
            awk '/^\[repos\]/{print "harness = \"'"$HARNESS_WIN"'\""}1' \
                "$LOCAL.bak-$(date +%Y%m%d)" > "$LOCAL"
        else
            { printf 'harness = "%s"\n\n' "$HARNESS_WIN"; cat "$LOCAL.bak-$(date +%Y%m%d)"; } > "$LOCAL"
        fi
        rm "$LOCAL.bak-$(date +%Y%m%d)"
        echo "✅ 已在 $LOCAL_REL 添加 harness 键"
    else
        CURRENT=$(grep "$KEY_RE" "$LOCAL" | head -1 | sed 's/^harness[[:space:]]*=[[:space:]]*//' | sed 's/^"//; s/"$//')
        if [ "$CURRENT" != "$HARNESS_WIN" ]; then
            cp "$LOCAL" "$LOCAL.bak-$(date +%Y%m%d)"
            sed -i "s|^harness[[:space:]]*=.*|harness = \"$HARNESS_WIN\"|" "$LOCAL"
            echo "✅ 已更新 $LOCAL_REL 的 harness 键（$CURRENT → $HARNESS_WIN）"
        fi
    fi
else
    if ! grep -q "^harness:" "$LOCAL" 2>/dev/null; then
        if grep -q "^repos:" "$LOCAL" 2>/dev/null; then
            cp "$LOCAL" "$LOCAL.bak-$(date +%Y%m%d)"
            awk '/^repos:/{print "harness: '"$HARNESS_WIN"'"}1' "$LOCAL.bak-$(date +%Y%m%d)" > "$LOCAL"
            rm "$LOCAL.bak-$(date +%Y%m%d)"
            echo "✅ 已在 $LOCAL_REL 添加 harness: 键（旧版 YAML）"
        else
            echo "" >> "$LOCAL"
            echo "harness: $HARNESS_WIN" >> "$LOCAL"
            echo " $LOCAL_REL 格式异常（缺 repos: 段），已追加 harness: 键到末尾"
        fi
    else
        CURRENT=$(grep "^harness:" "$LOCAL" | head -1 | sed 's/^harness:[[:space:]]*//')
        if [ "$CURRENT" != "$HARNESS_WIN" ]; then
            cp "$LOCAL" "$LOCAL.bak-$(date +%Y%m%d)"
            sed -i "s|^harness:.*|harness: $HARNESS_WIN|" "$LOCAL"
            echo "✅ 已更新 $LOCAL_REL 的 harness: 键（$CURRENT → $HARNESS_WIN）"
        fi
    fi
fi

# ---------- 第 1.6 步：写指针文件（新 ~/.guardrail-engine/，已有旧 ~/.agentguard/ 则沿用）----------
# 业务仓 AGENTS.md 的「路径约定」让 AI 按顺序取工程根：① $GUARDRAIL_ENGINE_DIR
# （旧名 $GUARDRAIL_ENGINE_DIR / $HARNESS_DIR 兼容）② 本文件。
# 不写本文件 → 同事跑完 setup_machine.sh 后第 ② 级落空，AI 在业务仓里照样解析不出 <HARNESS>
# （指针写法就白注入了）。此前只有 install_shim.py / bootstrap_agent_env.sh 写它，
# 而标准三步接入只跑本脚本 —— 缺口就在这。
# 幂等：内容已是当前工程根就不重写。
if [ -d "$HOME/.guardrail-engine" ] || [ ! -d "$HOME/.agentguard" ]; then
    CFG_HOME="$HOME/.guardrail-engine"
else
    CFG_HOME="$HOME/.agentguard"       # 旧机器继续用旧目录（不迁移、不丢配置）
fi
if [ ! -f "$CFG_HOME/harness.path" ] || [ "$(cat "$CFG_HOME/harness.path" 2>/dev/null)" != "$HARNESS_WIN" ]; then
    if [ "$DRY_RUN" = "1" ]; then
        # dry-run 只预览：不建目录、不落文件，否则"预览"就有了副作用
        echo "⏭️  [dry-run] 将写 $CFG_HOME/harness.path → $HARNESS_WIN"
    else
        mkdir -p "$CFG_HOME"
        printf '%s\n' "$HARNESS_WIN" > "$CFG_HOME/harness.path"
        echo "✅ 已写 $CFG_HOME/harness.path → $HARNESS_WIN"
    fi
else
    echo "⏭️  $CFG_HOME/harness.path 已是当前工程根，未改动"
fi

# ---------- 第 2 步：枚举 gate，逐仓装 hook ----------
# 用 python 读 config（含 local 覆盖层），输出 "gate名<TAB>仓库路径" 行
GATES=$($HARNESS_PY - "$HARNESS_WIN" <<'PYEOF'
import sys, os
harness = sys.argv[1]
sys.path.insert(0, os.path.join(harness, "harness"))
from _config import all_gates
for name, g in sorted(all_gates().items()):
    if not g.get("enabled"):
        continue
    rp = g.get("_repo_path") or ""
    print(f"{name}\t{rp}\t{'OK' if os.path.isdir(rp) else 'MISSING'}")
PYEOF
) || { echo "✗ 读取 config 失败（检查 $LOCAL_REL 语法：python -c 'import tomllib,sys;tomllib.load(open(sys.argv[1],\"rb\"))' $LOCAL 可自验）"; exit 1; }

echo "--- 逐仓安装 pre-commit hook ---"
# L2（评审 v7）：本循环在**管道子壳**里 —— 禁止在其中用 `+=1` 累加计数（子壳里的变量改不到外面，
# 会变成"看着在跑、其实没数"）。需要计数请用 `grep -c` 在循环外重算（参照 doctor.sh 的 N_HOOK_* 模式）。
echo "$GATES" | while IFS="$(printf '\t')" read -r GATE REPO STATUS; do
    [ -z "$GATE" ] && continue
    # 评审 C-1：Python 输出在 Windows 上以 \r\n 结尾，命令替换后比较恒假 → 先裁 \r。
    STATUS=${STATUS%$'\r'}
    if [ "$STATUS" = "MISSING" ]; then
        echo "  ⊘ $GATE: 仓库路径不存在（$REPO）——先在 $LOCAL_REL 改成你机器的路径"
    else
        if [ "$DRY_RUN" = "1" ]; then
            echo "  [dry-run] $GATE -> $REPO"
        else
            (cd "$REPO" && sh "$HARNESS/sandbox/install_hooks.sh" "$GATE") \
              && echo "  ✅ $GATE" \
              || echo "  ✗ $GATE 安装失败（见上方自检报错）"
        fi
    fi
done

# ---------- 第 3 步：验证 + 汇总 ----------
echo ""
echo "--- 门禁自检（每端抽 1 个 gate 跑 auto-gate 验证链路）---"
# L2（评审 v7）：本循环在**管道子壳**里 —— 禁止在其中用 `+=1` 累加计数（子壳里的变量改不到外面，
# 会变成"看着在跑、其实没数"）。需要计数请用 `grep -c` 在循环外重算（参照 doctor.sh 的 N_HOOK_* 模式）。
echo "$GATES" | while IFS="$(printf '\t')" read -r GATE REPO STATUS; do
    [ -z "$GATE" ] && continue
    # 评审 C-1：Python 输出在 Windows 上以 \r\n 结尾，命令替换后比较恒假 → 先裁 \r。
    STATUS=${STATUS%$'\r'}
    [ "$STATUS" = "OK" ] || continue
    cd "$REPO" && $HARNESS_PY "$HARNESS_WIN/harness/agents/auto-gate.py" >/dev/null 2>&1 \
      && echo "  ✅ $GATE: auto-gate 链路正常" \
      || echo "  ⚠ $GATE: auto-gate 退出码非 0（若是存量问题属预期，看首次全量输出确认）"
done

echo ""
echo "--- doctor 终检（端到端探针：拦的是真违规还是崩溃，一眼见分晓）---"
# 2026-09-10 新增：门禁装上 ≠ 门禁活着。doctor 做四项自检（UTF-8 兜底 / R9 双向探针 /
# hook gate 活性 / skill 软链），「拦下来且理由是真实违规」才算接入闭环。
# 单独跑：sh $HARNESS_WIN/sandbox/doctor.sh
if [ "$DRY_RUN" = "1" ]; then
    echo "  [dry-run] 跳过 doctor（正式接入后跑：sh $HARNESS_WIN/sandbox/doctor.sh）"
else
    sh "$HARNESS/sandbox/doctor.sh" || echo "  ⚠ doctor 有 ❌ 项，按上方输出处理后重跑"
fi

echo ""
echo "=== 完成。补装/重装单个 gate：sh $HARNESS_WIN/sandbox/install_hooks.sh <gate名> ==="
