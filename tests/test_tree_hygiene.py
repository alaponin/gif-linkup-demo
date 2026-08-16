"""The working tree must carry nothing that only a Mac's Finder invented.

Six empty "<name> 2" directories once sat beside load-bearing ones --
`hurl/templates/fragments 2`, `tests/golden/deployment 2`, three more under
`apps/*/tests/fixtures/` -- all stamped with the same minute, so one Finder
copy made all of them. They are the dangerous kind of junk: named almost
exactly like a directory the pack reads, empty, and invisible in `git status`
because git does not track empty directories at all. The first person to
"tidy up" by putting a fixture in one, or to read a diff and think a fixture
directory had been duplicated on purpose, pays for them.

.gitignore alone cannot be the fix. Ignoring a path keeps it out of git; it
does nothing about a folder-copy or a zip of this directory, which is how the
pack actually reaches a workshop machine -- and how 25 MB of darwin-only
Terraform provider binary and a real `.env` would travel too. `scripts/package.sh`
is the answer to that half (git archive, never the working tree); this test is
the answer to the half that says the tree itself should stay clean.

Runs in `scripts/verify.sh --fast` as part of its pytest step -- no new tier,
no new script for --fast to shell out to.
"""
from __future__ import annotations

import pathlib
import re

PACK = pathlib.Path(__file__).resolve().parent.parent

# Directories whose contents are none of this pack's business: git's own
# object store, the machine-local venv (site-packages legitimately holds all
# sorts of names), and anything a package manager vendored. .pytest_cache is
# pruned rather than refused because the runner executing this test creates it
# in the pack root as it goes -- a check that fails on its own side effect
# fails every second run. It is gitignored, and scripts/package.sh archives
# what git tracks, so it neither commits nor ships.
PRUNE = {".git", "node_modules", "__pycache__", ".pytest_cache"}

# Finder's duplicate suffix: "fragments 2", "report 2.json", " 3" on a third
# copy. A space, then digits, at the very end of the name (before any
# extension). Nothing this pack ships is named that way.
FINDER_DUPLICATE = re.compile(r" \d+$")


def _strays() -> list[str]:
    found = []
    for path in PACK.rglob("*"):
        rel = path.relative_to(PACK)
        if set(rel.parts) & PRUNE or rel.parts[0].startswith(".venv"):
            continue
        if path.name == ".DS_Store":
            found.append(str(rel))
        elif FINDER_DUPLICATE.search(path.stem):
            found.append(str(rel))
    return sorted(found)


def test_no_finder_duplicates_or_os_droppings():
    strays = _strays()
    assert not strays, (
        "the working tree holds paths that ship with the pack and belong to nobody:\n  "
        + "\n  ".join(strays)
        + "\n\nDelete them. A '<name> 2' is a Finder copy of the directory next to it "
        "(empty, and invisible to git status because git ignores empty directories); "
        ".DS_Store is a Finder dropping. Package with scripts/package.sh, "
        "which archives what git tracks rather than what happens to be on disk."
    )
