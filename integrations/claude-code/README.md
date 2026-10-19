# Claude Code 接入 agentguard

Claude Code 原生读 `AGENTS.md`（仓库根已有）+ 支持 `settings.json` hooks（边改边拦）。
若你的 `~/.claude/settings.json` 里**已有** hooks 段（例如 codebase-memory 之类工具装的钩子），**不要覆盖整个文件**，只并入新增条目；若还没有 hooks 段，直接新增即可。

## 1. 强绑定（推荐）：PostToolUse 钩子边改边拦

在 `settings.json` 的 `"hooks"` 里并入一个 `"PostToolUse"` 条目（若已有 PostToolUse，把本条目追加进列表）：

```jsonc
"hooks": {
    // ... 已有的其它钩子保留不动（没有就只留本条目）...
    "PostToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit|NotebookEdit",
        "hooks": [
          {
            "type": "command",
            "command": "python <HARNESS>/integrations/hooks/agentguard-gate.py claude",
            "timeout": 200
          }
        ]
      }
    ]
}
```

说明：
- 只在用户 isProject 打开某个端仓库时触发；command 在项目 cwd 运行，钩子脚本自动识别 gate。
- 钩子脚本**找不到 harness 工程就静默放行**（不阻塞任何人）；路径可用环境变量 `AGENTGUARD_DIR`（旧名 `HARNESS_DIR` 兼容）
  或指针文件 `~/.agentguard/harness.path`（内容=工程根）指定，实测两种都生效。
- 拦截时它以 exit 2 + stderr 把门禁问题回灌给模型（模型会接着改）；通过时不产生任何输出。
- 维护 skills 时 settings.json 常被官方工具重写，若丢失重加即可。

## 2. 弱绑定（必做，最低保障）

`~/.claude/CLAUDE.md`（全局）已由 skills-manager 分发 skill。若 agent 未识别 skill，
在全局 CLAUDE.md 加一行：

```
改 AgentGuard 任意客户×三端仓库代码后，必须在仓库根跑
python <HARNESS>/harness/agents/auto-gate.py 过门禁。
```

## 3. 验证

```
# 在某个端仓库根下
python <HARNESS>/harness/agents/auto-gate.py   # 应识别 gate 并跑门禁
```

## 6. Stop 事件：试过，**不能用**（2026-09-13 实测撤回）

原先的想法：`PostToolUse` 只查刚编辑的那个文件，那就再加一条 `Stop`（一轮结束时触发）跑全仓兜底。

**实测结论：加了 Stop 会把 PostToolUse 的回灌整个破坏掉。**

| 配置 | 端到端结果 | 留档里「门禁」出现次数 |
|---|---|---|
| 只有 PostToolUse | ✅ 门禁触发 + **结论回灌** | 2 |
| PostToolUse + Stop（exit 2） | ✅ 门禁触发、**回灌 = no** | **0** |

（两次都真跑了真实会话，其他条件不变；摘掉 Stop 立刻恢复 ✅。）

所以：**Claude Code 只挂 PostToolUse**。编辑期看不见的遗留问题（改了 A 却漏改必须同步的 B）
由**提交期兜底**接住 —— `git pre-commit` 是工具无关的，且不依赖模型配合。

> 这也是静态体检器的规则之一：claude 的关键事件**只要求 PostToolUse**，
> 见到多余的 Stop 会让人想起这段历史（体检器 `CLIENTS` 里 claude 那行的注释）。
