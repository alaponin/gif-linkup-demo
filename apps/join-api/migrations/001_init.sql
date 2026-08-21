-- apps/join-api/migrations/001_init.sql -- the Postgres equivalent of
-- store.py's _SCHEMA (SQLite). Applied by store.init() itself (see
-- store.py's _pg_migrate), inside pg_advisory_xact_lock('kp2-migrate') --
-- no Alembic, no migration framework. Idempotency and version bookkeeping
-- (the schema_version INSERT) are store.py's job, not this file's: this
-- file is pure DDL/grants, executed once per version by Python, which
-- records the version afterwards. Do NOT add an INSERT INTO schema_version
-- here -- it would double up with store.py's own bookkeeping insert.
--
-- schema_version here is one-row-per-migration (version + applied_at),
-- unlike SQLite's single always-one-row table -- Postgres already has a
-- migration file per version, so tracking "which files have I run" is the
-- natural fit and needs no UPDATE statement later. Pick one, be consistent:
-- this is it.
CREATE TABLE schema_version (
  version    INTEGER PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- record/detail: JSONB (SQLite: TEXT + json.dumps/loads in store.py).
-- submitted_at/at/issued_at/expires_at/revoked_at: TIMESTAMPTZ, stored UTC
-- -- store.py converts back to the same ISO-8601 strings the API always
-- emitted; callers see no difference.
CREATE TABLE requests (
  id           TEXT PRIMARY KEY,
  state        TEXT NOT NULL CHECK (state IN
               ('REJECTED','SUBMITTED','APPROVED','RUNNING','BLOCKED',
                'FAILED','ACTIVE','RETIRING','RETIRED')),
  submitted_at TIMESTAMPTZ NOT NULL,
  submitted_by TEXT,
  member_key   TEXT,
  record       JSONB NOT NULL
);
CREATE INDEX requests_by_member ON requests (member_key, state, submitted_at);
CREATE INDEX requests_by_state  ON requests (state);

-- Append-only, enforced by GRANT (no UPDATE/DELETE to joinapi below) rather
-- than the BEFORE UPDATE/DELETE triggers SQLite uses -- SQLite has no role
-- system to grant against, Postgres does. seq uses GENERATED ALWAYS AS
-- IDENTITY, the modern equivalent of SQLite's INTEGER PRIMARY KEY
-- AUTOINCREMENT.
CREATE TABLE request_events (
  seq        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  request_id TEXT REFERENCES requests(id),
  at         TIMESTAMPTZ NOT NULL,
  actor      TEXT NOT NULL,
  event      TEXT NOT NULL,
  detail     JSONB
);

CREATE TABLE tokens (
  name       TEXT PRIMARY KEY,
  sha256     TEXT NOT NULL,
  issued_at  TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ
);

-- Role creation (CREATE ROLE joinapi / joinapi_ro) is a provisioning-time
-- concern (Terraform/doctl, a later task), not this migration's job. These
-- GRANTs assume the roles already exist; wrapped in a pg_roles check so this
-- migration does not hard-fail against a database where they aren't created
-- yet (e.g. a local throwaway Postgres used only for pytest) -- it emits a
-- NOTICE and skips instead. Whoever runs provisioning afterwards is expected
-- to re-run store.init() (idempotent) or issue the GRANTs by hand once the
-- roles exist.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'joinapi') THEN
    GRANT SELECT, INSERT ON request_events TO joinapi;
    -- no UPDATE, no DELETE, to anyone but the cluster's admin role.
    GRANT SELECT, INSERT, UPDATE ON requests, tokens TO joinapi;  -- no DELETE, no DDL
    ALTER ROLE joinapi SET statement_timeout = '10s';
  ELSE
    RAISE NOTICE 'role "joinapi" does not exist yet -- skipping its GRANTs (provisioning creates it)';
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'joinapi_ro') THEN
    GRANT SELECT ON requests, request_events, tokens TO joinapi_ro;
  ELSE
    RAISE NOTICE 'role "joinapi_ro" does not exist yet -- skipping its GRANTs (provisioning creates it)';
  END IF;
END $$;
