-- apps/join-api/migrations/grants.sql -- request_events' append-only
-- enforcement (no UPDATE/DELETE granted to joinapi -- see 001_init.sql's
-- comment on why GRANT, not a trigger) plus joinapi_ro's read-only access.
-- Also grants SELECT on schema_version itself: store.py's _pg_init() reads
-- it unconditionally on every store.init() call (to decide which
-- migrations are pending), including startup under the joinapi role's own
-- DSN in the normal deployment shape -- schema bootstrapped once by an
-- owner/admin role, joinapi never owning the tables (the whole point of
-- these restrictive grants). Without this line every process restart after
-- the first one hard-fails at startup with
-- psycopg.errors.InsufficientPrivilege: permission denied for table
-- schema_version -- missed originally because it doesn't show up if
-- joinapi happens to be the first role to ever call store.init() (it then
-- becomes table owner via CREATE TABLE, which silently defeats the whole
-- append-only guarantee this file exists for).
--
-- NOT a versioned migration: unlike 001_init.sql, this file has no numeric
-- prefix, is never recorded in schema_version, and store.py's _pg_init()
-- runs it on EVERY store.init() call, not gated by "have I applied version
-- N yet". Table creation only needs to happen once ever, but these GRANTs
-- need to be retried on every startup until they succeed: role creation
-- (CREATE ROLE joinapi / joinapi_ro) is a provisioning-time concern
-- (Terraform/doctl, a later task) that can easily happen AFTER the first
-- store.init() call (e.g. a local throwaway Postgres used only for pytest,
-- or a cluster where the app container starts before provisioning
-- finishes). If these GRANTs were gated by schema_version the way table
-- creation is, a run before the roles existed would permanently record
-- "done" and never retry -- the one enforcement this migration provides
-- would then just never happen. Every statement here is safely re-runnable
-- (GRANT/ALTER ROLE are idempotent; re-granting an already-granted
-- privilege is a no-op), and the pg_roles check means a still-missing role
-- is a NOTICE, not a hard failure, so a normal run where the roles already
-- exist costs four cheap no-op statements every startup.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'joinapi') THEN
    GRANT SELECT, INSERT ON request_events TO joinapi;
    -- no UPDATE, no DELETE, to anyone but the cluster's admin role.
    GRANT SELECT, INSERT, UPDATE ON requests, tokens TO joinapi;  -- no DELETE, no DDL
    GRANT SELECT ON schema_version TO joinapi;  -- _pg_init() reads this every store.init() call
    ALTER ROLE joinapi SET statement_timeout = '10s';
  ELSE
    RAISE NOTICE 'role "joinapi" does not exist yet -- skipping its GRANTs (provisioning creates it; retried next store.init())';
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'joinapi_ro') THEN
    GRANT SELECT ON requests, request_events, tokens, schema_version TO joinapi_ro;
  ELSE
    RAISE NOTICE 'role "joinapi_ro" does not exist yet -- skipping its GRANTs (provisioning creates it; retried next store.init())';
  END IF;
END $$;
