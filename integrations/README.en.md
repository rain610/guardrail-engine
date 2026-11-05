# integrations/ —— Cross-Agent Integration Layer

> **Path note (must-read when sharing with colleagues)**: `<HARNESS>` in this document refers to guardrail-engine project root directory.
> Two options for value: ① Set environment variable `GUARDRAIL_ENGINE_DIR=<your path>` (legacy name `HARNESS_DIR` compatible); ② Execute `sh <HARNESS>/sandbox/where.sh` in project to print directly.
> Gate scripts internally all use relative paths to locate resources, **works when cloned to any directory**; only local editor/client hook configs need absolute paths.

> This directory is the "wiring layer for integrating core gates into each AI agent", separated from core checks/.
> Core layer (checks + run_gate + git hook) is agent-agnostic; here each agent family has its own wiring files.

## In Place (Local machine 2026-09-01)

- Universal entry: `harness/agents/auto-gate.py` — any agent runs one line in repo directory.
- Engineering map: `AGENTS.md` (repo root) — Claude Code / Codex / OpenCode etc. natively read it.
- Distribution skill: `guardrail-engine-skill/SKILL.md` — symlinked to
  `~/.skills-manager/skills/guardrail-engine`, then distributed to each agent's skills/ directory.

## Each Agent's Integration Method

>> Claude Code: `claude-code/README.md` — settings.json add PostToolUse hook calling auto-gate.
>> Codex:      `codex/README.md` — ~/.codex/AGENTS.md add one "run gate after code change", hooks optional by version.
>> OpenCode:   `opencode/README.md` — opencode.json add hooks or AGENTS.md instructions.
>> CodeBuddy:  `codebuddy/README.md` — relies on AGENTS.md / skill (current version has no native hook config).
>> Hermes:     `hermes/README.md` — skill + AGENTS.md map (Hermes reads AGENTS.md and skill).

## Layered Protection (One-line Overview for Agents)

1. **Enforcement layer (auto-effective, no agent cooperation needed)**: Business repo git pre-commit hook — any agent commit is blocked.
2. **Guidance layer (agent actively follows)**: Each agent's AGENTS.md / skill tells it "run auto-gate after code change".
3. **Tight loop (optional wiring)**: Agent's own post-edit hook — block while editing, real-time feedback.

## Maintenance

- Changed checks/ rules or this wiring file: commit with `[harness]`.
- Distribution skill moved (claude/codex/opencode/codebuddy/cursor skills/ symlinks):
  `~/skills-manager` under `guardrail-engine` -> this repo `integrations/guardrail-engine-skill` (legacy `agentguard` link still works).
