# evidence for packaging delivery (2026-09-14)

| file | covers |
|---|---|
| verify_entries.txt | three-entry consistency (CLI/MCP/git hook) + commit block + rollback drill + ledgers |
| client_registrations.txt | MCP registration records (OpenClaw / Claude Code / Codex) |
| plugin_validate_strict.json | Claude plugin strict validation (0 errors / 0 warnings) |
| selftest.txt | engine/MCP/CLI selftest + ENTRIES scan |
| suite_264_ok.txt | branch-tip export re-run (264 tests OK) |
| m1_build_test.txt | M1: uv build -> wheel -> venv install -> run (version / quickstart / mcp selftest) |
| m1_versions.txt | M1 cross-version: wheel installed & verified on Python 3.11 / 3.12 / 3.13 |
| e1_marketplace.txt | E1: marketplace build -> strict validate (0 errors / 0 warnings) -> OpenClaw read -> Claude marketplace add + plugin install (enabled) |
| e1_openclaw_install.txt | E1: OpenClaw-side bundle install — final: user-executed (2026-09-14 10:17) + verified; bundle loaded & enabled (skills + MCP servers), doctor clean; v1 root-cause note included |
| ci_first_run.txt | First GitHub CI run on this line (draft PR #1) — both matrix legs green (py3.11 / py3.12); POSIX hook exec-bit fix included |

Notes:

- the built wheel (`agentguard-0.1.0.dev0-py3-none-any.whl`) ships with the delivery pack, not the repo.
- the built marketplace (machine-stamped) is at `harness-packaging-2026-09-14/agentguard-tools-marketplace`;
  rebuild anywhere via `integrations/plugins/build_marketplace.py`.
- all evidence files are scanned for encoding damage (U+FFFD / PUA / double-encoded tokens) via
  `python sandbox/check_mojibake.py` — clean as of 2026-09-14.
