"""Horizontal field of view of the phone camera, from a photo of something of known width at a known distance.

Take the photo the way the app does (portrait, rear camera, same zoom), with the object spanning a clearly measurable
part of the image width: a ruler or A4 sheet held flat and square to the camera. Measure the object's width in the
image in pixels (any image viewer) and pass the image width too. Two or more shots at different distances are averaged.

  python scripts/calibrate_fov.py --object-cm 29.7 --image-width 1080 --shot 40 612 --shot 70 352

Each --shot is DISTANCE_CM PIXELS. Prints the value to set as HFOV_DEG before starting the server.
"""
import argparse
import math
import statistics


def hfov(object_cm: float, distance_cm: float, pixels: float, image_width: float) -> float:
    focal_px = pixels * distance_cm / object_cm  # pinhole: pixels = f * object / distance
    return math.degrees(2 * math.atan(image_width / (2 * focal_px)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--object-cm", type=float, required=True, help="real width of the object")
    ap.add_argument("--image-width", type=float, required=True, help="width of the photo in pixels")
    ap.add_argument("--shot", type=float, nargs=2, action="append", metavar=("DISTANCE_CM", "PIXELS"), required=True)
    a = ap.parse_args()
    each = [hfov(a.object_cm, d, px, a.image_width) for d, px in a.shot]
    for (d, px), v in zip(a.shot, each):
        print(f"  {d:g} cm, {px:g} px -> {v:.1f} deg")
    best = statistics.mean(each)
    spread = max(each) - min(each)
    print(f"HFOV_DEG={best:.1f}" + (f"   (shots disagree by {spread:.1f} deg: re-measure)" if spread > 3 else ""))
    print("Set it before starting the server, for example:  $env:HFOV_DEG = '%.1f'; python backend/run.py" % best)


if __name__ == "__main__":
    main()
