import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

# Tests must not depend on a calibration saved on this machine.
os.environ.pop("HFOV_DEG", None)
os.environ["FOV_CALIBRATION"] = str(Path(__file__).resolve().parent / "no_such_calibration.json")
