# Service brief — the Progressa federation core

*The input document for `prompts/federation-core.md`. Expected output: the
committed `configs/x-road-bus/federation-core.yaml`. Diff yours against it.*

## Who operates the bus

The **Progressa Digital Government Authority (PDGA)** is the Operating
Authority. It runs the Central Server and the management Security Server, and
it is the body that admits members. Its identifiers — instance, member class
code, and PDGA's own code, name and management subsystem — are already frozen
in `manifest.yaml`'s `identity:` block. They are inputs here. Do not generate
replacements for them.

Progressa admits government institutions only. The single member class needs a
description; its code is frozen with the rest.

## The demonstration federation

This is **Linkup**, a demonstration federation on one Docker host. Every
component addresses every other by its container DNS name on a single
network: the Central Server answers as `cs`, the certificate authority as
`ca`, the management Security Server as `ss-pdga`.

## Trust services

A **Test CA** container provides all three trust services: it signs
certificates, answers OCSP on port 8888, and time-stamps on port 8899. The
certificate profile is the Finnish VRK profile the X-Road distribution ships.

The certificate files themselves are not something to configure. The CA
container publishes them on a shared volume and the deployment reads them
from there; a path written down here would be a second copy of something the
deployment already gets right.

## Approval policy

Every auth-certificate and client registration request is to be approved
**explicitly**, over the Central Server admin API, as part of the deployment
run. Progressa will not use the auto-approve flags: those write into
`/etc/xroad/conf.d/local.ini`, and a Central Server that auto-approves
registrations would not be acceptable in production. The demonstration should
run the same sequence a production federation would.

## What is demonstration-only

Three things about this federation would not survive contact with production
and must be recorded as such: the Test CA standing in for a real trust
anchor, the fixed admin credentials baked into the test images, and the fact
that the whole federation runs on a single host.
