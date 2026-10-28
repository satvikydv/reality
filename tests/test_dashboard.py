import json

import pytest
from fastapi.testclient import TestClient

from app import main
from test_scan_service import client, frame_msg, jpeg, make_service, meta  # noqa: F401  (client is a fixture)


def test_dashboard_page_is_served_and_tv_is_gone(client):
    r = client.get("/dashboard")
    assert r.status_code == 200 and "Reality OS" in r.text and "js/dashboard.js" in r.text
    assert client.get("/js/dashboard.js").status_code == 200 and client.get("/css/theme.css").status_code == 200
    assert client.get("/tv").status_code == 404


def test_scan_list_and_changes_endpoints(client):
    assert client.get("/api/scans").json() == {"scans": []}
    assert client.get("/api/changes").json()["status"] == "need_two_scans"

    with client.websocket_connect("/ws") as ws:
        for yaw in (0, 90):  # the mug sits in a different direction in the second scan
            ws.send_text(json.dumps({"type": "scan_start"}))
            assert ws.receive_json()["type"] == "scan_started"
            ws.send_bytes(frame_msg(1, 2, meta(yaw), jpeg()))
            ws.send_text(json.dumps({"type": "scan_end"}))
            while ws.receive_json()["type"] != "scan_summary":
                pass

    scans = client.get("/api/scans").json()["scans"]
    assert [s["n_frames"] for s in scans] == [1, 1] and scans[0]["id"] > scans[1]["id"]
    r = client.get("/api/changes").json()
    assert r["status"] == "ok" and r["moved"][0]["label"] == "mug" and r["moved"][0]["to"]["replay"]["image_url"].startswith("/world/frame/")
    assert client.get(f"/api/changes?before={scans[1]['id']}&after={scans[0]['id']}").json()["status"] == "ok"
    assert client.get("/api/changes?before=999&after=1").json()["status"] == "no_such_scan"


def test_api_reports_503_when_the_service_cannot_start(monkeypatch):
    monkeypatch.setenv("BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(main, "_service", None)

    def broken():
        raise RuntimeError("MODEL_API_KEY not set")

    monkeypatch.setattr(main, "service_factory", broken)
    with TestClient(main.app) as c:
        r = c.get("/api/scans")
        assert r.status_code == 503 and "MODEL_API_KEY" in r.json()["detail"]
