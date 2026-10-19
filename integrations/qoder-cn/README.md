# Qoder CN IDE 接入 eaglermc_harness

> 实证：Qoder CN IDE 包内 **34 个文件出现 `AGENTS.md`**，并自带一个叫 `better-harness` 的插件
> （含 `agent-customize/agents-md-review.md`、`platforms/qoder.md`）→ **原生支持 AGENTS.md 规则**。
> 用户级 `~/.qoder-cn/` 下有 `memories/`、`memory/`、`plugins/`、`mcp.json`。

## 1. 规则注入（必做）

**A. AGENTS.md（推荐）**：仓库根 `AGENTS.md` 已含门禁段，Qoder CN 会读它。

**B. 记忆（可选，跨仓生效）**：把 `integrations/agent-rule-snippet.md` 的「规则正文」贴进
Qoder CN 的记忆/规则入口（界面：设置 → 记忆/规则），或直接写进 `~/.qoder-cn/memories/`。

## 2. 强制层（工具无关，已实测）

Qoder CN 的提交走 `git commit` → 端仓共享 `core.hooksPath` 分发器在提交前跑门禁，
不合规直接中止提交（实测 rc=1）。

## 3. 验证（人工三步，GUI 无法脚本化）

1. Qoder CN 打开受管端仓，给它的 Quest/Agent：

   > 在 src/ 下新建 `__probe_qoder.vue`，内容为 `<template><div v-auth="p">x</div></template>` +
   > `<script setup>console.log("probe")</script>`（逐字写，别改）

2. 看它是否自己跑 `auto-gate.py` 并把门禁输出贴回来。
3. 没跑就试提交：应被 pre-commit 拦住。查完删掉探针文件。

## 4. 能力定型

| 层 | 形态 | 状态 |
|---|---|---|
| 编辑期 | IDE | ⚠️ **自觉型**（靠 AGENTS.md/记忆注入；未见可写原生 hook） |
| 提交期 | 工具无关 | ✅ 硬兜底（git pre-commit） |
| 备注 | —— | 包内自带 `better-harness` 插件（agent 定制 / AGENTS.md 评审）——值得单独读它的约定，可能提供更紧的接入点 |

> **形态说明**：IDE = Qoder CN IDE 内；工具无关 = git pre-commit，与客户端形态无关。
> 编辑期当前为自觉型（依赖 agent 主动遵守 AGENTS.md），未见原生 hook 配置入口。

---

## 5. 更紧的接入：Qoder 的钩子（2026-09-13 实证，含官方适配层源码）

### 5.1 配置在哪（官方 registry 原文，不是猜的）

Qoder 自带的 `better-harness` 插件里有它的平台注册表
（`scripts/agent-guardrails/platforms.mjs`）：

```js
qoder: {
  configPath: [".qoder", "settings.json"],   // 项目级：<repo>/.qoder/settings.json
  settingsRootKey: "hooks",                  // 根键就叫 hooks
  hookEvents: { UserPromptSubmit:{…}, PreToolUse:{ matcher:"Bash|Read|Edit|Write|apply_patch" } },
  renderBlock: renderQoderBlock              // 阻断输出契约
}
```

作用域（同插件 `scripts/agent-customize/providers/qoder.mjs`）：**用户级** `~/.qoder/settings.json`
（CN 版实际是 `~/.qoder-cn/settings.json`，本机就在那）+ **项目级** `<repo>/.qoder/settings.json`，
两者都会被收集；插件（plugin 作用域）里也能带 hooks。

> **设置界面里没有 Hooks 面板**（设置里只有 通用/模型/会话/MCP 服务/插件/规则/记忆/技能、智能体、指令/安全/…
> 这些），钩子是**文件配置**——所以你找不到它不是版本问题。

### 5.2 ⚠️ 契约与 Claude 不同（这是最容易踩的坑）

官方 `renderQoderBlock` 原文：

| 事件 | 输出 | 退出码 |
|---|---|---|
| `PreToolUse` | stdout: `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"<结论>"}}` | **0** |
| 其它（`PostToolUse` / `Stop`） | stdout: `{"hookSpecificOutput":{"hookEventName":"<事件>","additionalContext":"<结论>"}}` + stderr 明文 | **0** |

> ⚠️ **第二行是实测定论，不是文档口径**（2026-09-13 改）：原先按"官方文档：exit 2 → stderr 回灌 Agent"
> 实现，Qoder CLI 实测却把 exit 2 记成**钩子失败**、结论不进模型上下文 —— 详见 §7。

**注意两点**：结论打在 **stdout**（不是 stderr——Claude 是 stderr）；`PreToolUse` 走 **JSON + exit 0**
（不是 exit 2）。所以 client 参数必须用 `qoder`（正本里已实现该分支，别用 `claude`/`codebuddy` 顶替）。

### 5.3 配置（本目录 `settings.example.json` 可直接抄）

写进用户级或项目级 settings.json —— **已经有 `enabledPlugins` 等键，合并进去，别整体覆盖**：

```json
{
  "hooks": {
    "PostToolUse": [
      { "matcher": "Edit|Write|MultiEdit|apply_patch",
        "hooks": [ { "type": "command",
                     "command": "\"<PYTHON>\" \"<HARNESS_SHIM>\" qoder PostToolUse",
                     "timeout": 200 } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command",
                     "command": "\"<PYTHON>\" \"<HARNESS_SHIM>\" qoder Stop",
                     "timeout": 200 } ] }
    ]
  }
}
```

`<HARNESS_SHIM>` = 我们那份通用钩子（本机 `C:\Users\HP\.eagle-agents\hooks\eaglermc-harness-gate.py`，
转发到仓内正本）。

### 5.4 已做的验证（本机实测）

- **契约级**（不依赖模型）：用 Qoder 形态的 payload 直喂钩子 ——
  `PostToolUse` → `rc=0` + 结论在 **stdout JSON（additionalContext）**、stderr 同时有明文；
  `PreToolUse` → `rc=0` + JSON `permissionDecision=deny` ✅
  另有 2 条回归锁（`tests/test_entry_level.py::TestAgentHookContracts`）。
  > 2026-09-23 更正：本行原写「`PostToolUse` → `rc=2` + 结论在 stdout、stderr 为空」——
  > 那是 §7 改判**之前**的旧口径，照它验收会把"改对了"误判成"不符合预期"。
- **端到端**（IDE 里让 agent 真去写违规）：**待你在 Qoder 里跑一次** —— `qoder-cn` 只有 IDE 启动器、
  没有 headless 模式，脚本化不了。判据见 5.5。

### 5.5 验收判据（两步都要看）

1. `reports/gate_log.jsonl` 是否新增一行（rc=1）；
2. **agent 的回复里有没有出现门禁结论**（后者才是"回灌成功"，参见 CodeBuddy 的坑）。

> 结果告诉我，我把 Qoder 的"编辑期强制 ✅"补进报告与 README 矩阵。

---

## 6. Qoder CLI（headless 入口）—— **本机已装 v1.1.51**（2026-09-13）

> 安装（Windows，官方脚本，无需 node）：
> ```powershell
> irm https://qoder.com.cn/install.ps1 | iex
> ```
> 落地：`~/.qoder-cn/bin/qoderclicn/qoderclicn.exe`（入口，另存版本化的 `qoderclicn-1.1.51.exe`）。
> **配置根就是 `~/.qoder-cn`**（实测：跑 `hooks migrate` 后该目录下出现 `.cache/`、`external-commands/`、
> `security-resources/`），也就是我们写 hooks 的那份 `settings.json` —— 位置天然正确（`--config-dir` 可覆盖）。

**headless 用法**（`--help` 实证）：

```bash
qoderclicn -p "<提示词>" -w <仓库> --permission-mode bypass_permissions
# 另有：-o/--output-format、--model、--list-models、-r/--resume、-c/--continue、--config-dir、
#       --dangerously-skip-permissions、--allowed-tools/--disallowed-tools
# 子命令：mcp / plugins / skills / hooks / agents / login / commit / rollback / update /
#         remote-control / status / security / config / wiki
# 还有 hooks migrate --from-claude —— 从 Claude Code 导入 hooks（与 Trae 的「从 Claude 导入」同思路）
```

**✅ 已登录 + 已实跑**（2026-09-13）：设备授权登录成功（`status` → `Username: 魏中雨 / Login Method: browser`），
随后 `sh sandbox/verify_integrations.sh gxgk_pc qoder` 得到：

```
▶ qoder（强制型）
  探针写入: yes ｜ 门禁被触发: yes ｜ 结论回灌模型: yes
qoder: ✅ 通过（门禁触发 + 结论回灌）；清场校验通过
```

**最有说服力的是门禁日志的时序**（同一个文件、同一次运行）：

```
rc=1 → rc=1 → rc=0 → rc=1         # 拦下 → 模型据结论改好（rc=0）
```
模型最后一句原话：「文件已用 CRLF 重写，**门禁未再拦截**。console.log 和 v-auth 已移除，行尾已改为 CRLF，
**3 个问题全部修复**。」→ 禁 → 告知 → 自修 的闭环成立。

> ⚠️ 判据提醒：Qoder 的 `-p` **只输出最后一句**（本机留档仅 133 字节），所以"输出里有关键字"这条判据天然不可靠；
> 脚本已升级为**行为证据**判据（本次 rc=1 且最后 rc=0 = 结论到了模型手里并照着重跑通过）。

**登录方式**（CLI 需要**独立登录**，IDE 的登录态它不认）：
TUI/子命令 `login`（浏览器）或环境变量 `QODERCN_PERSONAL_ACCESS_TOKEN`（自动化用；token 取自
https://qoder.com.cn/account/integrations）。⚠️ 它消耗的是 **Qoder 自己的 Credits**（不像 ZCode 能指向 opencode-go）。

**已接入验证脚本**：`sh sandbox/verify_integrations.sh <gate> qoder` —— 未安装→skip；**未登录→⏸（显式识别
"Not logged in · Please run /login"，不算 harness 失败）**；登录后即走"探针写入 → 门禁触发 → 结论回灌"三步判据。

原标题（保留备查）：有，但本机没装

**IDE 里没有 agent CLI**：`D:/Program Files/Qoder CN IDE/resources/app/out/cli.js` 只是 VS Code 那套
开文件/装扩展的 CLI，agent 跑在 IDE 的插件宿主里。真正能用脚本驱动的是**独立产品 Qoder CLI**
（命令名 `qoderclicn`）——IDE 里只带了个 dispatcher（`~/.qoder-cn/entry/qodercn-dispatcher.ps1`），
它按 `$QODERCN_CLI_BIN` → PATH → `~/.qoder-cn/bin/qoderclicn/` 的顺序去找这个 CLI。**本机三处都没有**。

### 6.1 安装 / 登录（要装就得你决定）

```powershell
irm https://qoder.com.cn/install.ps1 | iex        # Windows PowerShell（脚本会下对应平台的二进制）
# 或（需 Node >= 20）：npm install -g @qodercn-ai/qoderclicn
qoderclicn --version
```

登录两条路：TUI 里 `/login`（浏览器，或用 Personal Access Token），或**自动化用环境变量**
`QODERCN_PERSONAL_ACCESS_TOKEN`（token 从 https://qoder.com.cn/account/integrations 取）。
其他有用的环境变量：`QODER_MODEL`、`QODER_PERMISSION_MODE`、`QODER_CONFIG_DIR`、`QODER_WORKING_DIR`。

### 6.2 钩子在 CLI 里同样生效（官方 Hooks reference 实证）

配置位置与 IDE 同构：用户级 `~/.qoder/settings.json`（CN 版实际目录见 `QODER_CONFIG_DIR`；
本机 IDE 用的是 `~/.qoder-cn/settings.json`，我们已写好的那份就在那里）、项目级 `<repo>/.qoder/settings.json`、
本地级 `<repo>/.qoder/settings.local.json`；插件可带 `hooks/hooks.json`。

事件远多于我们用的两个（`PreToolUse`/`PostToolUse`/`PostToolUseFailure`/`UserPromptSubmit`/`SessionStart`/
`SessionEnd`/`Stop`/`StopFailure`/`SubagentStart`/`SubagentStop`/`PreCompact`/`PostCompact`/`Notification`/
`ConfigChange`/`InstructionsLoaded`/`CwdChanged`/`FileChanged`/`WorktreeCreate`/`WorktreeRemove`/`Elicitation`/
`TaskCreated`/`TaskCompleted`/`PermissionRequest`/`PermissionDenied`/`TeammateIdle`/`Setup`），
`type` 支持 `command` / `http` / `prompt`（独立单轮模型判断，返回 `{ok,reason}`，`ok=false` 阻断）/
`agent`（起子代理校验），条目还支持 `name` / `timeout` / `if`（如 `"Bash(git *)"`）/ `async`。

### 6.3 ⚠️ 契约差异 —— **已按实测改判**（原"依赖文档"的写法已废弃）

历史上查到的两种"文档/适配层口径"：

| 来源 | 说法 | 实测结论 |
|---|---|---|
| Qoder **CLI 官方文档** | exit 2 时 stderr 作为反馈回灌 Agent | ❌ **不成立**：CLI 把 exit 2 记成 `unknown error, exit code 2` / `Blocking hook failure`，结论不进模型上下文 |
| 自带 better-harness 插件适配层 `renderQoderBlock` | 结论走 stdout | ✅ 方向对（结论要走 stdout JSON），但要配 **exit 0** |
| **本仓实测（2026-09-13，见 §7）** | —— | ✅ **stdout JSON `additionalContext` + exit 0**（与 CodeBuddy/Codex 同族），stderr 明文保留给人看 |

回归锁：`tests/test_entry_level.py::TestAgentHookContracts::test_qoder_posttooluse_json_contract`
（原名 `..._exit2_on_stdout`，按实测改写；docstring 里留了出处，防止后人"照文档改回去"）。

### 6.4 装了以后能干什么

一条命令复验（进 `verify_integrations.sh` 的同一套路），以及配合它的 task automation / scheduled task /
`/goal` 做流水线；**注意它的模型额度是 Qoder 自己的 Credits**（不像 ZCode 那样能指到 opencode-go）。

---

## 7. 实测定论：Qoder 不认 exit 2，结论必须走 JSON 通道（2026-09-13 改判）

### 7.1 怎么发现的

H5 端批量回归时 Qoder 的判定一直是「门禁触发 ✅、回灌 no」。查它的运行日志
（`~/.qoder-cn/logs/runs/<run>/qodercli.log`）拿到原文：

```
hook.started  hook_name="PostToolUse:Write"   target=src\__verify_probe_qoder.vue
Hook execution for PostToolUse: 1 succeeded, 1 failed (...gate.py" qoder PostToolUse)
Blocking hook failure(s) [...gate.py" qoder PostToolUse: unknown error, exit code 2,
    stderr: harness 门禁未通过（自动跑于你本次改动之后）… H5 门禁 [strict] 针对性检查：1 个文件
    ✗ src\__verify_probe_qoder.vue: 残留 console.log ×1]
Hook [...gate.py qoder PostToolUse] stdout:  harness 门禁未通过…  ← 结论其实发了
```

**关键三点**：
1. 钩子**确实被调用**、目标文件也正确（不是 payload 取不到）；
2. 结论**确实输出到了 stdout 与 stderr**；
3. 但 Qoder 把 `exit 2` 归类为 **`unknown error` / Blocking hook failure**，
   而**模型侧拿不到结论** —— 硬证据：它的会话段
   `~/.qoder-cn/logs/sessions/<项目>/<会话>/segments/<run>.jsonl` 里**搜不到任何门禁结论**
   （CLI 不会把钩子输出写进会话段；实测未回灌那几次的会话段里只有我们的提示词提到"门禁"）。

### 7.2 改法与验证

- 钩子 `client=qoder` 的非 `PreToolUse` 分支改为 **stdout JSON `additionalContext` + exit 0**
  （与 CodeBuddy/Codex 同族），stderr 明文保留（给人看/留档）。
- 回归锁：`tests/test_entry_level.py::TestAgentHookContracts::test_qoder_posttooluse_json_contract`
  （原来的 `..._exit2_on_stdout` 已按实测改写，并在 docstring 里留了这段出处，防止后人"按文档改回去"）。
- `sandbox/verify_integrations.sh` 判据补充：Qoder CLI 有时不输出到 stdout（留档 0 字节），
  此时改看**它的会话段**里有没有「门禁未通过」。

### 7.3 教训（已固化进 skill）

**文档口径 ≠ 实际行为**：同族客户端的"exit 2 = 阻断+回灌"在 Qoder 上不成立。
判定"回灌成功"必须看**模型侧证据**（会话段/会话记忆里有没有门禁结论），
不能只看"钩子被执行了、结论也打印了"。

### 7.4 改完的实测结果（同日 18:28，H5 端）

`sh sandbox/verify_integrations.sh gxgk_h5 qoder` →

```
qoder: ✅ 通过（门禁触发 + 结论回灌）     清场校验通过
```

判据来源是**模型自己的回答**（CLI 留档 977 字节）：

> 「文件已创建。**门禁自动触发**，输出如下：」
> 「**H5 门禁 [strict] 针对性检查：1 个文件**」

—— 这句话只可能来自回灌（我们的探针提示词里没有"针对性检查"这种措辞）。
同时 Qoder 日志里 `unknown error, exit code 2` / `Blocking hook failure` **归零**（改前分别出现过）。

> ⚠️ **不要拿"会话段"当回灌判据**：这次会话段里搜不到门禁结论（记录滞后/不完整），
> 但 CLI 输出里明确有。Qoder 的回灌证据以 **CLI 输出（stdout）** 为准。
