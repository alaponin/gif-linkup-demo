# KP2 — Join, Plan C: own servers and un-joining

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. This plan implements **Plan C** of `docs/superpowers/specs/2026-08-01-member-join-api-design.md` §15. **Prerequisites: Plans A and B complete, committed, and live-verified.**

**Goal:** a joining member can bring up its own Security Server on a federation
that is already running, and a joined member can be removed from that
federation through the API rather than by tearing the whole thing down.

**Read this before starting.** Plan C is the least-derisked work in the design
and it is sequenced last deliberately. Today the pack has **no** live
de-registration capability at all: `scripts/member.sh remove` says explicitly
that it "does not touch a running federation — the member stays registered
there until `scripts/teardown.sh --purge`". The X-Road admin-API sequences for
unregistering a subsystem, deleting a client, and removing a member are not
implemented anywhere in this pack and have never been exercised against 7.7.0
by anyone here.

So Task 1 is a **spike**, not an implementation, and the remaining tasks are
written against what the spike is expected to find. If it finds something else,
revise Tasks 2–5 before executing them rather than forcing the plan onto the
findings. A plan that has to be rewritten after its own discovery task is
working correctly.

**Architecture:** Plan A's registry gains the two per-step fields it
deliberately deferred (`reverse`, and `probe` on the steps reversal touches).
Plan B's job runner gains the `BLOCKED` state and a health poll.
`scripts/join-agent.sh` is a host-side one-liner an operator runs; the API
never touches Docker.

**Tech Stack:** unchanged from Plan B.

## Global Constraints

- **`hurl/topology.json` must be byte-identical after a join-then-unjoin
  cycle.** `_allocate_numbers()`'s determinism already guarantees this and Task
  9 of `2026-07-27-kp2-member-parameterisation.md` already proved it for the
  config-only case. Extending it to a live cycle is this plan's headline
  assertion.
- **Canonical members can never be removed.** `member.sh remove` already
  refuses them; the API delegates to it rather than reimplementing the check,
  and a test asserts the API refuses before it delegates.
- **Every reversal is probe-guarded.** A reversal that assumes the forward path
  completed cleanly fails in the most confusing possible way (spec §10). This
  is the case that justifies probes existing at all (spec §5.3) and the reason
  they were not added everywhere in Plan A.
- **`--fast` and `--live` must not grow.** Own-server joins and un-joins are
  `--full`-tier work; `--full` was ~918s full / ~370s lite before this plan.
  Measure the new figures in Task 5 rather than estimating them. If Task 6
  retires `profile: full`, that measurement becomes single-profile — take the
  two-profile figures in Task 5 Step 3 anyway, because they are the evidence
  Task 6's gate is judged on.
- Commit after every task.

## Design decisions

1. **Task 1 produces findings and fixtures, not features.** Its deliverable is
   a document plus recorded HTTP exchanges under
   `apps/join-api/tests/fixtures/xroad/`, using the existing
   `scripts/capture-xroad-fixtures.sh` and `scripts/mkfixture.py`. Everything
   after it is testable in `--fast` because of what it captured.
2. **The agent is a script, not a worker.** `scripts/join-agent.sh <key>` does
   a port check and a `docker compose up`. No queue, no callback, no polling
   loop — the API already polls the new server's `:4000` healthcheck before it
   can drive any admin call, and that poll *is* the completion signal (spec
   §6.1). This also depicts "the member stands up its own server" more honestly
   than a worker would.
3. ~~**Deletion goes through the same approval gate as registration.**~~
   **REFUTED by Task 1's spike (2026-08-02).** It does not. `PUT
   /clients/{id}/unregister` raises a `CLIENT_DELETION_REQUEST` that the
   Central Server **auto-processes**: it never appears in
   `?status=WAITING`, it carries no `status` field at all, and
   `POST /management-requests/{id}/approval` against it returns `403`.
   There is no approval gate and nothing to wait for. The pleasing symmetry
   was wrong; the un-join is synchronous and considerably cheaper than this
   decision assumed. See `docs/xroad-770-notes.md` §11, finding 1. (§7 of
   the same file had already found this for an own-server member; the spike
   confirms it for a hosted one, so it holds in both topologies.)
4. **`reverse` is added per step, in reversal order, with a test.** Not a
   speculative field on all thirty steps (spec §16.1). Only the steps a
   reversal actually walks get one, and Task 2 adds them in the order Task 1's
   spike established.
5. **Task 3 is what makes `profile: full` redundant, so Task 6 retires it —
   gated, on evidence.** `full`'s remaining job today is to be the pack's only
   exercise of a member's *own* Security Server certificate sequence
   (`docs/production-delta.md`: a lite-only cycle never proves PNIA's and
   MoEYS's). Once a member can stand up its own server through the join API,
   that arrangement is a runtime operation on a lite base rather than a
   deploy-time profile — a better mechanism, and the one production actually
   uses, where the member provisions their own infrastructure. The mechanical
   merge is already 90% done: `resolve_hosted_on_map()` expresses lite as a
   preset over the same profile-independent `hosted_on` a joining member uses.
   But `full` is deleted **after** Task 5 Step 2 proves the replacement, never
   before — removing the only known-good own-server reference while Tasks 1
   and 3 are still discovering that path is exactly backwards.

## Out of scope

Endpoint-level access rights (spec open question 7). Per-agency credentials.
Changing an existing member. Removing a canonical member — ever.

---

## Task 1: Spike — establish the de-registration sequence against a live stack

**Files:** `docs/xroad-770-notes.md`, `apps/join-api/tests/fixtures/xroad/`, this plan

No production code. The output is knowledge and fixtures, and the plan's
remaining shape depends on them.

- [ ] **Step 1:** stand up `profile: lite` from cold and join a throwaway
      member through Plan B's API, hosted on `ss-plr`. This is the subject.
- [ ] **Step 2:** by hand, against the admin APIs, establish and record the
      working sequence for: revoking an access right; disabling then deleting a
      service description; unregistering a subsystem (**and whether it raises a
      management request requiring CS approval, which design decision 3
      assumes**); deleting a client on the Security Server; deleting its SIGN
      key and certificate; removing the member and subsystem on the Central
      Server. Record the actual status codes, not the documented ones — this
      pack has already found three places where 7.7.0 disagrees with its own
      OpenAPI model.
- [ ] **Step 3:** for each of those, establish the **probe** — the read that
      answers "does this still exist?" — and what it returns after the reversal.
      This is what Task 2's reversals are guarded by.
- [ ] **Step 4:** determine what happens to a *hosted* member's SIGN key when
      its client is deleted but the hosting server keeps serving other members.
      This is the case Plan B's topology makes normal and it has no upstream
      precedent in `setup.hurl`, which only ever builds up.
- [ ] **Step 5:** capture every exchange as a fixture. ~~via
      `scripts/capture-xroad-fixtures.sh`~~ — that script is hardcoded for the
      console's ACL fixtures and is the wrong tool; the reusable part is
      `scripts/mkfixture.py`, driven directly (`curl -ksi ... | mkfixture.py
      <raw> <out.json> <context>`), writing `apps/join-api/tests/fixtures/
      xroad/unjoin.*.json`. Note those land in mkfixture's `{status, headers,
      body, captured, context}` shape, while that directory's existing
      join fixtures are sliced Hurl report elements — two shapes, one
      directory, because the forward path is driven by Hurl and the reversal
      was driven by curl. Fixtures are what let Tasks 2–4 be tested in
      `--fast` instead of requiring a live stack per iteration.
- [ ] **Step 6:** write the findings into `docs/xroad-770-notes.md` alongside
      the seven corrections that file already records, and **revise Tasks 2–5
      of this plan** against what was actually found. Commit the spike before
      writing any implementation.

**Done, 2026-08-02.** Findings: `docs/xroad-770-notes.md` §11. Fixtures:
`apps/join-api/tests/fixtures/xroad/unjoin.*.json` (16). Design decision 3 is
refuted and struck through above; Tasks 2, 3, 4, 5 and the Sequencing section
are revised against what was found. Clean live de-registration **is**
achievable, so the plan's stop branch did not fire.

## Task 2: `reverse` and `probe` on the steps reversal walks

**Files:** `hurl/steps.py`, `hurl/templates/`, `tests/test_steps.py`

One context: the registry. Plan A left two fields out on purpose; this task
adds them, informed by Task 1 rather than guessed.

**Task 1's answer, in one place.** Six calls, five registry steps, no waits,
no approval round. Full evidence in `docs/xroad-770-notes.md` §11; the
recorded exchanges are `apps/join-api/tests/fixtures/xroad/unjoin.*.json`.

| `reverse` on | Call | Status | `probe` reads | Absence looks like |
| --- | --- | --- | --- | --- |
| `service.acl` | `POST /clients/{id}/service-clients/{subject}/access-rights/delete` | 204 | `GET .../service-clients/{subject}/access-rights` | `404 service_client_not_found` |
| `service.publish` | `DELETE /service-descriptions/{id}` | 204 | `GET /clients/{id}/service-descriptions` | `200 []` |
| `ss.client_register` | `PUT /clients/{id}/unregister` | 204 | `GET /clients/{id}` | `200 status: DELETION_IN_PROGRESS` |
| `ss.client_add` | `DELETE /clients/{id}` | 204 | `GET /clients/{id}` | `404 client_not_found` |
| `ss.sign_key_csr` | `DELETE /keys/{key_id}` | 204 | `GET /token-certificates/{hash}` | `404 certificate_not_found` |
| `cs.members_member` | `DELETE /members/{member_id}` | 204 | `GET /clients?q=<member_code>` | `200 {"clients": []}` |

- [ ] **Step 1:** add `reverse: str | None` to the `Step` dataclass. Default
      `None` — most steps still do not have one, and that is correct.
- [ ] **Step 2:** write the reversal templates for the six calls above, one
      per commit, each verified against its `unjoin.*.json` fixture. Three
      calls the plan originally budgeted for do **not** exist and must not be
      written: no `PUT /service-descriptions/{id}/disable` before the delete
      (not a precondition — the delete 204s against an enabled, serving
      description), no Central-Server approval round after the unregister
      (design decision 3, refuted), and no `DELETE /subsystems/{id}` (the
      member delete cascades to it; a follow-up subsystem delete 404s).
- [ ] **Step 2b:** the reversal **order** is not `reversed(REGISTRY)`.
      Forward is `ss.client_add` → `ss.sign_key_csr` → `ss.client_register`;
      backwards, what was established live is `client_register` →
      `client_add` → `sign_key_csr` — the client goes before its key. Encode
      that order explicitly (Task 4 Step 2 walks it) rather than deriving it
      by reversing the registry, and say in a comment that the strict mirror
      was never tried.
- [ ] **Step 2c:** `ss.sign_key_csr`'s reversal needs the key id, and it must
      correlate on `keys[].certificates[].owner_id`, never on the label —
      `ss-plr` carries four keys all labelled `"Sign key"`, so a label match
      deletes another agency's key. `PROBE_SS_SIGN_KEY.hurl.tmpl` already
      documents this correlation for the forward path; reuse it, do not
      re-derive it. One `DELETE /keys/{key_id}` removes the key *and* its
      still-`REGISTERED` certificate — no separate certificate delete,
      disable or unregister.
- [ ] **Step 3:** add a `probe` to each of those steps if Plan A Task 5 did not
      already classify it as needing one. Reversal is the case probes exist for.
      Two of the six probes do **not** signal absence with a 404 and a test
      must not assume they do: the service-description probe returns `200 []`,
      and the Central-Server probe returns `200 {"clients": []}` (there is no
      `GET /subsystems/{id}` on the CS at all — it is `405`).
- [ ] **Step 4:** extend `tests/test_steps.py`: every step with a `reverse` has
      a `probe`; every reversal's `requires` is satisfiable from the job
      context a completed forward run would have produced. That second
      assertion is the one that catches a reversal needing a value the forward
      path never captured — the most likely defect in this task. Note the
      concrete instance Task 1 surfaced: the service-description **id** and
      the SIGN **key id** are both forward `[Captures]`, and the SIGN key id
      is also re-derivable from the token by `owner_id` — assert the reversal
      can get them, and prefer re-deriving the key id over trusting a captured
      one, because the reversal must work on a resumed job whose context may
      predate a re-issued key.
- [ ] **Step 5:** byte-identical check still applies. Adding fields and
      reversal templates must not change what `generate.py` emits for a cold
      deploy. `tests/test_golden.py` and `check_scenarios.py` green. Commit.

## Task 3: Own-server joins — `BLOCKED`, the health poll, and the agent

**Files:** `apps/join-api/job.py`, `apps/join-api/app.py`, `scripts/join-agent.sh` (new), `apps/join-api/tests/`, `apps/console/static/{app.js,style.css}`, `apps/console/tests/test_app_join.py`, `runbook.md`

One context: the member-side boundary. Everything here is about the API
correctly *not* doing something.

**Found in Task 1, before you run anything live: the join API does not work
from a git worktree.** `docker-compose.yml` bind-mounts the checkout at
`/repo` so `writer.apply_real()` can run `git status --porcelain` (spec S9).
In a worktree the checkout's `.git` is a *file* holding an absolute host path
(`gitdir: .../geatdm-framework/.git/worktrees/<name>`) that does not exist
inside the container, so git exits 128 and **every approval 409s** with "could
not check whether ... is a clean checkout". Since this plan's own tasks are
meant to be executed in worktrees, decide how to handle it before Step 1 —
Task 1 worked around it with a throwaway compose override adding
`- <abs>/geatdm-framework/.git:<abs>/geatdm-framework/.git`, which is enough
to prove the point but is not a fix. The candidates are: mount the resolved
gitdir alongside `/repo` in `docker-compose.yml`, or have `writer` treat a
git failure as "cannot tell" rather than "dirty" — but note the current
fail-safe behaviour (refuse when the check cannot run) was a deliberate
join-b review finding, so relaxing it needs its own argument.

- [ ] **Step 1:** make `BLOCKED` reachable. A job whose next step has
      `actor: member` and whose target Security Server does not answer its
      healthcheck enters `BLOCKED` rather than failing. Plan A Task 3 Step 4
      already set `actor` per step, so this is a lookup, not a new
      classification.
- [ ] **Step 2:** the health poll: the API polls the target server's `:4000`
      and leaves `BLOCKED` when it answers. No callback, no work-order
      endpoint — spec §6.1 and §16.1 record why they were removed.
- [ ] **Step 3:** `scripts/join-agent.sh <key>`: check the allocated host ports
      are actually free, then `docker compose -f docker-compose.yml -f
      hurl/compose.members.yml up -d --wait --wait-timeout <n> ss-<key>`. The
      compose fragment, its volumes and its healthcheck are **already
      generated** by `generate.py`, so this adds no topology code. Note
      `generate.py` already refuses the AirPlay range (5000–5099, 7000) by
      construction; this check is for everything else on a given machine.
- [ ] **Step 4:** decide and implement what a busy port means — job failure or
      re-allocation (spec open question 6). Re-allocation breaks the
      determinism the Global Constraint depends on, so the default should be
      failure with a message naming the port and the process holding it.
      Whichever is chosen, say so in `runbook.md`.
- [ ] **Step 5:** the console shows `BLOCKED` with the exact command the
      operator should run. A state whose exit condition is "a human runs a
      script" must name the script. Concretely, in `apps/console/static/app.js`:
      `.join-state-blocked` gets its own style in `style.css` alongside the
      existing per-state classes (the template already lowercases the state into
      a class name, so a new state renders unstyled but not broken — this is
      the styling, not new machinery); the request card renders the
      copy-pasteable `scripts/join-agent.sh <key>` line when `state ===
      "BLOCKED"`; and the step list marks the `actor: member` step as the
      current one. `renderSteps()` **already reads `step.actor` for exactly
      this** ("so Plan C's own-server joins…" — its own comment), so the actor
      distinction is a CSS-and-copy change, not a data change.
- [ ] **Step 6:** no new console route. `BLOCKED` arrives through the existing
      `GET /api/join/requests` proxy and leaves through the existing
      `POST /requests/{id}/resume`, both already server-side-token-holding and
      already polled every few seconds while the join tab is open. If the
      state machine needs a console endpoint that does not exist yet, that is a
      signal Step 2's health poll is being reinvented in the browser — stop and
      re-read spec §6.1.
- [ ] **Step 7:** test with the agent stubbed — a `BLOCKED` job resumes when
      the server appears, and stays `BLOCKED` indefinitely without failing when
      it does not. Extend `apps/console/tests/test_app_join.py` for the
      `BLOCKED` card (the command string is rendered and names the right key).
      `verify.sh --fast` green. Commit.

## Task 4: `DELETE` — the reversal walk

**Files:** `apps/join-api/{app.py,job.py}`, `scripts/member.sh`, `apps/join-api/tests/`, `apps/console/{app.py,static/app.js,static/style.css}`, `apps/console/tests/test_app_join.py`

- [ ] **Step 1:** `DELETE /api/join/members/{key}`, operator token. Refuse
      canonical members before doing anything, with a message that names the
      frozen KP3/KP4 contract as the reason.
- [ ] **Step 2:** walk the job context's completed steps backwards, running
      each `reverse` guarded by its `probe`, in the order Task 2 Step 2b
      encoded — which is **not** `reversed(completed_steps)`. States
      `RETIRING` → `RETIRED`. Note two of the six probes signal absence with
      a `200` and an empty collection rather than a 404 (Task 2 Step 3), so a
      guard written as "probe 404s ⇒ already gone" is wrong for half the walk.
- [ ] **Step 3:** ~~handle the CS approval gate~~ **there is no approval gate
      and, for a hosted member, no wait either** (Task 1; `docs/
      xroad-770-notes.md` §11 findings 1 and 3). Do not build an approval
      round, do not poll `?status=WAITING` — it is empty, and approving the
      deletion request returns `403`. What this step becomes instead: make
      `DELETE /clients/{id}` **attempt-and-retry** rather than
      poll-then-attempt. Measured live, it was accepted `204` at t+0s from the
      unregister, while `GET /clients/{id}` still read `DELETION_IN_PROGRESS`
      — so that status is not a gate in either direction. §7 of the same notes
      recorded a `409 action_not_possible` window for an **own-server**
      member, which this spike could not re-verify (nothing can stand up a
      member's own server until Task 3 lands). So: try the delete, treat `409
      action_not_possible` as retryable within the same one-run retry budget
      the forward path uses, and re-check this against a real own-server
      un-join once Task 3 exists. The whole un-join for a hosted member is
      seconds, not minutes — the "most likely to be mistaken for a hang" worry
      does not apply to it.
- [ ] **Step 4:** if the member owned a Security Server, emit the instruction
      for the operator to stop the container and remove its three named volumes
      (`kp2-<key>-db`, `kp2-<key>-conf`, `kp2-<key>-archive`, per
      `compose.members.yml`'s generated `volumes:` block). Same posture as
      Task 3: the API does not touch Docker.
- [ ] **Step 4b:** the **hosted** counterpart of Step 4, which Task 1 found
      and nothing in this pack previously accounted for: a hosted member has
      no container and no volumes, but it does leave a **SIGN key and
      certificate on somebody else's Security Server**, fully intact
      (`REGISTERED`, `active`, good OCSP) after its client is deleted —
      `docs/xroad-770-notes.md` §11. Nothing in the admin API collects it. So
      the SIGN-key reversal is not optional cleanup for a hosted member, it is
      the only thing standing between a demo federation and one orphaned
      SIGNING key per member that ever left. Assert it in Step 9's test:
      after a hosted `DELETE`, `GET /tokens/0` on the hosting server carries
      no key whose `certificates[].owner_id` is the departed member — and
      still carries every other member's.
- [ ] **Step 5:** delegate config removal to `scripts/member.sh remove <key>`
      rather than reimplementing it — it already deletes the directory, strips
      the `identity.members` entry, refuses canonical members and regenerates.
      Call it; do not duplicate it. Task 1 ran this by hand at the end of two
      live un-join cycles and both left `hurl/topology.json` byte-identical to
      `tests/golden/lite/topology.json` with the scenario tree identical too,
      so the Global Constraint is already evidenced for the hosted case — Task
      5 Step 2 has to prove it for the own-server case, not from scratch.
- [ ] **Step 6:** the console's un-join affordance — **decide whether it exists
      at all before building it.** The join tab is a demonstration asset showing
      an agency arriving; a delete button next to it is a different act with a
      different audience, and the console has no destructive control today. Two
      defensible answers: (a) no button — `RETIRING`/`RETIRED` render as states
      like any other, un-joining is a `curl`/`scripts/member.sh` operation the
      runbook documents, and the console stays read-mostly-plus-approve; or (b)
      a button behind an explicit typed confirmation naming the member key.
      Default to (a). Whichever is chosen, the **states must render** either
      way: `.join-state-retiring` / `.join-state-retired` styled, and a
      `RETIRED` record either kept visibly in the list or dropped from it —
      say which, because a card that silently vanishes reads as a bug during a
      live demonstration. One input from Task 1 that cuts *against* (a) and
      should be weighed rather than ignored: a hosted un-join turned out to be
      six synchronous calls with no propagation wait — seconds, not the
      minutes `docs/xroad-770-notes.md` §7 budgeted. `RETIRING` may barely be
      observable, which makes a console button cheap to demonstrate and makes
      "narrate through the dead air" a non-argument. It does not change the
      *audience* argument, which is still the deciding one.
- [ ] **Step 7:** if and only if Step 6 chose (b): `DELETE
      /api/join/members/{key}` on the console proxying to join-api, following
      `_proxy_join()` exactly — operator token server-side, `_require_console_origin`
      on both ends, `_validated_join_request_id()`'s equivalent for the member
      key (it is attacker-supplied by the same argument the join payload is).
      Do not let the browser hold the operator token, and do not route this
      through `_MUTATE_LOCK`/the journal: that lock guards ACL mutations, and
      a member removal is not one — the same call Task 6 of join-b made for the
      join path.
- [ ] **Step 8:** Task 4 Step 4's Docker instruction (stop the container,
      remove the three named volumes) must reach the operator through the
      console too if it is what the console triggered. An API that returns "now
      go do this by hand" to a browser that then discards it is worse than not
      having the button — which is a further argument for Step 6's option (a).
- [ ] **Step 9:** a resumable-DELETE test: kill the process mid-reversal,
      resume, and confirm it completes without re-attempting reversals whose
      probes now report absence. Extend `apps/console/tests/test_app_join.py`
      for whichever of Step 6's options was chosen — including, under (a), a
      test that asserts the console exposes **no** delete route, so the
      decision is enforced rather than merely documented. `verify.sh --fast`
      green. Commit.

## Task 5: Prove the round trip and measure it

**Files:** `acceptance/2.7.md`, `scripts/acceptance.sh`, `scripts/verify.sh`, `scripts/console.sh`, `README.md`, `docs/production-delta.md`

- [ ] **Step 1:** extend `acceptance/2.7.md` with the own-server case and the
      un-join case. The own-server case is `--full`-tier so `--live` does not
      inherit its cost; the **hosted un-join** is not — Task 1 measured it at
      six synchronous calls with no propagation wait, so it costs `--live`
      almost nothing and belongs there alongside the hosted join 2.7 already
      asserts. Assert the whole of `docs/xroad-770-notes.md` §11's closing
      claims, since they are what "un-joined" actually means: the member is
      absent from the CS (`GET /clients?q=` empty), absent from the hosting
      Security Server's client list, absent from its **token** (no key with
      that `owner_id` — while every other member's key is still there), a
      real r1 call to it fails with `Server.ClientProxy.UnknownMember`, and
      `hurl/topology.json` is byte-identical.
- [ ] **Step 2:** the headline assertion, unattended, from cold:
      `teardown.sh --purge` → deploy `profile: lite` → seed → join a member
      **with its own Security Server** (agent invoked at `BLOCKED`) → `ACTIVE,
      verified: true` → `acceptance.sh` green → `DELETE` → `acceptance.sh` green
      → **`hurl/topology.json` byte-identical to before the join**. Only the
      own-server half of this is unproven: Task 1 ran the hosted half live
      twice (join through the API → hand-driven un-join → `member.sh remove`)
      and `hurl/topology.json` came back byte-identical to
      `tests/golden/lite/topology.json` both times, with `acceptance.sh` green
      after. Two things Task 1 could not test and this step must: whether
      `DELETE /clients/{id}` needs the `409 action_not_possible` retry on an
      own-server member (`docs/xroad-770-notes.md` §7 vs §11 finding 3), and
      whether the own-server AUTH cert needs its own reversal
      (`PUT /token-certificates/{hash}/unregister`) before the server's
      containers and volumes go — a hosted member has no AUTH key of its own,
      so that path is entirely unexercised.
- [ ] **Step 2b:** the same cycle with the console up (`scripts/console.sh up`),
      driven through the join tab rather than `curl` — the demonstration this
      pack exists to support. Watch specifically that `BLOCKED` renders with its
      command, that the poll picks the job back up after the agent runs without
      a manual refresh, and that the un-join leaves the tab in a state a
      presenter can explain. The console is `--full`'s console smoke already;
      this extends it to the states Plan C added. `--full`'s existing console
      pass is the right place for it, not a new tier.
- [ ] **Step 3:** measure `--full`'s new duration under both profiles, two cold
      runs each, the way `README.md`'s existing figures were established. Update
      `README.md` with the measured numbers, including if they are worse than
      hoped.
- [ ] **Step 4:** confirm the sizing claim in spec §12 by measuring rather than
      trusting it: `docker stats --no-stream` with lite plus a joined member's
      own Security Server. The spec predicts ~13 GB and says own-server joins
      and a real third-party backend cannot both fit on a 16 GB host. Record
      what is actually true.
- [ ] **Step 5:** `docs/production-delta.md` — the entry that matters most from
      this plan: **in production, the operator does not provision the member's
      server at all.** `scripts/join-agent.sh` simulates the joining agency's
      own infrastructure team; really, `BLOCKED` is satisfied by the member, on
      the member's hardware, with the member's own CA-issued certificates, and
      takes days rather than seconds.
- [ ] **Step 6:** record the Task 6 gate explicitly: state, in
      `docs/production-delta.md`, whether Step 2 proved that a lite base plus
      an own-server join covers everything `profile: full` covered. That
      sentence is what Task 6 acts on. Commit.

## Task 6: Retire `profile: full` — gated on Task 5 Step 2

**Files:** `hurl/generate.py`, `hurl/check_scenarios.py`, `scripts/lib-stack.sh`, `docker-compose.yml`, `deployment.yaml`, `configs/member-pnia/2.5.yaml`, `configs/member-moeys/2.2.yaml`, `apps/console/{truth.py,app.py}`, `apps/console/tests/test_truth.py`, `tests/golden/`, `tests/test_golden.py`, `README.md`, `PLAN.md`, `docs/superpowers/specs/2026-08-01-member-join-api-design.md`

**Gate — read before starting.** Run this task only if Task 5 Step 2 passed
*and* Step 6's recorded answer is yes. Two reasons to stop instead:

- Step 2 did not pass, or own-server joins landed as Task 3-only. Then `full`
  is still the only own-server proof and it stays. (The "if Task 1 reveals
  clean de-registration is not achievable" branch below, which used to be the
  other way into this reason, **did not fire** — Task 1 established the
  sequence live. This reason now rests on Task 5 Step 2 alone.)
- `full` turns out to be load-bearing as the **demo-fidelity** topology — the
  one where each agency visibly owns a server — rather than only as test
  coverage. That is a presentation requirement, not an architectural one, and
  it survives this task. In that case do Steps 1–2 only (collapse the code
  path) and skip Steps 3–5 (keep the topology).

Decide which of the three outcomes applies, write it down, and then execute
only what it licenses.

- [ ] **Step 1:** move `LITE_HOSTED_ON` out of `generate.py` into explicit
      `security_server.hosted_on: ss-plr` in `configs/member-pnia/2.5.yaml` and
      `configs/member-moeys/2.2.yaml`. The preset becomes config stated in the
      one place a member's hosting is already stated. `resolve_hosted_on_map()`
      loses its `profile` argument and its `if profile == "lite"` branch; the
      explicit-`hosted_on` path it already has does all the work.
- [ ] **Step 2:** regenerate and confirm the emitted tree is **byte-identical
      to `tests/golden/lite/`**. This is the whole safety argument for Step 1:
      if the preset and the explicit config are the same mechanism, moving
      between them changes nothing. If it is not byte-identical, stop — the two
      were not the same mechanism and this task's premise is wrong. Commit.
- [ ] **Step 3:** delete the profile *machinery*: `deployment.yaml`'s
      `profile:` key, `generate.py`'s `--profile` flag and its validation,
      `lib-stack.sh`'s `LITE` branch and its `--profile full` compose
      arguments, the topology.json/deployment.yaml profile-drift checks in
      `lib-stack.sh` and `check_scenarios.py`, and `topology.json`'s
      `"profile"` key. Note `topology.json` changing shape is a KP3/KP4-visible
      change — check `manifest.yaml` and the console before deleting the key
      rather than after.
- [ ] **Step 3b:** the console side of the same deletion, which is larger than
      one field. `truth.py`'s whole reason for existing is that a member's
      entrypoint "is only correct under `profile: full`" (its own module
      docstring) — with one topology that caveat goes, and the docstring must
      go with it or it becomes a lie about code that no longer branches. Also:
      `truth.py`'s `profile` attribute and its `deployment.get("profile",
      "full")` read; `app.py`'s health endpoint, which returns `{"status":
      "ok", "profile": TRUTH.profile}` — decide whether to drop the key or
      return a constant, and prefer dropping it, since a health probe asserting
      a constant is noise; and `test_truth.py`'s
      `test_full_profile_resolves`/`test_lite_profile_resolves` pair, which
      collapses to one test of the single resolution. Confirm nothing in
      `static/app.js` reads `profile` off `/api/topology` before deleting the
      key — the browser caches that response once on load, so a missing field
      fails at render time, not at request time, and would surface first in
      front of an audience.
- [ ] **Step 4:** delete the `profiles: ["full"]` tags on `ss-pnia` and
      `ss-moeys` in `docker-compose.yml` and the services themselves. Their
      pinned ports (5100/5180, 6000/6080) stay reserved in `PINNED_PORTS` —
      `_allocate_numbers()`'s determinism and the byte-identical Global
      Constraint both depend on nothing below `FRESH_PORT_START` moving.
- [ ] **Step 5:** collapse the golden corpus to one tree (`tests/golden/`,
      formerly `lite/`), drop `test_golden.py`'s `@parametrize` over profiles,
      and delete `tests/golden/full/`. `verify.sh --fast` green, then one
      `--full` from cold **including the console smoke pass** — `truth.py` is
      the console's entire model of the federation and Step 3b rewrote it, so a
      green `--fast` proves considerably less here than usual. Commit.
- [ ] **Step 6:** close out the spec: mark §15's three plans complete, replace
      the predictions in §5.3 (probes are rare) and §12 (sizing) with the
      measured answers, and record whether open questions 3 and 6 resolved as
      expected. `README.md` loses its two-profile timings and its "develop
      against lite, run one `--full` under full before closing out" guidance —
      which was the whole reason the two-tier habit existed. `PLAN.md` §2 (the
      lite topology) and §9 updated. Commit.

---

## Sequencing

Task 1 first, alone, and **revise the rest of the plan before executing it**.
Tasks 2 and 3 are independent of each other and could run in parallel by two
workers — Task 2 is `hurl/`, Task 3 is `apps/join-api/` plus a script — but
Task 4 needs both. Task 5 proves the round trip. Task 6 is last, is optional,
and is the only task in this plan that may legitimately be skipped in full —
its gate decides.

**Risk:** the highest of the three plans, and unevenly distributed. Task 3 is
well-precedented: `compose.members.yml` already generates everything the agent
needs, and the health poll is a pattern the pack uses in four places. Tasks 1,
2 and 4 are genuinely new territory — no upstream reference implementation,
because `setup.hurl` only ever builds a federation up and never takes one
apart. Expect Task 1 to take longer than it looks and to change Tasks 2 and 4;
that is the point of doing it first rather than discovering the same facts
three tasks deep with half an implementation written against a wrong guess.

~~**If Task 1 reveals that clean live de-registration is not achievable on
7.7.0**~~ — **it is achievable, and this branch does not apply** (Task 1,
2026-08-02; `docs/xroad-770-notes.md` §11). Six admin-API calls across two
servers, all synchronous, all `204`, no approval round, no propagation wait,
no global-configuration residue, no restart. `acceptance.sh` green afterward
and `hurl/topology.json` byte-identical, twice. Plan C is not reduced to Task
3, and Task 6's gate is not closed by this branch — it still turns on Task 5
Step 2 as written.

The branch is kept on the record, not deleted, because it was the plan's
stated stop condition and knowing it was tested and did not fire is worth
more than a clean-looking plan. Its reasoning also still governs the
*own-server* half, which Task 1 could not reach: if Task 5 Step 2 finds that
an own-server un-join leaves state a restart is needed to clear, ship the
hosted `DELETE` and say plainly that the own-server one is not supported,
rather than shipping a `DELETE` that half-works. Spec §16's principle stands
— a mechanism nobody can trust is worse than an acknowledged gap.

**On not doing Task 6 first.** It will be tempting — `full`/`lite` is visible
duplication (a doubled golden corpus, a `LITE` branch, two sets of timings) and
Tasks 1–4 are hard. Resist it. `full` is the pack's only working example of the
exact sequence Task 1 is spiking and Task 3 is building; deleting the reference
implementation before writing its replacement is how a one-day cleanup becomes
a week of blind debugging. The duplication is small and it is not going
anywhere.
