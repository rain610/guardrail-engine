# AgentGuard Offline Installation Package · Installation Instructions (v__VERSION__)

<!-- Repo single source: docs/packaging/INSTALL.md —— rendered into installation package by sandbox/pack_release.py (auto-replaces version / wheel name two types of placeholders), please don't modify body separately in package. -->

> This package is released under MIT License: free to use, modify and redistribute, retaining copyright and license notices (full text see `LICENSE` in package).

## Package Contents

- `__WHEEL__` (CLI + MCP thin service + **complete gate engine**: checks / harness / config / sandbox / golden-patterns)
- `marketplace/` (Claude Code / OpenClaw plugin marketplace bundle)
- `bootstrap.py` (Engine bootstrap: clones gate engine repo to local machine — **first step of integration**, see "Zero")
- `fix_paths.py` (Cross-machine path adaptation: one-click rewrite package config to your machine)
- `upgrade.py` (One-click upgrade: refresh copies installed on this machine with new package — dry-run by default, `--yes` to execute; **does no rollback**)
- `uninstall.py` (One-click uninstall: withdraws everything installed on this machine item by item — dry-run by default, `--yes` to execute)
- `OPENCLAW.md` (Complete OpenClaw integration steps)
- `LICENSE` (MIT License full text)
- `SHA1SUMS.txt` (Package file verification)

## Prerequisites

> ENC/Encoding note: When first integrating legacy repos, **files changed this time should be converted to conventional encoding** (
> unchanged legacy files don't churn, downgraded to INFO; files with non-conforming encoding that were changed are directly blocked) — see README capability boundary.

### Pre-check (run these three before installing, 30 seconds)

```bash
python -c "import sys; print('Python %d.%d.%d' % sys.version_info[:3]); assert sys.version_info >= (3, 11), 'Need Python 3.11+ (current version too low)'"
python -c "import shutil; print('git  :', shutil.which('git')  or '✗ not found')"
python -c "import shutil; print('node :', shutil.which('node') or '✗ not found')"
```

- **Python ≥ 3.11** (tested 3.11 / 3.12 / 3.13): Both this package and gate engine use it. Below 3.11 will
  **directly report human-readable error and exit** (exit code 2), won't let you hit incomprehensible AttributeError.
- **What the interpreter is called doesn't need your attention, but don't let it trip you up**: `python` name isn't everywhere — Ubuntu / WSL
  usually **only has `python3`**; conversely on Windows `python3` is often just Microsoft Store placeholder alias
  (`command -v` finds it, fails when run). So gate entry points **don't recognize names, only recognize if it runs**: in
  **`$HARNESS_PYTHON` → `python3` → `python` → `py -3`** order try each, take first one where
  `--version` exit code is 0 and output is `Python 3`; if none found then **fail-closed** (rc=2 + human-readable prompt),
  **not silent pass, nor misdiagnose "no interpreter installed" as "path wrong"**.
  When your interpreter name is special (conda environment, self-compiled), explicitly specify once — write into shell config for permanent effect:
  `export HARNESS_PYTHON=python3`.
- **git**: Commit-time gates required (pre-commit hook / changed file collection / tamper-evident self-check). Also has a **default-closed**
  fail-closed gear: Gates rely on `git diff` to judge "which lines this file changed this time", when unreadable downgrades per legacy —
  and "unreadable" has two causes, **exactly opposite**: ① file really didn't change this time (both diffs succeed, body empty) → downgrade is correct;
  ② git probe failed / git not installed / diff timeout → **never read successfully**, downgrade = silent relaxation (default gear only warns once on stderr).
  Set `HARNESS_GIT_REQUIRED=1` then: **when diff unreadable no longer downgrades per legacy**, 4 downgrade criteria take effect simultaneously.
  ⚠️ Unlike node, this gear **intentionally not default-on in pre-commit**: node one only affects "syntax check of files changed this time",
  when broken "not checked" is visible at a glance; while when diff sensor breaks it's **reverse** — whole-file caliber + no downgrade makes
  `cli.py check <some-file>` report that file's **historical** violations entirely as ❌, in repos where legacy not yet cleared equals completely blocking
  commits with zero information. So it's a gear for "manually turn on when suspecting sensor broken", not daily default.
  True non-git directories **don't trigger** this gear (`git rev-parse` explicitly answering "not a worktree" is legal input, distinguished from probe failure).
- **node**: **Required for gates enabling JS/TS syntax checks** — ⚠️ when local machine has no node **commits are blocked** (fail-closed,
  won't silently skip). Temporary bypass: `HARNESS_NODE_REQUIRED=0 git commit …` (not recommended long-term).
  **Rules that don't call node (pure Python rules, etc.) don't depend on node** ✓.
- Optional: AI clients (Claude Code / OpenClaw / CodeBuddy / Codex…); CLI only doesn't need.
- **No need to clone engine in advance**: Package's wheel comes with engine. After installing wheel run once
  `guardrail-engine init`, engine lands on local machine (hereafter `<HARNESS>`, default `~/.guardrail-engine/engine`) — see "Zero"

> **Unrelated to local machine layout, doesn't depend on any personal tools**: This package runtime only depends on **Python + git + repo paths on your machine** —
> doesn't need any personal skills or personal tools (`worktree-manager` / `huayu-manager` etc. are maintainer's own auxiliary
> skills, have them saves trouble, not having them is completely usable).
> Package's `.mcp.json` deliberately **leaves placeholders** (`__PY__` / `__MCP__` / `__HARNESS__`): changing machines must
> first run `python fix_paths.py` per installation instructions "Step 0" to fill in local machine paths. **This step cannot be skipped** (see below).

## Zero, Initialize Engine (wheel comes with it, one command)

Installation package's wheel **comes with complete engine** (rules, config, hooks, golden patterns), so no need to clone first.
After installing wheel run once `init`:

```bash
pip install "<this-package-directory>\__WHEEL__"
guardrail-engine init          # Copy to ~/.guardrail-engine/engine, and write ~/.guardrail-engine/harness.path
```

`init` **only copies, doesn't delete**: When target exists and content differs requires `--force`, old directory renamed-archive as
`<dir>.disabled-<timestamp>` (reversible); `--dir` can specify installation location. It runs once at end
`run_gate.py --list` self-check — seeing `self-check : list-gates rc=0` means engine really starts.

- `<HARNESS>` in entire text below refers to **engine directory from init** (default `~/.guardrail-engine/engine`).
- Engine directory is simultaneously where `guardrail_engine/mcp.py`, `harness/`, `checks/` are located, MCP registration and commit hooks
  both locate by it; changing directory only needs re-`init --dir` (pointer follows).
- Self-check (run after init, 3 seconds):

  ```bash
  guardrail-engine where -v
  sh <HARNESS>/sandbox/where.sh      # Print <HARNESS> actual location
  ```

- **What happens without init**: `guardrail-engine list` / `run` exit with rc=2 and explicit prompt `guardrail-engine init`;
  next section "Two" step 0 `fix_paths.py` validates whether `<HARNESS>/guardrail_engine/mcp.py` exists,
  if not directly exits and reports `✗ didn't find guardrail_engine/mcp.py under X`. Seeing this look back at this section first.
- **Want to use git to track engine updates** (dev / maintainer): Package still includes `bootstrap.py`, it clones engine repo to
  local machine — this is an **alternative** path, not installation prerequisite:

  ```bash
  python "<this-package-directory>\bootstrap.py" --dir D:/dev/guardrail-engine
  ```

  After running continue per commands it prints (path adaptation + generate overlay). Public source at
  `github.com/rain610/guardrail-engine`; internal/private mirror use `bootstrap.py --url <address>` to change source.

### Step 1: Fill repos with local machine paths (**not doing = local machine has zero managed repos**)

This is **easiest to miss after engine in place, and least noticeable when missed**, so listed separately.

Shared `config/harness.toml` **has no repo paths** (deliberately left empty since 2026-09-18): Whether you put repos on
`D:/` or `E:/`, what username, whether in WSL it's `/mnt/d/...`, are all **per-machine facts**, hardcoding in shared file
is wrong for others. So paths go in gitignored `config/harness.local.toml` (one per person).

But **key names don't need you to write**: Shared config's `[repos]` section already declares all managed repo keys (values all empty) —
"which repos are managed" is team policy, unrelated to machine. Those you haven't cloned on this machine, leave values empty,
not config error, don't need to delete, doesn't affect others.

> **Don't touch key names** (miss writing / misspell) — that's completely different from "leave values empty". Key name not in shared config = real config error:
> `--validate` blocks (rc=2), commit-time self-check reports ❌. This is intentional — that's "key name misspelled" and "new gate
> forgot to register" only time it speaks up.

**What happens when not filled**: Gates **don't** error interrupt, just **do nothing** — commit hooks run, judge, looks all normal,
but config has zero repo paths, so zero gate rules really execute. This is much harder to discover than errors, so engine makes it
**visible** — three places all say it:

```bash
python <HARNESS>/harness/run_gate.py --list        # Unconfigured gates show `@ (not configured)` one by one, prompt at end
python <HARNESS>/harness/run_gate.py --validate    # These gates show ⊘ and summarize (**don't block**: CI clean container is this form)
```

(Third place is runtime: commit hooks / each gate entry prints one `[config-warn]` summary. Three places say same thing,
wording differs only.)

Criterion: Gates in `--list` show **real paths**, not `(not configured)`.

**How to fill**:

```bash
sh <HARNESS>/sandbox/setup_machine.sh    # First time generates config/harness.local.toml and prompts you to edit, re-run after editing
```

When editing only write repos that **really exist** on your machine, write however many take effect however many (rest continue reporting not configured one by one);
Template `config/harness.local.example.toml`'s "leave values empty = local machine not managed" section has complete instructions.
Path writing: Windows write `D:/...` (forward slashes); **in WSL write WSL-perspective paths** (e.g., `/mnt/d/...`),
writing Windows paths in WSL all point to empty.

- After init there's one more thing to connect, not in this section: "Two" step 0 path adaptation (`fix_paths.py` fills **package-internal** placeholders).
  Complete narrative see repo `README.md` and `docs/packaging/RUNBOOK.md`.

> Don't confuse this section with "Two"'s **step 0**: This section manages **where engine comes from, where repos are**;
> that manages **package placeholders** — fill `.mcp.json` with local machine paths. Both things need doing, order is this section first, then that.

## One, Human CLI

```bash
pip install "<this-package-directory>\__WHEEL__"
guardrail-engine init           # Engine lands at ~/.guardrail-engine/engine (see "Zero")
guardrail-engine quickstart     # Self-check + usage guidance
guardrail-engine --help
```

## Two, Agent Integration

> **Step 0 (do once per install/upgrade): Path adaptation.** Package's marketplace `.mcp.json`
> has placeholders (`__PY__` / `__MCP__` / `__HARNESS__`) — plugin mechanism requires absolute paths, so
> when installing to local machine use this script to fill in **your local machine's** python and engine paths:
>
> ```bash
> python "<this-package-directory>\fix_paths.py" --harness "<your guardrail-engine path>"
> ```
>
> See `✅ adaptation complete`, and three arrow lines in output all point to local machine paths, then continue.
>
> **This step cannot be skipped** — including "in-place upgrade, machine hasn't changed" situation (what "in-place upgrade" means,
> where package should be extracted, see section five beginning): New package's `.mcp.json` has placeholders again
> (each package made fresh from template), missing this run leaves `__PY__` literal values, MCP service won't start,
> edit-time gates entirely fail. Benefit is here too: missing run is **immediate failure + visually distinguishable**, no longer "quietly using
> packaging machine's paths". Entire upgrade chain see section five.

- Claude Code:
  ```bash
  claude plugin marketplace add <this-package-directory>\marketplace
  claude plugin install agentguard@agentguard-tools
  claude plugin validate --strict <this-package-directory>\marketplace   # Self-check: expect 0 errors 0 warnings
  ```
- OpenClaw: See this package's `OPENCLAW.md` (also do step 0 first).
- Other MCP-supporting clients (general one line; paths subject to step 0 adaptation results):
  ```
  command = <python interpreter>
  args    = ["<HARNESS>/guardrail_engine/mcp.py"]
  ```
- Edit-time hook (**multiple clients share same entry point**; only with it do you get "check immediately after file change", having only commit-time fallback
  is also usable — this path is optional enhancement, but **belongs to installation surface, must withdraw together when uninstalling**; ⚠ only exception is third item
  below "global agents", it **deliberately not withdrawn**, reason in that section):

  ```bash
  python <HARNESS>/sandbox/install_shim.py           # Generate/refresh forwarder, pointer and global agents
  python <HARNESS>/sandbox/install_shim.py --check   # Health check: pointer → actual repo resolution chain (+ global agents status)
  ```

  Output **three items**:

  | Output | What it is |
  |---|---|
  | `~/.guardrail-engine/hooks/agentguard-gate.py` | Thin forwarder, points to **repo**, so engine update takes effect immediately |
  | `~/.guardrail-engine/harness.path` | Pointer (runtime chain prioritizes it) |
  | `~/.guardrail-engine/GLOBAL_AGENTS.md` | **Global agents**: Entry routing auto-loaded by each AI tool |

  First two items configure hook per each client's method, config locations see `integrations/<client>/README.md`.

  **Why third item (global agents) at this step**: It previously **had no generator** — only
  `sandbox/bootstrap_agent_env.sh` writes it, that script not in installation flow and has no callers,
  so after following this document, entry routing auto-loaded by each AI tool doesn't exist at all. Installing shim is **a step that inevitably runs**,
  so it renders it along the way.

  ⚠ It's **different** from other two items, two things to know:

  - **Sentinel partition**: In file between `<!-- HARNESS:BEGIN ... -->` and `<!-- HARNESS:END -->` is re-rendered by harness
    each run (rule changes written in reach those who already have this file), **manual edits in this interval are overwritten**;
    **outside interval is yours, harness never touches** — write local-machine-only rules there.
  - **Not withdrawn on uninstall** (see section four table): Precisely because outside interval belongs to you, "full machine exit" doesn't take
    your own rules away together. It stays in place continuing usable.

  > Incidentally: This path (edit-time hook) itself is **optional enhancement**, but global agents belongs here — meaning **those not installing edit-time
  > hooks don't auto-get global agents** (that's their choice, not blocked). Want only agents, not hooks, run separately
  > `python <HARNESS>/sandbox/global_agents.py <HARNESS>` (health check add `--check`).

## Three, Commit-Time Hard Fallback (optional)

Install commit interception for repo (unrelated to agent behavior, fallback enforcement):

```bash
git config core.hooksPath <HARNESS>/harness/hooks-dir
```

## Four, Uninstall (how to cleanly remove when not needed)

One command (recommended) — **dry-run by default, `--yes` to execute**:

```bash
python "<this-package-directory>\uninstall.py" --harness "<your guardrail-engine path>"          # Dry-run: lists each item to clean
python "<this-package-directory>\uninstall.py" --harness "<your guardrail-engine path>" --yes     # Real execution
```

It withdraws item by item per "what was installed" (auto-does what can be auto-done, prints ready commands for unreachable):

| What was installed (refer to "Two / Three") | How to withdraw | Method |
|---|---|---|
| Skill copies (`skill install`) | Rename-archive `<target>.disabled-<timestamp>` (reversible) | ✅ Auto (3 default hosts like `~/.claude/skills` etc.) |
| Commit-time hooks (`core.hooksPath`) | Add `--unhook <business-repo>`, withdraw once per repo (doesn't delete files) | ⚙️ Manual trigger per repo |
| Claude Code plugin | `plugin uninstall` + `marketplace remove` | ✅ Auto when `claude` CLI detected |
| OpenClaw extension | Move `extensions/agentguard` out to archive, **restart desktop to take effect** | ✅ Auto (refuses when characteristics don't match, prevents accidental deletion) |
| Human CLI (pip package) | `pip uninstall guardrail-engine` | ✅ Auto (different python add `--python`) |
| Edit-time shim (`install_shim.py`) | Two files rename-archive (reversible): `~/.guardrail-engine/hooks/agentguard-gate.py` and `harness.path` | ✅ Auto (forwards `install_shim.py --uninstall`; refuses when characteristics don't match, prevents accidental deletion) |
| Global agents (`~/.guardrail-engine/GLOBAL_AGENTS.md`) | **Not withdrawn** (see "Two": outside sentinel is rules you wrote, withdrawing equals taking them away together) | — Stays in place, still usable after exit |

> **Archive is "reversible", not "clean" (this wasn't written before)**: Rename-archive **doesn't delete any files**, so each uninstall permanently leaves
> a **complete copy** (skills are entire directory tree, shim is two files). Repeated "uninstall + reinstall" only grows, never shrinks.
> How to see it, how to clean:
> - `python <HARNESS>/guardrail_engine/cli.py skill status` lists skill debris (`<skill-name>.disabled-*`)
>   and counts; when confirmed unwanted, manually delete those directories (**gates don't delete for you** — they only archive).
> - Shim archives are `...agentguard-gate.py.disabled-<timestamp>` and `harness.path.disabled-<timestamp>`,
>   also under `~/.guardrail-engine/`, when confirmed unwanted manually delete (that directory has other things, this tool doesn't touch directory itself).

- Other clients with manually written MCP registrations: Go to respective configs and delete `guardrail-engine` entries (old config's `agentguard` key also works; `claude mcp remove` etc., see RUNBOOK).
- **Edit-time hook configs**: After shim archive, each client's hook config still writes that path → go to host configs and delete that hook.
  Only archiving files doesn't change configs, edit-time gates **silently fail** (same failure mode as when not installed).
- This tool **doesn't touch your `<HARNESS>` repo**; when installation package directory no longer needed, delete entire directory.
- Verification (all should show nothing): `pip show guardrail-engine` | `claude plugin list` | OpenClaw `plugins list` | `python <HARNESS>/guardrail_engine/cli.py skill status` (also reports debris) | `python <HARNESS>/sandbox/install_shim.py --check`.
  (Last command's output has one more line `[check] · global agents: generated` — that's **expected**: global agents per table above **deliberately not withdrawn**,
  and this line doesn't participate in command's success/failure judgment; when shim shows nothing it still exits with code 1 red.)
- Want to withdraw single item: Fine-grained commands (hooks / skills / MCP) see repo `docs/packaging/RUNBOOK.md` "4. Uninstall and Rollback".

## Five, Upgrade (replace old installation with new package)

### 5.0 First clarify what "in-place upgrade" means

"**In-place upgrade**" = **new package overwrites same fixed directory**, before and after upgrade "where package is" path **always unchanged**.
It's not "don't need to manage anything", nor "reinstall once" — it says **package directory location unchanged**, not
"installation action can be skipped".

**Recommended (default): Fixed directory.** Convention package directory as `%USERPROFILE%\guardrail-engine\`, inside directly placing
`fix_paths.py` / `marketplace/` / wheel etc. (**not** another layer with version-numbered directory). This way all `<this-package-directory>` in this document
always refer to same path, step 0 command each time **retype exactly as-is**, no need to remember
"where this version was extracted to".

> ⚠️ **One package reality**: After zip extracted has one layer with version number (`guardrail-engine-package-v<version>\`
> —— packaging script names by version, see `sandbox/pack_release.py`). So to land in fixed directory, needs one more step:
>
> ```
> First time: Extract → rename inner guardrail-engine-package-v<version>\ layer entirely to guardrail-engine\,
>         place under %USERPROFILE%
> Each time after: Extract → use that layer directory's **contents** to overwrite %USERPROFILE%\guardrail-engine\
>         (file explorer "replace files in target" is fine, what's overwritten is previous version's package, no loss)
> ```
>
> This step is indeed a bit roundabout. Want "extract is fixed directory, even this step saved" requires changing packaging script to let zip root directory
> not have version number — that's one packaging contract change (`verify_release.py` and multiple delivery locks all need to change),
> not in this document's scope, if needed find harness maintainer.

**Exception: Per-version directory.** Only use when needing **coexisting multiple versions / ability to rollback**: Extract to version-named new directory
(e.g., `%USERPROFILE%\agentguard-0.1.3-dev\`), each time tell host new path again (see table below
"changed directory" row). Cost is remembering new path each time, in exchange for coexistence and rollback.

> **Both layouts <u>don't need</u> running `uninstall.py` for upgrade.** It's for **exit** (leaving / changing machines /
> no longer using): after running your gates **close for a while**, commit hooks need per-repo `--unhook` then reinstall. Upgrade is
> "refresh copies", not "uninstall then reinstall".

### 5.1 One command (recommended)

**Recommended one command** (dry-run by default, `--yes` to execute; **does no rollback**, doesn't uninstall, doesn't delete files):

```bash
python "<this-package-directory>\upgrade.py" --harness "<your guardrail-engine path>"          # Dry-run: lists per-channel what will be done
python "<this-package-directory>\upgrade.py" --harness "<your guardrail-engine path>" --yes     # Real execution
```

It follows [1] path adaptation (cannot skip) → [2] Claude plugin → [3] OpenClaw extension → [4] pip package
→ [5] engine copy → [6] skill copy → [7] edit-time shim order **per-channel idempotent refresh**, and prints **before and after upgrade
local machine installation matrix** (engine / pip / plugin / skill each which version). Three hard rules: **only refreshes channels already installed**
(those not installed aren't installed for you, that's "installation"); **old files only listed for human judgment, not deleted for humans**; **repos all untouched**
(engine copy only recognizes ownership marker written by `guardrail-engine init`, old copy rename-archive is reversible).

Two pitfalls that speak up on the spot (both "looks upgraded, actually nothing changed"):
- **Same version number**: `claude plugin update` prints green "already latest" then **changes nothing**; pip channel same —
  `pip install -U` under same version number is **rc=0 and doesn't change a single byte** (pip original words: `is already installed
  with the same version as the provided wheel`). Script for both channels doesn't look at exit code, but **directly reconciles product after install**:
  if doesn't match directly says "this machine still running old content", and gives way out (go to engine repo bump `<HARNESS>/VERSION` repackage).
  Pip channel also **continues to run** `pip install --force-reinstall <this-package-wheel>` then reconcile again —
  tested same version number it really can change content, so script runs this step for you, and reports result;
  if even it can't change (permissions / index / blocked by other installation) then counts as real failure, then please manually intervene.
- **Missing path adaptation**: If copy's `.mcp.json` still has `__PY__` or points to non-existent local location, script names and reports it
  — this is exactly "edit-time gates entirely silently fail" scene.

> **Dry-run exit code now trustworthy** (fixed 2026-09-23, review L15): Dry-run branch and `--yes` branch **same criteria** —
> both sides read same failure list, has uncompleted items then **exit 1** and list each one.
> Previously dry-run branch threw away list it just accumulated, uniformly `return 0`, so "dry-run has ✗" and "dry-run passed" in exit code
> look identical, scripted calls (`if rc==0 then …`) would pass based on this.
> Still valid and **always consistent** one rule: **Prerequisites** failure (`--pack` points to wrong directory, package missing `fix_paths.py`)
> immediately `return 1`, subsequent channels all not done — because every one after builds on it.

## 5.2 Manual equivalent commands (when want to only refresh certain channel, or `upgrade.py` won't run)

First distinguish two integration types: **pointing to repo** (repo update takes effect, no need to touch); **installed copies** (per table below re-run installation commands to refresh).

| Integration Form | How to Upgrade |
|---|---|
| Engine copy (`<HARNESS>` from `guardrail-engine init`, default `~/.guardrail-engine/engine`) | `upgrade.py`'s [5/7] auto-refresh (source is this package wheel's built-in engine; after install byte-by-byte reconcile, no content change then init idempotent returns `engine already latest`, content changed then archive old directory then copy). Manual equivalent command: after installing new wheel run `guardrail-engine init --force`; content same then idempotent (`engine already latest`) |
| MCP registration (args point to `<HARNESS>/guardrail_engine/mcp.py`) | **No need to re-register** — path unchanged; engine content per previous line update |
| Git commit hooks (`core.hooksPath → <HARNESS>/harness/hooks-dir`) | **No need to reinstall** — path unchanged; engine content per previous line update (**don't** reinstall hooks for upgrade) |
| Edit-time shim (`~/.guardrail-engine/hooks/` and `harness.path`) | **No need to reinstall** — it only stores pointer; only when **changed machine / moved engine directory** re-run `python <HARNESS>/sandbox/install_shim.py` (health check: `--check`) |
| Global agents (`~/.guardrail-engine/GLOBAL_AGENTS.md`) | ⚠ **This one needs re-run to update** (different from previous): It's **rendered content**, not pointer. After engine repo's agents changes (`git pull` gets it), run once `python <HARNESS>/sandbox/install_shim.py` to re-render sentinel area; **outside sentinel area what you wrote yourself unaffected**. `--check` reports three items: status, whether sentinels complete, **and whether paths baked in sentinel area still current repo** (when moved engine repo but didn't re-run, it says it on the spot — only reminds, doesn't affect exit code) |
| Skill copies (`skill install` installed into host directory) | Re-run `python <HARNESS>/guardrail_engine/cli.py skill install --target <host>` (idempotent, incremental update) |
| Human CLI (pip installed wheel) | `pip install -U "<this-package-directory>\__WHEEL__"`, then engine copy auto-refreshed by `upgrade.py`'s [5/7] (manual equivalent: `guardrail-engine init --force`); **same version number** pip says `is already installed with the same version as the provided wheel` and **doesn't change a byte** (rc still 0), change to `pip install --force-reinstall "<this-package-directory>\__WHEEL__"` (`upgrade.py` reconciles product after install, when encountering same version number **runs this for you** then reconciles again) |
| Claude Code plugin | Fixed directory: `claude plugin marketplace update agentguard-tools` → `claude plugin update agentguard@agentguard-tools` → restart host<br>**Changed directory**: First step change to `claude plugin marketplace add "<new-package-directory>\marketplace"` — same-name marketplace silently **repoints** to new path, `plugin update` continues working<br>Neither needs `uninstall` / `marketplace remove` (same version number see 5.1 first pit) |
| OpenClaw extension | Reinstall with new package marketplace, or let `upgrade.py` in-place overwrite `extensions/agentguard` → restart desktop (complete steps see `OPENCLAW.md`) |

**Every package change must re-run "Two" step 0 `fix_paths.py`** — new package's plugin configs have placeholders again.
"My machine hasn't changed" doesn't constitute reason to skip: **what changed is package, not machine**.

> **Engine copy auto-refreshed by `upgrade.py`'s [5/7]**: Source is this package wheel's built-in engine (not via pip install state,
> `--skip pip` same applies); after install **byte-by-byte reconcile**, content unchanged then init idempotent returns `engine already latest`,
> content changed then archive old directory then copy. It only recognizes ownership marker written by `guardrail-engine init`
> (`<HARNESS>/.guardrail-engine-init.json`): When `--harness` is repo (has `.git`) or unknown directory **doesn't refresh** — running `init --force` on repo archives source. Manual equivalent command:
> `guardrail-engine init --force --dir "<HARNESS>"`.

Post-upgrade self-check: `python <HARNESS>/guardrail_engine/cli.py installed` (local machine installation matrix: which channels which version)
| `claude plugin validate --strict "<this-package-directory>\marketplace"` | OpenClaw `plugins list`
| `python <HARNESS>/sandbox/install_shim.py --check`.

Changed machine: First run `fix_paths.py` per "Two" step 0.

> Small suggestion: Future upgrades suggest first bumping number in `<HARNESS>/VERSION` then packaging (`python sandbox/pack_release.py` auto-includes version number naming),
> this way CLI / plugin upgrades go normal path, unupgraded machines visually distinguishable.
> Packaging script takes **last same-version** package's fingerprint (`PACK-INFO.json`'s `pack_sha1`, covering entire package content)
> and compares with this time: same version number but **different content** produces warning — that's exactly "colleague gets new package, `plugin update` but nothing changed"
> precursor, seeing it bump number repackage. Wheel fingerprinted by **content** (not file bytes), so simply re-running packaging once doesn't false-positive.

---

More background and troubleshooting: See repo `docs/packaging/RUNBOOK.md`.
