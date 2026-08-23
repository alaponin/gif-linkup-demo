"""The host reads hurl/topology.json as data; it no longer sources topology.sh.

`. "$PACK_DIR/hurl/topology.sh"` executed a file join-api can write --
hurl/ has to stay writable, because generate.py runs inside that container
and writes its outputs there (docs/security-review-2026-08-23.md, finding
H1). scripts/lib-core.sh's kp2_load_topology parses hurl/topology.json
instead, the same file apps/console/truth.py has always read.

Two things have to hold, and this file asserts both against the COMMITTED
golden pair (tests/golden/deployment/{topology.json,topology.sh} -- one
generation run, so they cannot be out of step with each other):

  1. a hostile value is refused by the charset check, not evaluated;
  2. the values the reader produces are the values topology.sh declares --
     otherwise the two readers drift and the bash callers quietly start
     using a different topology from the console's.

One deliberate exception to (2), asserted explicitly below rather than
papered over: the federation owner's own PDGA:MANAGEMENT pair. generate.py
puts it in topology.sh from manifest.yaml + configs/x-road-bus/
federation-core.yaml; it is not in topology.json at all. Its only reader,
scripts/acceptance.sh, skips it by name.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess

import pytest

PACK = pathlib.Path(__file__).resolve().parent.parent
LIB_CORE = PACK / "scripts" / "lib-core.sh"
GOLDEN = pathlib.Path(__file__).resolve().parent / "golden" / "deployment"

_MANAGEMENT_PAIR = "PDGA:MANAGEMENT"

_ASSOC = re.compile(r"^declare -A (\w+)=\(\n(.*?)^\)$", re.M | re.S)
_ENTRY = re.compile(r"^\s*\[([^\]]+)\]=(.*)$", re.M)
_PLAIN = re.compile(r"^(\w+)=\((.*)\)$", re.M)


def _parse_topology_sh(text: str) -> dict:
    """What `.` used to put in the shell, without running the file."""
    parsed: dict = {
        name: dict(_ENTRY.findall(body)) for name, body in _ASSOC.findall(text)
    }
    for name, body in _PLAIN.findall(text):
        parsed.setdefault(name, body.split())
    return parsed


def _load(json_path: pathlib.Path) -> subprocess.CompletedProcess:
    """Run kp2_load_topology and dump every array it declared, one
    NAME<TAB>KEY<TAB>VALUE line each -- comparable with _parse_topology_sh."""
    script = f'''
. "{LIB_CORE}"
kp2_load_topology "{json_path}"
for n in SS_UI SS_REST SS_REST_TLS HOST_SS CLIENT_CONN; do
  declare -n m=$n
  for k in "${{!m[@]}}"; do printf '%s\\t%s\\t%s\\n' "$n" "$k" "${{m[$k]}}"; done
done
for v in "${{SS_ORDER[@]}}"; do printf 'SS_ORDER\\t-\\t%s\\n' "$v"; done
'''
    return subprocess.run(["bash", "-euo", "pipefail", "-c", script],
                          capture_output=True, text=True)


def _as_dict(stdout: str) -> dict:
    out: dict = {}
    for line in stdout.splitlines():
        name, key, value = line.split("\t")
        if name == "SS_ORDER":
            out.setdefault(name, []).append(value)
        else:
            out.setdefault(name, {})[key] = value
    return out


def test_the_reader_agrees_with_the_topology_sh_generate_py_still_writes():
    proc = _load(GOLDEN / "topology.json")
    assert proc.returncode == 0, proc.stderr
    got = _as_dict(proc.stdout)
    want = _parse_topology_sh((GOLDEN / "topology.sh").read_text())

    assert sorted(got) == sorted(want), (
        f"the two readers declare different arrays: {sorted(got)} vs {sorted(want)}"
    )
    # SS_ORDER is stand-up order, so its ORDER is the assertion, not its set.
    assert got["SS_ORDER"] == want["SS_ORDER"]
    for name in ("SS_UI", "SS_REST", "SS_REST_TLS", "CLIENT_CONN", "HOST_SS"):
        expected = {k: v for k, v in want[name].items() if k != _MANAGEMENT_PAIR}
        assert got[name] == expected, f"{name} drifted between the two readers"


def test_the_management_pair_is_the_one_documented_difference():
    """Pinned so the drift check above cannot be quietly widened: exactly one
    key is missing, it is the federation owner's, and acceptance.sh -- the
    only reader of ${!HOST_SS[@]} -- skips exactly that key by name."""
    want = _parse_topology_sh((GOLDEN / "topology.sh").read_text())
    got = _as_dict(_load(GOLDEN / "topology.json").stdout)
    for name in ("HOST_SS", "CLIENT_CONN"):
        assert set(want[name]) - set(got[name]) == {_MANAGEMENT_PAIR}
    assert f'"$pair" = "{_MANAGEMENT_PAIR}"' in (PACK / "scripts/acceptance.sh").read_text()


@pytest.mark.parametrize("hostile", ["; rm -rf /", "$(touch /tmp/pwned)", "a\tb", "a\nb"])
def test_a_hostile_host_name_is_refused_by_the_charset_check(tmp_path, hostile):
    """The container writes hurl/topology.json, so this is the file an
    attacker who got code execution inside join-api would edit. Refused
    before the value ever reaches a bash assignment."""
    topo = json.loads((GOLDEN / "topology.json").read_text())
    topo["security_servers"][0]["host"] = hostile
    path = tmp_path / "topology.json"
    path.write_text(json.dumps(topo))

    proc = _load(path)
    assert proc.returncode != 0, proc.stdout
    assert "kp2_load_topology" in proc.stderr, proc.stderr
    assert not pathlib.Path("/tmp/pwned").exists()


def test_a_non_numeric_port_is_refused(tmp_path):
    topo = json.loads((GOLDEN / "topology.json").read_text())
    topo["security_servers"][0]["host_ui_port"] = "1000; id"
    path = tmp_path / "topology.json"
    path.write_text(json.dumps(topo))
    assert _load(path).returncode != 0


def test_an_empty_security_server_list_is_refused(tmp_path):
    """Same reason as the missing-file case below: acceptance.sh iterates
    SS_ORDER and HOST_SS, so empty arrays are green-over-nothing."""
    topo = json.loads((GOLDEN / "topology.json").read_text())
    topo["security_servers"] = []
    path = tmp_path / "topology.json"
    path.write_text(json.dumps(topo))
    proc = _load(path)
    assert proc.returncode != 0
    assert "no security_servers" in proc.stderr, proc.stderr


def test_a_missing_topology_json_refuses_rather_than_declaring_nothing(tmp_path):
    """Empty arrays would let acceptance.sh report green over a federation it
    never checked -- zero servers, zero pairs, zero failures."""
    proc = _load(tmp_path / "absent.json")
    assert proc.returncode != 0
    assert "generate.py" in proc.stderr, proc.stderr
