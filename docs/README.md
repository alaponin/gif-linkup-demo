# docs/ — four kinds, and a document is only one

**Reference** (this directory's root, plus `README.md`/`runbook.md`/`manifest.yaml`/`deployment.yaml`/`hurl/README.md`/`PLAN.md` at the pack root) — true right now: no history, no dates, no status tables.
**`docs/decisions/`** — frozen once written: dated, superseded rather than edited to look current.
**Generated** (`onboarding/*`, `docs/path-conformance.md`) — never hand-written; a test regenerates and diffs it.
**`docs/notes/`** — deletable without loss, excluded from any status claim.

`docs/path-conformance.md` is the only place status against the onboarding path is stated. Where any other document disagrees, that one is right.
