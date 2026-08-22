#!/usr/bin/env bash
# Report on, and retire, members discovered from configs/member-*/ (see
# hurl/generate.py's discover_members). No `add`: a member joins by running
# prompts/member.md and committing what it produces, not by this script
# writing config by hand -- that is the pack's teaching claim.
set -euo pipefail
. "$(dirname "$0")/lib-core.sh"

usage() {
  cat <<'USAGE'
Usage: scripts/member.sh list
       scripts/member.sh remove <key>
       scripts/member.sh drift <key>
       scripts/member.sh refresh <key>

  list          Print the deployed member set (key, origin, server, ports),
                read from hurl/topology.json.
  remove <key>  Delete configs/member-<key>/ and manifest.yaml's
                identity.members.<key> entry, then regenerate. Does NOT
                touch onboarding/<key>/ -- that record survives as evidence
                of what the operator revoked; it is written by
                DELETE /members/{key}'s federation-side retirement, not by
                this command. Refuses on a canonical member. Does not touch
                a running federation -- the member stays registered there
                until scripts/teardown.sh --purge.
  drift <key>   Re-fetch a joined member's current OpenAPI spec and diff its
                endpoint set against the baseline captured at join time.
                No auth, no HTTP to the join API --
                works whether or not it is even running. Fails clearly if
                '<key>' has no ACTIVE join-store record to compare
                against (never joined through the API, or joined before this
                feature existed). Reports drift since JOIN and, once
                'refresh' has run, drift since the last refresh -- the
                second is what a remediated member's warning clears from.
  refresh <key> Make the federation re-read the member's published OpenAPI
                specs (X-Road reloads a service description only on an
                explicit refresh), then record the act on the member's join
                record. This one DOES authenticate to a Security Server's
                admin API and DOES mutate federation state -- deliberately a
                separate subcommand rather than a flag on 'drift', which
                must stay something you can run against a federation that is
                not even up. It refuses if the spec as served now declares
                operations outside join.allowed_methods: a refresh publishes
                the current contract, it does not approve it.

There is no "add": run prompts/member.md against an agency brief instead.
USAGE
}

cmd_list() {
  local topo="$PACK_DIR/hurl/topology.json"
  [ -f "$topo" ] || fail "$topo not found -- run python3 hurl/generate.py first"
  local bind; bind=$(yq_get "$PACK_DIR/deployment.yaml" network.bind)
  python3 - "$topo" "$bind" <<'PY'
import json, sys
topo = json.load(open(sys.argv[1]))
bind = sys.argv[2]
ports = {s["host"]: (s["host_ui_port"], s["host_proxy_port"]) for s in topo["security_servers"]}
print(f"{'KEY':<8} {'ORIGIN':<10} {'SERVER':<10} UI              REST")
for sub in sorted(topo["subsystems"], key=lambda s: s["member_code"]):
    key = sub["member_code"].lower()
    host = sub["hosted_on"]
    ui, rest = ports.get(host, ("?", "?"))
    print(f"{key:<8} {sub['origin']:<10} {host:<10} {bind}:{ui:<6} {bind}:{rest}")
PY
}

cmd_remove() {
  local key=${1:?"remove needs a member key -- see: scripts/member.sh"}
  local dir="$PACK_DIR/configs/member-$key"
  [ -d "$dir" ] || fail "no configs/member-$key/ -- nothing to remove"

  local origin
  origin=$(python3 - "$PACK_DIR/manifest.yaml" "$key" <<'PY'
import sys, yaml
m = yaml.safe_load(open(sys.argv[1]))['identity']['members'].get(sys.argv[2]) or {}
print(m.get('origin', 'canonical'))
PY
)
  [ "$origin" = "canonical" ] && fail "'$key' is a canonical member -- the canonical four never renumber or leave. Only a joined member can be removed."

  rm -r "$dir"
  # The onboarding record is NOT deleted here. It is evidence of two things,
  # not one: that this member passed its gates, AND what the operator
  # revoked -- a deleted folder has nothing left to be evidence of either
  # way. This function is config removal, not retirement: retirement is the
  # federation-side reversal (apps/join-api/job.py's unjoin(), driven by
  # DELETE /members/{key}), which already ran before this script is ever
  # invoked and is what writes onboarding/<key>/99-retirement.md. A member
  # removed only by this command (never through the join API) has had no
  # federation-side retirement, so no retirement record is the truthful
  # outcome, and this script does not fabricate one.

  python3 - "$key" "$PACK_DIR/manifest.yaml" <<'PY'
import sys, pathlib
key, path = sys.argv[1], pathlib.Path(sys.argv[2])
lines = path.read_text().splitlines(keepends=True)
target = f"    {key}:\n"
out, i, removed = [], 0, False
while i < len(lines):
    if lines[i] == target:
        i += 1
        while i < len(lines) and (lines[i].strip() == "" or len(lines[i]) - len(lines[i].lstrip(" ")) > 4):
            i += 1
        removed = True
        continue
    out.append(lines[i])
    i += 1
if not removed:
    sys.exit(f"member.sh: identity.members.{key} not found in manifest.yaml")
path.write_text("".join(out))
PY

  ( cd "$PACK_DIR" && python3 hurl/generate.py >/dev/null )
  log "removed configs/member-$key/ and manifest.yaml identity.members.$key. Regenerated."
  log "the live federation (if one is running) still holds '$key' until: scripts/teardown.sh --purge"
}

cmd_drift() {
  local key=${1:?"drift needs a member key -- see: scripts/member.sh"}
  local dir="$PACK_DIR/configs/member-$key"
  [ -d "$dir" ] || fail "no configs/member-$key/ -- nothing to check"

  # The join-time baseline lives in the join store -- the most recently
  # submitted ACTIVE record whose payload.code matches this key
  # (case-insensitively) -- nothing enforces there is only ever one, so pick
  # the newest on ambiguity rather than assume. A member added by hand via
  # prompts/member.md, or one whose join predates this feature, has no such
  # record: fail clearly here, the same house style cmd_remove uses for its
  # canonical-member refusal, rather than crash further down.
  #
  # Backend dispatch (Task 5): deployment.yaml's datastore.kind decides
  # where that store actually lives. sqlite (the branch below, unchanged
  # from before Postgres existed) opens the file directly, read-only
  # (file:...?mode=ro): no auth, no HTTP to the join API, works whether or
  # not it is even running (plan §1.3) -- but `mode=ro` alone does NOT
  # actually deliver that promise for a WAL database, see the connect below.
  # postgres has no local file to open, so that
  # branch shells out to store.py's own CLI instead (`python -m store
  # dump-records`, in a throwaway join-api container -- `run --rm`, not
  # `exec`, so this too works whether or not the join-api container is up,
  # the same no-API-dependency promise the sqlite branch already makes) and
  # reimplements the identical filter (state == ACTIVE, payload.code
  # matches case-insensitively, newest submitted_at wins) over the JSONL
  # stream instead of SQL.
  local datastore_kind
  datastore_kind=$(yq_get "$PACK_DIR/deployment.yaml" datastore.kind 2>/dev/null || echo sqlite)

  local record_json
  if [ "$datastore_kind" = "postgres" ]; then
    # This needs Docker on whatever runs member.sh -- not guaranteed: the
    # join-api container itself has no Docker socket (Dockerfile's design
    # decision 8), so a `member.sh drift` run from inside it (which
    # NETWORK_HINT below, in cmd_refresh, documents as sometimes necessary
    # for the admin-API leg of `refresh`) would otherwise die on a raw
    # "command not found" instead of this file's usual clear message.
    command -v docker >/dev/null 2>&1 || fail "docker not found -- drift's Postgres path needs 'docker compose run' to reach the join store. Run this from wherever Docker is available (a droplet's own host shell, a laptop), not from inside the join-api container itself -- it has no Docker socket."

    # A temp file, not a pipe: `python3 -` already uses stdin to receive
    # ITS OWN script text (the heredoc below) -- piping dump-records'
    # output into that same stdin would starve the script of its source
    # before `for line in sys.stdin` ever ran (confirmed live: the pipe
    # variant exits 0 with an empty result and a stray "broken pipe" on
    # docker's side, silently -- not a crash, just wrong). A named file
    # sidesteps the conflict.
    local dump_file
    dump_file=$(mktemp)
    trap 'rm -f "$dump_file"' EXIT
    docker compose -f "$PACK_DIR/docker-compose.yml" run --rm -T join-api \
      python -m store dump-records > "$dump_file" \
      || fail "drift: 'docker compose run join-api python -m store dump-records' failed -- could not read the join store"
    record_json=$(python3 - "$dump_file" "$key" <<'PY'
import json, sys

dump_path, key = sys.argv[1], sys.argv[2]
best = None
with open(dump_path) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("state") != "ACTIVE":
            continue
        if (rec.get("payload") or {}).get("code", "").lower() != key.lower():
            continue
        if best is None or rec.get("submitted_at", "") > best.get("submitted_at", ""):
            best = rec
print(json.dumps(best) if best else "")
PY
)
    rm -f "$dump_file"
    trap - EXIT
  else
    local db="$PACK_DIR/out/join-store/join-store.sqlite3"
    [ -f "$db" ] || fail "no join store at $db -- join-api has not run yet (it creates the schema at startup), or state has not been migrated (scripts/migrate-join-store.py)."
    record_json=$(python3 - "$db" "$key" <<'PY'
import pathlib, sqlite3, sys

db_path, key = sys.argv[1], sys.argv[2]
# `mode=ro` on its own cannot open a WAL database once SQLite has removed
# -wal/-shm on the last writer's clean close: it needs the -shm index to
# read WAL and will not create one through a read-only handle, so a
# perfectly healthy store fails with a bare "unable to open database file"
# -- which is precisely the "works whether or not join-api is running"
# promise above, unkept. immutable=1 is the documented read path for a
# database nothing is writing, and an absent -wal is that proof; a -wal that
# IS present holds content immutable=1 would skip, so re-raise there rather
# than hand drift a stale baseline. Same guard as apps/join-api/store.py's
# _sqlite_connect() and scripts/acceptance.sh's 2.7 queries. The SELECT 1 is
# load-bearing: sqlite3.connect() is lazy, so the failure surfaces on the
# first statement and a try: around connect() alone would catch nothing.
try:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.execute("SELECT 1")
except sqlite3.OperationalError:
    if pathlib.Path(f"{db_path}-wal").exists():
        raise
    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
row = conn.execute(
    "SELECT record FROM requests WHERE member_key = ? AND state = 'ACTIVE' "
    "ORDER BY submitted_at DESC LIMIT 1",
    (key.lower(),),
).fetchone()
print(row[0] if row else "")
PY
)
  fi
  [ -n "$record_json" ] || fail "no ACTIVE join-store record for '$key' -- either it was never joined through the join API (e.g. added by hand via prompts/member.md) or its join predates this feature. drift has no join-time baseline to compare against."

  python3 - "$dir" "$key" "$record_json" <<'PY'
import glob, json, os, ssl, sys, urllib.request

import yaml

member_dir, key, record_json = sys.argv[1], sys.argv[2], sys.argv[3]

# The published spec_url is https once the mock backends serve TLS
# (docs/production-delta.md row 18), and this image's own trust store has no
# Test CA in it. KP2_XROAD_CA_BUNDLE names the mounted public certificate,
# exactly as apps/join-api/job.py's r1 call and apps/console/xroad.py's
# exchange already do -- one variable, one meaning, four callers.
#
# ADDITIVE, never a replacement: a joined member on a real host with a real
# certificate must still verify against the public roots, which is why this
# is not SSL_CERT_FILE (that variable REPLACES the default store, and would
# trade this bug for the mirror-image one). And never an unverified context:
# spec_url is applicant-controlled, so an unauthenticated fetch would let
# whoever holds the wire decide what this command reports as the member's
# current contract.
def _spec_ssl_context():
    ctx = ssl.create_default_context()
    bundle = os.environ.get("KP2_XROAD_CA_BUNDLE")
    if bundle:
        ctx.load_verify_locations(cafile=bundle)
    return ctx

record = json.loads(record_json)
baseline = record.get("endpoint_baseline") or {}
# The join-time baseline is never re-derived -- it is evidence of the contract
# this member was ADMITTED on. `refresh` amends the record instead, so there
# are two questions to answer, not one: has the contract moved since join
# (always true once it has moved, and it should stay true), and has it moved
# since the operator last remediated. The second is the one that clears.
refreshes = record.get("refreshes") or []
last_refresh = refreshes[-1] if refreshes else None
refresh_baseline = (last_refresh or {}).get("endpoints") or {}

yaml_files = sorted(glob.glob(os.path.join(member_dir, "*.yaml")))
if not yaml_files:
    sys.exit(f"member.sh drift: no config found under {member_dir}")
cfg = yaml.safe_load(open(yaml_files[0])) or {}
services = cfg.get("services") or []
if not services:
    print(f"{key}: publishes no services -- nothing to diff")
    sys.exit(0)

any_drift = False
for svc in services:
    code, spec_url = svc["code"], svc["spec_url"]
    base_paths = set(baseline.get(code, []))
    try:
        with urllib.request.urlopen(spec_url, timeout=10, context=_spec_ssl_context()) as resp:
            spec_doc = yaml.safe_load(resp.read())
        current_paths = set((spec_doc or {}).get("paths", {}).keys())
    except Exception as exc:
        any_drift = True
        print(f"{code}: could not fetch current spec at {spec_url}: {exc}")
        print(f"  (a docker-internal demo hostname like this one is only reachable "
              f"from inside the linkup network -- run this from a container on it, "
              f"e.g. docker compose exec join-api, if that is why this failed)")
        continue
    if code not in baseline:
        any_drift = True
        print(f"{code}: no join-time baseline for this service (published after join?) "
              f"-- current endpoints: {sorted(current_paths)}")
        continue

    def _diff(reference):
        return sorted(current_paths - reference), sorted(reference - current_paths)

    added, removed = _diff(base_paths)
    if not added and not removed:
        print(f"{code}: no drift ({len(current_paths)} endpoint(s), unchanged since join)")
        continue

    # Drifted from join. Whether that is an OPEN problem depends on what the
    # operator did about it: a recorded refresh is the federation having been
    # made to publish this contract, so the actionable diff is against that.
    print(f"{code}: DRIFT since join")
    for p in added:
        print(f"  + {p}")
    for p in removed:
        print(f"  - {p}")
    if code not in refresh_baseline:
        any_drift = True
        if last_refresh:
            print(f"  (the last refresh, {last_refresh['at']}, did not cover this service)")
        else:
            print(f"  the federation still publishes the join-time contract -- "
                  f"remedy with: scripts/member.sh refresh {key}")
        continue
    r_added, r_removed = _diff(set(refresh_baseline[code]))
    if not r_added and not r_removed:
        print(f"  clean since the last refresh ({last_refresh['at']}) -- the federation "
              f"publishes what this spec serves today")
        continue
    any_drift = True
    print(f"  and DRIFT since the last refresh ({last_refresh['at']}):")
    for p in r_added:
        print(f"    + {p}")
    for p in r_removed:
        print(f"    - {p}")
    print(f"  remedy with: scripts/member.sh refresh {key}")

sys.exit(1 if any_drift else 0)
PY
}

cmd_refresh() {
  local key=${1:?"refresh needs a member key -- see: scripts/member.sh"}
  local dir="$PACK_DIR/configs/member-$key"
  [ -d "$dir" ] || fail "no configs/member-$key/ -- nothing to refresh"

  # lib-core.sh only, like every other subcommand here -- deliberately NOT
  # lib-stack.sh. Its api_key/api helpers are curl, and this command has to
  # run from inside the linkup network (see the address note below), where
  # there is no curl: the join-api image is python:3.12-slim. cmd_drift
  # already answers exactly this with urllib, and so does this. Admin
  # credentials (and, for recording the act, the operator token -- plan
  # §1.3) come from the environment, which is where that container already
  # has them; a host-side run falls back to .env. One sourcing block,
  # triggered if EITHER is still unset -- but `.` re-executes every
  # assignment in .env unconditionally once it runs, so the gate alone only
  # decides WHETHER .env is read, not which variables survive it. Save
  # whichever of the two was already set, source, then restore it --
  # otherwise an operator's deliberately-exported XROAD_ADMIN_PASSWORD gets
  # silently overwritten by .env's value the moment KP2_JOIN_OPERATOR_TOKEN
  # alone was the one missing.
  if [ -z "${XROAD_ADMIN_PASSWORD:-}" ] || [ -z "${KP2_JOIN_OPERATOR_TOKEN:-}" ]; then
    if [ -f "$PACK_DIR/.env" ]; then
      _prior_admin_password="${XROAD_ADMIN_PASSWORD:-}"
      _prior_operator_token="${KP2_JOIN_OPERATOR_TOKEN:-}"
      set -a; . "$PACK_DIR/.env"; set +a
      [ -n "$_prior_admin_password" ] && XROAD_ADMIN_PASSWORD="$_prior_admin_password"
      [ -n "$_prior_operator_token" ] && KP2_JOIN_OPERATOR_TOKEN="$_prior_operator_token"
      unset _prior_admin_password _prior_operator_token
    fi
  fi
  [ -n "${XROAD_ADMIN_PASSWORD:-}" ] || fail "XROAD_ADMIN_PASSWORD is unset and $PACK_DIR/.env has none -- refresh authenticates to a Security Server's admin API."
  [ -n "${KP2_JOIN_OPERATOR_TOKEN:-}" ] || fail "KP2_JOIN_OPERATOR_TOKEN is unset and $PACK_DIR/.env has none -- refresh records the act through the join API (or the join store directly)."

  local topo="$PACK_DIR/hurl/topology.json"
  [ -f "$topo" ] || fail "$topo not found -- run python3 hurl/generate.py first"

  # Backend dispatch (Task 5): only the direct-write fallback below (the
  # `else` branch of `if api_up:`, used when join-api is not answering)
  # needs to know this -- the API-first path above it only ever talks to
  # join-api's own HTTP endpoint, which is identical regardless of what
  # backend sits behind it. Read once here, passed into the same Python
  # block everything else already runs in.
  local datastore_kind
  datastore_kind=$(yq_get "$PACK_DIR/deployment.yaml" datastore.kind 2>/dev/null || echo sqlite)

  python3 - "$topo" "$key" "$PACK_DIR/configs/x-road-bus/join-policy.yaml" \
           "$PACK_DIR/out/join-store/join-store.sqlite3" \
           "${XROAD_ADMIN_USER:-xrd}" "$XROAD_ADMIN_PASSWORD" "$KP2_JOIN_OPERATOR_TOKEN" \
           "$datastore_kind" "$PACK_DIR/docker-compose.yml" <<'PY'
import datetime, http.cookiejar, json, os, shutil, ssl, sqlite3, subprocess, sys, urllib.parse, urllib.request

import yaml

topo_path, key, policy_path, db_path, admin_user, admin_password, operator_token, \
    datastore_kind, compose_file = sys.argv[1:10]

# -- resolve ------------------------------------------------------------------
# The IN-NETWORK address (host:ui_port), never the host-mapped one. This
# command has to run from a container on the linkup network anyway -- the
# member's spec_url is a docker-internal hostname and the allowed-methods
# check below fetches it -- and from there 127.0.0.1:<host_ui_port> is
# nothing at all (confirmed live: connection refused). One address, one
# runtime, and it is the same one cmd_drift already documents.
topo = json.load(open(topo_path))
sub = next((s for s in topo["subsystems"] if s["member_code"].lower() == key.lower()), None)
if sub is None:
    sys.exit(f"member.sh refresh: '{key}' is not in hurl/topology.json -- regenerate, or check the key")
host = sub["hosted_on"]
ui = next((s["ui_port"] for s in topo["security_servers"] if s["host"] == host), None)
if ui is None:
    sys.exit(f"member.sh refresh: {key}'s Security Server '{host}' is not in hurl/topology.json")
client_id = sub["id"]

NETWORK_HINT = ("  (this has to run from a container on the linkup network -- "
                "docker compose exec join-api -- the same place scripts/member.sh drift runs)")

# The published spec_url is https once the mock backends serve TLS
# (docs/production-delta.md row 18), and this image's own trust store has no
# Test CA in it. KP2_XROAD_CA_BUNDLE names the mounted public certificate,
# exactly as apps/join-api/job.py's r1 call and apps/console/xroad.py's
# exchange already do -- one variable, one meaning, four callers.
#
# ADDITIVE, never a replacement: a joined member on a real host with a real
# certificate must still verify against the public roots, which is why this
# is not SSL_CERT_FILE (that variable REPLACES the default store, and would
# trade this bug for the mirror-image one). And never an unverified context:
# spec_url is applicant-controlled, so an unauthenticated fetch would let
# whoever holds the wire decide what this command reports as the member's
# current contract.
def _spec_ssl_context():
    ctx = ssl.create_default_context()
    bundle = os.environ.get("KP2_XROAD_CA_BUNDLE")
    if bundle:
        ctx.load_verify_locations(cafile=bundle)
    return ctx


# -- session ------------------------------------------------------------------
# X-Road's admin API authenticates by SESSION LOGIN, not an API key: POST
# /login with form params, keep the JSESSIONID cookie, and send the
# XSRF-TOKEN cookie's value back as an X-XSRF-TOKEN header on every call
# (docs/decisions/xroad-770-notes.md section 1). Certificate verification is
# off for the same reason run-linkup.sh passes --insecure: the Test CA's
# certificates are self-signed.
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=ctx), urllib.request.HTTPCookieProcessor(jar)
)
base = f"https://{host}:{ui}"
try:
    opener.open(base + "/login",
                data=urllib.parse.urlencode({"username": admin_user, "password": admin_password}).encode(),
                timeout=15)
except Exception as exc:
    sys.exit(f"member.sh refresh: could not log in to {host}'s admin API at {base}: {exc}\n" + NETWORK_HINT)
xsrf = next((c.value for c in jar if c.name == "XSRF-TOKEN"), None)
if xsrf is None:
    sys.exit(f"member.sh refresh: {host} accepted the login but set no XSRF-TOKEN cookie")


def api(method, path, body=None):
    req = urllib.request.Request(
        base + "/api/v1" + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-XSRF-TOKEN": xsrf, "Content-Type": "application/json"},
    )
    try:
        with opener.open(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        # X-Road's admin API says WHY in the body ({"status":409,"error":
        # {"code":"..."}}); urllib's str(HTTPError) is just the status line,
        # so a bare raise here would report "HTTP Error 500:" and nothing
        # else. Found exactly that way.
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {exc.read().decode(errors='replace')[:400]}") from None
    return json.loads(raw) if raw else None


try:
    descriptions = api("GET", f"/clients/{urllib.parse.quote(client_id, safe='')}/service-descriptions")
except Exception as exc:
    sys.exit(f"member.sh refresh: could not read {client_id}'s service descriptions from {host}: {exc}")
if not descriptions:
    print(f"{key}: no service descriptions published on {host} -- nothing to refresh")
    sys.exit(0)

# -- the governance check, BEFORE anything is published ------------------------
# The same allowed-methods rule apps/join-api/validate.py applies at join
# time, re-run against the spec as it is served NOW. A refresh makes the
# federation PUBLISH the current contract; it does not make the current
# contract APPROVED. Refusing here is what keeps this a governance tool
# rather than a convenience that launders an unreviewed write endpoint onto
# the bus.
allowed = {m.upper() for m in ((yaml.safe_load(open(policy_path)) or {}).get("join") or {}).get("allowed_methods", [])}
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}

served, refused = {}, []
for desc in descriptions:
    url = desc.get("url")
    try:
        with urllib.request.urlopen(url, timeout=15, context=_spec_ssl_context()) as resp:
            spec = yaml.safe_load(resp.read()) or {}
    except Exception as exc:
        sys.exit(f"member.sh refresh: could not fetch the published spec at {url}: {exc}\n" + NETWORK_HINT)
    paths = spec.get("paths") or {}
    served[desc["id"]] = (url, sorted(paths))
    for path, operations in paths.items():
        for method in operations or {}:
            if method.lower() in HTTP_METHODS and method.upper() not in allowed:
                refused.append(f"{method.upper()} {path}  (service description {desc['id']}, {url})")

if refused:
    sys.exit(
        "member.sh refresh: REFUSED -- the spec as served now declares operations outside "
        f"join.allowed_methods {sorted(allowed)}:\n  " + "\n  ".join(refused) +
        "\n\nA refresh publishes the current contract; it does not approve it. This member's "
        "contract has moved beyond what it was admitted on, which is a re-admission decision "
        "(an operator review, then a new join) -- not a refresh."
    )

# -- refresh -------------------------------------------------------------------
for desc in descriptions:
    try:
        # The body is not optional: this endpoint answers 415 without one and
        # 500 with an empty one (both confirmed live against 7.7.0). It is
        # the same ignore_warnings the pack's own Hurl templates already
        # pass on every admin-API write that offers it.
        api("PUT", f"/service-descriptions/{desc['id']}/refresh", {"ignore_warnings": True})
    except Exception as exc:
        sys.exit(f"member.sh refresh: refresh of service description {desc['id']} on {host} failed: {exc}")
    print(f"refreshed service description {desc['id']} ({served[desc['id']][0]})")

# -- record the act ------------------------------------------------------------
# An AMENDMENT. endpoint_baseline is never touched: it is evidence of the
# contract this member was ADMITTED on, and refreshing the federation does
# not re-admit anybody. cmd_drift then reports both facts -- drift since
# join, and drift since this refresh -- instead of a warning that can never
# clear.
#
# API-first, direct-write fallback (plan §1.3): join-api is the sole writer
# to the join store while it is running; a direct write here is safe
# precisely when it is not, because then it's the only writer. This
# container already runs on the `linkup` network for the admin-API calls
# above (see NETWORK_HINT), so join-api is reachable at its Compose service
# name.
endpoints = {}
for desc in descriptions:
    _url, paths = served[desc["id"]]
    for svc in desc.get("services") or []:
        code = svc.get("service_code") or svc.get("id")
        if code:
            endpoints[code] = paths

no_record_msg = (f"{key}: refreshed, but there is no ACTIVE join-store record to amend -- "
                  f"drift has no per-refresh baseline to compare against.")

join_api = "http://join-api:8000"
headers = {"Authorization": f"Bearer {operator_token}", "X-KP2-Console": "1"}
try:
    urllib.request.urlopen(join_api + "/health", timeout=3)
    api_up = True
except urllib.error.HTTPError:
    # An answered HTTP request, even a non-2xx one, proves the process is up
    # and listening -- only a connection-level failure below means "down."
    api_up = True
except urllib.error.URLError as exc:
    # A genuine socket timeout is NOT proof of "down" -- it is proof of
    # "could not tell in 3s," and a slow-but-actually-running join-api is
    # still the sole writer. Confirmed live (this Python): urlopen wraps a
    # connect-phase timeout as URLError(reason=TimeoutError(...)), never a
    # bare TimeoutError. Resolve the ambiguity toward the safe answer, same
    # principle as the HTTPError case above -- refuse the direct-write path
    # rather than risk two writers.
    if isinstance(exc.reason, TimeoutError):
        api_up = True
        print(f"member.sh refresh: {join_api}/health did not answer within 3s -- treating "
              f"join-api as up (ambiguous, not definitely down) and refusing the direct-write "
              f"fallback", file=sys.stderr)
    else:
        api_up = False
except Exception:
    api_up = False

if api_up:
    req = urllib.request.Request(join_api + "/requests", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            requests_list = json.loads(resp.read())["requests"]
    except Exception as exc:
        sys.exit(f"member.sh refresh: could not read {join_api}/requests: {exc}")
    best = None
    for rec in requests_list:
        if rec.get("state") != "ACTIVE":
            continue
        if (rec.get("payload") or {}).get("code", "").lower() != key.lower():
            continue
        if best is None or rec.get("submitted_at", "") > best.get("submitted_at", ""):
            best = rec
    if best is None:
        print(no_record_msg, file=sys.stderr)
        sys.exit(0)
    body = json.dumps({"endpoints": endpoints}).encode()
    req = urllib.request.Request(
        f"{join_api}/requests/{best['id']}/refreshes", method="POST", data=body,
        headers={**headers, "Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as exc:
        sys.exit(f"member.sh refresh: POST {join_api}/requests/{best['id']}/refreshes failed: "
                  f"HTTP {exc.code} {exc.read().decode(errors='replace')[:400]}")
    print(f"recorded the refresh via the join API ({len(endpoints)} service(s)); "
          f"endpoint_baseline untouched")
elif datastore_kind == "postgres":
    # Backend dispatch (Task 5): no local sqlite file to open against
    # Postgres, so the same read-modify-write shape the sqlite branch below
    # does over one connection is reimplemented as two `docker compose run
    # --rm join-api python -m store ...` calls -- dump-records to find the
    # ACTIVE record (same filter cmd_drift's Postgres branch uses: state ==
    # ACTIVE, payload.code matches case-insensitively, newest submitted_at
    # wins), then amend-refresh with the shallow-merged patch. `run --rm`,
    # not `exec`, uniformly with cmd_drift's Postgres branch and
    # scripts/join-store-export.sh/-import.sh -- works whether or not the
    # join-api container is up.
    #
    # NOT atomic the way the sqlite branch's single connection is: a second
    # concurrent `member.sh refresh` invocation during the gap between the
    # dump-records call and the amend-refresh call could race and clobber
    # each other's refreshes entry. This mirrors the plan's own framing of
    # the fallback as safe "precisely when [join-api] is not running,
    # because then it's the only writer" -- the sqlite branch relies on the
    # exact same operational assumption (no other process writes while the
    # API is down), it just never had to name the race because one
    # connection made it invisible there. No locking added for this --
    # out of scope.
    #
    # Guard against the invocation context NETWORK_HINT above documents as
    # sometimes necessary (running this from inside the join-api container,
    # for the admin-API login's sake) -- that container has no Docker
    # socket (Dockerfile's design decision 8), so without this check a
    # missing `docker` binary would surface as a raw FileNotFoundError
    # traceback instead of this file's usual clear message.
    if shutil.which("docker") is None:
        sys.exit("member.sh refresh: docker not found -- the direct-write fallback's "
                  "Postgres path needs 'docker compose run' to reach the join store. Run "
                  "this from wherever Docker is available (a droplet's own host shell, a "
                  "laptop), not from inside the join-api container itself -- it has no "
                  "Docker socket.")
    dump = subprocess.run(
        ["docker", "compose", "-f", compose_file, "run", "--rm", "-T", "join-api",
         "python", "-m", "store", "dump-records"],
        capture_output=True, text=True,
    )
    if dump.returncode != 0:
        sys.exit(f"member.sh refresh: could not read the join store (dump-records): {dump.stderr}")
    best_id, best = None, None
    for line in dump.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("state") != "ACTIVE":
            continue
        if (rec.get("payload") or {}).get("code", "").lower() != key.lower():
            continue
        if best is None or rec.get("submitted_at", "") > best.get("submitted_at", ""):
            best_id, best = rec["id"], rec
    if best is None:
        print(no_record_msg, file=sys.stderr)
        sys.exit(0)
    best.setdefault("refreshes", []).append({
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "endpoints": endpoints,
    })
    patch = json.dumps({"refreshes": best["refreshes"]})
    amend = subprocess.run(
        ["docker", "compose", "-f", compose_file, "run", "--rm", "-T", "join-api",
         "python", "-m", "store", "amend-refresh", best_id, patch],
        capture_output=True, text=True,
    )
    if amend.returncode != 0:
        sys.exit(f"member.sh refresh: amend-refresh failed for {best_id}: {amend.stderr}")
    print(f"recorded the refresh directly -- join-api is not running "
          f"({len(endpoints)} service(s)); endpoint_baseline untouched")
else:
    if not os.path.exists(db_path):
        print(no_record_msg, file=sys.stderr)
        sys.exit(0)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT id, record FROM requests WHERE member_key = ? AND state = 'ACTIVE' "
        "ORDER BY submitted_at DESC LIMIT 1",
        (key.lower(),),
    ).fetchone()
    if row is None:
        print(no_record_msg, file=sys.stderr)
        sys.exit(0)
    best_id, best = row[0], json.loads(row[1])
    best.setdefault("refreshes", []).append({
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "endpoints": endpoints,
    })
    conn.execute("UPDATE requests SET record = ? WHERE id = ?", (json.dumps(best), best_id))
    # Audit trail parity with store.save_request's own writes (plan §1.5) --
    # append-only table, no triggers to worry about since this only inserts.
    conn.execute(
        "INSERT INTO request_events (request_id, at, actor, event) VALUES (?, ?, 'operator', 'refresh:direct-write')",
        (best_id, datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    conn.commit()
    print(f"recorded the refresh directly -- join-api is not running "
          f"({len(endpoints)} service(s)); endpoint_baseline untouched")
PY

  log "$key: the federation now publishes the contract these specs serve today."
  log "  What this did NOT do: approve it. allowed_methods is the only policy re-applied;"
  log "  a changed field set, lawful basis or SLA is an operator review this cannot stand in for."
}


case "${1:-}" in
  list)    cmd_list ;;
  remove)  shift; cmd_remove "$@" ;;
  drift)   shift; cmd_drift "$@" ;;
  refresh) shift; cmd_refresh "$@" ;;
  *)       usage; exit 1 ;;
esac
