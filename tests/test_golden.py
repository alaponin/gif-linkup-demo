"""Golden-corpus test for hurl/generate.py -- testing-strategy plan Task 1.

Regenerates both profiles into a temp directory (via --out/--profile/--env,
which exist only for this test -- see hurl/generate.py's parse_args()) and
diffs the result against tests/golden/{full,lite}/. Turns the byte-identical
ritual pasted into every plan that touches generate.py into a two-second
check. If a change to generate.py *should* alter the output, regenerate the
corpus in the same commit -- see hurl/README.md.

No Docker, no network, no running federation: this is the fast tier.
"""
from __future__ import annotations

import filecmp
import pathlib
import subprocess

import pytest

PACK = pathlib.Path(__file__).resolve().parent.parent
GOLDEN = pathlib.Path(__file__).resolve().parent / "golden"
ENV_FIXTURE = GOLDEN / "env.fixture"


def _generate(tmp_path: pathlib.Path, profile: str) -> pathlib.Path:
    out_dir = tmp_path / profile
    # The pack's own system python3 (see hurl/README.md's host-runtime note:
    # 3.9+, not whatever interpreter is running pytest), so this test
    # exercises the host floor rather than sidestepping it.
    result = subprocess.run(
        [
            "python3", "hurl/generate.py",
            "--out", str(out_dir),
            "--profile", profile,
            "--env", str(ENV_FIXTURE),
        ],
        cwd=PACK,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"generate.py --profile {profile} failed:\n{result.stdout}\n{result.stderr}"
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


@pytest.mark.parametrize("profile", ["full", "lite"])
def test_generate_matches_golden_corpus(tmp_path, profile):
    actual = _generate(tmp_path, profile)
    _assert_trees_equal(actual, GOLDEN / profile)
