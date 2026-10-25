# Guardrail Engine

[![CI](https://github.com/rain610/guardrail-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/rain610/guardrail-engine/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](https://github.com/rain610/guardrail-engine)

> A zero-dependency, extensible code-gate engine — turn "conventions people must remember" into "checks machines enforce".

English | [简体中文](README.md)

> This project was formerly known as **AgentGuard** and was renamed to **Guardrail Engine** in v0.2.
> The legacy `agentguard` command, import package, and config directory keep working during the
> migration window (see [CHANGELOG](CHANGELOG.md)).

Guardrail Engine is a **general-purpose code-gate framework** that checks whether code follows team
conventions at edit time and commit time.

It is not a linter (ESLint / SonarQube-style analyzers). It is a **policy enforcement plane** that
decides whether a change may pass. Comparable mechanisms are Claude Code hooks / permissions and
Codex `execpolicy`.

It does not replace `AGENTS.md` either: `AGENTS.md` carries architectural intent and business
semantics (judgment for humans/models), while Guardrail Engine handles what can be **judged
mechanically**. The two are complementary.

## Why it exists

| Pain point | What Guardrail Engine does |
|---|---|
| Conventions live in docs; AI agents and developers forget them | Injects only ~2.4KB of always-on guidance (`~/.guardrail-engine/GLOBAL_AGENTS.md`, not the repo-root `AGENTS.md`) and loads details on demand |
| Rules only bind people who volunteer to follow them | Blocks at both edit time and commit time |
| Turning on rules drowns you in legacy debt | Two-state severity: new code is `error` (blocks), legacy is `info` (observed) |
| "Did the gate run?" is unanswerable | Every decision is recorded in a ledger with run summaries and rule coverage |
| Dependency cost of a new toolchain | **Zero third-party dependencies** — Python standard library only |

The engine layer does **not depend on any personal** skill or tool: a colleague can clone it or
`pip install` it and run immediately.

## Prerequisites

| Dependency | Minimum | Purpose |
|---|---|---|
| Python | **3.11+** | Engine runtime |
| Git | 2.x | diff / worktree detection |
| Bash (Git Bash on Windows) | — | Run `sandbox/*.sh` (demo / doctor / setup); PowerShell has no `sh` (a Python demo is provided) |
| Node.js (optional) | — | Only if a custom rule invokes `node`; the sample rules do not |

## Quick start

### 0. Install with pip (the wheel bundles the engine; no separate clone)

> **Before the first PyPI release**: the short command `pip install guardrail-engine` still returns
> 404. Publishing is handled by `release.yml` on `v*` tags (OIDC trusted publishing); until then,
> install from the source tree (`pip install .`) or from a locally built wheel.

```bash
git clone https://github.com/rain610/guardrail-engine.git
cd guardrail-engine
pip install .                    # install from source; the wheel bundles the engine
guardrail-engine init            # copy the bundled engine to ~/.guardrail-engine/engine and write ~/.guardrail-engine/harness.path
guardrail-engine quickstart      # self-check + print the MCP registration snippet
```

`init` only copies; it never deletes. If the target exists with different content it requires
`--force` and archives the old directory as `<dir>.disabled-<timestamp>` (reversible). Use `--dir`
to choose the install location. Afterwards, `guardrail-engine list` lists gates,
`guardrail-engine run <gate> <file>` checks specific files, and `guardrail-engine mcp` starts the
MCP server. In a source tree you can also run `python guardrail_engine/cli.py init`.

### 1. See it work from a clone (no configuration)

```bash
git clone https://github.com/rain610/guardrail-engine.git
cd guardrail-engine

sh sandbox/demo_gate.sh       # create a console.log in a temp dir -> block -> fix -> pass
python sandbox/demo_gate.py   # same, but no sh required (works in PowerShell / cmd)
sh sandbox/doctor.sh          # end-to-end self-check: probe / hook liveness / skill symlink
```

> **Windows needs Git Bash**: PowerShell and cmd do not ship `sh`. Run `python sandbox\demo_gate.py`
> directly for the demo, and run `doctor` / `setup` (`*.sh`) from Git Bash.

`demo_gate.sh` writes only to the system temp directory, touches no business repo, and needs no
configuration. It asserts both directions: the bad case must exit `rc=1` and name `NO-CONSOLE`, the
fixed case must exit `rc=0`; otherwise the script itself fails. "It ran without errors" therefore
means the gate really held, not that a demo script acted one out.

### 2. Wire it into your own repository

First create the machine-local config (git-ignored, affects only this machine):

```bash
sh sandbox/setup_machine.sh
```

The first run generates `config/harness.local.toml` from `config/harness.local.example.toml`, asks
you to fill in a path, and exits. Point the built-in `example` gate at your repository — **the key
name `example_repo` is declared in the shared config; the machine only fills the value**:

```toml
# config/harness.local.toml
[repos]
example_repo = "/path/to/your/project"
```

Then:

```bash
# Full scan
python harness/run_gate.py example --all

# Later, in a business repo, check only the current change (gate auto-detected)
cd /path/to/your/project
python /path/to/guardrail-engine/harness/agents/auto-gate.py

# Config health check: must exit 0
python /path/to/guardrail-engine/harness/run_gate.py --validate
```

> Do not add or rename `[repos]` keys in the machine-local config. Key names are team policy in the
> shared `config/harness.toml`; the machine only supplies paths. A missing key makes `--validate`
> exit `rc=2` — that is the only moment a typo or an unregistered key gets caught. To manage several
> repos, see "Adding a new gate".

### 3. Customize rules

The sample gate's rules live in the `EXAMPLE_RULES` list in `checks/example_ruleset.py`. Edit the
list; no new script is required:

```python
NO_TODO = RegexRule(
    id="NO-TODO",
    pattern=r"\bTODO\b",
    message="TODO comments are not allowed",
    fix_hint="Implement or delete the TODO",
)
```

Add it to `EXAMPLE_RULES`, then return to the Guardrail Engine root:

```bash
cd /path/to/guardrail-engine
python harness/run_gate.py example --all
python harness/run_gate.py --validate   # validates rule IDs and config keys
```

### 4. Install hooks (optional, recommended)

```bash
# Commit-time hook (run at the business repo root; the gate name is auto-detected)
cd /path/to/your/project
sh /path/to/guardrail-engine/sandbox/install_hooks.sh

# Edit-time hook (forwards post-edit events from AI clients)
python /path/to/guardrail-engine/sandbox/install_shim.py
```

## Sample rules

The project ships three sample rules in `checks/example_ruleset.py`:

| Rule ID | What it checks | Severity |
|---|---|---|
| `NO-CONSOLE` | Leftover `console.log` in code | block (warn inside string literals) |
| `NO-HARDIP` | Hard-coded IP addresses (each octet ≤255, version strings excluded) | block |
| `EOL-UNIFIED` | Mixed CRLF / LF line endings | block |

Run the sample gate:

```bash
python harness/run_gate.py example --repo <your-repo> --all
```

These rules are **heuristics**, not production parsers. Their limits are documented in the ruleset
header and printed by the self-check:

- Content inside comments is not a hit (`//`, `/* */`, `<!-- -->`, Python `#`);
- `console.log` inside a string only warns — it may be log copy or a documentation example;
- `1.2.3.4`-style version numbers are not treated as IPs (context such as `version=` / `v=`), and
  invalid IPv4 addresses are ignored;
- Continuation lines of multi-line block comments and multi-line strings are not tracked
  (single-line heuristic).

For stronger checks, subclass `Rule` from `checks/rule_base.py` and implement
`applicable()` / `check()`.

## Golden patterns: what to do after a block

A gate only answers "you cannot write it this way". A rule can also carry a `see` pointer that
delivers "write it this way" at the moment of the block:

```text
  ❌ src/logger.js:3 [NO-CONSOLE] console.log is not allowed (debug code must not be committed)
     FIX: Remove the console.log line and use the shared logger (see pattern)
     📖 See: golden-patterns/patterns/cross-cutting/logging.md
```

Golden patterns are more than a lint fix: they are a **task-oriented library of code skeletons**:

| What you are writing | Read |
|---|---|
| Request layer (token / response envelope / 401 / error messaging) | [`patterns/web/request-layer.md`](golden-patterns/patterns/web/request-layer.md) |
| List page (server pagination / filters / bulk actions / row actions) | [`patterns/web/list-page.md`](golden-patterns/patterns/web/list-page.md) |
| Form dialog (shared create-edit / validation / submit lock) | [`patterns/web/form-dialog.md`](golden-patterns/patterns/web/form-dialog.md) |
| Backend layering (Controller / Repository / soft delete / pagination) | [`patterns/backend/controller-repository.md`](golden-patterns/patterns/backend/controller-repository.md) |
| Validation and errors (400/404/500, do not swallow exceptions) | [`patterns/backend/validation-and-errors.md`](golden-patterns/patterns/backend/validation-and-errors.md) |
| Logging / configuration | [`logging.md`](golden-patterns/patterns/cross-cutting/logging.md), [`config-and-env.md`](golden-patterns/patterns/cross-cutting/config-and-env.md) |
| Build a business module from scratch | [`endtoend/task-management.md`](golden-patterns/endtoend/task-management.md) |

- Entry point: [`golden-patterns/index.md`](golden-patterns/index.md)
- Reference implementations: [`golden-patterns/reference/`](golden-patterns/reference/) (the positive
  samples run the sample gate as self-proof)
- Author your own pattern: [`golden-patterns/templates/00_范式制造模板.md`](golden-patterns/templates/00_范式制造模板.md)

Hooking a rule to a pattern takes two steps: put the **accepted** implementation under
`golden-patterns/`, and set `see = "golden-patterns/…"` on the rule class. Paths are always
repository-relative. Rules and patterns are reviewed together — a pattern is a reference frame, not
permission to bypass a rule.

## Architecture

```text
Client guidance layer   AGENTS.md / skill
       |
Tool layer              CLI / MCP / plugin
       |
Enforcement layer       edit-time hook + git pre-commit
       |
Decision engine         run_gate.py -> checks/*_check.py -> *_ruleset.py
       |
Ledger and health       reports/gate_log-<YYYY-MM>.jsonl / coverage / doctor
```

### Key directories

| Directory | Responsibility |
|---|---|
| `checks/` | Rule implementations and thin loaders |
| `harness/` | Dispatcher, config loading, hook entry points |
| `config/` | Gate and repository policy |
| `integrations/` | Per-client integration guides |
| `sandbox/` | Install, self-check, and packaging tools |
| `guardrail_engine/` | CLI / MCP package (`agentguard/` is a migration shim, removed in v0.3) |
| `tests/` | Unit tests |

## Rule model

The unified base class is `Rule` in `checks/rule_base.py`:

```python
class Rule:
    id: str
    name: str = ""
    severity_default: Severity = Severity.ERROR

    def applicable(self, ctx) -> bool:    # whether this rule applies to the file
        ...

    def check(self, ctx) -> list[Finding]:
        """Return violations"""
        ...

    def severity(self, ctx) -> Severity:  # two-state hook; defaults to severity_default
        ...
```

`RegexRule` in `checks/example_ruleset.py` is a regex adapter under the same protocol:

```python
RegexRule(id, pattern, message, fix_hint="",
          see="", string_severity=None, version_exempt=False, validator=None)
```

It runs through `Rule.run(ctx)` like a custom rule: `check(ctx)` returns `Finding` objects with
`Severity.ERROR / WARN / INFO`. When a single hit needs a different level (for example,
`console.log` inside a string only warns), assign `Finding.severity` directly; otherwise the
rule-level `severity_default` and the legacy-downgrade hook decide.

To narrow by file type or decide severity from context, subclass `rule_base.Rule` and override
`applicable()` / `check()`. `rule_base` also provides shared helpers for reading the staged index at
commit time (`read_bytes`), comment-span detection (`comment_spans`), and inline waivers
(`inline_waiver`).

### Rule types

| Type | Use case | Example |
|---|---|---|
| Single-line regex | Anti-pattern detection | `console.log`, hard-coded IPs |
| Whole-file rule | File-level constraints | Mixed line endings |
| `rule_base.Rule` subclass | File type / context / polymorphic severity | Custom `applicable()` + `severity()` |

## External analyzers (ESLint / Semgrep)

Built-in rules are **samples and a fallback**, not the primary analyzer. The engine layer is bound
by "zero third-party dependencies" and the standard library only offers Python's `ast`. Syntax and
type analysis for Vue / JS / TS / C# belongs to mature tools; Guardrail Engine owns the **policy
enforcement** layer: when to run, how to fail, how results reach the ledger, and how to waive.

Two adapter gates are registered but disabled by default (`enabled = false`):

```toml
[gates.eslint]
enabled = true                 # default false; enabling without the tool installed = rc=2 on every trigger
repo = "example_repo"          # points at a [repos] key; the local path lives in harness.local.toml
script = "checks/eslint_check.py"
run_on = ["post_edit"]         # deliberately not pre_commit (see "Known boundaries")

[gates.semgrep]
enabled = false
repo = "example_repo"
script = "checks/semgrep_check.py"
run_on = ["post_edit"]
```

```bash
python harness/run_gate.py eslint --all          # full scan: hand "." to the tool and let it traverse
python harness/run_gate.py eslint src/app.js     # targeted: only the named files
python harness/run_gate.py semgrep --all
```

Commands default to `eslint` / `semgrep` on PATH and can be overridden with environment variables
(a plain command or a JSON array):

```bash
export GUARDRAIL_ESLINT='["npx","eslint"]'
export GUARDRAIL_SEMGREP='["semgrep","scan"]'
export GUARDRAIL_ADAPTER_TIMEOUT=120          # seconds, default 300
```

Rule IDs from the external tool (such as `no-console` or
`python.lang.security.audit.eval`) work directly in `harness-ok:` inline waivers. Output format,
waiver syntax, and exit-code semantics match built-in rules; severity is mapped from the tool
(ESLint 1/2; Semgrep CRITICAL/ERROR/HIGH → block, MEDIUM/WARNING → warn, LOW/INFO → observe,
unknown → block).

**Fail-closed semantics** (these three boundaries are locked in
`tests/test_external_adapters.py`):

| Situation | Exit code | Why it cannot pass |
|---|---|---|
| Tool missing / cannot start / times out | `2` | No check was executed |
| Output is not JSON / tool exits with a config error (rc≥2) | `2` | The conclusion is untrustworthy |
| Semgrep reports non-empty `errors` | `2` | Some files were not scanned; results are incomplete |

`mode` / `legacy_baseline` / `disabled_rules` do **not** apply to adapter gates (the `--mode` flag is
accepted and ignored, and the ledger records no `mode_override`). Declaring them is rejected by
`--validate`: severity and rule selection belong to the tool's own config (`.eslintrc` /
`semgrep.yml`), and two switches would only fight each other.

**Known boundaries**

- External tools read **working-tree** files; the commit-time gate does not feed them the staged
  index. Adapter gates therefore **default to `post_edit` only**: under `pre_commit`, "stage the bad
  version + restore the good version in the working tree" bypasses them. For commit-time blocking,
  materialize a staged copy in the caller or accept working-tree semantics.
- External gates **do not participate in static rule coverage** (`--coverage` counts only built-in
  rules): the rule list belongs to the tool. Seeing "rules outside the inventory" after enabling a
  gate is expected.

## Configuration

Main config: `config/harness.toml` (read with the Python 3.11+ standard-library `tomllib`; zero
third-party dependencies).

```toml
[repos]
example_repo = ""      # key name in the shared config; the machine path goes in harness.local.toml

[gates.example]
enabled = true
repo = "example_repo"
script = "checks/example_code_check.py"
mode = "strict"           # strict | loose
run_on = ["pre_commit", "post_edit"]
line_ending = "auto"      # auto | CRLF | LF
encoding = "utf-8"        # backend repos often use utf-8-bom
legacy_baseline = false   # downgrade legacy hits to info
disabled_rules = []       # rule IDs disabled for this gate
```

The key referenced by `gates.<name>.repo` **must already exist** in `[repos]` (`harness_self` is
injected by the engine). The script named by `config.script` must exist. `--validate` checks both,
plus the value domains of `run_on` / `mode` / `line_ending`.

### Machine-local overlay

| Priority | Source | Purpose |
|---|---|---|
| 1 | Environment variable `HARNESS_CONFIG` | CI or unusual layouts |
| 2 | `config/harness.local.toml` | Machine-local repository paths |
| 3 | `config/harness.toml` | Shared baseline |

Legacy `config/harness.local.yaml` / `config/harness.yaml` are still read (compatibility layer), but
when both formats exist **TOML wins** and a notice is printed to stderr. New config should always be
TOML.

## Command reference

| Task | Command |
|---|---|
| List gates | `python harness/run_gate.py --list` |
| Check current changes | `python harness/agents/auto-gate.py` |
| Targeted check | `python harness/run_gate.py <gate> <file...>` |
| Full scan | `python harness/run_gate.py <gate> --all` |
| Config health check | `python harness/run_gate.py --validate` |
| 60-second demo (no sh) | `python sandbox/demo_gate.py` |
| Run summary | `python harness/run_gate.py --summary` |
| Coverage report | `python harness/run_gate.py --coverage` |
| Ledger verification + engine attribution | `python guardrail_engine/cli.py log --verify --engine` |
| Install commit hook | `sh sandbox/install_hooks.sh <gate>` |
| Install edit-time hook | `python sandbox/install_shim.py` |
| Integration self-check | `sh sandbox/doctor.sh` |
| Fast tests | `python sandbox/run_tests.py --fast` |
| Full tests | `python sandbox/run_tests.py --full` |
| Emergency bypass (recorded) | `python guardrail_engine/cli.py bypass --repo <repo> --reason "at least 10 characters"` |

> `bypass` is a **declared, recorded entry point, not a permission gate**: it blocks nothing and
> changes no gate decision.

## Exit codes

| Exit code | Meaning | Action |
|---|---|---|
| `0` | Pass | Continue |
| `1` | Gate blocked | Fix per the output |
| `2` | Config / invocation error (including a named target file that is missing or unreadable) | Check config and inputs |
| `3` | Unmanaged repository | Skip |

## Audit evidence strength

`sandbox/audit_reconcile.py` reconciles the ledger against commit history by **time window**, not by
content fingerprint. Its output is therefore **lead-level** evidence: it can show that the same gate
ran locally and passed around the time of a commit, but not that this commit's content was checked.

- Useful for human review and follow-up leads;
- **Do not** claim commits are self-certifying, and do not claim compliance with a mandatory audit
  regime.

The ledger also carries an **engine-content** fingerprint (see `Engine-Sha` below): it says which
gate code produced a decision. That is ledger attribution, not proof that a commit's content was
checked.

## Protection boundary (against AI edits to the gate)

The `[harness]` commit marker is a **change declaration**, not a permission boundary: anyone can
write it locally. The real enforcement lives in repository settings:

- `.github/CODEOWNERS` assigns `checks/`, `harness/`, `config/`, `sandbox/mutation_smoke.py`,
  `tests/test_lock_inventory.py`, `tests/lock-inventory.json`, and `.github/` to maintainers;
- In GitHub **Settings → Branches → Branch protection rules**, enable **Require a pull request
  before merging** and **Require review from Code Owners** for `main`. Committing CODEOWNERS alone
  only *requests* review; it does not block merges;
- This repo's commit-msg hook rejects commits that touch protected files without `[harness]` in the
  message. It stops accidents; someone determined to bypass it is not bound by it. The boundary is
  still CODEOWNERS + branch protection.

### Engine-content traceability (Engine-Sha)

`[harness]` says "the gate changed", not "what it changed to". Every ledger record carries
`engine_sha` (a 16-hex hash of the engine content), but you **must not** compare it directly with the
current engine: a normal upgrade, or even a one-line test change, moves the current value.

This repo uses **commit attribution**:

1. A `[harness]` commit appends `Engine-Sha: <16 hex>` to the message; the value comes from
   `python harness/run_gate.py --engine-sha` (read-only, works even with a broken config);
2. CI's `provenance` job recomputes the hash from the commit content and compares it with the
   trailer; a mismatch fails;
3. `python guardrail_engine/cli.py log --verify --engine` reconciles ledger `engine_sha` values
   against the set **declared in commit history**, starting at the first commit with a trailer.

What turns red is "a decision was produced by engine content that was never committed" — the shape
left by "weaken a rule → pass → restore → commit". Running the gate with an uncommitted engine during
development also lands in that list: commit that engine with a trailer, or rerun with a committed
engine. A failed reconciliation only affects this read command; it never blocks a gate.

## Extending

### Add a rule

1. Add a `RegexRule(...)` to `EXAMPLE_RULES` in `checks/example_ruleset.py` (for a custom ruleset,
   adjust the import in your check script)
2. Try it: `python harness/run_gate.py example --all`
3. Validate: `python harness/run_gate.py --validate` (rule IDs and config keys)

Need a ready-made encoding-convention ruleset? See "Optional ruleset: encoding hygiene"
([checks/encoding_ruleset.py](checks/encoding_ruleset.py)) — it is a library for your own check
script and registers no gate by default.

### Add a gate (multiple repos / independent ruleset)

Four places must change; missing one looks configured but is not:

1. **Shared** `config/harness.toml`, `[repos]`: add the key name (value `""`) — key names are team
   policy, independent of any machine
2. **Shared** `config/harness.toml`, `[gates.<name>]`: add the gate, point `repo =` at that key and
   `script =` at the check script. Copy `checks/example_code_check.py` and swap
   `from example_ruleset import ...` for your ruleset
3. `checks/registry.py`: register **both** `ENDPOINT_SCRIPTS` (endpoint name → script path) and
   `RULESETS` (script basename → ruleset module). Registering only the first leaves the gate
   runnable, but `--coverage` and the `harness_self` capability inventory cannot see the ruleset —
   a silent blind spot again. An unregistered gate falls back to `config.script`; `--validate` only
   warns (adapter gates are not in `RULESETS`; that is normal)
4. **Machine-local** `config/harness.local.toml`, `[repos]`: fill the path for that key (the only
   machine-specific step)

Then:

```bash
python harness/run_gate.py --validate      # must exit 0
python sandbox/run_tests.py --fast
```

### Optional ruleset: encoding hygiene (`checks/encoding_ruleset.py`)

This module is a **library for your own check script** and registers no gate by default: encoding
conventions are a per-repo fact (.NET repos often use `utf-8-bom`, frontend repos `utf-8`), and a
global default would flood the other kind of repo.

It addresses "AI writes following the wrong file's encoding": appending code to a legacy GBK
`Program.cs` produces mojibake in the diff. It has two levels: a changed or newly created file with
the wrong encoding blocks; legacy outliers in a full scan are reported only.

Wiring (follow the four steps in "Add a gate", swapping in this ruleset):

```python
import rule_base
from encoding_ruleset import rules_for

raw = rule_base.read_bytes(filepath)          # read via the index, do not open() directly
ctx = rule_base.CheckContext(path=..., rel=rel, raw=raw,
                             content=raw.decode("utf-8", errors="replace"),
                             repo=repo, mode=a.mode or "strict",
                             targeted=(scope == "targeted"))
for rule in rules_for("utf-8-bom"):           # take the value from your repo convention
    for f in rule.run(ctx):
        ...
```

Two easy-to-miss points: pass the original bytes as `raw=` (the rule judges bytes, not decoded
text), and pass `targeted=True` for targeted scans — without it `error_in_targeted` does not apply
and the rule tops out at `info` (installed but never blocking).

### Inline waivers

When a compliant construct is misreported, add a marker in a **comment** on the hit line or the line
immediately above:

```js
// harness-ok: NO-CONSOLE debug logging is gated behind a flag
if (DEBUG) console.log("debug info")
```

- Inline waiver: `harness-ok: <RULE-ID> <reason>` (applies to the sample gate)
- Markers inside string literals do not count; the rule ID must be exact and the reason is required.
- Rulesets share the same predicate through `rule_base.inline_waiver()`.

## Relationship to AGENTS.md

| Layer | Responsibility |
|---|---|
| `AGENTS.md` | Architectural intent, business semantics, red lines, human judgment |
| `Guardrail Engine` | Mechanically checkable rules, automatic triggering, ledger and acceptance |

Writing only `AGENTS.md` is cheapest but has no enforcement; adding Guardrail Engine gives you
mechanical blocking and an audit trail at edit and commit time.

## Supported AI clients

| Client | Integration |
|---|---|
| Claude Code | AGENTS.md + hooks |
| Codex | AGENTS.md + hooks |
| OpenCode | AGENTS.md |
| CodeBuddy | AGENTS.md |
| Trae CN | AGENTS.md |
| Qoder CN | AGENTS.md |
| ZCode | AGENTS.md |
| Other MCP hosts | MCP server |

## Tests

```bash
# Fast tests (edit loop; skips real packaging / install / git chains)
python sandbox/run_tests.py --fast

# Full tests (before commit / CI; includes real packaging / install / git chains)
python sandbox/run_tests.py --full

# Unit tests
python -m unittest discover -s tests
```

The release workflow (`.github/workflows/release.yml`) and the daily nightly
(`.github/workflows/nightly.yml`) additionally run three slow gates: mutation smoke (114 mutations
proving the locks really fail), real-artifact locks on a real package, and a clean-venv install that
runs `guardrail-engine --version` (the wheel bundles the engine; no checkout required). To reproduce
locally: run `python sandbox/pack_release.py`, then
`python tests/test_release_artifact.py TestRealArtifactPasses -v`; the venv command is the
"clean venv install smoke" step in `.github/workflows/release.yml`.

## Documentation

| Document | Content |
|---|---|
| `docs/packaging/INSTALL.md` | Install, upgrade, uninstall |
| `docs/packaging/RUNBOOK.md` | Day-to-day troubleshooting, rollback, reconciliation |
| `docs/packaging/COMPAT.md` | Client compatibility matrix |
| `docs/adr/` | Architecture decision records |
| `AGENTS.md` | Agent engineering map |

## License

MIT License — see [LICENSE](LICENSE).

## Contributing

Issues and pull requests are welcome.
