"""Golden-corpus test for hurl/generate.py, per the testing-strategy plan.

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
    hosted_on (D5), and job.py's step-engine tests cover the join API, not
    generate.py's rendering of a hosted member's stub/appended-client files.
    See _generate_hosted_fixture()'s docstring for the mechanism.

Turns the byte-identical ritual pasted into every plan that touches
generate.py into a two-second check. If a change to generate.py *should*
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
    configs/ needs to carry today (D5: no canonical member is hosted)."""
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
