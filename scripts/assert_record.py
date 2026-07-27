#!/usr/bin/env python3
"""Acceptance 2.6.2 — right learner: the bus returned the seeded record, field
by field, for the given NIN. Usage: assert_record.py <nin> <identity_json> <enrolment_json>
Exits non-zero with a named field diff on mismatch."""
import csv
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "apps" / "data"


def seeded(fname, nin):
    with open(DATA / fname, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["nin"] == nin:
                return row
    sys.exit(f"seed row for {nin} not found in {fname}")


def compare(label, expected, actual):
    """Every field the API actually returned must match the seed -- not
    every column the seed CSV carries. PNIA's identity-api withholds fields
    the credential purpose doesn't need (purpose limitation, proved by
    absence -- UX plan Task 5); this check must not demand their echo back."""
    if not actual:
        sys.exit(f"MISMATCH in {label}: empty response, expected fields from the seeded record")
    diffs = [f"  {k}: seeded={expected.get(k)!r} returned={v!r}"
             for k, v in actual.items() if str(v) != str(expected.get(k))]
    if diffs:
        sys.exit(f"MISMATCH in {label} for the returned record:\n" + "\n".join(diffs))


def main():
    nin, id_json, en_json = sys.argv[1], sys.argv[2], sys.argv[3]
    compare("identity (PNIA)", seeded("persons.csv", nin), json.loads(id_json))
    compare("enrolment (PLR)", seeded("enrolments.csv", nin), json.loads(en_json))
    print(f"right learner confirmed for NIN {nin}")


if __name__ == "__main__":
    main()
