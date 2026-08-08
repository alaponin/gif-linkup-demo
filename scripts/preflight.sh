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
# script exists to avoid.
set -uo pipefail

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

if [ "${#FAILURES[@]}" -eq 0 ]; then
  echo "preflight: docker, docker compose (v2), jq, curl, python3 (3.9+ with PyYAML), a SHA-256 tool, and bash 4+ are all present."
  exit 0
fi

echo "preflight: this host is missing what the pack needs. Install everything below, then re-run:" >&2
for f in "${FAILURES[@]}"; do
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
exit 1
