#!/usr/bin/env python3
"""Static check of prompts/ -- the generating prompts and their worked examples.

A prompt is only usable if it carries the thing to run, says how to prove the
result, and ships the input it asks you to paste. Each of those has gone
missing at least once:

  1. every module's `prompt:` in manifest.yaml exists and is under prompts/;
  2. every prompt carries a copy-paste Prompt block, a Realises: line binding
     it to a module (or saying why it has none), and a Prove it footer;
  3. every prompts/examples/<name>/ pairs its brief.md with an expected
     answer -- either an expected-*.yaml beside it, or a line in the brief
     naming the committed config that is the expected output. A brief with
     no answer is an exercise nobody can mark.

Usage:  python3 prompts/check_prompts.py       (exit 1 on any failure)

Run from the pack root, and by the ship gate's <pack>/<tool>/check_*.py
auto-discovery, which is what puts it in scripts/verify.sh --fast.
"""

from __future__ import annotations

import pathlib
import re
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("check_prompts.py: PyYAML is required (pip install pyyaml)")

PACK = pathlib.Path(__file__).resolve().parent.parent
PROMPTS = PACK / "prompts"

failures: list[str] = []


def note(msg: str) -> None:
    failures.append(msg)
    print(f"FAIL: {msg}")


def check_manifest_prompts() -> set[pathlib.Path]:
    manifest = yaml.safe_load((PACK / "manifest.yaml").read_text())
    bound = set()
    for module in manifest.get("modules") or []:
        rel = module.get("prompt")
        if not rel:
            note(f"module {module.get('id')!r} declares no prompt:")
            continue
        path = PACK / rel
        if not path.is_file():
            note(f"module {module.get('id')!r} names {rel}, which does not exist")
            continue
        if not rel.startswith("prompts/"):
            note(f"module {module.get('id')!r} names {rel}, which is outside prompts/")
        bound.add(path.resolve())
    return bound


def check_prompt_shape(bound: set[pathlib.Path]) -> None:
    prompts = sorted(p for p in PROMPTS.glob("*.md") if p.name != "README.md")
    if not prompts:
        note("prompts/ contains no prompt files")
    for path in prompts:
        text = path.read_text()
        rel = path.relative_to(PACK)
        # The copy-paste block is the prompt. Without it the file is an essay.
        if "## Prompt (copy-paste ready)" not in text:
            note(f"{rel} has no '## Prompt (copy-paste ready)' section")
        elif "```" not in text.split("## Prompt (copy-paste ready)", 1)[1]:
            note(f"{rel}'s Prompt section has no fenced block to copy")
        if not re.search(r"^\*\*Realises:\*\*", text, re.MULTILINE):
            note(f"{rel} has no '**Realises:**' line binding it to a module")
        if "## Prove it" not in text:
            note(f"{rel} has no '## Prove it' footer naming how to check its output")
        # A prompt bound to no module must say so rather than leave it ambiguous.
        if path.resolve() not in bound and not re.search(
            r"^\*\*Realises:\*\* no module", text, re.MULTILINE
        ):
            note(f"{rel} is bound to no module in manifest.yaml and does not say so")


def check_examples() -> None:
    examples = PROMPTS / "examples"
    if not examples.is_dir():
        note("prompts/examples/ does not exist")
        return
    folders = sorted(d for d in examples.iterdir() if d.is_dir())
    if not folders:
        note("prompts/examples/ contains no example folders")
    for folder in folders:
        rel = folder.relative_to(PACK)
        brief = folder / "brief.md"
        if not brief.is_file():
            note(f"{rel}/ has no brief.md")
            continue
        expected = sorted(folder.glob("expected-*.yaml"))
        if expected:
            for path in expected:
                try:
                    yaml.safe_load(path.read_text())
                except yaml.YAMLError as exc:
                    note(f"{path.relative_to(PACK)} is not parseable YAML: {exc}")
            continue
        # No expected file: the brief must name the committed config that is
        # the expected output, and that file must exist.
        cited = re.findall(r"`(configs/[^`]+\.yaml)`", brief.read_text())
        if not cited:
            note(
                f"{rel}/brief.md has no expected-*.yaml beside it and names no "
                "committed configs/*.yaml as its expected output"
            )
        for config in cited:
            if not (PACK / config).is_file():
                note(f"{rel}/brief.md names {config} as expected output, which does not exist")


def main() -> None:
    bound = check_manifest_prompts()
    check_prompt_shape(bound)
    check_examples()
    if failures:
        print(f"\n{len(failures)} problem(s)")
        sys.exit(1)
    n_prompts = len([p for p in PROMPTS.glob("*.md") if p.name != "README.md"])
    n_examples = len([d for d in (PROMPTS / "examples").iterdir() if d.is_dir()])
    print(f"OK -- {n_prompts} prompts, {n_examples} worked examples")


if __name__ == "__main__":
    main()
