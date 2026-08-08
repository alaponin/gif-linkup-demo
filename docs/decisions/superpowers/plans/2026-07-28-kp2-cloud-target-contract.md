# KP2 — Cloud Target Contract and Deployment Readiness

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. This plan implements findings **D2–D7**, **S7** and the remainder of **S8** from `docs/notes/reviews/2026-07-28-branch-review.md`. It **does not implement a DigitalOcean target** — that work is separate and should begin against the contract this plan writes.

**Goal:** Write down what a second deployment target must vary, and close the readiness gaps that belong to the pack rather than to the droplet. Today `generate.py` correctly refuses any `target` other than `docker-local`, so the seam exists — but nothing states what would have to change on the other side of it, which means the DigitalOcean work would start by rediscovering it.

**Architecture:** Mostly documentation, two small code changes (digest pinning, a Test CA guard), and two investigations whose answers are currently guesses. The output is `docs/deployment-targets.md`: the contract a `target: do-droplet` implementation is written against.

**Tech Stack:** Unchanged.

## Global Constraints

- **This plan changes no deployment behaviour on `docker-local`.** `scripts/acceptance.sh` must be green, unchanged, at the end of every task.
- **No speculative implementation.** Do not add a `do-droplet` branch, a Terraform directory, or configuration keys nothing reads. Write the contract; implement it separately.
- **An investigation may conclude "no".** Tasks 4 and 6 ask what X-Road and Docker actually do. Recording "we tried it and it does not work that way" is a successful outcome; asserting an untested mechanism is not.
- Commit after every task.

## Out of scope

The DigitalOcean implementation itself. S1/S2 (`2026-07-28-kp2-exposure-and-secrets.md`) — this plan assumes `network.bind` exists and builds on it. Multi-host topologies beyond stating that hostnames must become a target variable.

---

## Task 1: Write the target contract

**Files:** `docs/deployment-targets.md` (new), `deployment.yaml`, `README.md`

- [ ] **Step 1:** create `docs/deployment-targets.md` stating, for each dimension, what `docker-local` does today and what any other target must supply:

| Dimension | `docker-local` | What another target must decide |
| --- | --- | --- |
| **Hostnames** | Compose service names in `hurl/vars.env` (`cs`, `ss-pnia`) | Real DNS or addresses the moment components split across hosts. Upstream hit exactly this between 7.7.0 and `develop` — `docs/decisions/xroad-770-notes.md` §4 |
| **Bind address** | `network.bind: 127.0.0.1` | Which interface, and the `acknowledge_public_exposure` decision |
| **TLS verification** | `False` (Test CA) | `True` with a real chain — see console-hardening Task 4 |
| **Image provenance** | Tags, plus a digest-pinned testca | Digests for all three (Task 2) |
| **Certification authority** | `xrddev-testca` | An accredited CA; the Test CA is incompatible with a public target (Task 6) |
| **Secrets** | `.env` on disk, mode 600 | Where they come from and where they rest (Task 7) |
| **Persistence** | Named Docker volumes on one host | Backup, restore and recovery time (Task 4) |
| **Time** | The developer's laptop clock | NTP, mandatory (Task 5) |
| **Image acquisition** | Pull from Docker Hub / ghcr at deploy | Pre-pull, mirror, or accept the egress dependency (Task 8) |

- [ ] **Step 2:** state the profile guidance explicitly: **`profile: lite` is the cloud default.** `docs/production-delta.md` already measures ~2.1 GiB per Security Server; the conclusion is that full needs a 16 GB droplet and lite fits comfortably in 8 GB. Once the member-parameterisation plan's `hosted_on` work lands, a joining member costs no additional droplet.
- [ ] **Step 3:** add one line to `deployment.yaml` above `target:` pointing at the contract, so anyone changing that value finds the list.
- [ ] **Step 4:** link it from `README.md`. Commit.

## Task 2: Pin every image by digest (S7) — WITHDRAWN 2026-08-01

Superseded by `docs/superpowers/plans/2026-08-01-kp2-reproducible-builds.md` Task 2, which
owns this change (and pins `hurl` and `python:3.12-slim` besides, which this task did not
cover). Do not implement this task; see that plan instead.

**Files:** `deployment.yaml`, `docker-compose.yml`

`testca` is already digest-pinned — deliberately, and it is the pattern the other two should follow. `niis/xroad-security-server-sidecar:7.7.0` and `niis/xroad-central-server:noble-7.7.0` are mutable tags.

- [ ] **Step 1:** resolve the current digests from the images actually running: `docker image inspect --format '{{index .RepoDigests 0}}' <image>`. Take them from what has been tested, not from a fresh pull that might already differ.
- [ ] **Step 2:** add `cs_digest` and `ss_digest` alongside the existing `xroad.*` keys, in the same `tag@sha256:...` style as `testca_tag`, with a comment recording the date they were resolved and that the tags are kept for readability.
- [ ] **Step 3:** `teardown.sh --purge` → full redeploy → `scripts/acceptance.sh` green, proving the digests are the images the pack was verified against.
- [ ] **Step 4:** note in `docs/deployment-targets.md` that bumping X-Road means bumping three digests together, and that `docs/decisions/xroad-770-notes.md` §4 explains why scenarios and images move together. Commit.

## Task 3: Sizing conclusions where a deployer will find them

**Files:** `docs/deployment-targets.md`, `README.md`

- [ ] **Step 1:** `docs/production-delta.md` already holds the measurements (RAM per sidecar, cold-boot times, CPU contention). Do not restate them — cite them, and state the *conclusions* a deployer needs: droplet size per profile, expected first-deploy wall time, and what to do when the healthcheck budget is not enough (`hurl/compose.hurl.yml` already carries that reasoning at `retries: 120`).
- [ ] **Step 2:** commit.

## Task 4 (investigation): Backup, restore and recovery time

**Files:** `docs/deployment-targets.md`, `docs/decisions/xroad-770-notes.md`

Named Docker volumes on a droplet with no snapshot policy means a lost federation is a full redeploy plus re-registration. X-Road ships its own backup mechanisms for both server types; whether they are usable from these containers is untested.

- [ ] **Step 1:** find what the 7.7.0 admin API offers for backup and restore on both the Central Server and a Security Server, and whether the endpoints exist on these images.
- [ ] **Step 2:** try it: take a backup of `cs`, `teardown.sh --purge`, redeploy, restore, and see whether the federation comes back without re-registering members. Record exactly what happened.
- [ ] **Step 3:** whichever way it goes, document the recovery path and its measured cost. If restore works, that is the cloud backup story. If it does not, the story is "purge and redeploy, ~N minutes, members must re-register" — which is acceptable for a demonstration provided it is written down before someone needs it at a conference.
- [ ] **Step 4:** commit.

## Task 5: Require NTP

**Files:** `docs/deployment-targets.md`, `runbook.md`

X-Road signs and timestamps every message; a host with drifting time produces failures that present as certificate errors.

- [ ] **Step 1:** state the requirement in the contract, with the symptom, so the failure is diagnosable rather than mysterious.
- [ ] **Step 2:** add a one-line preflight to the runbook's prerequisites (`timedatectl status` on Linux, `sntp -sS` or System Settings on macOS) — a check, not an install script.
- [ ] **Step 3:** commit.

## Task 6: The Test CA cannot go to a public target (D6)

**Files:** `scripts/lib.sh`, `docs/deployment-targets.md`, `docs/production-delta.md`

`xrddev-testca` is a development image whose `/testca/sign` endpoint signs any CSR it is given. On a public interface that is a certificate factory for the federation's own trust anchor.

- [ ] **Step 1:** enforce it rather than document it: `lib.sh` refuses to bring the stack up when the `ca` service is in play **and** `network.acknowledge_public_exposure` is true. The message must say what the Test CA does, not just that it is disallowed.
- [ ] **Step 2:** confirm the refusal fires and that the loopback default is unaffected.
- [ ] **Step 3:** record in the contract that a non-loopback target requires replacing the CA, and that this is the one substitution that cannot be deferred to "later hardening".
- [ ] **Step 4:** add the row to `docs/production-delta.md`. Commit.

## Task 7: Where secrets rest (S8 remainder)

**Files:** `docs/deployment-targets.md`

`2026-07-28-kp2-exposure-and-secrets.md` Task 5 sets `hurl/vars.env` to mode 600 and audits for leakage, which covers the laptop case. What is left is the cloud decision.

- [ ] **Step 1:** state the options and their trade-offs in the contract: `.env` on the droplet's disk (simplest, survives reboot, readable by anyone with shell); generated into a tmpfs per deploy (no rest, but a reboot means regeneration and therefore a purge, since the token PIN cannot change under a live federation); or an external secret store (correct, and disproportionate for a demonstration).
- [ ] **Step 2:** make a recommendation and say why. Do not implement it — the DigitalOcean work chooses.
- [ ] **Step 3:** commit.

## Task 8: Image acquisition at deploy time (D7)

**Files:** `scripts/preload-images.sh` (new), `docs/deployment-targets.md`, `runbook.md`

The stack pulls from Docker Hub and ghcr during deployment. A firewalled droplet, a conference network or an air-gapped demo machine needs the images already present — and the pack already claims air-gap capability in the console plan's "no CDN fetch at runtime" constraint, which is only true of the *page*, not of the images.

- [ ] **Step 1:** `scripts/preload-images.sh` pulls every image the current `deployment.yaml` pins, by digest, and reports what it fetched — so a machine can be prepared while it still has network.
- [ ] **Step 2:** add `docker save`/`docker load` guidance for the genuinely offline case, with the caveat that the tarball is large.
- [ ] **Step 3:** note the egress dependency honestly in the contract and correct the air-gap claim wherever the pack overstates it.
- [ ] **Step 4:** commit.

## Task 9: Close out

- [ ] **Step 1:** `scripts/acceptance.sh` green on `docker-local`, both profiles.
- [ ] **Step 2:** mark D2–D7, S7 and S8 resolved in `docs/notes/reviews/2026-07-28-branch-review.md` with the date and, for the two investigations, what was actually found.
- [ ] **Step 3:** commit.

---

## Sequencing

Task 1 first — the rest are entries in the table it creates. Tasks 2, 5, 6 and 8 are small and independent. Task 4 is the long pole and the one most likely to change the contract's shape, so start the investigation early even though its write-up lands late. Nothing here blocks the console-hardening or simplification plans; all three can proceed in parallel.

The DigitalOcean work should not begin until Task 1 is committed, so that it has something to be written against rather than rediscovering each dimension as it hits it.
