# CodeBuddy 接入 guardrail-engine

> **2026-09-13 实测升级**：CodeBuddy **支持原生钩子**（与 Claude Code 同族），本机已接好并**端到端验证通过**——
> 它从"自觉型"升级为**强制型**：AI 写完文件 → 自动跑门禁 → 结论回灌模型（模型会据此自修）。

## 1. 规则注入（必做）

`guardrail-engine` skill 已软链到 `~/.codebuddy/skills/`；仓库根 `AGENTS.md` 也已含门禁段。

**用户级（跨仓 / 不在受管仓里工作时也生效）**：`sandbox/bootstrap_agent_env.sh` 会写
`~/.codebuddy/rules/agentguard.md` —— 一份指向 `~/.guardrail-engine/GLOBAL_AGENTS.md`
的**薄壳**（不是正文副本）。它是 **rules 目录**形态，且该目录默认不存在，由脚本 mkdir 现建。

> ⚠ 别把 `integrations/agent-rule-snippet.md` 的正文**复制**进 `~/.codebuddy/rules/`：
> 那等于在 HOME 里养第二份正文 —— 正文改了它不跟着变，而它正躺在"工具自动加载"的位置上，
> 仓库里的锁看不见。这与 bootstrap 薄壳的单一权威源设计直接冲突。

## 2. 编辑期钩子（推荐，已实测）

配置在 **`~/.codebuddy/settings.json`**（与已有的 `enabledPlugins` **并列**，别覆盖整个文件）：

```json
{
  "enabledPlugins": { "...": "原有内容保持不动" },
  "hooks": {
    "PostToolUse": [
      { "hooks": [ { "type": "command",
                     "command": "\"<PYTHON>\" \"<HARNESS_SHIM>\" codebuddy PostToolUse",
                     "timeout": 200 } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command",
                     "command": "\"<PYTHON>\" \"<HARNESS_SHIM>\" codebuddy Stop",
                     "timeout": 200 } ] }
    ]
  }
}
```

本机实际值（可照抄）：`<PYTHON>` = uv 的 python 全路径，`<HARNESS_SHIM>` = `C:\\Users\\HP\\.agentguard\\hooks\\agentguard-gate.py`
（shim 会转发到仓里正本，换机器只改这一处）。

**为什么不写 matcher**：CodeBuddy 的工具名与 Claude 未必一致，钩子自身带"取不到被编辑文件就跳过"的判据——
匹配不到不漏、匹配过宽也不慢。

## 3. ⚠️ 关键坑：退出码语义与 Claude 不同

CodeBuddy 的钩子契约（从它的 dist 原文实证）：

```
Exit code 0 - hookSpecificOutput.retry requests model retry
Other exit codes - show stderr to user only     ← 只给用户看，**不回灌模型**
```

所以**照 Claude 那样 `exit 2 + stderr` 是无效的**（实测：钩子确实被触发、门禁日志有记录，
但模型回答"没有遇到任何检查"）。回灌模型必须走 **stdout JSON**：

| 事件 | 输出 | 效果 |
|---|---|---|
| `PostToolUse` | `{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"<门禁结论>"}}` | 结论进模型上下文（编辑后自修） |
| `PreToolUse` | `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"<结论>"}}` | 真阻断（留给"危险命令"类前置判断） |

> 我们那份正本 `integrations/hooks/agentguard-gate.py` 已含 `client=codebuddy` 分支，照上表输出；
> 并有 2 条契约回归锁（`tests/engine/test_entry_level.py::TestAgentHookContracts`）。

## 4. 强制层（工具无关）

提交走 `git commit` → 端仓共享 `core.hooksPath` 分发器 → 不合规直接中止（实测 rc=1）。

## 5. 验证结果（2026-09-13 实跑）

```
codebuddy -p "<让 AI 写含违规的文件>" --permission-mode bypassPermissions
→ 门禁日志 +2 条（PostToolUse + Stop），rc=1，3 条规则全中（PC-EOL/PC-CONSOLE/PC-VAUTH）
→ 模型原样贴出门禁结论（证明 additionalContext 已回灌）
```

## 6. 能力定型

| 层 | 形态 | 状态 |
|---|---|---|
| 规则层 | CLI / IDE | ✅ skill + AGENTS.md（+ 可选 `~/.codebuddy/rules/`） |
| 编辑期 | CLI | ✅ **强制型**（原生钩子 + JSON 契约，2026-09-13 CLI 实测回灌成功） |
| 编辑期 | IDE 插件 | ⚠️ 未实测（插件版本回灌能力待验证，CLI 形态可用） |
| 提交期 | 工具无关 | ✅ 硬兜底（git pre-commit） |

> **形态说明**：CLI = 通过 `codebuddy -p` 命令行实测；IDE 插件 = CodeBuddy IDE 内的插件形态。
> 当前 ✅ 基于 CLI 实测，IDE 插件形态的回灌能力尚未验证。

## 7. 漏跑自查（2026-10-15 批次 4 起：心跳 / 跳过 / payload 捕获）

IDE 插件形态若疑似漏跑，两步定位（都读 `~/.guardrail-engine/`，不主动写盘）：

1. **心跳**：`hook-activity-codebuddy.json` 的 `last_ts` 是否在你那次编辑后更新？
   - 不更新 → **IDE 没调钩子**（形态/配置问题，回查 §2 配置是否生效）；
   - `last_decision=skip-no-file` → 钩子调了、但**取不到被编辑文件**（静默放行；看记录里的
     `tool` 字段区分：Read/Grep 类属正常噪音，Write/Edit 才是真漏）；
   - `last_decision=skip-out-of-tree` → 文件在 payload `cwd` 树外。★ 2026-10-15 批次 6 前，
     这正是 CodeBuddy IDE 的**常态**（其 `payload.cwd` 是**安装目录**、不是工作区，实测原文
     `D:\...\Programs\CodeBuddy CN`）；批次 6 起钩子改为**从文件路径反推仓根**，正常 Write/Edit
     应看到 `ran:rc=N`——仍见 skip 才需再查；
   - `last_decision=ran:rc=N` → 门禁真跑了（0=通过 / 1=拦截）。
2. **跳过记录**：写类事件提取失败会落一行 `hook-skips-YYYY-MM.jsonl`（60s/客户端节流；
   读类事件只心跳不落行；行内的 `tool` 字段同样用于区分噪音与真漏）。`ran:rc=1` 但模型手里
   没有结论 → 回灌问题（不是提取问题）。
3. **payload 捕获**（定案提取映射）：`touch ~/.guardrail-engine/hook-dump.on` → 在 IDE 里编辑一次
   → 看 `hook-payload-last-codebuddy.json`（长串截断、默认脱敏）→ 交给 harness 负责人修取数。
   用完删掉开关文件即恢复「完全不写盘」。

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
