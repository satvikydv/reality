"""Look-alikes (app/lookalike.py): which same-name pairs are worth a model call, and what each answer does to the scan."""
import asyncio

import cv2
import numpy as np
import pytest

from app import lookalike as L
from app.reasoner.base import Result
from app.world_model import WorldModel

W, H = 480, 640
CENTRE = [220.0, 300.0, 260.0, 340.0]
POLY = [[220, 300], [260, 300], [260, 340], [220, 340]]
JPEG = cv2.imencode(".jpg", np.random.default_rng(1).integers(0, 255, (H, W, 3), dtype=np.uint8))[1].tobytes()


def o(i, label, yaw, pitch=0.0, unc=3.0, first=None):
    return {"id": i, "label": label, "yaw": yaw, "pitch": pitch, "uncertainty_deg": unc, "first_seen": first if first is not None else float(i), "n_obs": 2}


# ------------------------------------------------------------------ which pairs are asked

def test_each_object_is_paired_with_its_nearest_look_alike_only():
    objs = [o(1, "laptop", 0), o(2, "laptop", 10), o(3, "laptop", 60), o(4, "mug", 5)]
    ask, decided = L.candidate_pairs(objs, {})
    assert [p.key for p in ask] == ["1-2"]                      # 1 and 2 are each other's nearest, inside the radius
    assert [(p.key, p.decided) for p in decided] == [("2-3", "different")]  # 3's nearest is 2, 50 degrees away: no call
    assert not any("1-3" == p.key for p in ask + decided)       # never all pairs: 3 laptops give 2 pairs here, not 3


def test_two_objects_seen_in_one_photo_are_different_without_a_call():
    ask, decided = L.candidate_pairs([o(1, "bag", 0), o(2, "bag", 6)], {1: {10, 11}, 2: {11}})
    assert ask == [] and decided[0].decided == "different" and "one photo" in decided[0].why


def test_the_radius_follows_the_uncertainty_within_bounds():
    assert L.radius_deg(1, 1) == L.MIN_RADIUS_DEG
    assert L.radius_deg(5, 3) == pytest.approx(20.0)
    assert L.radius_deg(30, 30) == L.MAX_RADIUS_DEG


def test_calls_are_capped_closest_pairs_first():
    objs = [o(i * 2 + 1, f"thing{i}", 0) for i in range(4)] + [o(i * 2 + 2, f"thing{i}", 4 + i) for i in range(4)]
    ask, _ = L.candidate_pairs(objs, {}, max_calls=2)
    assert [p.sep_deg for p in ask][:2] == sorted(p.sep_deg for p in ask)[:2]
    assert [p.verdict for p in ask] == [None, None, "skipped", "skipped"]


def test_names_number_objects_of_one_kind_in_the_order_the_scan_saw_them():
    names = L.display_names([o(5, "laptop", 0, first=3.0), o(2, "laptop", 40, first=1.0), o(9, "mug", 0)])
    assert names == {2: "laptop 1", 5: "laptop 2", 9: "mug"}


# ------------------------------------------------------------------ what an answer does

class FakeModel:
    def __init__(self, verdict, confidence=0.9, fail=False):
        self.verdict, self.confidence, self.fail, self.calls = verdict, confidence, fail, []

    async def respond(self, messages, *, tools=None, schema=None, effort="low", max_output_tokens=4000):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("model down")
        return Result(text="", parsed={"verdict": self.verdict, "confidence": self.confidence, "reason": "because"}, input_tokens=900, output_tokens=40)


def two_bottles(tmp_path):
    """Bottle A (yaw 0 and 2) and bottle B (yaw 15), never in one photo: geometry cannot tell; a cup far away."""
    wm = WorldModel(tmp_path / "w.db", frames_dir=tmp_path / "frames")
    rid = wm.create_room("R")["id"]
    sid = wm.start_scan(room_id=rid, ts=1.0)
    for yaw, ts, label, conf in ((0, 1.0, "bottle", 0.9), (2, 2.0, "bottle", 0.7), (15, 3.0, "bottle", 0.8), (90, 4.0, "cup", 0.6)):
        fid = wm.add_frame(sid, ts, yaw, 0.0, W, H, roll=0.0, screen_angle=0.0, jpeg=JPEG)
        wm.observe(fid, [{"label": label, "conf": conf, "box": CENTRE, "polygon": POLY}])
    wm.end_scan(sid, 5.0)
    wm.rebuild(sid)
    return wm, sid


def labels(wm, sid):
    return sorted(x["label"] for x in wm.list_objects(sid))


def test_a_sure_same_answer_merges_the_pair_and_survives_a_rebuild(tmp_path):
    wm, sid = two_bottles(tmp_path)
    model, events = FakeModel("same"), []

    async def emit(m):
        events.append(m)
    rep = asyncio.run(L.run(wm, model, sid, emit))
    assert rep["merged"] == 1 and labels(wm, sid) == ["bottle", "cup"] and rep["usd"] > 0
    wm.rebuild(sid)
    assert labels(wm, sid) == ["bottle", "cup"]  # kept through the correction log, like a merge made by hand
    assert [e["phase"] for e in events] == ["start", "result"] and events[0]["checking"][0]["a"]["replay"]["frame_id"]
    parts = model.calls[0][1]["content"]
    assert "15°" in parts[0]["text"] and sum(p["type"] == "image" for p in parts) >= 2  # directions and both pictures


@pytest.mark.parametrize("verdict,conf", [("different", 0.9), ("unsure", 0.9), ("same", 0.5)])
def test_anything_short_of_a_sure_same_keeps_both(tmp_path, verdict, conf):
    wm, sid = two_bottles(tmp_path)
    rep = asyncio.run(L.run(wm, FakeModel(verdict, conf), sid))
    assert rep["merged"] == 0 and labels(wm, sid) == ["bottle", "bottle", "cup"]
    logged = [c["kind"] for c in wm.list_corrections(scan_id=sid)]
    assert logged == (["distinct"] if verdict == "different" else [])


def test_a_failed_call_changes_nothing(tmp_path):
    wm, sid = two_bottles(tmp_path)
    rep = asyncio.run(L.run(wm, FakeModel("same", fail=True), sid))
    assert rep["merged"] == 0 and labels(wm, sid) == ["bottle", "bottle", "cup"]
    assert rep["pairs"][0]["verdict"] == "unsure"


def test_no_look_alikes_means_no_call_and_no_message(tmp_path):
    wm = WorldModel(tmp_path / "w.db", frames_dir=tmp_path / "frames")
    sid = wm.start_scan(room_id=wm.create_room("R")["id"], ts=1.0)
    fid = wm.add_frame(sid, 1.0, 0, 0.0, W, H, roll=0.0, screen_angle=0.0, jpeg=JPEG)
    wm.observe(fid, [{"label": "cup", "conf": 0.9, "box": CENTRE, "polygon": POLY}])
    model, events = FakeModel("same"), []

    async def emit(m):
        events.append(m)
    asyncio.run(L.run(wm, model, sid, emit))
    assert model.calls == [] and events == []


def test_the_overlay_carries_numbered_names(tmp_path):
    wm, sid = two_bottles(tmp_path)
    assert sorted(x["name"] for x in wm.overlay_objects(sid)) == ["bottle 1", "bottle 2", "cup"]
