#!/usr/bin/env bash
# guardrail-engine/sandbox/bootstrap_agent_env.sh
# ==============================================================================
# AI Agent 环境一键装配（新机器/新同事/新工具接入用）
# 做四件事：
#   1. 全局引导统一：~/.codex/AGENTS.md、~/.config/opencode/AGENTS.md、
#      ~/.claude/CLAUDE.md、~/.codebuddy/rules/agentguard.md
#      写薄壳指向 ~/.guardrail-engine/GLOBAL_AGENTS.md（旧 ~/.agentguard/ 仍认）
#      （原文件自动备份为 *.bak-<日期>，MCP 注入块会由工具自动重建）
#      ⚠ 只写**已确认该工具会自动加载**的位置（判据见 make_shell 调用处）：
#        往一个没人读的路径里写薄壳 = "装好了"的假象，比不写更糟。
#   2. skills 软链分发：~/.skills-manager/skills → 各工具目录（已存在则跳过）
#   3. 各受管仓库 pre-commit hook 检查（缺则提示跑 install_hooks.sh）
#   4. 校验 auto-gate 可执行（python 可用 + harness 路径正确）
# 用法：bash bootstrap_agent_env.sh
# ==============================================================================
set -u

HARNESS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # = guardrail-engine/
# Windows 风格正斜杠路径（cygpath -m）：喂给 **Windows 上的 python / git** 时必须用它 ——
# git-bash 的 `/d/...` 那边的程序打不开（实测踩到，见本文件末尾指针那段）。
# cygpath 不存在时（WSL / Linux）原样返回 `/mnt/d/...` 或 `/home/...`，正是那边要的形态。
HARNESS_WIN="$(sh "$HARNESS_ROOT/sandbox/where.sh" --win 2>/dev/null)"
[ -n "$HARNESS_WIN" ] || HARNESS_WIN="$HARNESS_ROOT"
if [ -d "$HOME/.guardrail-engine" ] || [ ! -d "$HOME/.agentguard" ]; then
  GLOBAL_DIR="$HOME/.guardrail-engine"
else
  GLOBAL_DIR="$HOME/.agentguard"       # 旧机器继续用旧目录（不迁移、不丢配置）
fi
GLOBAL_SRC="$GLOBAL_DIR/GLOBAL_AGENTS.md"
DATE_TAG="$(date +%Y%m%d)"
SKILLS_SRC="$HOME/.skills-manager/skills"

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
    echo "✗ 找不到可用的 Python 3 解释器（已试：HARNESS_PYTHON / python3 / python / py -3）"
    echo "  第 4 步的 auto-gate 校验要靠它；已按 fail-closed 中止（前 3 步也没做，可重复运行）。"
    echo "  修复：装 Python 3.11+；或显式指定解释器：export HARNESS_PYTHON=python3"
    exit 2
fi
# 调用处一律写 $HARNESS_PY 且**不加引号**（"py -3" 需要按空格拆成两个词）。
# ------------------------------------------------------------------------------

echo "=== AgentGuard AI Agent 环境装配 ==="
echo "harness: $HARNESS_ROOT"

# ---------- 1. 全局引导统一（哨兵分区：本区每次重渲染，区外永不触碰）----------
# 生成物里**不留维护者本机的目录布局**（2026-09-18 批次 4）：原先写的是
# `在受管仓库改码前：` —— 那是维护机上的
# 一个目录通配，同事的仓根本不长在那儿；更早一处还把 `$HARNESS_ROOT` 直接留成了字面量
# （单引号 heredoc 不展开），读者照着敲等于敲了个未定义变量。
# 现在：①「该管哪些仓」不再靠目录猜，改为指向 `run_gate.py --list`（唯一权威来源）；
#      ②本机真实路径用 __HARNESS__ 占位符 + 每次渲染烘进去（见下），并注明解释器差异。
#
# ---- 2026-09-19 哨兵分区（本机实测暴露的病，治的就是下面这段守卫）----
# 原先的守卫是 `if [ ! -f "$GLOBAL_SRC" ]`，else 分支注释写着「已存在时**不动它**
# （里面可能有你自己加的规矩）」。善意，但代价是**这个文件此后再也没有更新路径**：
# 改好的正文永远进不到已经有这个文件的机器上。本机实况就是活证据 —— 文件停在
# 2026-09-10 那版，里面还带着「D:/MyProject/aiauto/agentguard_* 目录通配」和 `python`
# （Ubuntu/WSL 上 command not found），**正是次日批次 4 要消灭的东西**；而每次运行
# 还会打一个绿色的「✓ 权威源已存在」。看起来健康，其实是死的。
# 改法：harness 只维护**哨兵之间**那一区，每次运行重渲染；哨兵区外一字不动，
# 用户自己加的规矩照旧保留。两份善意不冲突了。
# 渲染范式同 sandbox/install_shim.py 的 render()/PLACEHOLDER —— 不发明新机制。
# ---- 2026-09-19 正文与拼装搬去 sandbox/global_agents.py（**别再搬回来**）----
# 同一天 install_shim.py 也要写这份文件 —— 那是《安装说明》「二、Agent 接入」里同事**必然**
# 跑到的一步，而它原先只生成 shim 与指针，于是同事照文档做完**永远拿不到这份全局守则**，
# 可三份端级模板（AGENTS_{pc,h5,backend}_slim.md）都指着它。要让第二个写入方也会写，
# 最直觉的做法是"再抄一份"。**那样会漂，而且漂得很难看**：哨兵行只要差一个字符，
# 先写的那份就会被后写的那份判成"没有哨兵的老文件" → 走迁移分支，把整份旧内容追加到
# 哨兵区之后；两个写入方交替跑（装 shim → 跑 bootstrap → 再装 shim…），文件每跑一次
# 涨一倍，而屏幕上每次都打着绿色的 ✓。所以正文与拼装收进 sandbox/global_agents.py，
# 两个写入方都调它 —— 没有第二份可漂。锁：tests/engine/test_global_agents.py。
# 顺带修掉一个潜在 bug：原先那个占位符替换是拿 sed 的 s 命令做的，工程路径含竖线或 & 时
# 会被 sed 当元字符吃掉/改写（仓放在 ~/a&b/ 下就是活例）；str.replace 不解释元字符。
# 本脚本只留**人类文案**（下面 case 那段）；机读的动作词由模块打在 stdout，警告走 stderr。
GLOBAL_SRC="$GLOBAL_DIR/GLOBAL_AGENTS.md"
# ---- 2026-09-19 修：薄壳里写出去的是 git-bash 形态，可读它的却是 Windows 工具 ----
# 本机实测：三个薄壳正文写的是 `/c/Users/HP/.agentguard/GLOBAL_AGENTS.md` —— git-bash 的
# 形态。可这三个文件正是 **Windows 上的 AI 工具**去读的入口路由，它们打不开 `/c/...`。
# 同一件事本文件末尾指针那段已经治过一遍（连注释都写着「git-bash 的 `/d/...` 那边的程序
# 打不开」），并为此专门算了 $HARNESS_WIN；这里漏了，属同一个病没治干净。
# 判据与 sandbox/where.sh --win 逐字同源：cygpath 在就转 Windows 形态，不在（WSL/Linux）
# 原样返回 —— 那边 POSIX 形态才是对的，不能一律改写。
if command -v cygpath >/dev/null 2>&1; then
  GLOBAL_SRC_WIN="$(cygpath -m "$GLOBAL_SRC")"
else
  GLOBAL_SRC_WIN="$GLOBAL_SRC"
fi
mkdir -p "$GLOBAL_DIR"
GLOBAL_TOOL="$HARNESS_ROOT/sandbox/global_agents.py"
[ -f "$GLOBAL_TOOL" ] || {
  echo "✗ 缺 sandbox/global_agents.py —— 全局守则的正文与哨兵拼装都在它里面，本脚本已不再自带。"
  echo "  引擎仓不完整？重新 clone 或 git pull 后再跑本脚本。"
  exit 1
}
# 脚本路径用 $HARNESS_ROOT（bash 视角）：git-bash 的 /d/... 作 argv 传给 python.exe 能开
# （实测过），而且它一定已被定义 —— 不必依赖 where.sh 是否成功。
# 但**要烘进正文的路径**必须用 $HARNESS_WIN（Windows 形态），那是写进文件给别人看的。
GLOBAL_ACTION=$($HARNESS_PY "$GLOBAL_TOOL" "$HARNESS_WIN") || {
  echo "✗ 全局守则渲染失败（原文件未被改动）—— 修好后重跑本脚本"; exit 1; }

case "$GLOBAL_ACTION" in
  created)    echo "✓ 已创建权威源: $GLOBAL_SRC" ;;
  rerendered) echo "✓ 已重渲染哨兵区: $GLOBAL_SRC（哨兵区外未动）" ;;
  # 备份路径与「旧内容原样保留在哨兵区之后」由模块打在 stderr —— 那一句对两个
  # 写入方（本脚本 / install_shim.py）都要出现，所以归模块，此处不重复。
  migrated)   echo "✓ 已迁移为哨兵分区: $GLOBAL_SRC" ;;
  *)          echo "⚠ 全局守则渲染返回未知状态：[$GLOBAL_ACTION]（文件可能未更新，请检查）" ;;
esac
echo "    （门禁路径已按本机渲染为 $HARNESS_WIN）"

# 薄壳正文的**唯一生成处**：下面「要不要备份」和「写什么进去」两处都取它，
# 于是二者不可能不一致 —— 这正是原先那个 bug 的形状（判据和写入内容各写各的）。
# 注意它用 $GLOBAL_SRC_WIN（Windows 形态），理由见本文件上面那段。
shell_body() { # $1=工具名
  cat <<EOF
# $1 全局引导（薄壳，指向统一权威源）
本文件内容已统一迁移至：\`$GLOBAL_SRC_WIN\`（所有 AI 工具共用一份，改一处全部生效）。
**任何 AI 助手在此机器上工作前，请先完整阅读上述文件**（含 AgentGuard 受管仓库守则与通用编码准则）。
MCP/codebase-memory 等工具自动注入的内容不受影响，照常生效。
EOF
}

make_shell() { # $1=目标路径 $2=工具名
  local dest="$1" tool="$2" body
  mkdir -p "$(dirname "$dest")"
  if [ -L "$dest" ]; then
    echo "  $tool: 已是软链，跳过"
    return
  fi
  body="$(shell_body "$tool")"
  if [ -f "$dest" ]; then
    # ---- 2026-09-19 修：备份守卫原先判的是「含不含 GLOBAL_AGENTS.md 这个串」----
    # 那个串在薄壳正文里本来就有 ⇒ 守卫恒假 ⇒ **备份分支永不触发**；而下面的 `ln -s`
    # 对已存在的文件必然失败 ⇒ 每次都落到 `cat >` **无条件截断重写**。
    # 合起来的后果：往这三个文件里手写的任何东西，下次跑 bootstrap 被静默清掉、连备份都没有。
    # 本机实测就是活证据 —— 三个 .bak-20260919 里装的正是**薄壳本身**（被无条件备份下来的），
    # 而真正在改之前写过手写内容的那一版，一个痕迹都没留下。
    # 现在判据换成**内容**：跟即将写入的薄壳逐字相同才算「没什么可丢的」，直接跳过；
    # 只要不一样（手写内容 / 旧形态路径 / 别人改过），先落 .bak-<日期> 再覆盖。
    if [ "$(cat "$dest" 2>/dev/null)" = "$body" ]; then
      echo "  $tool: 薄壳已是最新，跳过"
      return
    fi
    cp "$dest" "$dest.bak-$DATE_TAG"
    echo "  $tool: 原文件备份 → $(basename "$dest").bak-$DATE_TAG"
  fi
  # ---- 2026-09-19 修（第三处，与前两处同一天实测抓出）：判据必须是**结果**不是退出码 ----
  # 本机 git-bash 未开 winsymlinks 时，`ln -s` 对**文件**也会**返回 0 却做一次拷贝**
  # （与第 2 节 make_dir_link 里那个目录版是同一个坑，那里已经治过，这里漏了）。
  # 拿退出码当成功 → dest 上留下一份 **GLOBAL_AGENTS.md 的副本**，而它正躺在
  # `~/.config/opencode/AGENTS.md` 这种「工具会自动加载」的位置上、且再不会刷新 ——
  # 就是本模块头号要防的「第二份可漂的正文」，只不过漂到了用户 HOME 里，仓库的锁看不见。
  # 专打**新机器**（只有 dest 不存在时才会走到这条分支），恰恰是 bootstrap 的主场景。
  # 所以只在 dest 不存在时才试链，且回验 [ -L ] 为真才认；没链成留下的东西一律清掉。
  if [ ! -e "$dest" ] && ln -s "$GLOBAL_SRC" "$dest" 2>/dev/null && [ -L "$dest" ]; then
    echo "  $tool: 软链 ✓"
  else
    [ -e "$dest" ] && [ ! -L "$dest" ] && rm -f "$dest"   # 清掉「冒充链接的拷贝」；真备份已在上面落好
    printf '%s\n' "$body" > "$dest"
    echo "  $tool: 写薄壳 ✓（软链需管理员/开发者模式）"
  fi
}

echo "--- 1. 全局引导 ---"
make_shell "$HOME/.codex/AGENTS.md" "codex"
make_shell "$HOME/.config/opencode/AGENTS.md" "opencode"
make_shell "$HOME/.claude/CLAUDE.md" "claude"
# ---- CodeBuddy（2026-10-12 补）----
# 与上面三家不同：它是**目录**形态（`rules/*.md`），且 `~/.codebuddy/rules/` 默认不存在
# （本机实测无此目录）—— 靠 make_shell 里的 mkdir -p 现建。
# 补它的理由：上面三家是"仓库级 AGENTS.md 读不到时"的兜底（跨仓对比、不在受管仓里工作），
# CodeBuddy 此前独缺这一层，而它恰好是本机主力 IDE。
#
# ⚠ 新增一家之前的三条判据（别凭"看起来该有"就往 HOME 里塞文件）：
#   ① 确认该工具**确实会自动加载**这个路径（官方文档或实测：写进去后开新会话能被读到）；
#   ② 只写薄壳（指向 GLOBAL_AGENTS.md），**绝不复制正文** —— 复制就是第二份可漂的正文；
#   ③ 位置不确定就**不写**，改记进 integrations/<工具>/README.md 的"待验证"。
# 照此，Trae CN / ZCode / Qoder CN / Hermes **暂不写用户级**：本机用户目录下没有确认会加载的
# md 入口（.trae-cn 只有 memory/、.zcode 只有 cli/、.qoder-cn 是 memories/、.hermes 是 config.yaml），
# 而它们**项目级**都吃 AGENTS.md —— 34 仓的指针已覆盖那条路。
make_shell "$HOME/.codebuddy/rules/agentguard.md" "codebuddy"

# ---------- 0. 受管仓库枚举（后两节 1b / 3 共用）----------
# 原先这两节各自用 `/d/MyProject/aiauto/agentguard_*` **通配本机目录**，问题有三层：
#   ① 同事机器上零匹配 → 「检查过了」与「什么都没检查」在屏幕上长得一模一样（静默失效）；
#   ② 通配的是**目录**，不是**受管仓**（本机实测多扫出 android_new / tool 等没接门禁的目录）；
#   ③ 与 config 里真正生效的 repos 段无关 —— 改了本地覆盖层（harness.local.toml），这两节照样看老地方。
# 改为从**生效配置**枚举（每个 gate 都带 _repo_path），与 sandbox/setup_machine.sh 同一范式。
# 引擎自身（harness_self）不在范围内：它不是受管仓；自己那份门禁由 core.hooksPath 指向
# harness/hooks-dir 自管，另有 checks/harness_self_check.py 体检。
MANAGED_REPOS=$($HARNESS_PY - "$HARNESS_WIN" <<'PYEOF'
import sys, os
sys.stdout.reconfigure(newline="\n")   # Windows 上默认把 \n 翻成 \r\n，下游字符串比较恒假（实测踩过）
harness = sys.argv[1]
sys.path.insert(0, os.path.join(harness, "harness"))
from _config import all_gates
order, names = [], {}
for name, g in sorted(all_gates().items()):
    if name == "harness_self" or not g.get("enabled"):
        continue
    rp = (g.get("_repo_path") or "").replace("\\", "/").rstrip("/")
    if not rp:
        continue
    if rp not in names:
        order.append(rp)
        names[rp] = []
    names[rp].append(name)
for rp in order:
    print("%s\t%s\t%s" % (rp, "OK" if os.path.isdir(rp) else "MISSING", " ".join(names[rp])))
PYEOF
) || { echo "✗ 读取生效配置失败（多半是 config/harness.local.toml 语法错）—— 修好后重跑本脚本"; exit 1; }

TAB=$(printf '\t')
# 计数在循环**外**重算：这两节的循环体在管道子壳里，`+=1` 改不到外面（setup_machine.sh 为同一件事
# 写过一遍）。`$2 == "OK"` 先裁 `\r` 再比 —— 那一类恒假比较本仓踩过。
REPO_TOTAL=$(printf '%s\n' "$MANAGED_REPOS" | grep -c .)
REPO_OK=$(printf '%s\n' "$MANAGED_REPOS" | awk -F'\t' '{sub(/\r$/, "", $2)} $2 == "OK"' | grep -c .)
if [ "$REPO_TOTAL" -eq 0 ]; then
  echo "✗ 本机没有任何受管仓库（生效配置里 0 条）—— 后两节靠它才知道该检查谁："
  echo "    · 还没写 config/harness.local.toml？先跑：sh $HARNESS_WIN/sandbox/setup_machine.sh"
  echo "    · WSL 下 repos 段要填 **WSL 视角**的路径（如 /mnt/d/...），不是 D:/..."
  echo "    · 空 ≠ 都没问题，空 = 什么都没检查。"
elif [ "$REPO_OK" -eq 0 ]; then
  echo "✗ 生效配置里有 $REPO_TOTAL 条受管仓库路径，但没有一条在本机存在（0/$REPO_TOTAL）——"
  echo "    · 换机器 / 换盘符后要把 repos 段改成你机器的实际路径（见 config/harness.local.toml）"
fi

echo "--- 1b. IDE 项目级规则检查（AGENTS.md 单源 + CLAUDE.md @import 桥接，2026-09-10 收敛） ---"
# 三家国产 IDE 已统一吃 AGENTS.md：CodeBuddy fallback / Trae 原生（设置开关）/ Qoder 自动识别。
# 项目里不应再有 CODEBUDDY.md / .trae / .qoder 方言文件（存在 = 漂移源，应删）。
# Claude Code 不原生读 AGENTS.md（官方 issue #6235 仅给 workaround）：CLAUDE.md 首行
# 必须 @AGENTS.md（官方推荐桥接，Windows 下优于软链）。
printf '%s\n' "$MANAGED_REPOS" | while IFS="$TAB" read -r repo status _gate_names; do
  [ -n "$repo" ] || continue
  status=${status%$'\r'}                       # 防御：输出若仍带 \r，`[ = ]` 会恒假
  [ "$status" = "OK" ] || continue
  [ -f "$repo/AGENTS.md" ] || continue
  n=$(basename "$repo")
  if [ -e "$repo/CODEBUDDY.md" ] || [ -d "$repo/.trae" ] || [ -d "$repo/.qoder" ]; then
    echo "  ⚠ $n: 存在 CODEBUDDY.md/.trae/.qoder 方言文件（应删除，统一吃 AGENTS.md fallback/原生读取）"
  fi
  if [ -f "$repo/CLAUDE.md" ] && grep -q '^@AGENTS.md' "$repo/CLAUDE.md"; then
    :
  else
    echo "  ⚠ $n: CLAUDE.md 缺首行 @AGENTS.md import（Claude Code 不读 AGENTS.md，写一行 '@AGENTS.md' 桥接）"
  fi
done
echo "  （已接入且已配好的仓未列出；受检 $REPO_OK/$REPO_TOTAL 条路径在本机存在）"

# ---------- 2. skills 链接分发 ----------
# 2026-09-19 改：本节原来是**无条件 `cp -r`**，实测出两个后果 ——
#   ① 拷出来的副本**再也不会被刷新**（原来只有 `[ ! -e ]` 一层判断）：正文改一处，这台机器上
#      那一份不跟着改。实测 codebuddy 上就压着一份 6 文件的陈旧技能，里面还教着已被降级成
#      ❌ 反例的写法；
#   ② 与其余 agent 的机制**不一致**：`~/.claude`、`~/.codex`、`~/.cursor`、`~/.gemini` 下的同名
#      条目本来都是链接，只有 codebuddy 这份是拷贝 —— 于是它成了唯一会漂的那个。
# 所以改为一律建**目录链接**；真建不了才退回拷贝，并且在输出里明说「这份不会自动刷新」。
# 为什么是 junction 而不是 `ln -s`：**git-bash 的 `ln -s` 对本机目录会返回 0 却做一次深拷贝**
# （2026-09-19 实测：`ls -la` 出来是普通目录、`[ -L ]` 为假 —— MSYS 未开 winsymlinks 时的已知行为）。
# 也就是说拿它当"软链"用，会把本节正要修的「陈旧副本」问题**原样搬回来、还不报错**。
# 而 **junction（mklink /J）不需要提权**，skills 目录树里现成的那些本来就是 junction，
# 本步只是与既成事实对齐。
make_dir_link() { # $1=源目录 $2=链接路径；成功返回 0
  local src="$1" link="$2"
  # 已存在就**绝不动它**：这样下面那句清理只可能删掉「本次调用自己刚建出来」的东西。
  # （陈旧副本的挪走由调用方负责，见本节循环里的 .stale-$DATE_TAG 分支。）
  [ -e "$link" ] && return 1
  if command -v cmd.exe >/dev/null 2>&1 && command -v cygpath >/dev/null 2>&1; then
    # `//J` 而非 `/J`：msys 会把 `/J` 当路径改写掉；路径要交给 Windows 形态（cygpath -w）
    cmd.exe //c mklink //J "$(cygpath -w "$link")" "$(cygpath -w "$src")" >/dev/null 2>&1
  else
    ln -s "$src" "$link" 2>/dev/null
  fi
  # ★ 判据是**结果**不是退出码：上面两条路都可能"返回 0 而没链成"（尤其 `ln -s` 那条会悄悄拷一份）。
  #   所以一律回验为真才认 —— 认不出来就清掉试建物并报失败，让调用方走**明说会漂**的拷贝退路，
  #   而不是在这里留一个冒充链接的副本。`[ -d ]` 同时挡掉指空链接。
  if [ -L "$link" ] && [ -d "$link" ]; then
    return 0
  fi
  [ -e "$link" ] && [ ! -L "$link" ] && rm -rf "$link" 2>/dev/null
  return 1
}

echo "--- 2. skills 链接 ---"
if [ -d "$SKILLS_SRC" ]; then
  for tool_dir in "$HOME/.codebuddy/skills"; do
    mkdir -p "$tool_dir"
    for s in "$SKILLS_SRC"/*/; do
      name="$(basename "$s")"
      src="$SKILLS_SRC/$name"          # 去掉 `*/` 带的尾斜杠：mklink 不接受以 `\` 结尾的路径
      link="$tool_dir/$name"
      if [ -L "$link" ]; then
        echo "  $name → $(basename "$tool_dir") 已是链接，跳过"
        continue
      fi
      if [ -d "$link" ]; then
        # 真目录 = 早先那版 `cp -r` 留下的陈旧副本。挪到一边**不删**（同第 1 步 make_shell 的
        # .bak-$DATE_TAG），万一里面有本地手改，人还能捞回来。
        # ⚠ 但**不能挪在 skills/ 里面**：那会多出一个 frontmatter 里 `name:` 完全相同的条目，
        #   扫描器可能把它当成第二个技能加载（等于用一个陈旧副本去抢同一个技能名）。
        #   所以放到 skills 的**兄弟目录** —— 仍在同一个 agent 配置目录下，人找得到，扫描器看不到。
        stale_dir="$(dirname "$tool_dir")/$(basename "$tool_dir")-stale-$DATE_TAG"
        bak="$stale_dir/$name"
        if [ -e "$bak" ]; then
          echo "  ⚠ $name: 原有副本不是链接，且 $(basename "$stale_dir")/$name 已存在 → 本步跳过，请先手动处理"
          continue
        fi
        if mkdir -p "$stale_dir" && mv "$link" "$bak" 2>/dev/null; then
          echo "  ⚠ $name: 原有副本不是链接，已挪到 $(basename "$stale_dir")/$name（未删）"
        else
          echo "  ⚠ $name: 原有副本不是链接且挪不动 → 本步跳过"; continue
        fi
      fi
      if make_dir_link "$src" "$link"; then
        echo "  $name → $(basename "$tool_dir") ✓（链接）"
      elif cp -r "$src" "$link" 2>/dev/null; then
        echo "  $name → $(basename "$tool_dir") ✓（拷贝 ⚠ 链接不可用；这份不会自动刷新）"
      else
        echo "  ⚠ $name → $(basename "$tool_dir") 失败（链接与拷贝都没成）"
      fi
    done
  done
else
  echo "  跳过（$SKILLS_SRC 不存在）"
fi

# ---------- 3. 受管仓库 pre-commit hook 检查 ----------
echo "--- 3. 受管仓库 pre-commit ---"
# 判据修正（2026-09-18 批次 4）：装钩子的机制是 `git config core.hooksPath` 指向共享 hooks-dir
# （见 install_hooks.sh:183），**钩子装好的仓里 `.git/hooks/pre-commit` 恰恰不存在** ——
# 老判据只认 `.git/hooks/pre-commit`，于是在本机对**每一个**仓都报了假警报（实测 41 条全假，
# 而真装好的仓一个也没漏：34 个 gate 的 hooksPath 全部指向 harness/hooks-dir）。
# 现在问 git 自己「你这个仓生效的 hooks 目录在哪」：`git rev-parse --git-path hooks` 会把
# core.hooksPath 算进去，连体工作区也一并正确（install_hooks.sh:75 为同一件事写过一遍）。
printf '%s\n' "$MANAGED_REPOS" | while IFS="$TAB" read -r repo status gate_names; do
  [ -n "$repo" ] || continue
  status=${status%$'\r'}
  [ "$status" = "OK" ] || continue
  gate_names=${gate_names%$'\r'}
  hooks_dir=$(git -C "$repo" rev-parse --git-path hooks 2>/dev/null)
  if [ -z "$hooks_dir" ]; then
    echo "  ⚠ $(basename "$repo"): 不是 git 仓库（或本机没有 git）—— 门禁无从安装"
    continue
  fi
  # 连体工作区下 git 可能给相对路径，统一拍成绝对再判
  case "$hooks_dir" in
    /*|[A-Za-z]:[/\\]*) ;;
    *) hooks_dir="$repo/$hooks_dir" ;;
  esac
  if [ ! -f "$hooks_dir/pre-commit" ]; then
    first_gate=${gate_names%% *}
    echo "  ⚠ $(basename "$repo"): 未接入门禁（生效 hooks 目录里没有 pre-commit：$hooks_dir）"
    echo "       补装：cd $repo && sh $HARNESS_WIN/sandbox/install_hooks.sh ${first_gate:-<gate名>}"
  fi
done
echo "  （已接入的仓未列出；受检 $REPO_OK/$REPO_TOTAL 条路径在本机存在）"

# ---------- 4. auto-gate 校验 ----------
echo "--- 4. auto-gate 校验 ---"
if [ -f "$HARNESS_ROOT/harness/agents/auto-gate.py" ]; then
  echo "  ✓ auto-gate.py 存在"
  # 探针必须**在非受管目录下**跑：脚本对「不在受管仓内」返回 3，那才证明 python 拉得起来、
  # 脚本本身没语法/导入错。原先是在调用者的 cwd 里跑 `--help` —— 站在受管仓里跑必然拿到 0，
  # 于是「⚠ 退出码 0（非 3）」在正常机器上也天天出现（本机实测就是如此），属于本节在治的
  # 同一类毛病：永远在喊 = 没人再读。临时目录里既非受管、也不受 cwd 影响。
  _probe_dir=$(mktemp -d 2>/dev/null)
  [ -n "$_probe_dir" ] || _probe_dir=/tmp
  ( cd "$_probe_dir" && $HARNESS_PY "$HARNESS_WIN/harness/agents/auto-gate.py" >/dev/null 2>&1 )
  rc=$?
  [ -d "$_probe_dir" ] && [ "$_probe_dir" != "/tmp" ] && rm -rf "$_probe_dir" 2>/dev/null
  [ $rc -eq 3 ] && echo "  ✓ python 可用，脚本可执行（非受管目录下退出码 3，符合预期）" \
                || echo "  ⚠ auto-gate 退出码 $rc（非 3，检查 python 环境）"
else
  echo "  ✗ auto-gate.py 缺失！harness 工程不完整"
fi

echo "--- 5. 门禁钩子定位指针 ---"
# 第三轮 N-ε：钩子脚本（integrations/hooks/）定位工程根的顺序是
# GUARDRAIL_ENGINE_DIR（旧名 AGENTGUARD_DIR / HARNESS_DIR 兼容）→ 指针文件 → 常见位置 → 从 cwd 向上找。
# 指针这一环原先没有任何脚本生成 = 文档里的选项②空转，这里补上（幂等）。
mkdir -p "$GLOBAL_DIR"
# 指针是给 **Python 钩子**读的，必须写 Windows 风格正斜杠路径：git-bash 的 `pwd` 给的是
# `/d/...`，Python 在 Windows 上打不开（实测踩到）。取值在文件顶部统一算好（HARNESS_WIN ←
# where.sh --win，内部走 cygpath -m），此处直接复用 —— 一处求值，两处受用。
printf '%s\n' "$HARNESS_WIN" > "$GLOBAL_DIR/harness.path"
echo "  ✓ 已写入 $GLOBAL_DIR/harness.path → $HARNESS_WIN"
echo "    （供 Claude/Codex 钩子在无环境变量时定位工程；换工程位置重跑本脚本即可）"

echo "=== 装配完成。所有 AI 工具现在读取同一份守则 ==="
