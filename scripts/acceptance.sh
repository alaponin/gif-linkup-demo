#!/usr/bin/env bash
# Run the KP2 acceptance suite — one check per module in acceptance/, in order.
# Exit non-zero on first failure. 2.6 is the framework's acceptance (Module 5.6).
# Checks are same-shell functions (no bash -c subshells); topology comes from lib.sh.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

PNEA_REST="http://localhost:${SS_REST[ss-pnea]}"
# Negative check goes through the SS that hosts MOEYS:PEMIS (its own server —
# so the denial genuinely comes from the provider-side ACL, not from ss-pnea
# rejecting an unknown client). Under LITE that is the shared provider SS.
BAD_SS=${HOST_SS[MOEYS:PEMIS]}
BAD_REST="http://localhost:${SS_REST[$BAD_SS]}"
CLIENT='X-Road-Client: PROGRESSA/GOV/PNEA/EXAMS'
BADCLIENT='X-Road-Client: PROGRESSA/GOV/MOEYS/PEMIS'
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
# Each subsystem REGISTERED on the SS that hosts it (lib.sh HOST_SS handles LITE).
check_client_registered() {  # $1 = MEMBER:SUBSYSTEM
  local ss=${HOST_SS[$1]} sub=${1##*:}
  local key; key=$(api_key "localhost:${SS_UI[$ss]}" "${XROAD_ADMIN_USER}" "${XROAD_ADMIN_PASSWORD}")
  api GET "localhost:${SS_UI[$ss]}" "$key" /clients \
    | jq -e --arg s "$sub" '.[]|select(.subsystem_code==$s)|.status=="REGISTERED"' >/dev/null
}
# Registration status is global-conf propagation, same asynchrony as the Hurl
# runner itself -- confirmed at P5 (2026-07-25): a cold reproducibility run hit
# PNEA:EXAMS still short of REGISTERED the instant acceptance.sh started right
# after deploy, though it settled seconds later. Retry, don't fail (lib.sh's
# retry(), same as everywhere else this asynchrony shows up in this pack).
for pair in MOEYS:PEMIS PNEA:EXAMS PLR:ENROLMENT PNIA:IDENTITY; do
  check_pair() { retry 12 5 "${pair} REGISTERED" check_client_registered "$pair"; }
  check "2.x(${pair})" "client REGISTERED on ${HOST_SS[$pair]}" check_pair
done

# ACL exactness (2.4/2.5): confirmed live at P0 2026-07-25 —
# GET /clients/{id}/service-clients lists every subject granted ANY access on
# that client; GET .../service-clients/{subject}/access-rights lists which
# service codes that subject holds. Exactness needs both: the subject list is
# exactly [PNEA:EXAMS] (nobody else got in), AND that subject's granted service
# is exactly the one this provider publishes (not some other service leaking
# in via a wider grant).
check_acl_exact() {  # $1 = SS hosting the client, $2 = client id, $3 = service code, $4 = sole grantee
  local ss=$1 client_id=$2 svc=$3 grantee=$4
  local key; key=$(api_key "localhost:${SS_UI[$ss]}" "${XROAD_ADMIN_USER}" "${XROAD_ADMIN_PASSWORD}")
  api GET "localhost:${SS_UI[$ss]}" "$key" "/clients/${client_id}/service-clients" \
    | jq -e --arg who "$grantee" '[.[].id] == [$who]' >/dev/null &&
  api GET "localhost:${SS_UI[$ss]}" "$key" "/clients/${client_id}/service-clients/${grantee}/access-rights" \
    | jq -e --arg svc "$svc" '[.[].service_code] == [$svc]' >/dev/null
}
check_241() { retry 12 5 "enrolment-api ACL settled" check_acl_exact "${HOST_SS[PLR:ENROLMENT]}" \
  PROGRESSA:GOV:PLR:ENROLMENT enrolment-api PROGRESSA:GOV:PNEA:EXAMS; }
check 2.4.acl "enrolment-api grants exactly PNEA:EXAMS, nothing else" check_241
check_251() { retry 12 5 "identity-api ACL settled" check_acl_exact "${HOST_SS[PNIA:IDENTITY]}" \
  PROGRESSA:GOV:PNIA:IDENTITY identity-api PROGRESSA:GOV:PNEA:EXAMS; }
check 2.5.acl "identity-api grants exactly PNEA:EXAMS, nothing else" check_251

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

ID_URL="$PNEA_REST/r1/PROGRESSA/GOV/PNIA/IDENTITY/identity-api/persons"
EN_URL="$PNEA_REST/r1/PROGRESSA/GOV/PLR/ENROLMENT/enrolment-api/enrolments"

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
  curl -sk -H "$BADCLIENT" "$BAD_REST/r1/PROGRESSA/GOV/PNIA/IDENTITY/identity-api/persons/$NIN" \
    | jq -e '.type == "Server.ServerProxy.AccessDenied"' >/dev/null
}
check 2.6.4 "negative — MOEYS:PEMIS (via its own SS $BAD_SS) denied by the provider ACL" check_264

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

log "ACCEPTANCE GREEN — the framework runs (mark modules VERIFIED via kp-solution-verify)"
