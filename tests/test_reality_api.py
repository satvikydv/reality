import asyncio

import pytest

from app import reality_api as api
from app.reasoner.base import Result
from app.scan import ScanPipeline
from app.scan_service import ScanService
from app.world_model import WorldModel
from test_scan_service import FakeReasoner, jpeg, make_service, meta, seg


def run(coro):
    return asyncio.run(coro)


async def scan(svc, yaws):
    async def send(o):
        pass
    s = svc.session(send)
    await s.start()
    for i, y in enumerate(yaws):
        await s.submit(i + 1, jpeg(), meta(y))
    return (await s.end())["scan_id"]


def test_where_is_found_reports_trust_and_hides_paths(tmp_path):
    svc = make_service(tmp_path)

    async def go():
        sid = await scan(svc, [100, 120])
        return sid, await api.where_is(svc, "the thing I drink coffee from")

    sid, r = run(go())
    assert r["status"] == "found" and r["concept"] == "mug" and r["scan_id"] == sid and r["seen_in_latest_scan"]
    o = r["object"]
    assert o["label"] == "mug" and o["frames_seen"] >= 1 and 0 < o["confidence"] <= 1 and o["age_seconds"] < 60 and "anchor_spread_deg" in o
    assert set(r["compass"]) == {"yaw", "pitch"} and set(r["from_scan_start"]) == {"yaw_deg", "pitch_deg"}
    assert r["sighting"]["image_url"] == f"/world/frame/{r['sighting']['frame_id']}.jpg" and "path" not in r["sighting"]
    assert r["answer"].startswith("The mug was last seen")


def test_where_is_not_seen_says_how_much_was_scanned(tmp_path):
    svc = make_service(tmp_path)
    r = run(api.where_is(svc, "my keys"))  # nothing scanned at all
    assert r["status"] == "not_seen" and r["latest_scan_id"] is None and "covered 0 of 360" in r["message"]

    async def go():
        await scan(svc, [0])
        svc.reasoner.concept = "scissors"
        return await api.where_is(svc, "something to cut paper")

    class Scissors(FakeReasoner):
        async def respond(self, messages, *, schema=None, **kw):
            if "objects" in schema["properties"]:
                return await super().respond(messages, schema=schema, **kw)
            return Result(text="", parsed={"concept": "scissors", "alternates": [], "confidence": 0.9})

    svc.reasoner = Scissors()
    svc.pipeline.reasoner = svc.reasoner
    r = run(go())
    assert r["status"] == "not_seen" and r["latest_scan_coverage"]["covered_deg"] > 0 and "may be in an area not scanned" in r["message"]


def test_where_is_unresolved(tmp_path):
    class Nothing(FakeReasoner):
        async def respond(self, messages, *, schema=None, **kw):
            return Result(text="", parsed={"concept": "", "alternates": [], "confidence": 0.0})

    wm = WorldModel(frames_dir=tmp_path / "f")
    reasoner = Nothing()
    svc = ScanService(wm, ScanPipeline(wm, reasoner, seg), reasoner)
    assert run(api.where_is(svc, "what is the meaning of life"))["status"] == "unresolved"


def test_list_objects_and_scan_summary(tmp_path):
    svc = make_service(tmp_path)
    assert api.list_objects(svc)["status"] == "no_scans" and api.scan_summary(svc)["status"] == "no_scans"
    sid = run(scan(svc, [0, 60, 120]))
    allo = api.list_objects(svc)
    assert allo["status"] == "ok" and allo["scan_id"] == sid and allo["count"] == 3 and {o["label"] for o in allo["objects"]} == {"mug"}
    assert api.list_objects(svc, label="laptop")["count"] == 0 and api.list_objects(svc, label="mugs")["count"] == 3
    assert api.list_objects(svc, scan_id=99)["status"] == "no_scans"
    s = api.scan_summary(svc)
    assert s["object_count"] == 3 and s["counts"] == {"mug": 3} and s["frames"] == 3 and s["summary"] == "3 mugs." and s["coverage"]["covered_deg"] > 100


def test_what_changed_needs_two_scans_then_reports_a_move(tmp_path):
    svc = make_service(tmp_path)
    assert api.what_changed(svc)["status"] == "need_two_scans"

    async def go():
        a = await scan(svc, [0])
        assert api.what_changed(svc)["status"] == "need_two_scans"
        b = await scan(svc, [90])  # the mug now sits 90 degrees away
        return a, b

    a, b = run(go())
    r = api.what_changed(svc)
    assert r["status"] == "ok" and (r["scan_before"], r["scan_after"]) == (a, b)
    assert [m["label"] for m in r["moved"]] == ["mug"] and "The mug moved" in r["summary"]
    for e in (r["moved"][0]["from"], r["moved"][0]["to"]):
        assert "path" not in e["replay"] and e["replay"]["image_url"].endswith(".jpg")
    assert api.what_changed(svc, 99, b)["status"] == "no_such_scan"
    assert api.what_changed(svc, a, b)["moved"][0]["label"] == "mug"


def test_empty_scans_are_ignored_for_defaults(tmp_path):
    svc = make_service(tmp_path)

    async def go():
        a = await scan(svc, [0])
        svc.wm.start_scan()  # aborted scan with no frames
        return a

    a = run(go())
    assert api.scan_summary(svc)["scan_id"] == a and api.list_objects(svc)["scan_id"] == a


def test_get_frame(tmp_path):
    svc = make_service(tmp_path)
    run(scan(svc, [0]))
    fid = svc.wm.list_frames(svc.wm.list_scans()[0]["id"])[0]["id"]
    assert api.get_frame(svc, fid)[:2] == b"\xff\xd8" and api.get_frame(svc, 9999) is None
