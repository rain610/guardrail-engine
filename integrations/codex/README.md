# Codex 接入 agentguard

> **路径说明（分享给同事时必读）**：本文中的 `<HARNESS>` 指 agentguard 工程根目录。
> 取值二选一：① 设环境变量 `AGENTGUARD_DIR=<你的路径>`（旧名 `HARNESS_DIR` 兼容）；② 在工程里执行 `sh <HARNESS>/sandbox/where.sh` 直接打印。
> 门禁脚本内部一律用相对路径定位资源，**clone 到任何目录都能跑**；只有本机的编辑器/客户端钩子配置需要写绝对路径。

Codex 读 `AGENTS.md`（全局 `~/.codex/AGENTS.md` 会注入，仓库根 AGENTS.md 在打开的工程内生效），
并支持 `~/.codex/hooks.json` 生命周期钩子（**2026-09-12 已在本机 Codex 0.154 实测跑通**，见 §2）。

## 1. 必做（最低保障）：AGENTS.md 加一条指令

在 `~/.codex/AGENTS.md` 或仓库根的 `AGENTS.md`（已含）里有一条：

```md
## 门禁
改 AgentGuard 任意客户×三端仓库代码后，必须在仓库根跑：
python <HARNESS>/harness/agents/auto-gate.py
退出码 0=通过，1=拦截（修复后重跑，不要 --no-verify 绕过），3=非受管仓库跳过。
```

## 2. 推荐（紧闭环）：hooks.json 边改边拦

Codex 从 `~/.codex/hooks.json` 读钩子（也支持 `config.toml` 内联 `[hooks]`）。在**已有的** `hooks` 对象里追两条：

```jsonc
{
  "hooks": {
    // ... 你已有的 PreToolUse / SessionStart 等条目保留不动 ...
    "PostToolUse": [
      {
        "matcher": "Bash|shell|powershell|apply_patch|Write|Edit|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "<PY> <HARNESS>/integrations/hooks/agentguard-gate.py codex PostToolUse",
            "timeout": 200,
            "statusMessage": "harness 门禁检查"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "<PY> <HARNESS>/integrations/hooks/agentguard-gate.py codex Stop",
            "timeout": 200
          }
        ]
      }
    ]
  }
}
```

`<PY>` 换成你的 python 可执行文件路径。**Windows 上命令必须写成"免引号 + 正斜杠"**——
实测带双引号的 Windows 路径会让钩子 spawn 失败（表现为 `hook: PostToolUse Failed`）。

实测要点（踩过的坑，照抄即可）：

| 坑 | 说明 |
|---|---|
| **钩子需要信任** | 非托管钩子要先审核：交互式 `codex` 里用 `/hooks` 信任一次；或在自动化里加 `--dangerously-bypass-hook-trust`（官方定位是"已自行审查过钩子来源的自动化"）。不加则钩子**不执行**。 |
| **Windows 工具名全是 `Bash`** | 命令内容其实是 PowerShell。钩子脚本因此按**命令特征**判断"是否真会写文件"，不是写操作就直接放行（否则读一次目录也跑门禁，会话白等几十秒）。 |
| **`Stop` 与 `PostToolUse` 的输出 schema 不同** | `PostToolUse` 可回 `hookSpecificOutput.additionalContext`（模型可见）；`Stop` 只支持 `continue/stopReason/systemMessage`——给 `Stop` 回 additionalContext 会被判 `hook run failed`。 |
| **改文件的路径可能是 shell** | Codex 常通过 shell 跑 `apply_patch`/重定向写文件，因此 `PostToolUse` 的 matcher 要带上 shell 类工具名。 |

## 3. 验证

```sh
# ① 弱绑定：任何端仓库根下
cd <你的某个端仓库>
python <HARNESS>/harness/agents/auto-gate.py     # 应识别 gate 并跑门禁

# ② 紧闭环：让 Codex 改一个必然违规的文件，应看到门禁输出被回灌
sh ~/.codex/codex-go.sh exec "在 src/ 下建个含 console.log 的 .vue，做完说明"
# （如未接 opencode-go，用你平时的 codex 启动方式）
```

---

## 实测：门禁触发可靠、结论回灌**不稳定**（2026-09-13，两次对照）

用 `sandbox/verify_integrations.sh <受测 gate> codex` 连跑两次，同一条探针、同一个模型
（deepseek-v4.1-flash via opencode-go），结果不同：

| 次 | 探针写入 | 钩子执行（留档可见） | 门禁日志 | 模型最终回答 |
|---|---|---|---|---|
| A | ✅ | `hook: PostToolUse` ×2 + `Stop` | rc=1，3 条规则全中 | **完整贴出门禁输出**（"❌ 发现 3 个阻断级问题"、exit code 1、甚至解释了 PowerShell 把 stderr 包装成报错）→ 回灌成功 |
| B | ✅ | `hook: PostToolUse` ×2 + `Stop` | rc=1（同） | **"没有任何 hook / 门禁 / lint / 拦截信息输出到终端"** → 回灌失败 |

两处留档：`%LOCALAPPDATA%\Temp\verify_out_codex.txt`（后一次覆盖前一次，A 次的关键片段见
`docs/2026-09-13_多客户端接入验证报告.md`）。

**结论（对使用的影响）**：
- **"门禁被触发"是可靠的**——日志每次都有 rc=1，规则也真命中；
- **"结论回到模型"不稳定**——所以 Codex 侧**不能只靠编辑期钩子**让 AI 自修；
  **提交期 `pre-commit` 兜底才是硬保证**（与工具无关，实测 rc=1 拦住提交）。

**可能原因（只是假设，未证）**：PostToolUse 的 `additionalContext` 是"注入下一轮上下文"，
而 `Stop` 走的是 `systemMessage`；不同工具（`apply_patch` 被沙箱拒 → 改走 PowerShell 直写）可能影响
Codex 是否把钩子输出显示在非交互（`-q`）会话里。要定性得多跑几次 + 抓 Codex 自己的日志。

**顺带证实两条环境事实**（留档里模型自己报的）：
1. `apply_patch` 在本机被沙箱拒（`Access is denied`，bat 位于 `C:\Users\HP\.codex\tmp\arg0\…`），
   模型改用 PowerShell 直写——这正是钩子 `WRITE_HINTS` 里维护那些 PowerShell 形态的原因；
2. 机器**没装 node** → PC 语法校验按设计**出声告警**（"未找到 node —— SYNTAX 规则本次已跳过，不会拦语法错（假绿风险）"），
   而不是静默通过。这条"宁可吵不可漏"的设计在真实会话里得到验证 ✅
