# Deploying Linkup on DigitalOcean with Terraform — brainstorm

**Status:** brainstorm, not a spec. Nothing here is decided or built.
**Scope:** a `target:` beyond `docker-local` (PLAN.md §9's "genuine non-Docker
target is a separate, not-yet-started spec").

Chosen starting posture, from the opening conversation:

| Question | Answer |
| --- | --- |
| Purpose | Private team sandbox |
| Topology | Droplet per Security Server |
| Access | Console public (TLS + auth), X-Road admin private |
| State / secrets | DO Spaces backend + SOPS/age |

Three of those four sit comfortably together. The third does not — see §1.

---

## 1. The one thing to argue about first: a public console

`docs/production-delta.md` already says this, in its own words:

> Console holds admin credentials server-side; loopback bind plus a CSRF guard
> … are its only access controls — neither is authentication, and the guard
> defends the write/exchange endpoints against a cross-origin *browser*, not
> against anyone who can already reach `:8090` directly.
>
> → *Credentials never colocated with a public-facing demo tool.*

A publicly-reachable console is therefore not "the console, exposed." It is
**the federation's admin credentials, exposed**, gated by whatever the proxy in
front adds and nothing else. It can read and mutate ACLs, and its write path
"exists purely to be theatrical for an audience."

That is a real conflict with "private team sandbox," which is the posture the
rest of this document assumes. Three ways out, in descending order of how much
I'd recommend them:

- **(a) Don't.** Keep the console on the tailnet with everything else. A "private
  team sandbox" by definition has no audience that can't be given a tailnet
  invite. This costs one droplet and one certificate less and removes the entire
  category of risk. **Recommended.**
- **(b) Public, but read-only.** Expose the console behind TLS + auth *with the
  ACL write path and the join tab disabled* — a demo-viewing mode. This needs a
  real flag in `apps/console/app.py` and a test that proves the mutate routes
  404 in that mode, which is a genuine (small) piece of pack work, and it is the
  version I'd sponsor if an audience truly must see it.
- **(c) Public, full function, behind an auth proxy.** Only if (b) is refused.
  Then the console must run on its **own** droplet, proxying to X-Road over the
  VPC, so the edge box holds no X-Road ports; and the admin credentials it holds
  should be a purpose-made account, not the CS's fixed `xrd/secret` (which the
  test image cannot rotate — so on DO, this argues for a non-test CS image).

Everything below is written for (a)/(b); (c) only changes §4's edge tier.

---

## 2. Topology: what "droplet per Security Server" actually implies

### 2.1 The shape

```
                      internet
                          │
                    (nothing, except:)
                          │
              ┌───────────┴────────────┐
              │  tailnet (WireGuard)   │      ← the only way in
              └───────────┬────────────┘
                          │
  ┌───────────────────────┴────────────────────────────────┐
  │  VPC  10.10.0.0/20   (private, no public route)        │
  │                                                        │
  │  orchestrator ── cs ── ca                              │
  │       │           │                                    │
  │       │      app-pnia app-plr app-pemis app-ptsb       │
  │       │      console  join-api                         │
  │       │                                                │
  │       ├── ss-pdga   (management SS)                    │
  │       ├── ss-plr                                       │
  │       ├── ss-pnea                                      │
  │       ├── ss-pnia   ┐ full profile only                │
  │       └── ss-moeys  ┘                                  │
  └────────────────────────────────────────────────────────┘
```

Droplet count: **lite = 3 SS + core + orchestrator = 5**; **full = 5 SS + core +
orchestrator = 7**. The orchestrator can fold into `core` if you don't mind the
Hurl runner living beside the Central Server.

### 2.2 Sizing, from the pack's own measurements

`docs/production-delta.md` measured this stack precisely, so sizing is not
guesswork:

| Component | Measured | Droplet |
| --- | --- | --- |
| Security Server (each) | 2.07–2.29 GiB | 4 GB / 2 vCPU |
| Central Server | 1.7–1.9 GiB | 4 GB / 2 vCPU |
| CA + 4 mocks + console + join-api | ~90 MiB + ~33 MiB×4 + ~41 MiB | fits on `core` |

Give each SS a 4 GB droplet and it runs at ~55% memory — tight-ish but real,
and X-Road sidecars are not bursty. If a `--full` run ever OOMs a Security
Server, the answer is 8 GB on that one droplet, not a rebuild.

**Rough cost** (verify against DO's current pricing page — the ladder below has
been stable but rates change): 4 GB/2 vCPU ≈ $24/mo, 8 GB/4 vCPU ≈ $48/mo.
So lite ≈ 5 × $24 ≈ **$120/mo**; full ≈ 7 × $24 ≈ **$168/mo**; plus block
storage (~$0.10/GiB/mo) and Spaces (~$5/mo).

Compare: the *same* stack on **one** 16 GB droplet is ≈ **$96/mo** and needs
zero pack changes. That comparison is the whole argument of §7's phase 1 — the
split topology costs more money *and* more engineering, and it buys exactly one
thing: it is what production actually looks like. That's a legitimate purchase
for a reference environment. It's a strange one for a sandbox. Decide
deliberately.

**Cost control worth building in from day one:** a scheduled shutdown. A sandbox
that runs nights and weekends triples its own bill. Terraform can't schedule
this; a DO Function or a cron on the orchestrator calling the API can. Note that
X-Road tolerates being stopped and started far better than it tolerates being
snapshotted and restored *later* — see `production-delta.md`'s "Federation
snapshots — measured, and their real shelf life."

### 2.3 What breaks when the bridge network goes away

This is the substance of the work. Today every inter-server address is a Docker
DNS name on the `linkup` bridge (`cs`, `ss-pdga`, `ca`), and every admin API is
a published localhost port (`1000`, `2000`, `3000`, `5100`, `6000`). Split
across droplets, both assumptions die.

**(i) Names.** X-Road AUTH certificates are issued for the Security Server's
`security_server.dns_name` in `configs/`. Whatever names you use on DO **must
match those**, or every certificate sequence fails. The cheapest way to satisfy
that is to keep the names exactly as they are and make them resolve to VPC
private IPs.

Options: a Terraform-rendered `/etc/hosts` on every droplet
(`templatefile()` over `digitalocean_droplet.*.ipv4_address_private`) —
no DNS dependency, no public leakage, one line of cloud-init; versus real DNS,
which DO does not offer as a *private* zone, so you'd be publishing private IPs
in public A records or running your own resolver. **`/etc/hosts` wins.** Its one
real cost: replacing a droplet rewrites the file on every other droplet, so make
that a Terraform-managed file with a cloud-init `write_files` + a re-render on
IP change, not something you hand-edit.

The happy consequence: `hurl/generate.py`'s host values need **no change at
all**. The names stay `cs` and `ss-pdga`; only their resolution moves.

**(ii) Ports and `network.bind`.** `deployment.yaml`'s `network.bind: 127.0.0.1`
now makes the servers unreachable from each other. But setting it to `0.0.0.0`
trips `scripts/lib-stack.sh`'s refusal, and rightly so — that refusal exists
because the SS `:8080` client-proxy interface has *no authentication at all*
("anyone who can reach it can impersonate any subsystem this server hosts").

Neither branch is correct here. The right value is **the droplet's own private
VPC IP** — reachable by the other X-Road hosts, unreachable from the internet,
and further gated by a cloud firewall scoped to the VPC CIDR. That is a
materially different security posture from `0.0.0.0` on a public interface, and
the pack's guard doesn't currently model it.

So this needs a real change to `lib-stack.sh` and `deployment.yaml`: a third
case, e.g.

```yaml
network:
  bind: vpc          # resolves at deploy time to this host's RFC1918 VPC address
```

with the guard's rules becoming: loopback → allow; RFC1918 → allow **but require
`network.vpc_cidr` to be declared and warn**; anything else → today's refusal.
Not "add another `acknowledge_` escape hatch" — that would make it one word away
from an exposed federation, which is the failure mode the current design
deliberately avoids.

**(iii) Cross-host ports that must open on the VPC.** Roughly (confirm against
X-Road 7.7 docs before writing firewall rules):

| From | To | Port | What |
| --- | --- | --- | --- |
| every SS | cs | 80 | global conf download |
| SS | SS | 5500 | message exchange |
| SS | SS | 5577 | OCSP response |
| SS, cs | ca | 8888 / 9998 / TSA | Test CA sign, OCSP, timestamping |
| SS | app-* | mock ports | provider backends |
| orchestrator | cs, every SS | 4000 | admin API — the Hurl bootstrap |
| console, join-api | cs, every SS | 4000 | admin API |
| console/consumer | SS | 8080 | client proxy — **the dangerous one** |

Nothing on that list should be reachable from outside the VPC.

**(iv) The bootstrap runner.** `hurl/run-linkup.sh` currently reaches every admin
API on `localhost:<published port>`. On DO it reaches `https://ss-pdga:4000` etc.
`hurl/generate.py` already threads a per-server `HOSTVAR`, and `hurl/topology.sh`
already declares `SS_UI`/`SS_REST`, so this may be mostly a mapping change rather
than a rewrite — worth a spike before committing to a number.

Run it from the **orchestrator droplet inside the VPC**, not from your laptop
over the tunnel: `--full` is a ~14 min cycle at `profile: full` and ~8 min at
`lite`, and pushing that through a VPN adds latency to every one of several
hundred admin-API calls. The orchestrator is also the natural single home for
`.env` (see §5) and for `scripts/verify.sh`.

**(v) Persistence.** Attach a **DO Block Storage volume per droplet**, mounted at
`/var/lib/docker/volumes`. This matters more than it looks: `/etc/xroad` holds
the softtoken with each member's SIGN and AUTH private keys. Lose it and the
member must re-register — a 60–160 s dance here, days in production.
`digitalocean_volume` + `lifecycle { prevent_destroy = true }`, and detach/reattach
rather than recreate.

---

## 3. Terraform layout

Keep a hard seam: **Terraform provisions infrastructure; the pack stands up the
federation.** Terraform's output is an inventory; `hurl/run-linkup.sh` consumes
it. Do *not* drive the federation bootstrap from `remote-exec` provisioners —
they aren't idempotent, they swallow failures, and they'd put a 14-minute
X-Road sequence inside `terraform apply`'s failure semantics. Cloud-init does
"install Docker, mount the volume, join the tailnet, write `/etc/hosts`, clone
the pack"; a plain `make deploy` on the orchestrator does the rest. This also
keeps `scripts/verify.sh`'s three tiers meaning exactly what they mean today.

```
infra/
  versions.tf          required_version, pinned provider versions
  backend.tf           s3 backend → Spaces
  vpc.tf               digitalocean_vpc, explicit CIDR
  droplets.tf          for_each over a topology map
  volumes.tf           one per droplet, prevent_destroy
  firewalls.tf         one per role, not per droplet
  tailscale.tf         auth key (ephemeral, pre-approved, tagged)
  dns.tf               only if the console goes public
  outputs.tf           inventory: name → private IP, for /etc/hosts + Ansible-less deploy
  cloud-init/
    common.yaml.tftpl
    orchestrator.yaml.tftpl
  envs/
    lite.tfvars        3 SS
    full.tfvars        5 SS
```

Drive the droplet set from a single map so lite/full is one variable, mirroring
`deployment.yaml`'s `profile:`:

```hcl
locals {
  security_servers = var.profile == "full" ?
    ["pdga", "plr", "pnea", "pnia", "moeys"] :
    ["pdga", "plr", "pnea"]
}
```

**Do not let this drift from `deployment.yaml`.** Two sources of truth for
"which servers exist" is exactly the class of bug this pack keeps eliminating
(`hurl/topology.sh` was generated for precisely this reason). Better: have
Terraform *read* `deployment.yaml` (`yamldecode(file(...))`) and derive the list,
so `profile:` stays the single knob. Or generate `lite.tfvars`/`full.tfvars`
from `hurl/generate.py` and add them to the `--fast` static checks.

Hygiene: `tflint` + `tfsec`/`checkov` in `scripts/verify.sh --fast` (they're
static, fast, and this is the tier that owns the ship gate); `prevent_destroy`
on volumes and the Spaces bucket; separate state per profile.

---

## 4. Security layers, outside in

**1. Nothing public.** Not "SSH from my IP" — *nothing*. Install Tailscale (or
plain WireGuard) via cloud-init on every droplet with a pre-authorised,
ephemeral, tagged auth key; bind `sshd` to the tailnet interface; DO cloud
firewall permits no public inbound whatsoever. This deletes the entire
brute-force and scanner surface, and it's ~6 lines of cloud-init. If you use
Tailscale, `tailscale ssh` also removes SSH key distribution as a problem.

**2. DO Cloud Firewalls, per role, not per droplet.** They filter at the
hypervisor — traffic they block never reaches the droplet's NIC at all — and
they apply to VPC traffic too, so a rule scoped to the VPC CIDR is a real
control, not decoration.

- `fw-core`: inbound from VPC CIDR only, on 80/4000/8888/9998 + mock ports.
- `fw-ss`: inbound from VPC CIDR only, on 4000/5500/5577/8080/8443.
- `fw-orchestrator`: no inbound at all (tailnet is not filtered by the cloud
  firewall in the same way — verify this against your Tailscale mode, it's the
  one rule most likely to bite).
- `fw-edge` (only under §1(b)/(c)): inbound 443 from anywhere, nothing else.
- **Egress**: default-allow is the DO default and is worth tightening. This
  stack's OCSP and TSA are the *local* Test CA, so egress can be limited to 443
  (Docker Hub, Tailscale, apt) + DNS. Doing so also constrains what a
  compromised `:8080` proxy can reach outward.

**3. VPC.** `digitalocean_vpc` with an explicit non-default CIDR, all droplets
in it, all X-Road traffic over private IPs. Inaccessible from the internet and
from other VPCs, and private traffic doesn't count against bandwidth — which
matters, because X-Road servers chat constantly.

**4. Host hardening** (cloud-init): non-root deploy user, `PermitRootLogin no`,
`PasswordAuthentication no`, unattended-security-upgrades, Docker daemon not
listening on TCP at all.

**5. Edge tier, only if §1 lands on (b)/(c):** its own droplet, Caddy with
Let's Encrypt via **DNS-01** through the DigitalOcean DNS provider — so port 80
never has to open — plus `oauth2-proxy` (Google/GitHub) or at minimum
`basic_auth` with an argon2 hash. It proxies over the VPC to `console:8000`. It
holds no X-Road ports and no `.env`.

**What this does *not* fix, and shouldn't pretend to:** the Test CA is still the
trust anchor, the CS admin credentials are still fixed in the test image, the
mocks are still mocks, service URLs are still plain HTTP inside the VPC. Every
row of `production-delta.md`'s table survives this deployment. Moving to DO
changes the *hosting*, not the *demo shortcuts*. It would be worth adding a row
to that table: "runs on cloud infrastructure the team does not operate 24/7."

---

## 5. State and secrets

**Terraform state → Spaces.** S3 backend with `endpoints.s3`,
`use_path_style = true`, and the `skip_*` validations Spaces requires. Enable
bucket versioning and SSE, and deny public access explicitly. Since Terraform
1.11, **`use_lockfile = true`** gives native S3 locking (a `.tflock` object
beside the state) and DynamoDB-based locking is deprecated — so Spaces now
supports real locking, which it historically didn't. Test it works against
Spaces before relying on it for a second operator.

**The thing to be disciplined about: state is a secret store whether you want it
to be or not.** Anything passed through a Terraform variable or `user_data`
lands in state in plaintext. So:

- The DO API token, Spaces keys, and the Tailscale auth key live in your local
  environment (SOPS-encrypted, `age` key on your machine + one offline backup),
  **not** in `.tfvars`. Export as `DIGITALOCEAN_TOKEN` etc.
- The Tailscale auth key does pass through `user_data` and therefore state.
  Mitigate by making it **ephemeral and single-use** so its post-apply value is
  worthless.
- **The pack's own secrets never go through Terraform at all.** Run
  `scripts/gen-secrets.sh` *on the orchestrator*, so `XROAD_TOKEN_PIN`,
  `XROAD_ADMIN_PASSWORD` and the two join tokens are born inside the VPC and
  never touch state, your laptop, or git. `.env` then exists in exactly one
  place, which is also what makes destroying the sandbox a clean operation.
- Use SOPS/age for what genuinely benefits from being in git: `deployment.yaml`
  overrides, the edge tier's `basic_auth` hash, any OAuth client secret. Commit
  `secrets.enc.yaml`, decrypt on the orchestrator.

One more: `KP2_JOIN_APPLICANT_TOKEN` is *one shared token for every applicant*
(`production-delta.md`). On a box other people can reach, that token is now a
network-reachable credential rather than a local-loopback one. If the join API is
reachable at all beyond the tailnet, this needs per-agency credentials first.

---

## 6. Open questions worth resolving before any code

1. **Does the console actually need to be public?** §1. This is the only
   decision that changes the security architecture rather than its parameters.
2. **Does `hurl/run-linkup.sh` work unmodified once hosts resolve via
   `/etc/hosts` and admin APIs move to `:4000`?** A one-day spike on a single
   droplet + one split-off SS answers this and de-risks the whole plan.
3. **Does an X-Road federation survive `docker compose down` / droplet stop and
   restart across days?** Related to but distinct from the snapshot shelf-life
   finding. Determines whether nightly shutdown is viable, which determines cost.
4. **Which region?** Latency to your team, and any data-residency story the KP
   narrative wants to tell. (`fra1` / `ams3` for an ITU-adjacent audience.)
5. **Does this become `target: do-vpc` in `deployment.yaml`, or a sibling repo?**
   The pack's `--fast` tier would gain Terraform linting either way; the question
   is whether `docker-local` and a cloud target share `deployment.yaml`'s schema.
6. **What is the teardown story?** A sandbox nobody can confidently destroy and
   rebuild isn't a sandbox. `terraform destroy` must be a routine, tested
   operation — which argues for the volumes being *deliberately* `prevent_destroy`
   and a documented two-step destroy.

---

## 7. Suggested sequencing

**Phase 1 — one droplet, zero pack changes.** A single 16 GB droplet,
Tailscale only, `network.bind` still `127.0.0.1`, everything on the Docker
bridge exactly as today. Terraform provisions droplet + volume + firewall +
tailnet; cloud-init installs Docker and clones the pack; you run `hurl/run-linkup.sh`
over the tunnel. This proves the cloud target end to end, costs less than the
split topology, and finds the unknowns (image pull time, disk, DO quirks) before
any of them are entangled with the multi-host work. **Do this first even if the
destination is the split topology.**

**Phase 2 — split the Security Servers out.** VPC, `/etc/hosts` rendering,
`network.bind: vpc` and its guard change, per-role firewalls, orchestrator
droplet. This is where the real spec is needed and where `target: do-vpc` earns
its keep. Success criterion: `scripts/verify.sh --full` green against the split
topology.

**Phase 3 — scale up the droplet count** to match the full canonical member
set, and run a measured `--full` to compare against the single-host baseline
recorded in `production-delta.md`. Expect it to be *slower*, not faster —
network hops replace loopback — and record the number the way the pack
records every other measurement.

**Phase 4 — the console question**, whichever way §1 goes, plus nightly
shutdown, a tested `terraform destroy` → `apply` → `run-linkup.sh` cycle, and a
new `production-delta.md` row for what cloud hosting does and doesn't change.

Sources: [DO VPC best practices](https://docs.digitalocean.com/products/networking/vpc/concepts/best-practices/) ·
[DO Cloud Firewalls](https://www.digitalocean.com/products/cloud-firewalls) ·
[Spaces as a Terraform backend](https://docs.digitalocean.com/products/spaces/reference/terraform-backend/) ·
[Terraform s3 backend](https://developer.hashicorp.com/terraform/language/backend/s3)
