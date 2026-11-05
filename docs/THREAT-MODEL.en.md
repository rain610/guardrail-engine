# Threat Model & Trust Boundaries

> Security boundaries, threat classification, and design trade-offs of Guardrail Engine.
> This document is a P0 deliverable before open-sourcing, addressing review suggestion #6.

---

## 1. System Boundaries

```
┌─────────────────────────────────────────────────────────┐
│  Engine Repo (guardrail-engine)                         │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────────┐ │
│  │ checks/  │  │ harness/ │  │ guardrail_engine/     │ │
│  │ Rules    │  │ Scheduler│  │ CLI + MCP + Ledger    │ │
│  └──────────┘  └──────────┘  └───────────────────────┘ │
│  ┌──────────────────────────────────────────────────┐   │
│  │ protected-patterns.txt  (single source of truth) │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
         │ install/reference    │ hook copy
         ▼                    ▼
┌─────────────────────────────────────────────────────────┐
│  Business Repo (managed repository)                     │
│  ┌────────────┐  ┌────────────┐  ┌──────────────────┐  │
│  │ .git/hooks/│  │ Business   │  │ AGENTS.md /      │  │
│  │ pre-commit │  │ code       │  │ inline waiver    │  │
│  │ commit-msg │  │            │  │ (harness-ok:)    │  │
│  └────────────┘  └────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│  Ledger                                                 │
│  .guardrail/ledger.jsonl                                │
│  - Each gate run produces one row                       │
│  - Row-chain hashing ensures sequence integrity         │
│  - engine_sha attributes to engine version              │
│  - anchor SHA256 anchors the first row                  │
└─────────────────────────────────────────────────────────┘
```

### Trust Anchors

| Anchor | Location | Purpose |
|------|------|------|
| `engine_sha` | Every ledger row + init marker | Attributes to engine code snapshot; `log --verify --engine` recalculates and compares |
| `protected-patterns.txt` | Engine Repo root | Single source of truth for protected file surface; both `harness_guard` and `commit-msg` read it |
| `harness_guard` hook | Engine Repo `.git/hooks/` | Blocks "weaken gates → don't commit → commit business code" attack |
| row-chain hash | `prev_hash` field in every ledger row | Chain hashing; tampering with any row breaks the chain |
| anchor SHA256 | First row of ledger | Anchors the starting point of the entire chain |

---

## 2. Threat Actors

### 2.1 Accidental

**Scenario**: AI agent writes code that violates rules (hardcoded IP, leftover console.log, mixed line endings).

**Mitigation**:
- pre-commit hook automatically blocks; agent must fix before committing.
- Rule output points to golden patterns (`📖 See: golden-patterns/...`); agent can self-correct.
- Inline waiver (`harness-ok: RULE_ID reason`) requires explicit declaration to avoid silent pass.

**Residual risk**: Agent may write bad code not covered by rules. This is a rule set thickness issue, not an engine defect.

### 2.2 Careless

**Scenario**: Developer uses `git commit --no-verify` to skip gates.

**Mitigation**:
- Ledger records every gate run; `--verify` can audit which commits didn't run gates.
- CI layer can be configured to "reject merges without ledger credentials" (requires team policy).

**Residual risk**: **`--no-verify` is a git-level escape hatch that cannot be blocked at the client side.** This is a design trade-off, not a vulnerability. The threat model assumes committers have physical write access; the engine's responsibility is *post-hoc detection* rather than *physical prevention*.

### 2.3 Malicious

#### 2.3.1 Weakening Gate Scripts

**Attack**: Modify `checks/*.py` or `harness/*.py` to disable rules without committing changes, then commit business code.

**Mitigation**:
- `harness_guard` detects whether Engine Repo has uncommitted protected file changes; blocks business repo commits if so.
- Protected file surface defined by `protected-patterns.txt`, covering all rule implementations and scheduler.
- Engine Repo's own `commit-msg` hook requires `[harness]` marker + `Engine-Sha` trailer.

**Residual risk**: If attacker can directly modify Engine Repo's `.git/hooks/harness_guard` itself, they can disable the protection loop. This requires local write access + understanding of hook mechanism. Mitigation: hook scripts themselves are in `protected-patterns.txt` (`^harness/hooks-dir/(pre-commit|commit-msg)$`), but copies under `.git/hooks/` are not git-tracked — this is an inherent limitation of the git hook mechanism.

#### 2.3.2 Tampering with Ledger

**Attack**: Directly edit `.guardrail/ledger.jsonl` to delete or modify historical records.

**Mitigation**:
- row-chain hashing: each row contains the previous row's hash; tampering with any row breaks the chain.
- `log --verify` recalculates the entire chain and alerts on inconsistency.
- anchor SHA256 anchors the first row, preventing "delete old rows → rebuild chain" attack.

**Residual risk**: Attacker can *delete the entire ledger file* and start from scratch. This requires also deleting hook copies under `.git/hooks/` to persist. Mitigation: CI layer can require ledger files to be append-only (git diff check that `ledger.jsonl` has zero deleted lines).

#### 2.3.3 Replacing Installation Copy

**Attack**: Replace engine files after `pip install` or `git archive` but before `init`.

**Mitigation**:
- `engine_sha` recorded at init is a snapshot fingerprint of engine code.
- `log --verify --engine` recalculates current engine file SHA and compares with ledger attribution.
- Users can manually verify `engine_sha` against known values from release tags after init.

**Residual risk**: Engine cannot self-certify "installation copy has not been tampered" — this is the trust anchor starting point issue. Mitigation: publish `engine_sha` signed manifest with GitHub Releases; users compare after init.

---

## 3. Tamper-Evident ≠ Tamper-Proof

**Core design trade-off**:

| Property | Meaning | Does Guardrail Engine achieve it? |
|------|------|------------------------|
| Tamper-proof | Physically prevent tampering | ❌ Cannot (committers have local write access) |
| Tamper-evident | Detect tampering after the fact | ✅ Row-chain hashing + engine_sha attribution + anchor |
| Tamper-resistant | Increase difficulty of tampering | ✅ harness_guard protection loop + protected file surface |

**Why not tamper-proof?**

- Gate tools run in the local environment of managed repositories; committers have full control over the file system.
- No local tool can prevent `rm -rf .git/hooks/` or binary replacement.
- Attempting tamper-proof would introduce rootkit-level complexity and still be unreliable.

**Correct strategy**: Accept tamper-evident and maximize the ability to *detect tampering*:
- Row-chain hashing makes single-row tampering break the chain.
- engine_sha makes engine version drift auditable.
- harness_guard narrows the attack window of "weaken gates → commit business code" to "must also modify hook copies".
- CI layer can add "ledger append-only" git diff checks.

---

## 4. Trust Boundary Inventory

### 4.1 Engine Repo vs Business Repo

**Boundary**: Engine Repo is "gate code"; Business Repo is "managed business code".

**Trust assumptions**:
- Engine Repo maintainer (you) is trusted.
- Business Repo committers are untrusted (may be careless, may be misled by AI).

**Cross-boundary operations**:
- `sandbox/install_hooks.sh` copies hook scripts from Engine Repo to Business Repo's `.git/hooks/`.
- After copying, Business Repo's hook copies **leave Engine Repo's version control** — this is an inherent limitation of the git hook mechanism.
- Mitigation: hook script source is protected by `protected-patterns.txt` in Engine Repo; changes require `[harness]` marker.

### 4.2 Shared Config vs Local Config

**Boundary**: `config/harness.toml` is team-shared policy; `config/harness.local.toml` is machine-local override.

**Trust assumptions**:
- Shared config is version-controlled via git; changes are auditable.
- Local config is not in git; each machine manages its own.

**Risk**: Local config can override shared policy (e.g., disable a rule).

**Mitigation**:
- `run_gate.py --validate` checks config consistency.
- CI layer can reject commits where "local config disables block-level rules" (requires team policy).

### 4.3 Inline Waiver vs Rule Judgment

**Boundary**: `harness-ok: RULE_ID reason` allows developers to declare "this rule does not apply to this line".

**Trust assumptions**:
- Waiver declaration is an explicit developer responsibility.
- AI agents should not add waivers on their own (prohibited by AGENTS.md).

**Risk**: Agent or developer abuses waivers to bypass rules.

**Mitigation**:
- Waivers must be in comments, must include rule ID, must include reason.
- Ledger records every waiver hit; `log --verify` can audit waiver frequency.
- CI layer can be configured to "alert when waiver rate exceeds threshold".

---

## 5. Attack Surface Summary

| Attack | Feasibility | Impact | Existing Mitigation | Additional Needed |
|------|--------|------|----------|--------|
| `--no-verify` skips gates | High (git native) | Medium (single bypass) | Ledger audit | CI rejects commits without credentials |
| Weaken checks/*.py without commit | Medium (requires Engine Repo change) | High (rules失效) | harness_guard blocking | — |
| Tamper with ledger | Medium (requires local write) | High (audit失效) | row-chain + anchor | CI checks ledger append-only |
| Replace installation copy | Low (requires MITM) | High (engine controlled) | engine_sha attribution | Release signed manifest |
| Delete .git/hooks/ | High (just rm) | Medium (protection loop失效) | None (git hook inherent limitation) | Documentation + CI fallback |

---

## 6. Secure Development Practices

### 6.1 Maintainers

- Each release includes `engine_sha` signed manifest (GitHub Releases).
- Periodically run `log --verify --engine` to check ledger-engine version consistency.
- Engine Repo `[harness]` commits must include `Engine-Sha` trailer (CI enforces).

### 6.2 Users

- Verify `engine_sha` against release manifest after init.
- Do not manually modify hook scripts outside Business Repo's `.git/hooks/`.
- Report ledger chain breaks immediately (see SECURITY.md).

### 6.3 Team Policy

- CI layer configures "ledger append-only" git diff checks.
- CI layer configures "reject merges without ledger credentials".
- Periodically audit `harness-ok` waiver frequency; investigate if abnormally high.

---

## 7. Known Unresolved Items

1. **Version control of `.git/hooks/` copies**: git hook mechanism itself does not support hook script version tracking. Currently relies on trust assumption "Engine Repo hook source is protected + users don't manually modify copies". No perfect solution; only documentation + CI fallback.

2. **Ledger deletion and rebuild**: Attacker can delete entire `ledger.jsonl` and start from scratch. row-chain can only detect "in-chain tampering", not "chain replacement". Mitigation: CI checks ledger file git diff has only added lines, no deleted lines.

3. **Local config trust**: `harness.local.toml` is not in git and cannot be audited. If local config disables block-level rules, gates silently pass. Mitigation: `--validate` check + documentation.

---

## Appendix: Glossary

| Term | Meaning |
|------|------|
| Engine Repo | guardrail-engine project repository, containing rule implementations and scheduler |
| Business Repo | Managed business repository, references Engine Repo via hooks |
| engine_sha | SHA256 of engine code snapshot, used for attribution and integrity verification |
| row-chain | Ledger row-chain hashing mechanism; each row contains previous row's hash |
| anchor | SHA256 of ledger's first row, anchors the starting point of the entire chain |
| harness_guard | Protection loop hook, blocks "weaken gates → commit business code" attack |
| protected-patterns.txt | Single source of truth for protected file surface |
| tamper-evident | Tampering can be detected after the fact |
| tamper-proof | Physically prevent tampering (this engine does not promise this) |
