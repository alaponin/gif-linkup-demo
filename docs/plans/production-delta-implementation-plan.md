# Implementation plan — six low-hanging fruits from `docs/production-delta.md`

Scope: the six items agreed from the production-delta review, ordered here as
independent, individually-committable changes. Each section states the design,
the exact files touched, the tests, the documentation rows that must move in
the same commit, the verification tier to run, and an effort estimate.

Ground rules that apply to every item (they come from the pack's own
discipline, not from preference):

- **`docs/path-conformance.yaml` wins on status.** Where an item changes what
  the pack implements, the conformance entry moves in the same commit, with
  `evidence:` paths that actually exist (`tests/test_path_conformance.py`
  existence-checks them). `docs/production-delta.md`'s corresponding row is
  updated too, but as narrative.
- **The join-policy three-key rule.** `configs/x-road-bus/join-policy.yaml`
  admits a new key only if something genuinely applies it, and
  `hurl/generate.py`'s `check_join_policy()` hard-fails on unknown keys — so
  any new key lands in three places at once: the YAML, the check, and the
  consumer. `tests/test_join_policy.py` updates with it.
- **The golden corpus.** Anything that changes `hurl/generate.py`'s output
  regenerates `tests/golden/` via `scripts/regen-golden.sh` in the same
  commit, and the diff is eyeballed before committing.
- **Verification tiers.** Every item ends with `scripts/verify.sh --fast`
  minimum; items that touch live behaviour name `--live` or `--full` below.
- **No new Python dependencies.** Everything below is stdlib + what
  `apps/join-api/requirements.txt` already carries.

Suggested sequence (rationale in §7): **5 → 2 → 1 → 3 → 6 → 4.**

---

## 1. Restrict `spec_url` (and `servers[].url`) before fetching — the SSRF guard

**Delta row:** table row "`Service.spec_url` … is a plain `str` with no scheme
or host restriction, fetched … from a container that also holds `JOB_SECRETS`
and can reach every admin API on `:4000`." The row itself prescribes the fix:
an allowed scheme and host set.

**Design.** A new validation check, `spec_url_origin`, inserted in
`validate.py`'s `_CHECKS` list immediately **before** `backend_reachability`
— so the URL is judged before the first byte is ever fetched. It enforces:

- scheme is `http` or `https` (rejects `file://`, `ftp://`, `gopher://`, a
  schemeless string);
- hostname is present and matches an allowlist;
- explicitly rejects IP literals, `localhost`, loopback/link-local ranges and
  the cloud metadata address `169.254.169.254` even if someone later widens
  the allowlist carelessly (defence in depth, one `ipaddress` stdlib call);
- the same test is applied to `servers[].url` inside
  `_check_backend_reachability` before `check_reachable()` is called — the
  delta row names only `spec_url`, but the backend URL is equally
  applicant-controlled and fetched from the same credential-holding
  container. The plan closes both or the guard is theatre.

**Where the allowlist lives.** A fourth key in
`configs/x-road-bus/join-policy.yaml`'s `join:` block:

```yaml
join:
  member_class: GOV
  default_hosting: hosted_on
  allowed_methods: [GET]
  spec_url_hosts: [app-pnea, app-plr, app-pnia, app-ptsb]  # + ports if desired
```

This passes the file's own admission test for a new key ("can it be set to
another value, and does something observably change?" — yes: validation
outcomes change). It is a *join* property, so this is the right scope, unlike
approval mode. Consequences: `check_join_policy()` in `hurl/generate.py`
learns the key (and asserts it is a non-empty list of hostnames),
`tests/test_join_policy.py` gains cases, and the YAML's "exactly three keys"
comment is rewritten to "exactly four" with the admission-test story intact.

Alternative considered and rejected: deriving the allowlist from Compose
service names at validation time — it couples `validate.py` to Docker state
it deliberately never reads, and makes the check untestable as a pure
function.

**Interaction to preserve:** `httpx.get()` does not follow redirects by
default — pin that explicitly (`follow_redirects=False`) in
`_default_fetch_spec` and `_default_check_reachable` with a one-line comment,
so a future httpx upgrade or a well-meaning edit can't reopen the hole via a
302 to `cs:4000`.

**Files.**

| File | Change |
| --- | --- |
| `apps/join-api/validate.py` | new `_check_spec_url_origin`, host check reused inside `_check_backend_reachability`; `follow_redirects=False` pinned |
| `configs/x-road-bus/join-policy.yaml` | `spec_url_hosts:` key + comment rewrite |
| `hurl/generate.py` | `check_join_policy()` admits + validates the new key |
| `tests/test_join_policy.py` | new-key cases (present, absent, empty, non-list) |
| `apps/join-api/tests/test_validate.py` | reject: `file://`, `http://cs:4000`, `http://127.0.0.1:4000`, `http://169.254.169.254`, IP literal, redirect-following disabled; accept: `http://app-ptsb:8080/openapi.yaml` |
| `docs/production-delta.md` | row 41 rewritten: guard now exists; production still wants network segregation of the fetch path |
| `docs/path-conformance.yaml` | if a validation-checks clause cites the check list, add evidence; otherwise no status moves |

**Watch out for:** the PTSB fixtures (`test_job.py`'s `_payload()`, the
`app-ptsb` mock) and the golden/hosted fixtures must use an allowlisted host
— they already use `app-ptsb`, so this should be a no-op, but run the full
join-api test suite before assuming. `check_join_policy` change alters no
generated output, so no golden regen expected — verify with `--fast`.

**Verify:** `--fast`, then one live hosted join + un-join (`--live` does not
join; use the console or `curl` per `runbook.md`) to prove a legitimate
submission still passes check 9.

**Effort:** ~half a day including tests.

---

## 2. Retention step in the un-join — export the archive before deleting it

**Delta row:** "Un-join deletes `kp2-<key>-archive` … with no retention step"
(onboarding path §2 GX). `runbook.md` already names the gap in prose ("This
pack implements no archival step; it only names the gap") — this item turns
that sentence into a step.

**Design.** The API never touches Docker (by design — that split stays), so
the fix lands in the *instruction*, not in a new code path:

- `job.py`'s `retire_instruction()` message gains an export command **before**
  the `docker volume rm` line:

  ```
  mkdir -p out/retired
  docker run --rm -v kp2-<key>-archive:/from -v "$PWD/out/retired:/to" \
    <python:3.12-slim pinned digest> tar czf /to/kp2-<key>-archive.tar.gz -C /from .
  docker rm -f <dns>
  docker volume rm kp2-<key>-db kp2-<key>-conf kp2-<key>-archive
  ```

  Use the `python:3.12-slim` digest the pack already pins (both Dockerfiles)
  rather than `alpine` — no new image on the host, and the digest-pin
  discipline is already established for exactly this image.
- The `retire_instruction` dict gains an `archive_export` field naming the
  output path, so the console's join tab (which renders the instruction) can
  show it without parsing the message string.
- Hosted members: their message-log records live in the *host's* archive
  volume, which survives — `retire_instruction()` returns `None` for them
  today and continues to. The retention story for hosted members is the
  host's, and the runbook paragraph says so in one sentence.
- `runbook.md`'s "On retention" paragraph is rewritten: the export step is
  now the documented procedure; the statutory-retention framing stays.

**Status decision (flag for sign-off):** `docs/path-conformance.yaml`'s
`GX.4` retention half is currently `named-absence`. An export-before-delete
instruction is honestly `simulated` (a real retention regime is a policy +
storage commitment, not a tarball), not `implemented`. Recommend: move GX.4
to `simulated` with a note that says exactly that, evidence pointing at
`retire_instruction` and the runbook section. This is the kind of status move
the repo owner should confirm — same sign-off habit as the MoEYS amendment.

**Files.**

| File | Change |
| --- | --- |
| `apps/join-api/job.py` | `retire_instruction()` message + `archive_export` field |
| `apps/join-api/tests/test_job.py` | update `retire_instruction` assertions; new field |
| `runbook.md` | "On retention" paragraph rewritten |
| `docs/production-delta.md` | row 37 updated: demo now exports before delete; production still needs a real retention regime |
| `docs/path-conformance.yaml` | GX.4 note (and status → `simulated`, pending sign-off) |
| `apps/console/static/app.js` | render `archive_export` if the join tab shows the instruction verbatim — check first; may be zero-change |

**Verify:** `--fast`; then one live own-server join/un-join and actually run
the printed commands — the export must produce a non-empty tarball on a real
archive volume.

**Effort:** ~2–3 hours.

---

## 3. Rate limiting on the join API

**Delta row:** "No rate limiting, no quota — the join API accepts as many
requests as it is given" on an endpoint that can register federation members.

**Design.** In-process token bucket, stdlib only, as a FastAPI dependency —
no `slowapi`, no Redis; the delta row's production side (distributed quota,
abuse monitoring) explicitly stays open.

- New module-level limiter in `app.py`, one bucket per bearer token
  (applicant and operator buckets are therefore separate), guarded by a
  `threading.Lock`, driven by an injectable monotonic clock so tests never
  sleep:

  ```python
  RATE_LIMIT = {"capacity": 30, "refill_per_minute": 30}   # generous: demo-first
  ```

- Applied to `POST /requests` (the registration surface the delta names) and
  `POST /requests/{id}/resume` (cheap to include, also drives the federation).
  **Not** applied to reads (`GET /catalogue`, `GET /requests*`) — discovery
  stays cheap — and not to approve/reject, which are already operator-token
  gated and 409-guarded by state.
- Over-limit → `429` with `Retry-After`, message in the pack's house style
  (name the limit, name the remedy).
- One quota alongside the rate: refuse a new submission when `out/join/`
  already holds N (say 200) records, with a message pointing at cleaning up
  `out/join/` — this bounds the disk-backed store the delta's "job context
  lives on local disk" row worries about, without pretending to be a real
  datastore.
- Constants live in `app.py` next to their use (the `RETRY_BUDGET` precedent
  in `job.py`), **not** in `join-policy.yaml` — rate limiting is a property
  of this service instance, not of a join, and the three(four)-key rule
  stays intact.

**Interaction to check before choosing numbers:** `scripts/acceptance.sh`'s
2.7 section and `exercises.md`'s join/un-join loop must run untouched at the
chosen limits — count their submissions first; 30/min has ample headroom for
both, but verify rather than assume.

**Files.**

| File | Change |
| --- | --- |
| `apps/join-api/app.py` | limiter + dependency on the two POST routes + store quota |
| `apps/join-api/tests/test_app_requests.py` (or new `test_app_ratelimit.py`) | bucket exhaustion → 429 + Retry-After; refill via injected clock; per-token isolation; quota refusal |
| `docs/production-delta.md` | row 34 updated: basic in-process limit exists; production still needs distributed quota + abuse monitoring |

**Verify:** `--fast`, then `--live` (acceptance 2.7 exercises the API
surface), then one manual join through the console to confirm the tab is
unaffected.

**Effort:** ~2–3 hours.

---

## 4. Drift remediation — `scripts/member.sh refresh <key>`

**Delta row:** "`scripts/member.sh drift <key>` *detects* this; nothing in
this pack *remedies* it — a production operator still has to act on what
drift reports."

**Design.** A new `refresh` subcommand — deliberately *not* a flag on
`drift`, because `drift` is documented as "no auth, no HTTP to the join API,
works whether or not it is even running" and must stay that way. `refresh` is
the opposite kind of act: it authenticates to a Security Server admin API and
mutates federation state.

Flow, reusing machinery that already exists:

1. Source `lib-stack.sh` (not just `lib-core.sh`) — that brings `.env`,
   `XROAD_BIND`, and the `api_key`/`api` session helpers.
2. Read `hurl/topology.json` (the same file `cmd_list` reads) to resolve the
   member's subsystem → `hosted_on` host → `host_ui_port`.
3. `api_key "$XROAD_BIND:$ui" "$XROAD_ADMIN_USER" "$XROAD_ADMIN_PASSWORD"`,
   then `GET /clients?...` to find the member's client id,
   `GET /clients/{id}/service-descriptions`, and
   `PUT /service-descriptions/{id}/refresh` for each — the explicit refresh
   the delta row says X-Road requires.
4. **Record the act.** Append a `refreshes:` entry to the member's
   `out/join/<id>.json` record — timestamp, per-service endpoint set after
   refresh — as an *amendment*, never overwriting `endpoint_baseline`. The
   baseline is deliberately "captured once, at join time, and never
   re-derived": it is evidence of the contract the member was admitted on,
   and refreshing the federation does not re-admit anyone. `cmd_drift`'s
   output then distinguishes "drift since join" from "drift since the last
   recorded refresh", so the operator sees both facts instead of a warning
   that never clears.
5. Print, in the house style, what was refreshed and what was not: a refresh
   makes the federation *publish* the current contract; it does not make the
   new contract *approved* — endpoint additions that exceed
   `join.allowed_methods` should make `refresh` refuse (re-run the same
   method check `validate.py` applies at join time against the freshly
   fetched spec) rather than silently publish a write endpoint.

That last clause is what keeps this a governance tool rather than a
convenience that launders unreviewed contract changes onto the bus.

**Files.**

| File | Change |
| --- | --- |
| `scripts/member.sh` | new `cmd_refresh` + usage text; `cmd_drift` learns to read `refreshes:` |
| `apps/join-api/job.py` / nothing | no API change — the record amendment is written by the script, same file, same atomic-rename pattern as `app.py`'s `_save_request` (small shared shell/python snippet) |
| `exercises.md` | exercise 3 ("catch a published contract drifting") gains the remediation step |
| `runbook.md` | drift section documents detect → review → refresh |
| `docs/production-delta.md` | row 31 updated: detect **and** remedy exist; production still needs the organisational review around it |

**Verify:** no unit-test surface (shell + live admin API) — this one is
proven the way the pack proves live behaviour: run exercise 3 end-to-end
against a joined member (join PTSB hosted, edit the mock's spec, restart the
mock, `drift` reports, `refresh`, `drift` reports clean-since-refresh,
acceptance stays green). Document that run's output in the commit message.

**Effort:** ~half a day, dominated by the live verification loop.

---

## 5. Promote `server-conf-cache-period` into `deployment.yaml`

**Delta row:** the 5s filming tune lives in `xroad-demo-local.ini`; a real
deployment should make that trade "on purpose, not by copying a demo value."

**Design.** One source of truth, analyst-facing, generated outward — the same
pattern `deployment.yaml` already uses for digests and bind:

- `deployment.yaml` gains:

  ```yaml
  xroad:
    ...
    # Proxy authorization-cache period, seconds. X-Road's documented default
    # is 60; this demo runs 5 so an ACL change is filmable (the trade and the
    # measurement: xroad-demo-local.ini's comment, docs/production-delta.md).
    # A production federation sizes this deliberately -- start from 60.
    server_conf_cache_period: 5
  ```

- `hurl/generate.py` renders the ini (the whole file — the
  `wsdl-validator-command` line and the explanatory comment come along) into
  a generated location beside its other outputs, e.g. `hurl/local.ini`,
  substituting the value from `deployment.yaml`. `lib-stack.sh` already
  requires generate to run before any `COMPOSE` use, so ordering holds.
- `docker-compose.yml`'s four `./xroad-demo-local.ini:/etc/xroad/conf.d/local.ini`
  mounts and `generate.py`'s `member_service_block` mount line all move to
  the generated path. The hand-written `xroad-demo-local.ini` is deleted —
  its comment block (including the live 60s measurement story) migrates into
  the generator's template string so the provenance survives.
- Golden corpus: the generated ini appears under `tests/golden/deployment/`;
  regenerate via `scripts/regen-golden.sh`, eyeball the diff.

**Alternative considered:** keep the ini hand-written and add a `--fast`
test asserting it agrees with a `deployment.yaml` key. Rejected: that is two
places for one value, which this pack's own "one rule, one place, no
indirection" comment (in `validate.py`) argues against.

**Files.**

| File | Change |
| --- | --- |
| `deployment.yaml` | new `xroad.server_conf_cache_period` key |
| `hurl/generate.py` | ini template + render; `member_service_block` mount path |
| `docker-compose.yml` | 4 mount lines |
| `xroad-demo-local.ini` | deleted (content moves into the template) |
| `tests/golden/**` | regenerated |
| `tests/test_golden.py` / `tests/test_addons.py` / `tests/test_compose_rw_mount_user.py` | whichever render the compose config — re-run, fix path expectations |
| `docs/production-delta.md` | row 26 updated: the value is now a declared deployment dimension, not a buried ini |
| `README.md` | the `deployment.yaml` description sentence gains the knob |

**Watch out for:** the sidecar entrypoint's `cp -a -n … backup/local.ini`
behaviour the ini's comment describes — the file must keep fully replacing
the packaged one, so the template must keep the `[proxy-ui-api]` section.
Also confirm nothing else greps for the literal filename
(`grep -rn xroad-demo-local` — today: `docker-compose.yml`, `generate.py`,
`production-delta.md` only).

**Verify:** `--fast` (golden), then a **cold `--full`** — this touches every
Security Server's boot config, so the reproducibility proof is the only
honest check.

**Effort:** ~2–3 hours + one ~13-minute `--full` run.

---

## 6. Per-applicant tokens

**Delta row:** "One shared `KP2_JOIN_APPLICANT_TOKEN` for every applicant …
| One credential per agency; should prefer mTLS." The mTLS half stays
production; this item builds the per-agency-credential half in the token
model the pack already has.

**Design.** Operator-issued named applicant tokens, alongside (not replacing)
the shared demo token:

- `POST /tokens` (operator-only): body `{"agency": "<name>"}` → responds once
  with a fresh `secrets.token_urlsafe(24)` value; the server persists only
  `{name, sha256(token), issued_at}` in `out/join/tokens.json` (same
  atomic-rename write as `_save_request`). The plaintext is never stored or
  logged — same credential discipline `app.py`'s docstring already states.
- `GET /tokens` (operator-only): names + issue dates, no hashes.
  `DELETE /tokens/{name}` (operator-only): revocation.
- `require_applicant` accepts, in order: operator token, shared applicant
  token, any issued token (compare via `secrets.compare_digest` against
  recomputed sha256). Issued-token requests resolve to a role of
  `applicant:<name>`.
- `POST /requests` records `submitted_by` on the record when the caller used
  a named token — exactly the field `app.py`'s own comment says restoring
  ownership later needs ("a `submitted_by` field and one comparison"). This
  item adds the field; it does **not** add per-request ownership
  restrictions (the demo's one-person-two-roles ergonomics stay).
- The shared token stays as the console's server-side credential and the
  zero-setup demo path; its delta row shrinks rather than disappears, and
  the row's production side becomes "shared demo credential must be disabled;
  mTLS preferred" — honest, still open.

**Files.**

| File | Change |
| --- | --- |
| `apps/join-api/app.py` | token store, three routes, `require_applicant` extension, `submitted_by` |
| `apps/join-api/tests/test_app_tokens.py` (new) | issue/list/revoke; auth with issued token; revoked token → 403; hash-only persistence; `submitted_by` recorded |
| `apps/join-api/tests/test_app_queue.py` | queue view carries `submitted_by` (if surfaced) |
| `runbook.md` | join API section: issuing an agency credential |
| `docs/production-delta.md` | row 28 updated |
| `docs/path-conformance.yaml` | check whether a §1/G0 clause cites the shared-token model; update note + evidence if so |

**Explicitly out of scope:** mTLS, token expiry, and wiring the console to
issued tokens (it keeps the shared one).

**Verify:** `--fast`; `--live`; one manual join using an issued token.

**Effort:** ~1 day. The largest item — schedule last among the join-api
changes.

---

## 7. Sequencing, commits, and cross-cutting checks

**Order: 5 → 2 → 1 → 3 → 6 → 4.**

- **5 first**: it is the only item touching every container's boot config and
  the golden corpus — land it and its cold `--full` before stacking anything
  else, so later failures can't be blamed on it.
- **2 second**: smallest, no live-behaviour change (an instruction string),
  clears the GX.4 sign-off question early.
- **1 → 3 → 6** in that order, all inside `apps/join-api/` — 1 is the
  security fix (highest value), 3 is additive middleware, 6 rewrites the auth
  dependency that 3's limiter keys on, so 6 goes after 3 to avoid rebasing
  the limiter twice.
- **4 last**: it depends on nothing above but costs the most live-verification
  time; batching its exercise-3 run after the join-api items means one joined
  member serves as the test bed for 1, 3, 6 and 4 in a single session.

One commit per item, each carrying its code + tests + `production-delta.md`
row + conformance entry together — the pack's stated rule that a status claim
and its evidence move as one.

**Cross-cutting regression watchlist** (run after each item, not only at the
end): `scripts/verify.sh --fast` (331 tests today); `tests/test_golden.py`
byte-identity; after item 5 and again after item 6, one cold `--full`; after
the join-api items, one full exercise loop (`exercises.md` 2–4: join, drift,
un-join, re-join) — the loop that has caught two real defects in this pack's
history (`FileExistsError` on re-join, the Compose healthcheck regression).

**Total estimate:** roughly 3–4 working days including live verification
runs, of which two cold `--full` cycles (~30 min wall clock) and one full
exercise loop (~1 h) are irreducible machine time.

**Open decisions needing the repo owner's sign-off before starting:**

1. GX.4 status move to `simulated` (item 2).
2. The fourth join-policy key (item 1) — it amends a file whose comment
   currently promises exactly three keys; same one-line sign-off record as
   the MoEYS amendment precedent.
3. Rate-limit numbers (item 3) — 30/min per token proposed; confirm against
   acceptance 2.7's actual submission count.
