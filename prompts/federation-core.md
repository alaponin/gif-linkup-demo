# Generating prompt — module 2.1 (Stand up the federation core)

**Building block(s):** x-road-bus
**Produces:** `configs/x-road-bus/federation-core.yaml`
**Public spec:** NIIS X-Road Central Server configuration (docs.x-road.global; niis.org); EIF Technical layer

## Problem

Before any member can join, the federation core must exist: a Central Server with an
instance identifier and member class, a trust anchor (CA + OCSP + TSA), and a
management Security Server for the CS owner. This prompt generates that core
configuration for the Progressa demonstration federation (Linkup).

## Prompt (copy-paste ready)

```
Below is the NIIS X-Road Central Server configuration sequence (initialisation,
member classes, trust services, management services) and the Progressa service
brief [paste both].

Generate the federation-core configuration for Progressa's demonstration
federation as a single YAML document with these fields:
(1) central_server — address, member_classes (one class for government
    institutions, description only — the code and the owner's identity
    (code/name/management_subsystem) are frozen in manifest.yaml's identity:
    block and are inputs here, not something this prompt generates);
(2) trust_services — the certification service's certificate profile, its OCSP
    responder URL, and the time-stamping service URL. The certificate files
    themselves are not a config field: they come from the CA container's shared
    volume at deploy time (no path to invent or drift out of sync);
(3) policy.management_request_approval — how auth-certificate and client
    registration requests are approved during deployment. Use `explicit` (each
    request approved over the Central Server admin API) rather than the
    auto-approve flags, which write into /etc/xroad/conf.d/local.ini and would
    not be acceptable on a production Central Server;
(4) management_security_server — code, DNS name. (Its owner is central_server.owner
    above — do not repeat it as a second field.)

Rules: every identifier (instance, member class, member codes, server codes) is
[confirm: verify against the live registry] until deployed. Mark every
demonstration-only shortcut (Test CA, fixed admin credentials, single host) in a
demo_only list. Do not invent a field the deployment does not apply: the config
is the deployment, so a block nothing reads is not documentation, it is drift.
Output only the YAML document.
```

## Inputs / outputs

- **Inputs:** the NIIS CS configuration guide (≥7.3) + the Progressa service brief
  (institutions, who operates the bus).
- **Output:** `configs/x-road-bus/federation-core.yaml`, applied by `scripts/deploy.sh`.

## Safeguard

The instance identifier and member class are permanent join keys — every later
config, and the KP3/KP4 packs, reference them. A wrong value here silently breaks
every subsequent registration. Freeze them in `manifest.yaml` and confirm before
deploy; never let the model invent replacement identifiers.
