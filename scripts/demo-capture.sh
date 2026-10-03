#!/usr/bin/env bash
# Capture a video's demo block from the running federation -- demo only,
# never production. Every beat in the video's beats file
# (apps/console/capture/beats*.yaml) becomes a file in
# out/demo-takes/<video>/ (frame .png, text capture .txt, artefact .md, clip
# .mp4 + its still .png), described by out/demo-takes/<video>/takes.json,
# which the deck builder reads for each slide's provenance line.
#
#   scripts/demo-capture.sh                      # video 5.6 (the pilot)
#   scripts/demo-capture.sh --video 5.4          # 5.4 | 5.5 | 5.6 | reveals
#   scripts/demo-capture.sh --all                # reveals -> 5.5 -> 5.6 -> 5.4
#   scripts/demo-capture.sh --nin <nin>          # 5.6's learner, if not acceptance.sh's
#   scripts/demo-capture.sh --allow-stale-timings  # 5.5's T1 from an earlier deploy
#
# Needs the federation and the console up (scripts/console.sh up), a clean
# ACL journal, and ffmpeg on the host (clips are encoded here). 5.6's
# break-the-proof beat revokes and restores a real grant; the console is reset
# before and after, and on any failure. 5.4 joins PTSB and un-joins it again,
# so it runs last in --all and needs configs/, manifest.yaml, onboarding/ and
# hurl/ clean; every path the join writes is restored afterwards and the tree
# is asserted clean.
#
# Run it twice and the .txt captures are byte-identical and the frames
# pixel-identical: ?filming=1 hides everything that differs between takes.
# The documented exceptions: clip MP4s (real-time shot spacing), 5.5's T1
# (measured seconds) and 5.4's J4/J7 (the admission and retirement records
# carry the request id and timestamps of the run that wrote them).
set -euo pipefail
. "$(dirname "$0")/lib-stack.sh"

NIN=""; VIDEOS=(5.6); STALE_OK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --nin) NIN=${2:?"--nin needs a NIN, e.g. --nin 02831663233"}; shift 2 ;;
    --video) case "${2:-}" in 5.4|5.5|5.6|reveals) VIDEOS=("$2") ;; *) fail "--video is 5.4, 5.5, 5.6 or reveals" ;; esac; shift 2 ;;
    --all) VIDEOS=(reveals 5.5 5.6 5.4); shift ;;
    --allow-stale-timings) STALE_OK=1; shift ;;
    *) echo "usage: scripts/demo-capture.sh [--video 5.4|5.5|5.6|reveals | --all] [--nin <nin>] [--allow-stale-timings]" >&2; exit 1 ;;
  esac
done

CONSOLE_URL="http://${XROAD_BIND}:8090"
JOIN_URL="http://${XROAD_BIND}:8091"
TAKES_ROOT="$PACK_DIR/out/demo-takes"
CAPTURE_DIR="$PACK_DIR/apps/console/capture"
HDR="X-KP2-Console: 1"

reset_console() { curl -sf -X POST -H "$HDR" "$CONSOLE_URL/api/reset" >/dev/null; }
beats_file() { [ "$1" = 5.6 ] && echo beats.yaml || echo "beats-$1.yaml"; }
pack_dirty() { [ -n "$(git -C "$PACK_DIR" status --porcelain -- .)" ]; }

command -v ffmpeg >/dev/null || fail "ffmpeg is not on PATH -- it encodes the clip beats' frames into an MP4"
curl -sf "$CONSOLE_URL/api/health" >/dev/null || fail "no console at $CONSOLE_URL -- scripts/console.sh up"
curl -sf -H "$HDR" "$CONSOLE_URL/api/acl" | jq -e '.dirty == false' >/dev/null ||
  fail "the console's ACL journal is not empty -- a demo is mid-permission-change. Run scripts/console.sh reset first."
if pack_dirty; then
  log "WARN: the pack has uncommitted changes -- takes.json records pack_dirty: true, and the slides' provenance names a commit that is not what ran"
fi
PACK_COMMIT=$(git -C "$PACK_DIR" rev-parse HEAD)
PACK_DIRTY=$(pack_dirty && echo true || echo false)   # before 5.4's join dirties and restores the tree

# The learner the browser beats and C7 use: the one acceptance.sh last wrote.
learner() {
  [ -n "$NIN" ] && return 0
  local app_file; app_file=$(ls -t "$PACK_DIR"/out/application-*.json 2>/dev/null | head -1)
  [ -n "$app_file" ] || fail "no out/application-*.json -- run scripts/acceptance.sh first"
  NIN=$(basename "$app_file" .json); NIN=${NIN#application-}
}

# ---- the browser beats of one video, then its clips encoded ----------------
browser_beats() {  # $1 video  $2 out dir
  local rel=${2#"$TAKES_ROOT"/}
  log "browser beats: $1 (the capture service, profile film)"
  "${COMPOSE[@]}" --profile film run --rm --build capture \
    --beats "/capture/$(beats_file "$1")" --console http://console:8000 --nin "${NIN:-none}" --out "/out/$rel"
  local frames clip crop
  for frames in "$2"/*.frames; do
    [ -d "$frames" ] || continue
    clip="${frames%.frames}.mp4"; crop=""
    [ -f "$frames/crop.txt" ] && crop="crop=$(cat "$frames/crop.txt"),"
    log "encoding $(basename "$clip")"
    (cd "$frames" && ffmpeg -v error -y -f concat -safe 0 -i frames.txt \
       -vf "${crop}fps=30,format=yuv420p" -c:v libx264 -crf 16 -movflags +faststart "$clip")
    # the clip's still is a full-viewport shot: cut it to the clip's rectangle too
    if [ -n "$crop" ]; then
      ffmpeg -v error -y -i "${frames%.frames}.png" -vf "${crop%,}" "${frames%.frames}.cropped.png"
      mv "${frames%.frames}.cropped.png" "${frames%.frames}.png"
    fi
    rm -rf "$frames"
  done
}

# ---- takes.json: every beat of the file must have produced its file --------
write_takes() {  # $1 video  $2 out dir  [$3 extra JSON merged into the top level]
  local extra=${3:-'{}'}
  python3 - "$2" "$CAPTURE_DIR/$(beats_file "$1")" "${NIN:-}" "$XROAD_VERSION" \
    "$PACK_COMMIT" "$PACK_DIRTY" "$extra" <<'PY'
import datetime, hashlib, json, pathlib, sys
import yaml
takes, beats_yaml, nin, xroad, commit, dirty, extra = sys.argv[1:8]
takes = pathlib.Path(takes)
doc = yaml.safe_load(open(beats_yaml))
bj = takes / "beats.json"
browser = json.loads(bj.read_text()) if bj.is_file() else {}
ext = {"text": "txt", "artefact": "md", "frame": "png", "clip": "mp4"}
beats = {}
for b in doc["beats"]:
    entry = browser.get(b["id"]) or {"file": f"{b['id']}.{ext[b['kind']]}", "kind": b["kind"], "caption": b["caption"]}
    path = takes / entry["file"]
    if not path.is_file():
        sys.exit(f"takes.json: beat {b['id']} has no file {path.name}")
    entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    if b["kind"] == "clip":
        entry["still"] = f"{b['id']}.png"
    for key in ("for", "slide", "measured"):
        if key in b:
            entry[key] = b[key]
    beats[b["id"]] = entry
out = {"video": doc["video"], "nin": nin or None, "xroad_version": xroad, "pack_commit": commit,
       "pack_dirty": dirty == "true",
       "captured_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
out.update(json.loads(extra))
out["beats"] = beats
(takes / "takes.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
bj.unlink(missing_ok=True)
PY
}

# A host beat's expected observation: every listed line must appear.
expect_lines() {  # $1 beat  $2 file  $3... fixed strings
  local beat=$1 file=$2; shift 2
  for want in "$@"; do
    grep -qF -- "$want" "$file" || fail "$beat: expected $(printf '%q' "$want") in $(basename "$file"), got:
$(cat "$file")"
  done
}

# exercises.md §2's payload -- the one source the join beats and REQ-2 share.
ptsb_payload() {
  python3 - "$PACK_DIR/exercises.md" <<'PY'
import json, re, sys
text = open(sys.argv[1]).read()
sec = text.split("## 2 ·", 1)[1].split("\n## 3 ·", 1)[0]
print(json.dumps(json.loads(re.search(r"-d '(\{.*?\})'", sec, re.S).group(1))))
PY
}

# ============================================================== 5.6 ==========
capture_56() {
  local out="$TAKES_ROOT/5.6"; rm -rf "$out"; mkdir -p "$out"
  log "C8-acceptance: scripts/acceptance.sh --summary --only 2.6"
  local summary; summary=$("$PACK_DIR/scripts/acceptance.sh" --summary --only 2.6)
  [ "$(printf '%s\n' "$summary" | grep -c '^2\.6\.[1-6]  PASS  ')" = 6 ] ||
    fail "C8-acceptance: expected 2.6.1-2.6.6 all PASS, got:
$summary"
  printf '$ scripts/acceptance.sh --summary --only 2.6\n%s\n' "$summary" > "$out/C8-acceptance.txt"

  learner
  log "C7-application: out/application-$NIN.json"
  python3 - "$PACK_DIR/out/application-$NIN.json" "$out/C7-application.txt" <<'PY'
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

  reset_console
  browser_beats 5.6 "$out"
  reset_console
  curl -sf -H "$HDR" "$CONSOLE_URL/api/acl" |
    jq -e '.dirty == false and ([.services[] | (.live | sort) == (.configured | sort)] | all)' >/dev/null ||
    fail "after the capture the live ACL does not equal the configured ACL -- see GET $CONSOLE_URL/api/acl"
  write_takes 5.6 "$out"
}

# ============================================================== 5.5 ==========
capture_55() {
  local out="$TAKES_ROOT/5.5"; rm -rf "$out"; mkdir -p "$out"

  log "T0-containers: docker compose ps"
  { echo '$ docker compose ps --format "{{.Name}}  {{.Health}}"'
    "${COMPOSE_ALL[@]}" ps --format '{{.Name}}\t{{.Health}}' |
      awk -F'\t' '$1=="cs"||$1=="ca"||$1~/^ss-/' |
      sort -t$'\t' -k1,1 | awk -F'\t' 'BEGIN{o["cs"]=0;o["ca"]=1} {k=($1 in o)?o[$1]:2; print k"\t"$0}' |
      sort -s -t$'\t' -k1,1n | cut -f2- | awk -F'\t' '{printf "%-8s  %s\n", $1, $2}'
  } > "$out/T0-containers.txt"
  [ "$(grep -c '^ss-.*healthy$' "$out/T0-containers.txt")" = 4 ] && expect_lines T0-containers "$out/T0-containers.txt" "cs        healthy" "ca        healthy" ||
    fail "T0-containers: expected cs, ca and four ss-* all healthy, got:
$(cat "$out/T0-containers.txt")"

  log "T1-stood-up: demo.sh's stages + out/deploy-timings.txt"
  local timings="$PACK_DIR/out/deploy-timings.txt" cs_started deploy_start stale=false
  [ -f "$timings" ] || fail "T1-stood-up: no out/deploy-timings.txt -- deploy the federation with scripts/demo.sh first"
  deploy_start=$(sed -n 's/^deploy_start=//p' "$timings")
  cs_started=$(date -d "$(docker inspect -f '{{.State.StartedAt}}' cs)" +%s 2>/dev/null ||
               python3 -c "import datetime,sys; print(int(datetime.datetime.fromisoformat(sys.argv[1][:26].rstrip('Z')).replace(tzinfo=datetime.timezone.utc).timestamp()))" "$(docker inspect -f '{{.State.StartedAt}}' cs)")
  if [ "$cs_started" -gt "$((deploy_start + 3600))" ]; then
    stale=true
    [ "$STALE_OK" = 1 ] || fail "T1-stood-up: out/deploy-timings.txt is from a deploy on $(date -r "$deploy_start" 2>/dev/null || echo "$deploy_start"), not the one now running (cs started later). Redeploy (scripts/demo.sh), or pass --allow-stale-timings"
    log "WARN: T1's timings are from an earlier deploy -- takes.json says timings_stale: true"
  fi
  python3 - "$PACK_DIR/scripts/demo.sh" "$timings" "$out/T1-stood-up.txt" <<'PY'
import re, sys
demo, timings, dst = sys.argv[1:4]
stages = re.findall(r'^stage "(step [0-4] -- [^"]*)"', open(demo).read(), re.M)
t = dict(l.strip().split("=", 1) for l in open(timings) if "=" in l)
boot, hurl, total = (int(t[k]) for k in ("phase_containers_boot_seconds", "phase_hurl_run_seconds", "total_seconds"))
if len(stages) != 5 or boot + hurl != total:
    sys.exit(f"T1-stood-up: expected steps 0-4 and boot + hurl = total, got {len(stages)} steps, {boot}+{hurl} vs {total}")
lines = ["$ scripts/demo.sh", *stages, "",
         "$ cat out/deploy-timings.txt   # measured by scripts/deploy.sh",
         f"containers healthy {boot} s · Hurl run {hurl} s · total {total} s"]
open(dst, "w").write("\n".join(lines) + "\n")
PY

  log "T2-members: scripts/member.sh list"
  { echo '$ scripts/member.sh list'; "$PACK_DIR/scripts/member.sh" list; } > "$out/T2-members.txt"
  expect_lines T2-members "$out/T2-members.txt" "pnea     canonical  ss-pnea" "plr      canonical  ss-plr" "pnia     canonical  ss-pnia"
  ! grep -q '^ptsb' "$out/T2-members.txt" || fail "T2-members: ptsb is joined -- un-join it (exercises.md §4) before filming 5.5"

  # 2.1 (paths live, containers healthy) is asserted, not printed: T0 already shows the
  # health, and 2.1 + 2.x together are over terminal_slide's 18 lines.
  log "T3-registered: scripts/acceptance.sh --summary --only 2.1 (asserted), then --only 2.x"
  local s21; s21=$("$PACK_DIR/scripts/acceptance.sh" --summary --only 2.1)
  ! printf '%s\n' "$s21" | grep -q '  FAIL  ' || fail "T3-registered: a 2.1 check failed:
$s21"
  { echo '$ scripts/acceptance.sh --summary --only 2.x'; "$PACK_DIR/scripts/acceptance.sh" --summary --only 2.x
  } > "$out/T3-registered.txt"
  ! grep -q '  FAIL  ' "$out/T3-registered.txt" || fail "T3-registered: a check failed:
$(cat "$out/T3-registered.txt")"
  expect_lines T3-registered "$out/T3-registered.txt" "2.x(PNEA:EXAMS)  PASS" "2.x(PLR:ENROLMENT)  PASS" "2.x(PNIA:IDENTITY)  PASS" \
    "2.x.acl(identity-api)  PASS" "2.x.acl(enrolment-api)  PASS" "2.x.addons(ss-pnea)  PASS"

  local dep_at; dep_at=$(python3 -c "import datetime,sys; print(datetime.datetime.fromtimestamp(int(sys.argv[1]), datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))" "$deploy_start")
  write_takes 5.5 "$out" "{\"timings_stale\": $stale, \"timings_deploy_start\": \"$dep_at\"}"
}

# ============================================================== reveals ======
capture_reveals() {
  local out="$TAKES_ROOT/reveals"; rm -rf "$out"; mkdir -p "$out"

  log "REQ-1: onboarding/pnea/02-requirements.md"
  cp "$PACK_DIR/onboarding/pnea/02-requirements.md" "$out/REQ-1.md"
  [ "$(grep -cE '^\| [A-Z]' "$out/REQ-1.md")" = 7 ] || fail "REQ-1: expected the header row and six items in onboarding/pnea/02-requirements.md"

  log "REQ-2: the member_requirements block of exercises.md §2's payload"
  ptsb_payload | python3 -c '
import json, sys
block = json.load(sys.stdin)["member_requirements"]
print("POST /requests   # exercises.md §2, the member_requirements block")
print(json.dumps({"member_requirements": block}, indent=2))' > "$out/REQ-2.txt"
  expect_lines REQ-2 "$out/REQ-2.txt" '"has_security_server": true' '"technical_contact": "Head of IT, PTSB"'

  log "SLA-1: onboarding/plr/03-sla/enrolment-api.md"
  cp "$PACK_DIR/onboarding/plr/03-sla/enrolment-api.md" "$out/SLA-1.md"
  expect_lines SLA-1 "$out/SLA-1.md" "enrolment-api"

  log "P1: join-api under posture: production with a permissive key, unacknowledged"
  local tmp; tmp=$(mktemp -d "$PACK_DIR/out/p1.XXXXXX")   # under $HOME: Colima mounts nothing else
  cp "$PACK_DIR/deployment.yaml" "$tmp/original.yaml"
  python3 - "$PACK_DIR/deployment.yaml" "$tmp/deployment.yaml" <<'PY'
import re, sys
text = open(sys.argv[1]).read()
# Only the posture flips: docker-local's join_workflow already sets commit_gate: advisory
# explicitly, with no acknowledge_permissive -- the refusal this reveal shows.
text, n = re.subn(r"^posture: demo\b", "posture: production", text, count=1, flags=re.M)
jw = (__import__("yaml").safe_load(text) or {}).get("join_workflow") or {}
if n != 1 or jw.get("commit_gate") != "advisory" or "commit_gate" in (jw.get("acknowledge_permissive") or []):
    sys.exit("P1: expected 'posture: demo' and an unacknowledged 'commit_gate: advisory' in deployment.yaml")
open(sys.argv[2], "w").write(text)
PY
  local logf="$tmp/join-api.log"
  "${COMPOSE[@]}" --profile demo run --rm --no-deps \
    -v "$tmp/deployment.yaml:/repo/deployment.yaml:ro" \
    join-api >"$logf" 2>&1 && fail "P1: join-api STARTED under an unacknowledged permissive key -- see $logf"
  cmp -s "$tmp/original.yaml" "$PACK_DIR/deployment.yaml" || fail "P1: deployment.yaml changed during the capture"
  { echo '$ docker compose run join-api   # deployment.yaml: posture: production, join_workflow.commit_gate: advisory'
    grep -m1 -oE "RuntimeError: join-api: deployment.yaml posture: production implies .*" "$logf" | fold -s -w 88
  } > "$out/P1.txt"
  expect_lines P1 "$out/P1.txt" "posture: production implies" "acknowledge_permissive"
  rm -rf "$tmp"

  log "P2: docs/path-conformance.md, its Summary table"
  awk '/^## Summary/{p=1} p&&/^## /&&!/Summary/{exit} p' "$PACK_DIR/docs/path-conformance.md" > "$out/P2.md"
  expect_lines P2 "$out/P2.md" "| implemented | 41 |" "| simulated | 6 |" "| named absence | 24 |" "| out of scope | 4 |"

  reset_console
  browser_beats reveals "$out"
  write_takes reveals "$out"
}

# ============================================================== 5.4 ==========
JOIN_TRACKED=(manifest.yaml onboarding hurl configs)
JOIN_HOLD="$PACK_DIR/out/join-film-hold"
# Plan D4: the join tab films this run's cards only. The store, and the legacy
# out/join/*.json records join-api refuses to start beside an empty store,
# are held aside for the run and put back after it.
HELD=(join-store join)

JOIN_STAGE=""   # "" before the join, "joined" once PTSB is on the bus, "retired" after J7

restore_after_join() {
  if [ "$JOIN_STAGE" = joined ]; then
    # PTSB is live on the Central Server and ss-plr: putting the files and the store back
    # now would leave the pack saying it never joined. Leave everything as it is.
    log "5.4 FAILED WITH PTSB JOINED -- nothing restored. join-api holds the joined record; un-join first:"
    log "  scripts/join.sh up; set -a; . ./.env; set +a"
    log "  curl -X DELETE -H 'X-KP2-Console: 1' -H \"Authorization: Bearer \$KP2_JOIN_OPERATOR_TOKEN\" $JOIN_URL/members/ptsb"
    log "  then git checkout/clean configs/member-ptsb onboarding/ manifest.yaml hurl/, and move the"
    log "  originals back from out/join-film-hold/ (join-store.held -> out/join-store, join.held -> out/join)"
    return 0
  fi
  log "5.4 teardown: restoring every path the join wrote"
  git -C "$PACK_DIR" checkout -- manifest.yaml onboarding/catalogue.yaml hurl/ 2>/dev/null || true
  git -C "$PACK_DIR" clean -fdq -- configs/member-ptsb onboarding/ptsb hurl/ 2>/dev/null || true
  (cd "$PACK_DIR" && python3 hurl/generate.py >/dev/null)
  "$PACK_DIR/scripts/join.sh" down >/dev/null 2>&1 || true
  if [ -d "$JOIN_HOLD" ]; then
    local d held
    for d in "${HELD[@]}"; do
      # A file-sync client on this tree has renamed a moved directory ("join-store 2")
      # mid-run once: take whatever the held entry is called now, and never delete the
      # film store unless the original is there to replace it.
      held=$(ls -d "$JOIN_HOLD/$d.held"* 2>/dev/null | head -1)
      [ -n "$held" ] || continue
      rm -rf "${PACK_DIR:?}/out/$d"
      mv "$held" "$PACK_DIR/out/$d"
    done
    rmdir "$JOIN_HOLD" 2>/dev/null || log "WARN: $JOIN_HOLD is not empty -- check it by hand: $(ls -A "$JOIN_HOLD" | tr '\n' ' ')"
  fi
  reset_console || true
}

capture_54() {
  local out="$TAKES_ROOT/5.4"; rm -rf "$out"; mkdir -p "$out"
  [ -z "$(git -C "$PACK_DIR" status --porcelain -- "${JOIN_TRACKED[@]}")" ] ||
    fail "5.4: ${JOIN_TRACKED[*]} must be clean -- the join writes there and the capture restores them"
  grep -q '^ptsb' <("$PACK_DIR/scripts/member.sh" list) && fail "5.4: ptsb is already joined -- un-join it first (exercises.md §4)"
  [ -f "$PACK_DIR/.env" ] && { set -a; . "$PACK_DIR/.env"; set +a; }

  "$PACK_DIR/scripts/join.sh" down >/dev/null 2>&1 || true
  [ -e "$JOIN_HOLD" ] && fail "5.4: $JOIN_HOLD exists -- a previous capture did not restore the join store; move it back by hand"
  mkdir -p "$JOIN_HOLD"
  trap 'restore_after_join' EXIT
  local d
  for d in "${HELD[@]}"; do if [ -e "$PACK_DIR/out/$d" ]; then mv "$PACK_DIR/out/$d" "$JOIN_HOLD/$d.held"; fi; done
  "$PACK_DIR/scripts/join.sh" up >/dev/null

  local payload bad
  payload=$(ptsb_payload)
  bad=$(printf '%s' "$payload" | jq -c '.code = "PT SB"')
  submit() { curl -s -X POST "$JOIN_URL/requests" -H "$HDR" -H "Authorization: Bearer $KP2_JOIN_APPLICANT_TOKEN" \
               -H "Content-Type: application/json" -d "$1"; }
  log "J0: submitting PT SB (a member code with a space)"
  submit "$bad" >/dev/null
  log "J1: submitting PTSB (exercises.md §2)"
  local resp req_id; resp=$(submit "$payload")
  req_id=$(printf '%s' "$resp" | jq -r '.id // empty')
  [ -n "$req_id" ] || fail "J1: the PTSB submission returned no request id: $resp"

  reset_console
  JOIN_STAGE=joined   # from here PTSB may be on the bus (J3 approves the join)
  browser_beats 5.4 "$out"

  log "J4-admission: onboarding/ptsb/01-admission.md"
  { echo '$ cat onboarding/ptsb/01-admission.md'; cat "$PACK_DIR/onboarding/ptsb/01-admission.md"; } > "$out/J4-admission.txt"
  expect_lines J4-admission "$out/J4-admission.txt" "RIHA-2026-001" "$req_id"

  log "J5-hosted: scripts/member.sh list"
  { echo '$ scripts/member.sh list'; "$PACK_DIR/scripts/member.sh" list; } > "$out/J5-hosted.txt"
  expect_lines J5-hosted "$out/J5-hosted.txt" "pnea     canonical" "plr      canonical" "pnia     canonical"
  grep -qE '^ptsb +joined +ss-plr' "$out/J5-hosted.txt" || fail "J5-hosted: expected ptsb joined on ss-plr, got:
$(cat "$out/J5-hosted.txt")"

  log "J6-proved: scripts/acceptance.sh --summary --only 2.7"
  { echo '$ scripts/acceptance.sh --summary --only 2.7'; "$PACK_DIR/scripts/acceptance.sh" --summary --only 2.7; } > "$out/J6-proved.txt"
  ! grep -q '  FAIL  ' "$out/J6-proved.txt" || fail "J6-proved: a 2.7 check failed:
$(cat "$out/J6-proved.txt")"
  expect_lines J6-proved "$out/J6-proved.txt" "2.7.r1(PTSB.awards-api)  PASS" "2.7.deny(PTSB.awards-api)  PASS" \
    "2.7.fields(PTSB.awards-api)  PASS" "2.7.catalogue(PTSB.awards-api)  PASS"

  log "J7-unjoin: DELETE /members/ptsb, then --only 2.7.unjoin"
  "$PACK_DIR/scripts/join.sh" up >/dev/null   # acceptance.sh --only 2.7 stops join-api when it is done
  curl -sf -X DELETE "$JOIN_URL/members/ptsb" -H "$HDR" -H "Authorization: Bearer $KP2_JOIN_OPERATOR_TOKEN" >/dev/null ||
    fail "J7-unjoin: DELETE /members/ptsb was refused"
  local i state
  for ((i=1; i<=60; i++)); do
    state=$(curl -sf "$JOIN_URL/requests/$req_id" -H "$HDR" -H "Authorization: Bearer $KP2_JOIN_OPERATOR_TOKEN" | jq -r .state)
    [ "$state" = RETIRED ] && break
    sleep 3
  done
  [ "$state" = RETIRED ] || fail "J7-unjoin: PTSB did not reach RETIRED (last state: $state)"
  JOIN_STAGE=retired
  { echo '$ curl -X DELETE .../members/ptsb   # then, once RETIRED:'
    echo '$ scripts/acceptance.sh --summary --only 2.7.unjoin'
    "$PACK_DIR/scripts/acceptance.sh" --summary --only 2.7.unjoin
    echo; echo '$ ls onboarding/ptsb/'; ls "$PACK_DIR/onboarding/ptsb/"; } > "$out/J7-unjoin.txt"
  expect_lines J7-unjoin "$out/J7-unjoin.txt" "2.7.unjoin(PTSB)  PASS" "2.7.unjoin.catalogue(PTSB)  PASS" "99-retirement.md"

  trap - EXIT
  restore_after_join
  [ -z "$(git -C "$PACK_DIR" status --porcelain -- "${JOIN_TRACKED[@]}")" ] ||
    fail "5.4: the tree is not clean after the restore:
$(git -C "$PACK_DIR" status --porcelain -- "${JOIN_TRACKED[@]}")"
  "$PACK_DIR/scripts/acceptance.sh" --summary --only 2.6 | grep -q '  FAIL  ' &&
    fail "5.4: 2.6 is not green after the un-join -- the federation is not as it was"
  write_takes 5.4 "$out" "{\"request_id\": \"$req_id\"}"
}

# ---- run ------------------------------------------------------------------
trap 'reset_console || true' EXIT
reset_console
mkdir -p "$TAKES_ROOT"
for v in "${VIDEOS[@]}"; do
  case "$v" in
    5.6) capture_56 ;; 5.5) capture_55 ;; reveals) capture_reveals ;; 5.4) capture_54 ;;
  esac
done

# ---- nothing secret in a text capture ---------------------------------------
for key in KP2_JOIN_OPERATOR_TOKEN KP2_JOIN_APPLICANT_TOKEN XROAD_TOKEN_PIN XROAD_ADMIN_PASSWORD; do
  value=${!key:-}
  [ ${#value} -ge 4 ] || continue
  if grep -rlF --include='*.txt' --include='*.md' -- "$value" "$TAKES_ROOT" >/dev/null 2>&1; then
    fail "the value of $key appears in $(grep -rlF --include='*.txt' --include='*.md' -- "$value" "$TAKES_ROOT" | xargs -n1 basename | tr '\n' ' ')-- do not publish these takes"
  fi
done

trap - EXIT
reset_console
for v in "${VIDEOS[@]}"; do log "takes in out/demo-takes/$v/:"; (cd "$TAKES_ROOT/$v" && ls -1); done
