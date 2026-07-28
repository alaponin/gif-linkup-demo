# KP2 build pack — self-review of PLAN v0.2 and the v0.1 artefact drafts

Review date: 2026-07-19. **Status: fixes applied same day** — §3 resolved via
option (a) (repo copy of check_pack.py; propagate upstream), all §4 items fixed
(retry shift, same-shell checks, negative check re-routed via MOEYS's own SS,
denial asserted on the X-Road error, 2.6.3 tightened to exact-set equality, SS
order deduped, lite via compose profiles + HOST_SS, ports centralised in lib.sh,
.gitignore, README refreshed, minor items), and §2/§5 items applied where
offline-possible (manifest video_ref crosswalk, prompt preconditions, 2.2 role
note, application-JSON artefact with provenance, message-log evidence noted,
P1 dogfood step, 5.5 calibration parked in PLAN §9). Still open: the §2.3
dogfooding itself (P1), and everything gated on a running stack (P0).

Scope: PLAN.md and every artefact drafted offline
(compose, configs, prompts, acceptance, scripts, mocks, seed data, docs), judged
against the KP2 Module 5 bundle, the build-pack contract (`kp-build-pack`,
`bb-config-gen`, `kp-solution-verify`) and the NIIS documentation the plan cites.

**Verdict:** the pack is structurally aligned with KP2 and the skill contracts,
and the offline verification (static check, YAML, syntax, right-learner test)
holds. But the draft has one structural conflict with the ship gate, one wrong
test design in the headline check, several concrete script bugs, and a set of
alignment gaps with the Module 5 narrative that need deliberate decisions, not
silent drift.

---

## 1. Alignment with KP2 — what holds

- **The proving slice is the right slice.** 2.6 implements exactly Module 5.6:
  PNEA pre-fills identity from PNIA and enrolment from PLR, asked-once, negative
  check, layers mapped. Acceptance 2.6's four assertions mirror the 5.6 AI-tip's
  given/when/then plus its safeguard ("right learner, not merely data returned";
  "half a check without the deny").
- **Module → artefact shape matches the contract.** Every module resolves to
  config + prompt + acceptance; prompts open "Below is …", decompose into named
  fields, end with the output format, and cite public specs only (NIIS, EIF,
  PAERA §5.2) — the bb-config-gen citation rule is respected everywhere,
  including deploy.sh referencing the public xrd-dev-stack rather than internal
  engines.
- **The demo/production honesty of 5.7 is carried through**: every shortcut in
  the configs is tagged `demo_only` and lands in `docs/production-delta.md`,
  including the migrate-and-retire task the hardening list forgets.
- **Gambia grounding is done as agreed**: regions, town-named schools, plausible
  names, ages consistent with senior secondary in 2026; the country is named
  nowhere; the NIN format is explicitly `[confirm]` rather than invented.

## 2. Alignment gaps — decisions needed (not bugs)

1. **Four vs five Security Servers.** Module 5.5 says "Central Server, four
   Security Servers, a Test CA". X-Road requires a management SS for the CS
   owner, so the runnable topology has five. The pack is right to follow the
   docs, but the video bundle and the pack now disagree on a number a viewer can
   count. → Add this to the Module 5 §5 open calibration items: either the 5.5
   script says "four member Security Servers (plus PDGA's management server)"
   or the demo folds management onto PDGA's server on-screen.
2. **No module ↔ subtopic crosswalk.** The pack numbers modules 2.1–2.6; the
   videos teach 5.4 (registration), 5.5 (federation), 5.6 (exchange), 5.7
   (production delta). Nothing records that 2.1↔5.5, 2.2–2.5↔5.4, 2.6↔5.6,
   docs/↔5.7. → Add a `video_ref` field per module in manifest.yaml (or a table
   in README) so a learner can go from video to artefact.
3. **Configs were hand-written, not generated.** bb-config-gen's rule is "never
   write a config by hand — the prompt's output is the artefact". The v0.1
   configs were drafted directly; the prompts exist but have never been run.
   Acceptable for a first draft, but the pack currently violates its own
   teaching claim. → Add a P1 dogfooding step: run each prompt for real, diff
   its output against the config, reconcile, and only then call the config
   "generated".
4. **Module 2.2 (MoEYS/PEMIS) is thin.** It registers a member and publishes a
   service nobody consumes; its only real role in the slice is being the
   unauthorised caller in 2.6. That is defensible (it seeds the KP3/KP4 join
   key and demonstrates "on the bus ≠ may call"), but the acceptance text
   should say so explicitly, and the 5.4 video's "same registration shape for
   every member" claim is exactly what 2.2 proves — worth stating.
5. **Joget disappeared from the runbook.** The scaffold's runbook cited
   "Linkup + Joget DX 8.x" and kp-solution-verify's live half mentions the
   Joget stack. Dropping Joget from KP2 was a deliberate decision (mocks behind
   stable OpenAPI contracts), but the pack should say once, in README, that
   KP2's slice is Joget-free by design and where Joget re-enters (KP4) — else
   a reader of the skill docs will look for a Joget dependency that isn't there.
6. **Interop Method Steps 5–8 are claimed but not referenced.** PLAN cites
   08-Interoperability as a source, yet no artefact points at the Member
   Requirements or SLA templates (5.2/5.3) that gate onboarding before the 5.4
   registration step. → One line in each member prompt's Problem section
   ("precondition: the member passed the Member Requirements checklist") would
   stitch the organisational layer to the technical config it teaches.

## 3. Structural conflict with the ship gate (must resolve)

`check_pack.py --ready` fails on any literal `[confirm:` in the pack — and the
prompts **must permanently contain** literal `[confirm: …]` text, because
instructing the model to emit confirm-markers is the anti-invention discipline
the videos teach. As drafted, the pack can never pass `--ready`. Options:
(a) check_pack.py exempts `prompts/` from the scan (change to the shared skill —
affects KP3/KP4 packs too, probably correctly); (b) prompts write the marker in
a non-matching spelling (e.g. `[confirm — …]` or `⟨confirm: …⟩`) — ugly, weakens
the teaching; (c) `--ready` gains an allowlist. Recommendation: (a), raised
against the itu-giga-kp plugin, since the same collision will hit every
implementation KP. Decide before P5, or VERIFIED is unreachable by definition.

## 4. Concrete defects in the drafts (fix at next pass)

1. **`lib.sh retry()` drops the command's first word.** Signature comment says
   `retry <tries> <sleep> <desc> -- <cmd…>` (`shift 4`), but every call site
   omits the `--`, so `shift 4` eats the command name (`curl`, `docker`).
   Every retry would fail. Fix: `shift 3`, drop the `--` convention.
2. **`acceptance.sh` calls shell functions inside `bash -c`.** `api`/`api_key`
   are defined in the parent shell; the `bash -c` subshells in `check` won't
   see them (not `export -f`ed). The 2.1 and 2.x checks fail before touching
   X-Road. Fix: `export -f api api_key` in lib.sh, or restructure `check` to
   run in the current shell.
3. **Wrong design for the 2.6.4 negative check.** It sends
   `X-Road-Client: MOEYS/PEMIS` through **ss-pnea**. PEMIS is not a client of
   ss-pnea, so ss-pnea rejects the request locally ("client not found") — the
   test would pass without ever exercising the provider ACL, which is the thing
   5.6 says it proves. Fix: route the negative call through ss-moeys's REST
   interface (localhost:6080; lite profile: the shared provider SS), so the
   denial genuinely comes from the ACL on the provider side. Update
   acceptance/2.6.md and configs/x-road-bus/2.6.yaml (`negative_check` should
   carry its own entrypoint).
4. **`! curl -sf` proves too much.** Any failure — network down, SS not up —
   passes the negative test (false positive). Assert the specific denial:
   capture status + body and match the X-Road access-denied error type, not
   mere non-success.
5. **2.6.3 doesn't test what it claims.** The heredoc asserts the form is
   covered by NIN+bus fields, but never asserts the pre-filled sets are
   disjoint from the citizen-provided set or that no *extra* fields leak
   (purpose limitation). Also `set(idr) | set(enr) - {"nin"}` has an operator-
   precedence surprise (harmless today, misleading tomorrow). Tighten to:
   covered AND no unexpected fields AND `nin` the only citizen field.
6. **Duplicate ss-pdga iteration in deploy.sh step 6.** `for ss in ss-pdga
   "${!SS_UI[@]}"` visits ss-pdga twice (it is also a key of SS_UI). Order the
   keys explicitly instead.
7. **Lite profile is declared but not implemented.** deploy.sh unsets the two
   SSs from SS_UI but never registers PLR/PNIA/MOEYS subsystems on the shared
   SS; acceptance.sh iterates ss-pnia/ss-moeys unconditionally and would fail
   under LITE=1; `deploy.replicas: 0` in the override is not reliably honoured
   by docker compose. Fix: use compose `profiles:` for ss-pnia/ss-moeys, add a
   LITE branch in deploy (host provider subsystems on ss-plr), and gate the
   acceptance loop on LITE.
8. **acceptance.sh scrapes ports out of docker-compose.yml with grep.** Brittle
   (breaks on reformatting) and duplicates knowledge lib.sh already has. Put
   one SS→port map in lib.sh and use it everywhere.
9. **No `.gitignore`.** `.env` (contains the PIN and admin password) and
   `__pycache__/` would be committed. Add one next to `.env.example`.
10. **Pack README is stale.** It predates the draft: no mention of
    docker-compose, apps/, docs/, REVIEW/PLAN, the Joget-free decision, or how
    to run the slice (it defers entirely to runbook.md, which is fine, but the
    "Built and proven" line is aspirational until P5). Refresh at P3.
11. **Minor:** seed.sh restarts app containers although CSVs are mounted from
    the host and apps load at startup — correct but the comment should say the
    restart *is* the reload; `yq_get` fails with a Python traceback rather than
    a clean error when a key is missing; runbook's "20–40 min" first-run
    estimate is unmeasured — mark `[confirm at P0]`.

## 5. Improvement points (beyond fixes)

- **Make 2.6 emit an artefact.** The exchange currently proves itself and
  vanishes. Have acceptance.sh write the assembled credential application
  (JSON: citizen field + pre-filled fields + per-field provenance) to an
  `out/` file — the tangible "the learner was asked once" object for the video
  demo, and the natural seam KP4's Joget form later replaces.
- **Health-gate the SSs, not just their UIs.** "UI answers" ≠ "global conf
  current". Add a deploy post-condition per SS (global-conf status via admin
  API) so acceptance failures point at propagation, not at the exchange.
- **Record message-log evidence.** X-Road's message log is the audit story the
  legal layer leans on; one acceptance line ("the exchange appears in the
  provider SS message log") would prove observability, cheaply.
- **Pin the testca image.** `TESTCA_TAG=latest` contradicts the pack's own
  reproducibility rule; pin a digest at P0.
- **PLAN P-phases lack owners/estimates.** Fine for now, but P0 has a hidden
  external dependency (Docker host with 16 GB); name it so P0 doesn't stall.

## 6. What is genuinely solid

The decision record (7.x + delta, Docker-first, mocks-with-OpenAPI-seams,
release images over xrd-dev-stack) with reasons; the doc-verified stand-up
sequence with its known-traps list; deterministic seed data with deliberate
mismatch rows and a generated README naming them; acceptance thinking in
positive *and* negative space; identifiers frozen once in manifest.yaml as
cross-pack join keys; and the offline test that the right-learner assertion
catches a planted mismatch — the reviewer's favourite kind of test: one that
was itself tested.
