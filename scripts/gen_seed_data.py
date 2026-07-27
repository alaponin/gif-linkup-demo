#!/usr/bin/env python3
"""Generate the Progressa demonstration seed data (deterministic).

Grounding: realities of a small West African country — regions, Lower/Upper
Basic + Senior Secondary school structure, 11-digit NIN-style identifiers
[confirm: exact NIN format at P0]. Progressa names only; the source country is
never named in any artefact.

Outputs (to the directory given as argv[1]):
  persons.csv         50 persons at PNIA (the identity authority)
  enrolments.csv      46 enrolments at PLR — 4 NINs deliberately missing
                      (the mismatch rows for the negative checks)
  school_records.csv  48 records at MoEYS/PEMIS (school census view)
"""
import csv
import random
import sys
from pathlib import Path

SEED = 2026  # deterministic — same data every run

GIVEN_F = ["Fatou", "Awa", "Isatou", "Mariama", "Adama", "Binta", "Haddy",
           "Kaddy", "Jainaba", "Sainabou", "Ndey", "Amie"]
GIVEN_M = ["Lamin", "Modou", "Ousman", "Ebrima", "Momodou", "Bakary", "Alieu",
           "Sulayman", "Kebba", "Yusupha", "Omar", "Saikou"]
FAMILY = ["Ceesay", "Jallow", "Touray", "Njie", "Bah", "Camara", "Sanneh",
          "Darboe", "Sowe", "Jammeh", "Faal", "Bojang", "Saidy", "Colley"]
REGIONS = ["Banjul", "Kanifing", "West Coast", "North Bank",
           "Lower River", "Central River", "Upper River"]
SCHOOLS = {
    "Banjul": "Banjul Senior Secondary School",
    "Kanifing": "Kanifing Senior Secondary School",
    "West Coast": "Brikama Senior Secondary School",
    "North Bank": "Kerewan Senior Secondary School",
    "Lower River": "Mansakonko Senior Secondary School",
    "Central River": "Janjanbureh Senior Secondary School",
    "Upper River": "Basse Senior Secondary School",
}
N_PERSONS = 50
N_MISSING_FROM_PLR = 4   # in PNIA, not PLR -> clean-404 negative check
N_MISSING_FROM_PEMIS = 2


def nin(rng):
    """11-digit NIN-style identifier [confirm: real format at P0]."""
    return "".join(str(rng.randint(0, 9)) for _ in range(11))


def main(outdir):
    rng = random.Random(SEED)
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)

    persons = []
    used = set()
    for i in range(N_PERSONS):
        sex = rng.choice(["F", "M"])
        given = rng.choice(GIVEN_F if sex == "F" else GIVEN_M)
        region = rng.choice(REGIONS)
        n = nin(rng)
        while n in used:
            n = nin(rng)
        used.add(n)
        persons.append({
            "nin": n,
            "given_name": given,
            "family_name": rng.choice(FAMILY),
            "date_of_birth": f"{rng.randint(2006, 2010)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            "sex": sex,
            "region": region,
            # PNIA plausibly holds these too, but the credential purpose
            # doesn't need them -- purpose limitation, proved by absence
            # (UX plan Task 5): the mock filters these out before they ever
            # reach the bus (apps/mock-registry/app.py), and the console's
            # legal pane shows they were withheld, never their values.
            "mother_name": f"{rng.choice(GIVEN_F)} {rng.choice(FAMILY)}",
            "birth_registration_no": f"BR{rng.randint(100000, 999999)}",
            "residence_address": f"House {rng.randint(1, 200)}, {region}",
        })

    missing_plr = {p["nin"] for p in rng.sample(persons, N_MISSING_FROM_PLR)}
    enrolments = [{
        "nin": p["nin"],
        "school": SCHOOLS[p["region"]],
        "level": "Senior Secondary",
        "enrolment_year": str(rng.randint(2022, 2025)),
        "status": rng.choice(["active"] * 9 + ["transferred"]),
    } for p in persons if p["nin"] not in missing_plr]

    eligible = [p for p in persons if p["nin"] not in missing_plr]
    missing_pemis = {p["nin"] for p in rng.sample(eligible, N_MISSING_FROM_PEMIS)}
    school_records = [{
        "nin": e["nin"],
        "school": e["school"],
        "grade": f"Grade {rng.randint(10, 12)}",
        "census_year": "2025",
    } for e in enrolments if e["nin"] not in missing_pemis]

    for name, rows in [("persons.csv", persons), ("enrolments.csv", enrolments),
                       ("school_records.csv", school_records)]:
        with open(out / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    with open(out / "README.md", "w", encoding="utf-8") as f:
        f.write(
            "# Progressa seed data (generated — do not hand-edit)\n\n"
            f"Deterministic (seed {SEED}); regenerate with scripts/gen_seed_data.py.\n\n"
            f"- persons.csv: {len(persons)} (PNIA)\n"
            f"- enrolments.csv: {len(enrolments)} (PLR)\n"
            f"- school_records.csv: {len(school_records)} (PEMIS)\n\n"
            "NINs in PNIA but deliberately NOT in PLR (clean-404 negative check):\n"
            + "".join(f"- {n}\n" for n in sorted(missing_plr))
            + "\nNIN format is a placeholder 11-digit string [confirm: at P0].\n")
    print(f"wrote {len(persons)} persons, {len(enrolments)} enrolments, "
          f"{len(school_records)} school records -> {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "apps/data")
