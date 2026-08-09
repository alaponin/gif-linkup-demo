"""scripts/render_catalogue.py -- writes onboarding/catalogue.yaml through
the same writer.write_catalogue() a join and an un-join call, so there is
one function that decides what the instance's catalogue looks like and three
callers of it.

Derived wholesale from manifest.yaml + configs/member-*/ on every run:
nothing here appends an entry and nothing deletes one. Invoked by
scripts/render-onboarding.sh, after the per-member trees -- see that script
for why this needs the dev .venv.
"""
from __future__ import annotations

import pathlib
import sys

PACK_DIR = pathlib.Path(sys.argv[1])

sys.path.insert(0, str(PACK_DIR / "apps" / "join-api"))
from writer import write_catalogue  # noqa: E402

print(f"rendered {write_catalogue(PACK_DIR).relative_to(PACK_DIR)}")
