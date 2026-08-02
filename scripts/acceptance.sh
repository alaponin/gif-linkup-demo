#!/usr/bin/env bash
# Run the KP2 acceptance suite — one check per module in acceptance/, in order.
# Exit non-zero on first failure. 2.6 is the framework's acceptance (Module 5.6).
# Checks are same-shell functions (no bash -c subshells); topology comes from lib-stack.sh.
#
#   scripts/acceptance.sh                # everything, unchanged
#   scripts/acceptance.sh --only 2.6      # only checks whose id is (or starts
#                                          # with) 2.6 -- matches 2.6.1..2.6.5
#   scripts/acceptance.sh --from 2.6      # 2.6 onward, in the same order
#
# Ids today are 2.1, 2.x(<MEMBER:SUBSYSTEM>), 2.x.acl(<service>),
# 2.6.1-2.6.5, 2.7.1, 2.7.r1(<member>.<service>) and 2.7.deny(<member>.
# <service>) (member-parameterisation Task 7 generalised what used to be
# discrete 2.2/2.3/2.4/2.5 into the 2.x(...) loops -- there is no longer a
# literal "2.4" id to select; --only/--from match against what actually
# runs today, not the pre-generalisation module numbers).
set -euo pipefail
. "$(dirname "$0")/lib-stack.sh"

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
# after deploy, though it settled seconds later. Retry, don't fail (lib-core.sh's
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
done < <(python3 - "$PACK_DIR/hurl/topology.json" <<'PY'
import json, sys
topo = json.load(open(sys.argv[1]))
for sub in topo['subsystems']:
    for svc in sub['services']:
        access = [a.replace('/', ':') for a in svc['access']]
        print(f"{sub['hosted_on']}\t{sub['id']}\t{svc['code']}\t{json.dumps(access)}")
PY
)

# ---- 2.6 the once-only exchange ---------------------------------------------
NIN=$(python3 - "$PACK_DIR/apps/data/persons.csv" "$PACK_DIR/apps/data/enrolments.csv" <<'PY'
import csv, sys
p={r['nin'] for r in csv.DictReader(open(sys.argv[1]))}
e={r['nin'] for r in csv.DictReader(open(sys.argv[2]))}
print(sorted(p&e)[0])
PY
)
MISSING_NIN=$(python3 - "$PACK_DIR/apps/data/persons.csv" "$PACK_DIR/apps/data/enrolments.csv" <<'PY'
import csv, sys
p={r['nin'] for r in csv.DictReader(open(sys.argv[1]))}
e={r['nin'] for r in csv.DictReader(open(sys.argv[2]))}
print(sorted(p-e)[0])
PY
)

# The exchange's shape (consumer, negative caller, the two r1 paths) comes
# from configs/x-road-bus/2.6.yaml -- not restated as bash literals. Its
# ENTRYPOINT fields stay unread on purpose: they are static ("http://ss-
# pnea:8080") and only correct under profile: full -- under lite the
# consumer/negative-caller can be hosted elsewhere, so the entrypoint is
# resolved from HOST_SS/SS_REST instead, the same live-confirmed trap
# apps/console/truth.py already documents and avoids.
mapfile -t _exchange < <(python3 - "$PACK_DIR/configs/x-road-bus/2.6.yaml" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1]))['exchange']
print(cfg['headers']['X-Road-Client'])
print(cfg['negative_check']['unauthorised_client'])
print(cfg['calls'][0]['r1_path'])
print(cfg['calls'][1]['r1_path'])
PY
)
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

# ---- 2.7 a new member joins the bus -- the join API -------------------------
# acceptance/2.7.md's clause: a real r1 call through an authorized consumer's
# own Security Server against a joined member's service returns 2xx, and the
# same call from an unauthorized subsystem is denied by the provider ACL --
# the one assertion in this suite that would catch a registry-perfect-but-
# dead member (design spec §2.4). Registry state itself (REGISTERED, exact
# ACL) is NOT re-asserted here -- acceptance/member.md's existing generic
# checks already cover any member, joined or not; duplicating them here was
# explicitly ruled out (spec §12, "do not duplicate").
#
# This is the one section that starts and stops its own building block
# (join-api, profile "demo") rather than assuming it is already up --
# proving module 2.7's own service deploys and reports healthy is part of
# what "2.7 is first-class" means, the same way every other module's
# acceptance implicitly proves its building block came up. Gated on whether
# the current selection would touch a 2.7 id at all, mirroring check()'s own
# SELECT_MODE/_FROM_REACHED reasoning -- an unrelated `--only 2.6` run must
# never bring join-api up, and a full run must leave it stopped afterward,
# not just running. (This section's own r1 assertion needs no join-api of
# its own -- it is a plain HTTP call through a Security Server, the same
# shape as 2.6.4/2.6.5. join-api is brought up here only to prove module
# 2.7's building block deploys; see acceptance.sh's Task-5 report for the
# alternative readings considered.)
# The rows this section will check, computed up front -- cheap (local files
# only: hurl/topology.json + out/join/*.json, no Docker, no join-api) -- so
# both _selection_touches_27() below and the actual check loop read the same
# data once. Every currently joined member (origin: joined in
# hurl/topology.json, discovered the same generic way acceptance/member.md
# already discovers any member) that has published a service with a
# non-empty access: list. A service with an EMPTY access: list has nobody to
# authorize -- there is nothing for the r1 clause to prove that member.md's
# own exactness check (no subjects at all) does not already prove, so it is
# skipped, not failed. If no member has joined yet, or nobody who has joined
# has published anything, this produces zero rows and the section passes
# vacuously -- there is nothing wrong with a federation nobody has joined.
#
# The endpoint each row calls comes from out/join/<id>.json's
# endpoint_baseline (join-b Task 5's fix to validate.validate() -- it used to
# discard the OpenAPI document check 9 fetches, so nothing preserved its
# endpoint set past submission). That is a deliberate choice, not an
# oversight: hurl/topology.json's services carry only {code, access}, and a
# joined member's spec_url is an internal linkup-network hostname
# (app-<key>:8000) this host-side script cannot reach directly -- only
# join-api's own container, on that network, ever fetched it, at submission
# time. A member with no ACTIVE out/join record (joined by hand via
# prompts/member.md rather than through the API) has no such baseline and is
# skipped with a logged reason -- this section proves the JOIN API's own
# effect, module 2.7, not every possible way a member can join.
mapfile -t _27_ROWS < <(python3 - "$PACK_DIR/hurl/topology.json" "$OUT_DIR/join" <<'PY'
import json, pathlib, sys

topo = json.load(open(sys.argv[1]))
join_dir = pathlib.Path(sys.argv[2])
instance, mclass = topo["instance"], topo["member_class"]
subs = topo["subsystems"]

# The most recently-submitted ACTIVE out/join/*.json record per member code
# (upper-cased) -- there should be at most one per key in practice, nothing
# enforces that, so pick the newest on ambiguity rather than assume.
baselines: dict[str, dict] = {}
if join_dir.is_dir():
    for f in sorted(join_dir.glob("*.json")):
        try:
            rec = json.loads(f.read_text())
        except Exception:
            continue
        if rec.get("state") != "ACTIVE":
            continue
        rec_code = (rec.get("payload") or {}).get("code")
        if not rec_code:
            continue
        prev = baselines.get(rec_code.upper())
        if prev is None or rec.get("submitted_at", "") > prev.get("submitted_at", ""):
            baselines[rec_code.upper()] = rec

for sub in subs:
    if sub.get("origin") != "joined":
        continue
    code = sub["member_code"]
    baseline_rec = baselines.get(code.upper())
    for svc in sub.get("services") or []:
        access = svc.get("access") or []
        if not access:
            continue  # nobody to authorize; member.md already proves "no subjects"
        if baseline_rec is None:
            print(f"no ACTIVE out/join record for {code} -- endpoint unknown from the host, skipping {svc['code']}", file=sys.stderr)
            continue
        paths = (baseline_rec.get("endpoint_baseline") or {}).get(svc["code"]) or []
        # Prefer a path with no {param} -- a collection GET is far likelier
        # to return 2xx unconditionally than one needing a real record id
        # this script has no way to know.
        plain = [p for p in paths if "{" not in p]
        endpoint = (plain or paths or [None])[0]
        if endpoint is None:
            print(f"no endpoint in {code}'s join-time baseline for {svc['code']}, skipping", file=sys.stderr)
            continue

        good_str = access[0]  # "PROGRESSA/GOV/<CODE>/<SUBSYSTEM>"
        good_pair = ":".join(good_str.split("/")[-2:])

        bad = next(
            (o for o in subs
             if o["id"] != sub["id"]
             and f"{instance}/{mclass}/{o['member_code']}/{o['subsystem_code']}" not in access),
            None,
        )
        if bad is None:
            print(f"no other subsystem exists to act as an unauthorized caller for {code}/{svc['code']}, skipping", file=sys.stderr)
            continue
        bad_str = f"{instance}/{mclass}/{bad['member_code']}/{bad['subsystem_code']}"
        bad_pair = f"{bad['member_code']}:{bad['subsystem_code']}"

        r1_path = f"/r1/{instance}/{mclass}/{code}/{sub['subsystem_code']}/{svc['code']}{endpoint}"
        print(f"{sub['hosted_on']}\t{code}\t{svc['code']}\t{good_str}\t{good_pair}\t{bad_str}\t{bad_pair}\t{r1_path}")
PY
)

# The real ids this section can emit -- 2.7.1 always, plus one 2.7.r1(...)/
# 2.7.deny(...) pair per row above. Built before deciding whether to run, so
# _selection_touches_27() can test each one with _id_matches() itself (the
# file's own hierarchical-prefix rule, ~line 27) instead of a parallel,
# narrower `case` pattern -- found in review: the earlier version only
# recognised a SELECT_ARG that itself started with "2.7", so a coarser
# `--only 2` (which _id_matches() says SHOULD match every id under module 2,
# same as it already does for 2.1/2.6) silently skipped this whole section
# with no SKIP log line to say so.
_27_IDS=(2.7.1)
for _row in "${_27_ROWS[@]}"; do
  IFS=$'\t' read -r _ _code _svc _ _ _ _ _ <<<"$_row"
  _27_IDS+=("2.7.r1(${_code}.${_svc})" "2.7.deny(${_code}.${_svc})")
done

_selection_touches_27() {
  [ "$SELECT_MODE" = all ] && return 0
  local id
  for id in "${_27_IDS[@]}"; do
    [ "$SELECT_MODE" = from ] && [ "$_FROM_REACHED" = 1 ] && return 0
    _id_matches "$id" "$SELECT_ARG" && return 0
  done
  return 1
}

if _selection_touches_27; then
  # join-api must come back down even if a check below fails -- check()'s
  # failure path runs fail() (lib-core.sh), which exits the whole script
  # directly rather than returning here, so the plain "down" call at the end
  # of this block would never run on failure without this trap. Scoped to
  # this block only (set right before "up", cleared right after this
  # block's own clean "down") so a normal successful run does not double-
  # stop it, and nothing outside this section is affected -- acceptance.sh
  # sets no other EXIT trap.
  trap '"$(dirname "$0")/join.sh" down' EXIT
  "$(dirname "$0")/join.sh" up

  check_271() { curl -sf "http://localhost:8091/health" | jq -e '.status=="ok"' >/dev/null; }
  check 2.7.1 "join-api deploys and reports healthy" check_271

  for _row in "${_27_ROWS[@]}"; do
    IFS=$'\t' read -r provider_host code svc client_header good_pair bad_header bad_pair r1_path <<<"$_row"
    GOOD_SS=${HOST_SS[$good_pair]:-}
    BAD_SS=${HOST_SS[$bad_pair]:-}
    if [ -z "$GOOD_SS" ] || [ -z "$BAD_SS" ]; then
      log "SKIP 2.7 r1(${code}.${svc}) -- ${good_pair} or ${bad_pair} not in this deployment's HOST_SS"
      continue
    fi
    GOOD_REST="http://localhost:${SS_REST[$GOOD_SS]}"
    BAD_REST="http://localhost:${SS_REST[$BAD_SS]}"

    check_r1_ok() {
      local http_code
      http_code=$(curl -sk -o /dev/null -w '%{http_code}' -H "X-Road-Client: $client_header" "$GOOD_REST$r1_path")
      [[ "$http_code" =~ ^2[0-9][0-9]$ ]]
    }
    check_r1_ok_retry() { retry 12 5 "${svc} r1 settled" check_r1_ok; }
    check "2.7.r1(${code}.${svc})" "${client_header} r1 call to ${svc} returns 2xx" check_r1_ok_retry

    check_r1_denied() {
      curl -sk -H "X-Road-Client: $bad_header" "$BAD_REST$r1_path" \
        | jq -e '.type == "Server.ServerProxy.AccessDenied"' >/dev/null
    }
    check "2.7.deny(${code}.${svc})" "${bad_header} (via its own SS $BAD_SS) denied by the provider ACL" check_r1_denied
  done

  "$(dirname "$0")/join.sh" down
  trap - EXIT
fi

if [ "$SELECT_MODE" = from ] && [ "$_FROM_REACHED" = 0 ]; then
  fail "--from $SELECT_ARG matched none of this run's check ids -- nothing ran. Ids today: 2.1, 2.x(<MEMBER:SUBSYSTEM>), 2.x.acl(<service>), 2.6.1-2.6.5, 2.7.1, 2.7.r1(<member>.<service>), 2.7.deny(<member>.<service>)."
fi
if [ "$SELECT_MODE" != all ] && [ "$_SELECTED_COUNT" = 0 ]; then
  fail "--$SELECT_MODE $SELECT_ARG matched none of this run's check ids -- nothing ran. Ids today: 2.1, 2.x(<MEMBER:SUBSYSTEM>), 2.x.acl(<service>), 2.6.1-2.6.5, 2.7.1, 2.7.r1(<member>.<service>), 2.7.deny(<member>.<service>)."
fi

if [ "$SELECT_MODE" = all ]; then
  log "ACCEPTANCE GREEN — the framework runs (mark modules VERIFIED via kp-solution-verify)"
else
  log "ACCEPTANCE GREEN for the SELECTED checks only (--$SELECT_MODE $SELECT_ARG, $_SELECTED_COUNT check(s)) -- this is NOT a full pass, do not mark modules VERIFIED from this alone"
fi
