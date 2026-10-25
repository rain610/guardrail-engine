#!/bin/sh
# guardrail-engine/sandbox/doctor.sh —— 接入自检/终检（2026-09-10）
# ============================================================
# 四项检查，逐项 ✅/⚠/❌：
#   1. 环境：python 可用 + 全部门禁入口脚本带 UTF-8 输出兜底
#      （GBK 控制台防崩溃兜底漏一个入口就在那个入口崩——逐文件 grep 核验）。
#   2. 门禁端到端探针（核心）：在临时仓里写一个 console.log 违规文件 →
#      run_gate 应 rc=1 且点名 NO-CONSOLE；改成合规写法 → 应 rc=0。
#      「拦下来」≠「拦对了」：GBK 崩溃 / worktree ValueError 也会 rc≠0，
#      必须核对拦截理由是真实规则违规。双向验证，只测单向两头都测不出。
#      探针不依赖任何受管仓。
#   3. hook 活性：主仓 .git/hooks 里装的 gate 必须在 config 中 enabled——
#      防「装了 hook + gate 关着」双重失效静默放行（2026-09-08 实测教训）。
#   4. skill 软链：codebuddy / claude / opencode 的 guardrail-engine 软链可达（旧 agentguard 也算）
#      （仅加速项，缺失不拦接入）。
#   另含 1.5/1.6 解析链自检（harness: 键 / 指针文件新鲜度）——后补项，编号沿用。
# 用法：sh <harness>/sandbox/doctor.sh
# 退出码：0=全绿（⚠ 不算失败） / 1=有 ❌ 必须处理。
set -u
HARNESS=$(cd "$(dirname "$0")/.." && pwd)
if command -v cygpath >/dev/null 2>&1; then
    HARNESS_WIN=$(cygpath -m "$HARNESS")
else
    HARNESS_WIN="$HARNESS"
fi
FAIL=0; WARN=0
fail() { echo "  ❌ $1"; FAIL=$((FAIL+1)); }
warn() { echo "  ⚠ $1"; WARN=$((WARN+1)); }
ok()   { echo "  ✅ $1"; }

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
    # 本脚本后面每一项（R9 探针 / hook 活性 / guard 挂死探针）都要 python：
    # 与其让它们各自崩成看不懂的报错，不如在这里就说清是什么坏了 —— 且**不**走下面
    # "无可用后端仓"那条 warn 分支（那条会把这台机器误诊成"仓库没 clone"）。
    fail "找不到可用的 Python 3 解释器（已试：HARNESS_PYTHON / python3 / python / py -3）"
    echo "       装 Python 3.11+ 后重跑；或显式指定解释器：export HARNESS_PYTHON=python3"
    echo "       （下面三项探针全靠它，无处可退；本次不继续，免得报出一串与真相无关的错）"
    exit 1
fi
# 调用处一律写 $HARNESS_PY 且**不加引号**（"py -3" 需要按空格拆成两个词）。
# ------------------------------------------------------------------------------

echo "=== guardrail-engine doctor 自检（engine=$HARNESS_WIN）==="
echo ""

# ---------- 1/4 环境 ----------
echo "--- 1/4 环境检查（python + UTF-8 兜底）---"
# 上方的解释器解析已在拿不到 python 时 fail+exit 1，所以这里的真分支恒成立；
# 保留 if/else 的形状（而不是摊平）是为了将来谁把上面那段早退删掉时，本段仍能自行降级。
if [ -n "$HARNESS_PY" ]; then
    VER=$($HARNESS_PY --version 2>&1)
    ok "python 可用（$VER；解释器：$HARNESS_PY）"
    # 2026-09-13 评审 v3 H1：清单**同源生成**（sandbox/list_entries.py），不再手工列举 ——
    # 手工清单漏了新入口（钩子 shim），同一个编码坑出现三次。
    if $HARNESS_PY "$HARNESS_WIN/sandbox/list_entries.py" --check >/tmp/harness_entries.$$ 2>&1; then
        N_ENTRIES=$($HARNESS_PY "$HARNESS_WIN/sandbox/list_entries.py" 2>/dev/null | wc -l | tr -d " ")
        ok "全部门禁入口脚本带 UTF-8 输出兜底（$N_ENTRIES 个入口，清单机器推导）"
    else
        fail "有入口缺 UTF-8 兜底（GUI/GBK 控制台会在该入口崩溃误拦）："
        sed "s/^/     /" /tmp/harness_entries.$$
    fi
    rm -f /tmp/harness_entries.$$
else
    fail "python 不可用（需 Python 3.11+，纯标准库无第三方依赖）"
fi
echo ""

# ---------- 1.5/4 统一解析链（local 覆盖层的 harness 键）----------
echo "--- 1.5/4 统一解析链（config/harness.local.toml → harness 键）---"
LOCAL_CFG="$HARNESS/config/harness.local.toml"
if [ ! -f "$LOCAL_CFG" ] && [ -f "$HARNESS/config/harness.local.yaml" ]; then
    LOCAL_CFG="$HARNESS/config/harness.local.yaml"   # 旧版兼容：没迁到 TOML 的机器接着说
fi
if [ ! -f "$LOCAL_CFG" ]; then
    warn "缺 config/harness.local.toml（跑 setup_machine.sh 生成）"
else
    LOCAL_REL="${LOCAL_CFG#$HARNESS/}"
    case "$LOCAL_CFG" in
        *.toml)
            HARNESS_KEY=$(grep '^harness[[:space:]]*=' "$LOCAL_CFG" 2>/dev/null | head -1 \
                          | sed 's/^harness[[:space:]]*=[[:space:]]*//' | sed 's/^"//; s/"$//')
            FIX_HINT="$LOCAL_REL 顶部加一行：harness = \"$HARNESS_WIN\""
            ;;
        *)
            HARNESS_KEY=$(grep '^harness:' "$LOCAL_CFG" 2>/dev/null | head -1 | sed 's/^harness:[[:space:]]*//')
            FIX_HINT="$LOCAL_REL 的 repos: 之前加一行：harness: $HARNESS_WIN"
            ;;
    esac
    if [ -z "$HARNESS_KEY" ]; then
        warn "$LOCAL_REL 缺 harness 键（各 AI 客户端渲染 GLOBAL_AGENTS.md/仓级 AGENTS.md 要靠它取路径）"
        echo "  修复：在 $FIX_HINT"
    elif [ "$HARNESS_KEY" = "$HARNESS_WIN" ]; then
        ok "$LOCAL_REL → harness = $HARNESS_KEY"
    else
        warn "$LOCAL_REL 的 harness 键值不对（$HARNESS_KEY ≠ $HARNESS_WIN）"
        echo "  修复：重跑 sh $HARNESS_WIN/sandbox/setup_machine.sh（会就地校正该键）"
    fi
fi
echo ""

# ---------- 1.6/4 指针文件新鲜度（新 ~/.guardrail-engine/harness.path，旧 ~/.agentguard/ 也认）----------
echo "--- 1.6/4 指针文件新鲜度（~/.guardrail-engine/ 或 ~/.guardrail-engine/）---"
# 2026-10-15 批次 5（评审遗留项）：业务仓 AGENTS.md 的「路径约定」第 2 级靠这个文件；
# 挪仓/换克隆后它会变**陈旧指针** —— AI 拿旧路径敲命令、屏幕上一切正常（同 09-19 那族
# 「看起来装好了其实没有」的失效）。判据：文件存在时，指向的目录必须含 harness/run_gate.py。
PTR_FILE="$HOME/.guardrail-engine/harness.path"
[ -f "$PTR_FILE" ] || PTR_FILE="$HOME/.agentguard/harness.path"
if [ ! -f "$PTR_FILE" ]; then
    echo "  ℹ 未写指针文件（$PTR_FILE）—— 新机器没跑过接入脚本时可接受；"
    echo "    但业务仓 AGENTS.md 的路径约定第 2 级要靠它，建议补跑：sh $HARNESS_WIN/sandbox/setup_machine.sh"
else
    PTR_VAL=$(tr -d '\r\n' < "$PTR_FILE" 2>/dev/null)
    if [ -z "$PTR_VAL" ]; then
        fail "指针文件为空（$PTR_FILE）——AI 在业务仓里解析不出 <HARNESS>；重跑 setup_machine.sh"
    elif [ ! -f "$PTR_VAL/harness/run_gate.py" ]; then
        fail "指针指向的目录缺 harness/run_gate.py（$PTR_VAL）——挪仓/换克隆后的陈旧指针；重跑 setup_machine.sh 校正"
    else
        ok "指针有效：$PTR_VAL"
        if [ "$PTR_VAL" != "$HARNESS_WIN" ]; then
            warn "指针指向另一个克隆（$PTR_VAL ≠ 当前 $HARNESS_WIN）——多克隆场景可忽略；否则重跑 setup_machine.sh"
        fi
    fi
fi
echo ""

# ---------- 2/4 门禁端到端探针 ----------
echo "--- 2/4 门禁端到端探针（违规→拦 / 修复→放行）---"
# 探针用内置 example gate：它的规则随引擎出货，与用户配置的业务仓无关，
# 因此可以在临时仓里做双向验证（违规→rc=1 / 合规→rc=0）。
# example 未启用时不做猜测式选 gate —— 自定义规则里没有"必然违规"的通用样本。
PROBE_INFO=$($HARNESS_PY - "$HARNESS_WIN" <<'PYEOF'
import sys, os
harness = sys.argv[1]
sys.path.insert(0, os.path.join(harness, "harness"))
from _config import all_gates
gates = all_gates()
print("example" if (gates.get("example") or {}).get("enabled") else "")
PYEOF
)
GATE=$(printf '%s' "$PROBE_INFO" | cut -f1)
if [ -z "$GATE" ]; then
    warn "无可用探针：example gate 未启用 —— 端到端探针跳过；如需验证自定义规则，请在目标仓跑 run_gate.py <gate> --all"
elif [ "$GATE" = "example" ]; then
    # 用临时仓验证引擎本身（--repo 覆盖，不需要 local.yaml 配过任何仓）
    PROBE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/agentguard-doctor.XXXXXX") || {
        fail "无法创建临时目录（mktemp -d 失败）——端到端探针无法执行"
        PROBE_DIR=""
    }
    if [ -n "$PROBE_DIR" ]; then
        if command -v cygpath >/dev/null 2>&1; then
            PROBE_DIR_WIN=$(cygpath -m "$PROBE_DIR")
        else
            PROBE_DIR_WIN="$PROBE_DIR"
        fi
        PROBE="$PROBE_DIR/demo.js"
        PROBE_WIN="$PROBE_DIR_WIN/demo.js"
        cleanup_example_probe() { rm -rf "$PROBE_DIR"; }
        trap cleanup_example_probe EXIT
        echo "  探针 gate=example（临时仓 $PROBE_DIR_WIN，不依赖业务仓）"
        cat > "$PROBE" <<'EOF'
console.log("doctor probe");
EOF
        rc=0
        OUT=$(HARNESS_ENTRY=doctor $HARNESS_PY "$HARNESS_WIN/harness/run_gate.py" \
              example --repo "$PROBE_DIR_WIN" "$PROBE_WIN" 2>&1) || rc=$?
        if [ "$rc" = "0" ]; then
            fail "违规探针未被拦截（rc=0）——门禁链路失效：查 gate 注册 / 示例规则是否被禁用"
        elif [ "$rc" != "1" ]; then
            fail "探针 rc=$rc（期望 1）——疑似环境/配置崩溃而非真实拦截，输出尾部："
            printf '%s\n' "$OUT" | tail -5 | sed 's/^/      /'
        elif ! printf '%s' "$OUT" | grep -q "NO-CONSOLE"; then
            fail "发生了拦截但输出无 NO-CONSOLE 标记——拦的可能是崩溃不是违规，输出尾部："
            printf '%s\n' "$OUT" | tail -5 | sed 's/^/      /'
        else
            ok "违规探针被 NO-CONSOLE 真实拦截（rc=1，违规标记确认）"
            cat > "$PROBE" <<'EOF'
const answer = 42;
EOF
            rc2=0
            OUT2=$(HARNESS_ENTRY=doctor $HARNESS_PY "$HARNESS_WIN/harness/run_gate.py" \
                   example --repo "$PROBE_DIR_WIN" "$PROBE_WIN" 2>&1) || rc2=$?
            if [ "$rc2" = "0" ]; then
                ok "合规版放行（rc=0）——双向验证通过：拦的是真违规，不是崩溃"
            else
                fail "合规版仍被拦（rc=$rc2）——门禁过严或探针形态不对，输出尾部："
                printf '%s\n' "$OUT2" | tail -5 | sed 's/^/      /'
            fi
        fi
        cleanup_example_probe
        trap - EXIT
    fi
fi
echo ""

# ---------- 2.5/4 分发器完整性（G2：缺环时静默关闭全部提交期门禁）----------
echo "--- 2.5/4 分发器完整性（hooks-dir → pre-commit.py）---"
if [ -f "$HARNESS/harness/hooks/pre-commit.py" ]; then
    ok "pre-commit 分发器的实现文件在位"
else
    fail "缺 harness/hooks/pre-commit.py —— 分发器会 fail-closed 阻断提交（G2）；请检查工程仓完整性"
fi
echo ""

# ---------- 3/4 hook 活性 ----------
echo "--- 3/4 hook 活性检查（hooksPath 指向共享分发器 + gate enabled）---"
# 2026-09-12 适配 hooksPath 模式（外部评审 P1-2）：端仓已无 .git/hooks 拷贝 hook，
# 改查每仓 git config core.hooksPath 是否指向本 harness 的 hooks-dir。
HOOK_REPORT=$($HARNESS_PY - "$HARNESS_WIN" <<'PYEOF'
import sys, os
harness = sys.argv[1]
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, os.path.join(harness, "harness"))
from _config import all_gates
import hook_dirs                                   # 与安装脚本同源的 hooks/备份目录解析（评审 M3）
gates = all_gates()
SHARED = os.path.normcase(os.path.abspath(os.path.join(harness, "harness", "hooks-dir")))
seen = set()
found_any = False
for name, g in sorted(gates.items()):
    rp = g.get("_repo_path") or ""
    if not rp or not os.path.isdir(rp) or rp in seen:
        continue
    seen.add(rp)
    import subprocess
    r = subprocess.run(["git", "-C", rp, "config", "--local", "core.hooksPath"],
                       capture_output=True, text=True, timeout=15)
    hp = (r.stdout or "").strip()
    if not hp:
        continue
    found_any = True
    hp_norm = os.path.normcase(os.path.abspath(hp))
    if hp_norm != SHARED:
        print(f"WARN\t{rp}\tcore.hooksPath 指向 {hp}（非本 harness 共享分发器）——可能是旧安装/他工具 hook，人工确认")
    elif not g.get("enabled"):
        print(f"FAIL\t{rp}\thooksPath 已接入但 gate {name} 在 config 中 disabled——提交静默放行（双重失效），启用该 gate 或移除 hooksPath")
    else:
        disp = os.path.join(hp, "pre-commit")
        if not os.path.isfile(disp):
            print(f"FAIL\t{rp}\t共享分发器缺失: {disp}")
        else:
            # 评审 v8（Codex P1）：hooksPath 是整目录接管 —— 若仓里原有 hook 没被备份/链式接管，
            # 它们的质量流程就被静默吞掉了。安装脚本会备份到 <common-dir>/agentguard-original-hooks/。
            # 2026-09-23 评审 M3：**扫的目录必须与安装脚本同源**。原先拼 `<absolute-git-dir>/hooks`，
            # 对 worktree 是错的：git 的 hook 查找走 common-dir，worktree 专属 gitdir 下的 hooks/
            # **git 从来不执行**（实测：那目录里放可执行 pre-commit，提交时根本没跑），我们也没接管过它 ——
            # 于是扫出假 ❌，还把指引写成"重跑 install_hooks.sh 会备份"，而安装脚本按 COMMON 解析、
            # 枚举不到那个目录，那句承诺对该文件永远不成立。解析规则见 harness/hook_dirs.py。
            git = lambda *a: subprocess.run(["git", "-C", rp] + list(a), capture_output=True,
                                            text=True, errors="replace").stdout.strip()
            real, backup = hook_dirs.resolve(git("rev-parse", "--absolute-git-dir"),
                                             git("rev-parse", "--git-common-dir"), rp)
            swallowed = []
            if real and os.path.isdir(real):
                for f in sorted(os.listdir(real)):
                    if f.endswith(".sample"):
                        continue
                    fp = os.path.join(real, f)
                    if not os.path.isfile(fp):
                        continue
                    try:
                        body = open(fp, encoding="utf-8", errors="replace").read()
                    except Exception:
                        body = ""
                    if "agentguard" in body:
                        continue
                    # 有原 hook 且没备份 → 就是被吞了
                    if not os.path.isfile(os.path.join(backup, f)):
                        swallowed.append(f)
            if swallowed:
                print(f"FAIL\t{rp}\thooksPath 已接管但以下原 hook 未备份/链式：{', '.join(swallowed)}"
                      f"（重跑 install_hooks.sh 会备份到 {backup}/）")
            else:
                print(f"OK\t{rp}\thooksPath→共享分发器, gate {name} enabled")
if not found_any:
    print("NONE\t-\t本机未发现任何接入 hooksPath 的端仓（跑过 install_hooks.sh 才有）")
PYEOF
)
printf '%s\n' "$HOOK_REPORT" | while IFS="$(printf '\t')" read -r VERDICT WHERE MSG; do
    [ -z "$VERDICT" ] && continue
    case "$VERDICT" in
        OK)   ok "$WHERE：$MSG" ;;
        WARN) warn "$WHERE：$MSG" ;;
        FAIL) fail "$WHERE：$MSG" ;;
        NONE) warn "$MSG" ;;
    esac
done
N_HOOK_FAIL=$(printf '%s\n' "$HOOK_REPORT" | grep -c "^FAIL" || true)
FAIL=$((FAIL + N_HOOK_FAIL))
# K6（评审 v6）：上面那个 while 是**管道右侧=子壳**，里面的 warn()/WARN++ 全部丢失 ——
# FAIL 早已用同款补丁重算，WARN 漏了（本机实测 33 条 ⚠ 显示在屏、汇总仍"⚠ 0 / 全绿"）。
# NONE 走的也是 warn()，一并计入。
N_HOOK_WARN=$(printf '%s\n' "$HOOK_REPORT" | grep -c "^WARN" || true)
N_HOOK_NONE=$(printf '%s\n' "$HOOK_REPORT" | grep -c "^NONE" || true)
WARN=$((WARN + N_HOOK_WARN + N_HOOK_NONE))
echo ""

# ---------- 3b/4 guard 闭 stdin 挂死探针（2026-09-11 P0 回归锁）----------
echo "--- 3b/4 guard 挂死探针（管道 stdin 不 EOF，须 2s 内返回）---"
# 外部评审实测 P0：guard 旧版在 stdin 非 tty 时无条件 sys.stdin.read()，GUI/CI 触发
# hook 时 stdin 是保持打开的管道 → 永久阻塞，commit 静默挂死。此探针是该 bug 的永久回归锁。
GUARD_OUT=$(sleep 30 | timeout 2 $HARNESS_PY "$HARNESS_WIN/checks/harness_guard.py" example 2>&1)
GRC=$?
if [ "$GRC" = "124" ]; then
    fail "guard 被管道 stdin 挂死（2s 超时强杀）——GUI/CI commit 会静默卡死，立即修 _commit_msg() 的 stdin 读取"
elif [ "$GRC" = "0" ] || [ "$GRC" = "1" ]; then
    ok "guard 对非 EOF 管道 stdin 不阻塞（rc=$GRC，2s 内返回）"
else
    warn "guard 退出码 $GRC 非预期（0/1 之外）——人工确认输出：$GUARD_OUT"
fi
echo ""

# ---------- 4/4 skill 软链 ----------
echo "--- 4/4 skill 软链检查（自动加载加速项，缺失不拦接入）---"
for SKILL_DIR in "$HOME/.codebuddy/skills" "$HOME/.claude/skills" "$HOME/.config/opencode/skills"; do
    LINK="$SKILL_DIR/guardrail-engine"
    LINK_OLD="$SKILL_DIR/agentguard"
    TOOL=$(basename "$(dirname "$SKILL_DIR")")
    if [ -e "$LINK/SKILL.md" ] || [ -e "$LINK_OLD/SKILL.md" ]; then
        ok "skill 软链可达（$TOOL）"
    else
        warn "skill 软链缺失/断链（$TOOL）——不影响门禁，仅少自动加载；可选修复：ln -s \"$HARNESS_WIN/integrations/guardrail-engine-skill\" \"$LINK\""
    fi
done
echo ""

# ---------- 汇总 ----------
echo "=== doctor 结果：❌ $FAIL 项必须处理 / ⚠ $WARN 项提示 ==="
if [ "$FAIL" -gt 0 ]; then
    echo "存在 ❌ 项：处理后再交付同事使用（⚠ 为提示，不算失败）"
    exit 1
fi
if [ "$WARN" -gt 0 ]; then
    echo "全绿（❌ 0 项），另附 $WARN 条提示待人工确认：clone → setup → 本报告。"
else
    echo "全绿。接入完成：clone → setup → 本报告。"
fi
exit 0
