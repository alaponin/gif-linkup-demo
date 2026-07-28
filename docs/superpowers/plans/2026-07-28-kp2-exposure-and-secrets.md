# KP2 — Network Exposure and Secrets Hygiene

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. This plan implements findings **S1** and **S2** of `docs/reviews/2026-07-28-branch-review.md`. It is a prerequisite for any DigitalOcean work and should land before the join-interface plans.

**Goal:** Make the demonstration stack safe by default on a host that is not a laptop. Two changes: every published port binds to loopback unless someone explicitly and deliberately says otherwise, and no working credential ships in the repository.

**Architecture:** No new machinery. `deployment.yaml` already is the analyst-facing, git-committed, secret-free deployment spec and `scripts/lib.sh` already reads it with `yq_get` and exports the results for Compose to interpolate — `network.bind` follows `profile` and `xroad.*` down exactly that path. Secrets keep living only in `.env`; what changes is that `.env.example` stops being a working credential file and starts being a template, with a generator and a deploy-time refusal behind it.

**Tech Stack:** Unchanged — bash, Python 3 + PyYAML, Docker Compose v2.

## Global Constraints

- **Nothing in the pack may require a non-loopback bind.** Verified before starting: every host-port consumer in the pack (`acceptance.sh`, `console.sh`, `member.sh`, the runbook's admin-UI table) already addresses `localhost`. Binding to `127.0.0.1` must therefore change no behaviour on a developer machine — if any check starts failing, that is a finding, not an expected cost.
- **The canonical port numbers do not change.** This plan changes the *interface* a port binds to, never the port itself. The member-parameterisation plan's pinning constraint stands.
- **The Central Server's `xrd`/`secret` is not ours to rotate.** It is fixed in the release image and `acceptance.sh` hardcodes it deliberately. Any "is this credential still the published default?" check must exempt it, or it will demand a change nobody can make.
- **`.env` is sourced by bash** (`lib.sh` does `set -a; . .env`), so generated values must be shell-safe: no `$`, backtick, quote, backslash or whitespace.
- Generated secrets must never be echoed to stdout, into a log line, or into a commit.
- Commit after every task.

## Design decisions

1. **`network.bind` lives in `deployment.yaml`, not `.env`.** A bind address is deployment shape, not a secret, and `deployment.yaml` is already the home for shape. `lib.sh` exports it as `XROAD_BIND`; `docker-compose.yml` interpolates `${XROAD_BIND:-127.0.0.1}` into every mapping so that even a bare `docker compose up`, with no script and no environment, is safe.
2. **Public exposure requires two statements, not one.** Setting `bind` to anything other than a loopback address additionally requires `network.acknowledge_public_exposure: true`. One value can be changed by someone skimming; two cannot be changed by accident. The refusal message names what is actually being exposed — the `:8080` proxy ports accept any `X-Road-Client` header without authentication.
3. **Exposure becomes a tested property.** `scripts/check-exposure.sh` asserts that every published port binds to loopback, and it runs in the same places the other static checks run. S1 was a one-line-per-service mistake; without a test it is a one-line-per-service mistake again the next time a service is added.
4. **`.env.example` becomes a template that cannot work.** Placeholders, not values. `scripts/gen-secrets.sh` writes a real `.env`; `lib.sh` refuses to deploy while a placeholder or a known-published value is in place. The refusal is what makes the change real — a template alone just gets copied and ignored.
5. **A PIN change after deployment is a purge, and the tooling says so.** The software token was initialised with the PIN in `.env` at the time; changing it later produces X-Road errors that look like certificate faults. Detect the mismatch and say "purge and redeploy", rather than letting someone debug the wrong layer for an afternoon.

## Out of scope

The other review findings — `nin` validation (S3), session caching (S4), header passthrough (S5), `verify=False` parameterisation (S6), image digest pinning (S7), and the untracked-source-files problem (C6). S3–S5 are a separate, contained console-hardening pass; C6 is a commit, not a plan. Nothing here implements a DigitalOcean target.

---

## Task 1: `network.bind` in the deployment spec

**Files:** `deployment.yaml`, `scripts/lib.sh`, `docker-compose.yml`, `hurl/compose.hurl.yml`

**Interfaces:** Exports `XROAD_BIND`, consumed by every port mapping in Compose.

- [x] **Step 1:** record the baseline so Step 5 can prove nothing changed:

```bash
docker compose -f docker-compose.yml --profile full config > /tmp/compose-before.yaml
```

- [x] **Step 2:** add to `deployment.yaml`, above `profile:`:

```yaml
network:
  # Host interface every published port binds to. 127.0.0.1 keeps the whole
  # stack reachable from this machine and from nowhere else. Reaching it from
  # elsewhere is an SSH tunnel or a VPN, not a change here.
  #
  # Any other value ALSO requires acknowledge_public_exposure: true below --
  # see scripts/lib.sh's refusal for what is actually being exposed.
  bind: 127.0.0.1
```

- [x] **Step 3:** in `lib.sh`, next to the existing `XROAD_VERSION`/`XROAD_CS_TAG` exports, add `export XROAD_BIND=$(yq_get "$DEPLOY_SPEC" network.bind)`. `yq_get` already fails cleanly on a missing key, which is the behaviour we want for a spec file that must carry it.
- [x] **Step 4:** rewrite every `ports:` entry in `docker-compose.yml` and `hurl/compose.hurl.yml` to interpolate it, keeping the port numbers exactly as they are:

```yaml
    ports: ["${XROAD_BIND:-127.0.0.1}:4000:4000"]
    ports: ["${XROAD_BIND:-127.0.0.1}:1000:4000", "${XROAD_BIND:-127.0.0.1}:1080:8080"]
```

  The `:-127.0.0.1` default is load-bearing: a bare `docker compose up` with no `lib.sh` in the picture must still be safe. `console` already binds to `127.0.0.1` explicitly — move it to the same variable so there is one mechanism rather than one mechanism and one exception.

- [x] **Step 5:** `docker compose ... config > /tmp/compose-after.yaml` and diff. Every mapping should have gained a `127.0.0.1` host IP and nothing else should differ.
- [x] **Step 6:** with the stack up: `scripts/acceptance.sh` green, `scripts/console.sh status` healthy, and the runbook's admin-UI table still reachable in a browser. Commit.

**Verified live (2026-07-28):** `hurl/compose.hurl.yml` turned out to have no
`ports:` entries at all (only healthchecks and a shared volume) — nothing to
rewrite there, contrary to the plan's file list. Diffed the rendered config
properly (a real `docker compose config` before/after, both with the same
`.env`/`XROAD_*` values so only the bind change could show up): exactly 12
`host_ip: 127.0.0.1` lines added — one per port across `cs`, `ca`, and the
five Security Servers — nothing else differed. Applied live via `docker
compose ... up -d` (no purge needed: only the port mapping changed, so
Compose recreated the seven X-Road containers against their existing
volumes and they resumed from persisted state in under a minute; `console`
needed no recreation since its rendered binding was already `127.0.0.1`).
`docker port` confirmed every mapping now shows `127.0.0.1:<port>`, not
`0.0.0.0`. `scripts/acceptance.sh` GREEN, `scripts/console.sh status`
healthy, and every URL in the runbook's admin-UI table reachable — except
one, which surfaced a real, pre-existing, unrelated documentation bug: the
Test CA's row said `https://localhost:8888/testca/` but that endpoint has
always been plain HTTP (matches `hurl/compose.hurl.yml`'s own healthcheck,
which uses `http://`). Fixed the one line while verifying the table, since
leaving it wrong would defeat the point of the check.

## Task 2: Public exposure needs an explicit acknowledgement

**Files:** `scripts/lib.sh`, `deployment.yaml`, `hurl/check_scenarios.py`

- [x] **Step 1:** in `lib.sh`, immediately after reading `network.bind`, refuse a non-loopback bind that is not acknowledged:

```
lib.sh: deployment.yaml sets network.bind=0.0.0.0 without
network.acknowledge_public_exposure: true.

On a non-loopback interface this publishes, with no authentication:
  - the five Security Server :8080 proxy ports. X-Road's client-proxy
    interface has NO authentication -- the caller simply asserts who it is
    in the X-Road-Client header, because that interface is defined to sit on
    the agency's trusted internal network. Anyone who can reach it can
    impersonate any subsystem this server hosts.
  - the Central Server admin UI, whose credentials are fixed in the release
    image (xrd/secret) and cannot be rotated.
  - the Test CA, whose /testca/sign endpoint signs any CSR it is given.

If that is genuinely what you want, set acknowledge_public_exposure: true.
Otherwise leave bind at 127.0.0.1 and reach the stack over an SSH tunnel.
```

- [x] **Step 2:** accept `127.0.0.1`, `::1` and `localhost` as loopback; everything else needs the acknowledgement.
- [x] **Step 3:** document the key in `deployment.yaml` as a commented-out line with the one-sentence reason, so its existence is discoverable without reading `lib.sh`.
- [x] **Step 4:** add the same validation to `hurl/check_scenarios.py` so the ship gate catches it too — a spec file that would deploy publicly should not pass `--ready` quietly.
- [x] **Step 5:** test both branches for real: set `bind: 0.0.0.0` without the acknowledgement (expect the refusal), then with it (expect a deploy that works and a loud one-line warning at the top of every script run). Restore `127.0.0.1`. Commit.

**Verified live (2026-07-28):** Step 3 landed as part of Task 1's edit to
`deployment.yaml` (natural to add both the field and its commented sibling
in one pass). `yq_get` prints Python's `bool` repr (`True`, capital T) for a
YAML `true`, so the `lib.sh` comparison checks against the literal string
`"True"`, not `"true"` — confirmed by testing `yq_get` on a real boolean
before writing the check, not assumed. `check_scenarios.py` reads the same
key through plain `yaml.safe_load` instead, so there it's a real Python
`bool` and the comparison is `is not True` — two different representations
of the same value in two different readers, both confirmed correct rather
than one copied to the other. Tested all four combinations live (refuse /
warn-and-proceed, in both `lib.sh` and `check_scenarios.py`): unacknowledged
`0.0.0.0` refuses with the exact message and exit 1 in both; acknowledged
`0.0.0.0` prints the warning and exits 0 in `lib.sh`, and passes clean in
`check_scenarios.py`. Restored `deployment.yaml` byte-identical to its
Task 1 state (`diff` clean) and re-confirmed a warning-free source and a
green `scripts/acceptance.sh`.

## Task 3: Exposure as a tested property

**Files:** `scripts/check-exposure.sh` (new), `scripts/acceptance.sh`, `hurl/run-linkup.sh`

- [x] **Step 1:** write `scripts/check-exposure.sh`: read `docker compose ... config --format json`, walk every service's published ports, and fail listing any whose host IP is absent, `0.0.0.0` or `::`. Exit 0 when every mapping is loopback, or when `acknowledge_public_exposure` is set (in which case print what is exposed and still exit 0).
- [x] **Step 2:** confirm the exact JSON shape on the running Compose version before relying on it — `docker compose config --format json | jq '.services[].ports'` — and pin the field names the script reads to what you observed.
- [x] **Step 3:** call it from `hurl/run-linkup.sh` before it brings containers up, so a misconfiguration is caught before anything listens rather than after.
- [x] **Step 4:** also call it at the top of `scripts/acceptance.sh`, so an unexpectedly exposed stack fails the suite rather than passing it.
- [x] **Step 5:** prove it catches a regression: temporarily add a service with a bare `ports: ["9999:9999"]`, confirm the script fails and names it, remove it. Commit.

**Verified live (2026-07-28):** confirmed the JSON shape empirically before
writing anything (Compose v5.2.0): a port entry has no `host_ip` key at all
when unbound, gains `host_ip: "127.0.0.1"` once a bind is interpolated —
absence, not `null` or `""`, is the "exposed" signal the script checks for.
`console` (profile `demo`) and `hurl` (profile `tools`) are both omitted
from `docker compose config` unless their profile is explicitly activated,
confirmed by rendering with none, then with `--profile full --profile demo
--profile tools` together — the script always activates all three so it
covers every service regardless of what's currently running. Tested every
path for real: safe config → exit 0, unacknowledged `0.0.0.0` → `lib.sh`'s
own gate refuses first (belt-and-suspenders, confirmed working), acknowledged
`0.0.0.0` → exit 0 listing all 13 exposed ports including `console`, and the
plan's own regression case — a bare `ports: ["9999:9999"]` on `cs` with
`network.bind` left correctly at `127.0.0.1` — fails and names `cs:9999`
exactly, proving the script catches what no `deployment.yaml` field alone
ever could. Confirmed `hurl/run-linkup.sh` refuses before touching any
container (5 Security Servers still running, untouched, after the refusal).
`scripts/acceptance.sh` GREEN with the check's pass-line as its first
output.

## Task 4: `.env.example` becomes a template, and a generator writes the real thing

**Files:** `.env.example`, `scripts/gen-secrets.sh` (new), `scripts/lib.sh`, `hurl/generate.py`, `runbook.md`, `README.md`

- [ ] **Step 1:** rewrite `.env.example` with placeholders that cannot work:

```
# KP2 build pack — secrets only. Generated by scripts/gen-secrets.sh; never
# committed (.gitignore). Deployment shape lives in deployment.yaml.
#
# Do NOT copy this file by hand and do NOT invent values here:
#   scripts/gen-secrets.sh
# writes a .env with random, shell-safe values. The pack refuses to deploy
# while the placeholders below are still in place.
XROAD_TOKEN_PIN=CHANGEME-run-scripts-gen-secrets.sh
XROAD_ADMIN_USER=xrd
XROAD_ADMIN_PASSWORD=CHANGEME-run-scripts-gen-secrets.sh
```

- [ ] **Step 2:** write `scripts/gen-secrets.sh`: generate a token PIN and an admin password from a shell-safe alphabet (`A-Za-z0-9` plus a small punctuation set excluding `$ \` ' " \\` and whitespace — see Global Constraints), at least 20 characters, from `/dev/urandom` via `LC_ALL=C tr -dc`. Write `.env` with mode `600`. Print that it wrote the file and **never print the values**.
- [ ] **Step 3:** refuse to overwrite an existing `.env` without `--force`, and make `--force` print the Task 6 warning: the software token was initialised with the current PIN, so rotating it requires `scripts/teardown.sh --purge` and a redeploy.
- [ ] **Step 4:** in `lib.sh`, after sourcing `.env`, refuse to proceed when `XROAD_TOKEN_PIN` or `XROAD_ADMIN_PASSWORD` is empty, still contains `CHANGEME`, or equals one of the historically published demo values (`Progressa123!`, `secret`, `Secret1234`). Name `scripts/gen-secrets.sh` in the message. **Exempt the Central Server's `xrd`/`secret`** — it is fixed in the image, `acceptance.sh` hardcodes it deliberately, and this check must not demand a change that cannot be made.
- [ ] **Step 5:** in `hurl/generate.py`, change `read_env()` to **fail** when `.env` is absent instead of falling back to `.env.example`. With placeholders in the example file that fallback would now generate a `vars.env` full of `CHANGEME` and fail deep inside a Hurl run, which is the worst place to discover it.
- [ ] **Step 6:** update `runbook.md` step 1 from `cp .env.example .env` to `scripts/gen-secrets.sh`, and mention it wherever `README.md` describes first run. Commit.

## Task 5: Secrets at rest in generated files

**Files:** `hurl/generate.py`, `scripts/lib.sh`

- [ ] **Step 1:** `hurl/vars.env` is generated containing the token PIN and admin password in cleartext and is mounted into the Hurl container. Set mode `600` on it at generation time, in the same place it is written.
- [ ] **Step 2:** audit for secret leakage into output: `grep -rn "XROAD_TOKEN_PIN\|XROAD_ADMIN_PASSWORD" scripts/ hurl/ apps/` and confirm no path echoes a value, including in `set -x` traces and error messages. Fix any that do.
- [ ] **Step 3:** confirm `.gitignore` still covers `.env`, `hurl/vars.env` and `out/`; add an explicit note next to the `.env` entry that it holds live credentials.
- [ ] **Step 4:** commit.

## Task 6: A changed PIN fails loudly, not confusingly

**Files:** `scripts/lib.sh`, `hurl/run-linkup.sh`, `docs/xroad-770-notes.md`

- [ ] **Step 1:** on a successful deploy, record a fingerprint of the PIN actually used — `printf '%s' "$XROAD_TOKEN_PIN" | shasum -a 256` — into `out/.token-fingerprint` (mode `600`, never the value itself).
- [ ] **Step 2:** on subsequent runs, if the fingerprint exists, disagrees with the current `.env`, and federation volumes still exist, fail with: the software token was initialised with a different PIN; either restore the old value or `scripts/teardown.sh --purge` and redeploy.
- [ ] **Step 3:** confirm live that this is the real behaviour — deploy, change the PIN in `.env`, redeploy, and record what X-Road actually does (which error, at which step, and whether the sidecar's autologin or the admin API fails first) in `docs/xroad-770-notes.md`. If it turns out to be benign, say so and downgrade the check to a warning; do not assert a failure mode nobody observed.
- [ ] **Step 4:** commit.

## Task 7: Documentation and verification

**Files:** `docs/production-delta.md`, `runbook.md`, `README.md`, `docs/reviews/2026-07-28-branch-review.md`

- [ ] **Step 1:** `production-delta.md` gains rows for what remains demo-only after this plan: loopback binding as the *only* network control (production needs network segmentation, a reverse proxy with real TLS, and authenticated admin access), and the Central Server's unrotatable fixed credentials. While in the file, correct the stale row that still lists auto-approve as a demo shortcut — it was replaced by `management_request_approval: explicit` (review finding C5, one line, and leaving it wrong undermines the rows that are right).
- [ ] **Step 2:** `runbook.md` gains a short "Reaching the stack from another machine" note: SSH local port forwarding, not a bind change.
- [ ] **Step 3:** mark S1 and S2 resolved in the review document, with the date and what was done.
- [ ] **Step 4: full verification on a clean machine.** `scripts/gen-secrets.sh` → `hurl/run-linkup.sh` → `scripts/seed.sh` → `scripts/acceptance.sh` green → `scripts/console.sh up` and all three tabs work → `scripts/check-exposure.sh` green. Then, from a second machine on the same network, confirm every port is refused. Commit.

---

## Sequencing and risk

Tasks 1–3 are independent of 4–6 and can be done first; Task 3 is what stops S1 from recurring and is worth more than Task 1 alone. Task 4 is the riskiest, because a refusal check that is too eager will block a working developer stack — write the check to name exactly which variable is wrong and what to run, and test the false-positive path (a legitimately generated `.env`) before committing. Task 6 Step 3 is an investigation and may end in "no check needed"; that is an acceptable outcome and better than a guard against a failure mode nobody confirmed.

The end state is that the only way to expose this stack publicly is to write a second, explicit line in a git-committed file that says you meant it — and that a fresh clone cannot deploy at all until it has generated its own credentials.
