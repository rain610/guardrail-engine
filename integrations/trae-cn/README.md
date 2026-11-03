# Trae CN 接入 guardrail-engine

> 实证：Trae CN 3.3.95 的 Electron 包内**命中了 `.trae/rules`**（`resources/app/**/index.mjs`），
> 且包内 6 个文件出现 `AGENTS.md` 字符串 —— 即它同时吃「项目规则目录」与「AGENTS.md」。

## 1. 规则注入（必做，二选一或都做）

**用仓库根 `AGENTS.md`**（本项目端仓都已配好，Trae CN 原生读它）—— **无需创建 `.trae/rules/`**：
规则正文只留 AGENTS.md 一处，复制到方言目录会造成两份漂移（这也是团队既有口径）。

> 若你偏好在 Trae 里再放一层：把 `integrations/agent-rule-snippet.md` 的「规则正文」贴进
> **Trae 设置里的规则入口**（本机生效），而不是往仓里加第二份规则文件。

## 2. 强制层（工具无关，已实测）

Trae CN 里的提交最终走 `git commit` → 端仓共享 `core.hooksPath` 分发器（本工程 `harness/hooks-dir`）
会在提交前跑门禁，**不合规直接中止提交**（实测 rc=1）。

## 3. 验证（人工三步，GUI 无法脚本化）

1. 用 Trae CN 打开一个受管端仓（如 `<你的 PC 端仓>`），把下面这句交给它的 AI：

   > 在 src/ 下新建 `__probe_trae.vue`，内容为：`<template><div v-auth="p">x</div></template>` +
   > `<script setup>console.log("probe")</script>`（逐字写，别改）

2. 看它**有没有自己跑** `auto-gate.py` 并把门禁输出贴回来（规则生效 = 会跑）。
3. 若没跑，直接提交试试：应被 pre-commit 拦住（硬兜底）。查完删掉 `__probe_trae.vue`。

## 4. 能力定型

| 层 | 形态 | 状态 |
|---|---|---|
| 编辑期（AI 改完自动跑门禁） | IDE | ⚠️ **自觉型**：靠规则注入；包内未见可写原生 hook 配置 |
| 提交期 | 工具无关 | ✅ 硬兜底（git pre-commit，工具无关） |

> **形态说明**：IDE = Trae CN IDE 内；工具无关 = git pre-commit，与客户端形态无关。
> 编辑期当前为自觉型（依赖 agent 主动遵守 AGENTS.md），包内虽有 hooks 资产管理但未见可写配置入口。

---

## 5. 更紧的接入：Trae CN 的 Hooks（2026-09-13 深挖，含包内实证）

**Trae CN 的钩子不是黑盒**——它把 hook 当成"上下文资产"管理，包内代码原文：

```js
[{ assetType: "hook",  scope: 3 /* Project|Global */, projectRelPath: ".trae/hooks.json", globalRelPath: "hooks.json" },
 { assetType: "command", scope: 3, projectRelPath: ".trae/commands/", globalRelPath: "commands/" }]
```

即：**项目级 `<repo>/.trae/hooks.json`**、**全局级 `~/.trae-cn/hooks.json`**（本机就是 `C:\Users\<用户名>\.trae-cn\hooks.json`）。

### 5.1 契约 = Claude Code 同款（可直接复用我们已有的钩子）

包内证据：事件名出现 `PreToolUse` / `Stop` / `hookEventName`；UI 有 `denyMessage`（**能拒绝工具调用**）、
状态栏有 Hooks 标签与 tooltip；原生模块里还有 `[Hooks] UserPromptSubmit blocked:`。
→ 与 Claude Code 同一套契约，所以**同一份正本不用改代码**。

> 🚫 **本节的 `client 参数用 claude` 已被 §5.6 推翻**（2026-09-13 抓包实测，2026-09-23 补标）：
> 正本为此新增了专门的 `trae` 契约 —— 它对 Trae **三路齐发**（stderr 明文 + stdout JSON +
> exit 2），而 `claude` 那一路只发 stderr。**client 一律写 `trae`**，命令形态按 §5.6 抄。

### 5.2 最省事的路子：让 Trae 自己"从 Claude 导入"

Trae 自带这个功能（包内文案原文）：

> *"The agent will read the `~/.claude/settings.json` file in the user's home directory and import its
> Hooks configuration."* —— 键名 `hooks_global_claude_import_desc`

而本机 `~/.claude/settings.json` 里**已经有我们门禁的 PostToolUse 钩子**（实测可用），
所以在 Trae 里：**设置 → Hooks → 全局 → 从 Claude 导入** ≈ 一键完成。

> ⚠️ 导入会把 Claude 里的**所有**钩子一起带过来（本机还有几个 `cbm-*` 的插件钩子）——
> 导入后把不需要的删掉即可，只留 `agentguard-gate` 那条。
>
> 🚫 **这条路子已被 §5.7 根因 B 推翻（2026-09-13 实测，2026-09-23 补标）：别用导入。**
> 导入进来的 Claude 版条目会与手写的 `hooks.json` **并存**（不是覆盖），而它带引号的命令形态
> 在 Trae 的 PowerShell 里直接语法错。**把「导入 CLAUDE 中的 Hooks 配置」关掉**，
> 改走 §5.6 的手写形态。

### 5.3 手写配置（不想导入时）

照 `hooks.example.json`（本目录）填真实路径，粘进 `~/.trae-cn/hooks.json` 或 `<repo>/.trae/hooks.json`
（下方形态 = §5.6 的实测结论；**免引号 + `trae` + `; exit $LASTEXITCODE`**）：

```json
{
  "hooks": {
    "PostToolUse": [
      { "matcher": "Write|Edit|MultiEdit|NotebookEdit",
        "hooks": [ { "type": "command",
                     "command": "<PYTHON> <HARNESS_SHIM> trae; exit $LASTEXITCODE",
                     "timeout": 200 } ] }
    ]
  }
}
```

> 本节原样曾是 `"\"<PYTHON>\" \"<HARNESS_SHIM>\" claude"` —— **照那个抄必然失效**（引号被
> PowerShell 拼崩、退出码被吞成 1、client 也不对）。2026-09-23 按 §5.6 改正，此处不再重复解释，
> 两个坑的现场证据都在 §5.6。

`<HARNESS_SHIM>`（用 `python <HARNESS>/sandbox/install_shim.py` 生成，别手搓） 指我们那份通用钩子（本机 `C:\Users\<用户名>\.guardrail-engine\hooks\agentguard-gate.py`，
它转发到仓内正本，换机器只改这一处）。**两个路径都不许含空格、都要写成正斜杠。**
项目级 `.trae/hooks.json` 建议提交进仓库（团队共享）。

### 5.3.5 ✅ 本机实测：Trae 的「从 Claude 导入」确实生效（2026-09-13）

在设置 → Hooks → **全局** 里打开「导入 CLAUDE 中的 Hooks 配置」后，Trae 真的把 Claude 的钩子写进了
`~/.trae-cn/hooks.json`（本机 411 字节）：

```json
{
  "hooks": {
    "PostToolUse": [
      { "matcher": "Write|Edit|MultiEdit|NotebookEdit",
        "hooks": [ { "type": "command",
                     "command": "\"…python.exe\" \"…agentguard-gate.py\" claude",
                     "timeout": 200 } ] }
    ]
  }
}
```

即：**一键导入 >> 手写配置**（导入的形态与 `hooks.example.json` 完全一致，且不含我们没导的那些 `cbm-*`）。
导入后 UI 的「已配置的 Hooks」应能从 0 变 1；同一页面还有 **运行日志** 面板可看执行记录。

### 5.4 ⚠️ 两个必须注意的开关（包内文案实证）

| 开关 | 证据 | 要点 |
|---|---|---|
| **启用 + 安全警告** | `hooks_global_security_warning_desc`：*"Once this feature is enabled, the Hooks commands configured in the user's home directory will execute automatically. Please ensure the source of the file is trustworthy…"* | 钩子文件放好了**不等于会执行**——在设置里显式启用并确认信任 |
| **运行方式**（本机默认是「沙箱运行」，**必须改**） | 官方文案：`沙箱运行` = "命令在沙箱中自动执行"；`沙箱外运行(host)` = "命令始终在沙箱外自动运行（存在一定风险）" | **改成「沙箱外运行」**。我们的钩子要调用**工作区之外**的 python 与门禁工程、并读端仓真实文件——沙箱里大概率不可达，表现为"钩子装好了却什么都不做"（最坏的静默失效）。该开关**不在** `settings.json` 里，只能在 UI 里改 |

### 5.5 验收（配完这样确认）

1. 在受管端仓里让 Trae 的 agent 改一个**必拦违规**（例如 PC 端 `console.log`）；
2. 看两处：门禁日志 `reports/gate_log.jsonl` 是否新增一行（rc=1），
   以及 **agent 的回复里有没有出现门禁结论**（后者才是"回灌成功"的判据，参见 CodeBuddy 那个坑）。

> 结果告诉我，我把"编辑期强制 ✅"补进 `docs/2026-09-13_多客户端接入验证报告.md` 与 README 矩阵。

---

## 6. headless 入口：**没有**（2026-09-13 查证，与 ZCode/Qoder 对比）

包内证据：Trae 的 agent 是**原生模块 + IDE 宿主**驱动——`resources/app/modules/ai-agent/ai_agent.dll`
（agent 主体）与 `modules/ai-agent/toolhost.dll`（工具宿主）；全盘扫描安装目录**没有任何可调用的 agent CLI**
（命中的 `*cli*.js` 都是 VS Code 的语言服务客户端 / git-ai 扩展之类的内部件）。
`%LOCALAPPDATA%\Temp\trae-agent-toolhost\jobs` 是 IDE 自己派的内部作业目录，不是可编程入口。

对比：ZCode 有 `resources/glm/zcode.cjs`（headless CLI，已打通，见 `integrations/zcode/README.md`）；
Qoder 有独立产品 `qoderclicn`（未装，见 `integrations/qoder-cn/README.md` §6）。

→ **Trae CN 的门禁验收只能在 GUI 里做**：设置 → Hooks 里把「运行方式」改成**沙箱外运行**，
然后让 agent 改一个必拦违规，按 §5.5 的两步判据验（日志新增 **且** agent 回复里出现门禁结论）。

---

### 5.6 实测根因：Trae 在 **PowerShell** 里执行钩子命令（2026-09-13 抓包定位）

首轮测试结果是**假象**：门禁没拦、模型说「未遇到任何自动检查」。Trae 自己的日志把真相说清了。

**排查通道（以后就用它）**：`%APPDATA%/Trae CN/logs/<会话>/window2/agent-hooks.log` ——
里面每次钩子执行的 **Command / stdin payload / Exit Code / stdout / stderr / ExecEnv** 全都有。

**两个坑（都和「宿主 shell 是 PowerShell」有关）**：

| # | 现象（agent-hooks.log 原文） | 根因 |
|---|---|---|
| 1 | `Exit Code: 1`，stderr：`所在位置 行:1 字符: 773 … 表达式或语句中包含意外的标记`（后面是我们那条带引号的命令） | 命令里带**引号**，被 Trae 拼进 PowerShell 后引号解析崩了 → 钩子压根没跑起来（与 Codex 当年同一个坑） |
| 2 | 修好引号后仍 `rc=1`（期望 2） | PowerShell **管道**会把非 0 退出码**吞成 1**（实测：正本直跑 rc=2 → 经 PS 管道 rc=1）。而 Trae 只把 `exit 2` 当「阻断 + stderr 回灌模型」，其它退出码算「非阻断错误」→ 模型什么都收不到 |

**修法（本机已落地）**：

1. `~/.trae-cn/hooks.json` 里的命令**不带任何引号**（路径无空格 + 正斜杠），并在末尾补
   `; exit $LASTEXITCODE` 把真实退出码透传出来：

   ```json
   { "hooks": { "PostToolUse": [ { "matcher": "Write|Edit|MultiEdit|NotebookEdit",
       "hooks": [ { "type": "command",
                    "command": "<你的python.exe> <HARNESS>/integrations/hooks/agentguard-gate.py trae; exit $LASTEXITCODE",
                    "timeout": 200 } ] } ] } }
   ```

   > 上面两处**必须换成你自己机器的实际路径**（2026-09-18 改：这里原先印着维护者本机的
   > 绝对路径 —— 而这条命令是全文档最容易被直接抄走的一段，照抄必然跑不起来）：
   > - `<你的python.exe>`：`python -c "import sys;print(sys.executable)"` 打印的那个，
   >   写成 **Windows 风格正斜杠**（`C:/.../python.exe`）；
   > - `<HARNESS>`：`sh <HARNESS>/sandbox/where.sh --win` 打印的值。
   >
   > 整条命令里**不能出现任何引号**，路径里也不能有空格 —— 这是下面两个坑的直接产物。

2. 正本新增 **`client=trae` 契约**：`PreToolUse` → stdout JSON `permissionDecision=deny`（exit 0）；
   其它事件 → **stdout JSON `additionalContext` + stderr 明文 + exit 2**（三路齐发，Trae 认哪条都能拿到结论）。

**实测（照 Trae 的方式：PowerShell 管道 + 合法 payload）**：

```
rc     = 2                                    ← Trae 认的阻断码
stdout = {"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"harness 门禁未通过…"}}
stderr = harness 门禁未通过…                  ← 官方口径那一路
回归锁：tests/engine/test_entry_level.py::TestAgentHookContracts::test_trae_contract_dual_channel_exit2
```

> **改完要重载**：Trae 可能缓存了导入时的旧命令 —— 在设置 → Hooks 里把「已配置的 Hooks」关掉再打开
> （或重开窗口），让它重读 `~/.trae-cn/hooks.json`，然后重跑一次验收。

---

### 5.7 第二轮实测：挖出两个更深的根因（2026-09-13）

命令形态修好后重跑，结果**还是**「模型说没遇到检查」。把 `agent-hooks.log` 逐行读完，发现两条：

**根因 A（我们自己的 bug，最致命）：钩子被 GBK 控制台打崩了**

```
Exit Code: 1
stderr: Traceback (most recent call last):
          File "...agentguard-gate.py", line 246, in main
            print(json.dumps({"hookSpecificOutput": {...
        UnicodeEncodeError: 'gbk' codec can't encode character '\u2717' in position 129
```

Windows 中文控制台给钩子的 stdout 是 **GBK(cp936)**，而我们的结论里有 `✗`（U+2717）→ `print` 直接抛异常
→ 钩子整体 traceback 退出 → **结论一个字都没发出去**。表观现象还是「门禁日志有、模型看不到」，
而且从现象上**完全看不出是编码问题**。
修法：钩子启动即 `sys.stdout/stderr.reconfigure(encoding="utf-8", errors="replace")`
（encoding 再怪也只是个别字符变问号，绝不因编码丢结论）。回归锁：
`tests/engine/test_entry_level.py::…::test_hook_survives_gbk_console`（在 `PYTHONIOENCODING=gbk` 下必须 rc=2 且结论完整）。

**根因 B：Trae 的「从 Claude 导入」会把导入的条目一起跑**

同一时刻的另一次执行是**旧的带引号命令**（`…"gate.py" claude Stop; $…` → PowerShell 语法错、Exit Code 1）：
那是导入进来的 Claude 版 Stop 钩子，它**和手改的 `hooks.json` 并存**（不是覆盖）。
所以要么把「导入 CLAUDE 中的 Hooks 配置」**关掉**（推荐，手写的那份已经是正确形态），
要么让被导入的那条命令本身也免引号 —— 后者我们顺手做了防御：`claude` 契约改成**双通道**
（stderr 明文 + stdout JSON），导入到别家也能拿到结论。

**教训**：客户端说"钩子执行了"≠ 结论送到了。判断链路要一路看到 **Exit Code / stdout / stderr 的实际内容**，
`Exit Code: 1 + UnicodeEncodeError` 这种才算把根因钉死。

---

### 5.8 ✅ 跑通实证（2026-09-13 16:52，三段证据）

| # | 证据来源 | 原文 |
|---|---|---|
| ① | Trae 钩子日志 `agent-hooks.log` | `ExecEnv: host` ｜ **`Exit Code: 2`** ｜ stdout 带 `{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"harness 门禁未通过…✗ src\\__probe_trae.vue: 残留 console.log…"}}` ｜ stderr 同名明文 |
| ② | 门禁日志 `reports/gate_log.jsonl` | `16:52:17 <受测 gate> rc=1 ['<规则ID>','<规则ID>']` |
| ③ | **Trae 会话记忆**（模型侧） | 16:52:44 `outcome`:「文件创建覆写操作**触发拦截，未实际生效**」<br>`learned`:「**仓库存在自动门禁检查机制**」「门禁检查会拦截残留 console.log 代码」「门禁检查不允许使用空壳 v-auth 指令，需改用 store getter/行级字段」<br>16:53:36：「**修复门禁未通过的问题**，按提示改到通过或使用行内豁免」…「探针文件使用行内豁免理由不成立」 |

对比同一会话更早的两条记录（15:55、16:31，均为修复前）：
`outcome = 文件创建成功，未遇到任何自动检查/门禁/拦截输出` —— 模型侧从「什么都没遇到」变成
「被拦下 + 复述出具体规则（含空壳 v-auth 的替代写法）」，**回灌闭环成立**。

#### GUI-only 客户端的取证方法（Trae 没有 CLI，靠这三处）

1. **钩子执行日志**（最硬）：`%APPDATA%/Trae CN/logs/<会话>/window*/agent-hooks.log`
   —— 每次执行有 Command / stdin payload / ExecEnv / **Exit Code** / stdout / stderr / Duration。
2. **门禁侧日志**：`reports/gate_log.jsonl`（ts / gate / mode / rc / rules）。
3. **模型侧证据**：`~/.trae-cn/memory/projects/<项目 slug>/<日期>/session_memory_*.jsonl`
   —— 每条含 `intent / actions / outcome / learned`，能直接看出模型**有没有把结论吃进去**。

> 注意：会话记忆是**异步、压缩式**写入的（`message_summary_time` 会滞后几分钟），
> 刚跑完可能还没落盘 —— 等一两分钟再读，别据此下"没回灌"的结论。
