"""The evaluation script, on its synthetic room: it must run, report every number the protocol asks for, and meet the thresholds the code claims."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eval_alignment  # noqa: E402


def test_the_synthetic_evaluation_reports_the_protocol_numbers_and_meets_its_thresholds():
    res = eval_alignment.run_synthetic(seed=1)
    names = [r["name"] for r in res["scenarios"]]
    assert names == ["same spot", "20 cm sideways (simulated parallax)", "start heading +90", "two objects moved"]
    assert all(r["quality"] == "good" for r in res["scenarios"])
    assert res["yaw_offset_error_deg"]["max"] <= 1.0 and res["yaw_offset_error_deg"]["n"] == 4        # yaw-offset error
    assert res["false_move_rate"] == 0 and res["objects_compared"] >= 8                              # false-move rate
    assert res["true_move_recall"] == 1.0 and res["true_moves"] == 2                                  # true-move recall
    sideways = res["scenarios"][1]
    assert sideways["moved"] == [] and len(sideways["possibly_moved"]) >= 1                           # a 20 cm step is doubt, not a move
    assert "possibly_moved_total" in res and "cant_compare_total" in res
    text = eval_alignment.report(res)
    assert "False-move rate" in text and "True-move recall" in text and "Possibly moved" in text and "Can't compare" in text
