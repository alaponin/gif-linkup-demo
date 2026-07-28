# Code review — `itu-presentation-skills-files`

Scope: `10-Knowledge-Products/KP2-GIF/KP2-build-pack` on branch `itu-presentation-skills-files`
(70 files, +10,921/−82 against `main`). Focus as requested: **simplification**,
**groundwork for a DigitalOcean deployment** (done separately), and **cyber security**.

Reviewed against the pack as it stands, not against the plans — several plan tasks are
mid-flight and their unfinished state is not treated as a finding.

General assessment: the engineering discipline here is unusually good. Generated
artefacts are genuinely generated, findings are recorded with dates and measurements,
and `docs/production-delta.md` is a more honest demo-vs-production list than most
production systems ship. The findings below are mostly about what happens when this
leaves a laptop.

Severity: **S** = security, **C** = simplification/correctness, **D** = cloud groundwork.
Priority: 🔴 fix before any cloud exposure · 🟠 fix soon · 🟡 worth doing.

---

## ✅ S1 — RESOLVED 2026-07-28 — Every X-Road surface is published on all host interfaces

`docker-compose.yml` publishes without a bind address, which means `0.0.0.0`:

```
cs        4000:4000            ss-pnea   2000:4000, 2080:8080
ca        8888:8888            ss-plr    3000:4000, 3080:8080
ss-pdga   1000:4000, 1080:8080 ss-pnia   5100:4000, 5180:8080
                               ss-moeys  6000:4000, 6080:8080
console   127.0.0.1:8090:8000   ← the only one bound correctly
```

On a droplet this exposes, to the whole internet:

1. **The five `:8080` proxy ports.** This is the severe one. X-Road's client-proxy
   interface has *no authentication* — the caller simply asserts who it is in the
   `X-Road-Client` header, because that interface is defined to sit on the agency's
   trusted internal network. Anyone who can reach `2080` can impersonate
   `PROGRESSA/GOV/PNEA/EXAMS` and read everything its ACL permits. The entire access-control
   story the demo teaches becomes false in the same moment.
2. **The Central Server admin UI** with the release image's fixed `xrd/secret`.
3. **Five Security Server admin UIs** with the `.env` password (default `secret`).
4. **The Test CA**, whose `/testca/sign` endpoint signs any CSR handed to it — so an
   attacker can mint a certificate the federation trusts.

Note that a host firewall does not save you: Docker publishes by writing its own
`nat`/`DOCKER-USER` rules that bypass UFW's `INPUT` chain. The fix is to bind explicitly.

**Fix:** add `network.bind` to `deployment.yaml` (default `127.0.0.1`), thread it through
every port mapping, and reach the UIs over an SSH tunnel or a WireGuard/VPC address. The
`console` service already does exactly this — the asymmetry is the tell that the decision
was made once and never generalised.

**Done:** `docs/superpowers/plans/2026-07-28-kp2-exposure-and-secrets.md`
Tasks 1-3. `network.bind` in `deployment.yaml` threaded through every port
mapping including `console`'s (default `127.0.0.1`); a non-loopback bind
additionally requires `network.acknowledge_public_exposure: true`, or
`scripts/lib.sh` refuses and names exactly what a non-loopback bind exposes;
`scripts/check-exposure.sh` asserts it from the rendered Compose config
itself (every profile activated, not just whatever is active), wired into
`hurl/run-linkup.sh` and `scripts/acceptance.sh`, and proven live to catch a
regression the bind-focused checks alone could not — a bare, unbound port
on an unrelated service, independent of `network.bind`'s own value.

## ✅ S2 — RESOLVED 2026-07-28 — Working default secrets are committed

`.env.example` ships `XROAD_TOKEN_PIN=Progressa123!` and `XROAD_ADMIN_PASSWORD=secret`,
`runbook.md` step 1 is `cp .env.example .env`, and the same PIN appears in committed plan
documents. Nothing anywhere forces a change, so the realistic outcome of a cloud
deployment is a federation running the password that is published in the repository.

**Fix:** ship `.env.example` with empty values and a `scripts/gen-secrets.sh` that writes
a random PIN and password; have `lib.sh` refuse to deploy when the live values equal the
example ones. Keep the demo values only in the docs that explain why demo values exist.

**Done:** `docs/superpowers/plans/2026-07-28-kp2-exposure-and-secrets.md`
Task 4 (and 5-6). `.env.example` now ships placeholders that cannot work;
`scripts/gen-secrets.sh` is the only thing that writes a real `.env`
(random, shell-safe, mode `600`); `lib.sh` refuses a `.env` that is empty,
a placeholder, or one of the values this repo used to publish (this
includes `Progressa123!`/`secret` by name, so the mechanism itself is the
record of what it closes); `hurl/generate.py` fails hard rather than
falling back to `.env.example`. Older plan/spec documents that mention the
retired PIN as part of the historical record of what was decided are left
as-is — they describe a past decision, not a live credential, and the
actual problem (nothing forcing a change) is what's closed. Also fixed
while auditing for secret leakage (Task 5): `hurl/check_scenarios.py`
printed both live secret values into its own failure message whenever
`vars.env` and `.env` disagreed. Task 6 adds a fingerprint check so a
changed PIN fails at deploy time with a clear message instead of the
certificate-shaped error it actually produces (confirmed live,
`docs/xroad-770-notes.md` §9).

## 🟠 S3 — `nin` reaches URL construction unvalidated

`apps/console/xroad.py`:

```python
url = entrypoint.rstrip("/") + call["r1_path"].format(nin=nin)
```

and `app.py`'s `_identity_held_fields()` interpolates the same value into a mock URL. `nin`
arrives from a FastAPI path parameter, and Starlette percent-decodes path parameters — so
`%2F` injects a path segment and redirects the call to a different X-Road service, or a
different path on the mock. Today the console is localhost-bound and demo-only, so this is
hardening rather than an incident; it is also precisely the bug that becomes an SSRF pivot
the first time someone puts the console behind a reverse proxy.

**Fix:** one guard. The seeded NINs are numeric —
`if not re.fullmatch(r"[0-9]{6,20}", nin): raise HTTPException(400, ...)`.

## 🟠 S4 — A new admin session per request

`_admin_session()` constructs an `AdminSession` — i.e. performs a fresh `POST /login`
against a Security Server — on every `/api/acl` call and every mutation. A page refresh
therefore logs in several times. That is credential use and audit-log noise for no benefit,
and it slows the tab the demo depends on.

**Fix:** cache one session per host, re-login on 401.

## 🟡 S5 — Upstream response headers passed through to the browser

`CallResult.headers = dict(resp.headers)` returns every upstream header verbatim to the
page. X-Road's responses are benign today, but wholesale passthrough is a habit worth not
forming. Allow-list what the inspector actually renders (status, content-type, `X-Road-*`).

## 🟡 S6 — `verify=False` is hardcoded, not derived

Correct for the Test CA, and honestly documented. But it appears as a literal in three
places in `apps/console/`, so when a real CA arrives there is no switch to throw. Derive it
from `deployment.yaml` now, defaulting to `False` for `target: docker-local`, so the real-CA
path is a config change rather than a code change.

## 🟡 S7 — Images pinned by mutable tag

`testca` is digest-pinned (good, and deliberate). `niis/xroad-security-server-sidecar:7.7.0`
and `niis/xroad-central-server:noble-7.7.0` are not. A cloud deployment should digest-pin
all three; the pack already demonstrates it knows how.

## 🟡 S8 — Cleartext secrets in the working tree

`hurl/vars.env` is generated with the token PIN and admin password in cleartext and mounted
into the Hurl container. Correctly gitignored, and reasonable on a laptop. On a droplet it
is a file readable by anyone with shell access — worth `chmod 600` at generation time, and
worth deciding whether it should be generated per-run into a tmpfs rather than persisted.

---

## 🔴 C6 — Files the pack needs to run are untracked

`git status` on this branch shows untracked, un-ignored:

```
apps/specs/                    the three OpenAPI contracts X-Road publishes from
configs/x-road-bus/2.6.yaml    the exchange definition truth.py and acceptance.sh read
scripts/teardown.sh            referenced by runbook.md
apps/data/                     seed CSVs (regenerable, but read directly by the console)
docs/xroad-8-delta.md          referenced by PLAN.md §7 P6
REVIEW.md
```

**A fresh clone of this branch cannot deploy or pass acceptance.** `apps/data/` is
defensible since `gen_seed_data.py` regenerates it deterministically, though the console
reads it at startup and would 500 without it. The OpenAPI specs and `2.6.yaml` are
hand-authored source and their absence is straightforwardly a mistake. This is the single
finding that most directly blocks the DigitalOcean work: you cannot `git clone` onto a
droplet and run.

## 🟠 C1 — `generate.py` is three tools in one file

At 1,584 lines it is now a Hurl generator, a topology compiler (`topology.json`,
`topology.sh`, `compose.members.yml`) and a validator. The topology compiler has different
consumers (`lib.sh`, the console, Compose), a different lifecycle and different tests, and
the member-parameterisation plan is about to add more to the same file.

**Fix:** extract `hurl/topology.py`. `lib.sh` and the console then depend on something small
and stable rather than on the module that also owns 145 request templates. Do it *before*
the parameterisation plan's Task 3–5, not after.

## 🟠 C5 — `production-delta.md` lists a gap that is already closed

Row 2 still reads *"Auto-approve management requests (`2.1.yaml`, deploy step 4)"*. That
shortcut was removed: `2.1.yaml` now declares `management_request_approval: explicit` and
the scenarios approve each request over the admin API. A production-gap table that lists a
gap you have already closed weakens every other row in it.

## 🟡 C2 — Video module numbers have leaked into the deployment machinery

`configs/member-pnia/2.5.yaml`, `20-ss-pnia.hurl`, `PINNED_SCENARIO_NO`, the manifest's
`scenarios:` claims — the KP's video-subtopic numbering is now load-bearing in four places,
and every reader needs a lookup table to answer "which module is PNIA?". The traceability is
worth keeping; the coupling is not.

**Option (large, flag not mandate):** name config files for the member
(`configs/member-pnia/member.yaml`) and keep `module: "2.5"` as a field *inside* them.

## 🟡 C3 — Three overlapping static validators

`hurl/check_scenarios.py`, the plugin's `check_pack.py`, and the static portions of
`acceptance.sh` all assert agreement between manifest, configs and scenarios, and have grown
independently. At minimum document which owns what; better, let `check_scenarios.py` be the
single static gate and leave `check_pack.py` to pack *shape* only.

## 🟡 C4 — `api_key()` still called that

It returns a cookie-jar path, not a key. The function comment at the top of `lib.sh` also
still says "API-key auth" and carries a `[confirm at P0]` marker resolved days ago. Renaming
it `admin_session()` costs minutes and removes a permanent source of confusion.

## 🟡 C7 — Leaked HTTP clients

`get_topology()` constructs `httpx.Client(verify=False, timeout=3.0)` per server per request
and never closes it. Use one client for the loop, or a `with` block.

## 🟡 C8 — Two Python runtimes with different floors, recorded nowhere

`generate.py` avoids `str.removeprefix()` because the host runs 3.7.9; `apps/console/app.py`
uses it freely because its container is 3.12. Both are correct and the rule is invisible.
One line in `hurl/README.md` prevents a future 3.9+ idiom landing in a host-run script.

---

## Cloud groundwork (DigitalOcean)

### D1 — Bind address is the prerequisite change

See S1. `network.bind` in `deployment.yaml`, defaulting to `127.0.0.1`, threaded through
every mapping. This belongs in the pack *now*, before the DO work starts, because it is the
difference between "safe by default" and "safe if the operator remembers".

### D2 — Write the target contract before implementing a second target

`generate.py` correctly rejects any `target` other than `docker-local`, so the seam exists.
What does not exist is a statement of what a second target must vary:

- **hostnames** — `vars.env` currently uses Compose service names, which is fine on one
  droplet and wrong the moment components split across hosts (upstream hit exactly this
  between 7.7.0 and `develop`; `docs/xroad-770-notes.md` §4 already tells the story)
- **bind addresses** (D1) · **TLS verification** (S6) · **image digests** (S7)
- **the CA** (D6) · **secrets provisioning** (S2, S8)

### D3 — Sizing conclusions, from measurements you already have

`production-delta.md` measures ~2.1 GiB per Security Server. State the conclusion: full
profile needs a 16 GB droplet, **lite is the cloud default**, and `hosted_on` — once the
parameterisation plan's Task 2 lands — is how a member joins without adding droplet cost.

### D4 — No backup or restore story

Named Docker volumes on a droplet with no snapshot policy means a lost federation is a full
redeploy plus re-registration. X-Road ships its own backup APIs for both server types. For a
demo this may be acceptable — but say so, and document `teardown.sh --purge` + redeploy as
the recovery path with its measured cost (two cold boots, already timed).

### D5 — Clock skew

X-Road signs and timestamps every message. A droplet with drifting time produces failures
that present as certificate errors. Require NTP in the deployment doc; it costs one line and
saves a day.

### D6 — The Test CA must not go to a public droplet

`xrddev-testca` is a development image whose sign endpoint will sign anything presented.
State in the target contract that it is incompatible with any published port, and that a
cloud target either keeps it strictly on a private network or moves to a real CA.

### D7 — Egress at deploy time

The stack pulls from Docker Hub and ghcr during deployment. A firewalled droplet or a
conference network needs pre-pulled images or a registry mirror. Trivial to note now,
painful to discover live.

---

## Suggested order

1. **C6** — commit the untracked source files. Nothing else can be tested from a clone until this is done.
2. **S1 + D1** — bind addresses, parameterised.
3. **S2** — generated secrets, with a deploy-time refusal on the example values.
4. **S3, S4, C7** — the console hardening trio; small, contained, same afternoon.
5. **C5** — correct the stale production-delta row.
6. **C1** — extract `topology.py` *before* the parameterisation plan's Tasks 3–5.
7. **D2** — write the target contract; then the DigitalOcean work can start against it.
