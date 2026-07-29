#!/usr/bin/env bash
# Run the KP2 acceptance suite — one check per module in acceptance/, in order.
# Exit non-zero on first failure. 2.6 is the framework's acceptance (Module 5.6).
# Checks are same-shell functions (no bash -c subshells); topology comes from lib.sh.
#
#   scripts/acceptance.sh                # everything, unchanged
#   scripts/acceptance.sh --only 2.6      # only checks whose id is (or starts
#                                          # with) 2.6 -- matches 2.6.1..2.6.5
#   scripts/acceptance.sh --from 2.6      # 2.6 onward, in the same order
#
# Ids today are 2.1, 2.x(<MEMBER:SUBSYSTEM>), 2.x.acl(<service>) and
# 2.6.1-2.6.5 (member-parameterisation Task 7 generalised what used to be
# discrete 2.2/2.3/2.4/2.5 into the 2.x(...) loops -- there is no longer a
# literal "2.4" id to select; --only/--from match against what actually
# runs today, not the pre-generalisation module numbers).
set -euo pipefail
. "$(dirname "$0")/lib.sh"

SELECT_MODE=all
SELECT_ARG=""
case "${1:-}" in
  --only) SELECT_MODE=only; SELECT_ARG=${2:?"--only needs an id, e.g. --only 2.6"} ;;
  --from) SELECT_MODE=from; SELECT_ARG=${2:?"--from needs an id, e.g. --from 2.6"} ;;
  "") ;;
  *) echo "usage: scripts/acceptance.sh [--only <id> | --from <id>]" >&2; exit 1 ;;
esac

# Hierarchical prefix match: "2.6" matches "2.6" itself, "2.6.1" (a literal
# "." boundary) and "2.x(...)" style ids (a literal "(" boundary) -- not an
# unrelated id that merely happens to start with the same characters.
_id_matches() {
  case "$1" in
    "$2"|"$2".*|"$2"'('*) return 0 ;;
    *) return 1 ;;
  esac
}

_FROM_REACHED=0
[ "$SELECT_MODE" = all ] && _FROM_REACHED=1
_SELECTED_COUNT=0

case "$SELECT_MODE" in
  all)  log "running the full acceptance suite" ;;
  only) log "SELECTION: --only $SELECT_ARG -- this is a PARTIAL run, not a full pass" ;;
  from) log "SELECTION: --from $SELECT_ARG -- this is a PARTIAL run, not a full pass" ;;
esac

"$(dirname "$0")/check-exposure.sh"

OUT_DIR="$PACK_DIR/out"; mkdir -p "$OUT_DIR"

# The demo console (apps/console/) journals every ACL mutation to this file
# and clears it on a clean reset. A non-empty journal means a demo is
# mid-permission-toggle -- run scripts/console.sh reset first, or this suite
# can fail for a reason that has nothing to do with the pack itself. A
# missing file (the console was never built/started) means business as
# usual -- this check never imports or requires the console.
JOURNAL_FILE="$OUT_DIR/console-acl-journal.json"
if [ -s "$JOURNAL_FILE" ] && [ "$(cat "$JOURNAL_FILE")" != "[]" ]; then
  fail "the demo console's ACL journal ($JOURNAL_FILE) is not empty -- the \
federation is mid-demo. Run scripts/console.sh reset, then re-run this suite."
fi

check() { local id=$1 desc=$2 fn=$3
  case "$SELECT_MODE" in
    only)
      _id_matches "$id" "$SELECT_ARG" || { log "SKIP $id (not selected: --only $SELECT_ARG)"; return 0; }
      ;;
    from)
      if [ "$_FROM_REACHED" = 0 ]; then
        if _id_matches "$id" "$SELECT_ARG"; then
          _FROM_REACHED=1
        else
          log "SKIP $id (before --from $SELECT_ARG)"; return 0
        fi
      fi
      ;;
  esac
  _SELECTED_COUNT=$((_SELECTED_COUNT + 1))
  if "$fn"; then log "PASS $id — $desc"; else fail "FAIL $id — $desc"; fi }

# ---- 2.1 federation core -----------------------------------------------------
CS_KEY=$(api_key localhost:4000 xrd secret)
check_21() {  # paths confirmed live at P0 2026-07-25
  api GET localhost:4000 "$CS_KEY" /initialization/status \
    | jq -e '.instance_identifier=="PROGRESSA"' >/dev/null &&
  api GET localhost:4000 "$CS_KEY" /member-classes \
    | jq -e 'map(.code)|index("GOV")!=null' >/dev/null
}
check 2.1 "instance PROGRESSA, class GOV, trust services registered" check_21

# ---- 2.2–2.5 member registrations & services --------------------------------
# Every subsystem HOST_SS names, REGISTERED on the SS that hosts it -- covers
# whatever member set is actually deployed (hurl/topology.sh, member-
# parameterisation Task 4), not a fixed list of four. PDGA:MANAGEMENT is
# HOST_SS's one non-member entry (the federation owner's own management
# subsystem, added by a different flow during 10-ss-pdga.hurl) -- excluded
# here, same scope as before this generalisation.
check_client_registered() {  # $1 = MEMBER:SUBSYSTEM
  local ss=${HOST_SS[$1]} sub=${1##*:}
  local key; key=$(api_key "localhost:${SS_UI[$ss]}" "${XROAD_ADMIN_USER}" "${XROAD_ADMIN_PASSWORD}")
  api GET "localhost:${SS_UI[$ss]}" "$key" /clients \
    | jq -e --arg s "$sub" '.[]|select(.subsystem_code==$s)|.status=="REGISTERED"' >/dev/null
}
# Registration status is global-conf propagation, same asynchrony as the Hurl
# runner itself -- confirmed at P5 (2025-07-25): a cold reproducibility run hit
# PNEA:EXAMS still short of REGISTERED the instant acceptance.sh started right
# after deploy, though it settled seconds later. Retry, don't fail (lib.sh's
# retry(), same as everywhere else this asynchrony shows up in this pack).
for pair in $(printf '%s\n' "${!HOST_SS[@]}" | sort); do
  [ "$pair" = "PDGA:MANAGEMENT" ] && continue
  check_pair() { retry 12 5 "${pair} REGISTERED" check_client_registered "$pair"; }
  check "2.x(${pair})" "client REGISTERED on ${HOST_SS[$pair]}" check_pair
done

# ACL exactness (generalised over every service every member config declares,
# not two bespoke checks for enrolment-api/identity-api): confirmed live at
# P0 2026-07-25 -- GET /clients/{id}/service-clients lists every subject
# granted ANY access on that client; GET .../service-clients/{subject}/
# access-rights lists which service codes that subject holds. Exactness needs
# both, for EVERY service -- including a service with an EMPTY access: list
# (pemis-api today), which must have NO subjects at all. That case was
# previously unchecked entirely; this loop covers it as a natural consequence
# of being generic rather than as a bespoke third check.
#
# The dataset comes from hurl/topology.json, not a second read of
# configs/member-*/*.yaml -- topology.json already carries each subsystem's
# hosted_on and each service's code+access (member-parameterisation Task 3),
# so this is the one place that data is read for this purpose.
check_acl_exact() {  # $1 = SS hosting the client, $2 = client id, $3 = service code, $4 = expected subjects (JSON array)
  local ss=$1 client_id=$2 svc=$3 want_json=$4
  local key; key=$(api_key "localhost:${SS_UI[$ss]}" "${XROAD_ADMIN_USER}" "${XROAD_ADMIN_PASSWORD}")
  api GET "localhost:${SS_UI[$ss]}" "$key" "/clients/${client_id}/service-clients" \
    | jq -e --argjson want "$want_json" '([.[].id] | sort) == ($want | sort)' >/dev/null || return 1
  local subj
  for subj in $(printf '%s' "$want_json" | python3 -c "import json,sys; print('\n'.join(json.load(sys.stdin)))"); do
    api GET "localhost:${SS_UI[$ss]}" "$key" "/clients/${client_id}/service-clients/${subj}/access-rights" \
      | jq -e --arg svc "$svc" '[.[].service_code] == [$svc]' >/dev/null || return 1
  done
}
while IFS=$'\t' read -r ss client_id svc want_json; do
  check_svc() { retry 12 5 "${svc} ACL settled" check_acl_exact "$ss" "$client_id" "$svc" "$want_json"; }
  want_desc=$(printf '%s' "$want_json" | python3 -c "import json,sys; a=json.load(sys.stdin); print(', '.join(a) if a else '(nobody)')")
  check "2.x.acl(${svc})" "${svc} grants exactly ${want_desc}" check_svc
done < <(python3 -c "
import json
topo = json.load(open('$PACK_DIR/hurl/topology.json'))
for sub in topo['subsystems']:
    for svc in sub['services']:
        access = [a.replace('/', ':') for a in svc['access']]
        print(f\"{sub['hosted_on']}\t{sub['id']}\t{svc['code']}\t{json.dumps(access)}\")
")

# ---- 2.6 the once-only exchange ---------------------------------------------
NIN=$(python3 -c "
import csv
p={r['nin'] for r in csv.DictReader(open('$PACK_DIR/apps/data/persons.csv'))}
e={r['nin'] for r in csv.DictReader(open('$PACK_DIR/apps/data/enrolments.csv'))}
print(sorted(p&e)[0])")
MISSING_NIN=$(python3 -c "
import csv
p={r['nin'] for r in csv.DictReader(open('$PACK_DIR/apps/data/persons.csv'))}
e={r['nin'] for r in csv.DictReader(open('$PACK_DIR/apps/data/enrolments.csv'))}
print(sorted(p-e)[0])")

# The exchange's shape (consumer, negative caller, the two r1 paths) comes
# from configs/x-road-bus/2.6.yaml -- not restated as bash literals. Its
# ENTRYPOINT fields stay unread on purpose: they are static ("http://ss-
# pnea:8080") and only correct under profile: full -- under lite the
# consumer/negative-caller can be hosted elsewhere, so the entrypoint is
# resolved from HOST_SS/SS_REST instead, the same live-confirmed trap
# apps/console/truth.py already documents and avoids.
mapfile -t _exchange < <(python3 -c "
import yaml
cfg = yaml.safe_load(open('$PACK_DIR/configs/x-road-bus/2.6.yaml'))['exchange']
print(cfg['headers']['X-Road-Client'])
print(cfg['negative_check']['unauthorised_client'])
print(cfg['calls'][0]['r1_path'])
print(cfg['calls'][1]['r1_path'])
")
CLIENT="X-Road-Client: ${_exchange[0]}"
BADCLIENT="X-Road-Client: ${_exchange[1]}"
ID_PATH_TMPL="${_exchange[2]}"
EN_PATH_TMPL="${_exchange[3]}"

CONSUMER_MEMBER_SUBSYSTEM="${_exchange[0]//\//:}"; CONSUMER_MEMBER_SUBSYSTEM="${CONSUMER_MEMBER_SUBSYSTEM#*:*:}"
BAD_MEMBER_SUBSYSTEM="${_exchange[1]//\//:}"; BAD_MEMBER_SUBSYSTEM="${BAD_MEMBER_SUBSYSTEM#*:*:}"
PNEA_REST="http://localhost:${SS_REST[${HOST_SS[$CONSUMER_MEMBER_SUBSYSTEM]}]}"
# Negative check goes through the SS that hosts the unauthorised caller (its
# own server -- so the denial genuinely comes from the provider-side ACL, not
# from the consumer's SS rejecting an unknown client). Under LITE that is the
# shared provider SS.
BAD_SS=${HOST_SS[$BAD_MEMBER_SUBSYSTEM]}
BAD_REST="http://localhost:${SS_REST[$BAD_SS]}"

ID_URL="$PNEA_REST${ID_PATH_TMPL%/\{nin\}}"
EN_URL="$PNEA_REST${EN_PATH_TMPL%/\{nin\}}"

# Same asynchronous-propagation risk as the registration-status checks above
# (retry, don't fail): confirmed live at P5 (2026-07-26) -- a fresh deploy's
# first exchange call can hit a transient failure moments before it settles.
# retry() itself only reports success/failure and discards stdout, so capture
# the body with a small inline retry instead.
fetch_retry() {
  local url=$1 i out
  for ((i=1; i<=12; i++)); do
    if out=$(curl -sf -H "$CLIENT" "$url" 2>/dev/null); then printf '%s' "$out"; return 0; fi
    log "waiting: $url ($i/12)"; sleep 5
  done
  fail "timed out: $url"
}
id_json=$(fetch_retry "$ID_URL/$NIN")
en_json=$(fetch_retry "$EN_URL/$NIN")

check_261() { [ -n "$id_json" ] && [ -n "$en_json" ]; }
check 2.6.1 "happy path — both cross-server calls resolve" check_261

check_262() { python3 "$PACK_DIR/scripts/assert_record.py" "$NIN" "$id_json" "$en_json"; }
check 2.6.2 "right learner — fields match the seeded record" check_262

check_263() {  # asked once: citizen gives NIN only; bus pre-fills exactly the rest
  python3 - "$id_json" "$en_json" <<'PY'
import json, sys
idr, enr = json.loads(sys.argv[1]), json.loads(sys.argv[2])
form = {"nin", "given_name", "family_name", "date_of_birth", "sex", "region",
        "school", "level", "enrolment_year", "status"}
citizen = {"nin"}
# providers echo the nin as key confirmation; it is not a pre-fill
prefilled = (set(idr) | set(enr)) - citizen
assert prefilled == form - citizen, (
    f"asked-once broken: missing={form - citizen - prefilled} "
    f"unexpected={prefilled - (form - citizen)}")   # coverage AND purpose limitation
assert citizen.isdisjoint(prefilled), "citizen field also pre-filled"
PY
}
check 2.6.3 "asked once — NIN is the only citizen field; bus pre-fills exactly the rest" check_263

check_264() {  # denial must come from the provider ACL, observed as an X-Road error —
               # not a transport failure. Happy path (2.6.1) already proved the bus is up.
               # Exact fault confirmed live at P0 (2026-07-25):
               # {"type":"Server.ServerProxy.AccessDenied","message":"Request is not
               # allowed: SERVICE:PROGRESSA/GOV/PNIA/IDENTITY/identity-api",...}, HTTP 500.
  curl -sk -H "$BADCLIENT" "$BAD_REST${ID_PATH_TMPL/\{nin\}/$NIN}" \
    | jq -e '.type == "Server.ServerProxy.AccessDenied"' >/dev/null
}
check 2.6.4 "negative — ${_exchange[1]} (via its own SS $BAD_SS) denied by the provider ACL" check_264

check_265() {
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "$CLIENT" "$EN_URL/$MISSING_NIN")
  [ "$code" = "404" ]
}
check 2.6.5 "negative — NIN absent from PLR yields a clean 404, not silence" check_265

# ---- artefact: the assembled application with per-field provenance -----------
# The tangible 'asked once' object (video demo prop; the seam a KP4 Joget form
# later replaces). Optional further evidence at P0: the exchange in the provider
# SS message log [confirm P0: message-log query].
python3 - "$NIN" "$id_json" "$en_json" > "$OUT_DIR/application-$NIN.json" <<'PY'
import json, sys
nin, idr, enr = sys.argv[1], json.loads(sys.argv[2]), json.loads(sys.argv[3])
app = {"nin": {"value": nin, "source": "citizen — the one thing asked"}}
for k, v in idr.items():
    if k != "nin":
        app[k] = {"value": v, "source": "PNIA identity-api over the bus"}
for k, v in enr.items():
    if k != "nin":
        app[k] = {"value": v, "source": "PLR enrolment-api over the bus"}
print(json.dumps({"credential_application": app}, indent=2))
PY
log "artefact: out/application-$NIN.json (citizen field + pre-filled fields + provenance)"

if [ "$SELECT_MODE" = from ] && [ "$_FROM_REACHED" = 0 ]; then
  fail "--from $SELECT_ARG matched none of this run's check ids -- nothing ran. Ids today: 2.1, 2.x(<MEMBER:SUBSYSTEM>), 2.x.acl(<service>), 2.6.1-2.6.5."
fi
if [ "$SELECT_MODE" != all ] && [ "$_SELECTED_COUNT" = 0 ]; then
  fail "--$SELECT_MODE $SELECT_ARG matched none of this run's check ids -- nothing ran. Ids today: 2.1, 2.x(<MEMBER:SUBSYSTEM>), 2.x.acl(<service>), 2.6.1-2.6.5."
fi

if [ "$SELECT_MODE" = all ]; then
  log "ACCEPTANCE GREEN — the framework runs (mark modules VERIFIED via kp-solution-verify)"
else
  log "ACCEPTANCE GREEN for the SELECTED checks only (--$SELECT_MODE $SELECT_ARG, $_SELECTED_COUNT check(s)) -- this is NOT a full pass, do not mark modules VERIFIED from this alone"
fi
