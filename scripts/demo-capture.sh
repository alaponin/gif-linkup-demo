#!/usr/bin/env bash
# Capture a video's demo block from the running federation -- demo only,
# never production. Every beat in apps/console/capture/beats.yaml becomes a
# file in out/demo-takes/ (frame .png, text capture .txt, clip .mp4 + its
# still .png), described by out/demo-takes/takes.json, which the deck
# builder reads for each slide's provenance line.
#
#   scripts/demo-capture.sh              # the learner scripts/acceptance.sh picks
#   scripts/demo-capture.sh --nin <nin>  # a different learner
#
# Needs the federation and the console up (scripts/console.sh up), a clean
# ACL journal, and ffmpeg on the host (the clip is encoded here). Takes
# ~3 minutes. The break-the-proof beat revokes and restores a real grant;
# the console is reset before and after, and on any failure.
#
# Run it twice and the .txt captures are byte-identical and the frames
# pixel-identical: ?filming=1 hides everything that differs between takes.
set -euo pipefail
. "$(dirname "$0")/lib-stack.sh"

NIN=""
case "${1:-}" in
  --nin) NIN=${2:?"--nin needs a NIN, e.g. --nin 02831663233"} ;;
  "") ;;
  *) echo "usage: scripts/demo-capture.sh [--nin <nin>]" >&2; exit 1 ;;
esac

CONSOLE_URL="http://${XROAD_BIND}:8090"
TAKES="$PACK_DIR/out/demo-takes"
BEATS="$PACK_DIR/apps/console/capture/beats.yaml"
HDR="X-KP2-Console: 1"

reset_console() { curl -sf -X POST -H "$HDR" "$CONSOLE_URL/api/reset" >/dev/null; }

command -v ffmpeg >/dev/null || fail "ffmpeg is not on PATH -- it encodes the clip beat's frames into an MP4"
curl -sf "$CONSOLE_URL/api/health" >/dev/null || fail "no console at $CONSOLE_URL -- scripts/console.sh up"
curl -sf -H "$HDR" "$CONSOLE_URL/api/acl" | jq -e '.dirty == false' >/dev/null ||
  fail "the console's ACL journal is not empty -- a demo is mid-permission-change. Run scripts/console.sh reset first."
if [ -n "$(git -C "$PACK_DIR" status --porcelain -- .)" ]; then
  log "WARN: the pack has uncommitted changes -- takes.json records pack_dirty: true, and the slides' provenance names a commit that is not what ran"
fi

trap 'reset_console || true' EXIT
reset_console
rm -rf "$TAKES"; mkdir -p "$TAKES"

# ---- C8: the acceptance check, as the slide shows it ------------------------
# Also (re)writes out/application-<nin>.json, which C7 shows.
log "C8-acceptance: scripts/acceptance.sh --summary --only 2.6"
summary=$("$PACK_DIR/scripts/acceptance.sh" --summary --only 2.6)
[ "$(printf '%s\n' "$summary" | grep -c '^2\.6\.[1-6]  PASS  ')" = 6 ] ||
  fail "C8-acceptance: expected 2.6.1-2.6.6 all PASS, got:
$summary"
printf '$ scripts/acceptance.sh --summary --only 2.6\n%s\n' "$summary" > "$TAKES/C8-acceptance.txt"

# ---- C7: the assembled application ------------------------------------------
# The learner is the one acceptance.sh just wrote, unless --nin says otherwise.
if [ -z "$NIN" ]; then
  app_file=$(ls -t "$PACK_DIR"/out/application-*.json | head -1)
  NIN=$(basename "$app_file" .json); NIN=${NIN#application-}
fi
log "C7-application: out/application-$NIN.json"
python3 - "$PACK_DIR/out/application-$NIN.json" "$TAKES/C7-application.txt" <<'PY'
import json, pathlib, sys
src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
if not src.is_file():
    sys.exit(f"C7-application: {src} does not exist -- run scripts/acceptance.sh for this learner first")
app = json.loads(src.read_text())["credential_application"]
citizen = [k for k, v in app.items() if v["source"].startswith("citizen")]
if citizen != ["nin"]:
    sys.exit(f"C7-application: expected nin to be the only citizen-sourced field, got {citizen}")
rows = [("field", "value", "source")] + [(k, str(v["value"]), v["source"]) for k, v in app.items()]
w0, w1 = (max(len(r[i]) for r in rows) for i in (0, 1))
lines = [f"out/{src.name} — the assembled application, one line per field", ""]
lines += [f"{a:<{w0}}  {b:<{w1}}  {c}" for a, b, c in rows]
dst.write_text("\n".join(lines) + "\n")
PY

# ---- C1-C6: the browser beats -----------------------------------------------
log "browser beats: the capture service (profile film), learner $NIN"
KP2_FILM_NIN=$NIN "${COMPOSE[@]}" --profile film run --rm --build capture

for frames in "$TAKES"/*.frames; do
  [ -d "$frames" ] || continue
  clip="${frames%.frames}.mp4"
  log "encoding $(basename "$clip")"
  (cd "$frames" && ffmpeg -v error -y -f concat -safe 0 -i frames.txt \
     -vf "fps=30,format=yuv420p" -c:v libx264 -crf 16 -movflags +faststart "$clip")
  rm -rf "$frames"
done

# ---- back to the configured state, and prove it -----------------------------
reset_console
curl -sf -H "$HDR" "$CONSOLE_URL/api/acl" |
  jq -e '.dirty == false and ([.services[] | (.live | sort) == (.configured | sort)] | all)' >/dev/null ||
  fail "after the capture the live ACL does not equal the configured ACL -- see GET $CONSOLE_URL/api/acl"

# ---- takes.json ---------------------------------------------------------------
python3 - "$TAKES" "$BEATS" "$NIN" "$XROAD_VERSION" \
  "$(git -C "$PACK_DIR" rev-parse HEAD)" "$([ -n "$(git -C "$PACK_DIR" status --porcelain -- .)" ] && echo true || echo false)" <<'PY'
import datetime, hashlib, json, pathlib, sys
import yaml
takes, beats_yaml, nin, xroad, commit, dirty = sys.argv[1:7]
takes = pathlib.Path(takes)
browser = json.loads((takes / "beats.json").read_text())
beats = {}
for b in yaml.safe_load(open(beats_yaml))["beats"]:
    entry = browser.get(b["id"]) or {"file": f"{b['id']}.txt", "kind": b["kind"], "caption": b["caption"]}
    path = takes / entry["file"]
    if not path.is_file():
        sys.exit(f"takes.json: beat {b['id']} has no file {path.name}")
    entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    if b["kind"] == "clip":
        entry["still"] = f"{b['id']}.png"
    beats[b["id"]] = entry
(takes / "takes.json").write_text(json.dumps({
    "video": yaml.safe_load(open(beats_yaml))["video"],
    "nin": nin,
    "xroad_version": xroad,
    "pack_commit": commit,
    "pack_dirty": dirty == "true",
    "captured_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "beats": beats,
}, indent=2, ensure_ascii=False) + "\n")
(takes / "beats.json").unlink()
PY

# ---- nothing secret in a text capture ---------------------------------------
for key in KP2_JOIN_OPERATOR_TOKEN KP2_JOIN_APPLICANT_TOKEN XROAD_TOKEN_PIN XROAD_ADMIN_PASSWORD; do
  value=${!key:-}
  [ ${#value} -ge 4 ] || continue
  if grep -lF -- "$value" "$TAKES"/*.txt >/dev/null 2>&1; then
    fail "the value of $key appears in $(grep -lF -- "$value" "$TAKES"/*.txt | xargs -n1 basename | tr '\n' ' ')-- do not publish these takes"
  fi
done

trap - EXIT
log "takes in out/demo-takes/ (learner $NIN):"
(cd "$TAKES" && ls -1)
