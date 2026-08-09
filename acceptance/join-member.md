# Acceptance check — module 2.7 (a new member joins the bus — the join API)

**Proves:** a join submitted through `apps/join-api` and approved actually
converges the live federation — not just the registry state `acceptance/
member.md` already checks for any member, but that the joined member's
service is reachable, and reachable **only** by the consumers its `access:`
list names.
**Run by:** `scripts/acceptance.sh` (join API HTTP + X-Road test calls per
the r1 REST protocol)

This document deliberately does **not** restate `acceptance/member.md`.
`member.md` already asserts, generically for any member `hurl/topology.json`
describes — canonical or joined — that its subsystem is `REGISTERED` and its
granted-subject list equals its config's `access:` list exactly. 2.7 asserts
what `member.md` cannot: the **join transition** itself (a request submitted
through the API, approved, and run to `ACTIVE` produces exactly that
registered, ACL-exact state) and **reachability** — that the resulting
member's backend is actually there, not merely registered. Registry state is
not evidence of a working backend this pack did not write; that is the gap
this check closes.

- **Given** a deployed, seeded federation (`scripts/deploy.sh`,
  `scripts/seed.sh`) and a valid join payload for a member with `hosted_on`
  set (Plan B's whole scope — an own-server join is `--full`-tier, out of
  scope here, see Tier placement below);
- **When** the payload is submitted (`POST /requests`), approved
  (`POST /requests/{id}/approve`), and the job (`apps/join-api/job.py`) runs
  to completion;
- **Then**:
  1. the request record's `state` is `ACTIVE` (not `FAILED` — a `FAILED`
     record here is a hard failure of this check, not a skip);
  2. the subsystem `member.md` already covers, restated only to say it is
     **not restated**: `member.md`'s existing generic checks, run against
     this member like any other, already prove `REGISTERED` and exact-ACL;
  3. **the r1 clause — the one that carries this module:** a real `r1` call,
     through the *joined* member's own consumer Security Server, against the
     joined member's published service ROOT (not a specific operation --
     `apps/join-api/job.py`'s own `r1_verify` step and this check both call
     the service root rather than a resource path, because there is no real
     record id either one has a way to know), with `X-Road-Client` set to an
     authorized consumer's
     identity, reaches the backend with no X-Road fault in the response (a
     plain backend 404 counts — it proves the call traversed X-Road end to
     end; only a `Server.*`/`Client.*` fault type means it did not); the same
     call, routed through an *unauthorized* subsystem's own Security Server
     with `X-Road-Client` set to that subsystem's identity, is denied with
     the SPECIFIC X-Road access-denied error (`Server.ServerProxy.AccessDenied`)
     — not a transport failure, not a plain 404. This mirrors `acceptance/2.6.md`'s 2.6.4/
     2.6.5 mechanics exactly (see `scripts/acceptance.sh`'s `check_264`/
     `check_265`): the denial must come from the provider-side ACL, proving
     the join did not just register a name but actually put a reachable,
     correctly-fenced backend behind it. This is the only assertion in the
     whole acceptance suite that would catch a
     registry-perfect-but-dead member — a join that passed every validation
     check yet points at a backend nobody can reach.
  3b. **field conformance (G5.9)** — a real, key-specific `r1` call through
     the authorized consumer (not the service-root call clause 3 makes, which
     404s against every mock in this pack) returns exactly the fields the
     joined member's own OpenAPI contract declares. Skipped, not failed, for
     a joined member whose backend does not follow this pack's generic
     mock-registry pattern (`scripts/acceptance.sh`'s `check_r1_fields`) --
     there is then no in-repo contract to check it against.
  4. the config and manifest on disk describe the member that is actually
     live — `configs/member-<key>/<key>.yaml` exists, and
     `manifest.yaml`'s `identity.members.<key>` has `origin: joined`.
  5. **the joined member is discoverable** — its published service appears in
     `GET /catalogue`, asked of the running join API with the applicant
     token, not read off the file the same process wrote. That distinction
     is the whole value of the clause: comparing a rendered file to the
     config it was rendered from tests the renderer against itself, which
     this pack does not accept as evidence. What a body that has just joined
     depends on is that the *service* answers the question, so the service
     is what is asked.

  Since this module has no fixed running example the way 2.6 has PNEA/PNIA/
  PLR, the given/when/then above is written generically over "the joined
  member" and "an authorized/unauthorized consumer" rather than a hardcoded
  agency. A concrete payload (PTSB) is submitted and this check runs against
  it for real.

## The own-server join

The clause above is written for a member with `hosted_on` set. A member that
brings up its **own** Security Server runs a different middle — the same
cold-deploy bring-up every canonical member gets (`ss.bringup_init`,
`ss.auth_key_csr`, `ss.sign_key_csr`, `ss.bringup_register`, `ss.activate`,
`ss.tsa_post`, then `ss.client_add`) — and the middle of it is the *member's*
work, not the operator's. That is the whole of what this case adds:

- **Given** the same deployed, seeded federation and a valid join payload
  with `security_server.own_server: true` and **no** `hosted_on`;
- **When** the payload is submitted and approved, the job stops at
  **`BLOCKED`** before the first `actor: member` step, naming the server it
  is waiting for; the member's own infrastructure stands that server up
  (`scripts/join-agent.sh <key>`, the demo's stand-in — see
  `docs/production-delta.md`), and the request is resumed
  (`POST /requests/{id}/resume`);
- **Then**, in addition to 1–4 above, all of which apply unchanged:
  5. the request reached `BLOCKED` at all, and reached it *before* running
     any `actor: member` step — a job that walked straight through would
     mean the operator had silently provisioned the member's infrastructure,
     which is the one thing `BLOCKED` exists to prevent;
  6. `BLOCKED` never expired into `FAILED`: a resume that still finds the
     server absent goes back to `BLOCKED`, as many times as it takes;
  7. the member's own Security Server is a real, registered server of the
     federation, not merely a running container — its AUTH certificate is
     `REGISTERED` on its own token and the Central Server lists it under
     `GET /security-servers`. This is what the hosted case has no analogue
     for at all: a hosted member owns no AUTH key, so nothing in
     this file's hosted clauses exercises this path.

## The un-join transition

`DELETE /members/{key}` walks the six steps of `hurl/steps.py`'s
`REVERSAL_ORDER` backwards and then runs `scripts/member.sh remove <key>`.
"Un-joined" is not one assertion but five, and they are exactly
`docs/decisions/xroad-770-notes.md` §11's closing claims — the section that established
the sequence live in the first place. Anything less than all five would let a
member look gone from wherever you happened to look. A sixth (4b below) was
added with the service catalogue: gone from the bus and still listed as
publishing is exactly the "looks gone from where you happened to look" failure,
one artefact further out.

- **Given** a member that joined through this API, reached `ACTIVE`, and has
  since been retired through `DELETE /members/{key}` to state `RETIRED`
  (discovered generically from `out/join/*.json`, the same way the clause
  above discovers joined members — a federation nobody has un-joined
  produces zero rows and passes vacuously), **and retired since the
  federation now running was deployed**: `out/join/` survives
  `scripts/teardown.sh --purge`, and a record from a previous federation
  would pass all four clauses trivially while asserting nothing about this
  one, so `retired_at` older than `out/deploy-timings.txt`'s `deploy_start`
  (or no such anchor at all) is a logged SKIP, never a PASS;
- **When** the walk has completed and `scripts/member.sh remove` has
  regenerated `hurl/`;
- **Then**:
  1. the member is absent from the **Central Server**: `GET /clients?q=<code>`
     returns `{"clients": []}`. Absence here is an **empty list, not a 404** —
     there is no `GET /subsystems/{id}` on the CS at all (it answers `405`),
     so this is the only viable read (§11 finding 4);
  2. the member is absent from the **hosting Security Server's client
     list**: `GET /clients` on that server lists no client with this
     `member_code`;
  3. the member is absent from that server's **token**: no key carries a
     certificate whose `owner_id` is this member's — **while every other
     member hosted on the same server still has its own**. Both halves are
     load-bearing. §11 found that deleting a hosted client leaves its SIGN
     key behind entirely intact (`REGISTERED`, `active`, good OCSP) and that
     nothing in the admin API ever collects it, so the first half is a real
     assertion about a step that can silently not happen; and a shared host
     (`security_server.hosted_on`) carries several keys all labelled
     `"Sign key"`, so the second half is what would catch a reversal that
     deleted the *wrong* agency's key by matching on the label;
  4. a **real `r1` call** to the departed member's service, from a consumer
     that was authorised before it left and through that consumer's own
     Security Server, fails with the specific X-Road fault
     `Server.ClientProxy.UnknownMember` — not a hang, not a stale success,
     and not a generic transport error. This is the reachability clause of
     the join case run backwards, and it is the only one of the five that
     proves the *bus* forgot the member rather than just the registries;
  4b. **the departed member's services are gone from `GET /catalogue`.** GX's
     third clause asks the operator to *remove the catalogue entry*; there is
     nothing to remove. The catalogue is derived from `configs/member-*/`,
     the un-join took that config away, and the next read simply does not
     find its services — so this clause closes by derivation and not by a
     delete path, which is the point, because a delete path is a thing that
     can be forgotten. **This check is the evidence for that half of GX**;
     the other two thirds, certificate revocation and consumer notification,
     remain absent (`docs/production-delta.md`). The member's own
     `onboarding/<key>/04-catalogue/` entries stay on disk, deliberately:
     the aggregate is the live view, the record is the history of what the
     operator revoked.
  5. `hurl/topology.json` is **byte-identical** to the single deployment
     golden (`tests/golden/deployment/topology.json`) — the working tree came back
     to exactly where it was before the join. This is
     the join-c plan's Global Constraint. Asserted only when no joined member remains in the
     topology; with one still present, byte-identical to the canonical
     golden is the wrong expectation and the check says so rather than
     failing.

  For an own-server member, clauses 2 and 3 have nothing to run against —
  its Security Server was destroyed with it (`retire_instruction()`'s
  `docker rm -f` / `docker volume rm`), so there is no client list and no
  token to read. Those two are skipped with a logged reason; 1, 4 and 5
  still apply and still run.

## Tier placement

This check is a `--live`-tier check: it
needs a running, seeded stack, and its cost is the real propagation waits
(registration settling, ACL propagation) that `--fast` cannot mock away.
Specifically the **hosted** join (`default_hosting: hosted_on` / an explicit
`hosted_on` in the payload) — the own-server join (host-agent bring-up,
`compose.members.yml`) is `--full`-tier only and not this check's scope.

**The un-join transition is `--live`-tier, the own-server join is not**, and
the split is a cost one, measured rather than assumed. The hosted un-join is
six calls with no approval round and nothing retried; a scripted own-server
un-join has been measured end to end, and the five clauses above are plain
reads on top of it. That is cheap enough to sit
beside the hosted join `--live` already carries. An own-server join is not:
it stands up a whole extra Security Server container (minutes to healthy,
~2 GB of RAM) and needs `scripts/join-agent.sh` run out of band at
`BLOCKED`, so it stays where `docs/production-delta.md`'s own-server
findings already put it — `--full`, driven by hand, and by preference
through the console's join tab, which is `--full`'s console smoke and the
demonstration this pack exists to support. Neither tier ever *performs* a
join: both clauses discover what has already happened
(`hurl/topology.json` for joined members, `out/join/*.json` for retired
ones) and pass vacuously when nothing has.

`--fast` never runs this document; it only exercises the pieces module 2.7
contributes that do not need a live stack (step-registry unit tests, the
golden byte-identical test, validation/policy tests against fixture specs,
job-context secret-leakage tests, the step engine against recorded
fixtures — all under `apps/join-api/tests/` and `tests/`, already covered by
`scripts/verify.sh --fast`, not restated here).

Status: VERIFIED on the live stack, `--full` from cold, against the
collapsed single (D5) topology, with both a hosted and an own-server join
and un-join (identity PTSB, see `docs/production-delta.md`): `2.7.1`,
`2.7.unjoin(PTSB)` and `2.7.unjoin.topology` all green from
`scripts/acceptance.sh`, and the own-server case reaches
`ACTIVE, verified: true`.

`apps/join-api/job.py` gives `join.r1_verify` its own retry budget,
`R1_RETRY_BUDGET = 54`, separate from the run's shared per-run budget
(120s). Without the separate budget, `ss.client_register`'s propagation wait
can exhaust the shared budget before `join.r1_verify` runs, and no resume
can revisit the step afterwards, leaving the request at `ACTIVE` with
`verified: false`. With the separate budget, a real own-server join reaches
`ACTIVE, verified: true` well within it — confirmed against a real
federation, not just the synthesised-response test in
`apps/join-api/tests/test_job.py`. See `docs/production-delta.md` for
background.
