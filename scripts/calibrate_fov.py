"""Calibrate the camera's horizontal field of view at the exact capture state, and save it for the server.

Photograph something of known width (an A4 sheet is 29.7 cm long, a ruler works too) held flat and square to the phone,
at two or more distances, using the app's "Save sample" button (the ... menu on the home screen). Do not touch zoom or
switch lenses between shots. The photos land in data/frames/ with a .json sidecar that records the camera state.
Measure the object's width in pixels in each photo (any image viewer), then:

  python scripts/calibrate_fov.py --object-cm 29.7 \
      --shot 40 612 data/frames/phone-20261029-101500-1.json \
      --shot 70 352 data/frames/phone-20261029-101530-2.json

Each --shot is DISTANCE_CM PIXELS SIDECAR_JSON. The result is written to data/fov_calibration.json together with the
resolution and zoom it is valid for; the server loads it on start and refuses frames from a camera in another state.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app.fovcal import CalibrationError, calibrate  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--object-cm", type=float, required=True, help="real width of the object")
    ap.add_argument("--shot", nargs=3, action="append", metavar=("DISTANCE_CM", "PIXELS", "SIDECAR"), required=True)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "data" / "fov_calibration.json"))
    ap.add_argument("--dry-run", action="store_true", help="print the result without saving it")
    a = ap.parse_args()
    try:
        shots = [{"distance_cm": float(d), "pixels": float(px), "sidecar": json.loads(Path(sc).read_text(encoding="utf-8"))} for d, px, sc in a.shot]
        cal = calibrate(shots, a.object_cm)
    except (CalibrationError, OSError, ValueError) as e:
        print(f"calibration failed: {e}", file=sys.stderr)
        return 1
    for (d, px, _), v in zip(a.shot, cal["shots"]):
        print(f"  {float(d):g} cm, {float(px):g} px -> {v:.1f} deg")
    print(f"HFOV {cal['hfov_deg']} deg at {cal['width']}x{cal['height']}, zoom {cal['zoom']}"
          + (f"   WARNING: shots disagree by {cal['spread_deg']} deg, re-measure" if cal["spread_deg"] > 3 else ""))
    if a.dry_run:
        return 0
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cal, indent=2), encoding="utf-8")
    print(f"saved {out}; restart the server to use it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
