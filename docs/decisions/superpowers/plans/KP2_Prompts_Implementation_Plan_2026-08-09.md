# KP2 prompts — implementation plan

**Date:** 9 August 2026
**Addresses:** the prompts items of `KP2_Learning_Integration_Review_2026-08-09.md` §3.2
**Scope:** `prompts/` (five existing files + a new `examples/` subtree), the crosswalk hooks in `manifest.yaml`, and — where the ship gate is concerned — one coordination item with the sibling `ITU-Giga-KP-Plugin`. No code paths in `apps/`, `hurl/` or `scripts/` change; the cardinal rule ("write the prompt first, run it, commit its output") is the method for every generated artefact below.

**Comment discipline (applies to every task below).** Comments in anything this plan adds or edits — including `check_prompts.py` (P4) and any header lines added to the prompt files — are terse and generic: what it does, nothing more. No references to WIP plans, dated reviews, decision records, or this plan's task ids (P1–P5). Where something needs to be *recorded* rather than merely commented — why the PTSB brief was reconstructed the way it was, why the C1/D1 template prompts were dropped, the old→new prompt-name history — it goes into a separate file (`prompts/examples/README.md`, `prompts/README.md`, or a `docs/decisions/` note), not into inline comments. P3's header line on each prompt states a durable fact ("realises Module X / video_ref N.N"), which is content, not commentary — that stays; narrations of how it got that way do not.

---

## 0. Current state, as verified in the code

- Five prompts exist: `federation-core.md`, `register-member.md`, `once-only-exchange.md`, `member.md`, `join-member.md`. All follow the house shape (Problem → copy-paste Prompt → Inputs/outputs → Safeguard) with `[confirm:]` discipline throughout.
- **No prompt's inputs ship.** Every prompt opens "Below is [the NIIS reference] and [the Progressa service brief] [paste both]" — and a search of the pack finds **no brief file anywhere** (the only `*example*` in the tree is `.env.example`). The PTSB join of 3 August necessarily used a brief; it was never committed. `out/join/*.json` records exist locally but `out/` is gitignored, so a clean checkout has neither the input nor a worked output for any prompt.
- **Endings are inconsistent:** `member.md` ends its Inputs/outputs with the real post-steps (`hurl/generate.py` → `member.sh list` → `deploy.sh`, member.md:84–88), but `federation-core.md`, `register-member.md` and `once-only-exchange.md` end on the Safeguard with no verification command at all.
- `join-member.md` states honestly that the join policy was written by hand before the prompt existed (join-member.md:22–27) — good teaching; keep.
- The manifest binds `prompt:` per module (manifest.yaml:71, 94, 102, 110), so the sibling kit's `check_pack.py` existence-checks each prompt file. Anything added under `prompts/examples/` is invisible to that gate today — safe to add, but see P4 for making it *checked* rather than merely present.
- Cross-reference drift confirmed: the module bundles and both August reviews refer to `prompts/2.4.md`-style names that no longer exist (renamed to the current five in S-02/D2, per the comment at manifest.yaml:75–88). Nothing on disk maps old names to new.
- The prompts' own preconditions now resolve: `member.md` items (5)–(6) carry Member Requirements + SLA into member configs, rendered to `onboarding/<key>/` by `scripts/render_onboarding.py` through the same `writer.render_onboarding_tree()` a real join uses. The 3 Aug Four-Layer Plan's C/D workstreams were effectively delivered this way rather than as `configs/onboarding/` templates.

## 1. Tasks

### P1 — Worked examples: ship each prompt's inputs and its committed answer

Create `prompts/examples/`, one folder per prompt that takes a brief:

```
prompts/examples/
├── README.md                      ← what these are; the reproduce-and-diff exercise
├── member-ptsb/
│   ├── brief.md                   ← the PTSB service brief (reconstructed once, committed)
│   ├── expected-manifest-entry.yaml
│   └── expected-ptsb.yaml         ← the two documents the prompt produces
├── federation-core/
│   └── brief.md                   ← the Progressa institutions + operator brief
├── register-member/
│   └── brief.md                   ← the canonical three-member brief
└── once-only-exchange/
    └── brief.md                   ← the credential-application scenario brief
```

1. **Reconstruct the PTSB brief** from what its outputs demand (`apps/specs/ptsb-awards.openapi.yaml` is still tracked; `member.md` items (1)–(6) enumerate every fact a brief must supply: name, code, subsystem, hosting choice, published service + ACL consumers, requirements/SLA answers). Run `prompts/member.md` against it for real; commit the two output documents as the expected answer. This simultaneously gives the tracked-spec-without-config asymmetry a purpose: the spec is the exercise fixture.
2. For the three canonical prompts, the "expected output" **is the committed config** (`configs/x-road-bus/federation-core.yaml` etc.) — the example folder needs only the brief plus one line in its README: "expected output: the committed file; diff yours against it."
3. `prompts/examples/README.md` states the exercise contract: run the prompt with the brief, diff against expected, then resolve `[confirm:]` markers against the live registry — and states the boundary: **example briefs are inputs, never things `generate.py` reads** (no new write path into the deploy sequence).
4. `join-member.md` gets no brief — its prompt's inputs are the NIIS reference + the pack's own note, both cited in the file; add one line saying so, so the absence reads as deliberate.

**Verify:** review (the diff exercise is itself the check); `--fast` unchanged (examples are inert data).
**Risk:** low. One real risk: the reconstructed PTSB brief drifting from what the committed spec implies — mitigated by generating the expected outputs *from* the brief via the prompt and checking the service block against the tracked spec by hand once.

### P2 — A uniform "Prove it" footer on every prompt

Append a final section to each of the five prompts, same heading, three lines max:

- `federation-core.md` → *Prove it:* `python3 hurl/generate.py && scripts/verify.sh --fast`; deployed for real by `scripts/deploy.sh`; proven by `acceptance/federation-core.md` (`--live`).
- `register-member.md` → *Prove it:* `hurl/generate.py` + `--fast`; live: `acceptance/register-member.md` + `member.md`'s generic per-member check.
- `once-only-exchange.md` → *Prove it:* `scripts/acceptance.sh` (2.6 is the headline check); tier `--live`.
- `member.md` → promote the existing post-steps (member.md:84–88) into the footer verbatim, adding the tier names.
- `join-member.md` → *Prove it:* `hurl/generate.py`'s `check_join_policy()` (`--fast`); exercised live by any join (`acceptance/join-member.md`, vacuous-by-default per README).

Wording rule: the footer names the *check that already exists* — no new checks are created by this task.

**Verify:** review.
**Risk:** none.

### P3 — The crosswalk hooks: bind prompts to subtopics and retire the old names

1. Add `video_ref` context to each prompt header: one line under the existing **Building block(s)** header — "Realises: Module `<id>` (`video_ref` N.N) — the production-grade form of subtopic N.N's AI usage tip." Values come from `manifest.yaml`; for `join-member.md` write the truth: "`video_ref: "?"` — no subtopic covers this module yet (fitness review W2, decision pending)."
2. Add a **name-migration table** to `prompts/README.md` (new, ten lines): old `2.N.md` name → current file, so every stale reference in the bundles and reviews has one place to resolve. (Editing the six module bundles themselves is a `build_kp2_moduleN` source edit + re-render — out of this plan's scope, but the table is what makes that edit mechanical later.)
3. When the learning-map generator lands (integration review R1), it reads `modules[].prompt` + `video_ref` from the manifest — this task's only job is to keep those two fields accurate, which they already are; no manifest change needed.

**Verify:** review.
**Risk:** none.

### P4 — Make the examples checked, not just present (coordination item)

The pack's own convention (`check_pack.py`'s `<pack>/<tool>/check_*.py` auto-discovery, used by the Four-Layer Plan A4) allows a `prompts/check_prompts.py` that asserts: every `modules[].prompt` in the manifest exists (already gated upstream), every prompt file contains a Prompt block and a Prove-it footer, and every `prompts/examples/*/brief.md` pairs with either an `expected-*.yaml` or a README line naming the committed file as expected output. ~40 lines, stdlib + PyYAML only, runs in `--fast` via the ship gate's auto-discovery with **no upstream change**.

**Verify:** `--fast`.
**Risk:** low; keep it under the ~2 s combined-checker budget the Four-Layer Plan §6 set for new `--fast` checkers.

### P5 — Prompts for the still-missing layers (scoped out, pointed at)

The legal (M2) and organisational (M3) generating prompts remain the W1 close's deliverables, not this plan's — but P1's `examples/` layout and P2's footer convention are written so those two arrive as `prompts/legal-decree.md` + `prompts/governance-pack.md` with a brief and a document-tier check each, following the same shape. This plan's only action: name them as "planned, W1" in `prompts/README.md` so the directory states its own gap (the named-absence house style).

## 2. Sequencing and cost

P1 is the bulk (one real prompt run + reconstruction); P2/P3 are an hour of editing; P4 is a small checker. Order: P2 → P3 → P1 → P4 (footers and headers first so P1's committed examples are born conforming; the checker last so it gates a finished shape). No Docker at any step; nothing touches the federation.

## 3. Reviewed against the code — corrections and confirmations

1. **Confirmed:** no example inputs exist anywhere in the pack (only `.env.example` matches); `out/join/` records are gitignored so they cannot serve as committed worked examples.
2. **Confirmed:** ending inconsistency — `member.md` alone carries post-run steps (member.md:84–88); the other three brief-taking prompts end at Safeguard.
3. **Correction to the 9 Aug review:** it said the Four-Layer Plan's C/D (Member Requirements / SLA prompts) were "folded into member configs" — verified true, and *more* than that: `prompts/member.md` items (5)–(6) are the generating play, `render_onboarding.py` renders the records through the same writer a live join uses, and consumer-only members are handled (SLA omitted, `lawful_basis` moved to per-service). C1/D1 as separate template prompts are therefore **not needed**; this plan drops them rather than re-proposing them.
4. **Confirmed:** `manifest.yaml:75–88`'s comment documents the S-01/S-02 collapse and rename that produced the stale `2.N` references; nothing on disk maps old→new (P3.2 fills this).
5. **Boundary check:** nothing in P1–P4 adds an input to `hurl/generate.py` or a write path into deploy — consistent with the Four-Layer Plan §2's "must not become a second input" guardrail, which reviewers should re-apply to any future variation of P1.
