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
# Ids today are 2.1, 2.x.addons(<host>), 2.x(<MEMBER:SUBSYSTEM>),
# 2.x.acl(<service>), 2.x.catalogue(<service-id>), 2.6.1-2.6.5, 2.7.1,
# 2.7.r1(<member>.<service>), 2.7.deny(<member>.<service>),
# 2.7.catalogue(<member>.<service>), 2.7.unjoin(<member>),
# 2.7.unjoin.catalogue(<member>) and 2.7.unjoin.topology
# (member-parameterisation generalised what used to be
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
CS_KEY=$(api_key ${XROAD_BIND}:4000 xrd secret)
# Expected identity comes from the topology this suite already reads, never
# from a literal here (manifest<->topology agreement is check_scenarios.py's).
EXP_INSTANCE=$(jq -r .instance "$PACK_DIR/hurl/topology.json")
EXP_CLASS=$(jq -r .member_class "$PACK_DIR/hurl/topology.json")
check_21() {  # paths confirmed live at P0
  api GET ${XROAD_BIND}:4000 "$CS_KEY" /initialization/status \
    | jq -e --arg inst "$EXP_INSTANCE" '.instance_identifier==$inst' >/dev/null &&
  api GET ${XROAD_BIND}:4000 "$CS_KEY" /member-classes \
    | jq -e --arg cls "$EXP_CLASS" 'map(.code)|index($cls)!=null' >/dev/null
}
# Description says only what check_21 asserts. The trust services
# (Test CA, OCSP, TSA) are registered by hurl/templates/01-cs-trust-services.hurl.tmpl
# at deploy time, but nothing here reads them back -- acceptance/federation-core.md.
check 2.1 "instance $EXP_INSTANCE, member class $EXP_CLASS" check_21

# ---- add-ons: operational + environmental monitoring ---------
# Server-level, not client-level -- xroad-monitor (environmental) and
# xroad-opmonitor (operational) ship pre-installed and supervisord-managed on
# the full (non-slim) Sidecar image every Security Server in this pack already
# runs (docker-compose.yml's x-sidecar anchor, tests/test_addons.py's static
# guard). Checked per SERVER (SS_ORDER, generated from configs/ + manifest.yaml
# same as everything else in this file), not per member/subsystem pair: an
# add-on is a property of the Security Server a member is hosted on, and
# SS_ORDER already includes any joined member's own server -- a hosted member
# needs no separate check, its host is already in this loop.
check_addons_running() {  # $1 = SS container/host name
  # `supervisorctl status` itself exits non-zero whenever ANY managed
  # process is not RUNNING -- xroad-autologin is a one-shot unlock that
  # legitimately EXITED once the software token is up, so its exit code
  # cannot gate this check; only grep's own match/no-match result can.
  local sup_status
  sup_status=$(docker exec "$1" supervisorctl status 2>/dev/null; true)
  printf '%s\n' "$sup_status" | grep -qE '^xroad-monitor\s+RUNNING' &&
  printf '%s\n' "$sup_status" | grep -qE '^xroad-opmonitor\s+RUNNING'
}
for ss_host in "${SS_ORDER[@]}"; do
  check_ss_addons() { retry 12 5 "${ss_host} add-ons RUNNING" check_addons_running "$ss_host"; }
  check "2.x.addons(${ss_host})" "operational + environmental monitoring add-ons RUNNING on ${ss_host}" check_ss_addons
done

# ---- 2.2–2.5 member registrations & services --------------------------------
# Every subsystem HOST_SS names, REGISTERED on the SS that hosts it -- covers
# whatever member set is actually deployed (hurl/topology.sh, member-
# parameterisation), not a fixed list of four. PDGA:MANAGEMENT is
# HOST_SS's one non-member entry (the federation owner's own management
# subsystem, added by a different flow during 10-ss-pdga.hurl) -- excluded
# here, same scope as before this generalisation.
check_client_registered() {  # $1 = MEMBER:SUBSYSTEM
  local ss=${HOST_SS[$1]} sub=${1##*:}
  local key; key=$(api_key "${XROAD_BIND}:${SS_UI[$ss]}" "${XROAD_ADMIN_USER}" "${XROAD_ADMIN_PASSWORD}")
  api GET "${XROAD_BIND}:${SS_UI[$ss]}" "$key" /clients \
    | jq -e --arg s "$sub" '.[]|select(.subsystem_code==$s)|.status=="REGISTERED"' >/dev/null
}
# Registration status is global-conf propagation, same asynchrony as the Hurl
# runner itself -- confirmed at P5: a cold reproducibility run hit
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
# P0 -- GET /clients/{id}/service-clients lists every subject
# granted ANY access on that client; GET .../service-clients/{subject}/
# access-rights lists which service codes that subject holds. Exactness needs
# both, for EVERY service -- including a service with an EMPTY access: list,
# which must have NO subjects at all (pemis-api was this pack's example of
# one, until MoEYS was retired; no canonical service is
# empty today, but a future joined member's could be). That case was
# previously unchecked entirely; this loop covers it as a natural consequence
# of being generic rather than as a bespoke third check.
#
# The dataset comes from hurl/topology.json, not a second read of
# configs/member-*/*.yaml -- topology.json already carries each subsystem's
# hosted_on and each service's code+access (member-parameterisation),
# so this is the one place that data is read for this purpose.
check_acl_exact() {  # $1 = SS hosting the client, $2 = client id, $3 = service code, $4 = expected subjects (JSON array)
  local ss=$1 client_id=$2 svc=$3 want_json=$4
  local key; key=$(api_key "${XROAD_BIND}:${SS_UI[$ss]}" "${XROAD_ADMIN_USER}" "${XROAD_ADMIN_PASSWORD}")
  api GET "${XROAD_BIND}:${SS_UI[$ss]}" "$key" "/clients/${client_id}/service-clients" \
    | jq -e --argjson want "$want_json" '([.[].id] | sort) == ($want | sort)' >/dev/null || return 1
  local subj
  for subj in $(printf '%s' "$want_json" | python3 -c "import json,sys; print('\n'.join(json.load(sys.stdin)))"); do
    api GET "${XROAD_BIND}:${SS_UI[$ss]}" "$key" "/clients/${client_id}/service-clients/${subj}/access-rights" \
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

# ---- the service catalogue: every entry the aggregate claims is really there -
# onboarding/catalogue.yaml names, per published service, the file holding its
# catalogue entry and the file holding its signed SLA. A DANGLING LINK is the
# specific failure this artefact exists to prevent -- an SLA reachable from
# the member and not from the service is the orphan-SLA problem in smaller
# form, and a link that resolves to nothing is that problem with a link on top
# of it. So that is the specific thing asserted, per service.
#
# Cross-artefact, not self-checking: the aggregate is derived from
# configs/member-*/ and the entries from the join payload, so this compares
# two artefacts rather than a generator against its own output.
#
# A member with no onboarding/<key>/ at all (a config added by hand rather
# than through the join API or scripts/render-onboarding.sh) is skipped with a
# logged reason -- there is no record to have a dangling link in.
while IFS=$'\t' read -r svc_id entry sla; do
  check_catalogue_entry() {
    [ -f "$PACK_DIR/$entry" ] || { log "  missing catalogue entry: $entry"; return 1; }
    grep -q "grants nothing" "$PACK_DIR/$entry" || {
      log "  $entry does not say that publication is not permission"; return 1; }
    [ "$sla" = "-" ] && return 0
    [ -f "$PACK_DIR/$sla" ] || { log "  $entry links an SLA that is not there: $sla"; return 1; }
  }
  check "2.x.catalogue(${svc_id})" "${svc_id}'s catalogue entry exists and its SLA link resolves" check_catalogue_entry
done < <(python3 - "$PACK_DIR" <<'PY'
import pathlib, sys, yaml

pack = pathlib.Path(sys.argv[1])
doc = yaml.safe_load((pack / "onboarding" / "catalogue.yaml").read_text())
for svc in doc.get("services") or []:
    key = svc["provider"]["key"]
    if not (pack / "onboarding" / key).is_dir():
        print(f"{key} has no onboarding/{key}/ record -- registered outside the join API "
              f"and never rendered; skipping {svc['id']}", file=sys.stderr)
        continue
    print(f"{svc['id']}\t{svc['entry']}\t{svc['sla'] or '-'}")
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
# from configs/x-road-bus/once-only-exchange.yaml -- not restated as bash
# literals. Its ENTRYPOINT fields stay unread on purpose: they are static
# ("http://ss-pnea:8080") and only correct for a member on its own server --
# a hosted member (security_server.hosted_on) is reachable through its
# host's server instead, so the entrypoint is resolved from HOST_SS/SS_REST
# instead, the same live-confirmed trap apps/console/truth.py already
# documents and avoids.
mapfile -t _exchange < <(python3 - "$PACK_DIR/configs/x-road-bus/once-only-exchange.yaml" <<'PY'
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
PNEA_REST="http://${XROAD_BIND}:${SS_REST[${HOST_SS[$CONSUMER_MEMBER_SUBSYSTEM]}]}"
# Negative check goes through the SS that hosts the unauthorised caller (its
# own server -- so the denial genuinely comes from the provider-side ACL, not
# from the consumer's SS rejecting an unknown client). If that caller were
# ever hosted (security_server.hosted_on), this would resolve to the host's
# shared server instead.
BAD_SS=${HOST_SS[$BAD_MEMBER_SUBSYSTEM]}
BAD_REST="http://${XROAD_BIND}:${SS_REST[$BAD_SS]}"

ID_URL="$PNEA_REST${ID_PATH_TMPL%/\{nin\}}"
EN_URL="$PNEA_REST${EN_PATH_TMPL%/\{nin\}}"

# Same asynchronous-propagation risk as the registration-status checks above
# (retry, don't fail): confirmed live at P5 -- a fresh deploy's
# first exchange call can hit a transient failure moments before it settles.
# retry() itself only reports success/failure and discards stdout, so capture
# the body with a small inline retry instead.
#
# The success criterion is "the body parses as JSON", NOT curl's exit code.
# curl -sf exits 0 on an X-Road REST response that is
# 200 with an empty or not-yet-valid body, which a fresh deploy really does
# return for a few seconds -- docs/production-delta.md's "Lite profile's full
# cycle, measured" recorded it happening on 2 of 3 fresh deploys and left it
# as a follow-up. It surfaces one check LATER, as a JSONDecodeError traceback
# out of scripts/assert_record.py under 2.6.2, which reads as a data bug in a
# federation that is merely still settling -- and it fails `--full`, the
# unattended reproducibility proof, for a reason that retrying here fixes.
fetch_retry() {
  local url=$1 i out
  for ((i=1; i<=12; i++)); do
    # `jq -e .` also exits non-zero on a body that is valid JSON `null` or
    # `false`. Deliberate and harmless here: every URL this is called with is
    # a record fetch that returns an object, so `null` is a not-ready
    # response, not a legitimate value to pass through -- and a stuck one
    # ends in fail() with the URL rather than in a confusing parse error
    # downstream. Worth knowing before reusing this helper for an endpoint
    # where `null` or `false` IS the answer.
    if out=$(curl -sf -H "$CLIENT" "$url" 2>/dev/null) &&
       printf '%s' "$out" | jq -e . >/dev/null 2>&1; then printf '%s' "$out"; return 0; fi
    # >&2 IS LOAD-BEARING, and this is the one function in the file where it
    # is: lib-core.sh's log() writes to STDOUT, and this function's stdout is
    # the value itself (`id_json=$(fetch_retry ...)`). Without the redirect a
    # single retry prepends "\033[1;34m[kp2]\033[0m waiting: ..." to the
    # captured body, and the next check dies on `json.loads` at char 0 --
    # which reads as the federation returning garbage rather than as this
    # line. Latent since fetch_retry was written (no retry ever fired before
    # the JSON condition above made one possible); found the moment it did.
    log "waiting: $url ($i/12)" >&2; sleep 5
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
  python3 - "$id_json" "$en_json" \
           "$PACK_DIR/configs/x-road-bus/once-only-exchange.yaml" <<'PY'
import json, sys
import yaml
idr, enr = json.loads(sys.argv[1]), json.loads(sys.argv[2])
# The form is the declared exchange, not a copy of it: editing asked_once
# without editing the specs (or the reverse) now fails here.
ao = yaml.safe_load(open(sys.argv[3]))["exchange"]["asked_once"]
citizen = set(ao["citizen_provides"])
form = citizen | set(ao["prefilled_from_bus"])
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
               # Exact fault confirmed live at P0:
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

check_266() {  # On the pack's headline exchange: the response carries exactly
               # the fields its own OpenAPI contract declares -- not merely
               # "seeded values match" (2.6.2), which cannot see a response
               # that adds a field the CSV carries and the contract withholds
               # (purpose limitation failing silently). Declared/required read
               # straight off the in-repo spec, mirroring
               # apps/join-api/validate.py's contract_fields() -- no HTTP fetch
               # needed, the canonical specs are already on disk.
  python3 - "$PACK_DIR/apps/specs/pnia-identity.openapi.yaml" "$id_json" \
            "$PACK_DIR/apps/specs/plr-enrolment.openapi.yaml" "$en_json" <<'PY'
import json, sys
import yaml

def contract_fields(spec_path):
    spec = yaml.safe_load(open(spec_path))
    schema = next(iter(spec["paths"].values()))["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    return set(schema.get("properties", {})), set(schema.get("required", []))

for spec_path, body_json in ((sys.argv[1], sys.argv[2]), (sys.argv[3], sys.argv[4])):
    declared, required = contract_fields(spec_path)
    returned = set(json.loads(body_json))
    undeclared = sorted(returned - declared)
    missing = sorted(required - returned)
    assert not undeclared and not missing, f"{spec_path}: undeclared={undeclared} missing={missing}"
PY
}
check 2.6.6 "field conformance — both responses carry exactly the fields their contract declares (G5.9)" check_266

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
# acceptance/join-member.md's clause: a real r1 call through an authorized consumer's
# own Security Server against a joined member's service returns 2xx, and the
# same call from an unauthorized subsystem is denied by the provider ACL --
# the one assertion in this suite that would catch a registry-perfect-but-
# dead member. Registry state itself (REGISTERED, exact
# ACL) is NOT re-asserted here -- acceptance/member.md's existing generic
# checks already cover any member, joined or not; duplicating them here was
# explicitly ruled out ("do not duplicate").
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
# The rows this section will check, computed up front -- cheap (local reads
# only: hurl/topology.json + a read-only query against the join store, no
# Docker, no running join-api) -- so both _selection_touches_27() below and
# the actual check loop read the same data once. Every currently joined
# member (origin: joined in hurl/topology.json, discovered the same generic
# way acceptance/member.md already discovers any member) that has published
# a service with a non-empty access: list. A service with an EMPTY access:
# list has nobody to authorize -- there is nothing for the r1 clause to
# prove that member.md's own exactness check (no subjects at all) does not
# already prove, so it is skipped, not failed. If no member has joined yet,
# or nobody who has joined has published anything, this produces zero rows
# and the section passes vacuously -- there is nothing wrong with a
# federation nobody has joined.
#
# The r1 call targets the SERVICE ROOT, not a specific operation path --
# mirrors apps/join-api/job.py's own _r1_target/ss.r1_verify step exactly
# (same file's ponytail comment explains why: "what this call has to prove
# is the registry-perfect-but-dead case ... any non-X-Road response proves
# that, including a backend 404"). This section originally preferred a
# parameter-free path from the join-time record's endpoint_baseline, falling
# back to an UNSUBSTITUTED "{param}" path segment otherwise -- found live,
# join-b's own first real join (PTSB): every service this pack
# publishes, canonical or joined, is GET-by-key shaped, so there is never a
# parameter-free path to prefer, and the fallback path can never return 2xx
# against any real backend. Fixed by asking the same question job.py already
# answers correctly for exactly this call shape: not "does this specific
# resource exist" (this script has no way to know a real record id), but
# "did the call traverse X-Road and reach a backend at all" -- see
# check_r1_ok below, which checks for an X-Road fault the same way
# apps/join-api/job.py's _default_r1_call does, not a literal 2xx. A member
# with no ACTIVE join-store record (joined by hand via prompts/member.md
# rather than through the API) is skipped with a logged reason -- this
# section proves the JOIN API's own effect, module 2.7, not every possible
# way a member can join.
mapfile -t _27_ROWS < <(python3 - "$PACK_DIR/hurl/topology.json" "$OUT_DIR/join-store/join-store.sqlite3" <<'PY'
import json, pathlib, sqlite3, sys

topo = json.load(open(sys.argv[1]))
db_path = pathlib.Path(sys.argv[2])
instance, mclass = topo["instance"], topo["member_class"]
subs = topo["subsystems"]

# The most recently-submitted ACTIVE join-store record per member code
# (upper-cased) -- there should be at most one per key in practice, nothing
# enforces that, so pick the newest on ambiguity rather than assume. Opened
# read-only (file:...?mode=ro): a WAL-mode database needs no running writer
# for a read, same as scripts/member.sh drift's own query.
baselines: dict[str, dict] = {}
if db_path.is_file():
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    for (record_json,) in conn.execute("SELECT record FROM requests WHERE state = 'ACTIVE'"):
        rec = json.loads(record_json)
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
            print(f"no ACTIVE join-store record for {code} -- skipping {svc['code']}", file=sys.stderr)
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

        r1_path = f"/r1/{instance}/{mclass}/{code}/{sub['subsystem_code']}/{svc['code']}/"
        # The field-conformance check below needs the service's own contract
        # -- the payload's spec_url, exactly as the join recorded it
        # (check_r1_fields resolves this to an in-repo
        # apps/specs/*.openapi.yaml path, no HTTP fetch either).
        payload_svc = next(
            (s for s in (baseline_rec.get("payload") or {}).get("services") or [] if s.get("code") == svc["code"]),
            None,
        )
        spec_url = (payload_svc or {}).get("spec_url", "")
        print(f"{sub['hosted_on']}\t{code}\t{svc['code']}\t{good_str}\t{good_pair}\t{bad_str}\t{bad_pair}\t{r1_path}\t{spec_url}")
PY
)

# The real ids this section can emit -- 2.7.1 always, plus one 2.7.r1(...)/
# 2.7.deny(...) pair per row above. Built before deciding whether to run, so
# _selection_touches_27() can test each one with _id_matches() itself (the
# file's own hierarchical-prefix rule) instead of a parallel, narrower `case`
# pattern -- the earlier version only recognised a SELECT_ARG that itself
# started with "2.7", so a coarser `--only 2` (which _id_matches() says SHOULD
# match every id under module 2,
# same as it already does for 2.1/2.6) silently skipped this whole section
# with no SKIP log line to say so.
_27_IDS=(2.7.1)
for _row in "${_27_ROWS[@]}"; do
  IFS=$'\t' read -r _ _code _svc _ _ _ _ _ _ <<<"$_row"
  _27_IDS+=("2.7.r1(${_code}.${_svc})" "2.7.deny(${_code}.${_svc})" "2.7.fields(${_code}.${_svc})"
            "2.7.catalogue(${_code}.${_svc})")
done

# ---- 2.7.unjoin -- the un-join transition -----------------------------
# acceptance/join-member.md's un-join clause: five assertions, which are exactly
# docs/decisions/xroad-770-notes.md #11's closing claims. Discovered the same generic,
# vacuous-by-default way the r1 rows above are: the newest RETIRED
# join-store record per member code. A federation nobody has un-joined
# produces zero rows and this section does not run at all.
#
# Deliberately reads the join store rather than anything in hurl/: a retired
# member is GONE from hurl/topology.json (that is clause 5), so topology
# cannot be the discovery source the way it is for a live joined member.
#
# ...and the join store outlives the federation it describes:
# scripts/teardown.sh --purge tears down containers and volumes, never out/.
# A RETIRED record from a PREVIOUS federation would otherwise produce a row
# whose every clause passes trivially (CS list empty, host token has no such
# key, r1 returns UnknownMember) -- four green checks asserting nothing
# about the federation now running, the same vacuous-PASS shape the sibling
# topology check above was just fixed for. So a record only counts if it was
# retired AFTER this federation was deployed: hurl/run-linkup.sh writes
# deploy_start (epoch) to out/deploy-timings.txt on every deploy, and job.py
# stamps retired_at when the walk completes. No anchor, or a record older
# than it -> SKIP with a reason, never a PASS.
#
# Purging the join store in teardown.sh instead was the alternative
# considered and rejected: those same records are the ACTIVE join baselines
# `scripts/member.sh drift` needs for its join-time endpoint baseline, and
# that the 2.7.r1 rows above need to exist at all -- deleting them on
# --purge would trade one vacuous PASS for silently skipping every r1 row
# after a purge-and-redeploy, which is the pack's own P5 reproducibility
# flow.
#
# ponytail: a plain `teardown.sh` (no --purge) followed by deploy.sh keeps the
# volumes but rewrites deploy_start, so a legitimate un-join from before that
# restart reads as stale and its row SKIPs. That is the safe direction (a
# logged SKIP, not a false PASS); anchor on the CS volume's CreatedAt instead
# if the restart-and-reassert flow ever matters.
mapfile -t _27_RETIRED < <(python3 - "$PACK_DIR/hurl/topology.json" "$OUT_DIR/join-store/join-store.sqlite3" "$OUT_DIR/deploy-timings.txt" <<'PY'
import datetime, json, pathlib, sqlite3, sys

topo = json.load(open(sys.argv[1]))
db_path = pathlib.Path(sys.argv[2])
timings = pathlib.Path(sys.argv[3])
instance, mclass = topo["instance"], topo["member_class"]
live = {s["member_code"].upper() for s in topo["subsystems"]}

deployed_at = None
if timings.is_file():
    for line in timings.read_text().splitlines():
        if line.startswith("deploy_start="):
            deployed_at = datetime.datetime.fromtimestamp(int(line.split("=", 1)[1]), datetime.timezone.utc)
if deployed_at is None:
    print(f"{timings} has no deploy_start -- cannot tell which federation a RETIRED record "
          f"describes; skipping every 2.7.unjoin row", file=sys.stderr)

newest: dict[str, dict] = {}
if db_path.is_file() and deployed_at is not None:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    for (record_json,) in conn.execute("SELECT record FROM requests WHERE state = 'RETIRED'"):
        rec = json.loads(record_json)
        code = ((rec.get("payload") or {}).get("code") or "").upper()
        if not code:
            continue
        try:
            retired_at = datetime.datetime.fromisoformat(rec["retired_at"])
            stale = retired_at < deployed_at
        except (KeyError, TypeError, ValueError):
            retired_at, stale = None, True
        if stale:
            print(f"{code} was retired {retired_at or 'at an unreadable time'}, before this federation "
                  f"was deployed ({deployed_at.isoformat()}) -- it never joined the federation now "
                  f"running, so its clauses would assert nothing; skipping", file=sys.stderr)
            continue
        prev = newest.get(code)
        if prev is None or rec.get("submitted_at", "") > prev.get("submitted_at", ""):
            newest[code] = rec

for code, rec in sorted(newest.items()):
    pay = rec["payload"]
    if code in live:
        # Retired once, joined again since. The record is history, not a
        # claim about now -- asserting absence would be wrong, not lenient.
        print(f"{code} has a RETIRED record but is live in hurl/topology.json again -- skipping", file=sys.stderr)
        continue
    svc = next((s for s in (pay.get("services") or []) if s.get("access")), None)
    if svc is None:
        # No authorised consumer -> no r1 clause. The registry clauses alone
        # would not prove the bus forgot the member, which is the one thing
        # this section exists for.
        print(f"{code} published no service with an access: list -- nothing for the r1 clause, skipping", file=sys.stderr)
        continue
    host = (pay.get("security_server") or {}).get("hosted_on") or ""
    if not host:
        print(f"{code} owned its own Security Server, destroyed with it -- clauses 2 and 3 "
              f"(host client list, host token) have nothing to read; 1, 4 and 5 still run",
              file=sys.stderr)
    subject = svc["access"][0]                       # PROGRESSA/GOV/<CODE>/<SUBSYSTEM>
    pair = ":".join(subject.split("/")[-2:])
    r1_path = f"/r1/{instance}/{mclass}/{code}/{pay['subsystem']}/{svc['code']}/"
    # The owner_id a SIGN certificate carries is MEMBER-level, not subsystem-
    # level ("PROGRESSA:GOV:PLR") -- hurl/templates/fragments/
    # PROBE_SS_SIGN_KEY.hurl.tmpl's own live-confirmed note.
    # "-" for the own-server case, never an EMPTY field: TAB is an IFS
    # WHITESPACE character, so bash's `read` collapses two consecutive tabs
    # into one and every later field lands in the wrong variable (found the
    # first time this ran against a real own-server retirement).
    print(f"{code}\t{host or '-'}\t{subject}\t{pair}\t{r1_path}\t{instance}:{mclass}:{code}")
PY
)
for _row in "${_27_RETIRED[@]}"; do
  IFS=$'\t' read -r _code _ _ _ _ _ <<<"$_row"
  _27_IDS+=("2.7.unjoin(${_code})" "2.7.unjoin.catalogue(${_code})")
done
if [ ${#_27_RETIRED[@]} -gt 0 ]; then _27_IDS+=(2.7.unjoin.topology); fi

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

  check_271() { curl -sf "http://${XROAD_BIND}:8091/health" | jq -e '.status=="ok"' >/dev/null; }
  check 2.7.1 "join-api deploys and reports healthy" check_271

  # The catalogue, read the way a member reads it -- through the API, with
  # the applicant token, not off the file the same process wrote. Comparing
  # a rendered file to the config it was rendered from would test the
  # renderer against itself; asking the running service what it publishes
  # asserts the property a joining body actually depends on.
  catalogue_json() {
    curl -sf -H "X-KP2-Console: 1" \
         -H "Authorization: Bearer ${KP2_JOIN_APPLICANT_TOKEN}" \
         "http://${XROAD_BIND}:8091/catalogue"
  }

  for _row in "${_27_ROWS[@]}"; do
    IFS=$'\t' read -r provider_host code svc client_header good_pair bad_header bad_pair r1_path spec_url <<<"$_row"
    GOOD_SS=${HOST_SS[$good_pair]:-}
    BAD_SS=${HOST_SS[$bad_pair]:-}
    if [ -z "$GOOD_SS" ] || [ -z "$BAD_SS" ]; then
      log "SKIP 2.7 r1(${code}.${svc}) -- ${good_pair} or ${bad_pair} not in this deployment's HOST_SS"
      continue
    fi
    GOOD_REST="http://${XROAD_BIND}:${SS_REST[$GOOD_SS]}"
    BAD_REST="http://${XROAD_BIND}:${SS_REST[$BAD_SS]}"

    # "2xx" per acceptance/join-member.md's prose meant, in practice, "the call
    # traversed X-Road and reached the backend" -- true for job.py's own
    # r1_verify (which passed) even though the service-root call above 404s
    # against every mock in this pack (none exposes a collection route, only
    # GET-by-key). Checked the same way apps/join-api/job.py's
    # _default_r1_call already does for this exact call shape: not the HTTP
    # status, but the ABSENCE of an X-Road fault body (a Server.*/Client.*
    # `type`) -- a plain backend 404 has no such field and correctly reads
    # as reachable; check_r1_denied below still requires the SPECIFIC
    # Server.ServerProxy.AccessDenied fault, so the two checks stay
    # unambiguously distinct regardless of this change.
    check_r1_ok() {
      local body is_fault
      body=$(curl -sk -H "X-Road-Client: $client_header" "$GOOD_REST$r1_path")
      is_fault=$(printf '%s' "$body" | jq -r '(.type // "") | test("^(Server|Client)\\.")' 2>/dev/null)
      [ "$is_fault" != "true" ]
    }
    check_r1_ok_retry() { retry 12 5 "${svc} r1 settled" check_r1_ok; }
    check "2.7.r1(${code}.${svc})" "${client_header} r1 call to ${svc} reaches the backend (no X-Road fault)" check_r1_ok_retry

    check_r1_denied() {
      curl -sk -H "X-Road-Client: $bad_header" "$BAD_REST$r1_path" \
        | jq -e '.type == "Server.ServerProxy.AccessDenied"' >/dev/null
    }
    check "2.7.deny(${code}.${svc})" "${bad_header} (via its own SS $BAD_SS) denied by the provider ACL" check_r1_denied

    # Field conformance on a JOINED member's own service, not just the two
    # canonical providers check_266 covers. Declared/required read straight
    # off the in-repo apps/specs/*.openapi.yaml -- no HTTP fetch, mirroring
    # apps/join-api/validate.py's contract_fields() -- resolved from
    # spec_url via docker-compose.yml's SPEC_FILE/CSV_FILE/KEY_FIELD (the
    # generic mock-registry pattern every joined member's demo backend
    # follows). A joined member with no such backend (spec_url not served by
    # this pack's own mock pattern) is skipped, not failed -- there is no
    # in-repo contract to check it against.
    check_r1_fields() {
      python3 - "$PACK_DIR" "$spec_url" "$GOOD_REST" "$r1_path" "$client_header" <<'PY'
import json, pathlib, sys, urllib.parse
import urllib.request

import yaml

pack_dir, spec_url, good_rest, r1_path, client_header = sys.argv[1:6]
host = urllib.parse.urlparse(spec_url).hostname

compose = yaml.safe_load(open(f"{pack_dir}/docker-compose.yml"))
svc = (compose.get("services") or {}).get(host)
if svc is None:
    print(f"SKIP: no docker-compose.yml service named {host!r} for {spec_url}", file=sys.stderr)
    sys.exit(0)
env = svc.get("environment") or {}
spec_file = env.get("SPEC_FILE", "")
csv_file = env.get("CSV_FILE", "")
key_field = env.get("KEY_FIELD", "")
if not (spec_file.startswith("/specs/") and csv_file.startswith("/data/") and key_field):
    print(f"SKIP: {host!r} does not follow the generic mock-registry pattern", file=sys.stderr)
    sys.exit(0)

spec = yaml.safe_load(open(f"{pack_dir}/apps/specs/{spec_file.removeprefix('/specs/')}"))
path_tmpl, path_item = next(iter(spec["paths"].items()))
schema = path_item["get"]["responses"]["200"]["content"]["application/json"]["schema"]
declared = set(schema.get("properties", {}))
required = set(schema.get("required", []))

import csv as csv_module
with open(f"{pack_dir}/apps/data/{csv_file.removeprefix('/data/')}", newline="") as f:
    key = next(csv_module.DictReader(f))[key_field]

# r1_path is the service ROOT (the call clause above needs exactly that, and
# 404s by design). A key-specific call needs the resource path too, and that
# comes off the same contract the declared fields do -- "/awards/{nin}",
# "/persons/{nin}". Appending the key alone assumes every provider serves its
# records at the service root, which none of them do.
path_param = next(p["name"] for p in path_item["get"].get("parameters", []) if p.get("in") == "path")
resource = path_tmpl.lstrip("/").replace("{" + path_param + "}", key)

req = urllib.request.Request(f"{good_rest}{r1_path}{resource}", headers={"X-Road-Client": client_header})
with urllib.request.urlopen(req, timeout=10) as resp:
    body = json.loads(resp.read())

returned = set(body)
undeclared = sorted(returned - declared)
missing = sorted(required - returned)
assert not undeclared and not missing, f"{spec_url}: undeclared={undeclared} missing={missing}"
PY
    }
    check "2.7.fields(${code}.${svc})" "${client_header} r1 call to ${svc} carries exactly its contract's declared fields (G5.9)" check_r1_fields

    # A member that joined and reached ACTIVE is discoverable: its service is
    # in the catalogue the API serves, without anyone having been asked.
    # The r1 path is the service id with an /r1/ prefix, so the id this row
    # already carries is the id the catalogue must list -- no second
    # construction of it here to disagree with the first.
    svc_id=${r1_path#/r1/}; svc_id=${svc_id%/}
    check_catalogue_lists() {
      catalogue_json | jq -e --arg id "$svc_id" '[.services[].id] | index($id) != null' >/dev/null
    }
    check "2.7.catalogue(${code}.${svc})" "${code}'s ${svc} appears in GET /catalogue" check_catalogue_lists
  done

  # -- the un-join transition (acceptance/join-member.md's un-join clause) ------------
  # Clauses 1-4 per retired member; clause 5 (byte-identical topology) is a
  # property of the working tree, not of a member, so it is asserted once.
  for _row in "${_27_RETIRED[@]}"; do
    IFS=$'\t' read -r code host_dns client_header good_pair r1_path gone_owner <<<"$_row"
    GOOD_SS=${HOST_SS[$good_pair]:-}
    if [ -z "$GOOD_SS" ]; then
      log "SKIP 2.7.unjoin(${code}) -- ${good_pair}, its authorised consumer before it left, is not in this deployment's HOST_SS"
      continue
    fi
    GOOD_REST="http://${XROAD_BIND}:${SS_REST[$GOOD_SS]}"

    # Clause 1. Absence on the CS is an EMPTY LIST, not a 404 -- there is no
    # GET /subsystems/{id} on the Central Server at all (405), so this read
    # is the only viable one (docs/decisions/xroad-770-notes.md #11 finding 4).
    check_unjoin_cs() {
      api GET ${XROAD_BIND}:4000 "$CS_KEY" "/clients?q=${code}" | jq -e '.clients == []' >/dev/null
    }
    # Clauses 2 and 3, both on the hosting Security Server -- skipped for an
    # own-server member, whose server went with it (retire_instruction()'s
    # docker rm -f / docker volume rm). Clause 3's second half is the one
    # that matters most: a shared host (security_server.hosted_on) carries
    # several keys ALL labelled "Sign key", so "this member's key is gone" is only
    # half the assertion -- every other hosted member's must still be there,
    # or a reversal that matched on the label deleted the wrong agency's
    # signing key (docs/decisions/xroad-770-notes.md #11, "What happens to a hosted
    # member's SIGN key").
    check_unjoin_host() {
      [ "$host_dns" != "-" ] || return 0   # own-server: its server is gone with it
      local ui=${SS_UI[$host_dns]:-} key token want
      [ -n "$ui" ] || return 1
      key=$(api_key "${XROAD_BIND}:${ui}" "${XROAD_ADMIN_USER}" "${XROAD_ADMIN_PASSWORD}") || return 1
      api GET "${XROAD_BIND}:${ui}" "$key" /clients \
        | jq -e --arg c "$code" 'map(select(.member_code == $c)) | length == 0' >/dev/null || return 1
      token=$(api GET "${XROAD_BIND}:${ui}" "$key" /tokens/0) || return 1
      want=$(python3 - "$PACK_DIR/hurl/topology.json" "$host_dns" <<'PY'
import json, sys
topo = json.load(open(sys.argv[1]))
codes = sorted({s["member_code"] for s in topo["subsystems"] if s["hosted_on"] == sys.argv[2]})
print(json.dumps([f"{topo['instance']}:{topo['member_class']}:{c}" for c in codes]))
PY
)
      printf '%s' "$token" | jq -e \
        --arg gone "$gone_owner" --argjson want "$want" '
          ([.keys[].certificates[]?.owner_id] | unique) as $have
          | ($have | index($gone) | not) and (($want - $have) == [])' >/dev/null
    }
    # Clause 4. The bus itself, not a registry: the departed member's service
    # is unreachable through a consumer that WAS authorised. Retried on the
    # same terms as every other propagation wait in this file -- the proxy's
    # own authorisation cache lags a live change by seconds (#6).
    check_unjoin_r1() {
      curl -sk -H "X-Road-Client: $client_header" "$GOOD_REST$r1_path" \
        | jq -e '.type == "Server.ClientProxy.UnknownMember"' >/dev/null
    }
    check_unjoin() {
      check_unjoin_cs || { log "  2.7.unjoin(${code}): still present on the Central Server"; return 1; }
      check_unjoin_host || { log "  2.7.unjoin(${code}): residue on ${host_dns} (client list or token)"; return 1; }
      retry 12 5 "${code} r1 unreachable" check_unjoin_r1
    }
    _where=$([ "$host_dns" = "-" ] && echo "its own (destroyed) server" || echo "$host_dns")
    check "2.7.unjoin(${code})" "${code} is gone from the CS, from ${_where} and from the bus" check_unjoin

    # GX asks the operator to remove the departed member's catalogue entry.
    # There is nothing to remove: the catalogue is derived from the member
    # configs, and the un-join took that config away, so the next read simply
    # does not find its services. This is that third of GX proven live -- the
    # evidence for it is this check, not an argument in a document.
    check_catalogue_forgot() {
      catalogue_json | jq -e --arg c "$code" \
        '[.services[] | select(.provider.code == $c)] | length == 0' >/dev/null
    }
    check "2.7.unjoin.catalogue(${code})" "${code}'s services are gone from GET /catalogue, by derivation and not by a delete path" check_catalogue_forgot
  done

  if [ ${#_27_RETIRED[@]} -gt 0 ]; then
    # Clause 5. The Global Constraint: a join-then-unjoin
    # cycle leaves hurl/topology.json byte-identical. The single deployment
    # golden IS the "before the join" state -- it is generated from the
    # canonical member set alone (tests/test_golden.py), so with every
    # joined member gone the two must be the same bytes.
    GOLDEN_TOPO="$PACK_DIR/tests/golden/deployment/topology.json"
    check_unjoin_topology() { cmp -s "$PACK_DIR/hurl/topology.json" "$GOLDEN_TOPO"; }
    # SKIP, not a vacuous PASS. With a member still joined, the golden
    # canonical topology is the wrong thing to compare against -- but
    # reporting PASS for a check that asserted nothing is how a green summary
    # comes to mean less than it looks like it does.
    if python3 -c 'import json,sys; sys.exit(0 if any(s.get("origin")=="joined" for s in json.load(open(sys.argv[1]))["subsystems"]) else 1)' "$PACK_DIR/hurl/topology.json"; then
      log "SKIP 2.7.unjoin.topology -- a member is still joined, so byte-identical to the canonical golden is the wrong expectation (not a failure)"
    else
      check 2.7.unjoin.topology "hurl/topology.json is byte-identical to $(basename "$(dirname "$GOLDEN_TOPO")")/topology.json after the un-join" check_unjoin_topology
    fi
  fi

  "$(dirname "$0")/join.sh" down
  trap - EXIT
fi

if [ "$SELECT_MODE" = from ] && [ "$_FROM_REACHED" = 0 ]; then
  fail "--from $SELECT_ARG matched none of this run's check ids -- nothing ran. Ids today: 2.1, 2.x.addons(<host>), 2.x(<MEMBER:SUBSYSTEM>), 2.x.acl(<service>), 2.x.catalogue(<service-id>), 2.6.1-2.6.5, 2.7.1, 2.7.r1(<member>.<service>), 2.7.deny(<member>.<service>), 2.7.catalogue(<member>.<service>), 2.7.unjoin(<member>), 2.7.unjoin.catalogue(<member>), 2.7.unjoin.topology."
fi
if [ "$SELECT_MODE" != all ] && [ "$_SELECTED_COUNT" = 0 ]; then
  fail "--$SELECT_MODE $SELECT_ARG matched none of this run's check ids -- nothing ran. Ids today: 2.1, 2.x.addons(<host>), 2.x(<MEMBER:SUBSYSTEM>), 2.x.acl(<service>), 2.x.catalogue(<service-id>), 2.6.1-2.6.5, 2.7.1, 2.7.r1(<member>.<service>), 2.7.deny(<member>.<service>), 2.7.catalogue(<member>.<service>), 2.7.unjoin(<member>), 2.7.unjoin.catalogue(<member>), 2.7.unjoin.topology."
fi

if [ "$SELECT_MODE" = all ]; then
  log "ACCEPTANCE GREEN — the framework runs (mark modules VERIFIED via kp-solution-verify)"
else
  log "ACCEPTANCE GREEN for the SELECTED checks only (--$SELECT_MODE $SELECT_ARG, $_SELECTED_COUNT check(s)) -- this is NOT a full pass, do not mark modules VERIFIED from this alone"
fi
