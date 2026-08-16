"""Tests for scripts/gen_seed_data.py -- the demonstration seed data.

The generator's one load-bearing property is that it is DETERMINISTIC.
apps/data/{persons,enrolments}.csv are committed, and three separate things
read them expecting the committed values:

  - scripts/acceptance.sh picks its 2.6 NIN as sorted(persons & enrolments)[0]
    and its clean-404 NIN as sorted(persons - enrolments)[0];
  - scripts/assert_record.py compares a live bus response against the seeded
    row for that NIN, field by field;
  - apps/data/awards.csv is HAND-written (PTSB is not a canonical member, so
    nothing generates it) and reuses NINs from persons.csv.

So an innocuous-looking edit -- reordering an rng draw, adding a name to a
list, moving a field -- silently reshuffles every NIN, and the damage shows
up as a live acceptance failure against a running federation, twenty minutes
in, looking like a bus problem rather than a data one. This file turns that
into a two-second diff.

test_regenerating_matches_the_committed_seed_data is the whole point; the
rest pin the invariants those three consumers actually depend on, so a
failure says which one broke rather than just "the bytes moved".
"""
from __future__ import annotations

import csv
import filecmp
import pathlib
import sys

import pytest

PACK = pathlib.Path(__file__).resolve().parent.parent
DATA = PACK / "apps" / "data"

sys.path.insert(0, str(PACK / "scripts"))
import gen_seed_data  # noqa: E402

GENERATED = ("persons.csv", "enrolments.csv", "README.md")


@pytest.fixture
def generated(tmp_path) -> pathlib.Path:
    out = tmp_path / "data"
    gen_seed_data.main(out)
    return out


def _rows(path: pathlib.Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _nins(path: pathlib.Path) -> set[str]:
    return {r["nin"] for r in _rows(path)}


# -- the headline: determinism ------------------------------------------------


def test_regenerating_matches_the_committed_seed_data(generated):
    """Byte-identical, including README.md -- which carries the list of NINs
    deliberately missing from PLR, so a reshuffle shows up there too."""
    for name in GENERATED:
        assert filecmp.cmp(generated / name, DATA / name, shallow=False), (
            f"apps/data/{name} is not what scripts/gen_seed_data.py now produces. "
            "If the change to the generator was intended, regenerate apps/data/ "
            "in the same commit -- and check apps/data/awards.csv's hand-written "
            "NINs still resolve, because nothing regenerates that one."
        )


def test_two_runs_produce_identical_output(tmp_path):
    """Determinism as a property of the generator itself, not merely
    agreement with a corpus that could have been regenerated from a
    non-deterministic run and committed."""
    first, second = tmp_path / "a", tmp_path / "b"
    gen_seed_data.main(first)
    gen_seed_data.main(second)
    for name in GENERATED:
        assert filecmp.cmp(first / name, second / name, shallow=False), f"{name} differs between runs"


def test_rerunning_over_an_existing_directory_is_clean(generated):
    """scripts/seed.sh reruns the generator over apps/data/ in place, so a
    second run must overwrite rather than append or fail on the existing
    directory."""
    before = (generated / "persons.csv").read_bytes()
    gen_seed_data.main(generated)
    assert (generated / "persons.csv").read_bytes() == before


# -- the shape acceptance.sh depends on ---------------------------------------


def test_the_counts_are_what_the_module_docstring_promises(generated):
    persons = _rows(generated / "persons.csv")
    enrolments = _rows(generated / "enrolments.csv")
    assert len(persons) == gen_seed_data.N_PERSONS == 50
    assert len(enrolments) == 50 - gen_seed_data.N_MISSING_FROM_PLR == 46


def test_nins_are_unique_and_eleven_digits(generated):
    persons = _rows(generated / "persons.csv")
    nins = [p["nin"] for p in persons]
    assert len(set(nins)) == len(nins), "a duplicate NIN would make a lookup ambiguous"
    assert all(len(n) == 11 and n.isdigit() for n in nins)


def test_exactly_four_persons_are_missing_from_the_enrolment_registry(generated):
    """The clean-404 negative check: NINs PNIA knows and PLR does not. Not
    the other way round -- an enrolment for an unknown person would be a
    different (and unintended) inconsistency."""
    persons, enrolments = _nins(generated / "persons.csv"), _nins(generated / "enrolments.csv")
    assert len(persons - enrolments) == gen_seed_data.N_MISSING_FROM_PLR
    assert enrolments - persons == set(), "every enrolment must belong to a known person"


def test_acceptance_can_select_both_of_its_nins(generated):
    """acceptance.sh takes sorted(...)[0] of each set with no emptiness
    check, so an empty intersection or difference is an IndexError inside a
    heredoc, mid-run."""
    persons, enrolments = _nins(generated / "persons.csv"), _nins(generated / "enrolments.csv")
    assert sorted(persons & enrolments), "2.6 has no NIN present in both registries"
    assert sorted(persons - enrolments), "there is no NIN for the clean-404 check"


def test_committed_awards_nins_all_exist_in_persons(generated):
    """apps/data/awards.csv is hand-written and never regenerated, so it is
    the file a reshuffle breaks silently. Checked against the freshly
    generated persons.csv, not the committed one, so this fails at the same
    moment the reshuffle happens rather than one commit later."""
    persons = _nins(generated / "persons.csv")
    orphans = _nins(DATA / "awards.csv") - persons
    assert not orphans, (
        f"awards.csv references NIN(s) {sorted(orphans)} that persons.csv no longer "
        "contains -- awards.csv is hand-written and must be updated by hand."
    )


# -- purpose limitation -------------------------------------------------------


def test_persons_carries_the_fields_that_must_be_withheld(generated):
    """The pack's purpose-limitation claim is proved by ABSENCE: PNIA holds
    these, the mock filters them out before the bus, and the console's legal
    pane shows they were withheld. That demonstration is vacuous if the
    fields were never in the seed data to begin with."""
    columns = set(_rows(generated / "persons.csv")[0])
    assert {"mother_name", "birth_registration_no", "residence_address"} <= columns


def test_enrolments_carries_only_the_declared_semantic_fields(generated):
    """configs/member-plr/plr.yaml declares enrolment as
    [nin, school, level, enrolment_year, status]. The seed data must not
    quietly hold more than the semantic map says the entity is."""
    assert set(_rows(generated / "enrolments.csv")[0]) == {
        "nin", "school", "level", "enrolment_year", "status",
    }


def test_the_readme_records_the_hand_written_awards_file(generated):
    """seed.sh overwrites README.md whole on every run, which once ate a
    hand-added note about awards.csv. It is hardcoded in the generator now;
    this is what stops it being dropped again."""
    readme = (generated / "README.md").read_text()
    assert "awards.csv" in readme and "hand-written" in readme
    for nin in _nins(generated / "persons.csv") - _nins(generated / "enrolments.csv"):
        assert nin in readme, "README must list the NINs missing from PLR"
