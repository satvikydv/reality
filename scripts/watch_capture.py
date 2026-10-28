"""Live coach for phone captures: one line per Save sample, plus USB and server health.

Watches data/frames for new Save-sample sidecars and says what to fix (turn step too small or too
big, camera tilted, phone rolled, stale sensor, dark or tiny image). Re-creates `adb reverse` if
the USB link dropped and came back. Prints only changes and new frames.

    python -u scripts/watch_capture.py [--step 15] [--frames data/frames]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=8).stdout
    except Exception as e:  # noqa: BLE001
        return f"ERR {e}"


def wrap180(d: float) -> float:
    return (d + 180) % 360 - 180


def health() -> tuple[str, str, str]:
    dev = "none"
    for line in run("adb", "devices").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            dev = parts[1]  # device / unauthorized / offline
    rev = "8000" if "tcp:8000" in run("adb", "reverse", "--list") else "missing"
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/healthz", timeout=2).read()
        srv = "up"
    except Exception:  # noqa: BLE001
        srv = "down"
    return dev, rev, srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=Path, default=ROOT / "data" / "frames")
    ap.add_argument("--step", type=float, default=15.0, help="intended yaw step between shots, degrees")
    ap.add_argument("--health-only", action="store_true", help="only report USB / port forward / server changes (for the auto scan)")
    a = ap.parse_args()
    seen = {p.name for p in a.frames.glob("*.json")}
    prev = None  # (yaw, t)
    total, n, last_dir = 0.0, 0, 0
    last_health = None
    print(f"watching {a.frames} ({len(seen)} older frames ignored); target turn per shot ~{a.step:.0f} deg")
    while True:
        h = health()
        if h != last_health:
            dev, rev, srv = h
            msg = []
            if dev != "device":
                msg.append(f"PHONE NOT CONNECTED over USB (adb says {dev}): replug the cable, unlock the screen")
            elif rev == "missing":
                run("adb", "reverse", "tcp:8000", "tcp:8000")
                msg.append("USB was back but the port forward was gone; re-created adb reverse, reload the page on the phone")
            if srv == "down":
                msg.append("SERVER DOWN on 127.0.0.1:8000: start it with  $env:PLAIN_HTTP='1'; .\.venv\Scripts\python.exe backend\run.py")
            print("health: " + ("; ".join(msg) if msg else "phone connected, port forwarded, server up"), flush=True)
            last_health = h
        for p in ([] if a.health_only else sorted(a.frames.glob("*.json"), key=lambda p: p.stat().st_mtime)):
            if p.name in seen:
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                continue  # still being written
            seen.add(p.name)
            n += 1
            o = (d.get("orientation") or {}).get("absolute") or (d.get("orientation") or {}).get("relative")
            jpg = p.with_suffix(".jpg")
            kb = jpg.stat().st_size / 1024 if jpg.exists() else 0
            if not o:
                print(f"#{n} {p.stem[-12:]}: NO ORIENTATION in sidecar; was Start camera tapped before Save sample?", flush=True)
                continue
            yaw, pitch, roll, age = o["yaw"], o["pitch"], o.get("roll") or 0.0, o.get("age_ms") or 0
            tips = []
            step = None
            if prev is not None:
                step = wrap180(yaw - prev)
                dirn = 1 if step > 0 else -1
                if abs(step) < 5:
                    tips.append(f"barely turned ({step:+.0f} deg): turn about {a.step:.0f} deg before the next shot")
                elif abs(step) > 2 * a.step:
                    tips.append(f"turned {step:+.0f} deg since last shot, more than 2x the step: frames will not overlap, shoot more often or go back")
                if last_dir and abs(step) >= 5 and dirn != last_dir:
                    tips.append("direction reversed; keep turning one way")
                if abs(step) >= 5:
                    last_dir = dirn
                    total += step
            if abs(pitch) > 30:
                tips.append(f"camera pitch {pitch:+.0f} deg: aim closer to level (pointing {'down' if pitch < 0 else 'up'} a lot)")
            if abs(roll) > 15:
                tips.append(f"phone rolled {roll:+.0f} deg: hold it upright")
            if age > 200:
                tips.append(f"orientation reading is {age:.0f} ms old: hold still a moment before tapping")
            if kb and kb < 200:
                tips.append(f"image only {kb:.0f} KB: dark or blurry? hold steadier / more light")
            if (d.get("camera") or {}).get("screen_angle") not in (0, None):
                tips.append(f"screen rotated {d['camera']['screen_angle']} deg: keep the phone in portrait")
            prev = yaw
            print(f"#{n} {p.stem[-12:]}: yaw {yaw:5.1f} ({'first' if step is None else f'{step:+.0f}'}) pitch {pitch:+.0f} roll {roll:+.0f} {kb:.0f} KB  "
                  f"turned {total:+.0f} deg so far  " + ("OK" if not tips else "FIX: " + "; ".join(tips)), flush=True)
        time.sleep(1.0)


if __name__ == "__main__":
    main()
