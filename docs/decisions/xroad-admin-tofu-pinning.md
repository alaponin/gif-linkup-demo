# The :4000 admin API — TOFU pinning, not `verify=False`

**Status: DECIDED and implemented.** security-review-remediation-plan.md
Phase C (M1). Current state lives in `docs/production-delta.md` row 19.

**Question analysed:** every caller of a Security Server's or the Central
Server's `:4000` admin REST API ran with certificate verification off
(`verify=False`, `ssl.CERT_NONE`, `curl -k`) — the sidecar image presents a
self-signed certificate nothing issues, so there was no CA to verify it
against. Can this be closed without re-issuing that certificate from a real
CA (out of scope: it is the image's own boot-time behaviour, not this pack's
config)?

## The spike

Before writing any of the four call sites, one question decided the whole
approach: does `ssl.create_default_context(cafile=<the server's own
certificate>)` verify a connection to that server at all, given the
certificate is self-signed?

Verified live against a running `ss-pdga`: **yes**, both through Python's
`ssl`/`httpx` and through `curl --cacert`, *provided the certificate carries
`basicConstraints CA:TRUE`* — which the sidecar's self-signed leaf does
(`pathlen:0`). OpenSSL is willing to trust a directly-supplied self-signed
root; it does not need a *chain*, only a root it was told to trust.

**Hostname verification does not survive the same test.** The certificate's
CN is the container's own runtime hostname — `db6f02c5e6c2`, not `ss-pdga`,
regenerated on every container recreate — and its `subjectAltName` carries
the container's internal Docker-network IP, never the address any caller
here actually connects with (`XROAD_BIND:<published-port>` from the host,
the Compose service name from inside another container). No caller can name
a hostname the certificate would accept, so `check_hostname = False` stays,
even once the certificate itself is pinned.

## What "pinned" means here, precisely

**Trust-on-first-use, not out-of-band distribution.** `hurl/run-linkup.sh`
captures each server's certificate over the network, from the server it is
about to trust, the moment that server first reports healthy
(`out/xroad-admin-certs/<host>.pem`). This closes "an attacker sits on the
path from here on" — a later connection presenting a *different* certificate
fails, loudly, rather than being silently accepted the way `verify=False`
accepted anything. It does **not** close "an attacker sat on the path during
the capture itself" — the same honesty caveat `scripts/lib-stack.sh`'s
`testca_bundle()` already carries for the Test CA, restated here rather than
re-argued.

## Two mechanisms, not one, because Python and curl differ

`ssl.create_default_context(cafile=..., check_hostname=False)` is sufficient
for every Python caller (`apps/console/xroad.py`'s `AdminSession`,
`scripts/member.sh`'s `refresh`) — chain verification against the pinned
leaf, hostname checking explicitly off.

**curl has no CLI-only equivalent.** `--cacert` without `-k` still enforces
hostname matching; there is no flag that verifies the chain while skipping
the hostname the way `CURLOPT_SSL_VERIFYHOST=0` does from the library API.
Confirmed live: `--cacert <pinned-leaf>` connecting to a name the
certificate does not carry fails exactly like an unpinned connection would.
So `scripts/lib-stack.sh`'s `api_key()`/`api()` use `-k` (skip the
impossible chain+hostname check) plus `--pinnedpubkey sha256//<hash of the
captured certificate's public key>` (add back a real check) — confirmed
live: a matching hash succeeds, a wrong one fails with curl exit 90 ("SSL:
public key does not match pinned public key"). `openssl` computes the hash
(`x509 -pubkey | pkey -pubin -outform der | dgst -sha256`) and is now a
`scripts/preflight.sh` requirement for exactly this reason.

## What stayed unpinned, deliberately

`apps/join-api/job.py`'s Hurl `--insecure` and its `verify=False`
reachability probe (`_default_server_up`) are **not** re-plumbed to pin.
Hurl's own trust configuration is a deploy-critical path this plan
deliberately did not touch (decision 3, security-review-remediation-plan.md).
Instead, `posture: production` refuses to run either without
`join_workflow.hurl_insecure: true` **and** `hurl_insecure` named in
`join_workflow.acknowledge_permissive` — the same two-statement idiom the
plan's other posture switches already use. An unacknowledged production
deployment cannot run the join workflow at all rather than running it
silently unverified.

## Fallback, not refusal, when nothing is captured yet

Every pinning call site falls back to today's `verify=False` behaviour, once,
with a logged warning, when no certificate has been captured for a host —
not deployed through `hurl/run-linkup.sh` yet, or a unit test with no cert
directory at all. This is what keeps docker-local's zero-setup demo path
working unchanged the moment a container first starts, before any deploy has
run.
