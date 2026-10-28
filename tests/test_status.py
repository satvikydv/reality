"""Home-screen health, scan deletion, scan cancel and the 'not found' explanation (phone first-run UX)."""
import asyncio
import json
from pathlib import Path

import httpx
import openai
import pytest

from app import main, status
from test_scan_service import client, frame_msg, jpeg, make_service, meta  # noqa: F401  (client is a fixture)


def run_scan(ws, yaws=(0,)):
    ws.send_text(json.dumps({"type": "scan_start"}))
    assert ws.receive_json()["type"] == "scan_started"
    for i, yaw in enumerate(yaws):
        ws.send_bytes(frame_msg(i + 1, 2, meta(yaw), jpeg()))
    ws.send_text(json.dumps({"type": "scan_end"}))
    while True:
        m = ws.receive_json()
        if m["type"] == "scan_summary":
            return m


class FakeModels:
    def __init__(self, outcome):
        self.outcome, self.calls = outcome, 0

    async def list(self):
        self.calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return []


class FakeClient:
    def __init__(self, outcome):
        self.models = FakeModels(outcome)

    def with_options(self, **kw):
        return self


def api_error(cls, code):
    req = httpx.Request("GET", "https://api.test/v1/models")
    return cls("x", response=httpx.Response(code, request=req), body=None)


def state_for(outcome, monkeypatch, key="k"):
    monkeypatch.setenv("MODEL_API_KEY", key)
    kc = status.KeyCheck(make_client=lambda: FakeClient(outcome))
    return asyncio.run(kc.state()), kc


def test_key_states(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert asyncio.run(status.KeyCheck(make_client=lambda: pytest.fail("no key: no network")).state())["state"] == "missing"
    assert state_for(None, monkeypatch)[0]["state"] == "ok"
    assert state_for(api_error(openai.AuthenticationError, 401), monkeypatch)[0]["state"] == "invalid"
    assert state_for(api_error(openai.PermissionDeniedError, 403), monkeypatch)[0]["state"] == "invalid"
    assert state_for(api_error(openai.NotFoundError, 404), monkeypatch)[0]["state"] == "set"  # reachable, endpoint absent: not verified
    assert state_for(api_error(openai.InternalServerError, 500), monkeypatch)[0]["state"] == "unreachable"
    assert state_for(openai.APIConnectionError(request=httpx.Request("GET", "https://api.test")), monkeypatch)[0]["state"] == "unreachable"
    assert state_for(asyncio.TimeoutError(), monkeypatch)[0]["state"] == "unreachable"


def test_key_check_is_cached_and_failures_retry_sooner(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "k")
    now = [0.0]
    fake = FakeClient(None)
    kc = status.KeyCheck(make_client=lambda: fake, clock=lambda: now[0])

    async def go():
        await kc.state(); await kc.state()
        assert fake.models.calls == 1  # the second poll is free
        now[0] = status.CACHE_S + 1
        await kc.state()
        assert fake.models.calls == 2
        await kc.state(force=True)
        assert fake.models.calls == 3
        fake.models.outcome = asyncio.TimeoutError()
        await kc.state(force=True)
        now[0] += status.RETRY_S + 1  # a failure is retried after 20 s, not after 5 min
        fake.models.outcome = None
        assert (await kc.state())["state"] == "ok" and fake.models.calls == 5

    asyncio.run(go())


def test_status_empty_then_after_a_scan(client, monkeypatch):
    monkeypatch.setattr(main, "key_check", status.KeyCheck(make_client=lambda: FakeClient(None)))
    monkeypatch.setenv("MODEL_API_KEY", "k")
    r = client.get("/api/status").json()
    assert r["server"] is True and r["api"]["state"] == "ok" and r["last_scan"] is None and r["scans"] == 0

    with client.websocket_connect("/ws") as ws:
        run_scan(ws, yaws=(0, 90))
    r = client.get("/api/status").json()
    last = r["last_scan"]
    assert r["scans"] == 1 and last["n_objects"] == 2 and last["n_frames"] == 2
    assert 0 < last["coverage_pct"] < 100 and last["covered_deg"] > 0 and last["ended_at"] >= last["started_at"]


def test_status_answers_when_the_service_cannot_start(monkeypatch):
    monkeypatch.setenv("BIND_HOST", "127.0.0.1")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr(main, "_service", None)
    monkeypatch.setattr(main, "service_factory", lambda: (_ for _ in ()).throw(RuntimeError("MODEL_API_KEY not set")))
    monkeypatch.setattr(main, "key_check", status.KeyCheck())
    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        r = c.get("/api/status")
    assert r.status_code == 200
    j = r.json()
    assert j["server"] is True and j["api"]["state"] == "missing" and j["last_scan"] is None and "RuntimeError" in j["service_error"]


def test_delete_one_scan_and_all(client):
    with client.websocket_connect("/ws") as ws:
        run_scan(ws, yaws=(0,))
        run_scan(ws, yaws=(90,))
    scans = client.get("/api/scans").json()["scans"]
    assert len(scans) == 2
    wm = client.svc.wm
    paths = [f["path"] for s in scans for f in wm.list_frames(s["id"])]
    assert paths and all(Path(p).exists() for p in paths)

    oldest = scans[1]["id"]
    r = client.delete(f"/api/scans/{oldest}")
    assert r.status_code == 200 and r.json() == {"deleted": [oldest]}
    assert [s["id"] for s in client.get("/api/scans").json()["scans"]] == [scans[0]["id"]]
    assert wm.db.execute("SELECT COUNT(*) FROM objects WHERE scan_id=?", (oldest,)).fetchone()[0] == 0
    assert wm.db.execute("SELECT COUNT(*) FROM detections WHERE scan_id=?", (oldest,)).fetchone()[0] == 0
    assert wm.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] >= 1  # the other scan's data is untouched
    assert client.delete(f"/api/scans/{oldest}").status_code == 404

    r = client.delete("/api/scans")
    assert r.json() == {"deleted": [scans[0]["id"]]} and client.get("/api/scans").json() == {"scans": []}
    assert not any(Path(p).exists() for p in paths)
    assert client.get("/api/status").json()["last_scan"] is None


def test_delete_never_removes_files_outside_the_frames_folder(tmp_path):
    wm = make_service(tmp_path).wm
    outside = tmp_path / "keep.jpg"
    outside.write_bytes(b"x")
    sid = wm.start_scan()
    fid = wm.add_frame(sid, 1.0, 0, 0, 10, 10)
    wm.db.execute("UPDATE frames SET path=? WHERE id=?", (str(outside), fid))
    wm.db.commit()
    assert wm.delete_scan(sid) is True and outside.exists()
    assert wm.delete_scan(sid) is False


def test_cancel_deletes_the_running_scan_and_sends_no_summary(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "scan_start"}))
        assert ws.receive_json()["type"] == "scan_started"
        ws.send_bytes(frame_msg(1, 2, meta(0), jpeg()))
        ws.send_text(json.dumps({"type": "scan_cancel"}))
        seen = []
        while True:
            m = ws.receive_json()
            seen.append(m["type"])
            if m["type"] == "scan_cancelled":
                break
        assert "scan_summary" not in seen
        ws.send_text(json.dumps({"type": "scan_cancel"}))  # nothing running: ignored, the socket stays usable
        run_scan(ws, yaws=(0,))
    assert len(client.get("/api/scans").json()["scans"]) == 1  # only the scan made after the cancel


def test_not_found_says_how_much_was_checked(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "find", "query": "mug"}))
        r = ws.receive_json()
        assert r["found"] is False and r["checked"] == {"scans": 0, "covered_deg": 0, "scan_id": None}
        run_scan(ws, yaws=(0, 60))
        ws.send_text(json.dumps({"type": "find", "query": "mug"}))
        ok = ws.receive_json()
        assert ok["found"] is True and "checked" not in ok


def test_a_slow_key_check_answers_checking_and_finishes_in_the_background(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "k")

    class SlowModels:
        async def list(self):
            await asyncio.sleep(0.3)
            return []

    class SlowClient:
        models = SlowModels()

        def with_options(self, **kw):
            return self

    kc = status.KeyCheck(make_client=lambda: SlowClient())

    async def go():
        first = await kc.state(wait=0.05)  # the Home screen does not wait for the model API
        assert first["state"] == "checking"
        await asyncio.sleep(0.4)
        assert (await kc.state(wait=0.05))["state"] == "ok"  # the next poll has the verdict

    asyncio.run(go())
