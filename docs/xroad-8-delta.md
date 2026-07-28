# X-Road 8 delta note (v0.1 targets X-Road 7.x)

The pack is built on X-Road 7.x — the mature, production-proven line with stable
Docker images, the Test CA tooling and the admin REST APIs this pack automates.
X-Road 8 ("Spaceship") is NIIS's next architecture; this note records what would
change so the pack is not blindsided. All items [confirm: against current NIIS
X-Road 8 documentation at the time of revisiting — the roadmap moves].

What is expected to carry over unchanged: the federation concept (members,
subsystems, access control), the once-only exchange pattern, the four-layer
interoperability model, the OpenAPI service contracts, and therefore the shape of
`configs/` — the pack's declarative YAML stays valid as intent.

What is expected to change: the deployment topology (X-Road 8 moves toward a more
container-native, modular architecture rather than the monolithic Security Server),
messaging/trust internals (including work toward eIDAS-style trust services and
verifiable-credential patterns), and consequently most of `scripts/deploy.sh`
(admin API surfaces will differ) and the compose file (different images/components).

Practical rule: treat `configs/` and `acceptance/` as portable, `scripts/` and
`docker-compose.yml` as 7.x-specific. Revisit when NIIS declares X-Road 8 GA and
the ITU cloud target chooses its version.
