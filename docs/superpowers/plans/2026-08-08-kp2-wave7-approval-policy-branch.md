# KP2 — Wave 7: the approval-policy branch (deferred from Wave 6, OD-1b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. **Prerequisites: Wave 6 complete and committed (in particular Task 6, which deletes `join-policy.yaml`'s duplicate `approval` key — this wave extends the *surviving* key in `configs/x-road-bus/federation-core.yaml`). `--full` green and the current timings re-measured on the machine this wave will be measured on.**

> **Simplicity and security pass, 2026-08-08.** Applied after review. Two
> simplifications — `provides` does not become policy-aware (Task 3), and there
> is no second golden tree (Task 4). Two security controls added as design
> decisions 7 and 8: `automatic` must never be committable as the default, and
> the generated Central Server `local.ini` must be proven not to drop a packaged
> key.

**Goal:** make the onboarding path's §3 fact 1 — *"approval of registration
requests is an operator policy choice, automatic or manual, since v6.21.0"* —
**demonstrable and measured**, rather than described.

---

## Do not start this wave without a driver

This is the only plan in the pack that begins by arguing against itself, because
the failure mode here is specific: the work is interesting, the mechanism is
tractable, and none of that is a reason to do it.

**Not a driver:**

- A configuration key exists and is unread. (Wave 6 deleted it. That is the fix.)
- The path names the choice. The path names many things the pack correctly does
  not implement — a service catalogue, a second Central Server, a trust-service
  SLA. `docs/path-conformance.md` records those as named absences and that is a
  complete answer.
- It would be more "complete". Completeness against the path is not the pack's
  goal; teaching the modules is, and `S3.4` is honest as a named absence.

**A driver:**

- A Topic 5 subtopic teaches approval policy, or a framework reader asks what
  automatic approval costs and the answer needs to be measured rather than
  asserted.
- A funder or an operating authority is choosing between the two and the
  programme owes them a number.

**If there is no driver, close this plan unstarted.** Wave 6's
`production-delta.md` row already states the position honestly.

---

## What this wave is actually for

**Not a toggle. A measured contrast.** If the only outcome is that
`management_request_approval: automatic` works, the wave has produced a
configuration option and taught nothing — and it will have spent two `--full`
budgets doing it. The deliverable is the **comparison**: the same join, run both
ways, with the difference in wall-clock, in operator actions, and in what the
audit trail contains, written down.

That comparison is the thing the path's §3 fact 1 asserts and nobody has
measured: *"automatic approval collapses days into seconds and moves the control
to G0–G1, where arguably it belongs."* In a demo the *approval* latency is
seconds either way — a human is not waiting — so the honest finding may well be
that automatic approval saves almost nothing technically, and that its entire
value is organisational: it removes an operator action, and with it an audit
record. That would be a more useful result for a framework reader than a working
toggle.

**The default stays `explicit`, permanently, and is now enforced.**
`federation-core.yaml`'s comment states the reasoning and it survives this wave
intact: the explicit sequence *"works unchanged against a production Central
Server, where auto-approval would be unacceptable"*, and the approval step
*"stays visible in the run — which is also better teaching"*. Design decision 7
turns that from a comment into a test.

---

## Read this before starting: the four things that make it expensive

1. **It is a deploy-time branch, so `--fast` cannot prove it.** Auto-approval is
   a property of how the Central Server was started. Every meaningful assertion
   needs a live stack, and proving both values needs **two** `--full` runs from
   cold (~13 min each on the standard topology, plus teardown). Budget for that
   or do not start.
2. **The Central Server has no `local.ini` mount today.** `xroad-demo-local.ini`
   is bind-mounted over `/etc/xroad/conf.d/local.ini` on all four Security
   Servers (`docker-compose.yml` lines 70, 81, 92, 107) and on **no** Central
   Server. Adding one means meeting the trap the Security Server file already
   documents: the sidecar entrypoint does `cp -a -n … backup/local.ini`, writing
   the packaged defaults **only on first boot**, so a bind-mounted replacement
   must carry every default it displaces. The SS file carries
   `wsdl-validator-command` for exactly this reason. Design decision 8 makes
   this mechanical rather than a matter of care.
3. **Two templates lose a capture.** Under automatic approval nothing is ever
   `WAITING`, so the `GET …?status=WAITING` → `POST /{id}/approval` halves of
   `SS_BRINGUP_REGISTER.hurl.tmpl` and `MEMBER_CLIENT_REGISTER.hurl.tmpl` must be
   **omitted**, not skipped at runtime — the GET returns an empty `items` array
   and the capture `jsonpath "$.items[0].id"` fails the step.
4. **The retry budget was measured under explicit approval.**
   `R1_RETRY_BUDGET = 54` exists because `ss.client_register`'s propagation wait
   ate 95–107 s of a 120 s budget. Approval latency and **global-configuration
   propagation** are different things and only the first one disappears — so the
   budget probably does not shrink much. Re-measure; do not reason about it.

---

## Global Constraints

- **`explicit` remains the shipped default and the `--full` path**, enforced by
  a test (design decision 7).
- **`tests/golden/` under `explicit` must be byte-identical** before and after
  this wave.
- **No behaviour may depend on the policy value outside generation.**
  `apps/join-api/job.py` must not branch on it. If the join API needs to know
  the approval mode, the design is wrong — the whole point is that the
  difference is baked into the generated scenarios at deploy time.
- **Every task updates `docs/path-conformance.yaml` and re-renders.**
- Commit after every task.

---

## Design decisions

1. **`policy.management_request_approval` accepts exactly two values**, and
   `check_policy()` keeps rejecting everything else — including the
   `policy.auto_approve` key it already rejects by name. Widening a validator
   from "one legal value" to "two legal values" must not widen it to "any
   value".

2. **The branch lives entirely in `hurl/generate.py`.** It selects a template
   per step from the policy; `steps.py` stays a declarative registry and
   `job.py` never sees the policy. This is what keeps global constraint 3 true.

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
   a contract nothing reads. Note the divergence in the AUTO templates' header
   comments instead.

5. **The automatic templates are new files, not conditionals inside the
   existing ones.** `fragments/SS_BRINGUP_REGISTER_AUTO.hurl.tmpl` and
   `fragments/MEMBER_CLIENT_REGISTER_AUTO.hurl.tmpl` — each is the existing
   file's first block and nothing else. The templates are read by humans
   comparing the two paths; a conditional would obscure exactly the difference
   this wave exists to show.

6. **The generated Central Server `local.ini` is generated, not committed by
   hand.** It is derived from the policy value, like every other artefact under
   `hurl/`. Committing a hand-written CS ini beside a generated SS one would
   reintroduce the drift class Wave 6 spent its effort removing.

7. **`automatic` must never be committable as the default.** *(Security.)*
   Auto-approval means any Security Server that can reach the Central Server has
   its registration approved with no human in the loop — the setting
   `federation-core.yaml` itself calls *"unacceptable"* in production. The pack's
   own gap analysis names the threat: *"a learner who reuses the pack as a
   template inherits the omission."* A warning comment in a generated file is
   not a control against someone cloning the repository. A test asserting that
   the **committed** `configs/x-road-bus/federation-core.yaml` says `explicit`
   is three lines and is the control.

8. **Dropping a packaged Central Server default is a generate-time failure.**
   *(Security.)* "Carry every packaged default" as a prose instruction will be
   got wrong, and the first symptom would be a Central Server running with a
   security-relevant default silently absent. Make it mechanical: read the
   packaged `local.ini` out of the image, and fail generation if any key present
   in it is absent from the generated file.

9. **The measurement is an artefact, not a commit message.** Its home is
   `docs/production-delta.md` (which already carries the pack's measured
   figures) plus one row in `docs/path-conformance.md` via `S3.4`. A number that
   lives only in a terminal scrollback has not been produced.

---

## Task 1 — Establish the mechanism before building anything

**The cheapest possible failure here is discovering at Task 4 that the mechanism
is different from the one this plan assumes.** Two of the three possible answers
make the rest of the wave much smaller.

- [ ] **Step 1:** against a running Central Server, fetch its own OpenAPI
      description — `GET /api/v1/openapi.yaml`, which the path's §3 fact 3 notes
      the CS serves — and search it for a system-parameters, settings or
      management-request-policy endpoint. **If auto-approval is settable over
      the admin API, Tasks 3 and 4 collapse to one generated call and this wave
      becomes cheap.** Do not assume the `local.ini` route without looking; the
      pack's own history (the API-key assumption that did not survive contact
      with 7.7.0, `docs/xroad-770-notes.md` §1) is the argument for looking.
- [ ] **Step 2:** if there is no API route, confirm the flag names and section
      against the running container, not against memory: read
      `/etc/xroad/conf.d/local.ini` and the packaged defaults out of the CS
      image, and confirm `auto-approve-auth-cert-reg-requests`,
      `auto-approve-client-reg-requests` and `auto-approve-owner-change-requests`
      and the section they sit under. **Capture the packaged file verbatim** —
      it is the input to design decisions 6 and 8.
- [ ] **Step 3:** determine whether the flags are read at start-up only or on
      change. If a restart of `xroad-center` is required, say so in the plan
      record: it decides whether `automatic` is a redeploy-level or a
      runtime-level choice, and that distinction is half of what the wave is
      measuring.
- [ ] **Step 4 — write the finding down before writing any code.** Append it to
      `docs/xroad-770-notes.md` as a new numbered section, in the same form as
      that file's existing rows (assumption → what 7.7.0 actually does). If the
      finding is "the API route exists", **stop and revise this plan** before
      continuing; Tasks 3–5 are written against the file route.
- [ ] **Step 5:** commit the note. No code yet.

---

## Task 2 — Widen the validator to exactly two values, and pin the default

- [ ] **Step 1:** in `hurl/generate.py`'s `check_policy()`, accept
      `explicit | automatic` and keep rejecting everything else with the same
      named-value error. Keep the `policy.auto_approve` rejection exactly as it
      is — a different key, still wrong.
- [ ] **Step 2 — the security control (design decision 7).** Add
      `test_the_committed_federation_policy_is_explicit()` to
      `tests/test_join_policy.py`'s neighbourhood: the committed
      `configs/x-road-bus/federation-core.yaml` must say `explicit`. Write the
      failure message as the explanation — *`automatic` is a demonstration
      setting; a clone of this repository must not inherit a federation that
      approves registrations with no human in the loop.* This is the control;
      the file header comment in Task 4 is only the courtesy.
- [ ] **Step 3:** add `template_for(step, approval)` and the optional
      `template_auto` field to `Step` in `hurl/steps.py` (design decision 3),
      defaulting to `None` so every other step is unaffected. A step with no
      `template_auto` under `automatic` returns its normal template rather than
      raising — only two steps differ.
- [ ] **Step 4:** unit-test both in `tests/test_steps.py`: a third policy value
      is a hard failure; and `template_for` under `explicit` returns exactly what
      `BY_ID[...].template` returned before this wave, **for every step in the
      registry**. That second assertion is what protects the shipped default.
- [ ] **Step 5:** `--fast` green, `tests/golden/` unchanged. Commit. **Nothing
      has changed behaviourally yet, and that is the point** — the widening and
      its guard land separately from the thing they enable.

---

## Task 3 — The two automatic templates

- [ ] **Step 1:** create `hurl/templates/fragments/SS_BRINGUP_REGISTER_AUTO.hurl.tmpl`
      — the `PUT …/token-certificates/{hash}/register` block and its `HTTP 204`
      assertion, and nothing after it.
- [ ] **Step 2:** the same for
      `hurl/templates/fragments/MEMBER_CLIENT_REGISTER_AUTO.hurl.tmpl` — the
      `PUT …/clients/{id}/register` block and its `HTTP 204`.
- [ ] **Step 3:** head each file with a comment naming the file it is the
      counterpart of, stating why the approval half is absent, and — per design
      decision 4 — that the step's declared `provides`
      (`@P@_auth_cert_req_id` / `@CAP_P@_client_req_id`) is not produced under
      this rendering, which is safe because nothing consumes it: `requires` is
      enforced at runtime, `provides` is not. **Do not make `provides`
      policy-aware.** Confirm the "nothing consumes it" claim with a grep across
      `hurl/` and `apps/` as part of this step rather than trusting this
      sentence.
- [ ] **Step 4:** wire both onto their steps' `template_auto`.
- [ ] **Step 5:** the probes (`PROBE_SS_BRINGUP_REGISTER`,
      `PROBE_SS_CLIENT_REGISTER`) are **unchanged** — they read status, and
      `REGISTERED` is still the answer; only the window in which
      `REGISTRATION_IN_PROGRESS` is observable shrinks. Assert this rather than
      editing them, and note it in the header comments.
- [ ] **Step 6:** `--fast`, `tests/golden/` unchanged under `explicit`. Commit.

---

## Task 4 — Generate the Central Server configuration

Skip entirely if Task 1 found an admin-API route; do that instead.

- [ ] **Step 1:** teach `hurl/generate.py` to emit a CS `local.ini` **only under
      `automatic`** (design decision 6), carrying the three flags and every
      packaged default Task 1 Step 2 captured. Head the generated file with the
      same demo-only warning `xroad-demo-local.ini` carries, plus one line
      stating that auto-approval is unacceptable in a production federation.
- [ ] **Step 2 — the security control (design decision 8).** In the same
      generator, read the packaged CS `local.ini` and **fail generation if any
      key present in it is absent from the generated file**. Commit the packaged
      file as a fixture so the check runs without a container. This is what turns
      the `cp -a -n` trap from a thing to remember into a thing that cannot
      happen.
- [ ] **Step 3:** add the conditional bind-mount to the Central Server service.
      Follow whatever mechanism `hurl/compose.members.yml` already uses for
      generated compose fragments rather than adding a second one.
- [ ] **Step 4:** if Task 1 Step 3 found a restart is needed, handle it in
      `scripts/deploy.sh` — and prefer starting the CS with the file already in
      place over restarting it, so the deploy stays a single cold path.
- [ ] **Step 5 — no second golden tree.** `tests/golden/` covers the shipped
      configuration; a full parallel tree for an unshipped variant is machinery
      that must then be maintained. Assert instead, at unit level, that the two
      AUTO fragments render to the expected text and that the generated CS
      `local.ini` contains the three flags. **Task 5's live run is the real
      proof** — this is a deploy-time branch and no static tree could have
      proven it anyway.
- [ ] **Step 6:** `--fast` green under both values; `tests/golden/` (the
      `explicit` tree) byte-identical. Commit.

---

## Task 5 — Prove it live, both ways, and measure

**This is the task the wave exists for.** Tasks 2–4 are plumbing; if the wave
stops before this one it has produced a configuration option and no knowledge.

- [ ] **Step 1:** `--full` from cold under `explicit`. Record: total deploy
      time, and the wall-clock of a hosted join and an own-server join measured
      the way `README.md` already reports them. This is the control,
      re-measured on today's machine rather than quoted from the README.
- [ ] **Step 2:** `--full` from cold under `automatic`. Record the same three
      numbers. Confirm the federation actually stands up — every Security Server
      `REGISTERED` and active with no approval call anywhere in the run.
- [ ] **Step 3:** run a real join both ways (`apps/join-api`: submit → approve →
      `ACTIVE, verified: true`) and record the difference. Note explicitly
      whether `R1_RETRY_BUDGET`'s 95–107 s propagation wait shrank, stayed, or
      moved. **Expect it to stay** — approval latency and global-configuration
      propagation are different clocks — and if it does, that is the finding.
- [ ] **Step 4 — the audit-trail difference, which is the real one.** Under
      `explicit`, `GET /management-requests` on the Central Server shows an
      approved request with an approver and a timestamp. Under `automatic`,
      record what it shows instead. The path's §3 fact 3 notes that
      management-request origin IPs are carried into the CS audit log *"which is
      what makes an automated join auditable"* — check whether that still holds
      when nobody approved anything. **If the audit record is materially thinner
      under `automatic`, that is the most important sentence this wave will
      produce**, and it belongs in the v0.3 amendment note as well as here.
- [ ] **Step 5:** write it up — a `docs/production-delta.md` section with the
      table, and the honest conclusion whichever way it falls. If the technical
      saving is negligible, say so plainly: the path's *"collapses days into
      seconds"* is then a claim about the **organisational** wait at G0–G1, not
      about the technical sequence, and the pack has the measurement to say
      which.
- [ ] **Step 6:** restore `explicit` in the committed config — Task 2 Step 2's
      test will fail the commit otherwise, which is the control working. Update
      `S3.4` from `named-absence` to `implemented`, citing `template_for`, both
      AUTO templates and the measurement section. Re-render. Commit.

---

## Task 6 — Make the choice visible where it is taught

Only if Task 5's finding warrants it. A measurement nobody encounters is a
measurement nobody has.

- [ ] **Step 1:** one paragraph in `runbook.md` — how to switch, what it costs,
      and that `explicit` is the shipped default and why.
- [ ] **Step 2:** one line in `README.md`'s deployment description.
- [ ] **Step 3:** if Task 5 Step 4 found an audit-trail difference, add it to
      `docs/GEATDM-Interop-Member-Onboarding-Path-v0.3-amendments.md` as a new
      amendment against §3 fact 1 — the path currently presents the choice as a
      time/control trade and would be stronger presenting it as a
      time/control/**evidence** trade.
- [ ] **Step 4:** `--fast`. Commit.

---

## How to abandon this cleanly

If Task 1 finds the `local.ini` route requires a Central Server restart
mid-deploy that the single-host topology cannot absorb, or if Task 5's
`automatic` run cannot be made reliable in the time budget, **stop and revert to
Wave 6's position** rather than shipping a half-branch:

- Revert Tasks 3–4. Keep Task 2 Step 2's default-pinning test **either way** —
  it costs three lines and guards a setting that will be discussed again.
- Revert Task 2's widening unless a second value is actually reachable;
  otherwise it becomes the same decoration Wave 6 deleted.
- Keep Task 1's `docs/xroad-770-notes.md` finding. **It is worth the whole wave
  on its own** — it converts "the flags exist" from something read in release
  notes into something observed at 7.7.0, which is exactly what that document is
  for.
- Leave `S3.4` as a named absence, and add the abandonment reason to its note.
  A named absence with a measured reason behind it is a stronger artefact than
  most implementations.

## Sequencing

Task 1 → 2 → 3 → 4 → 5 → 6, strictly. Task 1 can invalidate the rest and must
finish first. Tasks 2 and 3 are inert without 4. Task 5 is the deliverable.
Task 6 is conditional on Task 5's result.

## Exit

- `policy.management_request_approval` accepts exactly `explicit` and
  `automatic`, and a third value is still a hard generate-time failure.
- The **committed** configuration says `explicit`, and a test fails if it ever
  does not.
- The generated Central Server `local.ini` cannot silently drop a packaged key.
- Both values have stood a federation up from cold and carried a real join to
  `ACTIVE, verified: true`.
- `tests/golden/` under `explicit` is byte-identical to its pre-wave state, and
  no second golden tree was created.
- `docs/production-delta.md` carries the measured comparison — wall-clock,
  operator actions, and audit-trail content — with a stated conclusion.
- `S3.4` reads `implemented`, citing the measurement rather than the mechanism.
