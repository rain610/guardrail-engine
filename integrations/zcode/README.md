# ZCode 接入 eaglermc_harness

> **2026-09-13 实证更新**：ZCode **不是黑盒**——它的内置运行时 `resources/glm/zcode.cjs` 里能直接读到它的约定：
> - **支持 `AGENTS.md`**（bundle 内命中 20 次，其自带说明文本写作 *"instruction files such as AGENTS.md in the ZCode client"*）；
> - **钩子机制与 Codex 同款**：`hooks/hooks.json` + 事件 `PreToolUse` / `PostToolUse` / `SessionStart` / `Stop`
>   + 输出键 `hookSpecificOutput.additionalContext`（命中 9 次）+ `hook_trust`（工作区钩子信任审核）；
> - 项目配置发现顺序：`zcode.json` → `.zcode/config.json`（`hooks` 为其中的一个字段，另有 `hooksRoot{enabled,timeoutMs}`）。
>
> 结论：**规则层可直接用 AGENTS.md（端仓已配）；编辑期钩子可接（Codex 同款契约）**，
> 但 hooks 段的确切 schema 要在它的 Workspace Hook 面板里配置一次（我不擅自往你的仓里写配置）。

## 1. ZCode 有图形化钩子面板（2026-09-13 截图实证）

**设置 → 钩子 → 新建钩子**（左侧栏：常规/外观/模型设置/浏览器控制/电脑控制/Agent 能力[记忆·子智能体·插件·MCP 服务器·技能·命令·**钩子**]/数据与统计/索引库/使用统计/引导）。

表单字段与建议填法（表单原文：`管理任务生命周期钩子，在特定事件发生时自动执行命令。`）：

| 字段 | 填什么 | 说明 |
|---|---|---|
| **事件** | `PostToolUse` | 下拉默认是 `PreToolUse`；**门禁要查"写完的内容"，必须选写后事件**（见 §3：PreToolUse 查的是改动前的内容，新建文件更是无内容可查） |
| **运行方式** | `进程` | 保持默认 |
| **作用域** | `用户` | 全机器生效（覆盖所有端仓）；也可选项目级只对当前仓生效 |
| **匹配器** | `Write, Edit` | 占位提示原文"例如 Write, Edit, Bash"；**留空 = 匹配该事件的所有输入** |
| **命令** | `C:\Users\HP\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none\python.exe` | 只填解释器 |
| **参数**（每行一个 argv） | 见下方三行 | 表单把命令与参数分开，正好免掉引号地狱 |
| 高级 | 可不动 | 超时等 |

**参数三行**（逐行填）：

```
C:\Users\HP\.eagle-agents\hooks\eaglermc-harness-gate.py
zcode
PostToolUse
```

保存后建议再加一个 `Stop` 事件的钩子（同样三行，末行改成 `Stop`）——回合结束做**全仓兜底**。

## 2. 契约 = Codex 同款（实证）

`zcode.cjs` 内含 `hooks.json`、`hookSpecificOutput.additionalContext`、`hook_trust`（Workspace Hook Trust）
与事件名 `PreToolUse / PostToolUse / Stop / SessionStart / UserPromptSubmit / PermissionRequest`。
所以 client 参数用 **`zcode`**（正本里等价于 codex 分支：stdout JSON `additionalContext` 回灌模型）。
首次运行 ZCode 会要求**信任**该工作区的钩子（`bundleDigest`/`declarationDigest` 机制），照提示授权即可。

> ⚠️ ZCode 的 payload 是**扁平**的（2026-09-13 实证：整个 bundle 里 `tool_input` 出现 0 次、
> `file_path` 12 次——它读写工具的 zod 参数就是顶层 `file_path`/`content`）。
> 正本 `target_file()` 已同时兼容"嵌套 + 扁平 + 从命令提取"，并有 2 条回归锁钉住
> （`test_zcode_flat_payload_is_really_checked` / `..._command_payload_extracts_path`）——
> 否则接 ZCode 会"钩子跑了但取不到文件"→ 静默不检查。

## 3. 为什么内容检查必须放 PostToolUse，不是 PreToolUse

两条理由，**都不是"假绿"那么简单**（2026-09-23 按正本实际行为复核；旧版这里只写了一态）：

| 阶段 | 正本对"被编辑文件"做什么 | 内容检查有效吗 |
|---|---|---|
| PreToolUse | 文件**不存在**（= 新建场景）→ 直接放行；文件存在 → 对**改动前**的内容跑门禁 | ✗ 前者无内容可查；后者查的是**存量内容**，结论与本次编辑无关 |
| PostToolUse | 对**写完之后**的内容跑门禁，结论回灌模型 | ✓ 这才是"这次改动合规吗" |

所以：**PostToolUse 做内容门禁**，PreToolUse 只留给"危险命令/受保护路径"类前置判断。

> ★ 别照旧版那句话去推（"会因文件不存在而直接通过（假绿）"）—— 那是 v13 fail-closed 改造**之前**
> 的形态。中间有一段时间，`--only` 一个都没匹配上会走 rc=2，于是把门禁接在 PreToolUse 上会
> **把新建文件全拒掉**、理由还写成"门禁调用出错 rc=2（多半是配置问题）"（2026-09-23 实测
> qoder / trae / codebuddy 三种客户端都是 deny）。现在正本对"PreToolUse + 文件不存在"显式放行
> （`integrations/hooks/eaglermc-harness-gate.py`，锁在
> `tests/test_review_2026_09_23.py::TestPreToolUseNewFile`），**结论仍不变：内容门禁放 PostToolUse**。

## 4. 落盘位置与确切 schema（2026-09-13 实证，已填好并自检通过）

| 作用域 | 文件 |
|---|---|
| **用户级** | `~/.zcode/cli/config.json` ← GUI 里「作用域=用户」写这里 |
| **项目级** | `<工作区>/.zcode/config.json`（`zcode.json` 同理，含 `hooks` 段） |

**schema（从 `zcode.cjs` 的 zod 定义实证，注意是 `.strict()`——别加多余键）**：

```jsonc
{
  "hooks": {
    "enabled": true,            // ⚠️ 默认是 false！不开总开关装了也不跑
    "timeoutMs": 200000,
    "maxOutputBytes": 32768,
    "events": {                 // 合法事件：SessionStart / UserPromptSubmit / PreToolUse /
      "PostToolUse": [          //   PermissionRequest / PostToolUse / PostToolUseFailure / Stop
        {
          "matcher": "Write|Edit",       // 可省略；省略 = 匹配该事件全部输入
          "hooks": [                     // 非空数组
            {
              "type": "process",         // "process"=进程（GUI 的「运行方式：进程」）
                                         // "command"=shell 执行（另有 async/shell/timeout 字段）
              "command": "<PYTHON 全路径>",
              "args": ["<HARNESS_SHIM>", "zcode", "PostToolUse"],
              "timeoutMs": 200000,
              "enabled": true
            }
          ]
        }
      ],
      "Stop": [ /* 同上，args 末项改 "Stop" —— 回合结束全仓兜底 */ ]
    }
  }
}
```

> 我们这条**故意不写 `matcher`**：ZCode 的工具名与 Claude 未必一致，钩子自身带"取不到被编辑文件就跳过"的判据——
> 匹配不到不漏、匹配过宽也不慢。

**已验证（本机 2026-09-13）**：
- 配置写入 `~/.zcode/cli/config.json`（880 字节，`enabled=true`，PostToolUse + Stop 两条）；
- **自检通过**：按配置里的命令原样执行（ZCode 形态的**扁平** payload）→ `rc=0`，
  stdout 为 `{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"harness 门禁未通过…"}}`，
  stderr 为空，结论里带命中文件名 ✅
- **待你验收**：写配置后 ZCode 需**重载配置**（重启应用或重开「设置 → 钩子」页），
  页面里应显示已安装该钩子；若提示**工作区信任**要同意（它的日志会出现 `config_hooks_pending_trust`）。
  然后让 AI 改一个必拦违规 → 判据见 §6。

## 4.5 ✅ 本机已打通：headless CLI + opencode-go 通道（2026-09-13 实测，含端到端结论）

**ZCode 自带 headless CLI**（用 Electron 自带的 node 跑它的运行时，不弹窗口）：

```bash
ELECTRON_RUN_AS_NODE=1 "D:/Program Files/ZCode/ZCode.exe" "D:/Program Files/ZCode/resources/glm/zcode.cjs" \
  --cwd <仓库> -p "<提示词>"        # -p/--prompt：跑一条就不开 TUI
```
（`zcode version` / `doctor` / `login` / `plugins` / `skills` 等子命令也都在。）

**模型来源：opencode-go**（与 Codex/Claude Code 同一通道）。两处必须的搭配：

1. ZCode 的模型配置键是 **`model`**，值为**字符串** `"openai/<模型id>"`（用它的 `openai` 内置 kind —— 该 kind 不需要在配置里写 baseURL）：
   ```json
   { "hooks": { /* 我们那两个钩子 */ }, "model": "openai/deepseek-v4.1-flash" }
   ```
   ⚠️ 实验结论：`provider` / `modelCatalog` / 对象形态的 `model` 都会被它的 strict schema 判无效
   （表现是 `Error: Model config is missing`）——只有这个字符串形态可用。
2. opencode-go **必须带 `x-opencode-session` 头**，而 ZCode 侧无处配置自定义请求头 →
   中间放一个小代理补头 + 注入密钥：**`opencode-go-proxy.py`**（本目录，纯标准库）
   ```bash
   OPENAI_BASE_URL=http://127.0.0.1:8791/v1   OPENAI_API_KEY=dummy   # 真密钥由代理从 Hermes .env 运行时读取
   ```
   本目录的 `zcode-go.sh` 把这两步包成一个启动器（代理没起会自动起）：
   ```bash
   sh integrations/zcode/zcode-go.sh --cwd <仓库> -p "<提示词>"
   ```
   密钥**不落配置文件**，只在代理进程里注入（与 `~/.codex/get-key.py` 同一套原则）。

**端到端实测结果（2026-09-13，`sandbox/verify_integrations.sh gxgk_pc zcode`）**：

```
▶ zcode（强制型）
  探针写入: yes ｜ 门禁被触发: yes ｜ 结论回灌模型: yes
  日志: gxgk_pc targeted rc=1 ['PC-EOL','PC-CONSOLE','PC-VAUTH']
zcode: ✅ 通过（门禁触发 + 结论回灌）；清场校验通过
```

模型最终回复的原话（说明结论确实进了模型）：

> 创建后**确实遇到了一次自动门禁检查**（由 harness 在写入后自动触发，不是我手动跑的）……
> `harness 门禁未通过` / `PC 门禁 [strict] 针对性检查：1 个文件` / `❌ 发现 3 个阻断级问题`

**副产品**：ZCode 现在也进了 `verify_integrations.sh`（客户端参数写 `zcode`），
与 claude / opencode / codebuddy / codex 并列，可一条命令复验。

## 5. 强制层（工具无关，兜底仍在）

提交走 `git commit` → 端仓共享 `hooksPath` 分发器 → 不合规直接中止（实测 rc=1），与 IDE 是否读规则无关。

## 6. 验收判据（两步都要看）

1. `reports/gate_log.jsonl` 是否新增一行（rc=1）；
2. **agent 的回复里有没有出现门禁结论** —— 后者才是"回灌成功"的判据（参见 CodeBuddy 那个坑：
   钩子跑了、日志有记录、规则也命中，但模型一无所知）。

## 7. 能力定型（保存并验证后可从 🔧 升级为 ✅）

| 层 | 形态 | 状态 |
|---|---|---|
| 规则层 | IDE 插件 | ✅ AGENTS.md（×20 命中实证） |
| 编辑期 | IDE 插件 | 🔧 有完整 GUI 面板 + Codex 同款契约，正本已兼容其扁平 payload；**待你在面板里保存一次并验收** |
| 提交期 | 工具无关 | ✅ 硬兜底 |

---

## 附：为什么这里**故意不设 matcher**（2026-09-13 外部评审 G3 的答复）

外部评审提出：codebuddy / zcode 的 `PostToolUse` 未设 matcher，若其读类工具也携带可提取的路径/命令，
会多点触发（额外延迟与上下文噪音），建议收窄到写工具。

**核验与决定：保留不设 matcher，理由如下（都基于实测）**

1. **噪音可忽略**：抽了 H5 端批量回归里各客户端的门禁调用数（`reports/gate_log.jsonl` 按窗口统计）——
   claude 1 次 / opencode 1 次 / **codebuddy 4 次** / codex 2 次 / **zcode 3 次** / 每会话
   （qoder 180 次是它自己的 agent 循环重写同一文件，与 matcher 无关）。
2. **收窄的代价是"漏拦"**：这两家是 Claude 格式配置的 fork，但**工具名未必与 Claude 一致**
   （已有实例：Qoder 用 `Edit|Write|MultiEdit|apply_patch`，ZCode 的 payload 是扁平结构）。
   matcher 写窄一点，只要它换了工具名就会**静默漏拦** —— 而"漏拦"是本项目最不能接受的失败类型
   （对比"多跑一次"只是几秒延迟）。
3. **钩子自身已有兜底**：取不到目标文件就跳过，不会误报。

→ 结论：**用"多跑一次"换"不漏拦"**。若将来实测到某家读类工具真的高频触发（>20 次/会话），
再按它的工具名收窄并把工具名写进对应 README。
