# KP2 — Join, Plan A: the step registry

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. This plan implements **Plan A** of `docs/superpowers/specs/2026-08-01-member-join-api-design.md` §15. It is self-contained, ships nothing user-visible, and is the **prerequisite** for Plan B (`2026-08-01-kp2-join-b-api.md`).

**Goal:** express the admin-API sequence `hurl/generate.py` already emits as an
ordered registry of named **steps**, each declaring what it needs and what it
produces, so that a later caller can run *one* step at a time. Today the
sequence exists only as whole-file emission: `build_ss_file()`,
`build_hosted_client()` and `build_service_file()` produce monolithic bodies
that `hurl/run-linkup.sh` concatenates into a single Hurl invocation, because
Hurl captures do not cross file boundaries.

That is correct for cold deploy and useless for anything resumable. The join
API (Plan B) needs to run step 14 of 30 without re-running steps 1–13. The
alternative — a second implementation of the sequence in Python — is the
failure mode `PLAN.md` §5 already refused once: "a second, weaker copy of the
headline check is worse than none — the two drift and the weaker one passes."

**Architecture:** new `hurl/steps.py` holding a `Step` dataclass and the
ordered registry. `generate.py` builds its output by rendering the registry in
order and joining — the same templates, the same order, the same bytes. Nothing
consumes the registry per-step yet; Plan B does. The value delivered by this
plan alone is that the sequence acquires names and a declared contract, and
that a test proves the contract matches the templates.

**Tech Stack:** unchanged. Standard library plus the existing `pytest`.

## Global Constraints

- **Byte-identical output, every task, both profiles.** This is the entire
  safety mechanism, and it is the same one `2026-08-01-kp2-generate-templates.md`
  used successfully. Baseline before starting:

```bash
for p in full lite; do
  sed -i.bak "s/^profile: .*/profile: $p/" deployment.yaml
  python3 hurl/generate.py
  cp -r hurl/scenarios /tmp/steps-$p-scenarios
  for f in vars.env topology.json topology.sh compose.members.yml; do cp hurl/$f /tmp/steps-$p-$f; done
done
```

  Restore `profile: full` afterwards. Diff after **every** step converted, not
  at the end of the task. A trailing-newline difference is a failure.

- **`tests/test_golden.py` must stay green throughout.** If it goes red the
  answer is always to revert the last conversion, never to regenerate the
  corpus.
- **`hurl/check_scenarios.py` must stay green too**, and its reported counts
  must not move. It checks capture ordering and variable use across the
  *concatenated* set — a property no single-file diff catches, and precisely
  the property this plan is at risk of breaking. Record its baseline numbers in
  Task 1 Step 1 and re-check after every task.
- **Move, do not improve.** No renaming a Hurl variable, no reordering
  requests, no tidying a template being wrapped. Improvements go in a separate
  commit, after.
- Commit after every task.

## Design decisions

1. **`hurl/steps.py`, not more of `generate.py`.** The file is already the
   pack's largest and `2026-07-28-kp2-simplification.md` wants it smaller. The
   registry is a genuinely separate concern with a genuinely separate consumer
   (Plan B), so it gets its own module rather than another 300 lines in
   `main()`.
2. **`provides` is the Hurl `[Captures]` names; `requires` is the Hurl
   `{{var}}` names.** Both are *runtime* Hurl identifiers. They are not
   `sub()`'s `@name@` tokens, which are substituted in Python before Hurl ever
   sees the file. Conflating the two is the mistake this plan is most likely to
   make; the checker in Task 4 exists to make it impossible.
3. **No `reverse` field.** DELETE is Plan C. Giving every step a reversal
   template now means designing ~30 reversals before one has been written or
   tested, against the least-understood sequences in the whole design (spec
   §5.2, §16.1). The field is added in Plan C when the sequences are known.
4. **No `probe` field beyond an enumerated few.** Resume is served by Plan B's
   persisted `last_completed_step`; idempotence by 409-as-success, which this
   pack has already proven live on ACL grant/revoke (`PLAN.md` §11). Task 5
   enumerates the steps where `409` is genuinely ambiguous and only those get a
   probe (spec §5.3).
5. **`actor` is a field from the start.** `operator` or `member` — cheap, one
   literal per step, and it is what Plan B's console colours a step list by and
   what Plan C's `BLOCKED` state keys off. Unlike `reverse`, it requires no
   design work per step: it is a fact about the step that is already known.
6. **The registry is data, not a class hierarchy.** A list of frozen
   dataclasses. No base class, no `execute()` method — Plan A has no executor,
   and inventing an interface before its only caller exists is how this design
   got over-built the first time.

## Out of scope

Any executor. Any HTTP service. Any change to what the scenarios *do*. Any
change to `check_scenarios.py`'s assertions. Extracting `hurl/topology.py`
(`2026-07-28-kp2-simplification.md` Task 1) — a different seam in the same
file; land one fully before starting the other.

---

## Task 1: The `Step` dataclass, the registry, and one step through it

**Files:** `hurl/steps.py` (new), `hurl/generate.py`, `tests/test_steps.py` (new)

The first conversion establishes the mechanism. Pick a step that is short, has
one obvious capture, and appears in exactly one place.

- [x] **Step 1:** baseline per Global Constraints. Run `python3
      hurl/check_scenarios.py` and **write its reported counts into this file**
      (captures, variables, identifiers) — every later task checks against them.
      Confirm `tests/test_golden.py` green before touching anything.

      **Baseline recorded 2026-08-01 (profile: full):** `OK -- 82 captures, 18
      variables, identifiers match manifest.yaml`. `tests/test_golden.py`: 2
      passed. Full `tests/` suite: 15 passed. Both profiles' artefacts
      snapshotted to `/tmp/steps-{full,lite}-{scenarios,vars.env,topology.json,
      topology.sh,compose.members.yml}`.
- [x] **Step 2:** create `hurl/steps.py` with the dataclass and nothing else:

```python
@dataclasses.dataclass(frozen=True)
class Step:
    id: str                    # "cs.init", "ss.auth_key_csr" -- dotted, stable, never renumbered
    template: str              # filename under hurl/templates/
    actor: str                 # "operator" | "member" -- see design §4
    requires: tuple[str, ...]  # Hurl {{var}} names this step reads
    provides: tuple[str, ...]  # Hurl [Captures] names this step writes
```

  No methods. No inheritance. `id` is stable forever — Plan B persists it in
  job contexts and Plan C attaches reversals to it, so a rename is a migration.
- [x] **Step 3:** convert **one** step: the Central Server trust-services
      block. It is three requests, its interpolations come from
      `core["trust_services"]`, and its captures are few enough to check by
      eye. `generate.py` renders it through the registry entry instead of
      calling `render()` directly.
- [x] **Step 4:** regenerate both profiles. Diff all four artefacts against the
      baseline. `tests/test_golden.py` green. `check_scenarios.py` counts
      unmoved.
- [x] **Step 5:** `tests/test_steps.py` with one assertion for now: every `id`
      in the registry is unique and matches `^[a-z0-9]+(\.[a-z0-9_]+)+$`. This
      test grows in Task 4 and is the file the contract checker lands in.
      Commit.

## Task 2: Convert the Central Server and management-server sequence

**Files:** `hurl/steps.py`, `hurl/generate.py`

Scenarios `00`–`03` and `10`. These share one context — they run once, against
one host, in a fixed order, with no per-member loop — so converting them
together means holding one mental model rather than five.

- [x] **Step 1:** convert `00-cs-init.hurl`'s blocks: instance init, software
      token login, INTERNAL and EXTERNAL signing keys, member class. Note the
      init response is **200, not 201** (`PLAN.md` §8) — the assertion moves
      with the template unchanged.

      Implementation note: the four blocks were physically split out of the
      single `00-cs-init.hurl.tmpl` into `fragments/CS_INIT.hurl.tmpl`,
      `CS_MEMBER_CLASS.hurl.tmpl`, `CS_TOKEN_LOGIN.hurl.tmpl`,
      `CS_SIGNING_KEYS.hurl.tmpl` (verified byte-for-byte reassembly before
      wiring) — steps `cs.init`, `cs.member_class`, `cs.token_login`,
      `cs.signing_keys`.
- [x] **Step 2:** convert `02-cs-members.hurl`. This emits a block **per
      member**, so the per-member fragment is one `Step` rendered in a loop and
      joined in Python — the same rule Design decision 4 of the templates plan
      set, for the same reason. The registry holds the step once; the loop is
      `generate.py`'s.
- [x] **Step 3:** convert `03-cs-anchor.hurl` and `10-ss-pdga.hurl`. The
      management server is where `ca_name` is captured and reused by every later
      CSR — this is the single most-depended-on `provides` in the registry, so
      declare it carefully and note in a comment that steps in Tasks 3 read it.

      Implementation note: `SS_BRINGUP_INIT.hurl.tmpl` was split at the
      AUTH-key/CSR boundary (verified byte-for-byte reassembly) into
      `ss.bringup_init` (unchanged name, truncated content) and a new
      `ss.auth_key_csr` (`fragments/SS_AUTH_KEY_CSR.hurl.tmpl`) so
      `ss.ca_name_capture` could become its own step instead of a Python
      string-splice (`@CANAME@`), matching the design spec §5.2 example id.
      The inline management-registration block (~90 lines, previously a raw
      `sub()` string literal in `main()`) was extracted to
      `fragments/SS_MGMT_REGISTER.hurl.tmpl` (step `ss.mgmt_register`).
      `build_ss_file()` (Task 3's function) shares `SS_BRINGUP_INIT.hurl.tmpl`
      with the pdga path, so its call site needed the same split applied here
      to stay byte-identical — Task 3 still owns the rest of that function.
- [x] **Step 4:** verify byte-identical after each conversion, not at the end.
      `check_scenarios.py` counts unmoved.
- [x] **Step 5:** commit each conversion separately.

## Task 3: Convert the member sequences

**Files:** `hurl/steps.py`, `hurl/generate.py`

Scenarios `20`–`23`, the hosted-client path, and `30`–`32`. These share the
other context — per-member, profile-dependent, and the place where `hosted_on`
splits the flow. `build_ss_file()`, `build_hosted_client()` and
`build_service_file()` are the three functions in play and they are best read
together.

- [x] **Step 1:** convert `build_ss_file()`'s sequence: anchor upload, owner
      init, token login, AUTH then SIGN key-with-CSR, CSR download as **PEM**
      (`?csr_format=PEM`, not the DER it was generated as — `PLAN.md` §8),
      Test CA signing, cert import, AUTH registration, CS approval, activation,
      timestamping service.

      Implementation note: the AUTH-key-CSR/`ca_name` half of this (steps
      `ss.bringup_init` through `ss.auth_key_csr`) was already done in Task 2,
      forced by the shared template. This step converted the rest:
      `ss.sign_key_csr` (`MEMBER_SIGN_KEY.hurl.tmpl`), `ss.bringup_register`,
      `ss.activate`, `ss.tsa_post`, `ss.client_add`, `ss.client_register`.
- [x] **Step 2:** convert `build_hosted_client()`. Its ordering is
      load-bearing and was found live: **client-add must precede its SIGN-key
      generation, which must precede its registration**
      (`2026-07-26-deployment-spec-and-lite-profile.md`). The registry order is
      now the thing that encodes that; add a comment on those three steps saying
      so, because a future reader reordering a list is more likely than a future
      reader reordering a template.

      Implementation note: comment added both on the three `Step` entries in
      `hurl/steps.py` and at `build_hosted_client()`'s call site.
- [x] **Step 3:** convert `build_service_file()`: service-description POST with
      `type: OPENAPI3`, then the separate enable (services are disabled when
      added — `PLAN.md` §8), then the ACL grants. Note the observed response
      codes that disagree with the OpenAPI model (register 204 not 200, enable
      200 not 204) travel with the templates and must not be "corrected".
- [x] **Step 4:** set `actor` on every step now that all of them exist. Under
      `hosted_on`, every step is `operator`. For a member with its own server,
      the anchor-upload through cert-import run is `member`; CS approval is
      `operator`. This is the field Plan C's `BLOCKED` keys off, so getting the
      boundary right here saves a pass later.

      Implementation note: a genuine tension surfaced here that the plan
      didn't call out — several step ids are shared between a joining
      member's own-server bring-up (`build_ss_file`, where `actor` should
      read as declared below) and two contexts where it should not:
      `main()`'s 10-ss-pdga block (the *operator's* own management server,
      never a joining member) and `build_hosted_client()`/the hosted branch
      of `build_service_file()` (Task 3 Step 2's "under `hosted_on`, every
      step is `operator`"). `Step.actor` is one static field per id, so it
      cannot vary by call site. Resolved by declaring the own-server-member
      default on each `Step` and documenting both exceptions as call-site
      overrides in a comment block in `hurl/steps.py` (above `ss.bringup_init`)
      and inline at `build_hosted_client()` — Plan A has no executor to read
      either value yet (design decision 6), so this is a documentation gap,
      not a correctness one; Plan B's per-step runner will need to apply the
      hosted/pdga override itself rather than trust the registry's default
      blindly for those two call sites.

      Final assignment: `member` — `ss.bringup_init`, `ss.auth_key_csr`,
      `ss.sign_key_csr`, `ss.activate`, `ss.tsa_post`, `ss.client_add`,
      `service.publish`. `operator` — everything CS-side (`cs.*`),
      `ss.bringup_register` and `ss.client_register` (both bundle a CS
      approval as their dominant/gating action), `service.acl` (matches
      `PLAN.md` §11: ACL grant/revoke is a console/operator action), and the
      three PDGA-only steps `ss.ca_name_capture`, `ss.mgmt_register`,
      `ss.tsa_capture` (always accurate — never rendered for a joining
      member).
- [x] **Step 5:** verify byte-identical **under both profiles** after each
      conversion — lite is where the hosted path is exercised, and it is the
      profile most likely to reveal a mis-scoped capture prefix. Commit each
      separately.

## Task 4: Make `requires`/`provides` a checked contract, not a comment

**Files:** `tests/test_steps.py`, `hurl/steps.py`

A declared contract nobody verifies is documentation that rots. This task makes
the declarations mechanically true, and it is the task that gives Plan A value
independent of Plan B.

- [x] **Step 1:** write a parser in the test — not in `steps.py` — that reads a
      template and extracts (a) every `{{name}}` it references, (b) every
      `[Captures]` name it defines. Regex is sufficient; these are generated
      files with a fixed shape, and a Hurl parser dependency for a test is not
      warranted.

      Implementation note: the parser reads the raw, unrendered `.tmpl`
      source under `hurl/templates/` (via `step.template`), not the
      generated `hurl/scenarios/*.hurl` output — the generated files only
      exist for one already-chosen member/profile, and would hide the
      registry's per-id, cross-member contract. `ss.*`/`service.*` templates
      still carry `@HOSTVAR@`/`@P@`/`@SESS_P@`/`@CAP_P@`/`@SPECVAR@`
      placeholders at this stage; `hurl/steps.py`'s `requires`/`provides` are
      declared in that same raw form (`"@P@_xsrf_token"`, not
      `"pdga_xsrf_token"`).
- [x] **Step 2:** assert per step: `provides` **equals** the template's capture
      set, exactly. Not a superset — an undeclared capture is a value Plan B
      would silently fail to thread.
- [x] **Step 3:** assert per step: every `{{name}}` the template references is
      either in `requires`, or in `provides` (a step may capture then use), or
      in the known set of `vars.env` globals (`cs_host`, admin credentials, the
      token PIN and so on). Anything else fails with the step id and the name.
- [x] **Step 4:** assert across the registry, in order: every `requires` is
      provided by an **earlier** step or is a global. This is the check that
      would have caught the hosted-client ordering bug of Task 3 Step 2 before a
      live run did, and it is the closest this plan gets to proving the sequence
      is executable one step at a time.

      Implementation note: three different `sub()`-parameter names
      (`@P@`/`@SESS_P@`/`@CAP_P@`) can each carry the same *kind* of
      per-member identifier depending on which session/namespace is in scope
      at a given call site (a member's own session vs. a hosting member's
      session vs. where a capture lands — see `ss.sign_key_csr`'s two
      distinct prefixes). A literal string-equality ordering check would
      never recognise `@SESS_P@_xsrf_token` as satisfied by
      `ss.bringup_init`'s `@P@_xsrf_token`. Resolved with `_canon()`, which
      collapses any leading `@UPPER_CASE@` token to one placeholder before
      comparing — a structural "was a `<member>_xsrf_token` captured
      earlier" check, not a full per-instantiation verifier (that's what
      `tests/test_golden.py`'s byte-identical corpus proves instead).
- [x] **Step 5:** run it. Expect failures — the declarations written in Tasks
      1–3 were written by hand. Fix the **declarations**, never the templates;
      a template edit at this point breaks byte-identity and the golden test
      will say so. Commit.

      Result: **zero failures on the first run.** Per this step's own
      warning ("a checker that finds nothing is more likely broken than
      vindicated"), this was verified rather than accepted at face value —
      the declarations in Tasks 1–3 were written by transcribing each
      template's actual `{{...}}`/`[Captures]` content by hand as each step
      was added (not guessed independently beforehand), so a clean first run
      reflects that workflow rather than a checker with no teeth. Confirmed
      the checker does have teeth: two deliberately-broken variants of
      `hurl/steps.py` (a wrong `provides` set; a dropped `provides` that
      breaks ordering) were run against the suite and both were caught,
      then reverted before committing.

## Task 5: 409-safety audit, probes for the ambiguous few, close out

**Files:** `hurl/steps.py`, `tests/test_steps.py`, `hurl/README.md`, `docs/superpowers/specs/2026-08-01-member-join-api-design.md`

- [x] **Step 1:** walk the registry and classify every step: (a) read-only, (b)
      mutation that returns `409` on repeat, (c) mutation where `409` would be
      ambiguous, (d) mutation that is not safe to repeat at all. Record the
      classification as a field or a comment per step, with the reason.

      Result: 3 (a), 10 (b), 8 (c), 0 (d) of 21 steps. Recorded as a one-line
      comment above each `Step` in `hurl/steps.py`, plus a legend at the top
      of `REGISTRY`.
- [x] **Step 2:** for class (c) only, add a `probe` — a template that answers
      "has this already happened?" — and declare it in the registry. Expect this
      to be a small number of steps. If it turns out to be most of them, stop
      and say so in the spec: that would invalidate design decision §5.3 and is
      worth knowing before Plan B builds on it.

      8 of 21 (38%) needed one — more than "small", short of "most" (this
      plan's own threshold for stopping). Recorded in the spec, not treated
      as invalidating §5.3. Wrote 8 `PROBE_*.hurl.tmpl` fragments and a new
      `Step.probe` field, then validated all 8 **live**: extracted the real
      Central/Security Server OpenAPI specs from the running images
      (`docker cp` + `unzip` the `openapi-model-*.jar`), confirmed field
      names against them, then ran the actual pinned `hurl` image against a
      real deployed federation end to end (`docker run ... hurl --test`).
      One endpoint assumption was wrong and fixed from the live response
      (`cs.signing_keys`'s probe: no `/configuration-sources` list exists;
      the Central Server's own signing keys are on its token, `GET
      /tokens`). One structural finding surfaced only by testing against the
      lite profile's `hosted_on` topology: a shared host's token carries one
      identically-labelled SIGN key **per hosted member**, so
      `ss.sign_key_csr`'s probe must correlate by the certificate's
      `owner_id`, not by label alone — documented in that probe's own
      comment.
- [x] **Step 3:** for class (d), if any exist, they cannot be resumed onto and
      Plan B must not try. Add a test asserting class (d) is empty, or, if it is
      not, that every class (d) step is flagged so Plan B's runner can refuse to
      resume across it. An unresumable step that nobody flagged is the worst
      outcome this plan can produce.

      Class (d) is empty. Added `Step.unsafe_to_repeat` (defaults `False`)
      and `tests/test_steps.py::test_no_step_is_unsafe_to_repeat`, plus
      `test_ambiguous_steps_have_a_probe` (every declared `probe` path
      exists on disk).
- [x] **Step 4:** `scripts/verify.sh --fast` green. Then the real proof:
      `scripts/teardown.sh --purge` → `scripts/verify.sh --full` under
      `profile: lite`, green. The golden corpus proves the bytes are identical;
      only a deploy proves the bytes still stand a federation up. Once, at the
      end, not per task.

      Both green. `--full`'s first acceptance run hit a transient 2.6.2
      failure (empty cross-server response); re-running the same, unchanged
      acceptance suite against the same already-deployed stack passed
      cleanly — the already-documented asynchronous-propagation flake
      (`scripts/acceptance.sh`'s own comment on `fetch_retry`, "confirmed
      live at P5"), not a regression from this plan's refactor. Every
      Hurl-scenario-driven module (2.1 through 2.6.1 — everything Tasks 1-4
      touched) passed on the first run. Console smoke also green. Left the
      federation running afterward (known-good state) rather than tearing it
      down unprompted.

      Bonus, opportunistic given the live stack was already up: resolved
      design spec §"Open questions" item 3 (Hurl JSON capture extraction) —
      ran `--report-json` against a real request and confirmed
      `entries[].captures` is a `{name, value}` array per `[Captures]` block,
      exactly the granularity Plan B's executor needs. See that section.
- [x] **Step 5:** document in `hurl/README.md`: what `hurl/steps.py` is, that
      step ids are stable identifiers other things persist, that `requires`/
      `provides` are Hurl runtime names and not `@name@` tokens, and that
      `tests/test_steps.py` is what keeps the declarations honest.
- [x] **Step 6:** record the outcome in the design spec — the actual step count,
      the class (c) list from Step 2, and whether Step 3 found anything. §5.3's
      claim that probes are rare is a prediction; replace it with the measured
      answer. Commit.

---

## Sequencing

Strictly in order. Tasks 2 and 3 both depend on Task 1's dataclass; Task 4's
checker only makes sense once every step exists; Task 5's audit needs Task 4
green or it will be auditing declarations that are wrong.

**Do not interleave with any other plan that edits `generate.py`** —
`2026-07-28-kp2-simplification.md` Task 1 touches the same file. Land one
fully, commit, then start the other.

**Risk:** medium, fully mitigated. Every conversion is mechanical and checked
against a byte-exact baseline plus a golden corpus that names the first
differing line. The realistic failure is not a broken federation — the corpus
catches that — it is a `requires`/`provides` declaration that is wrong in a way
Task 4 does not catch because the template's shape defeated the regex. If Task
4 Step 5 passes on the first run with no fixes needed, be suspicious: hand-written
declarations across ~30 steps do not come out right first time, and a checker
that finds nothing is more likely broken than vindicated.
