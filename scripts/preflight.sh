#!/usr/bin/env bash
# Check that this host has what the pack needs before an expensive deploy
# starts (D11, docs/notes/reviews/2026-08-01-branch-review.md). A minimal Ubuntu
# cloud image lacks jq, a SHA-256 tool under the name run-linkup.sh expects,
# a python3 with PyYAML, and docker compose as a v2 plugin unless it came
# from a Docker-ready marketplace image -- each of those used to fail
# separately, at the point of use, cryptically, six to fifteen minutes into
# a deploy. This checks; it never installs. Printing the install line is
# help -- running apt-get on someone's machine on their behalf is not.
#
#   scripts/preflight.sh
#
# Collects every failure and reports them together: four round trips to
# install four missing packages, one at a time, is the failure mode this
# script exists to avoid. The same applies to the .env check below -- a
# missing key kills a compose command mid-deploy, so it is worth naming here.
# Clock sync and RAM warn rather than fail: neither can be measured reliably
# enough to refuse a deploy on, so exit 0 still means "deployable".
set -uo pipefail
PACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "$(uname -s)" in
  Darwin) OS=macos ;;
  Linux)
    if [ -f /etc/os-release ] && grep -qiE '^id(_like)?=.*(debian|ubuntu)' /etc/os-release; then
      OS=debian
    else
      OS=linux
    fi
    ;;
  *) OS=unknown ;;
esac

# hint <debian-command> <macos-command>
hint() {
  case "$OS" in
    debian) echo "    $1" ;;
    macos)  echo "    $2" ;;
    *)      echo "    Debian/Ubuntu: $1"; echo "    macOS: $2" ;;
  esac
}

FAILURES=()

command -v docker >/dev/null 2>&1 || FAILURES+=("docker")
if command -v docker >/dev/null 2>&1 && ! docker compose version >/dev/null 2>&1; then
  FAILURES+=("compose")
fi
command -v jq   >/dev/null 2>&1 || FAILURES+=("jq")
command -v curl >/dev/null 2>&1 || FAILURES+=("curl")

if ! command -v python3 >/dev/null 2>&1; then
  FAILURES+=("python3")
elif ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
  FAILURES+=("python3-floor")
elif ! python3 -c 'import yaml' 2>/dev/null; then
  FAILURES+=("pyyaml")
fi

command -v sha256sum >/dev/null 2>&1 || command -v shasum >/dev/null 2>&1 || FAILURES+=("sha256")

# Checked against the interpreter actually running this script
# ($BASH_VERSINFO), not against /bin/bash -- on macOS /bin/bash is 3.2, but
# every script in this pack is #!/usr/bin/env bash, so what resolves via
# PATH is what matters. scripts/acceptance.sh's mapfile and
# hurl/topology.sh's declare -A both need bash 4+.
[ "${BASH_VERSINFO[0]}" -ge 4 ] || FAILURES+=("bash4")

# -- .env ---------------------------------------------------------------------
#
# The required key set is derived from docker-compose.yml's own ${VAR:?}
# interpolations, not from .env.example: those are the interpolations that
# actually abort a compose command, and compose expands them file-wide before
# any profile filtering, so one missing key stops the whole pack. A key added
# to compose later is caught here with no edit to this script.
ENV_PROBLEMS=()
ENV_FILE="$PACK_DIR/.env"
REQUIRED_KEYS=$(grep -oE '\$\{[A-Z0-9_]+:\?' "$PACK_DIR/docker-compose.yml" | sed 's/^\${//; s/:?$//' | sort -u)

if [ ! -f "$ENV_FILE" ]; then
  ENV_PROBLEMS+=("- .env not found -- run scripts/gen-secrets.sh")
else
  missing_keys=""
  for key in $REQUIRED_KEYS; do
    value=$(sed -n "s/^${key}=//p" "$ENV_FILE" | tail -1)
    if [ -z "$value" ]; then
      missing_keys="$missing_keys $key"
    else
      case "$value" in
        *CHANGEME*) ENV_PROBLEMS+=("- .env has $key still set to a placeholder -- run scripts/gen-secrets.sh --force (read its PIN-rotation warning first)") ;;
      esac
    fi
  done
  # Deliberately defers to gen-secrets.sh rather than naming a flag: that
  # script appends the keys it can add without rotating anything, and says so
  # if the file needs --force instead. Naming --force here would be wrong for
  # the keys it self-heals.
  [ -n "$missing_keys" ] && ENV_PROBLEMS+=("- .env is missing:$missing_keys -- re-run scripts/gen-secrets.sh; it appends what it can and tells you if the file needs --force")
fi

# -- warn-only checks ---------------------------------------------------------
#
# Neither of these can be measured reliably enough to block a deploy on, and a
# false positive that refuses to deploy is worse than a line of advice.
WARNINGS=()

# The join tokens are interpolated with ${VAR:-}, so they are not in the
# required set above and their absence does not stop a deploy -- it costs the
# join demo only. Warn rather than fail, and say what it costs.
if [ -f "$ENV_FILE" ]; then
  join_missing=""
  for key in KP2_JOIN_APPLICANT_TOKEN KP2_JOIN_OPERATOR_TOKEN; do
    grep -q "^${key}=." "$ENV_FILE" || join_missing="$join_missing $key"
  done
  [ -n "$join_missing" ] && WARNINGS+=("- .env has no:$join_missing -- the federation deploys without them, but the join demo does not (join-api refuses to start; the console's join tab shows the remedy). Re-run scripts/gen-secrets.sh with no flags to append just those.")
fi

# Drift presents as certificate errors, not time errors -- which is why it is
# worth naming here rather than leaving to be diagnosed later.
if [ "$OS" = macos ]; then
  WARNINGS+=("- clock sync not checked on macOS -- confirm it yourself: sntp -sS time.apple.com. A drifting clock produces failures that look like certificate errors.")
elif command -v timedatectl >/dev/null 2>&1; then
  if [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" != "yes" ]; then
    WARNINGS+=("- host clock is not NTP-synchronised (timedatectl). A drifting clock produces failures that look like certificate errors, not time errors.")
  fi
fi

# ~10.9-11.1 GiB steady state, measured for the current four-Security-Server
# topology (runbook.md Prerequisites). Docker Desktop's VM makes host RAM an
# approximation, hence a warning.
MEM_KIB=""
if [ -r /proc/meminfo ]; then
  MEM_KIB=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
elif [ "$OS" = macos ]; then
  MEM_KIB=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1024 ))
fi
if [ -n "$MEM_KIB" ] && [ "$MEM_KIB" -gt 0 ] && [ "$MEM_KIB" -lt 12582912 ]; then
  WARNINGS+=("- $(( MEM_KIB / 1048576 )) GiB RAM on this host; the federation needs ~11 GiB in steady state (runbook.md Prerequisites).")
fi

print_warnings() {
  [ "${#WARNINGS[@]}" -eq 0 ] && return
  echo "preflight: warnings (not blocking):" >&2
  printf '%s\n' "${WARNINGS[@]}" >&2
}

if [ "${#FAILURES[@]}" -eq 0 ] && [ "${#ENV_PROBLEMS[@]}" -eq 0 ]; then
  echo "preflight: docker, docker compose (v2), jq, curl, python3 (3.9+ with PyYAML), a SHA-256 tool, bash 4+, and every .env key the deploy requires are all present."
  print_warnings
  exit 0
fi

echo "preflight: this host is not ready. Fix everything below, then re-run:" >&2
[ "${#ENV_PROBLEMS[@]}" -eq 0 ] || printf '%s\n' "${ENV_PROBLEMS[@]}" >&2
for f in ${FAILURES[@]+"${FAILURES[@]}"}; do
  case "$f" in
    docker)
      echo "- docker not found" >&2
      hint "sudo apt-get install -y docker.io" "brew install --cask docker" >&2
      ;;
    compose)
      echo "- docker compose v2 (the plugin form) not found -- a standalone docker-compose (v1) does not satisfy this" >&2
      hint "sudo apt-get install -y docker-compose-plugin" "brew install --cask docker  # Docker Desktop bundles the v2 plugin" >&2
      ;;
    jq)
      echo "- jq not found" >&2
      hint "sudo apt-get install -y jq" "brew install jq" >&2
      ;;
    curl)
      echo "- curl not found" >&2
      hint "sudo apt-get install -y curl" "brew install curl" >&2
      ;;
    python3)
      echo "- python3 not found" >&2
      hint "sudo apt-get install -y python3" "brew install python3" >&2
      ;;
    python3-floor)
      echo "- python3 is older than 3.9 (found $(python3 -c 'import platform; print(platform.python_version())' 2>/dev/null))" >&2
      hint "sudo apt-get install -y python3" "brew install python3" >&2
      ;;
    pyyaml)
      echo "- python3 lacks PyYAML (import yaml fails)" >&2
      hint "sudo apt-get install -y python3-yaml" "python3 -m pip install pyyaml" >&2
      ;;
    sha256)
      echo "- no SHA-256 tool found (need sha256sum or shasum)" >&2
      hint "sudo apt-get install -y coreutils" "shasum ships with macOS by default -- check PATH" >&2
      ;;
    bash4)
      echo "- bash is older than 4 (running under bash ${BASH_VERSINFO[0]})" >&2
      hint "sudo apt-get install -y bash" "brew install bash  # then make sure it comes before /bin/bash (3.2) in PATH" >&2
      ;;
  esac
done
print_warnings
exit 1
