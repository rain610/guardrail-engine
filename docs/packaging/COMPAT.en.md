# Client Integration Compatibility Matrix (guardrail-engine · recommended)

> Verification levels: **✅ Verified (tested this round)** | **◐ Partially verified (see notes)** | **✗ Not verified (not assumed working by default)**.
> Test environment: Windows 11 · Git-for-Windows (MSYS bash) · Python 3.13 · 2026-09-14.
> Original evidence: `evidence/client_registrations.txt`, `evidence/verify_entries.txt` (this delivery package).

## 1. MCP Entry (`guardrail_engine/mcp.py` · single file · zero third-party dependencies)

| Client | Integration | Status | Verification / Notes |
|---|---|---|---|
| Any MCP host (protocol layer) | Configure `command` + `args` | ✅ | Strict handshake (initialize → initialized → tools/list → tools/call), 6-tool catalog, error paths (-32601/-32602), stdout protocol purity; fixture end-to-end rc=1/BLOCKED |
| Claude Code 2.1.269 | `claude mcp add -s user` | ✅ | `Added stdio MCP server agentguard ... to user config`; visible in `claude mcp list` |
| Claude Code plugin form | `.claude-plugin/plugin.json` + `.mcp.json` | ✅ (validation level) | `claude plugin validate --strict`: **0 errors / 0 warnings**; end-to-end self-certification PASS; session-level loading (`--plugin-dir`) verified, marketplace distribution ◐ (not verified this round) |
| OpenClaw 2026.6.8 | Plugin bundle (`plugins install`, marketplace source) | ✅ | 2026-09-14 10:17 install (user-side execution); inspect: Format: bundle / capabilities: skills, mcpServers; skill `agentguard` ready; doctor clean; agent session tested callable (AVAILABLE) |
| Codex 0.154.0 | `codex mcp add` | ✅ | `Added global MCP server 'agentguard'`; visible in `codex mcp list` |

**Registration command transcripts (reusable / reversible)**. Service name unified as `guardrail-engine`; legacy key `agentguard` remains readable during migration:

```bash
# OpenClaw (auto-probe before save)
openclaw mcp add guardrail-engine --command "<py>" --arg "<HARNESS>/guardrail_engine/mcp.py" \
  --env GUARDRAIL_ENGINE_DIR=<HARNESS forward-slash form>
# Remove: openclaw mcp unset guardrail-engine

# Claude Code (user-level; note -e is variable, don't pass multiple bare words during registration)
claude mcp add -s user guardrail-engine -- "<py>" "<HARNESS>/guardrail_engine/mcp.py"
# Remove: claude mcp remove -s user guardrail-engine

# Codex
codex mcp add guardrail-engine --env GUARDRAIL_ENGINE_DIR=<HARNESS forward-slash form> \
  -- "<py>" "<HARNESS>/guardrail_engine/mcp.py"
# Remove: codex mcp remove guardrail-engine
```

## 2. CLI Entry (`guardrail_engine/cli.py`)

| Environment | Status | Notes |
|---|---|---|
| Windows 11 + PowerShell / cmd | ✅ | All subcommands tested (run/check/log/ops/skill/hooks/bypass/setup/quickstart) |
| Git-for-Windows bash | ✅ | Hook install/uninstall, doctor forwarding tested |
| Ubuntu CI / Linux | ✅ | Code is all stdlib, path handling has cross-drive fallback; **CI first run both green** (py3.11 51s / py3.12 53s; run 34796814674, 2026-09-14) |
| macOS | ✗ | Not verified |

## 3. Git Commit Hooks (hard fallback, `core.hooksPath` shared dispatcher)

| Scenario | Status | Notes |
|---|---|---|
| Windows business repo: violation blocking | ✅ | Real git fixture: commit(bad) rc=1 + fix guidance; commit(good) rc=0 |
| Windows business repo: one-click install/uninstall/rollback | ✅ | install → hooksPath effective; uninstall → normal commit restored (no files deleted) |
| Unmanaged repo fail-closed | ✅ | Existing capability (tested in prior rounds; no regression this round) |
| Ubuntu / no bash environment | ◐ | Hooks depend on bash; CLI reports explicit error on Windows without bash (not silent pass) |

## 4. Skill Pack (`integrations/guardrail-engine-skill`)

| Host | Status | Notes |
|---|---|---|
| claude / opencode / codebuddy directories | ✅ | Installer tested in isolated HOME (install/status/uninstall-rename-archive) |
| "Skill loaded and executed" within host | ◐ | Three local directories in place (doctor existing check passes); full behavior loop pending host-side manual confirmation |

## 5. Other Environment Notes

- Console encoding: all three shells have UTF-8 fallback (`list_entries --check` covers, 36 entries all green).
- When `python` not in PATH: hook chain fail-closed with error (not silent pass); see RUNBOOK §5 for handling.
- This matrix updates with each verification round; new client verifications同步 update this file and delivery report.
