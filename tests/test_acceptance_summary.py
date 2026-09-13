"""scripts/acceptance.sh --summary's line shape -- what a text capture renders
on a slide (scripts/demo-capture.sh, beat C8). The format is the contract, not
the checks or their timings, so this pins lib-core.sh's summary_line and that
acceptance.sh routes every check through it."""
import pathlib
import subprocess

PACK = pathlib.Path(__file__).resolve().parent.parent
LIB_CORE = PACK / "scripts" / "lib-core.sh"


def test_summary_line_shape():
    out = subprocess.run(
        ["bash", "-c", '. "$1"; summary_line 2.6.4 PASS "negative — PLR denied by the provider ACL"',
         "_", str(LIB_CORE)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert out == "2.6.4  PASS  negative — PLR denied by the provider ACL\n"


def test_acceptance_records_both_verdicts_through_summary_line():
    text = (PACK / "scripts" / "acceptance.sh").read_text()
    assert 'summary_line "$id" PASS "$desc"' in text
    assert 'summary_line "$id" FAIL "$desc"' in text
    assert "--summary) SUMMARY=1" in text
