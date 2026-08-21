-- apps/join-api/migrations/001_init.sql -- the Postgres equivalent of
-- store.py's _SCHEMA (SQLite). Applied by store.init() itself (see
-- store.py's _pg_init), inside pg_advisory_xact_lock('kp2-migrate') --
-- no Alembic, no migration framework. Idempotency and version bookkeeping
-- (the schema_version INSERT) are store.py's job, not this file's: this
-- file is pure table-creation DDL, executed once ever by Python, which
-- records the version afterwards. Do NOT add an INSERT INTO schema_version
-- here -- it would double up with store.py's own bookkeeping insert.
--
-- schema_version here is one-row-per-migration (version + applied_at),
-- unlike SQLite's single always-one-row table -- Postgres already has a
-- migration file per version, so tracking "which files have I run" is the
-- natural fit and needs no UPDATE statement later. Pick one, be consistent:
-- this is it.
--
-- GRANTs are deliberately NOT in this file -- see grants.sql. They used to
-- be, wrapped in a pg_roles-existence check so a run before provisioning
-- created joinapi/joinapi_ro wouldn't hard-fail. But that check living
-- inside a once-ever, schema_version-gated migration meant the skip was
-- permanent: if 001_init.sql ran before those roles existed, schema_version
-- recorded version 1 as applied, and no later store.init() call would ever
-- re-attempt the GRANTs -- even though request_events' only append-only
-- enforcement (no UPDATE/DELETE granted to joinapi) depends on them having
-- actually run. grants.sql runs unconditionally on every store.init() call
-- instead, so provisioning creating the roles later gets picked up the next
-- time the process (re)starts.
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
