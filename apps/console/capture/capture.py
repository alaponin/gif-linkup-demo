"""Film the demonstration console, beat by beat, from beats.yaml.

Runs inside the `capture` Compose service (profile film), next to the console
on the linkup network; scripts/demo-capture.sh is the entry point and owns
everything outside the browser (the reset, the host beats, takes.json).

    capture.py --beats beats.yaml --console http://console:8000 --nin <nin> --out /out

Writes, per browser beat: <id>.png (frame), <id>.txt (text), or <id>.frames/
plus <id>.png (clip: one PNG per shot and a concat list with the real time
between shots, which the host turns into <id>.mp4 -- this image has no
H.264 encoder). Screenshots instead of Playwright's own recorder: that one is
low-bitrate VP8, and small text is what these frames exist to show.

Then beats.json: id -> {file, kind, caption}. A failed assertion exits 1 naming
the beat and what the page showed; the failing beat writes nothing.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import yaml
from playwright.sync_api import Page, sync_playwright

# 1280x720 CSS pixels at scale 1.5: the PNG is still 1920x1080, but the page
# lays out and renders its text 1.5x larger -- at 1920x1080 scale 1 the
# console's 16px text is a few pixels tall once the frame is on a slide on a phone.
VIEWPORT = {"width": 1280, "height": 720}
SCALE = 1.5
TIMEOUT_MS = 30_000
HEADERS = {"X-KP2-Console": "1"}


class BeatFailed(Exception):
    pass


def fill(value, nin):
    return value.replace("{nin}", nin) if isinstance(value, str) else value


def check(page: Page, assertions, nin, context=None):
    for a in assertions:
        if "js" in a:
            got = page.evaluate(f"(receipts) => ({a['js']})", context)
            if got != a["equals"]:
                raise BeatFailed(f"{a['js']} -> {got!r}, expected {a['equals']!r}")
            continue
        loc = page.locator(fill(a["selector"], nin))
        texts = loc.all_inner_texts()
        if "count" in a and len(texts) != a["count"]:
            raise BeatFailed(f"{a['selector']}: {len(texts)} match(es), expected {a['count']}: {texts!r}")
        if "text" in a:
            want = fill(a["text"], nin)
            if not texts or any(want not in t for t in texts):
                raise BeatFailed(f"{a['selector']}: expected every match to contain {want!r}, got {texts!r}")
        if "visible" in a and loc.first.is_visible() != a["visible"]:
            raise BeatFailed(f"{a['selector']}: visible={not a['visible']}, expected {a['visible']}")


class Clip:
    """Screenshots at whatever pace the page allows, each held for the real time until the next."""

    def __init__(self, out: Path, beat_id: str):
        self.dir = out / f"{beat_id}.frames"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.shots: list[tuple[Path, float]] = []
        self.still: Path | None = None
        self.pin: str | None = None

    def shoot(self, page: Page):
        path = self.dir / f"{len(self.shots):05d}.png"
        page.mouse.move(0, 0)
        if self.pin:
            scroll_to(page, self.pin)
        page.screenshot(path=str(path))
        self.shots.append((path, time.monotonic()))

    def shoot_until(self, page: Page, expression: str, timeout_s=TIMEOUT_MS / 1000):
        deadline = time.monotonic() + timeout_s
        while True:
            # A re-render in filming mode holds its "before" beat for step();
            # inside a clip nobody is waiting to photograph that, so release it.
            page.evaluate("window.kp2film.waiting() && window.kp2film.step()")
            done = page.evaluate(expression)
            self.shoot(page)   # after the check, so the last shot shows the state it confirmed
            if done:
                return
            if time.monotonic() > deadline:
                raise BeatFailed(f"record_until timed out: {expression}")

    def shoot_for(self, page: Page, ms: int):
        end = time.monotonic() + ms / 1000
        while time.monotonic() < end:
            self.shoot(page)

    def finish(self):
        """frames.txt for ffmpeg's concat demuxer: each shot held until the next was taken."""
        lines = []
        for (path, t), (_, t_next) in zip(self.shots, self.shots[1:] + [(None, self.shots[-1][1] + 0.2)]):
            lines += [f"file '{path.name}'", f"duration {t_next - t:.3f}"]
        lines.append(f"file '{self.shots[-1][0].name}'")
        (self.dir / "frames.txt").write_text("\n".join(lines) + "\n")


def scroll_to(page: Page, selector: str):
    page.evaluate("(s) => document.querySelector(s).scrollIntoView({block: 'start'})", selector)


def render_receipts(calls):
    """The two bus calls as a terminal would show them: request, status, the body verbatim."""
    out = []
    for c in calls:
        out.append(f"GET {c['url']}")
        out.append(f"  -> {c['status_code']}  {c['headers'].get('content-type', '')}")
        out += ["  " + ln for ln in json.dumps(c["body"], indent=2, ensure_ascii=False).splitlines()]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def run_beat(page: Page, beat, args, out: Path):
    nin, clip, text = args.nin, None, None
    for action in beat.get("actions", []):
        (verb, arg), = action.items()
        arg = fill(arg, nin)
        if verb == "goto":
            page.goto(args.console + arg)
        elif verb == "tab":
            page.click(f".tab-btn[data-tab='{arg}']")
        elif verb == "click":
            page.click(arg)
        elif verb == "wait":
            page.wait_for_selector(arg, state="visible")
        elif verb == "wait_js":
            page.wait_for_function(arg)
        elif verb == "step":
            page.wait_for_function("window.kp2film.waiting()")
            page.evaluate("window.kp2film.step()")
        elif verb == "scroll":
            scroll_to(page, arg)
        elif verb == "expect":
            check(page, arg, nin)
        elif verb == "receipts":
            resp = page.request.get(args.console + arg, headers=HEADERS)
            text = resp.json()["calls"]
        elif verb in ("pin", "record_until", "record_for", "still"):
            clip = clip or Clip(out, beat["id"])
            if verb == "pin":
                clip.pin = arg
            elif verb == "record_until":
                clip.shoot_until(page, arg)
            elif verb == "record_for":
                clip.shoot_for(page, arg)
            else:
                clip.still = clip.shots[-1][0]
        else:
            raise BeatFailed(f"unknown action {verb!r}")
        page.mouse.move(0, 0)   # no hover state left on whatever was clicked

    check(page, beat.get("assert", []), nin, context=text)

    kind, beat_id = beat["kind"], beat["id"]
    if kind == "frame":
        page.screenshot(path=str(out / f"{beat_id}.png"))
        return f"{beat_id}.png"
    if kind == "text":
        (out / f"{beat_id}.txt").write_text(render_receipts(text))
        return f"{beat_id}.txt"
    if kind == "clip":
        clip.finish()
        (out / f"{beat_id}.png").write_bytes((clip.still or clip.shots[-1][0]).read_bytes())
        return f"{beat_id}.mp4"
    raise BeatFailed(f"unknown kind {kind!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--beats", required=True)
    ap.add_argument("--console", required=True)
    ap.add_argument("--nin", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    beats = [b for b in yaml.safe_load(open(args.beats))["beats"] if not b.get("host")]

    written = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--hide-scrollbars", "--font-render-hinting=none"])
        page = browser.new_context(viewport=VIEWPORT, device_scale_factor=SCALE, color_scheme="light",
                                   locale="en-GB", timezone_id="UTC").new_page()
        page.set_default_timeout(TIMEOUT_MS)
        for beat in beats:
            try:
                written[beat["id"]] = {"file": run_beat(page, beat, args, out), "kind": beat["kind"],
                                       "caption": beat["caption"]}
            except Exception as exc:   # noqa: BLE001 -- any failure names its beat
                shutil.rmtree(out / f"{beat['id']}.frames", ignore_errors=True)   # nothing for a failed beat
                snap = out / f"FAILED-{beat['id']}.png"
                page.screenshot(path=str(snap))
                sys.exit(f"beat {beat['id']} failed: {exc}\n  the page at that moment: {snap.name}")
            print(f"beat {beat['id']}: {written[beat['id']]['file']}", flush=True)
        browser.close()
    (out / "beats.json").write_text(json.dumps(written, indent=2) + "\n")


if __name__ == "__main__":
    main()
