# The build pack — Building a Government Interoperability Framework (GIF)

> **On the Progressa Learner Registry (PLR).** The PNEA ← PNIA + PLR exchange this pack proves is
> the **target-state** slice that the National Learner Registry programme delivers. In the Progressa
> baseline the PLR is planned, not started — its absence is the sector problem. The pack builds the
> exchange the country is working towards, not one it already has.

The runnable companion to the *Government Interoperability Framework (GIF)* course videos. The videos teach the build; this
pack **is** the ready solution — the configuration the modules generate, the prompts
that generate it, the scripts that deploy it, and the acceptance checks that prove it.

- **Track:** interoperability
- **Depends on:** none (foundation)

## Requirements

- **Docker ≥ 24 with Compose v2 ≥ 2.24**, plus `git`, `curl`, `jq`, `python3`
  3.9+ with PyYAML, a SHA-256 tool and bash 4+. `scripts/preflight.sh` checks
  every one of them at once and installs nothing.
- **~11 GiB RAM** in steady state, measured live (`docker stats --no-stream`:
  four Security Servers ~2.2–2.3 GiB each, Central Server ~1.8 GiB, Test CA
  ~88 MiB, mock providers ~65 MiB each, up from ~32 MiB before each grew a
  second, TLS listener). Fits a 16 GB host.
- **~15 GB free disk:** ~4.6 GB of pinned images (Security Server Sidecar
  2.31 GB, pulled once and shared by all four servers; Central Server 1.78 GB;
  Test CA 542 MB), ~1 GB of locally built mock / console / join-api images,
  ~1.4 GB of named volumes once deployed, and Docker build cache above that.
- **4 CPU cores or more.** Unmeasured, unlike the figures above: five JVMs run
  concurrently, and a narrower host stretches the ~13-minute cold `--full`
  rather than breaking it.
- **A git clone of this repository**
  (`git clone https://github.com/alaponin/gif-linkup-demo`). `join-api`
  bind-mounts the checkout and its `.git`, and `scripts/package.sh` builds from `git
  archive` — an unzipped copy stands the federation up fine, but not the join
  demo.

## Quickstart

```
scripts/demo.sh            # preflight, .env, deploy, seed, acceptance, console -- ~10 min from zero
scripts/verify.sh --live   # re-prove it after a change
scripts/teardown.sh        # stop; volumes survive (--purge only for a from-zero rebuild)
```

`demo.sh` names each step as it runs it and refuses if a federation is already
deployed; step 1 is `scripts/gen-secrets.sh`, which writes the real `.env`
(`.env.example` is a placeholder template and cannot work by itself). Then
`exercises.md` — five exercises over the operations `runbook.md` documents,
each with the observations to expect: break and restore the once-only proof,
join a member, catch a published contract drifting, un-join, and watch the
reproducibility proof run.

- **Stand it up (the long way):** `runbook.md`
- **Index:** `manifest.yaml` (module → BB → config → prompt → acceptance, with
  `video_ref` to the Topic 5 subtopic each module realises, and the frozen
  Progressa identifiers that are the join keys the DPI Roadmap and Building Block Approach courses reuse)
- **Status against the onboarding path:** `docs/path-conformance.md` — the
  only place the pack states what it does and does not implement of the
  member onboarding path it teaches.
  Generated from `docs/path-conformance.yaml`; every cited evidence path is
  existence-checked by `tests/test_path_conformance.py`, so a status claim
  cannot outlive the file it cites. Four statuses and no tick mark. **Where a
  narrative document disagrees with it, it wins** — that divergence is what
  once let findings be recorded as closed by files that never existed.
- **Design records:** `docs/decisions/` — reasoning, never status
- **Hand it to someone:** `scripts/package.sh` — a zip (or `.tar.gz`) built with
  `git archive`, so it holds what a fresh clone would. Never zip the working
  directory: it carries the real `.env`, `out/`, the `.venv` and ~25 MB of
  darwin-only Terraform provider binary, all gitignored and all of which a
  Finder zip copies anyway. Give a clone rather than an archive when the
  session includes the join demo — `join-api` needs the checkout's `.git`
  (`scripts/preflight.sh` refuses any other layout; `runbook.md`
  Prerequisites says what breaks and why).
- **Verify a change:** `scripts/verify.sh --fast|--live|--full` — three tiers,
  chosen by the tool, not by whoever is typing. `--fast` (static checks, the
  ship gate, exposure, the test suite — no running containers, no network)
  **~50s**; `--live` (`--fast`, then `acceptance.sh` against a running stack)
  **~80s**; `--full` (purge, deploy, seed, acceptance, console smoke — the
  reproducibility proof) **~13 min** cold against the standard topology. Which
  tier to run when, what each does and does not prove, and why `--live` never
  performs a real member join: `runbook.md`, "Verifying a change".

## What's in the pack

| Folder / file | What it holds |
|---|---|
| `deployment.yaml` | The analyst-facing deployment spec: X-Road version pins, network bind, the proxy authorization-cache period every Security Server boots with (`server_conf_cache_period`, rendered into `hurl/local.ini`), and the digest pins that back them (`cs_digest`, `ss_digest`, `testca_tag`). `.env` carries only secrets. |
| `docker-compose.yml` | X-Road 7.7.0: Central Server, Test CA, and four Security Servers: PDGA plus PNEA, PLR and PNIA each on their own. MoEYS is retired. |
| `configs/` | Declarative YAML per module. |
| `prompts/` | The prompts that generate the configs. |
| `hurl/` | The federation as config-as-code: Hurl scenarios driving the admin REST APIs, generated from `configs/`, retargeted from X-Road 7.7.0's own `setup.hurl`. |
| `acceptance/` | Given/when/then per module. `once-only-exchange.md` is the framework's acceptance; `member.md` is the generic per-member check every joined member gets automatically; `join-member.md` is the join API's own transition and reachability check. |
| `scripts/` | Deploy, seed, acceptance and teardown; `member.sh list\|remove\|drift` (reports on, retires and checks drift for joined members); `join.sh up\|down\|status` (the join API's service lifecycle); and `verify.sh`, the tiered entry point above. |
| `tests/` | The golden corpus for `hurl/generate.py` (`test_golden.py`, no Docker). |
| `apps/` | Mock REST registries behind the Security Servers, their OpenAPI contracts, and invented Progressa seed data. See below for `apps/console/` and `apps/join-api/`. |
| `docs/` | The production delta (Module 5.7), the X-Road 8 note, what reading the 7.7.0 reference corrected, and `deployment-targets.md`, the contract a `target:` other than `docker-local` would be written against. |

Two of the `apps/` are worth naming:

- **`apps/console/`** is the optional one-page demonstration UI (`scripts/console.sh up`). It is a demo asset, not a module, and never in the acceptance path. `scripts/demo-capture.sh` films its beats for the Module 5 videos (`apps/console/capture/`). Its **4 · Join a member** tab is a thin proxy, holding the token server-side, onto `apps/join-api/`.
- **`apps/join-api/`** is the `join-member` module's own service. It validates and drives a real member join, from a submitted payload to `ACTIVE`, over the live X-Road admin API.

## What is published on this bus

This is answered by artefacts rather than by asking someone:

- `onboarding/<key>/04-catalogue/<code>.md`, one per published service. It gives the X-Road service id, the contract, the semantic entity and its tier-1 exchange pattern, the lawful basis, the ACL subjects, and a link to the signed SLA. The SLA is reachable *from the service*, not only from the member.
- `onboarding/catalogue.yaml` for the instance as a whole.
- `GET /catalogue` on the join API, which serves the same derived data as JSON under the **applicant** token rather than the operator one. A catalogue gated behind the credential of the people who already know what is published answers nothing.

Three properties to keep in mind:

- **It is `listMethods`, not `allowedMethods`.** It says what was registered here, never what the bus will let you call, and it says that on the response rather than only on this page.
- **Appearing in it grants nothing,** and both files say so on their face. They ask nothing new of a joining member either: every field is something the registration already collected.
- **It is the register's own output, not a collector.** It is complete for members this register admitted and blind to anything else on the bus. `docs/production-delta.md` names what a production ecosystem still needs beside it.

The aggregate is derived wholesale from `manifest.yaml` + `configs/member-*/`. It is regenerated by `scripts/render-onboarding.sh` and by the join API at both ends of a member's life. `scripts/member.sh remove` alone does **not** regenerate it (`runbook.md`, "The service catalogue").

## Members and joining

The number and identity of members is a property of `configs/member-*/` plus `manifest.yaml`'s `identity.members`, not of this pack's source code.

There is still no `scripts/member.sh add`, and that is on purpose: writing member config by hand is exactly what this pack demonstrates you don't need to do. There are two ways in instead:

- **The join API** (`scripts/join.sh up`, or the console's **4 · Join a member** tab) drives a real, hosted member from a submitted payload through validation, operator approval, config generation and the live X-Road admin-API sequence to `ACTIVE, verified: true`. It is live-verified end to end: submit → approve → `ACTIVE` → `acceptance.sh` green → `member.sh list` → `member.sh remove` → regenerate → `acceptance.sh` green again, with the join step itself well under two minutes.
- **The manual flow** in `prompts/member.md`: run the prompt against an agency brief and commit what it produces. It is still there for anyone without a running stack to submit against.

### Hosted or own Security Server

`apps/join-api` covers both shapes of join: a hosted member (the default) and one that brings up its own Security Server (`security_server.own_server: true`; `runbook.md`, "A join with the member's OWN Security Server").

On a single-host demo deployment, default a joining member to `hosted_on` an existing Security Server. The join API does this by default (`configs/x-road-bus/join-policy.yaml`'s `default_hosting: hosted_on`). It costs zero extra containers and RAM, and it sidesteps every own-server finding in `docs/production-delta.md`: a real port-allocation bug, two real Compose gaps, and host-CPU-contention risk under several concurrent JVMs. Reserve a joined member's own server for when the demonstration specifically needs one.

### Identifier conventions

A submitted payload's `code` and `subsystem` must satisfy the identifier and member-code conventions that `docs/conventions.md` publishes. This is the onboarding path's §0.5/§1a prerequisite, which the pack now states rather than leaving implicit in `validate.py`. `security_server.dns_name` follows the same doc's `ss-<key>` host-naming convention, which the pack applies consistently but does not check at request time.

## What the pack is an instance of

- **GovStack's Information Mediation building block (subtopic 4.7).** X-Road as the message bus, join-api as the onboarding gate, and `configs/semantic/semantic-map.yaml` (Module 4, checked by `apps/join-api/validate.py` check 8, not merely published) as the shared field dictionary together realise it. A member joins the mediator once and reaches every other member's declared exchanges through it, rather than negotiating a bilateral integration per pair.
- **GovStack's Registration building block.** The join API is an instance of it: the payload is the eForm, `validate.py`'s checks are the eligibility determinants, operator approval is the registrar role, and the membership record is the issued credential. That is why joining an agency and a learner applying for a certificate run the same shape at two scales.

What each tier-1 pattern label means, and the BB specification it anchors on, is in `docs/pattern-register.md`.

## Joget-free by design

This course's slice is **Joget-free**: the member systems are mocks behind stable OpenAPI contracts. That is the seam where the Joget DX apps of the Building Block Approach to Digital Services course plug in later without touching the X-Road configuration. `docs/kp4-seam.md` states the seam as a contract: what is frozen, the two shapes a Joget app can take, the data fixture it must serve, and the host it has to fit on.

## Status

Built and proven with the course's production kit: its configuration generator fills the configs, and its acceptance gate proves the pack runs.

**Status: VERIFIED.** `check_pack.py --ready` passes and the live acceptance suite is green. That includes the reproducibility proof (`teardown.sh --purge` → cold redeploy → reseed → acceptance, unattended) and a full console up/exercise/reset pass.

Scope: Education only, public anchors only. Demo only, never production (`docs/production-delta.md`).
