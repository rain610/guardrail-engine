# ADR-0001: Five Iron Rules of Packaging (Boundary Between Shell and Engine)

- Status: Accepted
- Date: 2026-09-14
- Context: Packaging route (branch `feat/packaging`; companion "Packaging Execution Plan" and "Packaging Branch Execution Checklist")
- Related review lessons: G1/K1 (intent right, seam wrong), K6 (summary caliber), F3/Q③ (human actions)

## Background

harness prepares to distribute via multiple "shells": CLI (`agentguard`), MCP thin service, skill packs, host plugins.
More shells means more "second copy of logic" and "silent failure surface" — the highest-frequency defect class in this project's history is precisely
"looks like running, actually not counting / not blocking" (reporting safety).

## Decision (Five Iron Rules)

1. **Logic only one copy (engine), shell only forwards.**
   Shells prohibit: redefining engine symbols (`compute_changed_lines` / `_find_hits` / `filter_hits_by_lines` /
   `is_untracked` / `script_line_offset` etc.); prohibit line number / coordinate system calculations
   (K1 lesson: cross-layer coordinate systems are this project's highest-frequency silent failure point, shell layer only uses rc + summary).
2. **Every new shell goes into ENTRIES list + doctor health check upon launch.**
   "Shell not installed / installed wrong" must be visible (otherwise another "reported safety nobody knew").
3. **Single source of version:** `VERSION` → CLI `--version` → MCP serverInfo → skill metadata, four places consistent (test locked).
4. **Human actions never enter automated shells:** accept / change baseline only allowed at real terminal (F3/Q③ conclusion).
5. **Distribution must not change enforcement layer behavior:** hooks-dir / install flow / each gate semantics unchanged.

## Consequences

- New shells must enter `tests/packaging/test_packaging_contract.py`'s `SHELL_FILES` registry and pass contract lock;
- Changes violating iron rules 1/5 directly rejected in review;
- Releases violating iron rules 2/3 considered incomplete (M5 acceptance item).
