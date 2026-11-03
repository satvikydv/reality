"""The phone app can be installed (Chrome's "Install app"): a manifest with what installation needs, the icons it names, and no
service worker (a cache of the app's files would bring back stale code; every feature needs the server anyway)."""
from pathlib import Path

from PIL import Image

from test_scan_service import client  # noqa: F401  (fixture)

WEB = Path(__file__).resolve().parents[1] / "web"


def test_the_manifest_has_what_install_needs(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
    m = r.json()
    assert m["name"] == "Reality OS" and m["start_url"] == "/" and m["display"] in ("fullscreen", "standalone")
    assert m["orientation"] == "portrait"  # the camera guard expects portrait; installed, the lock actually holds
    sizes = {(i["sizes"], i.get("purpose", "any")) for i in m["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes


def test_every_icon_is_served_at_its_stated_size(client):
    for i in client.get("/manifest.webmanifest").json()["icons"]:
        r = client.get(i["src"])
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        w, h = (int(v) for v in i["sizes"].split("x"))
        assert Image.open(WEB / i["src"].lstrip("/")).size == (w, h)


def test_the_page_links_the_manifest_and_registers_no_service_worker(client):
    html = client.get("/").text
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert "serviceWorker" not in html and not any("serviceWorker" in p.read_text(encoding="utf-8") for p in (WEB / "js").glob("*.js"))
