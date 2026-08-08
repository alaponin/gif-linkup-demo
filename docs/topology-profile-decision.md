# Topology and profiles — decision analysis

**Status: DECIDED and implemented.** T1 (4 servers, all own-server, no profile
split) was chosen on the evidence in §2.3/§6 below. `deployment.yaml` has no
`profile:` key; one topology exists. This document is kept as the decision
record: why T1 over T2, and why dropping the profile split was worth it
independent of server count. Current measured figures live in
`docs/production-delta.md`.
**Question analysed:** can we cut Security Servers further, and drop the
`full` / `lite` profile split entirely?
**Feeds:** `docs/onboarding-alignment-design.md` §4.3 and §8.2.

**Short answer:** the floor is **3 Security Servers**, not fewer; profiles can go,
and dropping them is worth more than the server count is. The two questions are
coupled — whether a single profile is viable depends on which topology wins.

---

## 1. The floor — what cannot be removed, and why

### 1.1 `ss-pdga` is load-bearing

`hurl/steps.py` on `ss.mgmt_register`:

> "**PDGA-only: nominates the management Security Server as the provider of the
> CS's own management services.** No other member's bring-up runs this."

X-Road's Central Server needs a management service provider, and that subsystem
has to live on a Security Server. The only ways to remove `ss-pdga` are to host
`PDGA:MANAGEMENT` on a *member's* server — putting the operator's management
services on a member's infrastructure, which inverts the governance separation
Module 3 teaches — or to skip management services entirely, which is not X-Road.

**`ss-pdga` stays.** It is also the reason a reduced topology never drops below
three servers: PDGA is load-bearing in every configuration.

### 1.2 Three members is the floor, for reasons already established

- **Two providers** — once-only means composing across two authoritative sources.
  One provider makes it a proxied API call. Collapsing PNIA and PLR into one
  agency destroys the *cross-agency* premise, which is the whole subject.
- **One consumer** — the exchange needs a caller.

So: 1 management server + 3 members. The only remaining question is **how many of
the three members own a Security Server**, and that is decided by G2, not by
taste.

### 1.3 What G2 decides for us

| Member | Data | G2 says |
|---|---|---|
| PNIA | Authoritative **personal** data (`nin`, `given_name`, `date_of_birth`, `sex`) | Must own its server — hosting it puts its signing key on a peer's token |
| PLR | Authoritative enrolment data | Same |
| PNEA | Consumes only; publishes nothing | **May be hosted** — the path names "small consumer-only bodies" as exactly what hosting suits |

So the floor is **3 Security Servers** (PDGA, PNIA, PLR) with PNEA hosted, and
the realistic alternative is 4 with PNEA on its own.

---

## 2. The options

**T1 (4 servers, all own-server) is what got built** — see §6. T2 was never
built, so its figures below are estimates derived from two earlier measured
points (a five-own-server topology and a three-own-server-plus-two-hosted-clients
topology), giving roughly 200s per own-server bring-up.

| | **T1 — 4 servers** | **T2 — 3 servers** |
|---|---|---|
| Servers | pdga, pnia, plr, pnea | pdga, pnia, plr |
| Hosted | none | PNEA |
| `--full` cycle | **~13 min, measured** — see `docs/production-delta.md` | ~8 min, estimate |
| RAM | **~11 GiB, measured** | ~9 GiB, estimate |
| G2 compliant | ✓ | ✓ |
| Both exchange legs cross-server | ✓ | depends on host — §2.1 |
| Hosted path in cold deploy | ✗ | ✓ |

### 2.1 T2's one complication: where PNEA is hosted

| Host | Consequence |
|---|---|
| `ss-plr` | PNEA→PLR becomes intra-server; only PNEA→PNIA crosses. Weakens one leg of the once-only demonstration. |
| `ss-pnia` | Mirror image — PNEA→PNIA intra, PNEA→PLR crosses. |
| `ss-pdga` | **Both legs stay cross-server.** But the operator holds a member's signing key. |

`ss-pdga` is the least-bad host on the trust argument: PDGA already runs the
Central Server and can register or de-register anyone, so it holding PNEA's
signing key adds little marginal power — whereas PLR holding it would give a
*peer member* authority the governance model never grants. It is still worth
checking against Estonia/Finland practice before committing, since the path
describes hosting between members and is silent on operator-as-host.

### 2.2 The argument that pushes toward T2 — and it is not the server count

If T1 is chosen **and** profiles are dropped, no canonical member is hosted, so
`build_hosted_client()` and `resolve_hosted_on_map()` leave the cold-deploy path
entirely. They would still be exercised by the join API (every joined member is
hosted by default) and by `--full`'s join, so the code is not dead — but the
*cold-deploy* hosted path loses its only coverage.

T2 keeps it covered, and gets a pedagogical bonus: it puts the **G2 hosting
decision into the canonical set as a worked example** — authoritative publisher
gets its own server, consumer-only body is hosted. That is a lesson T1 has to
teach in prose and T2 teaches by construction. It is the strongest argument in
this document, and it is not about cost.

### 2.3 What the literature and practice actually say — checked, and it flips §5

The §2.2 argument rested on "consumer-only bodies are hosted" being established
practice. It is not, or not in the form used there. Checked against X-Road
documentation and the two reference instantiations:

**Confirmed — hosting itself is first-class.**

- Multi-tenancy is architectural, not a workaround: *"A single Security Server
  can host several organizations (multi-tenancy), where the organization managing
  the Security Server is the server owner, and the hosted organizations are
  Security Server clients."*
- X-Road's glossary has a defined term for it — **security server host**: *"a
  member who provides security server hosting services to third parties and
  other members."*
- *"A Security Server can be shared between multiple organizations or provided as
  a service by a third party."*

**Confirmed — but the host is a commercial third party, not the operator.** In
Estonia this is a live market: Telia sells an X-tee turvaserver as a managed
shared service (through Riigipilv, the state cloud), with HSM-backed keys, and
subscribers authorise Telia to manage their certificate. turvaserver.ee and Almic
offer comparable services. **RIA — the operator — is not the host.** A T2
variant that put PNEA on `ss-pdga`, the operator's server, has no support in the
practice found here.

**Not confirmed — "consumer-only" as the hosting criterion.** Nothing ties
hosting to the consumer-versus-provider distinction. X-Road's own definition of a
security server client is role-neutral: *"a member or a subsystem of a member,
whose relation with the security server is registered… who can use the security
server on behalf of a member to exchange data."* Providers are hosted too — that
is what the commercial services sell.

The consumer-only rule is the **onboarding path's own inference**, and a
defensible one: it follows from the signing-key delegation argument in G2. But it
is the path's reasoning, not sourced practice, and this document originally
treated it as established. **Worth flagging back to the path author** — G2's
exit test would be stronger citing the commercial-host model, where the
delegation is handled by contract and HSM rather than avoided.

**Finland answers "small organisation" differently, and the pack already
implements its answer.** Rather than hosting, DVV points small organisations at
the **containerised Security Server Sidecar** — *"allowing it to run on any Linux
platform without requiring a separate Host Server… particularly suited for
smaller organizations seeking a lightweight deployment approach."*

`docker-compose.yml` already runs `niis/xroad-security-server-sidecar` for every
Security Server, measured at ~2.1 GiB each. **The pack is already running
Finland's answer.** Under that model the cost of a member owning its server is a
container, which is precisely why an all-own-server topology is affordable here
and would not be on physical hosts.

### 2.4 What is not on the table

- **2 servers** — needs one provider, which kills once-only, or hosts an
  authoritative publisher on a peer, which violates G2.
- **A single hosted-PNIA profile** — hosts PNIA on `ss-plr`, i.e. models a peer
  holding the national identity authority's signing key. Already rejected on the
  same G2 grounds (`docs/onboarding-alignment-design.md` §8.2).

---

## 3. Dropping the profiles — the work

Ten changes landed across `generate.py`, `deployment.yaml`,
`docker-compose.yml`, the golden corpus, `acceptance/*.md`, `README.md`,
`runbook.md`, `docs/production-delta.md` and the console test fixtures — all
in one pass with a single golden regeneration, per §4.4's recommendation
below. Two workarounds it removed are worth remembering as *why* this was
worth doing, not just cheaper config — see §4.1.

---

## 4. Consequences

### 4.1 Two workarounds disappear

These are the parts worth having beyond "less config."

**The `depends_on` hole.** `hurl/README.md`:

> "The runner's `depends_on` waits on `cs`, `ca`, `ss-pdga`, `ss-pnea` and
> `ss-plr` only; `ss-pnia` and `ss-moeys` belong to the `full` compose profile,
> **which a non-profiled dependency cannot reference**, so they are covered by
> the retries instead."

Two servers are outside the dependency graph and rely on retry timing. That is a
profile artefact, and it vanishes with profiles.

**Stub scenario files.** Under lite, `20-ss-pnia.hurl` and `22-ss-moeys.hurl` are
written as stubs "still written, so `manifest.yaml`'s scenario claims keep
resolving" — files that exist to satisfy a checker rather than to run. Also gone.

### 4.2 The test matrix collapses

Before: 3 tiers × 2 profiles, with README guidance on which combination to run
when ("develop against lite… run one `--full` under full profile before closing
out a plan"). After: 3 tiers. One golden, one topology, one story for a learner
to hold.

**The profile split was optimising the tier that runs least.** `--fast` and
`--live` are the day-to-day tiers, both under a couple of minutes; `--full` runs
"once before the plan is closed out, not a per-task ritual." Profiles existed
because `--full` at the largest historical topology was painful — but run once
per plan, at the current ~13-minute figure, the pain is gone.

### 4.3 What is genuinely lost

- **A documented cheap cycle**, if the single topology is slow. T2 (~8 min,
  estimate) is close to the historical lite speed, so little is lost there. T1
  (~13 min, measured) is a real regression against lite for anyone who
  redeploys often.
- **Scale demonstration.** Showing the federation at two sizes goes away. Minor —
  the join API demonstrates growth better, and live.
- **Cold-deploy hosted coverage**, under T1 only. See §2.2.

### 4.4 Risk

The main one is **concentration.** Removing profiles landed alongside the
member reduction, the rename and the frozen-contract amendment, in the same
piece of work — the largest in the programme.

Splitting it would have been worse: profile removal changes topology, so doing
it separately buys a second re-baselining event and breaks the "one
re-baselining event" rule (`docs/onboarding-alignment-design.md` §1.1).
Structuring that work as sequenced steps with a single regeneration at the
end, and its own `--full` proof, kept it to one pass.

---

## 5. Testing strategy impact

Short version: **the cheap tiers are untouched, the golden corpus does not
shrink, and for a normal plan the total verification time goes *down* despite
`--full` being slower than lite was.**

### 5.1 Profiles never made the cheap tiers cheap

| Tier | Touches topology? | Effect of this change |
|---|---|---|
| `--fast` | **No** — "no running containers, no network, no federation" | Essentially none; marginally simpler |
| `--live` | Needs a running stack, **never deploys one** | None in kind |
| `--full` | Yes — purge, deploy, seed, acceptance | Single number now, not a profile-dependent pair — see `docs/production-delta.md` |

`--fast` is the tier that runs after every step, and it never had a federation to
be a profile of. `--live` runs once per task and refuses to deploy — it uses
whatever stack is already up. **Neither got more expensive.** The profile split
was only ever discounting `--full`, the tier the README itself says is "not a
per-task ritual."

So the answer to "is there still a cheap way to run checks": yes, the same two
ways as before, at the same cost.

### 5.2 Golden files stay — and can keep covering the hosted path

They do not disappear, and they need not even halve. The key is a distinction the
profile split blurred:

- `deployment.yaml`'s `profile:` — **a deployment choice**: which topology gets
  stood up in Docker.
- `generate.py --topology-fixture` — **a generator input**: which topology gets
  rendered.

`test_golden.py` uses the second, not the first, and never deploys anything.
**Removing the deployable profile does not require removing the generator
fixture.**

So the shape landed as:

| | Before | After |
|---|---|---|
| Deployable topologies | 2 (`full`, `lite`) | **1** |
| Golden fixtures | 2, tied to the profiles | **2, decoupled** — one matching the real deployment, one hosted-rendering fixture that is never deployed |
| What the second one is for | A second thing to deploy and document | Keeping `build_hosted_client()` / `resolve_hosted_on_map()` under byte-identical test |

This closes the one real coverage gap T1 opens (§2.2): with no canonical member
hosted, cold-deploy hosted *rendering* would otherwise lose its only golden. The
join API's tests do not cover it — `job.py`'s own docstring is explicit that the
job engine differs from what `run-linkup.sh` does with the same templates ("one
invocation per step, not one per run").

Cost of keeping it: a directory of YAML inputs and a generated tree. No
containers, no RAM, no deploy time, no README guidance — it is a test fixture,
not a configuration a contributor has to choose between.

The generator flag is named `--topology-fixture`, not `--profile`, so nothing in
the pack is called "profile" once the deployment key is gone — sharing the word
is what made these look like one concept.

### 5.3 The `--full` arithmetic improves for a normal plan

The old workflow was "develop against lite for the cheap full cycle, run one
`--full` under full profile before closing out a plan." So a plan used to pay
**N lite cycles plus one full-profile cycle**, against the single-topology
figure now:

| Full cycles in a plan | Old two-profile total | Now (single topology) |
|---|---|---|
| 1 | Highest relative cost | **Lowest** — clearly faster |
| 2 | | Still faster |
| 3 | | Roughly a wash |
| 4+ | | Slower |

Because `--full` is explicitly not a per-task ritual, most plans sit at 1–2
cycles, where dropping the profile is a real, if modest, win — the lite
discount was partly illusory: it was paid back in full at the end of every
plan by the mandatory full-profile proof. A plan that runs `--full` three or
more times before closing out gets no clear win from this change alone. See
`docs/production-delta.md` for the current absolute figures behind this
comparison.

### 5.4 A reliability gain that is worth more than the seconds

`docs/production-delta.md` records a reproducible failure under host CPU
contention with six concurrent Security Server JVMs — an admin API that accepted
a TLS handshake and never completed it, after a Hikari "thread starvation or
clock leap detected" warning.

Server counts in the `--full` path, including an own-server join:

| | Canonical | With own-server join |
|---|---|---|
| Before (full profile) | 5 | **6** — the count that produced the failure |
| After (T1) | 4 | **5** — one clear of it |

A flaky verification tier costs far more than a slow one, because it burns a
full deploy cycle *and* the time spent deciding whether the failure was real.

### 5.5 What to watch

- **`--full` is now the only deploy path**, so a regression in it has no cheaper
  sibling to bisect against. Mitigated by `--fast` and `--live` being untouched,
  and by the hosted fixture keeping the generator honest.
- **`--fast` keeps growing** as tests are added across plans, and `--full` runs
  it inside `hurl/run-linkup.sh`, so it compounds. Unrelated to this decision,
  but §5.3's arithmetic is only as current as `--full`'s own figure, which
  `docs/production-delta.md` keeps up to date.

---

## 6. Recommendation

**Drop the profiles — yes, clearly, and this is unaffected by the topology
choice.** The win is not disk or RAM; it is one topology, one golden, one story,
two workarounds removed, and a README that stops asking a contributor
to choose a profile before they can run a test.

**Topology — T1 (4 servers, all own-server).** This reverses an earlier
recommendation, on the evidence in §2.3:

1. **T2's headline argument does not survive the check.** It was that T2 teaches
   the G2 hosting decision by construction. But the criterion it would teach —
   *consumer-only bodies are hosted* — is the path's inference rather than sourced
   practice, and the host it would use — the **operator** — is contradicted by
   Estonian practice, where hosts are commercial third parties. T2 would teach a
   worked example of something unsourced, which is worse than teaching it in
   prose.
2. **T1 matches the practice that is sourced.** Finland's documented answer for
   small organisations is the containerised Sidecar, not hosting — and the pack
   already runs `niis/xroad-security-server-sidecar` at ~2.1 GiB per server. An
   own server for every member *is* the modern lightweight answer.
3. **Hosting stays demonstrated, and correctly.** The join API's `hosted_on`
   model is member-hosts-member, which is exactly X-Road's defined *security
   server host* — "a member who provides security server hosting services to
   third parties and other members." The pack already models the sourced pattern
   in the right place; only the canonical-set proposal in §2.2 was inventing one.
4. It has no subtleties a reviewer has to re-derive: every call is cross-server,
   every member owns its keys, and there is no delegation to explain.

**Cost of the choice:** roughly 2 GiB and a few minutes more than T2's estimate.
`--full` runs once per plan, so ~13 minutes is acceptable, and it remains
comfortably better than the historical two-profile baseline.

**The one thing T1 gives up** is cold-deploy coverage of
`build_hosted_client()` — it is exercised only through the join path. That is a
testing concern with a testing answer (a join in the `--full` cycle, which
already happens), not a reason to model an unsourced topology in the canonical
set.

### 6.1 Feedback for the onboarding path

G2's hosting table would be stronger with the commercial-host model in it. As
written it frames hosting as a delegation to avoid; practice treats it as a
service to buy, with the signing-key delegation handled by contract and HSM —
Telia's X-tee service is the worked example, and RIA's non-involvement as host is
itself informative. The "suits small consumer-only bodies" row should be marked
as the path's own reasoning rather than reference practice.

---

## 7. What this changed in the design document

| Section | Change |
|---|---|
| §4.3 (the reduction) | Added profile removal; default topology is 4 servers, all own-server |
| §8.1 | Unchanged — component coverage is topology-independent |
| §8.2 | **Confirmed, not superseded.** Its "4 servers, all own-server" correction stands, now with sourced backing rather than only the G2 argument |
| §7 (Risks) | The reduction's scope grew; noted the single-regeneration structure |
| Target shape (§3) | Unchanged — no `hosted_on` in any canonical member config |

Nothing in the data layers, onboarding record or monitoring add-ons work was
affected. The conventions register, governance config, semantic map and
onboarding record are all topology-independent.

---

## Sources

- [X-Road Security Architecture](https://docs.x-road.global/Architecture/arc-sec_x_road_security_architecture.html) — multi-tenancy; server owner vs. clients; key types
- [X-Road Terms and Abbreviations](https://x-tee.ee/docs/live/xroad/terms_x-road_docs.html) — definitions of *security server host* and *security server client*
- [X-Road Architecture](https://x-road.global/architecture) — sharing a Security Server between organisations / provision by a third party
- [Telia X-tee turvaserver](https://www.telia.ee/ari/serverid/x-tee-server) and [Riigipilv — X-tee turvaserver](https://www.riigipilv.ee/teenused/turvalisus/x-tee-turvaserver) — Estonian commercial shared Security Server service, HSM, certificate management delegation
- [turvaserver.ee](https://turvaserver.ee/), [Almic — X-Road security server provider](https://almic.ee/services/x-road/) — further commercial hosts
- [RIA — Data exchange layer X-tee](https://www.ria.ee/en/state-information-system/data-exchange-platforms/data-exchange-layer-x-tee) — operator role
- [Suomi.fi Data Exchange Layer — general technical description](https://kehittajille.suomi.fi/services/data-exchange-layer/service-presentation/general-technical-description) and [Finnish Government — containerised Security Server Sidecar](https://valtioneuvosto.fi/en/-//16079645/the-suomi.fi-data-exchange-layer-is-being-updated-to-improve-user-experience-the-docker-containerised-sidecar-will-be-available-as-of-february-22) — Sidecar as the lightweight option for smaller organisations
