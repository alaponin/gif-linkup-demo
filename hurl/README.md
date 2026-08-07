# hurl/ — standing Linkup up as configuration

This directory is the Linkup federation expressed as **config-as-code**: a set of
Hurl scenarios that drive the Central Server and Security Server admin REST APIs
on port 4000 until Progressa's education-sector bus exists.

It is a Progressa retargeting of `development/hurl/scenarios/setup.hurl` at
X-Road **tag 7.7.0** — the same call sequence NIIS itself uses to bring up
`Docker/xrd-dev-stack`, with `DEV:COM:1234` replaced by the identifiers frozen in
`manifest.yaml`. Nothing here is invented: every endpoint, payload shape and
expected status code is taken from the upstream scenario at that tag.

```
hurl/
  generate.py          scenarios and vars.env are GENERATED from configs/
  templates/*.hurl.tmpl source templates generate.py renders (see below)
  check_scenarios.py   static check — undefined variables, ordering, manifest drift
  run-linkup.sh        concatenate, bring the stack up, run
  compose.hurl.yml     overlay: the runner, the CA cert volume, healthchecks
  vars.env             Hurl --variables-file (generated)
  scenarios/*.hurl     the federation (generated)
  .build/              concatenated run files (transient)
```

## Run it

```bash
hurl/run-linkup.sh --dry-run   # build the concatenated file, run nothing
hurl/run-linkup.sh             # stand the federation up
scripts/seed.sh                # then load the demonstration data
scripts/acceptance.sh          # then prove it
```

Proving the pack is `acceptance.sh`'s job, not this directory's. It owns module
2.6's four assertions, two of which — exact-set equality of the assembled
credential application, and the seeded-record comparison in `assert_record.py` —
are beyond what a Hurl scenario can express. There is deliberately no second,
weaker copy of the headline check here.

Expect a stretch of HTTP errors and retries partway through. Global configuration
generation and distribution is asynchronous and takes minutes; upstream's own
runner sets `--retry 12 --retry-interval 10000` for exactly this reason, and so
does ours. Retries are the design, not a symptom.

## Why the files are concatenated

Hurl captures do not cross file boundaries. The global configuration anchor
(`gconf_anchor`), the Test CA's name (`ca_name`), the timestamping service
(`tsa_name`, `tsa_url`) and every session's XSRF token are captured once and
reused by later requests, so the scenarios are `cat`-ed together in lexical order
into `.build/setup.hurl` and handed to a single `hurl` invocation — the same
thing NIIS's `run-hurl.sh` does. The numbering is the execution order:

| Files | What happens |
| --- | --- |
| `00`–`03` | Central Server: initialise `PROGRESSA`, member class `GOV`, signing keys, Test CA + OCSP + TSA, members and subsystems, management service provider, download the anchor |
| `10` | management Security Server at PDGA: certificates, registration, the management WSDL and the owners-group ACL. Captures `ca_name`, `tsa_name`, `tsa_url` for everything after it |
| `20`–`23` | PNIA, PLR, PNEA: anchor, init, AUTH + SIGN keys, Test CA signing, certificate import, registration, approval, subsystem client (`22` was MoEYS's; retired, reserved, not reused -- see `generate.py`'s `PINNED_SCENARIO_NO`) |
| `30`–`32` | OpenAPI 3 service descriptions, enable, access rights |

Which module each scenario realises is recorded in `manifest.yaml` (`scenarios:`
per module), so the chain runs config → prompt → scenario → acceptance.
`check_scenarios.py` fails if a scenario is unclaimed or a claim does not resolve.

## Templates (`hurl/templates/`)

`generate.py`'s emitted scenarios are rendered from `.hurl.tmpl` files here,
not built as Python f-strings. Hurl's own variable syntax is `{{var}}`;
Python f-strings also use `{}`, so an f-string emitting Hurl needs every
literal brace doubled and every Hurl variable quadrupled — a standing tax on
editing the pack's most important generated artefact, and a class of bug (a
miscounted brace produces either a Python error or valid-but-wrong Hurl).
`.hurl.tmpl` files are plain text: Hurl's `{{var}}` passes through untouched,
and `generate.py`'s own substitutions use the `@name@` convention (`sub()` /
`render()`) instead, which cannot collide with it. `.tmpl` — not bare `.hurl`
— marks them as source belonging in git, distinct from the generated,
gitignored `hurl/scenarios/`.

Templates are always read from the real pack checkout, never from `--out`'s
redirected output directory — `generate.py`'s `TEMPLATES` constant makes the
same PACK-not-HURL_DIR distinction already documented at line 32 for
`manifest.yaml` and `configs/`.

The golden corpus (below) is what makes editing a template safe: every
change is checked against a byte-exact baseline before it can be trusted.

**Measured result** (docs/reviews/2026-08-01-branch-review.md finding C14):
`generate.py` went from 1639 to 1085 lines (-34%); `main()` from 706 to 521
lines (-26%). `grep -n '{{{{' hurl/generate.py` returns nothing — no
quadrupled-brace Hurl block survives. 16 `.hurl.tmpl` files exist: 5 scenario
templates plus 11 single-purpose fragments under `hurl/templates/fragments/`.
`main()`'s reduction is smaller than the finding's "~700 lines" framing might
suggest, because most of what remains in `main()` after this plan is not
Hurl emission at all — it's the member/service loops, and the
`topology.json`/`topology.sh`/`compose.members.yml` generation, none of
which this plan's scope (moving f-string Hurl blocks) touches.

## Step registry (`hurl/steps.py`)

The admin-API sequence the templates above encode is also expressed as an
ordered registry of named `Step`s — `hurl/steps.py`, added by
`docs/superpowers/plans/2026-08-01-kp2-join-a-step-registry.md` ("Plan A").
`generate.py` renders the registry in order to build `hurl/scenarios/`; the
bytes are identical to what a direct `render()` call would have produced —
nothing about cold deploy changed. What the registry adds is that the
sequence now has *names* a later caller can address one at a time: the
future join API (Plan B) needs to run step 14 of ~24 without re-running
steps 1-13, which the concatenated Hurl files alone cannot support (Hurl
captures do not cross file boundaries).

- **Step ids are stable identifiers other things persist.** `id` (e.g.
  `"ss.auth_key_csr"`) is dotted, never renumbered, and never reused for a
  different step — Plan B persists it in a job's context to know what to
  resume from, and Plan C (DELETE) will attach a reversal to it. Renaming one
  is a migration, not a refactor.
- **`requires`/`provides` are Hurl *runtime* identifiers — `{{var}}` names
  and `[Captures]` names — never `generate.py`'s `sub()` `@name@` tokens**,
  which are substituted in Python before Hurl ever sees the file. A
  per-member step (anything under `ss.*`/`service.*`) still carries
  `@HOSTVAR@`/`@P@`/`@SESS_P@`/`@CAP_P@`/`@SPECVAR@` placeholders in its
  declared `requires`/`provides` at this stage, because the concrete Hurl
  identifier (`"pdga_xsrf_token"` vs `"pnia_xsrf_token"`) only exists after
  `generate.py` substitutes a specific member in — the placeholder form is
  what's structurally true across every member the step is ever rendered
  for. See `hurl/steps.py`'s module docstring for the full reasoning.
- **`tests/test_steps.py` is what keeps the declarations honest.** It parses
  each step's raw `.tmpl` source (never the rendered `hurl/scenarios/`
  output, which reflects only one already-chosen member set) and
  asserts: `provides` equals the template's exact capture set; every
  `{{name}}` reference is accounted for by `requires`, `provides`, or a known
  `vars.env` global; and, walking the registry in order, every `requires` is
  satisfied by an earlier step's `provides` or a global. A declared contract
  nobody checks is documentation that rots — this is what makes it not.
- **`actor`** (`"operator"` | `"member"`) records who performs a step in the
  join lifecycle a joining member with its own Security Server goes through.
  Two call sites are documented exceptions to a step's declared default
  rather than a second field (`hurl/steps.py`'s comment above
  `ss.bringup_init` has the details): `main()`'s bring-up of the *operator's*
  own management Security Server, and `build_hosted_client()`'s steps, which
  "under `hosted_on`, every step is `operator`" regardless of the default.
- **`probe`** (a template path, or `None`) is set only where a step's 409
  behaviour on repeat is ambiguous — most of the registry is not: a repeat
  either conflicts cleanly (`409`, this pack's proven default — `PLAN.md`
  Section 11) or is naturally idempotent. Audited counts (Task 5 of the
  join-a plan, 2026-08-01): 3 read-only, 10 `409`-safe, 8 ambiguous (probed),
  0 unsafe to repeat. The 8 probes were written, then run live against a real
  deployed federation (`docker run` the pinned `hurl` image directly against
  a real Central Server and Security Server, not just re-derived from the
  OpenAPI spec) — one endpoint guess turned out wrong
  (`PROBE_CS_SIGNING_KEYS.hurl.tmpl` first assumed a
  `GET /configuration-sources` list that does not exist; the Central
  Server's own signing keys are on its token, `GET /tokens`) and was fixed
  from the live response, not just the spec. See each `PROBE_*.hurl.tmpl`'s
  own comment for what it confirmed and any correlation caveats found live
  (e.g. `PROBE_SS_SIGN_KEY.hurl.tmpl`: a shared host's token under
  `hosted_on` carries one same-labelled key per hosted member, so a probe
  reading the label alone is not enough).
- **`unsafe_to_repeat`** defaults `False`; `tests/test_steps.py` asserts the
  registry has none today. If a future step ever needs it, Plan B's runner
  must refuse to resume across it rather than trust `409`-as-success blindly.

Nothing in this pack runs the registry per-step yet — that is Plan B's job.
Plan A's value is that the sequence now has a name and a checked contract
independent of any executor.

## Do not hand-edit

`scenarios/` and `vars.env` are artefacts. The identifiers come from
`configs/*.yaml` and `manifest.yaml`; the admin credentials and the software-token
PIN come from `.env`, the same file Compose injects into the containers — never
from a constant in `generate.py`, or the scenarios would authenticate with a value
the containers do not have. Change the source and re-run:

```bash
python3 hurl/generate.py && python3 hurl/check_scenarios.py
```

`run-linkup.sh` regenerates before every run, so a stale scenario cannot be
deployed by accident.

`vars.env` carries no comments on purpose — Hurl's `--variables-file` is a plain
`name=value` list and a trailing `#` ends up inside the value. Commentary lives
here instead.

`check_scenarios.py` runs as part of the ship gate: `kp-solution-verify`'s
`check_pack.py` executes any `<pack>/<tool>/check_*.py` it finds, so a scenario set
with an undefined variable or a drifted credential cannot pass `--ready`.

## Host Python runtime (two-decisions plan Task 3/C10)

Host-run scripts (`hurl/generate.py`, `hurl/check_scenarios.py`,
`scripts/gen_seed_data.py`, `scripts/assert_record.py`,
`scripts/mkfixture.py`, and `scripts/lib.sh`'s inline `yq_get`) run under
whatever `python3` resolves to on the operator's machine — not a container,
which is why they used to avoid 3.9+ idioms (`str.removeprefix`, etc.):
`generate.py` had a comment claiming "host runs system python3.7.9".

**That comment was wrong about *why*.** Investigated live (2026-08-01): the
actual Apple-shipped `python3` (`/usr/bin/python3`, the Xcode Command Line
Tools stub) is **3.9.6**, not 3.7. The 3.7.9 came from a stray Homebrew
install at `/usr/local/bin/python3` shadowing it earlier in `PATH` — an
artefact of one developer's machine, not a constraint macOS itself imposes.
All six host-run entry points' only hard dependency beyond the standard
library is PyYAML (`generate.py`'s `import yaml`) — already a required
install step regardless of interpreter version, so the floor was never
"whatever ships with macOS", it was "whatever the operator happens to have
installed".

**Decision: raise the floor to 3.9+.** This deletes the invisible
host-vs-container idiom rule (C8 in the simplification plan) — both sides
now support `removeprefix`/`removesuffix` — and CI's `python-version` moves
off an EOL 3.7 pin (`.github/workflows/kp2-fast.yml`) that would eventually
stop being satisfiable on hosted runner images at all. The
`scripts/check-python-floor.sh` lint queued in the simplification plan's
Task 5 is withdrawn: there is no longer a restriction for it to enforce.
Cost, paid honestly: an operator on a stock Mac whose `python3` still
resolves to something older than 3.9 needs to fix that — but they need a
non-ancient interpreter to install PyYAML cleanly anyway, so this is not a
new burden, it is naming one that already existed.

## Golden corpus

`tests/golden/{full,lite}/` is a committed fixture of exactly what
`generate.py` emits today — `scenarios/`, `vars.env`, `topology.json`,
`topology.sh`, `compose.members.yml` — generated from a fixed, fake
`tests/golden/env.fixture` rather than a real `.env`. `tests/test_golden.py`
regenerates (via `generate.py --out DIR --env FILE`, flags that exist only
for this test) and diffs against it, in under two seconds, no Docker, no
network. `generate.py` lost the profile concept in Wave 3 Task 4 (one
topology, D5); `{full,lite}/` collapsing into a single directory is Wave 3
Task 5's job, not done here:

```bash
.venv/bin/python3 -m pytest tests/test_golden.py -v
```

This is the byte-identical proof past plans pasted a `cp -r /tmp/base-*`
ritual for, made permanent and cheap instead of manual and skippable.

**When a change to `generate.py` should alter the output**, regenerate the
corpus **in the same commit** as the change, so the diff is reviewable
alongside the code that caused it:

```bash
python3 hurl/generate.py --out /tmp/golden-full --env tests/golden/env.fixture
rm -rf tests/golden/full
cp -r /tmp/golden-full tests/golden/full
```

(`tests/golden/lite/` is untouched by this command on purpose — regenerating
it from the same profile-less `generate.py` would just be a second copy of
`full/`, which is exactly the collapse Wave 3 Task 5 does deliberately, not
as a side effect of an unrelated template change.)

A golden test whose corpus gets updated blindly — without looking at what
changed and why — is theatre, not a test. Read the diff before committing it.

## Retargeting

`vars.env` is the only place hostnames appear. It currently holds Docker Compose
service names on the `linkup` network. Pointing the same scenarios at the ITU
cloud, an LXD stack or a set of real VMs is an edit to `generate.py`'s host
values and nothing else — which is the whole argument for driving the admin APIs
rather than clicking the UIs.

## Known limits

- **Not idempotent against persisted state.** The scenario set always runs the
  full stand-up sequence; if the Central Server volume already has a
  `PROGRESSA` instance (a plain `teardown.sh` without `--purge`, then a rerun),
  `POST /initialization` returns `409 init_already_initialized` and the run
  fails immediately (confirmed at P0, 2026-07-25). Resuming a stopped-but-not-
  purged federation is `docker compose ... up -d` directly — see runbook.md
  "Teardown" — not a rerun of `run-linkup.sh`, which is the from-zero path only.
- The scenarios have not yet been parsed by `hurl` or `hurlfmt` — the sandbox
  this pack was authored in has no network access to the Hurl release. Run
  `hurlfmt --check hurl/.build/setup.hurl` at P0 before trusting the syntax.
- Everything here is demo-grade: Test CA as trust anchor, fixed credentials,
  plain HTTP between the Security Servers and the mock providers, one host. See
  `docs/production-delta.md` and `docs/xroad-770-notes.md`.
