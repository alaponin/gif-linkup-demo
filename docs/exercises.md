# Exercises

Five things to do with a running federation, in the order they make sense.
Every command here is documented in `runbook.md` — this file links to it and
never re-documents it. What this file adds is the **expected observations**:
what you should see if it worked, so that "it did something" and "it did the
right thing" stop being the same sentence.

Start from a stack that is up and green:

```
scripts/demo.sh          # from zero, ~10 minutes
scripts/acceptance.sh    # if it is already up
```

Exercises 2–4 run against the same joined member and are meant to be done in
order: 2 brings PTSB in, 3 inspects it, 4 takes it out again.

---

## 1 · Break and restore the once-only proof

**Goal:** see that the access-control list is the trust device, not the
prose about it. Revoking one grant makes exactly one half of the form fail.

**Commands** — console only, no shell (`runbook.md` Steps 5):

```
scripts/console.sh up          # http://localhost:8090
```

Tab **1 · Ask once**: pick a learner, run the exchange, then press **Break
the proof**. Tab **2 · How it worked** shows the same exchange by layer;
tab **3 · Who's allowed** shows the grant you just removed.

**Expected observations**

- The denial is an X-Road fault: HTTP 500 carrying
  `"type": "Server.ServerProxy.AccessDenied"` and an `x-road-error` header of
  the same value. The bus refuses the call and the provider never sees it —
  it is not a 404 and not an application error.
- The denial takes a few seconds to take effect (the console polls for it);
  so does the restore. Neither is instant.
- Only the PNIA half of the form fails. PLR still pre-fills, because its
  grant is untouched — one revocation, one broken source.
- The header tally (`questions avoided this session`) stops rising for the
  fields that no longer arrive; `asked N · pre-filled M / T` shows the drop.
- A red journal banner appears and the context bar reads
  `Permissions: modified`. The console has written to
  `out/console-acl-journal.json`.
- `scripts/acceptance.sh` now **refuses to run**, naming that journal and
  telling you to reset. This is deliberate: a suite that ran anyway would
  fail for a reason that has nothing to do with the pack.
- **Restore the proof** (or `scripts/console.sh reset`) empties the journal,
  the banner clears, and `scripts/acceptance.sh` is green again.

**Cleanup:** `scripts/console.sh reset`.

**Module:** `once-only-exchange` (manifest.yaml, video ref 5.6).

**What just happened, per layer:** *legal/organisational* — the ACL is the
machine-readable form of who was authorised to ask; *technical/semantic* —
nothing about the message changed, only whether the bus would carry it.

---

## 2 · Join PTSB, hosted on another member's Security Server

**Goal:** watch an agency arrive: applicant submits, operator approves, and
the technical join runs itself.

**Commands** (`runbook.md` → Joining a member → *Join via the API*):

```
scripts/join.sh up            # the join API at http://localhost:8091
```

Submission is the **applicant's** act, with the applicant token, from
outside — so it stays a `curl`:

```
curl -s -X POST http://localhost:8091/requests \
  -H "X-KP2-Console: 1" \
  -H "Authorization: Bearer $KP2_JOIN_APPLICANT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "PTSB",
    "name": "Progressa Tertiary Scholarship Board",
    "subsystem": "SCHOLARSHIP",
    "subsystem_description": "Scholarship award register",
    "security_server": {"code": "SS-PTSB", "dns_name": "ss-ptsb", "hosted_on": "ss-plr"},
    "backend": {"auth": "none"},
    "semantic": {"entity": "award", "key": "nin",
                 "fields": ["nin", "award_id", "program", "year"],
                 "pattern": "digital_registries_lookup"},
    "services": [{
      "code": "awards-api",
      "spec_url": "http://app-ptsb:8000/spec.yaml",
      "access": ["PROGRESSA/GOV/PNEA/EXAMS"],
      "lawful_basis": "[confirm: cite the decree article]",
      "sla": {"availability": "99.5% monthly uptime",
              "response_time": "500 ms at the 95th percentile",
              "support_hours": "08:00-17:00 on working days",
              "incident_response": "acknowledged within 4 working hours",
              "change_notice": "30 days for a breaking change",
              "signatory": "Head of IT, PTSB"}
    }],
    "member_requirements": {
      "has_security_server": true, "has_registered_identity": true,
      "standards_portfolio_adopted": true, "data_conformant": true,
      "technical_contact": "Head of IT, PTSB"
    }
  }'
```

Then approve in the console's **4 · Join a member** tab, supplying a decision
reference (any minute reference — `RIHA-2026-001` will do). The tab polls
itself; the operator token never reaches the browser.

**Expected observations**

- The request appears in the tab with a **computed config diff** *before*
  anything is written. Approval is what makes it real.
- Approval refuses outright if the checkout is dirty
  (`DirtyCheckoutError`) — the API writes files into the repository and will
  not do so on top of uncommitted work. Commit first.
- `state` reaches `ACTIVE, verified: true` well inside two minutes for a
  **hosted** join. (An own-server join ends `ACTIVE, verified: false` — a
  known defect, documented in `runbook.md`, not something you caused.)
- `onboarding/ptsb/` appears, and `01-admission.md` carries the decision
  reference you typed. Canonical members have no such file — they never
  passed an admission through this API, which is why their records begin at
  `02`.
- `onboarding/ptsb/00-gates.md` reads **implemented** on the Admission row,
  where a canonical member's reads **named absence**.
- `onboarding/catalogue.yaml` gains a row for
  `PROGRESSA/GOV/PTSB/SCHOLARSHIP/awards-api`, visible in console tab
  **5 · What's on the bus**.
- `scripts/member.sh list` shows `ptsb` with `origin: joined` — never
  `canonical`, whatever the payload said, because the payload cannot say it.
- `scripts/acceptance.sh` stays green, including the generic
  `acceptance/member.md` checks, which now run against PTSB too.

**Cleanup:** none yet — exercises 3 and 4 need this member.

**Module:** `join-member` (manifest.yaml).

**What just happened, per layer:** *legal/organisational* — the technical
join could not start without an admission reference, and that coupling is now
written down; *technical/semantic* — the member's own OpenAPI contract is
what got published, not a description of it.

---

## 3 · Detect drift in a published contract

**Goal:** a service description is a promise. See the pack notice when the
backend stops keeping it.

**Commands** (`runbook.md` → Joining a member → *Drift*):

Edit `apps/specs/ptsb-awards.openapi.yaml` — add a path alongside
`/awards/{nin}`. `/spec.yaml` is served straight from the host mount, so the
edit is live with no restart:

```
docker compose exec join-api python3 scripts/member.sh drift ptsb
```

(The mock's *field filtering* is a different thing: that is read once at
start-up, so changing which fields the response carries does need a
`docker restart app-ptsb`. Drift reads the published description, not the
data.)

**Expected observations**

- Run from the host instead of inside the network, this reports
  `nodename nor servname provided`. **That error is the trap working**: the
  spec URL is an internal `linkup` hostname (`app-ptsb:8000`), reachable only
  from a container on that network. It is not a bug and not a misconfigured
  member.
- Run from inside, the output is an **endpoint diff** against the baseline
  captured at join time (`out/join/*.json`) — the paths added, the paths
  gone. An unchanged spec produces no diff.
- No auth, no HTTP to the join API: `scripts/join.sh down` first and the
  check still works.

**Cleanup:** `git checkout apps/specs/ptsb-awards.openapi.yaml`.

**Module:** `join-member` (the drift half — the register's own upkeep).

**What just happened, per layer:** *legal/organisational* — a contract that
silently changed is a change nobody agreed to; *technical/semantic* — the
diff is against the description the member published, not against the data.

---

## 4 · Un-join PTSB

**Goal:** leaving is a walk backwards through the same gates, not a delete
button.

**Commands** (`runbook.md` → Joining a member → *Un-join via the API*):

```
curl -X DELETE -H "X-KP2-Console: 1" \
     -H "Authorization: Bearer $KP2_JOIN_OPERATOR_TOKEN" \
     http://localhost:8091/members/ptsb
```

**Expected observations**

- States go `ACTIVE` → `RETIRING` → `RETIRED`. Poll `GET /requests/{id}` or
  watch the console's join tab, which renders both.
- The reversal is visible as six steps in reverse order: revoke the ACL,
  delete the service description, unregister the client, delete the client,
  delete its SIGN key, delete the member on the Central Server.
- **There is no un-join button in the console**, deliberately — a
  destructive control is a different act for a different audience. The
  `RETIRED` card stays in the list rather than vanishing.
- The catalogue row is gone; tab 5 no longer lists `awards-api`.
- `onboarding/ptsb/` is **retained**, and gains `99-retirement.md`. The
  record of a member that left is not itself deleted.
- Canonical members are refused before anything happens, naming the reason.
  Try it: `DELETE /members/pnia` changes nothing.
- The next join against a dirty checkout fails with `DirtyCheckoutError` —
  the un-join has just written files. Commit them.
- Re-issue the same `DELETE` and it succeeds again, skipping what is already
  gone: every reversal is guarded by a read that proves whether it needs
  doing.

**Cleanup:** commit or discard what the walk wrote.

**Module:** `join-member` (gate GX).

**What just happened, per layer:** *legal/organisational* — the message-log
retention half of GX is **not** implemented here and the record says so;
*technical/semantic* — absence is asserted, not assumed
(`acceptance/join-member.md`).

---

## 5 · The reproducibility proof, watched

**Goal:** the claim is that this pack rebuilds from zero. Watch it be true.

**Commands:**

```
scripts/verify.sh --full
```

**Expected observations**

- It **purges first** (`teardown.sh --purge`) — this destroys the running
  federation and every volume. That is the point: nothing survives to make
  the next stage easier. Do not run this to "check something quickly".
- Stage timings land roughly where `runbook.md` says: ~156s to
  containers-healthy, then a ~395s Hurl run, ≈9–10 minutes to deploy, before
  seed and acceptance.
- A stretch of HTTP errors and retries partway through the deploy is
  global-configuration propagation, not a failure.
- Where each stage's output lands: `out/deploy-timings.txt` (timings),
  `out/hurl-report/` (the stand-up run), `out/application-<nin>.json` (the
  once-only artefact acceptance writes), `out/console-acl-journal.json` (the
  console's, empty on a clean run).
- It ends with a fixture-drift check — recorded X-Road fixtures that no
  longer describe the live server fail here rather than rotting quietly.

**Cleanup:** none — you are left with a fresh, green federation.

**Module:** `federation-core` (manifest.yaml, video ref 5.5).

**What just happened, per layer:** *legal/organisational* — a demonstrator
who cannot rebuild cannot hand over; *technical/semantic* — the same
configuration produced the same federation, which is what "as code" has to
mean.
