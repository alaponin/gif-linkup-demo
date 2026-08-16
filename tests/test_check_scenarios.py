"""Tests for hurl/check_scenarios.py -- the static scenario gate.

check_scenarios.py is a tier-1 guard: `scripts/verify.sh --fast` runs it
first, and the ship gate (kp-solution-verify's check_pack.py, which executes
any `<pack>/<tool>/check_*.py` it finds) runs it again for `--ready`. That
makes it the one thing standing between a broken scenario set and a deploy
that only fails 20 minutes in. Until this file existed it had no tests of
its own, which is the worst place in the pack for a silent regression: a
guard that stops catching things still exits 0, so --fast stays green and
gets quietly weaker. Each test below breaks exactly one thing and asserts
the specific complaint, so a check that stops firing fails here loudly.

The fixture pack is assembled from the committed golden corpus
(tests/golden/) rather than hand-written: tests/test_golden.py already
proves that corpus is byte-identical to what hurl/generate.py produces, so
this suite cannot drift from the generator's real output. Its vars.env and
env.fixture also already agree on the fixture credentials, which is what
makes the clean-pack case clean.

No production change was needed to test this. check_scenarios.py reads PACK
and SCEN inside main() (not at import), and `failures` is a module-level
list `note()` appends to, so _check() below simply points all three at a
temp pack per test. That is also why every test must go through _check():
it resets `failures`, which would otherwise accumulate across tests in one
pytest process.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys

import pytest
import yaml

PACK = pathlib.Path(__file__).resolve().parent.parent
GOLDEN = PACK / "tests" / "golden"

sys.path.insert(0, str(PACK / "hurl"))
import check_scenarios as cs  # noqa: E402


@pytest.fixture
def pack(tmp_path) -> pathlib.Path:
    """A minimal pack that check_scenarios.py passes on, built from the
    golden corpus. Only the files check_scenarios.py actually reads:
    hurl/{scenarios,vars.env,topology.json}, .env, manifest.yaml,
    deployment.yaml -- no configs/, no templates, no generate.py."""
    hurl = tmp_path / "hurl"
    hurl.mkdir()
    shutil.copytree(GOLDEN / "deployment" / "scenarios", hurl / "scenarios")
    shutil.copy(GOLDEN / "deployment" / "vars.env", hurl / "vars.env")
    shutil.copy(GOLDEN / "deployment" / "topology.json", hurl / "topology.json")
    shutil.copy(GOLDEN / "env.fixture", tmp_path / ".env")
    shutil.copy(PACK / "manifest.yaml", tmp_path / "manifest.yaml")
    shutil.copy(PACK / "deployment.yaml", tmp_path / "deployment.yaml")
    return tmp_path


def _check(pack: pathlib.Path) -> list[str]:
    """Run the gate against `pack` and return what it complained about.
    Empty list == the gate passed (main() returns instead of sys.exit(1))."""
    cs.PACK = pack
    cs.SCEN = pack / "hurl" / "scenarios"
    cs.failures = []
    try:
        cs.main()
    except SystemExit as exc:
        assert exc.code == 1, f"expected exit 1 on failure, got {exc.code!r}"
        assert cs.failures, "exited non-zero without recording a failure"
    else:
        assert not cs.failures, "recorded failures but still exited 0"
    return list(cs.failures)


def _scenarios(pack: pathlib.Path) -> list[pathlib.Path]:
    return sorted((pack / "hurl" / "scenarios").glob("*.hurl"))


def _edit_manifest(pack: pathlib.Path, mutate) -> None:
    path = pack / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text())
    mutate(manifest)
    path.write_text(yaml.safe_dump(manifest))


def _edit_topology(pack: pathlib.Path, mutate) -> None:
    path = pack / "hurl" / "topology.json"
    topo = json.loads(path.read_text())
    mutate(topo)
    path.write_text(json.dumps(topo, indent=2))


def _matching(failures: list[str], needle: str) -> list[str]:
    return [f for f in failures if needle in f]


# -- the clean baseline -------------------------------------------------------


def test_a_clean_pack_passes(pack):
    """Every negative test below is only meaningful if the unmutated fixture
    passes -- otherwise they could all be firing on the same unrelated
    breakage."""
    assert _check(pack) == []


# -- 1. undefined variables ---------------------------------------------------


def test_a_variable_that_is_neither_defined_nor_captured_is_caught(pack):
    target = _scenarios(pack)[0]
    target.write_text(target.read_text() + "\nGET https://{{no_such_variable}}/x\n")
    assert _matching(_check(pack), "{{no_such_variable}} before it exists")


def test_a_capture_used_before_the_request_that_produces_it_is_caught(pack):
    """The ordering bug the concatenated scenario set makes easy to write and
    impossible to see: file 30 captures it, file 00 uses it. Hurl only finds
    out at run time, 20 minutes in."""
    files = _scenarios(pack)
    late = None
    for line in files[-1].read_text().splitlines():
        m = cs.CAPTURE_LINE.match(line.strip())
        if m:
            late = m.group(1)
            break
    assert late, "fixture's last scenario captures nothing -- pick another"
    files[0].write_text(f"GET https://x/{{{{{late}}}}}\n" + files[0].read_text())
    assert _matching(_check(pack), f"{{{{{late}}}}} before it exists")


# -- 2. vars.env hygiene ------------------------------------------------------


def test_a_comment_line_in_vars_env_is_rejected(pack):
    """Hurl's --variables-file is a plain name=value list: a '#' ends up
    inside the value, silently corrupting a hostname or a PIN."""
    vars_env = pack / "hurl" / "vars.env"
    vars_env.write_text("# a helpful comment\n" + vars_env.read_text())
    assert _matching(_check(pack), "contains '#'")


def test_a_trailing_comment_on_a_value_in_vars_env_is_rejected(pack):
    vars_env = pack / "hurl" / "vars.env"
    vars_env.write_text(vars_env.read_text() + "extra_host=ss-x  # trailing\n")
    assert _matching(_check(pack), "contains '#'")


def test_a_line_that_is_not_name_equals_value_is_rejected(pack):
    vars_env = pack / "hurl" / "vars.env"
    vars_env.write_text(vars_env.read_text() + "just_a_bare_word\n")
    assert _matching(_check(pack), "is not name=value")


# -- 3. credentials agreeing with .env ----------------------------------------


def test_a_credential_that_disagrees_with_env_is_caught(pack):
    """vars.env's token_pin and .env's XROAD_TOKEN_PIN drifting apart is the
    failure that looks like a certificate fault at the first key generation.
    Every one of the three pairs must be checked, not just the first."""
    for var, env_key in (
        ("token_pin", "XROAD_TOKEN_PIN"),
        ("ss_admin_user", "XROAD_ADMIN_USER"),
        ("ss_admin_password", "XROAD_ADMIN_PASSWORD"),
    ):
        vars_env = pack / "hurl" / "vars.env"
        original = vars_env.read_text()
        vars_env.write_text(
            "\n".join(
                f"{var}=DRIFTED-VALUE" if line.startswith(f"{var}=") else line
                for line in original.splitlines()
            )
            + "\n"
        )
        assert _matching(_check(pack), f"vars.env's {var} disagrees with .env's {env_key}")
        vars_env.write_text(original)


def test_the_credential_complaint_never_prints_the_credentials(pack):
    """A token PIN and an admin password are live secrets, and this gate's
    output lands in CI logs. The message must name the KEYS that disagree,
    never either value -- the diff-style message a reader would reach for
    first is a secret-leakage path. check_scenarios.py says so in a comment;
    this is what holds it to it."""
    vars_env = pack / "hurl" / "vars.env"
    vars_env.write_text(
        "\n".join(
            "token_pin=SECRET-FROM-VARS-ENV" if line.startswith("token_pin=") else line
            for line in vars_env.read_text().splitlines()
        )
        + "\n"
    )
    failures = _check(pack)
    assert _matching(failures, "token_pin disagrees")
    blob = "\n".join(failures)
    assert "SECRET-FROM-VARS-ENV" not in blob
    # ...and not the .env side either, which is the real secret of the two.
    assert "FIXTURE-NOT-REAL-PIN-000000" not in blob


def test_env_example_is_used_when_there_is_no_env(pack):
    """A fresh clone has .env.example and no .env. The gate must still run
    the credential check rather than crash on a missing file."""
    (pack / ".env").rename(pack / ".env.example")
    assert _check(pack) == []


# -- 4. the module <-> scenario claim chain -----------------------------------


def test_a_module_claiming_a_scenario_that_does_not_exist_is_caught(pack):
    _edit_manifest(
        pack,
        lambda m: m["modules"][0].update(
            scenarios=m["modules"][0]["scenarios"] + ", hurl/scenarios/99-imaginary.hurl"
        ),
    )
    assert _matching(_check(pack), "claims hurl/scenarios/99-imaginary.hurl, which does not exist")


def test_a_scenario_no_module_claims_is_caught(pack):
    (pack / "hurl" / "scenarios" / "97-orphan.hurl").write_text("GET https://cs:4000/\n")
    assert _matching(_check(pack), "97-orphan.hurl is not claimed by any module")


def test_a_joined_members_scenario_is_allowed_to_be_unclaimed(pack):
    """The deliberate exemption, and the one most at risk of being
    "tidied up" into the strict rule: manifest.yaml's modules are the frozen
    curriculum, so a member that joined at run time can never be claimed by
    one. Tolerated only when the file's own member key resolves to an
    origin: joined member -- see the next test for the other half."""
    (pack / "hurl" / "scenarios" / "96-ss-ptsb.hurl").write_text("GET https://cs:4000/\n")
    _edit_manifest(
        pack,
        lambda m: m["identity"]["members"].update(
            ptsb={"code": "PTSB", "name": "Progressa Teacher Standards Board",
                  "subsystem": "AWARDS", "origin": "joined"}
        ),
    )
    assert _check(pack) == []


def test_an_unknown_members_scenario_is_still_unclaimed(pack):
    """The exemption above must not degrade into "any NN-ss-*.hurl passes".
    A member key that is in no manifest entry at all defaults to canonical,
    and canonical scenarios must be claimed."""
    (pack / "hurl" / "scenarios" / "96-ss-ghost.hurl").write_text("GET https://cs:4000/\n")
    assert _matching(_check(pack), "96-ss-ghost.hurl is not claimed by any module")


# -- 5. identity: vs identifiers: (the cross-pack contract) -------------------


def test_an_identifiers_instance_that_disagrees_with_identity_is_caught(pack):
    _edit_manifest(pack, lambda m: m["identifiers"].update(instance="OTHERLAND"))
    failures = _check(pack)
    assert _matching(failures, "identity.instance")


def test_an_identifiers_owner_that_disagrees_with_identity_is_caught(pack):
    _edit_manifest(pack, lambda m: m["identity"]["owner"].update(code="NOTPDGA"))
    assert _matching(_check(pack), "disagrees with identifiers.owner")


def test_a_manifest_member_never_registered_in_the_scenarios_is_caught(pack):
    _edit_manifest(
        pack,
        lambda m: m["identifiers"]["members"].append("PROGRESSA/GOV/NOBODY:NOWHERE"),
    )
    failures = _check(pack)
    assert _matching(failures, "never registered in the scenarios")
    assert _matching(failures, "never added as a client")


def test_a_manifest_service_never_published_is_caught(pack):
    _edit_manifest(
        pack,
        lambda m: m["identifiers"]["services"].append("PROGRESSA/GOV/PNIA/IDENTITY/ghost-api"),
    )
    assert _matching(_check(pack), "never published")


def test_a_joined_member_in_the_frozen_identifiers_contract_is_caught(pack):
    """identifiers: is the cross-pack contract KP3/KP4 build on. A member
    that joined at run time must never leak into it, even though its
    identity: entry is perfectly valid."""
    _edit_manifest(
        pack,
        lambda m: m["identity"]["members"]["pnia"].update(origin="joined"),
    )
    assert _matching(_check(pack), "only canonical members belong in the frozen identifiers")


# -- 6. topology.json ---------------------------------------------------------


def test_a_missing_topology_json_is_caught(pack):
    (pack / "hurl" / "topology.json").unlink()
    assert _matching(_check(pack), "topology.json does not exist")


def test_a_topology_missing_a_canonical_subsystem_is_caught(pack):
    _edit_topology(pack, lambda t: t["subsystems"].pop(0))
    assert _matching(_check(pack), "topology.json is missing canonical subsystem")


def test_a_duplicate_host_port_in_topology_is_caught(pack):
    _edit_topology(
        pack,
        lambda t: t["security_servers"][1].update(
            host_ui_port=t["security_servers"][0]["host_ui_port"],
            host_proxy_port=t["security_servers"][0]["host_proxy_port"],
        ),
    )
    failures = _check(pack)
    assert _matching(failures, "duplicate host_ui_port")
    assert _matching(failures, "duplicate host_proxy_port")


def test_a_port_in_the_airplay_range_is_caught(pack):
    """macOS's AirPlay Receiver binds 5000-5099 and makes the port hang
    rather than refuse, which reads as a broken federation."""
    _edit_topology(pack, lambda t: t["security_servers"][0].update(host_ui_port=5000))
    assert _matching(_check(pack), "in the 5000-5099 range")


def test_a_subsystem_hosted_on_an_unknown_server_is_caught(pack):
    _edit_topology(pack, lambda t: t["subsystems"][0].update(hosted_on="ss-nonexistent"))
    assert _matching(_check(pack), "not one of the running security_servers")


# -- 7. scenario numbering ----------------------------------------------------


def test_two_scenarios_claiming_the_same_number_is_caught(pack):
    """generate.py's own allocator prevents this at generation time; the gate
    checks it again because it must also catch a hand-edited scenario set."""
    first = _scenarios(pack)[0]
    number = first.name.split("-", 1)[0]
    twin = first.with_name(f"{number}-duplicate.hurl")
    twin.write_text(first.read_text())
    _edit_manifest(
        pack,
        lambda m: m["modules"][0].update(
            scenarios=m["modules"][0]["scenarios"] + f", hurl/scenarios/{twin.name}"
        ),
    )
    assert _matching(_check(pack), f"scenario number {number} is used by more than one file")


# -- 8. deployment.yaml's public-exposure acknowledgement ---------------------


def test_a_public_bind_without_acknowledgement_is_caught(pack):
    """Binding off-loopback publishes the X-Road proxy ports, the admin UIs
    and the Test CA with no authentication. Allowed, but only said out loud
    -- the same rule scripts/lib-stack.sh enforces at deploy time, pinned
    here so --ready catches it too."""
    path = pack / "deployment.yaml"
    spec = yaml.safe_load(path.read_text())
    spec.setdefault("network", {})["bind"] = "0.0.0.0"
    spec["network"].pop("acknowledge_public_exposure", None)
    path.write_text(yaml.safe_dump(spec))
    assert _matching(_check(pack), "without network.acknowledge_public_exposure")


def test_a_public_bind_with_acknowledgement_passes(pack):
    """The acknowledgement is an escape hatch that must actually work --
    otherwise the only way to deploy publicly is to edit the gate."""
    path = pack / "deployment.yaml"
    spec = yaml.safe_load(path.read_text())
    spec.setdefault("network", {}).update(bind="0.0.0.0", acknowledge_public_exposure=True)
    path.write_text(yaml.safe_dump(spec))
    assert _check(pack) == []
