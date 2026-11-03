"""The evaluation script, on its synthetic worlds: it must run, report every number the protocol asks for, and meet the thresholds the code claims."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eval_alignment  # noqa: E402


def test_the_synthetic_evaluation_reports_the_protocol_numbers_and_meets_its_thresholds():
    res = eval_alignment.run_synthetic(seed=1)
    names = [r["name"] for r in res["scenarios"]]
    assert names == ["same spot", "start heading +90", "two objects moved"]
    assert all(r["quality"] == "good" for r in res["scenarios"])
    assert res["yaw_offset_error_deg"]["max"] <= 1.0 and res["yaw_offset_error_deg"]["n"] == 3        # yaw-offset error
    assert res["false_move_rate"] == 0 and res["objects_compared"] >= 8                              # false-move rate
    assert res["true_move_recall"] == 1.0 and res["true_moves"] == 2                                  # true-move recall
    assert "possibly_moved_total" in res and "cant_compare_total" in res
    text = eval_alignment.report(res)
    assert "False-move rate" in text and "True-move recall" in text and "Possibly moved" in text and "Can't compare" in text


def test_the_near_field_evaluation_runs_every_scenario_and_reports_the_counts():
    res = eval_alignment.run_near_field_seeds([1])
    rows = {r["name"]: r for r in res["runs"][0]["scenarios"]}
    assert list(rows) == eval_alignment.NEAR_NAMES
    assert res["false_moves"] == 0 and res["false_added"] == 0 and res["false_removed"] == 0        # nothing claimed that did not happen
    assert rows["desk, 20 cm sideways"]["moved"] == []                                               # a step to the side is not a move
    assert rows["desk, start heading +90"]["quality"] == "good" and rows["desk, start heading +90"]["offset_error_deg"] <= 3.0
    assert res["true_found"] + res["true_found_low_confidence"] >= 2                                 # the moved mouse and phone are found
    text = eval_alignment.report_seeds(res)
    assert "False moves" in text and "moved (low confidence)" in text and "Can't compare" in text
