# Acceptance check — module 2.1 (federation core)

**Proves:** the Central Server is initialised with the Progressa instance, trust
services are registered, and global configuration is being generated — the
federation core other modules build on.
**Run by:** `scripts/acceptance.sh` (CS admin REST API)

- **Given** `configs/x-road-bus/federation-core.yaml` is deployed on the local stack
  (`scripts/deploy.sh` has completed);
- **When** the check queries the CS admin API for initialisation status, member
  classes, certification services, timestamping services and the internal
  configuration anchor;
- **Then**
  - instance identifier is `PROGRESSA`; member class `GOV` exists;
  - one certification service (Test CA) with one OCSP responder (`http://ca:8888`)
    and one timestamping service (`http://ca:8899`) are registered;
  - the internal configuration anchor downloads successfully (global conf is
    being generated);
  - owner member `GOV/PDGA` exists with subsystem `MANAGEMENT` set as the
    management-service provider.

Check commands (workstation): `curl -k https://localhost:4000/api/v1/...`
[confirm: exact endpoint paths at P0 against the CS admin API / Hurl reference].

Status: UNVERIFIED until this passes on the live stack (kp-solution-verify).
