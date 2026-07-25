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
| `20`–`23` | PNIA, PLR, MoEYS, PNEA: anchor, init, AUTH + SIGN keys, Test CA signing, certificate import, registration, approval, subsystem client |
| `30`–`32` | OpenAPI 3 service descriptions, enable, access rights |

Which module each scenario realises is recorded in `manifest.yaml` (`scenarios:`
per module), so the chain runs config → prompt → scenario → acceptance.
`check_scenarios.py` fails if a scenario is unclaimed or a claim does not resolve.

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
- **`LITE=1` is not supported.** The scenarios initialise `ss-pnia` and
  `ss-moeys` as servers in their own right, and the lite profile does not start
  them; `run-linkup.sh` refuses to run rather than failing twenty minutes in.
  Supporting lite means teaching `generate.py` to host those two subsystems as
  extra clients of `ss-plr` — the topology table in `scripts/lib.sh` (`HOST_SS`)
  is the source of truth for that mapping.
- The runner's `depends_on` waits on `cs`, `ca`, `ss-pdga`, `ss-pnea` and
  `ss-plr` only; `ss-pnia` and `ss-moeys` belong to the `full` compose profile,
  which a non-profiled dependency cannot reference, so they are covered by the
  retries instead.
- The scenarios have not yet been parsed by `hurl` or `hurlfmt` — the sandbox
  this pack was authored in has no network access to the Hurl release. Run
  `hurlfmt --check hurl/.build/setup.hurl` at P0 before trusting the syntax.
- Everything here is demo-grade: Test CA as trust anchor, fixed credentials,
  plain HTTP between the Security Servers and the mock providers, one host. See
  `docs/production-delta.md` and `docs/xroad-770-notes.md`.
