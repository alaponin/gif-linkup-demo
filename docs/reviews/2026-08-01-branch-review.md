# Code review (third pass) — `itu-presentation-skills-files`

Scope: `10-Knowledge-Products/KP2-GIF/KP2-build-pack`, working tree as of 2026-08-01,
continuing `docs/reviews/2026-07-29-branch-review.md`. Focus as requested:
**simplification**, **cloud groundwork (DigitalOcean)**, **testing**, **cyber security**.

Findings continue the existing numbering. **S** = security · **C** = complexity ·
**T** = testing · **D** = deployment.
Priority: 🔴 blocks cloud · 🟠 fix soon · 🟡 worth doing.

**Method note.** This pass reviewed the working tree, not a `git diff` — `.git` was not
reachable from the review environment, so "still open" below means *the code still has the
defect*, not *no commit claims to have fixed it*. Everything asserted here was read in the
file named. `pytest tests/ apps/console/tests/` was run: **27 passed, 3 failed**, all three
failures being `tests/test_tiers.py` on a host with no Docker CLI — which is exactly the
contract T1 landed, so this is a correct failure, not a regression (see T4).

---

## Verification of the previous review's open findings

**Closed, and closed well:**

- **C11 (`lib.sh` did six jobs)** — split into `lib-core.sh` (pure: `PACK_DIR`, `log`/`fail`,
  `retry`, `yq_get`, no exits) and `lib-stack.sh` (Docker, `.env`, policy). The PIN-fingerprint
  guard moved out of source-time into `check_token_fingerprint()` in `run-linkup.sh`, so
  `console.sh status` and `member.sh list` no longer pay for a `docker volume inspect` they
  never use. This is the fix as specified.
- **T1 (fast tier claimed to be Docker-free)** — `README.md` now states the narrower, true
  claim: no running containers, no network, no federation, *but the Docker CLI is required*,
  and a running daemon is not. The reasoning is in the text rather than asserted.
- **T3 (nothing tests the tiers)** — `tests/test_tiers.py` exists and is unusually
  well-argued: its docstring explains why it calls `check-exposure.sh` directly instead of
  `verify.sh --fast` (self-recursion through pytest collection), and why it fakes
  `DOCKER_HOST` instead of stopping the real daemon.
- **T2 / S11 (snapshots)** — `scripts/federation.sh` is gone, and `production-delta.md`
  records the measurement that justified retiring it rather than just deleting the code.

**Still open, unchanged:**

- **🔴 S9 — `shasum` will not exist on a minimal droplet.** `hurl/run-linkup.sh:79` and `:148`
  still call `shasum -a 256` with no fallback. On Debian/Ubuntu that binary comes from
  `perl-modules`; `sha256sum` (coreutils) is the portable name. This is two lines and it is
  the first thing that will break on a DigitalOcean droplet. It was flagged as the top item
  three days ago and is still the top item.
- **🟠 S3 — `nin` reaches URL construction unvalidated.** See S12 below; the finding has
  widened, not narrowed.
- **🟠 S4 — a new admin session per request.** `app.py:_admin_session()` still constructs a
  fresh `xroad.AdminSession` — i.e. a full HTTPS `POST /login` — per call site.
  `GET /api/acl` does one per service; `journal.reset()` does one per journal entry *and* one
  per expected-ACL service. A three-service reset is six logins.
- **🟡 S5 — upstream headers passed to the browser.** `xroad.py:CallResult.headers` is
  `dict(resp.headers)` wholesale, serialised into `/api/exchange/{nin}`'s response.
- **🟡 S6 / S7 / S8** — `verify=False` hardcoded; images pinned by mutable tag (see C13);
  cleartext secrets in the tree (`.env`, `hurl/vars.env`, both mode 600 — accepted by design,
  but see D9).
- **🟠 C1 — `generate.py` is three tools in one file.** 1,639 lines, of which `main()` is
  ~700. See C14 for a concrete, low-risk way out.
- **🟡 C7 — leaked HTTP clients.** Confirmed in three places; see S14.
- **🟠 C9 — the pack cannot verify itself outside this monorepo.** `verify.sh:18` still hard-
  codes `$PACK_DIR/../../ITU-Giga-KP-Plugin/skills/kp-solution-verify/scripts/check_pack.py`
  and `fail`s if it is absent. A droplet checkout of this pack alone cannot run `--fast`.
- **🟠 C10 — the Python floor.** Now three different numbers in the tree: `.python-version`
  says `3.11.14`, `hurl/README.md` states a 3.9+ host floor, and both Dockerfiles are
  `python:3.12-slim`. `verify.sh:38` runs `scripts/check-python-floor.sh` "if it exists" —
  **it does not exist**, so the guard that was supposed to settle this is a no-op. (For what
  it is worth, `generate.py` and the golden corpus both ran clean under 3.10 during this
  review, so the real floor is at most 3.10.)
- **🟡 D8 — the cloud contract is still unwritten.** `deployment.yaml` still carries
  `target: docker-local` as the only accepted value, still correctly enforced in
  `generate.py:947`. Nothing has moved here.

**Cannot verify:** C6 (untracked files) and C12 (uncommitted work) — both require `git`.

---

## 🔴 S12 — `nin` is a path-traversal and SSRF parameter, on two paths not one — RESOLVED 2026-08-01

Resolved by `docs/superpowers/plans/2026-08-01-kp2-console-request-boundary.md` Task 1:
`NIN_RE` applied at all three call sites (`get_exchange`, `get_exchange_negative`,
`_identity_held_fields`), plus a docstring line on `xroad.exchange()` naming `app.py`
as the boundary. Checked against real behaviour, not assumed: three of the six shapes
tested (a %2F traversal, a raw `..` segment, an empty segment) never actually reach the
handler over real HTTP at all — decoded or normalised into a 404 before this code runs,
confirmed against both `TestClient` and a real `uvicorn` process. The validator is still
exercised directly against all six shapes regardless, as the defense-in-depth this plan
called for. Superseded S3 (`2026-07-28-kp2-console-hardening.md` Task 1, marked withdrawn).

S3 named `/api/exchange/{nin}`. The tree now has a second, worse instance.

```python
# app.py:151        no validation at all
url = entrypoint.rstrip("/") + call["r1_path"].format(nin=nin)

# app.py:198        f-string, straight into a URL, unvalidated
httpx.get(f"{TRUTH.identity_mock_base_url}/persons/{nin}/held-fields", ...)
```

`r1_path` is `/r1/PROGRESSA/GOV/PNIA/IDENTITY/identity-api/persons/{nin}`. A `nin` of
`../../../../..%2f<anything>` rewrites which X-Road service the request addresses — the
console becomes a general-purpose client of the Security Server's r1 proxy, asserting
`X-Road-Client: PROGRESSA/GOV/PNEA/EXAMS`, for any caller who can reach `:8090`. The
`/held-fields` path does the same against the mock registry, off the bus entirely.

The NIN format is not in doubt: `apps/data/persons.csv` is eleven digits throughout.

```python
NIN_RE = re.compile(r"\A[0-9]{11}\Z")

def _validated_nin(nin: str) -> str:
    if not NIN_RE.match(nin):
        raise HTTPException(400, "nin must be 11 digits")
    return nin
```

Apply it in `get_exchange`, `get_exchange_negative` and `_identity_held_fields`. A FastAPI
`Path(pattern=...)` annotation does the same job declaratively and is probably better here.
Cheap, and it closes the finding on both paths at once.

## 🔴 S13 — the console's write endpoints have no CSRF protection — RESOLVED 2026-08-01

Resolved by `docs/superpowers/plans/2026-08-01-kp2-console-request-boundary.md` Task 2:
a `_require_console_origin` dependency applied to all three write endpoints, requiring
`X-KP2-Console: 1` (a cross-origin form cannot set it) and rejecting a present-and-wrong
`Origin`. Also applied to `GET /api/exchange/{nin}` and its `/negative` sibling (Step 5's
decision: they don't mutate the ACL, but do cause real bus calls a cross-origin `<img
src>` could trigger). `scripts/console.sh reset` updated in the same commit. Verified live
in a real browser with this finding's own four-line form, served from a different origin:
refused with 403, journal left empty.

`POST /api/acl/revoke`, `/api/acl/grant` and `/api/reset` take no body, no custom header and
no token. Any web page the presenter's browser happens to have open can submit

```html
<form action="http://localhost:8090/api/acl/revoke" method="POST">
```

— a simple cross-origin form POST, no preflight, no CORS check, because CORS does not stop
the *request*, only the attacker reading the *response*. The attacker does not need the
response; the side effect is the attack. Loopback binding does not help: the browser is on
the host.

The realistic impact is not dramatic (an ACL flipped mid-demo, which the watchdog would undo
in ≤120s) but the shape is exactly wrong for a tool whose entire purpose is demonstrating
access control, and it becomes serious the moment the cloud contract puts this behind
anything. Two lines of defence, both cheap:

1. Require a header the browser cannot set cross-origin from a form — e.g.
   `Content-Type: application/json` plus an explicit `X-KP2-Console: 1` check.
2. Reject requests whose `Origin`/`Sec-Fetch-Site` is not same-origin.

This also mitigates DNS rebinding, which is the same attack with a longer fuse.

## 🟠 S14 — every `httpx.Client` in the console leaks, and one leaks per iteration

C7 identified this; it is worth restating with the count, because one instance is in a loop:

```python
# app.py:117 — inside `for ss in TRUTH.topology["security_servers"]`
resp = httpx.Client(verify=False, timeout=3.0).get(...)
```

A five-server `GET /api/topology` opens five clients (five connection pools, five sets of
sockets) and closes none. `xroad.py:34` (`AdminSession`) and `xroad.py:136` (`exchange`) do
the same once each. With S4's session-per-request on top, a demo that leaves the topology tab
polling will accumulate file descriptors for as long as it runs.

The fix collapses S4, S14 and C7 into one change: a module-level
`httpx.Client(verify=False)` reused everywhere, plus a small `dict[str, AdminSession]` cache
keyed on host, with sessions rebuilt on 401. That is fewer lines than the code it replaces.

## 🟠 S15 — the journal's crash-safety guarantee is undercut by a non-atomic write — RESOLVED 2026-08-01

Resolved by `docs/superpowers/plans/2026-08-01-kp2-console-journal-integrity.md` Task 1:
`_write` now writes a temp file beside the target (not `/tmp` -- `OUT_DIR` is a Docker
bind mount, and `os.replace`'s atomicity only holds within one filesystem) and
`os.replace`s it in. `_read` refuses a corrupt file with a `RuntimeError` naming the path
and the two recovery options, rather than the tempting-but-wrong fix of returning `[]`
(that would silently convert "may be mid-mutation" into "nothing to do"). Confirmed
`scripts/acceptance.sh`'s journal check is unaffected: a clean reset still writes exactly
`"[]"`.

`journal.py` is built entirely around one promise: *a crash between the pending write and the
live call still leaves enough on disk to reverse*. `test_crash_mid_write_recovery` proves the
logic. But the write itself is:

```python
def _write(self, entries): self.path.write_text(json.dumps(entries, indent=2))
```

`write_text` truncates, then writes. A crash *inside that window* leaves a truncated file, and
then `_read()` raises `json.JSONDecodeError` — which means `is_dirty()` raises, which means
`_lifespan`'s startup reset raises, which means the container will not start, and
`acceptance.sh:59`'s journal check reads a non-empty non-`[]` file and `fail`s the suite. The
one failure mode the module was designed to survive is the one that bricks it.

```python
def _write(self, entries):
    self.path.parent.mkdir(parents=True, exist_ok=True)
    tmp = self.path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, indent=2))
    os.replace(tmp, self.path)   # atomic on POSIX
```

Worth a test that writes a truncated journal file and asserts the app still starts. See T5.

## 🟠 S16 — concurrent mutations can lose a journal entry — RESOLVED 2026-08-01

Resolved by the same plan's Task 2: a module-level `_MUTATE_LOCK` (`threading.Lock`,
not `async def` -- converting the mutate endpoints to `async def` would put blocking
httpx calls on the event loop, trading this finding for S17) held across the whole of
`_mutate_acl` and around `post_reset`/the watchdog reset via one shared
`_reset_locked()`. Scoped explicitly in a comment: one console process, not a
distributed lock. Confirmed the concurrency test actually catches the race before
keeping it -- with the lock replaced by a no-op, two real threads crashed outright on
the atomic write's shared temp-file path rather than quietly losing an entry, an even
sharper failure than expected and proof the lock is load-bearing.

`append_pending` and `mark_applied` are read-modify-write with no lock. `_mutate_acl` is a
`def` (not `async def`) endpoint, so FastAPI runs it in a threadpool — two concurrent POSTs
genuinely interleave. Two clicks in quick succession can produce: A reads `[]`, B reads `[]`,
A writes `[x]`, B writes `[y]`, and entry `x` is gone from the journal while its live mutation
already happened. That is precisely the state `reset()` cannot repair, and it will present as
"reset says ok but the ACL is wrong" — the failure mode `production-delta.md` warns
discredits the pack.

A single `threading.Lock` around the mutate path is sufficient and honest for a demo tool.

## 🟠 S17 — the watchdog freezes the whole app while it runs — RESOLVED 2026-08-01

Resolved by the same plan's Task 3: both call sites go through one shared
`_reset_locked()` wrapped in `await asyncio.to_thread(...)`. The startup reset in
`_lifespan` is deliberately **non-blocking** (the decision Task 3 Step 2 asked to be
made explicitly, not by accident): the alternative -- blocking startup until the reset
completes -- means `/api/health` cannot answer until every reset HTTP call finishes,
which is very likely what `verify.sh --full`'s "console health check still failing 30s
after `console.sh up`" retry loop was papering over. A console that briefly reports
healthy while a startup reset reconciles a dirty journal in the background is
acceptable for a demo tool explicitly outside the acceptance path; the mutate lock and
watchdog still enforce the invariant either way. Confirmed shutdown does not hang on
the now-backgrounded task despite a running thread being uncancellable, and confirmed
live against a real Security Server: revoke, no heartbeat, `/api/health` returned 200
on all 150 of 150 one-second polls across the wait, and the watchdog fired and
restored the ACL based on genuine heartbeat staleness. Both new regression tests
needed a real design correction while writing them: `asyncio.wait_for`'s own timeout is
scheduled on the same loop it would need to detect as stuck, so only total elapsed
wall-clock time (not a cooperative asyncio timeout) can catch a starved loop.

```python
async def _watchdog():
    ...
    journal_mod.reset(JOURNAL, _admin_session, ...)   # blocking httpx, inside the event loop
```

`reset()` performs several blocking HTTPS logins at up to 10s timeout each. For that whole
period the ASGI event loop is stopped: `/api/health` does not answer, `/api/heartbeat` does
not answer (so the page's own heartbeat cannot land), and `verify.sh --full`'s console health
probe can fail for a reason that has nothing to do with the console being broken. Same defect
in `_lifespan`'s startup reset, which blocks startup entirely.

`await asyncio.to_thread(journal_mod.reset, ...)` in both places.

## 🟠 C13 — the reproducibility rule is applied to one image out of five — RESOLVED 2026-08-01

Resolved by `docs/superpowers/plans/2026-08-01-kp2-reproducible-builds.md` Task 1 and Task 2:
Task 1 froze both images' Python dependencies into hash-verified `requirements.txt` files
(`pip install --require-hashes`), resolved from the versions actually running. Task 2 pinned
the remaining four images by digest -- `cs_digest`/`ss_digest` alongside `deployment.yaml`'s
existing `cs_tag`/`version` (same `tag@sha256:…` style as `testca_tag`), `hurl` pinned inline
in `hurl/compose.hurl.yml` (a build/test tool, not part of the federation, so no
`deployment.yaml` key), and `python:3.12-slim` pinned in both Dockerfiles. Verified: a full
`teardown.sh --purge` → redeploy → `acceptance.sh` on the pinned images, all four generated
artefacts stay byte-identical for both profiles, and two independent clean builds of both
images produce identical `pip freeze` output. Supersedes
`docs/superpowers/plans/2026-07-28-kp2-cloud-target-contract.md` Task 2, now withdrawn.

`deployment.yaml` digest-pins the Test CA (`latest@sha256:018e9f…`) and cites PLAN.md
decision 1 for why. The other four images are not pinned at all:

| Image | Where | Pin |
| --- | --- | --- |
| `xrddev-testca` | `deployment.yaml` | ✅ digest |
| `niis/xroad-central-server` | `deployment.yaml` `cs_tag: noble-7.7.0` | mutable tag |
| `niis/xroad-security-server-sidecar` | `deployment.yaml` `version: 7.7.0` | mutable tag |
| `ghcr.io/orange-opensource/hurl` | `hurl/compose.hurl.yml` | **`:latest`** |
| `python:3.12-slim` | both Dockerfiles | mutable tag |

`hurl:latest` is the one that will actually bite: it is the tool that drives every admin API
call in the deploy, and an upstream release changing `--retry` semantics or report format
would surface as a mysterious federation failure. The X-Road tags at least name a version.

Related and in the same change: **neither Dockerfile pins its Python dependencies.**

```dockerfile
RUN pip install --no-cache-dir fastapi uvicorn httpx pyyaml
```

There is no `requirements.txt` and no lockfile anywhere in the pack. A rebuild in six months
produces a different console. For a pack whose headline claim is *reproducibility*, and whose
`--full` tier is *the reproducibility proof*, this is the largest remaining hole in that
claim — and it is a supply-chain exposure too, not only a determinism one.

## 🟠 C14 — the concrete way out of `generate.py` (C1)

C1 says "three tools in one file" and is right, but "split it" is a big ask. There is a much
smaller change that removes most of the pain and is nearly risk-free, because the golden
corpus proves it byte-for-byte:

`main()` is ~700 lines of f-strings emitting Hurl. Because Hurl uses `{{var}}` and Python
f-strings use `{}`, every literal brace is doubled and every Hurl variable is *quadrupled*:

```python
GET https://{{{{cs_host}}}}:4000
```

That is `{{{{cs_host}}}}` in source to produce `{{cs_host}}` in output. Meanwhile the file
already contains the right answer — `SS_BRINGUP_INIT`, `MEMBER_SIGN_KEY`, `SERVICE_ACL` and
friends are plain strings using `sub()`'s `@name@` convention, which does not fight Hurl at
all.

**Move the remaining inline f-strings into `hurl/templates/*.hurl.tmpl` and render them with
the existing `sub()`.** Then:

- the templates become real `.hurl` files — syntax-highlightable, diffable, greppable,
  reviewable by someone who knows X-Road but not Python;
- the `{{{{ }}}}` class of bug disappears entirely;
- `main()` shrinks to what it should be: load config → allocate → render → write;
- and C1's actual split (allocator / templates / writer) becomes obvious afterwards rather
  than being a prerequisite.

Do it one template at a time; `tests/test_golden.py` fails loudly on any byte that changes.
This is the single highest-value simplification available in the pack.

## 🟠 C15 — shell-to-Python string interpolation, used as a pattern

Five places build a Python program by string-substituting shell variables into its *source*:

- `scripts/lib-core.sh:28` — `yq_get()` interpolates both `$1` (path) and `$2` (key)
- `scripts/check-exposure.sh:42` — interpolates `$DEPLOY_SPEC`, which comes from the
  `KP2_DEPLOY_SPEC` **environment variable**
- `scripts/member.sh:59` — interpolates `$key` (a user-supplied CLI argument) and `$PACK_DIR`
- `scripts/acceptance.sh:147, 157, 175` — interpolates `$PACK_DIR`
- `scripts/lib-stack.sh:113` — interpolates `$TOPO_JSON`

A single apostrophe in any of those values is arbitrary code execution. Today none of them is
attacker-controlled, so this is a latent defect and not a live vulnerability — but
`KP2_DEPLOY_SPEC` and `member.sh remove <key>` are already one step removed from a person
typing, and a checkout path containing a quote is enough to break `yq_get` for everyone.

The fix pattern is **already in this codebase, done correctly**: `member.sh`'s manifest edit
uses `python3 - "$key" "$PACK_DIR/manifest.yaml" <<'PY'` and reads `sys.argv`. Apply that
everywhere; it is mechanical, and it makes the heredocs quoted (`<<'PY'`), which also stops
shell expansion inside the Python.

## 🟡 C16 — housekeeping

- `.DS_Store` is committed at the pack root and under `docs/superpowers/`, and is **not** in
  `.gitignore` — which is otherwise the most carefully-reasoned `.gitignore` I have read.
- No `.dockerignore`. Both image builds send their whole context; `apps/console`'s context
  includes `tests/` (with all four fixture trees) and `__pycache__/`.
- `scripts/check-python-floor.sh` is referenced by `verify.sh` and does not exist (C10).

---

## Testing

## 🟠 T4 — there is no CI in this pack

The previous review cited "the CI workflow's own header" as one of the pack's best pieces of
writing. There is no `.github/` directory in `KP2-build-pack` today. Either it lives at the
monorepo root (in which case the pack cannot be tested standalone — the same defect as C9), or
it was removed. Either way, the three tiers are currently only ever run by a person choosing
to run them.

This matters more than it usually would, because `--fast` is ~8s and genuinely good. The
missing piece is small: a workflow that runs `verify.sh --fast` on push. It needs C9 resolved
first (the ship-gate path) — which is another reason to promote C9.

## 🟠 T5 — the untested paths are the ones that would hurt

The console's unit tests are good — `test_xroad.py`'s four negative cases (409-as-success both
ways, non-`AccessDenied` 500, transport failure) test exactly the distinctions that were
confirmed live, and `test_truth.py`'s `inconsistent` fixture tests a refusal. The gaps are
specific:

1. **`apps/mock-registry/app.py` has no tests at all.** Its `DECLARED_FIELDS` filter is the
   pack's *headline privacy claim* — purpose limitation proved by absence — and it is
   currently only ever exercised inside a ~6–15 minute live run via `acceptance.sh` 2.6.3.
   Three unit tests (declared fields returned; undeclared field withheld; `/held-fields`
   returns names and never values) would move the pack's most important claim into the 8s
   tier. `persons.csv` carries `mother_name`, `birth_registration_no` and
   `residence_address` — none declared — so the fixture already exists.
2. **No test for a corrupt journal file** (S15).
3. **No test for concurrent mutation** (S16) — a two-thread test would fail today.
4. **`generate.py`'s allocators are covered only via the golden corpus.**
   `allocate_ports()`'s `FORBIDDEN_PORT_RANGE` logic exists *because* of two live incidents
   (5000 and 7000, both documented in `production-delta.md`), and no test asserts that a
   forbidden port is actually refused. That is a regression waiting to happen in the one
   function whose bugs cost an afternoon each to diagnose.

## 🟡 T6 — `fetch_retry`'s success criterion is known-wrong and recorded as such

`production-delta.md` records a live run where `acceptance.sh` hit `MISMATCH … empty response`
because `fetch_retry` treats curl's exit code as success, and an X-Road REST 200 with a
not-yet-valid JSON body passes that test. It was honestly recorded as "not investigated
further, outside what this measurement task asked" — correct at the time. It is now a known
flake in the headline acceptance check. `curl … | python3 -c 'import json,sys; json.load(sys.stdin)'`
inside the retry loop, or a `jq -e .` guard, makes the retry mean what it says.

---

## Cloud groundwork (DigitalOcean)

D8 is right that the contract is the blocker. Concretely, here is what a droplet changes,
ordered by what will break first:

**🔴 D9 — Sizing is the first real constraint, and it is already measured.**
`production-delta.md` measures ~2.1 GiB per Security Server, ~1.8 GiB for the CS, and
15–17 GiB total for a 5-server `full` profile. That is a **$96/mo 16 GB droplet running at its
limit**, or a 32 GB one to be safe. `profile: lite` (3 servers, ~370s deploy vs ~918s) fits
comfortably in 8 GB. **The cloud contract should make `lite` the default cloud profile and say
why**, with `full` as an explicitly-sized opt-in. This is the single most consequential line
in the contract and the data to write it already exists.

**🔴 D10 — `bind: 127.0.0.1` + SSH tunnel is the right cloud posture, and should be written
down as the contract, not left as a delta-table row.** Everything the pack needs on a droplet
works over `ssh -L`. The alternative — a reverse proxy with real TLS and authentication in
front of the admin UIs and the console — is a substantially larger piece of work, and the
`acknowledge_public_exposure` gate exists precisely so nobody reaches for it casually. Say so
in `deployment.yaml`'s comment block for `target: do-droplet`, and keep the gate.

**🟠 D11 — Host-tool portability, beyond S9.** A minimal Ubuntu droplet does not have: `jq`
(used throughout `acceptance.sh`), `hurl` (containerised — fine), `shasum` (S9), or a Python
with PyYAML. `docker compose` v2 as a plugin is present on DO's Docker marketplace image but
not on a bare one. The cloud contract needs a `scripts/preflight.sh` that checks for these by
name and fails with an install line, rather than each one failing separately and cryptically
somewhere inside a 6-minute deploy. Note also that `acceptance.sh` uses `mapfile` and
`topology.sh` uses `declare -A`, so bash ≥4 is a hard requirement — true on Ubuntu, worth
asserting.

**🟠 D12 — Container hardening is not started. — RESOLVED 2026-08-01** Both images run as root, neither declares a
`USER`, neither has a `HEALTHCHECK` (the console's health is checked externally by
`verify.sh`, the mocks' by `seed.sh` — both via `docker exec`), and dependencies are unpinned
(C13). On a single trusted laptop this is defensible. On a droplet with a public IP it is the
baseline a reviewer will ask about first. `USER nobody`, a `HEALTHCHECK`, and a pinned
`requirements.txt` are perhaps ten lines across both files.

Resolved by `docs/superpowers/plans/2026-08-01-kp2-reproducible-builds.md` Task 3: `.dockerignore`
on both build contexts (console: 475.6kB → 93.7kB), `USER nobody` in both Dockerfiles (verified
against the actual demo flow -- clicking revoke through the console API and confirming the ACL
journal still writes to `/out` as `nobody`), and a `HEALTHCHECK` on each hitting its existing
health endpoint (`/v1/health`, `/api/health`; console uses `python -c` + `urllib`, no new
package). `scripts/console.sh up` now passes `--wait` so "up" means "serving"; the external
health checks in `seed.sh`/`verify.sh` stay as a deliberate backstop, not removed.

**🟡 D13 — `target:` should be extended before it is needed, not when.** `generate.py:947`
refuses anything but `docker-local`, which is correct and good. But every cloud-specific
difference identified above (profile default, bind policy, image pins, preflight) is a
*value*, not a code path — which means `target: do-droplet` can be a second column in a small
table rather than a fork in the generator. Deciding that now keeps the cloud work from
becoming a second implementation of the pack.

---

## What is notably good

- **`tests/test_tiers.py` is a model of its kind.** It tests a *claim in the documentation*,
  not a function, and its docstring records both the rejected design (calling `verify.sh
  --fast`, which self-recurses through pytest collection) and the manual confirmation that
  the `DOCKER_HOST` fake is equivalent to a truly stopped daemon. Reviews should be able to
  read a test and learn why it is shaped that way; this one delivers.
- **The lib split (C11) was done to the letter**, including moving the fingerprint check to
  the one script that has a reason to care. It would have been easy to split the file and
  leave the Docker call where it was.
- **`truth.py` resolves entrypoints from `topology.json` rather than trusting `2.6.yaml`'s
  static `entrypoint:` fields**, and says exactly why in the module docstring. The same trap
  is avoided the same way in `acceptance.sh`. That is a real architectural decision, held
  consistently in two places by two different languages.
- **`_mutate_acl`'s `prior_state` comment** is the best comment in the pack: it names the bug,
  the live confirmation, and what would go wrong if the invariant were relaxed.
- **`production-delta.md` retiring `federation.sh` and keeping the measurements that justified
  it.** Deleting the code and keeping the evidence is the right way round, and rare.
- **`gen-secrets.sh`'s alphabet reasoning** — choosing a deliberately narrower character set
  than the minimum safe one, rather than enumerating what happens to be safe today.

---

## Suggested order

1. **S9** — two lines (`sha256sum` with a `shasum` fallback). Still blocking the cloud work,
   three days on.
2. **S12 + S13** — NIN validation and a CSRF guard. Together perhaps 20 lines, and they are
   the two findings that would be embarrassing in a security review of a *government
   interoperability* pack.
3. **S15 + S16 + S17** — the three console correctness defects. All small, all in one file
   each, and S15/S16 undermine a guarantee the pack explicitly makes.
4. **C9** — unblocks T4 (CI) and standalone droplet checkout. Promote it above the cloud plan.
5. **T4** — a workflow running `verify.sh --fast` on push, once C9 allows it.
6. **C13** — pin `hurl`, pin the base images, add `requirements.txt`. Closes S7 and the
   largest remaining hole in the reproducibility claim.
7. **T5.1** — three unit tests for the mock registry's field filter. The pack's headline
   claim should not be provable only by a 15-minute live run.
8. **C14** — templates out of `main()`, one at a time, golden corpus as the guard. Then C1's
   real split.
9. **The cloud contract (D8–D13)** — with D9's sizing decision written first, since it
   determines everything else.
10. **S4 + S14 + C7** — the session/client consolidation. One change, three findings.
