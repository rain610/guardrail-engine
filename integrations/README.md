# integrations/ —— 跨 Agent 集成层

> **路径说明（分享给同事时必读）**：本文中的 `<HARNESS>` 指 guardrail-engine 工程根目录。
> 取值二选一：① 设环境变量 `GUARDRAIL_ENGINE_DIR=<你的路径>`（旧名 `HARNESS_DIR` 兼容）；② 在工程里执行 `sh <HARNESS>/sandbox/where.sh` 直接打印。
> 门禁脚本内部一律用相对路径定位资源，**clone 到任何目录都能跑**；只有本机的编辑器/客户端钩子配置需要写绝对路径。

> 本目录是「把核心门禁接入各 AI agent」的接线层，与核心 checks/ 分离。
> 核心层（checks + run_gate + git hook）agent 无关；这里每一家 agent 各一份接线文件。

## 已就位（本机 2026-09-01）

- 通用入口：`harness/agents/auto-gate.py` —— 任何 agent 在仓库目录下跑一行即可。
- 工程地图：`AGENTS.md`（仓库根）—— Claude Code / Codex / OpenCode 等原生读它。
- 分发 skill：`guardrail-engine-skill/SKILL.md` —— 已软链进
  `~/.skills-manager/skills/guardrail-engine`，再分发到各 agent 的 skills/ 目录。

## 各 agent 的接入方式

>> Claude Code：`claude-code/README.md` —— settings.json 加 PostToolUse 钩子调用 auto-gate。
>> Codex：      `codex/README.md` —— ~/.codex/AGENTS.md 加一条「改码后跑门禁」，hooks 视版本可选。
>> OpenCode：   `opencode/README.md` —— opencode.json 加 hooks 或 AGENTS.md 指令。
>> CodeBuddy：  `codebuddy/README.md` —— 依赖 AGENTS.md / skill（当前版本无原生 hook 配置）。
>> Hermes：     `hermes/README.md` —— skill + AGENTS.md 地图（Hermes 读 AGENTS.md 与 skill）。

## 分层保障（给 Agent 的一句话总览）

1. **强制层（已自动生效，无需 agent 配合）**：端仓库 git pre-commit 钩子 —— 任何 agent commit 都被拦。
2. **指引层（agent 主动遵守）**：各 agent 的 AGENTS.md / skill 告诉它「改码后跑 auto-gate」。
3. **紧闭环（可选接线）**：agent 自身的 post-edit 钩子 —— 边改边拦，实时反馈。

## 维护

- 改了 checks/ 规则或此接线文件：commit 带 `[harness]`。
- 分发 skill 已动过（claude/codex/opencode/codebuddy/cursor 的 skills/ 软链）：
  `~/skills-manager` 下 `guardrail-engine` -> 本仓库 `integrations/guardrail-engine-skill`（旧 `agentguard` 链接仍可用）。