# KP2 — Wave 7: the approval-policy branch

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. **Do not start without reading the gate below — this plan is an option, not queued work.**
>
> **Prerequisites:** (1) `docs/superpowers/plans/2026-08-08-kp2-approval-policy-spike.md` complete, with its recommendation written into this file's gate section; (2) Wave 6 complete and committed, in particular Task 6 which deletes `join-policy.yaml`'s duplicate `approval` key — this wave extends the *surviving* key in `configs/x-road-bus/federation-core.yaml`; (3) `--full` green.

> **Restructured, 2026-08-08.** The investigation and the measurement moved out
> of this plan into a separate spike, because they produce knowledge the
> framework wants regardless of whether the pack ever ships a switch, and gating
> them behind this plan's start gate suppressed the one thing that made the work
> valuable. **What remains here is only the switchable capability.** Also
> applied: two simplifications (`provides` does not become policy-aware; no
> second golden tree), two security controls (design decisions 7 and 8), and the
> comment guardrail.

**Goal:** let a reader set `policy.management_request_approval: automatic` in
`configs/x-road-bus/federation-core.yaml`, redeploy, and get a working
federation — so the onboarding path's §3 fact 1 is a thing the pack can *do*,
not only a thing it has measured.

---

## The gate

**The spike already harvested the knowledge.** What this plan adds is the
ability to *switch*, and switching has a permanent cost: a second code path in
`hurl/generate.py` and two extra templates that `--fast` can never prove,
maintained for as long as the pack exists, in a pack whose stated scope is
"Education only, demo only".

**Decision owner: the framework owner, not the implementer.** This is the same
rule Wave 6's open decisions carry, and it is stated because the earlier version
of this gate was self-certifiable — anyone motivated to do the work could read
the list below, nod, and proceed, which is not a control. If you are executing
this plan and did not receive the decision from the framework owner, stop and
ask.

**Run this plan only if the spike's recommendation says so.** Its three
outcomes:

| Spike found | Do |
| --- | --- |
| An admin-API route exists | **Rewrite this plan first.** Tasks 3 and 4 collapse to one generated call; the cost that motivates this gate largely disappears. |
| A real saving, and someone needs to switch | Run this plan as written. |
| The saving is negligible | **Close this plan unstarted.** The spike produced everything of value; `S3.4` stays a named absence with a measurement behind it. |

**Not a driver, in any outcome:** that the path names the choice, or that the
pack would be more "complete". The path names a service catalogue, a second
Central Server and a trust-service SLA too; `docs/path-conformance.md` records
those as named absences and that is a complete answer.

> **Spike recommendation:** _(the spike writes it here — run / rewrite / close,
> and why. If this line still reads like this, the spike has not been done and
> this plan is not ready to start.)_

---

## Read this before starting: what makes it expensive

1. **It is a deploy-time branch, so `--fast` cannot prove it.** Every meaningful
   assertion needs a live stack, and proving both values needs **two** `--full`
   runs from cold (~13 min each, plus teardown).
2. **The Central Server has no `local.ini` mount today.** `xroad-demo-local.ini`
   is bind-mounted over `/etc/xroad/conf.d/local.ini` on all four Security
   Servers (`docker-compose.yml` lines 70, 81, 92, 107) and on **no** Central
   Server. The sidecar entrypoint writes packaged defaults **only on first
   boot**, so a bind-mounted replacement must carry every default it displaces.
   Design decision 8 makes that mechanical. The spike captured the packaged file
   — use it rather than re-deriving it.
3. **Two templates lose a capture.** Under automatic approval nothing is ever
   `WAITING`, so the `GET …?status=WAITING` → `POST /{id}/approval` halves of
   `SS_BRINGUP_REGISTER.hurl.tmpl` and `MEMBER_CLIENT_REGISTER.hurl.tmpl` must be
   **omitted**, not skipped at runtime. The spike observed this failing, which is
   why separate templates rather than a runtime skip.
4. **The retry budget was measured under explicit approval.** The spike has the
   comparison; carry its number into Task 5 rather than re-deriving it.

---

## Global Constraints

- **`explicit` remains the shipped default and the `--full` path**, enforced by
  a test (design decision 7).
- **`tests/golden/` under `explicit` must be byte-identical** before and after
  this wave.
- **No behaviour may depend on the policy value outside generation.**
  `apps/join-api/job.py` must not branch on it. If the join API needs to know
  the approval mode, the design is wrong — the difference is baked into the
  generated scenarios at deploy time.
- **Comments state the reason, not its provenance** — see below.
- **Every task updates `docs/path-conformance.yaml` and re-renders.**
- Commit after every task.

### Comment guardrail (applies to every task in this wave)

A comment explains why the code is the way it is, **in the code's own terms**. It
does not cite where the decision was recorded. Provenance belongs in git history
and in `docs/decisions/`.

**Do not write, in any code file or template:** a path to `docs/superpowers/**`,
a plan or wave or spike name, `spec S<n>`, `design decision <n>`, `decision <n>`,
`join-<x> plan`, `P<n>` — including the numbered decisions in *this* plan.

**Do write:** the reason itself; a path to a **Reference** document
(`docs/production-delta.md`, `docs/conventions.md`, `docs/decisions/xroad-770-notes.md`)
where the reader genuinely needs it; an upstream ticket or version
(`XRDDEV-1960`, `7.7.0`); a sibling source file the reader must open anyway.

**The test:** if the cited document were deleted, would the comment still be
useful? If not, inline the reason. And if a comment is longer than the code it
explains, the explanation belongs in a Reference document and the comment is one
sentence plus a pointer.

This matters here specifically: the two AUTO templates in Task 3 are read by
humans comparing the two approval paths. A header comment that says *"the
approval half is absent because nothing is ever WAITING under auto-approval"* is
useful; one that says *"per Wave 7 design decision 5"* sends the reader to a
document that will be frozen in `docs/decisions/` and eventually wrong.

---

## Design decisions

1. **`policy.management_request_approval` accepts exactly two values**, and
   `check_policy()` keeps rejecting everything else — including the
   `policy.auto_approve` key it already rejects by name. Widening a validator
   from "one legal value" to "two legal values" must not widen it to "any
   value".

2. **The branch lives entirely in `hurl/generate.py`.** It selects a template
   per step from the policy; `steps.py` stays a declarative registry and
   `job.py` never sees the policy.

3. **A second template on the existing `Step`, not a second `Step`.** Add an
   optional `template_auto: str | None = None` field to the frozen `Step`
   dataclass and a `template_for(step, approval)` helper. **Do not add
   `ss.bringup_register.auto` as a new registry entry.** Step ids are persisted
   in `out/join/*.json` job records and read back on resume; forking an id
   breaks resume for any job in flight across the change, and would also need
   handling in `REVERSAL_ORDER` and `BY_ID`. One logical step, two renderings.

4. **`provides` stays as it is, with a comment.** Verified: `requires` **is**
   enforced at runtime (`job.py:1023` checks every name against the variable
   map), `provides` is **not** — it is used for substitution and for an
   `endswith("_xsrf_token")` session test (`job.py:188`) that the two `req_id`
   captures do not match. Making `provides` policy-aware would be machinery for
   a contract nothing reads.

5. **The automatic templates are new files, not conditionals inside the
   existing ones.** Each is the existing file's first block and nothing else.
   The templates are read by humans comparing the two paths; a conditional would
   obscure exactly the difference this wave exists to make switchable.

6. **The generated Central Server `local.ini` is generated, not committed by
   hand.** Derived from the policy value, like every other artefact under
   `hurl/`.

7. **`automatic` must never be committable as the default.** *(Security.)*
   Auto-approval means any Security Server that can reach the Central Server has
   its registration approved with no human in the loop — the setting
   `federation-core.yaml` itself calls *"unacceptable"* in production, and the
   pack's own gap analysis names the threat: *"a learner who reuses the pack as
   a template inherits the omission."* A warning comment in a generated file is
   not a control against someone cloning the repository. A test asserting the
   **committed** config says `explicit` is three lines and is the control.

8. **Dropping a packaged Central Server default is a generate-time failure.**
   *(Security.)* "Carry every packaged default" as a prose instruction will be
   got wrong, and the first symptom would be a Central Server running with a
   security-relevant default silently absent. Read the packaged `local.ini`
   (captured by the spike, committed as a fixture) and fail generation if any
   key present in it is absent from the generated file.

---

## Task 1 — Widen the validator to exactly two values, and pin the default

- [ ] **Step 1:** in `hurl/generate.py`'s `check_policy()`, accept
      `explicit | automatic` and keep rejecting everything else with the same
      named-value error. Keep the `policy.auto_approve` rejection exactly as it
      is — a different key, still wrong.
- [ ] **Step 2 — the security control.** Add
      `test_the_committed_federation_policy_is_explicit()` near
      `tests/test_join_policy.py`: the committed
      `configs/x-road-bus/federation-core.yaml` must say `explicit`. Write the
      failure message as the explanation — *`automatic` is a demonstration
      setting; a clone of this repository must not inherit a federation that
      approves registrations with no human in the loop.* This is the control;
      Task 3's file header is only the courtesy.
- [ ] **Step 3:** add `template_for(step, approval)` and the optional
      `template_auto` field to `Step` in `hurl/steps.py`, defaulting to `None`
      so every other step is unaffected. A step with no `template_auto` under
      `automatic` returns its normal template rather than raising — only two
      steps differ.
- [ ] **Step 4:** unit-test both in `tests/test_steps.py`: a third policy value
      is a hard failure; and `template_for` under `explicit` returns exactly what
      `BY_ID[...].template` returned before this wave, **for every step in the
      registry**. That second assertion is what protects the shipped default.
- [ ] **Step 5:** `--fast` green, `tests/golden/` unchanged. Commit. **Nothing
      has changed behaviourally yet, and that is the point** — the widening and
      its guard land separately from the thing they enable.

---

## Task 2 — The two automatic templates

- [ ] **Step 1:** create `hurl/templates/fragments/SS_BRINGUP_REGISTER_AUTO.hurl.tmpl`
      — the `PUT …/token-certificates/{hash}/register` block and its `HTTP 204`
      assertion, and nothing after it.
- [ ] **Step 2:** the same for
      `hurl/templates/fragments/MEMBER_CLIENT_REGISTER_AUTO.hurl.tmpl` — the
      `PUT …/clients/{id}/register` block and its `HTTP 204`.
- [ ] **Step 3:** head each file with a comment naming its counterpart and
      giving the reason in the code's own terms (see the comment guardrail):
      the approval half is absent because under auto-approval nothing is ever
      `WAITING`, so the pending-then-approve pair would capture nothing and fail
      the step; and the step's declared `provides` is therefore not produced
      under this rendering, which is safe because nothing consumes it —
      `requires` is enforced at runtime, `provides` is not. **Do not make
      `provides` policy-aware.** Confirm the "nothing consumes it" claim with a
      grep across `hurl/` and `apps/` as part of this step rather than trusting
      this sentence.
- [ ] **Step 4:** wire both onto their steps' `template_auto`.
- [ ] **Step 5:** the probes (`PROBE_SS_BRINGUP_REGISTER`,
      `PROBE_SS_CLIENT_REGISTER`) are **unchanged** — they read status, and
      `REGISTERED` is still the answer; only the window in which
      `REGISTRATION_IN_PROGRESS` is observable shrinks. Assert this rather than
      editing them.
- [ ] **Step 6:** `--fast`, `tests/golden/` unchanged under `explicit`. Commit.

---

## Task 3 — Generate the Central Server configuration

Skip entirely if the spike found an admin-API route; do that instead, and
rewrite this task before starting it.

- [ ] **Step 1:** commit the packaged Central Server `local.ini` the spike
      captured as a test fixture. Everything below depends on it, and capturing
      it here rather than reaching into a container keeps `--fast` container-free.
- [ ] **Step 2:** teach `hurl/generate.py` to emit a CS `local.ini` **only under
      `automatic`**, carrying the three flags and every packaged default from
      Step 1's fixture. Head the generated file with the same demo-only warning
      `xroad-demo-local.ini` carries, plus one line stating that auto-approval
      is unacceptable in a production federation.
- [ ] **Step 3 — the security control (design decision 8).** In the same
      generator, fail generation if any key present in the packaged fixture is
      absent from the generated file. This turns the first-boot-defaults trap
      from a thing to remember into a thing that cannot happen.
- [ ] **Step 4:** add the conditional bind-mount to the Central Server service.
      Follow whatever mechanism `hurl/compose.members.yml` already uses for
      generated compose fragments rather than adding a second one.
- [ ] **Step 5:** if the spike found a restart is needed, handle it in
      `scripts/deploy.sh` — and prefer starting the CS with the file already in
      place over restarting it, so the deploy stays a single cold path.
- [ ] **Step 6 — no second golden tree.** `tests/golden/` covers the shipped
      configuration; a full parallel tree for an unshipped variant is machinery
      that must then be maintained. Assert instead, at unit level, that the two
      AUTO fragments render to the expected text and that the generated CS
      `local.ini` contains the three flags and every fixture key. **Task 4's
      live run is the real proof** — this is a deploy-time branch and no static
      tree could have proven it anyway.
- [ ] **Step 7:** `--fast` green under both values; `tests/golden/` (the
      `explicit` tree) byte-identical. Commit.

---

## Task 4 — Prove the generated branch live

The spike measured the *hand-flipped* stack. This task proves the *generated*
one, which is the only thing the spike could not do.

- [ ] **Step 1:** `--full` from cold under `automatic`. Confirm the federation
      stands up — every Security Server `REGISTERED` and active, with no
      approval call anywhere in the run.
- [ ] **Step 2:** run a real join (submit → approve → `ACTIVE, verified: true`).
      Compare against the spike's hand-flipped numbers: they should agree. **If
      they do not, the generated path differs from the hand-flipped one and that
      difference is the finding** — chase it rather than recording the new
      number.
- [ ] **Step 3:** `--full` from cold under `explicit`, to prove the default path
      is undamaged. This is the run that matters most: the wave's risk is not
      that `automatic` fails loudly, it is that `explicit` changes quietly.
- [ ] **Step 4:** restore `explicit` in the committed config — Task 1 Step 2's
      test will fail the commit otherwise, which is the control working.
- [ ] **Step 5:** update `docs/production-delta.md`'s spike section with one
      paragraph: the switch is now a config value plus a redeploy, and the
      numbers held. Update `S3.4` from `named-absence` to `implemented`, citing
      `template_for`, both AUTO templates and the generator check. Re-render.
      Commit.

---

## Task 5 — Make the choice visible where it is taught

Only if Task 4 held. A capability nobody can find is a capability nobody has.

- [ ] **Step 1:** one paragraph in `runbook.md` — how to switch, what it costs,
      and that `explicit` is the shipped default and why.
- [ ] **Step 2:** one line in `README.md`'s deployment description.
- [ ] **Step 3:** `--fast`. Commit.

---

## How to abandon this cleanly

If Task 3 finds the `local.ini` route requires a Central Server restart
mid-deploy that the single-host topology cannot absorb, or if Task 4's
`automatic` run cannot be made reliable in the time budget, **stop rather than
shipping a half-branch**:

- Revert Tasks 2–3. Keep Task 1 Step 2's default-pinning test **either way** —
  three lines guarding a setting that will be discussed again.
- Revert Task 1's widening unless a second value is actually reachable;
  otherwise it becomes the same decoration Wave 6 deleted.
- Leave `S3.4` as a named absence with the spike's measurement behind it, and
  add the abandonment reason to its note. **Nothing of value is lost by
  abandoning here** — that is what moving the spike out of this plan bought.

## Sequencing

Task 1 → 2 → 3 → 4 → 5, strictly. Tasks 1 and 2 are inert without 3. Task 4 is
the proof. Task 5 is conditional on Task 4.

## Exit

- `policy.management_request_approval` accepts exactly `explicit` and
  `automatic`, and a third value is still a hard generate-time failure.
- The **committed** configuration says `explicit`, and a test fails if it ever
  does not.
- The generated Central Server `local.ini` cannot silently drop a packaged key.
- Both values have stood a federation up from cold and carried a real join to
  `ACTIVE, verified: true`, and the `automatic` numbers agree with the spike's.
- `tests/golden/` under `explicit` is byte-identical to its pre-wave state, and
  no second golden tree was created.
- No code file or template cites a plan, a spike, a wave or a numbered decision.
- `S3.4` reads `implemented`.
