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

  list          Print the deployed member set (key, origin, server, ports),
                read from hurl/topology.json.
  remove <key>  Delete configs/member-<key>/ and manifest.yaml's
                identity.members.<key> entry, then regenerate. Refuses on a
                canonical member. Does not touch a running federation --
                the member stays registered there until
                scripts/teardown.sh --purge.

There is no "add": run prompts/member.md against an agency brief instead.
USAGE
}

cmd_list() {
  local topo="$PACK_DIR/hurl/topology.json"
  [ -f "$topo" ] || fail "$topo not found -- run python3 hurl/generate.py first"
  python3 - "$topo" <<'PY'
import json, sys
topo = json.load(open(sys.argv[1]))
ports = {s["host"]: (s["host_ui_port"], s["host_proxy_port"]) for s in topo["security_servers"]}
print(f"{'KEY':<8} {'ORIGIN':<10} {'SERVER':<10} UI              REST")
for sub in sorted(topo["subsystems"], key=lambda s: s["member_code"]):
    key = sub["member_code"].lower()
    host = sub["hosted_on"]
    ui, rest = ports.get(host, ("?", "?"))
    print(f"{key:<8} {sub['origin']:<10} {host:<10} localhost:{ui:<6} localhost:{rest}")
PY
}

cmd_remove() {
  local key=${1:?"remove needs a member key -- see: scripts/member.sh"}
  local dir="$PACK_DIR/configs/member-$key"
  [ -d "$dir" ] || fail "no configs/member-$key/ -- nothing to remove"

  local origin
  origin=$(python3 -c "
import yaml
m = yaml.safe_load(open('$PACK_DIR/manifest.yaml'))['identity']['members'].get('$key') or {}
print(m.get('origin', 'canonical'))
")
  [ "$origin" = "canonical" ] && fail "'$key' is a canonical member -- the canonical five never renumber or leave. Only a joined member can be removed."

  rm -r "$dir"

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

case "${1:-}" in
  list)   cmd_list ;;
  remove) shift; cmd_remove "$@" ;;
  *)      usage; exit 1 ;;
esac
