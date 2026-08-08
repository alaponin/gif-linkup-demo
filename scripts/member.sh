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
                endpoint set against the baseline captured at join time
                (design spec §2.4/§5.4). No auth, no HTTP to the join API --
                works whether or not it is even running. Fails clearly if
                '<key>' has no ACTIVE out/join/*.json record to compare
                against (never joined through the API, or joined before this
                feature existed).

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
  [ "$origin" = "canonical" ] && fail "'$key' is a canonical member -- the canonical five never renumber or leave. Only a joined member can be removed."

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

  # The join-time baseline (spec S5.4) lives in the job context, i.e. the
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
    added, removed = sorted(current_paths - base_paths), sorted(base_paths - current_paths)
    if not added and not removed:
        print(f"{code}: no drift ({len(current_paths)} endpoint(s), unchanged since join)")
        continue
    any_drift = True
    print(f"{code}: DRIFT")
    for p in added:
        print(f"  + {p}")
    for p in removed:
        print(f"  - {p}")

sys.exit(1 if any_drift else 0)
PY
}

case "${1:-}" in
  list)   cmd_list ;;
  remove) shift; cmd_remove "$@" ;;
  drift)  shift; cmd_drift "$@" ;;
  *)      usage; exit 1 ;;
esac
