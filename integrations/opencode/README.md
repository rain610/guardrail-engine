# OpenCode 接入 guardrail-engine

OpenCode 读 `AGENTS.md`（全局 `~/.config/opencode/AGENTS.md` 注入 + 仓库根 AGENTS.md），
并支持 **plugins（TS）**——本仓提供一个现成插件，把 OpenCode 从「自觉制」升级为
**编辑期自动闭环**：AI 每次写完文件自动跑门禁，结论直接回灌给模型（与 Claude Code 的
PostToolUse 钩子同级）。

> 2026-09-13 实测：插件生效（`gate_rc=1`、模型收到结论并如实汇报），见 §3。

## 1. 必做（弱绑定）：AGENTS.md 指令

在 `~/.config/opencode/AGENTS.md`（或仓库根 AGENTS.md，已含）追加：

```md
## 门禁
改 AgentGuard 任意受管仓代码后，必须在仓库根跑：
python <HARNESS>/harness/agents/auto-gate.py
退出码 0=通过，1=拦截（修复后重跑，不要绕过），3=非受管仓库跳过。
```

## 2. 推荐（紧闭环）：装插件 —— 自动跑门禁 + 回灌模型

正本在仓里：`<HARNESS>/integrations/opencode/agentguard-gate.ts`
（本机只放一个**薄 shim**，避免两份实现漂移）：

```sh
# 1) 本机插件目录（没有就建）
mkdir -p ~/.config/opencode/plugins

# 2) 写 shim（只一行 re-export，换机器只改这里的路径）
cat > ~/.config/opencode/plugins/agentguard-gate.ts <<'EOF'
export { default } from "<HARNESS>/integrations/opencode/agentguard-gate.ts"
EOF
```

也可以直接把正本 `cp` 过去（代价是两份会漂移，**不推荐**）。

插件行为：

| 项 | 说明 |
|---|---|
| 触发点 | `tool.execute.after`——只对**真会写文件**的调用动手（write/edit/patch 类工具名，或 shell 命令命中写入特征：`Set-Content`/`>` `/`[System.IO.File]` 等，与 Codex 侧的判据同源）；读目录、搜索不触发 |
| 做什么 | 调用仓里的**正本** `integrations/hooks/agentguard-gate.py opencode PostToolUse`（与 Claude/Codex 同一份实现，单一来源） |
| 回灌 | 门禁未通过时把结论追加进该次工具调用的 `output`，模型看得到并应据此修复（与 Claude 的 exit 2 同效） |
| 放行策略 | 找不到 harness 工程 / 异常 / 超时 一律静默放行——**宁可不过门禁，也绝不卡住编辑器** |
| 兜底（默认关） | `HARNESS_OPENCODE_IDLE_CHECK=1` 时，回合结束（`session.idle`）再跑一次 `auto-gate.py`（覆盖"写文件判据没命中"的漏网改动） |

可调环境变量：`GUARDRAIL_ENGINE_DIR`（旧名 `HARNESS_DIR` 兼容） / `HARNESS_PYTHON` / `HARNESS_OPENCODE_IDLE_CHECK` / `HARNESS_OPENCODE_LOG`。

> **为什么插件要读 JSON 而不是退出码**：hook 自身的退出码恒为 2（表"阻断"），无法区分
> 「门禁未通过」与「门禁调用出错」。所以 `client=opencode` 这一支回一段
> `{"agentguard":{"gate_rc":…,"message":…}}`，插件用其中的 `gate_rc` 决定措辞。
> （这是本插件初版踩过的 bug：把 rc=2 误报成"harness 配置出错"。）

## 3. 验证（真跑，别只看配置）

```sh
sh <HARNESS>/sandbox/verify_integrations.sh <gate> opencode
```

脚本会：在受测仓建一个含 3 处违规的探针文件 → 让 OpenCode 自己写它 → 断言
①门禁日志新增该 gate 记录 ②输出里出现门禁结论 → 清理探针 → 比对 `git status` 回到基线。
