import asyncio

import cv2
import numpy as np
import pytest

from app import anchoring as A
from app.reasoner.base import Result
from app.sam_client import SamCall, SamObject
from app.scan import ScanPipeline, clean_names, iou, pose_from_meta, resize_jpeg, suppress_overlaps
from app.world_model import WorldModel


def run(coro):
    return asyncio.run(coro)


def jpeg(w=480, h=640):
    ok, buf = cv2.imencode(".jpg", np.full((h, w, 3), 90, dtype=np.uint8))
    return buf.tobytes()


class FakeReasoner:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def respond(self, messages, **kw):
        self.calls.append(messages)
        return Result(text="", parsed={"objects": self.replies.pop(0)}, input_tokens=1000, output_tokens=100, latency_ms=5.0)


def sam_ok(*items):
    """items: (x1, y1, x2, y2, conf)"""
    return SamCall([SamObject(str(i), tuple(b[:4]), b[4], None) for i, b in enumerate(items)], 10.0, 5.0, "completed", 0)


def segmenter(table, log=None):
    async def seg(data_url, concept):
        if log is not None:
            log.append(concept)
        v = table[concept]
        return v.pop(0) if isinstance(v, list) else v
    return seg


@pytest.fixture
def wm():
    m = WorldModel()
    yield m
    m.close()


POSE = {"yaw": 100.0, "pitch": 0.0, "roll": 0.0, "screen_angle": 0, "source": "absolute", "api": "sensor:Abs"}


def test_clean_names():
    assert clean_names(["Water Bottle", "the person", "laptop", "keys, wallet", "laptop", "a very long object name here", "Desk", ""], 5) == ["water bottle", "laptop"]
    assert clean_names(["cup", "bag", "pen"], 2) == ["cup", "bag"]
    assert clean_names(["Smartphone", "phone", "Computer Mouse", "mouse"], 5) == ["phone", "mouse"]  # synonyms collapse


def test_suppress_overlaps():
    a = {"label": "bottle", "box": [0, 0, 100, 200], "conf": 0.90}
    b = {"label": "water bottle", "box": [2, 2, 100, 198], "conf": 0.88}  # same object, more specific, within tie
    c = {"label": "laptop", "box": [300, 300, 400, 400], "conf": 0.7}
    out = suppress_overlaps([a, b, c])
    assert sorted(d["label"] for d in out) == ["laptop", "water bottle"]
    low = {"label": "phone", "box": [0, 0, 100, 200], "conf": 0.6}
    assert sorted(d["label"] for d in suppress_overlaps([a, low])) == ["bottle"]
    assert iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0


def test_pose_from_meta():
    meta = {"orientation": {"absolute": {"yaw": 10, "pitch": -5, "roll": 1, "api": "A"}, "relative": {"yaw": 99, "pitch": 0, "roll": 0}}, "camera": {"screen_angle": 90}}
    p = pose_from_meta(meta)
    assert (p["yaw"], p["source"], p["screen_angle"]) == (10, "absolute", 90)
    assert pose_from_meta(meta, prefer="relative")["yaw"] == 99
    only_rel = {"orientation": {"absolute": None, "relative": {"yaw": 7, "pitch": 0}}}
    assert pose_from_meta(only_rel)["source"] == "relative"
    assert pose_from_meta({}) is None


def test_resize_keeps_aspect():
    small, w, h = resize_jpeg(jpeg(1080, 1920), 960)
    assert (w, h) == (540, 960) and cv2.imdecode(np.frombuffer(small, np.uint8), 1).shape[:2] == (960, 540)
    assert resize_jpeg(jpeg(), 960)[1:] == (480, 640)


def test_process_anchors_detections_in_world_model(wm):
    s = wm.start_scan()
    seg = segmenter({"mug": sam_ok((220, 300, 260, 340, 0.9)), "laptop": sam_ok((0, 0, 40, 40, 0.3), (300, 400, 380, 460, 0.95))})
    pipe = ScanPipeline(wm, FakeReasoner(["Mug", "laptop", "person"]), seg)
    r = run(pipe.process(s, jpeg(), 1.0, POSE))
    assert r.stored and r.names == ["mug", "laptop"] and r.dropped_low_conf == 1 and r.sam_calls == 2
    assert sorted(d["label"] for d in r.detections) == ["laptop", "mug"]
    mug = wm.find("mug")[0]
    assert A.angular_distance(mug["yaw"], mug["pitch"], 100.0, 0.0) < 1.0  # box centre is the image centre
    assert wm.get_frame(r.frame_id)["width"] == 480 and wm.frame_jpeg(r.frame_id) is None  # no frames_dir configured
    assert r.usd == pytest.approx(1000 / 1e6 * 1.25 + 100 / 1e6 * 4.25 + 2 * 0.0025)


def test_second_frame_gets_known_names_and_merges(wm):
    s = wm.start_scan()
    fake = FakeReasoner(["mug"], ["mug"])
    pipe = ScanPipeline(wm, fake, segmenter({"mug": sam_ok((220, 300, 260, 340, 0.9))}))
    run(pipe.process(s, jpeg(), 1.0, POSE))
    r = run(pipe.process(s, jpeg(), 2.0, POSE))
    assert "Known names: mug" in fake.calls[1][1]["content"][0]["text"] and "Known names" not in fake.calls[0][1]["content"][0]["text"]
    assert len(wm.list_objects(s)) == 1 and wm.list_objects(s)[0]["n_obs"] == 2 and not r.observed[0]["new"]


def test_sam_error_retries_then_succeeds(wm):
    s = wm.start_scan()
    bad = SamCall([], 1.0, None, "error", 0, "boom")
    log = []
    pipe = ScanPipeline(wm, FakeReasoner(["mug"]), segmenter({"mug": [bad, sam_ok((220, 300, 260, 340, 0.9))]}, log))
    r = run(pipe.process(s, jpeg(), 1.0, POSE))
    assert r.stored and r.sam_calls == 2 and log == ["mug", "mug"] and r.errors == []


def test_failed_sam_call_means_frame_not_stored(wm):
    s = wm.start_scan()
    bad = SamCall([], 1.0, None, "error", 0, "boom")
    pipe = ScanPipeline(wm, FakeReasoner(["mug", "laptop"]), segmenter({"mug": bad, "laptop": sam_ok((0, 0, 50, 50, 0.9))}))
    r = run(pipe.process(s, jpeg(), 1.0, POSE))
    assert not r.stored and r.frame_id is None and r.errors == ["mug: boom"]
    assert wm.list_frames(s) == [] and wm.list_objects(s) == []  # an incomplete look must not count as scanned


def test_resized_frame_is_anchored_with_resized_dims(wm):
    s = wm.start_scan()
    pipe = ScanPipeline(wm, FakeReasoner(["mug"]), segmenter({"mug": sam_ok((250, 460, 290, 500, 0.9))}), max_side=960)
    r = run(pipe.process(s, jpeg(1080, 1920), 1.0, POSE))
    f = wm.get_frame(r.frame_id)
    assert (f["width"], f["height"]) == (540, 960)
    mug = wm.find("mug")[0]
    assert A.angular_distance(mug["yaw"], mug["pitch"], 100.0, 0.0) < 1.0  # centre of 540x960 is (270, 480)


def test_merge_parts_joins_boxes_of_one_thing_only():
    from app.scan import merge_parts
    d = lambda label, box, conf=0.9: {"label": label, "box": box, "conf": conf}  # noqa: E731
    out = merge_parts([d("laptop", [0, 0, 100, 100], 0.9), d("laptop", [20, 20, 60, 60], 0.7), d("laptop", [20, 20, 180, 100], 0.8)])
    assert len(out) == 1 and out[0]["box"] == [0, 0, 180, 100] and out[0]["conf"] == 0.9  # nested box, then one that overlaps the union
    apart = merge_parts([d("mug", [0, 0, 50, 50]), d("mug", [200, 0, 250, 50])])
    assert len(apart) == 2  # two mugs side by side stay two
    mixed = merge_parts([d("laptop", [0, 0, 300, 200]), d("cup", [100, 100, 150, 150])])
    assert {x["label"] for x in mixed} == {"laptop", "cup"}  # a cup in front of a laptop is not part of it


def test_raster_polygon_outlines_the_blob_in_image_pixels():
    from app.sam_client import raster_polygon
    r = np.zeros((40, 60), np.uint8)
    r[10:30, 20:50] = 1  # a 30 x 20 rectangle inside a 60 x 40 mask
    poly = raster_polygon(r, left=100.0, top=200.0, scale_x=2.0, scale_y=2.0)
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    assert len(poly) <= 32 and 4 <= len(poly)
    assert min(xs) == pytest.approx(100 + (20 + .5) * 2) and max(xs) == pytest.approx(100 + (49 + .5) * 2)
    assert min(ys) == pytest.approx(200 + (10 + .5) * 2) and max(ys) == pytest.approx(200 + (29 + .5) * 2)
    assert raster_polygon(np.zeros((5, 5), np.uint8), 0, 0) is None
    ring = np.zeros((200, 200), np.uint8)
    cv2.circle(ring, (100, 100), 80, 1, -1)
    assert 8 <= len(raster_polygon(ring, 0, 0)) <= 32  # a circle is simplified, not stored as hundreds of points


def test_mask_info_reads_a_real_encoded_mask():
    from meta_sam_parser import SegmentationMask, SegmentationMaskBounds
    from meta_sam_parser._mask_codec import _encode_raster
    from app.sam_client import mask_info
    r = np.zeros((30, 40), np.uint8)
    r[5:25, 10:30] = 1
    mask = SegmentationMask("one_bit", _encode_raster(r.flatten().tolist(), 40, 30), 40, 30)
    area, poly = mask_info(mask, SegmentationMaskBounds(left=100, top=200, right=140, bottom=230))
    assert area == 20 * 20
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    assert min(xs) == pytest.approx(110.5) and max(xs) == pytest.approx(129.5) and min(ys) == pytest.approx(205.5) and max(ys) == pytest.approx(224.5)
    stretched = mask_info(mask, SegmentationMaskBounds(left=0, top=0, right=80, bottom=60))[1]  # mask is half the size of its box
    assert max(p[0] for p in stretched) == pytest.approx(2 * 29 + 1)
