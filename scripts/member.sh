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
                '<key>' has no ACTIVE out/join/*.json record to compare
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

  # The join-time baseline lives in the job context, i.e. the
  # most recently-submitted ACTIVE out/join/*.json record whose payload.code
  # matches this key (case-insensitively) -- nothing enforces there is only
  # ever one, so pick the newest on ambiguity rather than assume. A member
  # added by hand via prompts/member.md, or one whose join predates this
  # feature, has no such record: fail clearly here, the same house style
  # cmd_remove uses for its canonical-member refusal, rather than crash on a
  # missing file further down.
  local record_json
  record_json=$(python3 - "$PACK_DIR/out/join" "$key" <<'PY'
import glob, json, os, sys

join_dir, key = sys.argv[1], sys.argv[2]
best = None
for path in sorted(glob.glob(os.path.join(join_dir, "*.json"))):
    try:
        rec = json.load(open(path))
    except Exception:
        continue
    if rec.get("state") != "ACTIVE":
        continue
    code = (rec.get("payload") or {}).get("code", "")
    if code.lower() != key.lower():
        continue
    if best is None or rec.get("submitted_at", "") > best.get("submitted_at", ""):
        best = rec
print(json.dumps(best) if best is not None else "")
PY
)
  [ -n "$record_json" ] || fail "no ACTIVE out/join/*.json record for '$key' -- either it was never joined through the join API (e.g. added by hand via prompts/member.md) or its join predates this feature. drift has no join-time baseline to compare against."

  python3 - "$dir" "$key" "$record_json" <<'PY'
import glob, json, os, sys, urllib.request

import yaml

member_dir, key, record_json = sys.argv[1], sys.argv[2], sys.argv[3]
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
        with urllib.request.urlopen(spec_url, timeout=10) as resp:
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

  # lib-stack.sh, not just lib-core.sh: this subcommand authenticates to a
  # Security Server's admin API and mutates federation state, so it needs
  # .env, XROAD_BIND and the api_key/api session helpers. Sourced HERE rather
  # than at the top of this file on purpose -- `drift` is documented as "no
  # auth, no HTTP to the join API, works whether or not it is even running",
  # and lib-stack.sh's credential refusal would end that.
  . "$PACK_DIR/scripts/lib-stack.sh"

  local topo="$PACK_DIR/hurl/topology.json"
  [ -f "$topo" ] || fail "$topo not found -- run python3 hurl/generate.py first"

  # subsystem id (the admin API's client id), the SS hosting it, and that
  # server's host-mapped admin port -- the same file cmd_list reads.
  local resolved
  resolved=$(python3 - "$topo" "$key" <<'PY'
import json, sys
topo = json.load(open(sys.argv[1]))
key = sys.argv[2]
sub = next((s for s in topo["subsystems"] if s["member_code"].lower() == key.lower()), None)
if sub is None:
    sys.exit(f"member.sh refresh: '{key}' is not in hurl/topology.json -- regenerate, or check the key")
ports = {s["host"]: s["host_ui_port"] for s in topo["security_servers"]}
host = sub["hosted_on"]
if host not in ports:
    sys.exit(f"member.sh refresh: {key}'s Security Server '{host}' has no host-mapped admin port in hurl/topology.json")
print(f"{sub['id']}\t{host}\t{ports[host]}")
PY
) || exit 1
  local client_id host ui
  IFS=$'\t' read -r client_id host ui <<<"$resolved"

  local jar
  jar=$(api_key "$XROAD_BIND:$ui" "$XROAD_ADMIN_USER" "$XROAD_ADMIN_PASSWORD") \
    || fail "could not log in to $host's admin API at $XROAD_BIND:$ui -- is the federation running?"

  local descriptions
  descriptions=$(api GET "$XROAD_BIND:$ui" "$jar" "/clients/${client_id}/service-descriptions") \
    || fail "could not read $client_id's service descriptions from $host"

  # The governance half, before anything is published: re-run the SAME
  # allowed-methods check apps/join-api/validate.py applies at join time,
  # against the spec as it is served NOW. A refresh makes the federation
  # publish the current contract; it does not make the current contract
  # approved. Refusing here is what keeps this a governance tool rather than
  # a convenience that launders an unreviewed write endpoint onto the bus.
  local plan
  plan=$(python3 - "$PACK_DIR/configs/x-road-bus/join-policy.yaml" "$descriptions" <<'PY'
import json, sys, urllib.request

import yaml

policy = (yaml.safe_load(open(sys.argv[1])) or {}).get("join") or {}
allowed = {m.upper() for m in (policy.get("allowed_methods") or [])}
descriptions = json.loads(sys.argv[2])
methods = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}

rows, refused = [], []
for desc in descriptions:
    url = desc.get("url")
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            spec = yaml.safe_load(resp.read()) or {}
    except Exception as exc:
        sys.exit(f"member.sh refresh: could not fetch the published spec at {url}: {exc}\n"
                 f"  (a docker-internal demo hostname is only reachable from inside the linkup "
                 f"network -- run this from a container on it, e.g. docker compose exec join-api)")
    paths = sorted((spec.get("paths") or {}).keys())
    for path, operations in (spec.get("paths") or {}).items():
        for method in operations or {}:
            if method.lower() in methods and method.upper() not in allowed:
                refused.append(f"{method.upper()} {path} (service description {desc.get('id')}, {url})")
    for svc in desc.get("services") or []:
        rows.append({"id": desc.get("id"), "url": url,
                     "service": svc.get("service_code") or svc.get("id"), "paths": paths})
    if not (desc.get("services") or []):
        rows.append({"id": desc.get("id"), "url": url, "service": None, "paths": paths})

if refused:
    sys.exit("member.sh refresh: REFUSED -- the spec as served now declares operations "
             "outside join.allowed_methods " + str(sorted(allowed)) + ":\n  "
             + "\n  ".join(refused)
             + "\n\nA refresh publishes the current contract; it does not approve it. This "
               "member's contract has changed beyond what it was admitted on -- that is a "
               "re-admission decision (an operator review, then a new join), not a refresh.")
print(json.dumps(rows))
PY
) || exit 1

  local ids
  ids=$(printf '%s' "$plan" | python3 -c 'import json,sys; print("\n".join(sorted({str(r["id"]) for r in json.load(sys.stdin)})))')
  [ -n "$ids" ] || { log "$key: no service descriptions published -- nothing to refresh"; return 0; }

  local desc_id
  while read -r desc_id; do
    api PUT "$XROAD_BIND:$ui" "$jar" "/service-descriptions/${desc_id}/refresh" >/dev/null \
      || fail "refresh of service description $desc_id on $host failed"
    log "refreshed service description $desc_id on $host"
  done <<<"$ids"

  # Record the act as an AMENDMENT. endpoint_baseline is never touched: it is
  # evidence of the contract this member was admitted on, and refreshing the
  # federation does not re-admit anybody. cmd_drift then reports both facts --
  # drift since join, and drift since this refresh -- instead of a warning
  # that can never clear.
  python3 - "$PACK_DIR/out/join" "$key" "$plan" <<'PY'
import datetime, glob, json, os, sys

join_dir, key, plan = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
best, best_path = None, None
for path in sorted(glob.glob(os.path.join(join_dir, "*.json"))):
    try:
        rec = json.load(open(path))
    except Exception:
        continue
    if rec.get("state") != "ACTIVE":
        continue
    if (rec.get("payload") or {}).get("code", "").lower() != key.lower():
        continue
    if best is None or rec.get("submitted_at", "") > best.get("submitted_at", ""):
        best, best_path = rec, path
if best is None:
    print(f"{key}: refreshed, but no ACTIVE out/join/*.json record to amend -- "
          f"drift has no per-refresh baseline to compare against.", file=sys.stderr)
    sys.exit(0)

endpoints = {}
for row in plan:
    if row["service"]:
        endpoints[row["service"]] = row["paths"]
best.setdefault("refreshes", []).append({
    "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "endpoints": endpoints,
})
# Same atomic temp-and-rename apps/join-api/app.py's _save_request uses: a
# reader must never see a half-written record.
tmp = best_path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(best, fh, indent=2)
os.replace(tmp, best_path)
print(f"recorded the refresh on {os.path.basename(best_path)} "
      f"({len(endpoints)} service(s)); endpoint_baseline untouched")
PY

  log "$key: the federation now publishes the contract these specs serve today."
  log "  What this did NOT do: approve it. The allowed-methods check above is the only"
  log "  policy re-applied; a changed field set, a changed lawful basis or a changed SLA"
  log "  is an operator review, and this command cannot stand in for one."
}


case "${1:-}" in
  list)    cmd_list ;;
  remove)  shift; cmd_remove "$@" ;;
  drift)   shift; cmd_drift "$@" ;;
  refresh) shift; cmd_refresh "$@" ;;
  *)       usage; exit 1 ;;
esac
