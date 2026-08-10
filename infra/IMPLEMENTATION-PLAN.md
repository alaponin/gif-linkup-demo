# KP2 on DigitalOcean — implementation plan

Companion to `DO-DEPLOYMENT.md` (the analysis) and the scaffold in
`infra/terraform/`, `infra/ci/remote-deploy.sh` and
`.github/workflows/kp2-federation.yml`. That trio is the *what*; this document
is the *how and in what order*, with a review of the scaffold folded in.
Target shape, unchanged: one 16 GB Droplet in a dedicated DO project, the pack
running as plain `docker-local` behind an SSH tunnel, Terraform state in
Spaces, and a manually-triggered GitHub Actions workflow with three actions
(`up` / `deploy` / `destroy`).

Total estimated effort: **about half a working day**, of which roughly an hour
is waiting on the first cold deploy and its verification runs.

## Review of the scaffold (what changed as a result)

A second pass over the generated files against the pack's own contracts found
two defects and a set of confirmations. Both defects are now fixed in the
committed files.

**Fixed 1 — redeploy over a live federation would have failed.** `deploy.sh`
delegates to `hurl/run-linkup.sh --setup`, which replays the full admin-API
init sequence and is not idempotent over an already-initialised federation.
The original `remote-deploy.sh` would have replayed it on every run.
It now detects a running `cs` container and defaults to proving the live stack
(`acceptance.sh` only); a destructive purge-and-redeploy is an explicit choice
— the workflow's new `redeploy: true` input, which follows the pack's own
reproducibility path (`teardown.sh --purge` → cold deploy).

**Fixed 2 — `ssh-keyscan` false success.** Some versions exit 0 with no keys
scanned; the wait loop now checks that `known_hosts` is actually non-empty
before proceeding.

**Confirmed against the pack:** the full-monorepo rsync (with `.git`)
satisfies join-api's bind-mount of `../../..` and its `git status` clean-check;
a shallow CI checkout is fine for that check; rsync's protect filters keep the
droplet-local `.env` and `out/` safe across redeploys; cloud-init installs
exactly the `preflight.sh` checklist (compose v2 as a plugin, PyYAML, jq, curl,
bash 4 and sha256sum already in Ubuntu base); NTP is systemd-timesyncd plus an
explicit `timedatectl set-ntp true`; loopback bind plus SSH-only firewall is
the posture `lib-stack.sh` enforces anyway (it refuses non-loopback while the
Test CA exists, and no acknowledgement flag overrides that).

**Fixed 3 — a `deploy` onto a live federation resets the join state.** The
rsync protects `.env` and `out/` but not `configs/member-<key>/` or
`manifest.yaml`, which `apps/join-api/app.py` writes and commits droplet-side.
Acceptance stays green (module 2.7's rows come from `hurl/topology.json`, which
regenerates from the same reset `configs/`, so a wiped member is skipped rather
than failed) — what is lost is demo state, not correctness. Left as-is with the
ceiling and its upgrade path recorded on the rsync step: re-run the join after
a `deploy`, or condition the excludes on the same live-stack test
`remote-deploy.sh` already does.

**Known limitations, accepted for now** (revisited in Phase 6): CI SSHes as
root with the firewall's SSH rule open to the world (key-only auth; GitHub
runners have no fixed IPs); `cloud-init status --wait` fails the job on a
degraded boot, which is loud by design; 16 GB leaves no room for a demo that
joins a member with its *own* Security Server.

## Phase 0 — Prerequisites (~30 min, one-time)

You need a DigitalOcean account with billing enabled and admin access to the
GitHub repository hosting the `geatdm-framework` monorepo. In the DO control
panel create two credentials: a personal access token with write scope
(API → Tokens), and a Spaces access key pair (API → Spaces Keys). Locally,
generate the deploy keypair the CI will use to reach the droplet:
`ssh-keygen -t ed25519 -f kp2-deploy -N ""`. Nothing else is needed on your
machine unless you want the optional local shakeout in Phase 2, which needs
Terraform ≥ 1.6.

**Done when:** you hold four values — DO token, Spaces key ID + secret, and
the keypair files.

## Phase 1 — State bucket and GitHub secrets (~20 min, one-time)

Create the Terraform state bucket in Spaces (control panel → Spaces → Create
bucket), region `fra1`. Bucket names are global, so pick your own —
`kp2-terraform-state-<something>` — and put that name into
`infra/terraform/backend.hcl`. Then add the five repository secrets in GitHub
(Settings → Secrets and variables → Actions): `DO_TOKEN`,
`SPACES_ACCESS_KEY_ID`, `SPACES_SECRET_ACCESS_KEY`, `KP2_SSH_PRIVATE_KEY`
(contents of `kp2-deploy`), `KP2_SSH_PUBLIC_KEY` (contents of
`kp2-deploy.pub`).

**Done when:** the bucket exists and all five secrets are set.

## Phase 2 — Local shakeout of the Terraform module (~30 min, recommended)

The credential-free half is **done**: `terraform init -backend=false` +
`terraform validate` ran against the real `digitalocean/digitalocean` provider
(resolved 2.99.1 under the `~> 2.40` constraint) and the configuration is
valid; `terraform fmt -check` is clean; `.terraform.lock.hcl` is committed with
hashes for `linux_amd64` (what CI runs), `darwin_arm64` and `darwin_amd64`, so
CI's `terraform init` cannot fail on a missing platform hash. What is left
needs your credentials.

Optional but worth it: run the first `init`/`plan`/`apply` from your laptop
before wiring CI, so credential or provider problems surface interactively
rather than inside a workflow log. From `infra/terraform/`:

```bash
export AWS_ACCESS_KEY_ID=<spaces key id>
export AWS_SECRET_ACCESS_KEY=<spaces secret>
export TF_VAR_do_token=<do token>
export TF_VAR_ssh_public_key="$(cat ~/path/to/kp2-deploy.pub)"
terraform init -backend-config=backend.hcl
terraform plan   # expect 4 resources: project, ssh key, droplet, firewall
terraform apply
```

`plan` is where the credentials themselves get checked — the provider and the
configuration are already validated (above). After apply, confirm
the droplet booted ready: `ssh -i ~/path/to/kp2-deploy root@$(terraform output
-raw droplet_ip) cloud-init status --wait && docker compose version`. Leave the
droplet up for Phase 3, or destroy it — either works, CI recreates at will.

**Done when:** `apply` succeeds, the droplet appears inside the `kp2-linkup`
DO project (not the default one), and `docker compose version` answers over
SSH. **Rollback:** `terraform destroy` — the account is back to bucket-only.

## Phase 3 — Wire up CI (~30 min, one-time)

The file work is **done**: the workflow lives at
`.github/workflows/kp2-federation.yml` (the monorepo root — GitHub reads
workflows nowhere else, so there is one copy, not a copy plus a reference
version that can drift), and `infra/` is committed on `main`. What is left is
to push, then run the workflow from the Actions tab with
`action: up`. First run budget is ~20–25 minutes: droplet boot and apt
(~3–4 min), image pulls (several GB, digest-pinned), then the pack's own
sequence — `gen-secrets` → `preflight` → `preload-images` → `deploy` (the
~11–13 min Hurl admin-API run) → `seed` → `acceptance`. The run summary ends
with the exact SSH tunnel command.

Two failure modes to expect on a first run, neither structural: a healthcheck
timeout if the droplet is slow (the pack's guidance: bigger droplet before
raising `retries:` in `hurl/compose.hurl.yml`), and transient apt/registry
flakes during cloud-init (re-run the workflow; `up` is safe to repeat — a
live federation is detected and proven, not re-initialised).

**Done when:** the workflow is green end to end, `acceptance.sh` included.

## Phase 4 — Human verification and the join demo (~45 min, one-time)

Open the tunnel from the run summary
(`ssh -L 8090:127.0.0.1:8090 -L 4000:127.0.0.1:4000 -L 8091:127.0.0.1:8091
root@<ip>`), then check the console at `http://127.0.0.1:8090` and the CS UI
at `https://127.0.0.1:4000`. On the droplet, run the pack's own proof at the
tier the pack prescribes for a finished change: `scripts/verify.sh --live`.
Then exercise what a laptop demo exercises: a hosted join through the
console's **4 · Join a member** tab (PTSB reaching `ACTIVE, verified: true`),
`member.sh list`, `member.sh remove`. This confirms the two droplet-specific
seams — the monorepo mount with `.git`, and the join tokens generated
droplet-side — actually hold.

**Done when:** `verify.sh --live` is green on the droplet and a hosted join
completes and retires cleanly. This is the milestone at which the deployment
is *demonstrated*, not just deployed.

## Phase 5 — Operating rhythm (ongoing)

The steady state is a three-step demo-day routine: run the workflow with
`up` about half an hour before you need the federation; tunnel in and demo;
run `destroy` when done. Costs at that rhythm are ~$0.14/hour while up plus
the flat $5/month Spaces subscription — call it $7/month for weekly sessions,
versus ~$101/month if left always-on. Code changes ship with `action: deploy`:
against a live stack it re-syncs the checkout and proves it (acceptance), and
with `redeploy: true` it does the full purge-and-cold-redeploy when the change
touches deployment shape. The `concurrency: kp2-federation` guard means runs
queue rather than collide; there is exactly one droplet, so there is nothing
to coordinate beyond "one workflow run at a time," which it enforces itself.

Things that intentionally do NOT survive a destroy: the droplet-side `.env`
(fresh secrets each `up`, harmless on a fresh token), joined members (they
re-register — that re-registration *is* the demo), and anything in `out/`.
The DO project, the state bucket, and the GitHub secrets persist.

## Phase 6 — Hardening backlog (optional, in effort order)

None of these block anything above; pick them up if the deployment outgrows
"ephemeral demo." First, tighten `admin_cidrs` to your own IP once you know
whether you ever run `deploy` from outside CI — with GitHub-hosted runners
this means either accepting open-SSH (key-only) or moving to a self-hosted
runner or Tailscale on the droplet, which also removes the tunnel step from
the demo routine. Second, a non-root deploy user on the droplet (docker group,
sudo-less) — cosmetic for a single-purpose ephemeral host, expected for
anything longer-lived. Third, size up to `s-8vcpu-32gb` (~$0.29/hr) if
own-Security-Server joins become part of the demonstration — the analysis
covers why 16 GB is tight for a fifth JVM. Fourth, if you want pushes to
`KP2-build-pack/**` on main to auto-run `deploy`, add a `push` trigger with a
path filter to the workflow — deliberately left out so a demo droplet never
appears surprisingly on someone's bill.

## Acceptance checklist (the whole implementation, at a glance)

- [ ] Phase 0: DO token, Spaces keys, deploy keypair in hand
- [ ] Phase 1: state bucket created; bucket name in `backend.hcl`; five GitHub secrets set
- [x] Phase 2 (no credentials needed): `terraform validate` green against the real provider, `fmt` clean, lock file pinned for `linux_amd64`
- [ ] Phase 2 (needs credentials): `terraform apply`; droplet lands in the dedicated project; `docker compose version` over SSH
- [x] Phase 3 (no credentials needed): workflow at `.github/workflows/kp2-federation.yml`, `infra/` committed on `main`
- [ ] Phase 3 (needs credentials): `up` run green including `acceptance.sh`; tunnel command in run summary
- [ ] Phase 4: `verify.sh --live` green on the droplet; hosted join → `ACTIVE, verified: true` → remove, all via tunnel
- [ ] Phase 5: one full `destroy` → `up` cycle completed, proving the ephemeral loop end to end
- [ ] Phase 6 items triaged: decided (not necessarily done) for each

## Risk register (abridged)

The three risks worth naming, with their mitigations already in place: a
**non-idempotent re-deploy** breaking a live stack (mitigated: live-stack
detection + explicit `redeploy` input, fixed in this review); **undersized
host** turning healthcheck timeouts into phantom federation defects
(mitigated: 16 GB floor, pack guidance says scale the droplet, and the
`droplet_size` variable makes that a one-line change); **state loss** if the
Spaces bucket is deleted with a droplet still alive (mitigated by habit, not
code: destroy through the workflow, never delete the bucket first — recovery
is manual droplet deletion in the DO console, which the dedicated project
makes easy to spot).
