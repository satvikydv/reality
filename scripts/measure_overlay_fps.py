"""Overlay frame rate and draw time in headless Chrome: Calm, Normal and Debug, on a few and on many remembered objects, with and without CPU throttling.

Headless Chrome on this machine is not a phone: use the ratios between modes, and the throttled rows (a rough stand-in for a slower
CPU), not the absolute numbers. The page runs in demo mode (no server data, no camera), drawing the same overlay code the phone runs.

    python scripts/measure_overlay_fps.py [--seconds 4]
"""
from __future__ import annotations

import argparse
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("BIND_HOST", "127.0.0.1")

import uvicorn  # noqa: E402

from app import main  # noqa: E402
from test_entry_flow import BROWSER, Page, free_port  # noqa: E402


def measure(page: Page, mode: str, objects: int, throttle: int, seconds: float) -> dict:
    page.call("Emulation.setCPUThrottlingRate", rate=throttle)
    page.call("Page.navigate", url=f"{page.base}/?demo&screen=explore&objects={objects}&fov=100&yaw=40")
    page.wait("!!window.__ui", what="the page")
    page.eval(f"localStorage.setItem('ros.detail','{mode}')")
    page.call("Page.reload")
    page.wait("!!window.__ui && window.__ui.screen()==='explore'", what="the explore screen")
    time.sleep(1.5)  # warm up
    fps, draw, labels, drawn = [], [], [], []
    end = time.time() + seconds
    while time.time() < end:
        s = page.eval("window.__ar.stats()")
        fps.append(s["fps"]); draw.append(s["drawMs"]); drawn.append(s.get("drawn", 0)); labels.append(s.get("labels", s.get("drawn", 0)))
        time.sleep(0.25)
    return {"fps": statistics.median(fps), "draw_ms": statistics.median(draw), "labels": statistics.median(labels), "objects": s["objects"]}


def main_() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=4.0)
    a = ap.parse_args()
    if BROWSER is None:
        sys.exit("no Chrome or Edge found (set CHROME_BIN)")
    port, cport = free_port(), free_port()
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    while not server.started:
        time.sleep(0.05)
    prof = tempfile.mkdtemp(prefix="ros-fps-")
    proc = subprocess.Popen([BROWSER, "--headless=new", f"--remote-debugging-port={cport}", f"--user-data-dir={prof}", "--no-first-run", "--disable-gpu", "--mute-audio",
                             "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{cport}/json/version", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    try:
        page = Page(f"http://127.0.0.1:{port}", cport, {"ros.onboarded": "1", "ros.primed": "1"})
        rows = []
        for throttle in (1, 6):
            for objects in (16, 60):
                for mode in ("calm", "normal", "debug"):
                    rows.append((throttle, objects, mode, measure(page, mode, objects, throttle, a.seconds)))
        print("| CPU throttle | Objects | Mode | fps (median) | Draw ms (median) | Labels shown (median) |\n|---|---|---|---|---|---|")
        for t, n, m, r in rows:
            print(f"| {t}x | {n} | {m} | {r['fps']:.0f} | {r['draw_ms']:.2f} | {r['labels']:.0f} |")
        page.close()
    finally:
        proc.kill()
        server.should_exit = True
        th.join(timeout=10)
        shutil.rmtree(prof, ignore_errors=True)


if __name__ == "__main__":
    main_()
