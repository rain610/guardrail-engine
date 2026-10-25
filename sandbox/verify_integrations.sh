#!/bin/sh
# =====================================================================================
# verify_integrations.sh —— 可重复的「AI 客户端 × 门禁」端到端验证
#
# 用法：
#   sh sandbox/verify_integrations.sh                 # 默认 gate=example，跑全部可用客户端
#   sh sandbox/verify_integrations.sh <gate>          # 换受测 gate（会解析出它的仓库路径）
#   sh sandbox/verify_integrations.sh example claude codex   # 只跑指定客户端
#
# 每个客户端一趟：
#   1. 在受测仓新建含 3 处违规的探针文件（纯 LF 行尾 + console.log + 空壳 v-auth）
#   2. 用该客户端的**非交互入口**让 AI 自己写这个文件（真跑，不是模拟）
#   3. 断言两件事：① 门禁日志新增一条该 gate 的记录（门禁真被触发）
#                  ② 客户端输出里出现门禁结论（结论回灌到了模型）
#   4. 清理探针；最后比对 git status 与开跑前一致（绝不碰真实代码、不产生提交）
# 再跑一层：仓库级 pre-commit 兜底（暂存探针 → git hook run pre-commit → 断言非 0 → 撤暂存）
#
# 客户端分两类（结论里的措辞要区分，别把"自觉"当"强制"）：
#   强制型：claude(PostToolUse 钩子) / opencode(本仓插件) / codex(hooks.json+信任)
#           / codebuddy / zcode / qoder —— 失败即 退出码 1
#   自觉型：**当前为空**（机制保留：判定器按 --hard no 走"只标 ⚠ + 提示靠 git pre-commit 兜底"）
#   ★ 2026-09-23 更正（评审 L12）：本节此前写"codebuddy(无原生 hook，靠 skill/规则) → 未跑只标 ⚠"，
#     而下面第 192 行起早就把 codebuddy 归成**强制型**（2026-09-13 起它有 PostToolUse 钩子，
#     契约验证在本文件末尾那一节）。注释与代码说反了两个月 —— 照注释读，会以为 codebuddy 从没被真跑过。
#     ⚠️ 改这张名单时**两处一起改**（本节的分类 + 下面 for 循环里的 yes/no），否则又是同一个坑。
#
# 退出码：0=强制型全过；1=有强制型失败
# =====================================================================================
set -u
HARNESS="$(cd "$(dirname "$0")/.." && pwd)"

# git-bash 的 $(pwd) 给 /d/... 形态，**原生 python/git 打不开**（本脚本第一版就因此解析不到仓库路径）。
# 所有交给原生程序当参数的路径，一律先过 nat() 转成 D:/... 形态。
nat() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else (cd "$1" && pwd -W 2>/dev/null || pwd); fi
}

GATE="${1:-example}"
shift 2>/dev/null || true
TOOLS="${*:-claude opencode codebuddy codex}"
TMP="${LOCALAPPDATA:-/tmp}/Temp"
mkdir -p "$TMP" 2>/dev/null || TMP=/tmp
LOG="$HARNESS/reports/gate_log.jsonl"

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
    echo "[X] 找不到可用的 Python 3 解释器（已试：HARNESS_PYTHON / python3 / python / py -3）"
    echo "    本脚本每个环节都要用它（解析仓路径 / 判定器 / 钩子契约 / 配置体检），已中止。"
    echo "    修复：装 Python 3.11+；或显式指定解释器：export HARNESS_PYTHON=python3"
    exit 2
fi
# 调用处一律写 $PY 且**不加引号**（"py -3" 需要按空格拆成两个词）；PY 是本脚本其余各处的既有名。
PY="$HARNESS_PY"
# ------------------------------------------------------------------------------

# ---- 解析受测仓路径（从 run_gate --list 的 [ON ] <gate>: <script> @ <path>）----
HARNESS_N="$(nat "$HARNESS")"
REPO="$("$PY" "$HARNESS_N/harness/run_gate.py" --list 2>/dev/null \
        | sed -n "s/.*\[ON \] ${GATE}:.* @ //p" | head -1)"
[ -n "$REPO" ] || { echo "[X] 找不到 gate $GATE 的仓库路径（run_gate --list）"; exit 2; }
[ -d "$REPO" ] || { echo "[X] 仓库路径不存在: $REPO"; exit 2; }
HARNESS_WIN="$HARNESS_N"
REPO_WIN="$(nat "$REPO")"

echo "=============================================================="
echo " 门禁接入验证：gate=$GATE"
echo " 受测仓: $REPO_WIN"
echo " 客户端: $TOOLS"
echo "=============================================================="

BASE="$(cd "$REPO_WIN" && git status --porcelain 2>/dev/null | sort)"
FAIL=0
SUMMARY=""

log_lines() { wc -l < "$LOG" 2>/dev/null || echo 0; }

# 给某个客户端生成提示词（探针文件名唯一，便于归因）
prompt_for() {
  f="$TMP/verify_prompt_$1.txt"
  cat > "$f" <<EOF
请在该仓库新建文件 src/__verify_probe_$1.vue，内容严格按下面这段（逐字写，不要改、不要额外解释）：

<template>
  <div v-auth="p">x</div>
</template>
<script setup>
console.log("probe");
</script>

写完后，把你看到的任何自动检查 / 门禁 / 拦截输出**原样完整贴出来**；如果没有任何检查被触发，就明确说"没有遇到任何检查"。不要自行修复文件。
EOF
  echo "$f"
}

run_tool() {   # $1=客户端名；输出写到 $TMP/verify_out_<name>.txt
  name="$1"; pf="$(prompt_for "$name")"; out="$TMP/verify_out_$name.txt"
  case "$name" in
    claude)    (cd "$REPO_WIN" && timeout 420 claude -p "$(cat "$pf")" --allowedTools Write) >"$out" 2>&1 ;;
    opencode)  (cd "$REPO_WIN" && timeout 420 opencode run "$(cat "$pf")") >"$out" 2>&1 ;;
    codebuddy) (cd "$REPO_WIN" && timeout 420 "${LOCALAPPDATA}/Programs/WorkBuddy/resources/app.asar.unpacked/cli/bin/codebuddy" \
                  -p "$(cat "$pf")" --permission-mode bypassPermissions) >"$out" 2>&1 ;;
    codex)     # 模型用命令行覆盖：profile 里可能写着别的（如 hy3，2026-09-13 实测上游 high demand）。
               # 走 CC Switch → opencode-go 的可用模型是 deepseek-v4.1-flash；可用 CODEX_MODEL 覆盖。
               (cd "$REPO_WIN" && timeout 600 sh "$HOME/.codex/codex-go.sh" exec \
                  -m "${CODEX_MODEL:-deepseek-v4.1-flash}" -C "$REPO_WIN" -s workspace-write \
                  "$(cat "$pf")" < /dev/null) >"$out" 2>&1 ;;
    qoder)     # 2026-09-13 装上 Qoder CN CLI（独立产品，见 integrations/qoder-cn/README.md §6）。
               # 它用 Qoder 自己的账号额度（不是 opencode-go），配置目录 ~/.qoder-cn（hooks 在里面）。
               (cd "$REPO_WIN" && timeout 600 "$HOME/.qoder-cn/bin/qoderclicn/qoderclicn.exe" \
                  -p "$(cat "$pf")" -w "$REPO_WIN" --permission-mode bypass_permissions) >"$out" 2>&1 ;;
    zcode)     # 2026-09-13 实测打通：ZCode 有 headless CLI（Electron 自带 node 跑 zcode.cjs），
               # 经 ~/.zcode/zcode-go.sh → 本地代理补 x-opencode-session 头 → opencode-go。
               (cd "$REPO_WIN" && timeout 600 sh "$HOME/.zcode/zcode-go.sh" --cwd "$REPO_WIN" \
                  -p "$(cat "$pf")") >"$out" 2>&1 ;;
    *)         echo "unknown" > "$out"; return 1 ;;
  esac
}

check_one() {  # $1=客户端名 $2=是否强制型(yes/no)
  name="$1"; hard="$2"
  command -v "$name" >/dev/null 2>&1 || { case "$name" in
      codebuddy) CB="${LOCALAPPDATA}/Programs/WorkBuddy/resources/app.asar.unpacked/cli/bin/codebuddy"
                 [ -f "$CB" ] || { echo "  [skip] $name 未安装"; SUMMARY="$SUMMARY\n  $name: 跳过（未安装）"; return 0; } ;;
      codex)     [ -f "$HOME/.codex/codex-go.sh" ] || { echo "  [skip] $name 未安装"; SUMMARY="$SUMMARY\n  $name: 跳过（未安装）"; return 0; } ;;
      zcode)     [ -f "$HOME/.zcode/zcode-go.sh" ] || { echo "  [skip] $name 未安装"; SUMMARY="$SUMMARY\n  $name: 跳过（未安装）"; return 0; } ;;
      qoder)     [ -x "$HOME/.qoder-cn/bin/qoderclicn/qoderclicn.exe" ] || { echo "  [skip] $name 未安装"; SUMMARY="$SUMMARY\n  $name: 跳过（未安装）"; return 0; } ;;
      *)         echo "  [skip] $name 未安装"; SUMMARY="$SUMMARY\n  $name: 跳过（未安装）"; return 0 ;; esac; }

  probe="$REPO_WIN/src/__verify_probe_$name.vue"
  rm -f "$probe"
  before="$(log_lines)"
  echo "--------------------------------------------------------------"
  echo "▶ $name（$([ "$hard" = yes ] && echo 强制型 || echo 自觉型)）"
  run_tool "$name" || true
  after="$(log_lines)"
  hit_log=no; [ "$after" -gt "$before" ] && hit_log=yes

  # ── 判定交给纯函数判定器（Q4，2026-09-13 外部评审 v3）：这里只做采集与展示。
  #    判据本身在 sandbox/verify_judge.py（封闭枚举 + 证伪套件 tests/tooling/test_verify_judge.py）。
  #    历史：关键字口径过窄→放宽、Qoder 只输出最后一句→加行为证据、上游报错→加识别，
  #    每次改都是人肉回归；抽出来后改成受控修改。
  wrote=no;    [ -f "$probe" ] && wrote=yes
  # G5（评审 v5）：切片必须**按被测 gate 过滤** —— 否则窗口内无关记录的 rc=2/3
  # 在 F1 之后会被升级成硬 FAIL（gate_side_error），把别人的账算到这次验证头上。
  tail -n +$((before + 1)) "$LOG" 2>/dev/null | grep -F "\"gate\": \"$GATE\"" > "$TMP/slice_$name.log" || : > "$TMP/slice_$name.log"
  J_LINE=$($PY "$HARNESS_WIN/sandbox/verify_judge.py" --client "$name" --hard "$hard" \
             --wrote "$wrote" --log-slice "$TMP/slice_$name.log" \
             --output "$TMP/verify_out_$name.txt" 2>/dev/null | head -2)
  J_HEAD=$(printf '%s' "$J_LINE" | head -1)
  J_VERDICT=$(printf '%s' "$J_HEAD" | cut -d'|' -f1)
  J_FAIL=$(printf '%s' "$J_HEAD" | cut -d'|' -f2)
  J_SUMMARY=$(printf '%s' "$J_HEAD" | cut -d'|' -f3-)
  J_HINT=$(printf '%s' "$J_LINE" | sed -n '2p')
  if [ -z "$J_VERDICT" ]; then          # 判定器本身异常：宁可报出来，也不要静默算通过
    FAIL=1; J_VERDICT="judge_error"
    SUMMARY="$SUMMARY\n  $name: ❌ 判定器执行失败（sandbox/verify_judge.py）——请查 python 环境"
    echo "  ❌ 判定器执行失败"
  else
    [ "$J_FAIL" = "1" ] && FAIL=1
    SUMMARY="$SUMMARY\n  $J_SUMMARY"
    [ -n "$J_HINT" ] && echo "  $J_HINT"
  fi

  printf "  探针写入: %s ｜ 门禁被触发: %s ｜ 判定: %s\n" "$wrote" "$hit_log" "$J_VERDICT"
  if [ "$hit_log" = yes ]; then
    tail -1 "$LOG" | "$PY" -c "import json,sys;d=json.load(sys.stdin);print('  日志:',d.get('ts'),d.get('gate'),d.get('scope'),'rc=',d.get('rc'),list((d.get('rules') or {}).keys()))" 2>/dev/null
  else
    sed -n '1,6p' "$TMP/verify_out_$name.txt" | sed 's/^/    | /'
  fi
  rm -f "$probe"
}

for t in $TOOLS; do
  case "$t" in
    claude|opencode|codex) check_one "$t" yes ;;
    codebuddy)             check_one "$t" yes ;;   # 2026-09-13：已装 PostToolUse 钩子（JSON 契约）→ 强制型
    zcode)                 check_one "$t" yes ;;   # 2026-09-13：已配 PostToolUse/Stop 钩子（zcode 契约）→ 强制型
    qoder)                 check_one "$t" yes ;;   # 2026-09-13：已配 PostToolUse/Stop 钩子（qoder 契约）→ 强制型
    *) echo "  [skip] 未知客户端 $t" ;;
  esac
done

# ---------------- 钩子契约验证（不依赖模型：提供方抖动时这节仍然保证 harness 侧正确）----------------
echo "--------------------------------------------------------------"
echo "▶ 钩子契约验证（合成 payload 直喂 integrations/hooks/agentguard-gate.py）"
HOOK="$HARNESS_WIN/integrations/hooks/agentguard-gate.py"
PAY="$TMP/verify_payload.json"
PROBE="$REPO_WIN/src/__verify_probe_hook.vue"
printf '<template>\r\n  <div v-auth="p">x</div>\r\n</template>\r\n<script setup>\r\nconsole.log("probe");\r\n</script>\r\n' > "$PROBE"
printf '{"cwd":"%s","tool_name":"Write","tool_input":{"file_path":"%s"}}' "$REPO_WIN" "$PROBE" > "$PAY"

# claude：期望 exit 2（Claude Code 的阻断语义）+ stderr 有结论
"$PY" "$HOOK" claude PostToolUse < "$PAY" >"$TMP/verify_hook_claude.txt" 2>&1
RC=$?
if [ "$RC" = "2" ] && grep -q "门禁未通过" "$TMP/verify_hook_claude.txt"; then
  echo "  ✅ claude 契约：exit 2 + 结论（stderr）"
  SUMMARY="$SUMMARY\n  钩子契约 claude: ✅ exit 2 + 结论"
else
  echo "  ❌ claude 契约不符：exit=$RC"; FAIL=1; SUMMARY="$SUMMARY\n  钩子契约 claude: ❌ exit=$RC"
fi

# opencode：期望 stdout JSON 带 gate_rc（插件靠它分"未通过"与"调用出错"）
"$PY" "$HOOK" opencode PostToolUse < "$PAY" 2>/dev/null > "$TMP/verify_hook_opencode.json"
if "$PY" -c "import json,sys;d=json.load(open(r'$TMP/verify_hook_opencode.json',encoding='utf-8'));sys.exit(0 if d['agentguard']['gate_rc']==1 else 1)" 2>/dev/null; then
  echo "  ✅ opencode 契约：JSON gate_rc=1"
  SUMMARY="$SUMMARY\n  钩子契约 opencode: ✅ JSON gate_rc=1"
else
  echo "  ❌ opencode 契约不符"; FAIL=1; SUMMARY="$SUMMARY\n  钩子契约 opencode: ❌"
fi

# codex：期望 stdout JSON（hookSpecificOutput.additionalContext）；其写入判据需要命令行特征
CODEX_PAY="$TMP/verify_payload_codex.json"
printf '{"cwd":"%s","tool_name":"Bash","tool_input":{"command":"Set-Content -Path src/__verify_probe_hook.vue -Value x"}}' "$REPO_WIN" > "$CODEX_PAY"
"$PY" "$HOOK" codex PostToolUse < "$CODEX_PAY" 2>/dev/null > "$TMP/verify_hook_codex.json"
if "$PY" -c "import json,sys;d=json.load(open(r'$TMP/verify_hook_codex.json',encoding='utf-8'));sys.exit(0 if '门禁未通过' in d['hookSpecificOutput']['additionalContext'] else 1)" 2>/dev/null; then
  echo "  ✅ codex 契约：JSON additionalContext 带结论"
  SUMMARY="$SUMMARY\n  钩子契约 codex: ✅ additionalContext 带结论"
else
  echo "  ❌ codex 契约不符"; FAIL=1; SUMMARY="$SUMMARY\n  钩子契约 codex: ❌"
fi

# codebuddy：期望 stdout JSON 带 additionalContext（非 0 退出码只给用户看，回灌必须走 JSON）
"$PY" "$HOOK" codebuddy PostToolUse < "$PAY" 2>/dev/null > "$TMP/verify_hook_codebuddy.json"
if "$PY" -c "import json,sys;d=json.load(open(r'$TMP/verify_hook_codebuddy.json',encoding='utf-8'));sys.exit(0 if '门禁未通过' in d['hookSpecificOutput']['additionalContext'] else 1)" 2>/dev/null; then
  echo "  ✅ codebuddy 契约：JSON additionalContext 带结论"
  SUMMARY="$SUMMARY\n  钩子契约 codebuddy: ✅ JSON additionalContext"
else
  echo "  ❌ codebuddy 契约不符"; FAIL=1; SUMMARY="$SUMMARY\n  钩子契约 codebuddy: ❌"
fi

# 反向锁：合规文件必须放行（exit 0），防"钩子把正常提交也拦了"
# ⚠️ 必须先删掉违规探针：钩子走 auto-gate，会收集**仓内全部未提交改动**——
#    留着违规探针会让"合规文件"被判拦（本脚本第一版就这么误报过）。
rm -f "$PROBE"
CLEAN="$REPO_WIN/src/__verify_clean.vue"
# 合规探针的**行尾必须按端来写**：PC 仓要求 CRLF、H5 仓要求 LF。
# 2026-09-13 实测踩坑：这里原先硬编码 CRLF（PC 口径），跑 H5 gate 时合规文件被 H5 的行尾规则
# 判为「行尾污染：文件含 CRLF（H5 仓要求 LF）」→ 反向锁误报成「❌ 误报型灾难」。
# 门禁判断没错，是探针按错端写的 —— 反向锁要验的是"合规文件放行"，探针必须先真合规。
case "$GATE" in
  *_h5)
    printf '<template>\n  <div :key="1">ok</div>\n</template>\n<script setup>\nconst a = 1;\n</script>\n' > "$CLEAN"
    ;;
  *)
    printf '<template>\r\n  <div :key="1">ok</div>\r\n</template>\r\n<script setup>\r\nconst a = 1;\r\n</script>\r\n' > "$CLEAN"
    ;;
esac
printf '{"cwd":"%s","tool_name":"Write","tool_input":{"file_path":"%s"}}' "$REPO_WIN" "$CLEAN" > "$TMP/verify_payload_clean.json"
"$PY" "$HOOK" claude PostToolUse < "$TMP/verify_payload_clean.json" >/dev/null 2>&1
RC=$?
if [ "$RC" = "0" ]; then
  echo "  ✅ 反向锁：合规文件放行（exit 0，不误拦）"
  SUMMARY="$SUMMARY\n  反向锁（合规放行）: ✅"
else
  echo "  ❌ 反向锁失败：合规文件被拦（exit=$RC）—— 误报型灾难"; FAIL=1
  SUMMARY="$SUMMARY\n  反向锁（合规放行）: ❌ exit=$RC"
fi
rm -f "$CLEAN"

# ---------------- 钩子契约指纹（Q3：漂移即红；只比结构、不比消息文案）----------------
echo "▶ 钩子契约指纹比对（夹具复跑 7 家 × 3 事件：客户端升级或我们改坏分支都会红）"
if [ -f "$HARNESS_WIN/sandbox/check_contracts.py" ]; then
  CC_OUT="$($PY "$HARNESS_WIN/sandbox/check_contracts.py" --all 2>&1)"; CC_RC=$?
  printf '%s\n' "$CC_OUT" | sed 's/^/  /'
  # F7（评审 v4）：rc=0 也涵盖 ⚠（version_changed / no_baseline），不能一律报"与基线一致"。
  CC_WARN=$(printf '%s\n' "$CC_OUT" | grep -c "⚠" || true)
  CC_BAD=$(printf '%s\n' "$CC_OUT" | grep -c "❌" || true)
  if [ "$CC_RC" -ne 0 ]; then
    FAIL=1; SUMMARY="$SUMMARY\n  契约指纹: ❌ 有漂移（$CC_BAD 家；见上面逐家说明，有意改动需 --accept --reason 更新基线）"
  elif [ "${CC_WARN:-0}" -gt 0 ]; then
    SUMMARY="$SUMMARY\n  契约指纹: ⚠ $CC_WARN 条待复核（无基线/版本变化，不算失败）"
  else
    SUMMARY="$SUMMARY\n  契约指纹: ✅ 与基线一致"
  fi
else
  echo "  [skip] 未找到 sandbox/check_contracts.py"
fi
echo "--------------------------------------------------------------"

# ---------------- 仓库级兜底：pre-commit（工具无关） ----------------
echo "--------------------------------------------------------------"
echo "▶ 仓库级兜底 pre-commit（工具无关，接得住「没跑门禁就直接提交」）"
probe="$REPO_WIN/src/__verify_probe_precommit.vue"
printf '<template>\r\n  <div v-auth="p">x</div>\r\n</template>\r\n<script setup>\r\nconsole.log("probe");\r\n</script>\r\n' > "$probe"
(cd "$REPO_WIN" && git add "src/__verify_probe_precommit.vue" >/dev/null 2>&1 && \
 git hook run pre-commit >"$TMP/verify_precommit.txt" 2>&1; echo "rc=$?" > "$TMP/verify_precommit.rc")
RC="$(sed -n 's/^rc=//p' "$TMP/verify_precommit.rc" 2>/dev/null)"
(cd "$REPO_WIN" && git restore --staged "src/__verify_probe_precommit.vue" >/dev/null 2>&1)
rm -f "$probe"
if [ "${RC:-0}" != "0" ]; then
  echo "  ✅ pre-commit 拦住（退出码 $RC）；提交会被 git 中止"
  SUMMARY="$SUMMARY\n  pre-commit 兜底: ✅ 拦住（rc=$RC）"
else
  echo "  ❌ pre-commit 没拦住（退出码 0）——查 hooksPath 与 gate 配置"
  SUMMARY="$SUMMARY\n  pre-commit 兜底: ❌ 未拦住"
  FAIL=1
fi

# ---------------- 客户端配置静态体检（不需要 CLI；GUI-only 客户端靠它）----------------
echo "▶ 客户端配置静态体检（不需要装 CLI：Trae 这类 GUI-only 客户端，靠它抓「必炸」配置）"
if [ -f "$HARNESS_WIN/sandbox/check_client_configs.py" ]; then
  CFG_OUT="$($PY "$HARNESS_WIN/sandbox/check_client_configs.py" 2>&1)"; CFG_RC=$?
  printf '%s\n' "$CFG_OUT" | sed 's/^/  /'
  if [ "$CFG_RC" -eq 0 ]; then
    SUMMARY="$SUMMARY\n  客户端配置体检: ✅ 配置形态正确（引号/退出码保真/JSON/客户端参数/关键事件）"
  else
    # 2026-09-21：体检器现在也查**受管仓里的项目级配置**，两类 ❌ 的含义不同，别混成一句
    # ——「有配置必坏（钩子静默失效）」对项目级那份是**假话**：那 32 份 `zcode.json` 从来没
    # 生效过，撤掉它们不影响任何钩子（用户级那份 `~/.zcode/cli/config.json` 才是干活的那个）。
    # 措辞按实际分岔写，读者才知道该慌哪一条。
    FAIL=1; SUMMARY="$SUMMARY\n  客户端配置体检: ❌ 见上（**用户级**=配置真坏、钩子必静默失效；**项目级**=受管仓里残留的配置随仓分发、写死本机路径，撤掉即可）"
  fi
else
  echo "  [skip] 未找到 sandbox/check_client_configs.py"
fi
echo "--------------------------------------------------------------"

# ---------------- 清场校验 ----------------
AFTER="$(cd "$REPO_WIN" && git status --porcelain 2>/dev/null | sort)"
echo "--------------------------------------------------------------"
echo "本报告（客户端输出留档：$TMP/verify_out_*.txt）"
printf "%b\n" "$SUMMARY"
if [ "$BASE" = "$AFTER" ]; then
  echo "  ✅ 清场校验通过：受测仓 git status 与开跑前一致（未碰真实代码）"
else
  echo "  ❌ 清场校验失败：受测仓状态变了"
  diff <(printf '%s\n' "$BASE") <(printf '%s\n' "$AFTER") | sed 's/^/    /'
  FAIL=1
fi
echo "=============================================================="
exit $FAIL
