"""Golden-corpus test for hurl/generate.py.

Two cases, not a `--profile full|lite` parametrize (the profile concept was
removed from generate.py itself; D5: one topology):

  - test_generate_matches_golden_deployment: regenerates from the real
    configs/ (via --out/--env, which exist only for this test -- see
    hurl/generate.py's parse_args()) and diffs against
    tests/golden/deployment/.
  - test_generate_matches_golden_hosted_fixture: regenerates from a fixture
    member-config set in which PNEA's config sets security_server.hosted_on
    explicitly, and diffs against tests/golden/hosted-fixture/generated/.
    This is the one code path in hurl/generate.py's resolve_hosted_on_map()
    that no canonical config exercises today -- no member in configs/ sets
    hosted_on, and job.py's step-engine tests cover the join API, not
    generate.py's rendering of a hosted member's stub/appended-client files.
    See _generate_hosted_fixture()'s docstring for the mechanism.

Turns the byte-identical ritual every change to generate.py used to
repeat into a two-second check. If a change to generate.py *should*
alter the output, regenerate the corpus in the same commit -- see
hurl/README.md.

No Docker, no network, no running federation: this is the fast tier.
"""
from __future__ import annotations

import filecmp
import pathlib
import shutil
import subprocess

import pytest
import yaml

PACK = pathlib.Path(__file__).resolve().parent.parent
GOLDEN = pathlib.Path(__file__).resolve().parent / "golden"
ENV_FIXTURE = GOLDEN / "env.fixture"


def _generate_deployment(tmp_path: pathlib.Path) -> pathlib.Path:
    out_dir = tmp_path / "deployment"
    # The pack's own system python3 (see hurl/README.md's host-runtime note:
    # 3.9+, not whatever interpreter is running pytest), so this test
    # exercises the host floor rather than sidestepping it.
    result = subprocess.run(
        [
            "python3", "hurl/generate.py",
            "--out", str(out_dir),
            "--env", str(ENV_FIXTURE),
        ],
        cwd=PACK,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"generate.py failed:\n{result.stdout}\n{result.stderr}"
    )
    return out_dir


def _generate_hosted_fixture(tmp_path: pathlib.Path) -> pathlib.Path:
    """Generate from tests/golden/hosted-fixture/member-configs/ instead of
    the real configs/ -- the only way to exercise resolve_hosted_on_map()'s
    explicit-hosted_on branch without a live joined member.

    generate.py has no CLI flag to redirect its *input*: PACK (generate.py:33)
    is resolved from the script's own __file__, and every read that matters
    here (manifest.yaml, deployment.yaml, configs/x-road-bus/*.yaml,
    configs/member-*/*.yaml, plus the hurl/templates/ that steps.py's
    registry points at) goes through that same hardcoded PACK -- only --out
    (output) and --env (secrets) are test-facing knobs. Adding a --configs
    flag to the real argument parser to serve one test would be new CLI
    surface for a problem this has a clean answer to already.

    So: build a throwaway copy of the minimal subset of the pack that
    generate.py actually touches -- hurl/generate.py, hurl/steps.py,
    hurl/templates/, copied FRESH from the real pack every run so this can
    never drift from what generate.py actually does -- plus the committed
    fixture member-config set (manifest.yaml, deployment.yaml,
    configs/x-road-bus/{federation-core,join-policy}.yaml,
    configs/member-{pnea,plr,pnia}/*.yaml, with pnea's own config the only
    one that differs from the real pack's: it sets
    security_server.hosted_on: ss-plr). Because PACK resolves from
    __file__, running the *copied* generate.py naturally treats the temp
    directory as its pack root -- zero code changes to the real
    hurl/generate.py, and the fixture set the input to a config nothing in
    configs/ needs to carry today (no canonical member is hosted)."""
    mini_hurl = tmp_path / "hosted-fixture-pack" / "hurl"
    mini_pack = mini_hurl.parent
    mini_hurl.mkdir(parents=True)
    shutil.copy(PACK / "hurl" / "generate.py", mini_hurl / "generate.py")
    shutil.copy(PACK / "hurl" / "steps.py", mini_hurl / "steps.py")
    shutil.copytree(PACK / "hurl" / "templates", mini_hurl / "templates")

    fixture = GOLDEN / "hosted-fixture" / "member-configs"
    shutil.copy(fixture / "manifest.yaml", mini_pack / "manifest.yaml")
    shutil.copy(fixture / "deployment.yaml", mini_pack / "deployment.yaml")
    shutil.copytree(fixture / "configs", mini_pack / "configs")

    out_dir = tmp_path / "hosted-fixture-out"
    result = subprocess.run(
        [
            "python3", str(mini_hurl / "generate.py"),
            "--out", str(out_dir),
            "--env", str(ENV_FIXTURE),
        ],
        # cwd=PACK, not mini_pack: the script path, --out and --env above are
        # all absolute, and generate.py's own PACK (its module-level
        # constant) resolves from __file__, not from cwd -- so the copied
        # script treats mini_pack as its pack root regardless of the
        # subprocess's cwd. cwd=PACK instead of mini_pack matters for a
        # different reason: PACK carries the pinned .python-version
        # (3.11.14) pyenv's shim reads to pick an interpreter new enough for
        # generate.py's str.removeprefix() (3.9+, hurl/README.md's
        # host-runtime note); a bare tmp_path has no such pin and pyenv
        # falls back to a stale system Python (confirmed live: 3.7.9, no
        # removeprefix, AttributeError before this was cwd=PACK).
        cwd=PACK,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"generate.py (hosted-fixture pack copy) failed:\n{result.stdout}\n{result.stderr}"
    )
    return out_dir


def _assert_trees_equal(actual: pathlib.Path, expected: pathlib.Path) -> None:
    """Diff two directory trees, reporting the first differing (or missing,
    or extra) file and, for text files, the first differing line -- 'not
    equal' is not an actionable test failure message."""
    actual_files = {p.relative_to(actual) for p in actual.rglob("*") if p.is_file()}
    expected_files = {p.relative_to(expected) for p in expected.rglob("*") if p.is_file()}

    missing = expected_files - actual_files
    assert not missing, f"generate.py did not produce: {sorted(str(p) for p in missing)}"
    extra = actual_files - expected_files
    assert not extra, f"generate.py produced unexpected files not in the golden corpus: {sorted(str(p) for p in extra)}"

    for rel in sorted(expected_files):
        a, e = actual / rel, expected / rel
        if filecmp.cmp(a, e, shallow=False):
            continue
        a_lines = a.read_text().splitlines()
        e_lines = e.read_text().splitlines()
        for i, (al, el) in enumerate(zip(a_lines, e_lines), start=1):
            if al != el:
                pytest.fail(
                    f"{rel} differs from the golden corpus at line {i}:\n"
                    f"  generated: {al!r}\n"
                    f"  golden:    {el!r}"
                )
        pytest.fail(f"{rel} differs from the golden corpus (length: {len(a_lines)} vs {len(e_lines)} lines)")


def test_generate_matches_golden_deployment(tmp_path):
    actual = _generate_deployment(tmp_path)
    _assert_trees_equal(actual, GOLDEN / "deployment")


def test_generate_matches_golden_hosted_fixture(tmp_path):
    actual = _generate_hosted_fixture(tmp_path)
    _assert_trees_equal(actual, GOLDEN / "hosted-fixture" / "generated")


# -- fixture/real config drift ------------------------------------------------
#
# The hosted-fixture member configs are a COPY of the real configs/, so they
# drift. They already have: sla: and member_requirements: were dropped from
# the copies at some point and nobody noticed, because hurl/generate.py --
# the only thing this fixture is fed to -- reads neither.
#
# That is harmless exactly as long as it stays true, and nothing was watching
# the "as long as". Both fields ARE read, by scripts/render_onboarding.py; the
# day this fixture is pointed at anything but generate.py, their absence stops
# being decoration and starts being a wrong test. The tests below draw the
# line explicitly: the fields generate.py reads must match the real configs
# exactly, and the fields it does not read may be absent -- but only the two
# that are absent today. A third one going missing is new drift and fails.

# What hurl/generate.py actually reads out of a member config -- confirmed
# against discover_members() (key/identity agreement), resolve_hosted_on_map()
# (security_server.hosted_on), build_ss_file()/build_service_file()/
# build_hosted_client() and main()'s member_service_block(). Anything here is
# load-bearing for this fixture; anything not here is decoration as far as
# generate.py is concerned.
_LOAD_BEARING = frozenset({"module", "building_block", "security_server", "services", "client", "consumes", "semantic"})

# The two decoration fields the fixture copies are allowed to omit, and who
# does read them. Extend this ONLY with the consumer named -- an entry with no
# reader is a field that should have been deleted from the real config too.
_MAY_BE_ABSENT = {
    "member_requirements": "scripts/render_onboarding.py",
    "sla": "scripts/render_onboarding.py (per service)",
}

# The single deliberate difference: PNEA's fixture copy sets hosted_on, which
# is the entire reason the fixture exists (no canonical member sets it, so
# resolve_hosted_on_map()'s explicit branch is otherwise unreachable).
_INTENDED_DELTAS = {("pnea", "security_server", "hosted_on"): "ss-plr"}

_FIXTURE_CONFIGS = GOLDEN / "hosted-fixture" / "member-configs"


def _member_config_pairs():
    for fixture_path in sorted((_FIXTURE_CONFIGS / "configs").glob("member-*/*.yaml")):
        key = fixture_path.parent.name.removeprefix("member-")
        real_path = PACK / fixture_path.relative_to(_FIXTURE_CONFIGS)
        yield key, yaml.safe_load(real_path.read_text()), yaml.safe_load(fixture_path.read_text())


def test_hosted_fixture_configs_carry_every_field_generate_py_reads():
    """The load-bearing half must match the real configs exactly, so the
    golden corpus keeps describing the real pack. The one permitted delta is
    PNEA's hosted_on -- declared, not merely tolerated."""
    for key, real, fixture in _member_config_pairs():
        for field in sorted(_LOAD_BEARING & set(real)):
            expected = real[field]
            if field == "services":
                # sla: lives inside each service block; compare the rest.
                expected = [{k: v for k, v in s.items() if k not in _MAY_BE_ABSENT} for s in expected]
            if (key, field) == ("pnea", "security_server"):
                expected = {**expected, **{
                    sub: value for (k, f, sub), value in _INTENDED_DELTAS.items()
                    if (k, f) == (key, field)
                }}
            assert fixture.get(field) == expected, (
                f"tests/golden/hosted-fixture/member-configs/configs/member-{key}/ has drifted "
                f"from the real configs/member-{key}/ in {field!r}, which hurl/generate.py READS "
                "-- the golden corpus no longer describes the real pack. Sync the fixture and "
                "regenerate with scripts/regen-golden.sh."
            )


def test_hosted_fixture_omits_only_the_documented_decoration_fields():
    """The other half of the same contract: catch a NEW field going missing.
    If generate.py ever starts reading one of the omitted fields, move it out
    of _MAY_BE_ABSENT and into _LOAD_BEARING -- the test above then demands it
    back in the fixture."""
    for key, real, fixture in _member_config_pairs():
        absent = set(real) - set(fixture)
        assert absent <= set(_MAY_BE_ABSENT), (
            f"member-{key}'s fixture copy is missing {sorted(absent - set(_MAY_BE_ABSENT))}, which is "
            "new drift. If generate.py does not read it, document it in _MAY_BE_ABSENT with the "
            "script that does; otherwise copy the field back."
        )
        for service in real.get("services", []):
            fixture_service = next(
                (s for s in fixture.get("services", []) if s.get("code") == service["code"]), None
            )
            assert fixture_service is not None, f"member-{key} fixture is missing service {service['code']}"
            missing = set(service) - set(fixture_service)
            assert missing <= set(_MAY_BE_ABSENT), (
                f"member-{key}'s service {service['code']} is missing {sorted(missing - set(_MAY_BE_ABSENT))} "
                "in the fixture copy -- new drift."
            )


def test_the_hosted_fixture_delta_is_the_one_it_exists_for():
    """PNEA's hosted_on is the fixture's whole reason to exist. If it is ever
    dropped, both golden tests keep passing while silently covering nothing --
    resolve_hosted_on_map()'s explicit branch goes untested again."""
    fixtures = {key: fixture for key, _real, fixture in _member_config_pairs()}
    assert fixtures["pnea"]["security_server"].get("hosted_on") == "ss-plr"
    for key, fixture in fixtures.items():
        if key != "pnea":
            assert "hosted_on" not in fixture["security_server"], (
                f"member-{key} now sets hosted_on in the fixture too -- the corpus no longer "
                "isolates the single hosted member the test claims to cover."
            )
