# Packaging Integration Runbook (guardrail-engine · recommended)

> Audience: Anyone who needs to integrate guardrail-engine gates on a machine / in a business repo (**no other documentation required**).
> One-page quick reference → Three entry points → Ledger → Bypass → Rollback → Troubleshooting → Known limitations → Evolution suggestions.
> Path convention: `<HARNESS>` = guardrail-engine engine root. Both sources work:
> ① After installing this package (`pip install guardrail-engine`, or source tree `pip install .` / local wheel) and running `guardrail-engine init`,
>    it's `~/.guardrail-engine/engine`;
> ② When cloning source, it's the project root (e.g., `D:/Rain/project/guardrail-engine`).
> In form ①, `python <HARNESS>/guardrail_engine/cli.py …` below can be replaced with `guardrail-engine …` (legacy `agentguard` alias still works during migration).

---

## 0. One-Page Quick Reference ("Install once and use")

```bash
# ⓪ When you only have pip package: first land the bundled engine locally (skip if cloning source)
pip install guardrail-engine && guardrail-engine init
#    For development / unreleased builds: pip install . && guardrail-engine init

# ① Self-check (3 seconds, any directory) — also prints all integration methods
python <HARNESS>/guardrail_engine/cli.py quickstart

# ② Make agent usable directly (MCP: one-line config)
python <HARNESS>/guardrail_engine/cli.py mcp --print-config
#    Claude Code:  claude mcp add guardrail-engine -- "<python>" "<HARNESS>/guardrail_engine/mcp.py"
#    OpenClaw:     openclaw mcp add guardrail-engine --command "<python>" --arg "<HARNESS>/guardrail_engine/mcp.py"
#    Codex:        codex mcp add guardrail-engine -- "<python>" "<HARNESS>/guardrail_engine/mcp.py"

# ③ Commit-time hard fallback (execute once per business repo)
python <HARNESS>/guardrail_engine/cli.py hooks install --repo <business-repo-path>

# ④ (Optional) Agent-side skill pack
python <HARNESS>/guardrail_engine/cli.py skill install --target claude   # or opencode / codebuddy

# ⑤ First judgment
python <HARNESS>/guardrail_engine/cli.py check <files-you-changed>
```

Result interpretation: `rc=0` pass; `rc=1` blocked (fix per ❌ in output); `rc=2` config/location error; `rc=3` unmanaged repository.

> Delivery self-check: when documentation/evidence shows garbled characters, use `python <HARNESS>/sandbox/check_mojibake.py` to scan (U+FFFD / PUA / double-encoding tokens; included with evidence since 2026-09-14).

---

## 1. Three Entry Points (Same Engine, Consistent Results)

| Entry Point | What to Use | Applicable Scenarios |
|---|---|---|
| **MCP Tools** | `harness_check_file` / `harness_run_gate` / `harness_list_gates` / `harness_summary` / `harness_coverage` / `harness_doctor` | When host has guardrail-engine configured, agent can call directly within session, no terminal needed |
| **CLI** | `python <HARNESS>/guardrail_engine/cli.py run/check/summary/...` | Manual terminal, scripts, CI |
| **Git Hooks** | `core.hooksPath` shared dispatcher (install see §0-③) | **Hard fallback**: even if agent side installs nothing, commits are still blocked |

> Three entry points share the same judgment kernel and same ledger; inconsistent results = bug, report directly to harness maintainer.

## 2. Judgment Ledger (Auditable)

- **Gate ledger**: `reports/gate_log.jsonl`. Each entry contains: `ts`(time) / `entry`(source: hook|cli|mcp) / `ver`(engine version) / `gate` / `scope` / `files` / `rc` / `ms` / `rules`(rule-level stats).
  - `skipped: true` = this run **executed no rules** (gate not enabled). Both it and "ran and passed" are `rc=0`; distinguish by this field (review v13 item 15).
  - `weakened: [...]` = this judgment was weakened by **permitted** means, only appears when it happens: `local_disabled` (local `config/harness.local.toml` turned off this gate) / `policy_override:gates.<gate>.<field>` (`HARNESS_ALLOW_POLICY_OVERRIDE=1` allowed policy override). A single `grep weakened reports/gate_log.jsonl` extracts all weakened judgments.
  - Both fields are **not written** during clean runs (hanging empty markers on every row equals no markers); `cli.py log` table's `mark` column renders them as `skip` / `weak:…`, `harness/run_gate.py --summary` also separately reports "skipped N times / weakened N times (which gates)".
- View: `cli.py log --last 50` (table) | Export: `cli.py log --export out.csv --format csv|json` | Aggregate: `cli.py summary` | Coverage: `cli.py coverage`.
- **Operations ledger**: `reports/ops_log.jsonl` (install/uninstall/bypass declarations, etc.), view: `cli.py ops`.
- **Write failure strategy**: Ledger write failure **does not** reverse-block gates (by design, logs never block commits); but "unparseable" line count is shown in `cli log` for troubleshooting.

### Local Reconciliation (Audit Trail, since 2026-09-14 / Rectification A1)

Use "commits × ledger" reconciliation to find local author commits that "have code changes but no gate pass record at commit time (within 15-minute window)":

```bash
python <HARNESS>/sandbox/audit_reconcile.py            # All business repos, last 14 days
python <HARNESS>/sandbox/audit_reconcile.py --since 7d --gate example
```

Output `reports/reconcile_<timestamp>.md`: Four categories (✅ has record / ◻ before ledger period / ⚠ no record / ⛔ has non-pass records) + **all managed repos** hook health check + `.git/config` modification time evidence.
"How many repos" is not hardcoded — that's **per-machine fact** (repo paths are in gitignored `config/harness.local.toml`, different for each person),
to see local list: `python <HARNESS>/harness/run_gate.py --list`.
Caliber reminder: output is **audit trail** not verdict (ledger write failure itself is silent); "before ledger period" / "config modified after commit" annotations distinguish historical commits during wiring transition period.

## 3. Emergency Bypass (Emergency only, must leave trace)

```bash
python <HARNESS>/guardrail_engine/cli.py bypass --repo <business-repo> --reason "≥10 character reason (why, for whom)"
git -C <business-repo> commit --no-verify -m "<commit message>"
```

- `bypass` writes "bypass declaration" to operations ledger and prints visible prompt (person/time/reason/actual command).
- ⚠️ **Characterization: `bypass` is "declaration trace entry", not permission gate** (review 2026-09-23 L19) — it doesn't block any commits,
  nor change gate judgment, any caller can declare it. All it buys is "visible, auditable" this one thing.
- Note: `--no-verify` itself cannot be intercepted by hooks; ledger records your **declaration**. Must explain to maintainer afterwards.
- Don't use bypass as substitute for fix; consecutive bypasses appear in ledger and can be audited.

## 4. Uninstall and Rollback

**Full machine exit (one-click)**: `python uninstall.py` in installation package (dry run) → add `--yes` (execute) — withdraws skill copies / commit-time hooks / Claude plugin / OpenClaw extension / pip package / edit-time shim item by item (对照 package's "Installation Instructions" section 4). Below are fine-grained commands per item:

```bash
# Commit-time hook: uninstall (core = withdraw core.hooksPath; doesn't delete any files)
python <HARNESS>/guardrail_engine/cli.py hooks uninstall --repo <business-repo>

# Skill pack: uninstall (rename-archive, reversible: <target>.disabled-<timestamp>)
python <HARNESS>/guardrail_engine/cli.py skill uninstall --target claude

# Edit-time shim: uninstall (two files rename-archive, reversible; 7 clients' edit-time gate entry points)
python <HARNESS>/sandbox/install_shim.py --uninstall

# MCP: remove on host side
#   claude mcp remove guardrail-engine | openclaw mcp unset guardrail-engine | codex mcp remove guardrail-engine

# OpenClaw extension: delete ~\.openclaw-autoclaw\extensions\agentguard → restart desktop (or uninstall.py auto-removes to archive)
```

**Cost of archiving** (don't just look at "reversible" two words): Rename-archive **doesn't delete files**, so each uninstall permanently leaves a complete copy
(skills are an entire directory tree). Repeated "uninstall + reinstall" only grows, never shrinks — `cli.py skill status` lists debris paths and counts,
manually delete `<skill>.disabled-*` when confirmed unwanted (gates only archive, don't delete for you).
**Edit-time hook config needs separate deletion**: After shim archive, client hooks still point to that path → go to each host config and delete that hook
(`integrations/<client>/README.md`), otherwise edit-time gates silently fail (same failure mode as when not installed).

Rollback verification: After uninstall, `git commit` should immediately restore to normal (no longer triggers gates); `hooks status` should show "not set".
Residual check: If `.git/hooks/pre-commit` still exists (old copy mode residual), tool won't delete on your behalf, please handle after manual confirmation.

## 5. Troubleshooting (Symptom → Cause → Handling)

| Symptom | Cause | Handling |
|---|---|---|
| Commit "seems not checked", no output | hooksPath not set / points to non-existent directory (git silently skips) | `git config core.hooksPath` self-check; `cli.py hooks install --repo <repo>`; refer to README.md's three-state description |
| Commit blocked but don't understand | Normal blocking | Fix per output ❌; use `cli.py log` to see this judgment's rule details |
| rc=2 config/location error | Gate registration or path issue | `python <HARNESS>/harness/run_gate.py --validate` |
| MCP tools don't appear in host | Config not effective / command wrong | `openclaw mcp probe guardrail-engine` / `claude mcp list` / `codex mcp list` self-check; check service stderr |
| Console garbled/crash | GBK console | All three shells have UTF-8 fallback; other entries see `doctor` section 1 check |
| Documentation/evidence shows garbled characters ( / 锟斤拷 type) | Collection pipeline encoding damage (PS pipeline decodes as cp936 / Windows CLI GBK output collected as UTF-8) | During collection: python writes UTF-8 files directly, CLI output cp936 fallback decoding; before commit: `python sandbox/check_mojibake.py` self-check (this line's example characters are for illustration, mojibake-ok) |
| hooks report `python: command not found` | No python in PATH | Add python to PATH; or use `cli.py` direct run to verify before installing hooks |
| Suspected false positive | Rule boundary | Prefer "inline waiver" (`harness-ok: <rule-ID> <reason>`, see skill pack "Inline Waiver" section); rule truly not applicable to this repo → report to maintainer to change `disabled_rules`, **don't** hard-add markers |

## 6. Plugin Form (Claude Code / OpenClaw, optional)

Plugins are the third-layer integration form (installation carrier). Claude-compatible marketplace (`agentguard-tools`, including `agentguard`)
has completed **dual-host live testing** on dev machine: Claude Code strict validation 0 errors and installed enabled; OpenClaw completed installation per bundle (skill + MCP; agent session tested directly callable). **See `OPENCLAW.md` for complete OpenClaw steps.**

Build (re-stamp when changing machines):

```bash
python <HARNESS>/integrations/plugins/build_marketplace.py --harness <HARNESS> --out <DIR>
```

Entire offline installation package (wheel + marketplace + installation instructions + SHA1SUMS + zip) one-click rebuild:

```bash
python <HARNESS>/sandbox/pack_release.py
```

Integration (choose one per host):

```bash
# Claude Code
claude plugin marketplace add <DIR>
claude plugin install agentguard@agentguard-tools

# OpenClaw
openclaw plugins marketplace list <DIR>
node <openclaw.mjs> plugins install agentguard --marketplace <MKT>   # Complete steps/pitfalls: OPENCLAW.md
```

Upgrade (after getting new package): Integrations pointing to repo (MCP / git hooks / edit-time shim) **need no handling**; CLI / plugin /
skill copies, etc. re-run corresponding install commands per `docs/packaging/INSTALL.md` "Section 5, Upgrade" ("in-place upgrade" means
**package directory location unchanged**, not "installation action can be skipped" — every package change requires re-running `fix_paths.py` once).
**Don't run `uninstall.py` for upgrade**: That's for "exit", after running gates will be closed for a while, commit hooks need per-repo reinstall.

Remove (reversible):

```bash
claude plugin uninstall agentguard
claude plugin marketplace remove agentguard-tools
```

> Build artifacts contain local absolute paths, not in repo (repo only stores templates + builder).
>
> Platform boundary: On managed runtimes (some desktop hosts), host plugin installation needs **human execution** — agent initiation is blocked by write permission boundary.

## 7. Known Limitations (Honest Version)

- pip package form **provided** (`pyproject.toml` + `[project.scripts] guardrail-engine`), and published to
  PyPI: `pip install guardrail-engine` works. Release is completed by `release.yml` triggered by a `v*` tag (see §7.5).
- Claude plugin prototype stamped per machine (config contains absolute paths), host marketplace distribution form not verified (see `COMPAT.md`).
- `bypass` only records "declaration", cannot force-intercept `--no-verify` (requires hook-side cooperation, future increment).
- MCP/CLI **persistent regression tests in repo** (`tests/engine/test_agentguard.py`, 10 cases: protocol fixtures / tool behavior / three-entry consistency).
- Ledger write failure **doesn't block** gates (by design, logs never block commits); since 2026-09-16 prints one line
  `[run_gate] ⚠ judgment ledger write failed…` on stderr, no longer completely silent (previously only had one `except: pass`, write failure unknown to anyone).

## 7.5 Release (Maintainer): tag → GitHub Release → PyPI

Release action is only one: align `VERSION` and `CHANGELOG.md` on `main` then tag.

```bash
# 1) VERSION and CHANGELOG in same commit: move [Unreleased] content into ## [<version>] — <date>
# 2) Tag and push (release.yml validates tag ↔ VERSION consistency, mismatch fails directly)
git tag v0.2.0
git push origin v0.2.0
```

`release.yml` has three stages:

- `build`: Unit tests → mutation smoke → `python -m build` (sdist + wheel) →
  `pack_release.py --wheel` uses **same round** wheel to assemble offline installation package → real artifact lock →
  upload `release-dist` artifact;
- `release`: Download `release-dist`, re-verify then create GitHub Release (with same wheel/sdist +
  internal installation package zip);
- `publish`: Download **same** `release-dist`, clean venv smoke then upload PyPI.

`release` / `publish` **don't rebuild**; GitHub Release and PyPI have the same wheel/sdist.

PyPI `publish` job uses OIDC short-lived credentials, repo **doesn't need** `PYPI_API_TOKEN`. Before first release
need to register trusted publisher once on PyPI web interface:

| Field | Value |
|---|---|
| PyPI project | `guardrail-engine` (create same-name project on PyPI before first release, or let first upload auto-create — need to register pending publisher on that project's Publishing page) |
| Owner | `rain610` |
| Repository | `guardrail-engine` |
| Workflow | `release.yml` |
| Environment | `pypi` |

Repo-side corresponding settings: GitHub → Settings → Environments create new `pypi` (can add required reviewers as needed,
manual confirmation before release). `build` job passes `python -m build` first builds sdist, **then from sdist** builds wheel,
this step exposes `MANIFEST.in` missing engine issue; `publish` only downloads same artifact, does
`twine check` and clean venv `init` smoke, doesn't rebuild.

Failure handling:

- `tag and VERSION mismatch`: Delete tag (`git tag -d` / `git push origin :refs/tags/<tag>`) then re-tag;
- PyPI reports `File already exists`: Same version cannot re-upload, bump `VERSION` then release;
- `publish` fails after `gh release create`: GitHub Release already created, but PyPI doesn't have this version —
  after fix use `workflow_dispatch` (if enabled) or add new tag to re-run; don't manually `twine upload`
  bypass smoke.

## 8. Future Evolution Suggestions

1. ~~Add `tests/engine/test_agentguard.py`~~ — **Completed (2026-09-16 in repo, 10 cases)**.
2. ~~M1: `pyproject` + `agentguard` global command~~ — **Completed** (`pyproject.toml` + `[project.scripts]`, maintains zero dependencies);
   Remaining: M3 skill version alignment and more host installers; M4 plugin marketplace form.
3. OBS-1 line-ending policy (`.gitattributes` script class `eol=lf`) and CI `workflow_dispatch` first run (see review v7 §4).
4. Ledger can add "rule version → change date" mapping for cross-version comparison (extension of review v6 Q2).
