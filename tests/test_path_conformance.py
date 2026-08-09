"""The existence test behind docs/path-conformance.yaml.

This is the test that would have caught the 2026-08-08 finding. Three of the
pack's own findings were recorded as closed by `configs/governance/
governance.yaml` and a BB pattern register in `configs/x-road-bus/
join-policy.yaml`; neither had ever been created, the claims sat in prose in
two documents, and nothing in the suite could contradict them. A status claim
that cannot fail is not a status claim.

So every row of path-conformance.yaml must cite evidence, every cited path
must exist on disk, and every cited symbol must actually be found in that
file. The rule is deliberately the same one the pack applies to its own
configuration -- hurl/generate.py's check_policy(): "a block the generator
silently ignores is worse than no block at all: it reads as configuration and
is decoration." A citation nothing checks reads as evidence and is decoration.

The symbol check is a substring match, not a parse. That is enough: it fails
when a function is renamed or a file is gutted, which is the drift that
actually happens, and it costs nothing to keep true.
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

import pytest
import yaml

PACK = pathlib.Path(__file__).resolve().parent.parent
SOURCE = PACK / "docs" / "path-conformance.yaml"

sys.path.insert(0, str(PACK / "scripts"))
from render_path_conformance import SECTION_TITLES, STATUSES, render  # noqa: E402

DOC = yaml.safe_load(SOURCE.read_text())
CLAUSES = DOC["clauses"]

# One parametrisation per clause rather than a loop inside one test: a failure
# names the clause that is wrong, not just the first one.
IDS = [c["id"] for c in CLAUSES]
EVIDENCE = [
    (c["id"], item) for c in CLAUSES for item in c["evidence"]
]


def test_the_source_yaml_parses_and_is_not_empty():
    assert CLAUSES, "path-conformance.yaml declares no clauses"


def test_clause_ids_are_unique():
    duplicates = sorted({i for i in IDS if IDS.count(i) > 1})
    assert not duplicates, f"duplicate clause id(s): {duplicates}"


@pytest.mark.parametrize("clause", CLAUSES, ids=IDS)
def test_every_clause_has_the_required_fields(clause):
    for field in ("id", "section", "clause", "status", "evidence"):
        assert clause.get(field), f"{clause.get('id')} has no {field}"


@pytest.mark.parametrize("clause", CLAUSES, ids=IDS)
def test_every_status_is_one_of_the_four(clause):
    assert clause["status"] in STATUSES, (
        f"{clause['id']} has status {clause['status']!r}; the four are {list(STATUSES)}. "
        "A clause that fits none of them is a defect, not a fifth status."
    )


@pytest.mark.parametrize("clause", CLAUSES, ids=IDS)
def test_out_of_scope_states_why(clause):
    """The status that can be abused. 'Out of scope' without a reason is
    indistinguishable from 'not done', which is what named-absence is for."""
    if clause["status"] == "out-of-scope":
        assert clause.get("rationale"), f"{clause['id']} is out-of-scope with no rationale"


@pytest.mark.parametrize(
    "clause_id,item", EVIDENCE, ids=[f"{cid}:{item['path']}" for cid, item in EVIDENCE]
)
def test_every_cited_evidence_path_exists(clause_id, item):
    target = PACK / item["path"]
    assert target.exists(), (
        f"{clause_id} cites {item['path']}, which does not exist. Either the "
        "claim is stale or the file moved -- do not delete the check."
    )


@pytest.mark.parametrize(
    "clause_id,item",
    [(cid, item) for cid, item in EVIDENCE if item.get("symbol")],
    ids=[f"{cid}:{item['symbol']}" for cid, item in EVIDENCE if item.get("symbol")],
)
def test_every_cited_symbol_is_found_in_its_file(clause_id, item):
    target = PACK / item["path"]
    text = target.read_text(errors="replace")
    assert item["symbol"] in text, (
        f"{clause_id} cites {item['symbol']!r} in {item['path']}, which no longer "
        "contains it."
    )


def test_the_rendered_markdown_is_in_sync_with_the_yaml():
    """Generated, never hand-maintained -- the same rule
    writer.render_onboarding_tree() applies to onboarding/<key>/."""
    target = PACK / "docs" / "path-conformance.md"
    assert target.exists(), "docs/path-conformance.md has not been rendered"
    assert target.read_text() == render(DOC), (
        "docs/path-conformance.md is stale -- run "
        "`python3 scripts/render_path_conformance.py`"
    )


def test_the_renderer_check_flag_agrees():
    """--check is what a ship gate would call; prove it is wired, not just
    that render() is."""
    result = subprocess.run(
        [sys.executable, str(PACK / "scripts" / "render_path_conformance.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_gate_the_path_defines_has_at_least_one_clause():
    """The register's own completeness: every gate the path document defines
    has to be answered here, whether the answer is an implementation or an
    absence. This is the only place that completeness is enforced -- the
    per-member register (onboarding/<key>/00-gates.md) carries its own copy of
    the gate list, hand-maintained, with nothing checking the two agree."""
    sections = {str(c["section"]) for c in CLAUSES}
    for gate in ("G0", "G1", "G2", "G3", "G4", "G5", "G6", "GX"):
        assert gate in sections, f"no clause covers {gate}"


def test_the_register_cites_no_outside_document_as_its_authority():
    """The gate model is the pack's own. It was drafted in working documents
    that are no longer carried, and a register that named one of them as its
    authority outlived them the moment they were withdrawn -- so meta must not
    grow that citation back."""
    for key in ("path_document", "path_document_sha256", "path_version"):
        assert key not in DOC["meta"], (
            f"meta.{key} is back: the register defines its own gates and has no "
            "outside document to cite"
        )


def test_every_section_title_has_at_least_one_clause():
    """The reverse of test_every_gate_the_path_defines_has_at_least_one_clause,
    generalised to every section rather than just the eight gates, and the
    reverse of render_path_conformance.render()'s own check: that function
    fails on a clause in an unknown section; this fails on a known section
    with no clause. No new data structure -- SECTION_TITLES is already the
    one list of sections."""
    sections_with_clauses = {str(c["section"]) for c in CLAUSES}
    for key, title in SECTION_TITLES:
        assert key in sections_with_clauses, f"{title!r} ({key}) has no clauses"


# Documents withdrawn from the pack. The onboarding-path drafts were work in
# progress that a status register should never have cited as authority; the
# branch reviews and gap analyses were working notes whose findings have since
# landed in the code. Citing one of them is now a dangling reference.
WITHDRAWN = (
    "GEATDM-Interop-Member-Onboarding-Path",
    "onboarding-alignment-design",
    "onboarding-path-gap-analysis",
    "branch-review",
    "do-terraform-brainstorm",
)


def test_no_withdrawn_document_is_cited_by_a_tracked_file():
    """A withdrawn document leaves its citations behind, and a reader who
    follows one finds nothing -- the same failure the evidence-path checks
    above exist to prevent, one level up. Git-tracked files only: working
    notes kept locally (docs/decisions/superpowers/, gitignored) may still
    refer to whatever they were written against."""
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=PACK, capture_output=True, text=True, check=True
    ).stdout.split("\0")
    here = pathlib.Path(__file__).resolve()
    stale: dict[str, list[str]] = {}
    for rel in tracked:
        if not rel or pathlib.Path(rel).suffix not in {".md", ".yaml", ".yml", ".py", ".sh"}:
            continue
        path = PACK / rel
        if not path.is_file() or path.resolve() == here:
            continue  # this file names them in order to look for them
        text = path.read_text(errors="replace")
        for name in WITHDRAWN:
            if name in text:
                stale.setdefault(name, []).append(rel)
    assert not stale, f"withdrawn documents still cited: {stale}"
