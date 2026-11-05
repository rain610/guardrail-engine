# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.2.x   | Yes       |
| < 0.2   | No        |

## Reporting a Vulnerability

Guardrail Engine is a code-gate framework. Security issues fall into two categories:

1. **Bypass / weakening** — a way to make gates silently pass malicious or invalid code.
2. **Supply-chain** — tampering with engine files, ledger, or hook scripts.

### How to report

- **Preferred**: open a [GitHub Security Advisory](https://github.com/rain610/guardrail-engine/security/advisories/new) (private).
- **Email**: send details to the maintainer via the email on the GitHub profile.

Include: affected version, reproduction steps, impact assessment.

### What to expect

- Acknowledgement within 3 business days.
- A fix timeline will be discussed in the advisory; typical target is < 14 days for critical bypasses.
- Credit in the release notes unless you prefer anonymity.

## Known Limitations (by design)

These are **not vulnerabilities** — they are documented trade-offs:

- **`git commit --no-verify` always bypasses pre-commit gates.** This is a git-level escape hatch that no client-side tool can prevent. The engine's response is *tamper-evident*: the ledger records every gate run, and `--verify` replays the audit trail.
- **Tamper-evident ≠ tamper-proof.** The ledger detects post-hoc modification of protected files, but cannot physically prevent a user with write access from editing them. The `harness_guard` hook narrows the window by blocking commits while protected files have uncommitted changes.
- **Engine trust anchor is local.** An installed copy of the engine is trusted based on `engine_sha` recorded at init time. If the installation itself is compromised before init, the engine cannot self-detect this. Verify `engine_sha` against a known-good source after installation.
- **Zero third-party dependencies** in the engine core (`checks/`, `harness/`, `guardrail_engine/`). External adapter gates (ESLint, Semgrep) are optional and off by default; enabling them introduces those tools' own trust assumptions.
