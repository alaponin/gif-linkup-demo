# KP2 — Cloud Target Contract and Deployment Readiness

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Provenance.** This is a pruned rewrite of an earlier plan by the same name
(2026-07-28), kept only for the tasks that are still genuinely open. Its
Task 2 (digest-pin every image) is done — `deployment.yaml` already carries
`cs_digest`/`ss_digest` alongside `testca_tag`, and `docker-compose.yml`
prefers them over the mutable tags. Its Task 9 close-out review no longer
exists as a document to update. Everything below is what that plan never
finished.

**Goal:** write down what a second deployment target must vary, and close
the readiness gaps that belong to the pack rather than to the host. Today
`generate.py` correctly refuses any `target` other than `docker-local`, so
the seam exists — but nothing states what would have to change on the other
side of it. `PLAN.md` §9 still lists ITU cloud (Linkup) retargeting as
parked, blocked on environment specifics; this plan is what that retargeting
would be written against, not the retargeting itself.

**Architecture:** Mostly documentation, one small code change (a hard
refusal, not just a warning), and two investigations whose answers are
currently guesses. The output is `docs/deployment-targets.md`: the contract
a non-`docker-local` target implementation is written against.

## Global Constraints

- **This plan changes no deployment behaviour on `docker-local`.**
  `scripts/acceptance.sh` must be green, unchanged, at the end of every task.
- **No speculative implementation.** Do not add a second `target` branch, a
  Terraform directory, or configuration keys nothing reads. Write the
  contract; implement a real target separately.
- **An investigation may conclude "no".** Task 2 asks what X-Road and Docker
  actually do. Recording "tried it, does not work that way" is a successful
  outcome; asserting an untested mechanism is not.
- Commit after every task.

---

## Task 1: Write the target contract

**Files:** `docs/deployment-targets.md` (new), `deployment.yaml`, `README.md`

- [ ] **Step 1:** create `docs/deployment-targets.md` stating, for each
      dimension, what `docker-local` does today and what any other target
      must supply:

| Dimension | `docker-local` | What another target must decide |
| --- | --- | --- |
| **Hostnames** | Compose service names in `hurl/vars.env` (`cs`, `ss-pnia`) | Real DNS or addresses the moment components split across hosts — `docs/decisions/xroad-770-notes.md` §4 |
| **Bind address** | `network.bind: 127.0.0.1` | Which interface, and the `acknowledge_public_exposure` decision |
| **TLS verification** | `False` (Test CA) | `True` with a real chain |
| **Image provenance** | Digest-pinned (`cs_digest`/`ss_digest`/`testca_tag`) | Already covered — carry forward, nothing new to decide |
| **Certification authority** | `xrddev-testca` | An accredited CA; the Test CA is incompatible with a public target (Task 4) |
| **Secrets** | `.env` on disk, mode 600, `scripts/gen-secrets.sh` | Where they come from and where they rest on the target (Task 5) |
| **Persistence** | Named Docker volumes on one host | Backup, restore and recovery time (Task 2) |
| **Time** | The developer's laptop clock | NTP, mandatory (Task 3) |
| **Image acquisition** | Pull from Docker Hub / ghcr at deploy | Pre-pull, mirror, or accept the egress dependency (Task 6) |

- [ ] **Step 2:** cite `docs/production-delta.md`'s sizing measurements
      (RAM per sidecar, cold-boot times) rather than restating them, and
      state the *conclusions* a deployer needs: expected first-deploy wall
      time, and what to do when the healthcheck budget is not enough
      (`hurl/compose.hurl.yml`'s `retries: 120`).
- [ ] **Step 3:** add one line to `deployment.yaml` above `target:` pointing
      at the contract, so anyone changing that value finds the list.
- [ ] **Step 4:** link it from `README.md`. Commit.

## Task 2 (investigation): Backup, restore and recovery time

**Files:** `docs/deployment-targets.md`, `docs/decisions/xroad-770-notes.md`

Named Docker volumes on a host with no snapshot policy means a lost
federation is a full redeploy plus re-registration. X-Road ships its own
backup mechanisms for both server types; whether they are usable from these
containers is untested.

- [ ] **Step 1:** find what 7.7.0's admin API offers for backup and restore
      on both the Central Server and a Security Server, and whether the
      endpoints exist on these images.
- [ ] **Step 2:** try it: back up `cs`, `teardown.sh --purge`, redeploy,
      restore, and see whether the federation comes back without
      re-registering members. Record exactly what happened.
- [ ] **Step 3:** document the recovery path and its measured cost either
      way. If restore works, that is the story. If it does not, the story
      is "purge and redeploy, ~N minutes, members must re-register" — that
      is acceptable for a demonstration provided it is written down before
      someone needs it live.
- [ ] **Step 4:** commit.

## Task 3: Require NTP

**Files:** `docs/deployment-targets.md`, `runbook.md`

X-Road signs and timestamps every message; a host with drifting time
produces failures that present as certificate errors.

- [ ] **Step 1:** state the requirement in the contract, with the symptom,
      so the failure is diagnosable rather than mysterious.
- [ ] **Step 2:** add a one-line preflight to the runbook's prerequisites
      (`timedatectl status` on Linux, `sntp -sS` or System Settings on
      macOS) — a check, not an install script.
- [ ] **Step 3:** commit.

## Task 4: The Test CA cannot go to a public target

**Files:** `scripts/lib-stack.sh`, `docs/deployment-targets.md`,
`docs/production-delta.md`

`xrddev-testca` is a development image whose `/testca/sign` endpoint signs
any CSR it is given. `scripts/lib-stack.sh` already refuses a non-loopback
`network.bind` unless `network.acknowledge_public_exposure: true` is set —
but once acknowledged, it only warns; the Test CA keeps running. On a public
interface that is a certificate factory for the federation's own trust
anchor, and no acknowledgement should be able to buy that.

- [ ] **Step 1:** in `scripts/lib-stack.sh`'s bind check, refuse outright
      (not just warn) when the resolved bind is non-loopback **and** the
      `ca` service is part of the compose set — regardless of
      `acknowledge_public_exposure`. The message must say what the Test
      CA's `/testca/sign` endpoint does, not just that it is disallowed.
- [ ] **Step 2:** confirm the refusal fires and that the loopback default is
      unaffected.
- [ ] **Step 3:** record in the contract that a non-loopback target requires
      replacing the CA, and that this is the one substitution that cannot
      be deferred to "later hardening".
- [ ] **Step 4:** add the row to `docs/production-delta.md`. Commit.

## Task 5: Where secrets rest

**Files:** `docs/deployment-targets.md`

`scripts/gen-secrets.sh` covers the laptop case. What is left is the target
decision.

- [ ] **Step 1:** state the options and their trade-offs in the contract:
      `.env` on the target's disk (simplest, survives reboot, readable by
      anyone with shell); generated into a tmpfs per deploy (no rest, but a
      reboot means regeneration and therefore a purge, since the token PIN
      cannot change under a live federation); or an external secret store
      (correct, and disproportionate for a demonstration).
- [ ] **Step 2:** make a recommendation and say why. Do not implement it —
      a real target's implementation chooses.
- [ ] **Step 3:** commit.

## Task 6: Image acquisition at deploy time

**Files:** `scripts/preload-images.sh` (new), `docs/deployment-targets.md`,
`runbook.md`

The stack pulls from Docker Hub and ghcr during deployment. A firewalled
host, a conference network or an air-gapped demo machine needs the images
already present.

- [ ] **Step 1:** `scripts/preload-images.sh` pulls every image
      `deployment.yaml` pins, by digest, and reports what it fetched — so a
      machine can be prepared while it still has network.
- [ ] **Step 2:** add `docker save`/`docker load` guidance for the genuinely
      offline case, with the caveat that the tarball is large.
- [ ] **Step 3:** note the egress dependency honestly in the contract, and
      correct any place the pack overstates air-gap capability of the
      images themselves (as opposed to the console page, which genuinely
      makes no CDN fetch at runtime).
- [ ] **Step 4:** commit.

## Task 7: Close out

- [ ] **Step 1:** `scripts/acceptance.sh` green on `docker-local`.
- [ ] **Step 2:** commit.

---

## Sequencing

Task 1 first — the rest are entries in the table it creates. Tasks 3, 4 and
6 are small and independent. Task 2 is the long pole and the one most likely
to change the contract's shape, so start that investigation early even
though its write-up lands late.

A real second target (e.g. a cloud droplet) should not begin until Task 1 is
committed, so that it has something to be written against rather than
rediscovering each dimension as it hits it.

## Exit

- `docs/deployment-targets.md` exists and covers all nine dimensions in
  Task 1's table.
- `scripts/lib-stack.sh` refuses a public bind with the Test CA in play,
  unconditionally.
- Backup/restore and NTP are each either a documented working path or a
  documented, measured limitation — not silence.
- `scripts/preload-images.sh` exists and pulls every pinned image by digest.
- `scripts/acceptance.sh` is green, unchanged, on `docker-local`.
