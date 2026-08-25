#!/usr/bin/env bash
# Capture (or, with --check, re-capture and diff) the real X-Road responses
# apps/console/tests/test_xroad.py's fixtures record -- testing-strategy
# plan. Recorded fixtures that nobody re-records eventually describe
# a server that no longer exists; --check is what stops that silently.
#
#   scripts/capture-xroad-fixtures.sh          # (re-)write the committed fixtures
#   scripts/capture-xroad-fixtures.sh --check  # re-capture into a temp dir, diff
#                                               # status+body against the committed
#                                               # ones, fail on drift
#
# Needs a running federation with PNEA:EXAMS currently granted identity-api
# (the pack's own committed default ACL state) -- restores that grant
# itself after the revoke/re-grant round trip either mode requires.
set -euo pipefail
PACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$PACK_DIR/scripts/lib-stack.sh"

FIXTURE_DIR="$PACK_DIR/apps/console/tests/fixtures/xroad"
CLIENT_ID="PROGRESSA:GOV:PNIA:IDENTITY"
SUBJECT_ID="PROGRESSA:GOV:PNEA:EXAMS"
# MOEYS:PEMIS retired; PLR:ENROLMENT is the negative check's
# unauthorised caller now (configs/x-road-bus/once-only-exchange.yaml's negative_check) --
# same reasoning here, this only needs a real bus member not granted this
# service.
UNGRANTED_SUBJECT="PROGRESSA:GOV:PLR:ENROLMENT"
SVC=identity-api

if [ "${1:-}" = "--check" ]; then
  OUT_DIR=$(mktemp -d)
else
  OUT_DIR="$FIXTURE_DIR"
fi
mkdir -p "$OUT_DIR"

# PNIA's own admin UI port -- resolved via HOST_SS, not hardcoded to
# ss-pnia, because a member with security_server.hosted_on set (a joined,
# hosted member) would be reachable via its host's server instead (the same
# resolution scripts/acceptance.sh already documents and relies on for the
# same reason).
PNIA_SS=${HOST_SS[PNIA:IDENTITY]}
PLR_SS=${HOST_SS[PLR:ENROLMENT]}

jar=$(api_key "${XROAD_BIND}:${SS_UI[$PNIA_SS]}" "$XROAD_ADMIN_USER" "$XROAD_ADMIN_PASSWORD")
# api_key() leaves an authenticated cookie jar behind in mktemp's directory
# with no cleanup of its own -- this script's own responsibility, since api_key()
# is shared by callers (scripts/acceptance.sh) that manage a jar's lifetime
# very differently.
trap 'rm -f "$jar"' EXIT
token=$(awk '$6 == "XSRF-TOKEN" { print $7 }' "$jar")
RAW_TMP=$(mktemp -d)

# Pinned against PNIA_SS's own captured certificate -- the three admin-API
# captures below were still raw `curl -k`, bypassing api_key()/api()'s
# pinning entirely, because they need the full response (headers + body,
# for mkfixture.py) that api()'s `curl -sf` throws away. Fixed by pinning
# _capture() itself rather than switching to api(). exchange_access_denied
# below is a DIFFERENT call -- the plain-HTTP r1/consumer proxy on :8080,
# never :4000 -- and passes no security flags of its own for that reason.
_admin_curl_opts "$PNIA_SS"
ADMIN_OPTS=("${_ADMIN_CURL_OPTS[@]}")

_capture() {  # $1=name $2=context $3...=curl args (after -si)
  local name=$1 context=$2; shift 2
  curl -si "$@" > "$RAW_TMP/$name.raw"
  python3 "$PACK_DIR/scripts/mkfixture.py" "$RAW_TMP/$name.raw" "$OUT_DIR/$name.json" "$context"
}

log "capturing read_acl_404"
_capture read_acl_404 \
  "GET /clients/{id}/service-clients/{subject}/access-rights where subject is not a service-client on this resource at all" \
  "${ADMIN_OPTS[@]}" -b "$jar" -X GET "https://${XROAD_BIND}:${SS_UI[$PNIA_SS]}/api/v1/clients/${CLIENT_ID}/service-clients/${UNGRANTED_SUBJECT}/access-rights" \
  -H "X-XSRF-TOKEN: ${token}"

log "capturing grant_409_duplicate"
_capture grant_409_duplicate \
  "POST /clients/{id}/service-clients/{subject}/access-rights granting a right already held" \
  "${ADMIN_OPTS[@]}" -b "$jar" -X POST "https://${XROAD_BIND}:${SS_UI[$PNIA_SS]}/api/v1/clients/${CLIENT_ID}/service-clients/${SUBJECT_ID}/access-rights" \
  -H "X-XSRF-TOKEN: ${token}" -H "Content-Type: application/json" \
  -d "{\"items\":[{\"service_code\":\"${SVC}\"}]}"

log "revoking, capturing revoke_409_not_found, then restoring the grant"
curl -sf "${ADMIN_OPTS[@]}" -b "$jar" -X POST "https://${XROAD_BIND}:${SS_UI[$PNIA_SS]}/api/v1/clients/${CLIENT_ID}/service-clients/${SUBJECT_ID}/access-rights/delete" \
  -H "X-XSRF-TOKEN: ${token}" -H "Content-Type: application/json" \
  -d "{\"items\":[{\"service_code\":\"${SVC}\"}]}" -o /dev/null
_capture revoke_409_not_found \
  "POST /clients/{id}/service-clients/{subject}/access-rights/delete revoking a right already revoked" \
  "${ADMIN_OPTS[@]}" -b "$jar" -X POST "https://${XROAD_BIND}:${SS_UI[$PNIA_SS]}/api/v1/clients/${CLIENT_ID}/service-clients/${SUBJECT_ID}/access-rights/delete" \
  -H "X-XSRF-TOKEN: ${token}" -H "Content-Type: application/json" \
  -d "{\"items\":[{\"service_code\":\"${SVC}\"}]}"
curl -sf "${ADMIN_OPTS[@]}" -b "$jar" -X POST "https://${XROAD_BIND}:${SS_UI[$PNIA_SS]}/api/v1/clients/${CLIENT_ID}/service-clients/${SUBJECT_ID}/access-rights" \
  -H "X-XSRF-TOKEN: ${token}" -H "Content-Type: application/json" \
  -d "{\"items\":[{\"service_code\":\"${SVC}\"}]}" -o /dev/null

log "capturing exchange_access_denied"
_capture exchange_access_denied \
  "GET /r1/.../identity-api/persons/{nin} from a caller (PLR:ENROLMENT) not granted access -- provider-side ACL denial" \
  -H "X-Road-Client: PROGRESSA/GOV/PLR/ENROLMENT" \
  "http://${XROAD_BIND}:${SS_REST[$PLR_SS]}/r1/PROGRESSA/GOV/PNIA/IDENTITY/identity-api/persons/02831663233"

rm -rf "$RAW_TMP"

if [ "${1:-}" = "--check" ]; then
  DRIFTED=0
  for f in "$FIXTURE_DIR"/*.json; do
    name=$(basename "$f")
    [ -f "$OUT_DIR/$name" ] || { echo "no fresh capture for $name"; DRIFTED=1; continue; }
    # Compare status+body only, with "detail" stripped from the body --
    # headers carry a fresh Date/correlation-id every single call and would
    # never match; captured/context are this tool's own metadata, not
    # X-Road's behaviour; and X-Road's own error bodies put a random
    # per-request trace UUID in "detail" (found live: this made every
    # exchange_access_denied re-capture "drift" even with zero real change
    # -- the UUID is never the same value twice by design, not a regression).
    a=$(python3 -c "
import json
d = json.load(open('$f'))
b = d['body']
if isinstance(b, dict) and 'detail' in b:
    b = {k: v for k, v in b.items() if k != 'detail'}
print(json.dumps({'status': d['status'], 'body': b}, sort_keys=True))
")
    b=$(python3 -c "
import json
d = json.load(open('$OUT_DIR/$name'))
b = d['body']
if isinstance(b, dict) and 'detail' in b:
    b = {k: v for k, v in b.items() if k != 'detail'}
print(json.dumps({'status': d['status'], 'body': b}, sort_keys=True))
")
    if [ "$a" != "$b" ]; then
      echo "DRIFT in $name:"
      echo "  committed: $a"
      echo "  live now:  $b"
      DRIFTED=1
    fi
  done
  rm -rf "$OUT_DIR"
  [ "$DRIFTED" = 0 ] || fail "X-Road's behaviour has moved since apps/console/tests/fixtures/xroad/ was recorded -- re-record it (scripts/capture-xroad-fixtures.sh) only after confirming the new shape is real, not a regression."
  log "xroad fixtures still match live behaviour"
fi
