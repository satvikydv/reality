"""The phone page in a real browser (headless Chrome or Edge, driven over the DevTools protocol).

What is checked here cannot be checked from Node: that opening `/` touches no camera, motion sensor or microphone (every API
is replaced by a spy before the page's own scripts run), that the camera starts only from a tap, that the intro and the
permission primer come before the browser's prompts, and that every way of leaving a camera screen stops every track.
The camera itself is a canvas stream, so no hardware is needed. Skipped, loudly, when no Chrome or Edge is installed.
"""
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from app import main, status
from synthetic_world import GOOD
from test_scan_service import frame_msg, jpeg, make_service, meta
from test_status import FakeClient

websockets_sync = pytest.importorskip("websockets.sync.client")


def find_browser():
    cands = [os.environ.get("CHROME_BIN"), r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
             r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
             "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    for n in ("google-chrome", "chromium", "chrome", "msedge"):
        cands.append(shutil.which(n))
    return next((c for c in cands if c and Path(c).exists()), None)


BROWSER = find_browser()
pytestmark = [pytest.mark.skipif(BROWSER is None, reason="no Chrome or Edge found (set CHROME_BIN): the entry-flow tests need a real browser"),
              pytest.mark.filterwarnings("ignore:connect.. must be used as a context manager")]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# Runs in the page before any of its own scripts. Everything that could touch the camera, the sensors or the microphone is
# replaced by something that writes to window.__media, so the tests can say exactly what the page did and in what order.
SPY = r"""
(() => {
  if (!sessionStorage.getItem('__t')) { sessionStorage.setItem('__t', '1'); localStorage.clear(); const seed = __SEED__; for (const k in seed) localStorage.setItem(k, seed[k]); }
  const log = window.__media = []; window.__stops = 0; window.__deny = null; window.__offline = false;
  const stream = () => {
    const c = document.createElement('canvas'); c.width = 640; c.height = 480; const x = c.getContext('2d'); let n = 0;
    setInterval(() => { x.fillStyle = 'hsl(' + ((n++ * 7) % 360) + ',50%,40%)'; x.fillRect(0, 0, 640, 480); }, 50);
    const s = c.captureStream(15);
    for (const t of s.getTracks()) { const stop = t.stop.bind(t); t.stop = () => { window.__stops++; stop(); }; }
    return s;
  };
  if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = async (c) => {
    log.push('getUserMedia ' + JSON.stringify(c));
    if (window.__deny) { const e = new Error('denied'); e.name = window.__deny; throw e; }
    return stream();
  };
  if (window.DeviceOrientationEvent) DeviceOrientationEvent.requestPermission = async () => { log.push('requestPermission'); return 'granted'; };
  for (const n of ['AbsoluteOrientationSensor', 'RelativeOrientationSensor', 'Accelerometer', 'Gyroscope', 'Magnetometer'])
    if (window[n]) window[n] = class { constructor() { log.push('new ' + n); } addEventListener() {} start() {} stop() {} };
  for (const n of ['SpeechRecognition', 'webkitSpeechRecognition'])
    if (window[n]) window[n] = class { constructor() { log.push('new ' + n); } start() {} stop() {} };
  const add = window.addEventListener.bind(window);
  window.addEventListener = (t, ...r) => { if (/^device(orientation|motion)/.test(t)) log.push('listen ' + t); return add(t, ...r); };
  Object.defineProperty(navigator, 'wakeLock', { configurable: true, value: { request: async () => {
    log.push('wakeLock.request'); const s = new EventTarget(); s.release = async () => { log.push('wakeLock.release'); }; return s; } } });
  try { screen.orientation.lock = async (m) => { log.push('orientation.lock ' + m); }; screen.orientation.unlock = () => { log.push('orientation.unlock'); }; } catch (e) {}
  // Tests must be silent: speech is recorded in window.__spoken instead of coming out of the laptop's speakers.
  window.__spoken = [];
  try { speechSynthesis.speak = (u) => { window.__spoken.push(u.text); }; speechSynthesis.cancel = () => {}; } catch (e) {}
  const f = window.fetch.bind(window);
  window.fetch = (u, ...a) => (window.__offline && String(u).includes('/api/status') ? Promise.reject(new TypeError('Failed to fetch')) : f(u, ...a));
})();
"""


class Page:
    """One browser tab. eval() runs JavaScript, tap() sends a real mouse press so the page sees a user gesture."""

    def __init__(self, base, port, seed=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT")
        self.target = json.load(urllib.request.urlopen(req))
        self.port = port
        self.base = base
        self.ws = websockets_sync.connect(self.target["webSocketDebuggerUrl"], max_size=None)
        self.n = 0
        self.errors = []
        for m in ("Runtime.enable", "Page.enable"):
            self.call(m)
        self.call("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)
        self.call("Page.addScriptToEvaluateOnNewDocument", source=SPY.replace("__SEED__", json.dumps(seed or {})))

    def call(self, method, **params):
        self.n += 1
        i = self.n
        self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv(timeout=20))
            if m.get("id") == i:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m["result"]
            if m.get("method") == "Runtime.exceptionThrown":
                self.errors.append(m["params"]["exceptionDetails"].get("exception", {}).get("description") or str(m["params"]["exceptionDetails"]))
            if m.get("method") == "Runtime.consoleAPICalled" and m["params"]["type"] == "error":
                self.errors.append("console.error: " + " ".join(str(a.get("value", a.get("description", ""))) for a in m["params"]["args"]))

    def eval(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", str(r["exceptionDetails"])))
        return r["result"].get("value")

    def open(self, path="/"):
        self.call("Page.navigate", url=self.base + path)
        self.wait("!!window.__ui", what="the page to boot")
        return self

    def wait(self, expr, timeout=10.0, what=None):
        end = time.time() + timeout
        while time.time() < end:
            v = self.eval(expr)
            if v:
                return v
            time.sleep(0.05)
        raise AssertionError(f"timed out waiting for {what or expr}; page errors: {self.errors}")

    def settle(self, seconds=1.2):
        """Let everything the page does on its own happen (status poll, async boot), then drain protocol events."""
        time.sleep(seconds)
        self.eval("1")

    def tap(self, selector):
        r = self.eval("(()=>{const e=document.querySelector(%s);if(!e)return null;e.scrollIntoView({block:'center'});const b=e.getBoundingClientRect();"
                      "return [b.x+b.width/2,b.y+b.height/2,b.width,b.height,e.disabled||false]})()" % json.dumps(selector))
        assert r and r[2] > 0 and r[3] > 0, f"{selector} is not visible"
        assert not r[4], f"{selector} is disabled"
        for t in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=t, x=r[0], y=r[1], button="left", clickCount=1)

    def media(self):
        return self.eval("window.__media")

    def gum(self):
        return [m for m in self.media() if m.startswith("getUserMedia")]

    def screen(self):
        return self.eval("window.__ui.screen()")

    def text(self, sel):
        return self.eval("document.querySelector(%s)?.textContent" % json.dumps(sel))

    def close(self):
        try:
            self.ws.close()
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/close/{self.target['id']}")
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    import uvicorn

    mp = pytest.MonkeyPatch()
    tmp = tmp_path_factory.mktemp("entry")
    svc = make_service(tmp)
    mp.setenv("BIND_HOST", "127.0.0.1")
    mp.setenv("MODEL_API_KEY", "test-key")
    mp.setattr(main, "_service", None)
    mp.setattr(main, "service_factory", lambda: svc)
    mp.setattr(main, "key_check", status.KeyCheck(make_client=lambda: FakeClient(None)))
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    end = time.time() + 15
    while not server.started and time.time() < end:
        time.sleep(0.05)
    assert server.started, "the test server did not start"

    prof = tempfile.mkdtemp(prefix="ros-chrome-")
    cport = free_port()
    proc = subprocess.Popen([BROWSER, "--headless=new", f"--remote-debugging-port={cport}", f"--user-data-dir={prof}", "--no-first-run",
                             "--disable-gpu", "--no-default-browser-check", "--mute-audio", "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                             "--autoplay-policy=no-user-gesture-required", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.time() + 20
    while time.time() < end:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{cport}/json/version", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("the browser did not start")

    class Site:
        base = f"http://127.0.0.1:{port}"
        svc_ = svc
        pages = []

        def page(self, seed=None):
            p = Page(self.base, cport, seed)
            self.pages.append(p)
            return p

        def room(self, name, active=False, label=None, yaw=0.0, ts=100.0, saved=True, frames=1):
            """A room, optionally with a scan holding one object, stored straight in the world model."""
            wm = self.svc_.wm
            rid = wm.create_room(name, active=active)["id"]
            if label:
                self.scan_in(rid, label, yaw, ts, saved, frames)
            return rid

        def scan_in(self, rid, label="mug", yaw=0.0, ts=100.0, saved=True, frames=1):
            wm = self.svc_.wm
            sid = wm.start_scan(ts=ts, room_id=rid, status="saved" if saved else "draft")
            for i in range(frames):
                fid = wm.add_frame(sid, ts + i, yaw, 0.0, 480, 640, roll=0.0, screen_angle=0.0, jpeg=b"jpg")
                wm.observe(fid, [{"label": label, "box": [220, 300, 260, 340]}])
            wm.end_scan(sid, ts + 5)
            return sid

    s = Site()
    yield s
    for p in s.pages:
        p.close()
    proc.kill()
    server.should_exit = True
    th.join(timeout=10)
    shutil.rmtree(prof, ignore_errors=True)
    mp.undo()


READY = {"ros.onboarded": "1", "ros.primed": "1"}  # a returning user who has been through the intro and the permission primer
HOME_LOADED = "document.getElementById('s-home').dataset.state!=='loading'&&document.getElementById('s-home').dataset.state!=='slow'"


@pytest.fixture()
def clean(site):
    site.svc_.wm.delete_all()  # every test starts with no rooms and no scans, and makes what it needs
    yield


def booted(site, seed=READY, path="/"):
    p = site.page(seed).open(path)
    p.wait(HOME_LOADED, what="Home to load")
    return p


def registry():
    """{screen: [states]} from the page's own registry (web/js/screens.js)."""
    out = subprocess.run(["node", "-e", "import('./web/js/screens.js').then(m=>console.log(JSON.stringify(Object.fromEntries(Object.entries(m.SCREENS).map(([k,v])=>[k,v.states])))))"],
                         capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]), check=True)
    return json.loads(out.stdout)


# ------------------------------------------------------------------------------------------------ entry flow

@pytest.mark.parametrize("seed,landing,path", [({}, "welcome", "/"), (READY, "home", "/"), (READY, "home", "/?debug"), ({}, "welcome", "/?debug")])
def test_opening_the_page_does_not_touch_camera_sensors_or_microphone(site, clean, seed, landing, path):
    site.room("Living Room", active=True, label="mug")
    p = site.page(seed).open(path)
    p.wait("document.getElementById('stat').textContent.startsWith('Connected')", what="the server check to finish")
    p.settle(1.5)  # long enough for any auto-start, a status poll and the rooms load to have run
    assert p.media() == [], f"the page touched media or sensor APIs on load: {p.media()}"
    assert p.screen() == landing
    assert p.eval("document.getElementById('v').srcObject") is None
    assert p.eval("document.body.classList.contains('live')") is False
    assert p.errors == []


def test_home_lists_rooms_and_the_bottom_bar_has_five_tabs(site, clean):
    site.room("Living Room", active=True, label="mug")
    site.room("Wardrobe")
    p = booted(site)
    p.wait("document.querySelectorAll('#hrooms .rcard').length===2")
    assert p.eval("[...document.querySelectorAll('#nav button')].map(b=>b.textContent)") == ["Rooms", "Find", "Scan", "Changes", "Settings"]
    assert p.eval("document.querySelector('#nav [aria-current=page]').dataset.nav") == "home"
    cards = p.eval("[...document.querySelectorAll('#hrooms .rcard')].map(c=>c.textContent)")
    assert any("Living Room" in c and "ACTIVE" in c and "1 object" in c for c in cards) and any("Wardrobe" in c and "Not scanned yet" in c for c in cards)
    assert p.text("#s-home .roomsw .rn") == "Living Room"


def test_home_with_no_rooms_has_an_empty_state(site, clean):
    p = booted(site)
    assert p.eval("document.getElementById('s-home').dataset.state") == "empty" and "No rooms yet" in p.text("#s-home .statecard")
    assert p.text("#s-home .roomsw .rn") == "No room"
    p.tap("#s-home [data-show=empty] [data-act=add-room]")
    assert p.screen() == "wizard" and p.media() == []


def test_the_spy_sees_the_camera_and_the_camera_starts_only_after_the_wizard(site, clean):
    p = booted(site)
    p.tap("#nav [data-nav=scan]")
    assert p.screen() == "wizard" and p.gum() == [] and p.eval("document.body.classList.contains('nav-on')") is False
    p.tap("#wzchips [data-name=Desk]")
    p.tap("#wzgo")
    p.wait("document.body.classList.contains('live')", what="the camera to start")
    assert p.screen() == "scan" and len(p.gum()) == 1 and '"audio":false' in p.gum()[0]
    assert p.eval("document.getElementById('s-scan').dataset.state") == "ready" and p.text("#scanroom") == "Desk"
    assert site.svc_.wm.resolve_room("Desk") is not None  # the wizard created the room


def test_the_camera_starts_from_a_tap_on_explore_and_on_guide_me(site, clean):
    rid = site.room("Living Room", active=True, label="mug")
    p = booted(site)
    p.tap(f"#hrooms [data-id='{rid}']")
    p.wait("document.getElementById('s-room').dataset.state==='default'")
    assert p.gum() == []
    p.tap("[data-act=explore-room]")
    p.wait("document.body.classList.contains('live')")
    assert p.screen() == "explore" and len(p.gum()) == 1
    p.tap("#s-explore [data-act=stop]")
    assert p.screen() == "room" and p.eval("document.body.classList.contains('live')") is False

    p.tap("#nav [data-nav=find]")
    p.eval("document.getElementById('q').value='mug'")
    p.tap("#findbtn")
    p.wait("document.getElementById('s-result')?.classList.contains('on')", what="the find result")
    assert p.screen() == "result" and len(p.gum()) == 1
    p.tap("#guidebtn")
    p.wait("document.body.classList.contains('live')")
    assert p.screen() == "guide" and len(p.gum()) == 2


# ------------------------------------------------------------------------------------------------ first run

def test_first_run_intro_then_primer_then_the_browser_prompts_in_that_order(site, clean):
    p = site.page().open()
    assert p.screen() == "welcome" and "Find things in your room" in p.text("#introcard")
    assert p.text("#intronext") == "Next"
    p.tap("#intronext")
    assert "How to scan" in p.text("#introcard") and "people out of frame" in p.text("#introcard")
    p.tap("#intronext")
    assert "sent to Meta’s model API" in p.text("#introcard") and "delete any scan" in p.text("#introcard")
    assert p.text("#intronext") == "Allow camera and motion"
    assert p.media() == [], "no prompt before the button"
    p.tap("#intronext")
    assert p.screen() == "primer" and p.media() == [], "the primer comes before the browser prompts"
    assert "Camera" in p.text("#primeritems") and "Motion sensors" in p.text("#primeritems") and "microphone" in p.text("#primernote")
    p.tap("#primergo")
    p.wait("window.__media.length>=2", what="the browser prompts")
    assert [m.split(" ")[0] for m in p.media()] == ["requestPermission", "getUserMedia"]
    p.wait("document.body.dataset.screen==='home'")
    assert p.eval("localStorage.getItem('ros.onboarded')") == "1" and p.eval("localStorage.getItem('ros.primed')") == "1"
    assert p.eval("window.__stops") == 1, "the permission probe releases the camera again"
    assert p.eval("document.getElementById('v').srcObject") is None


def test_the_intro_is_shown_once_and_can_be_skipped(site, clean):
    p = site.page().open()
    p.tap("[data-act=intro-skip]")
    assert p.screen() == "home" and p.media() == []
    p.call("Page.reload")
    p.wait("!!window.__ui")
    assert p.screen() == "home", "a second visit goes straight to Home"
    assert p.eval("localStorage.getItem('ros.onboarded')") == "1"


def test_a_user_who_skipped_the_intro_still_gets_the_primer_before_the_camera(site, clean):
    p = booted(site, {"ros.onboarded": "1"})
    p.tap("#nav [data-nav=scan]")
    p.tap("#wzchips [data-name=Kitchen]")
    p.tap("#wzgo")
    p.wait("document.body.dataset.screen==='primer'", what="the primer (the room is created first)")
    assert p.media() == []
    p.tap("#primergo")
    p.wait("document.body.dataset.screen==='scan'", what="the scan screen after the permissions")
    p.wait("document.body.classList.contains('live')")
    assert p.eval("document.getElementById('s-scan').dataset.state") == "ready", "the pre-scan checklist, not a scan that already started"


def test_denied_camera_is_explained_and_can_be_retried(site, clean):
    p = booted(site, {"ros.onboarded": "1"})
    p.eval("window.__deny='NotAllowedError'")
    p.tap("#nav [data-nav=scan]")
    p.tap("#wzchips [data-name=Desk]")
    p.tap("#wzgo")
    p.wait("document.body.dataset.screen==='primer'")
    p.tap("#primergo")
    p.wait("!document.querySelector('#s-primer .problem').hidden", what="the explanation")
    assert "Camera access was blocked" in p.text("#problemtitle") and "Try again" in p.text("#problembody")
    assert p.text("#primergo") == "Try again" and p.screen() == "primer"
    assert p.eval("localStorage.getItem('ros.primed')") is None, "a refusal is not remembered as done"
    p.eval("window.__deny=null")
    p.tap("#primergo")
    p.wait("document.body.dataset.screen==='scan'")  # the retry worked and carried on to the scan the user asked for
    assert p.eval("localStorage.getItem('ros.primed')") == "1"


def test_a_camera_taken_away_later_goes_back_to_the_explanation(site, clean):
    rid = site.room("Living Room", active=True, label="mug")
    p = booted(site)
    p.eval("window.__deny='NotAllowedError'")
    p.tap(f"#hrooms [data-id='{rid}']")
    p.wait("document.getElementById('s-room').dataset.state==='default'")
    p.tap("[data-act=explore-room]")
    p.wait("document.body.dataset.screen==='primer'")
    assert "Camera access was blocked" in p.text("#problemtitle")
    assert p.eval("document.body.classList.contains('live')") is False


# ------------------------------------------------------------------------------------------------ camera lifecycle

def test_every_way_out_of_a_camera_screen_stops_the_camera(site, clean):
    rid = site.room("Living Room", active=True, label="mug")
    p = booted(site)
    stops = lambda: p.eval("window.__stops")  # noqa: E731
    p.tap(f"#hrooms [data-id='{rid}']")
    p.wait("document.getElementById('s-room').dataset.state==='default'")

    p.tap("[data-act=explore-room]")
    p.wait("document.body.classList.contains('live')")
    assert p.eval("document.querySelector('#s-explore .camstop').textContent").startswith("Camera on") and p.text("#s-explore .camstop b") == "STOP"
    p.wait("window.__media.includes('wakeLock.request')", what="the wake lock")
    assert "orientation.lock portrait" in p.media()
    assert any(m.startswith("new AbsoluteOrientationSensor") for m in p.media()), "the sensors start with the camera"
    p.tap("#s-explore [data-act=stop]")  # the Stop control
    assert p.screen() == "room" and stops() == 1
    assert p.eval("document.getElementById('v').srcObject") is None and p.eval("document.body.classList.contains('live')") is False
    assert "wakeLock.release" in p.media() and "orientation.unlock" in p.media()

    p.tap("[data-act=explore-room]")  # Back button
    p.wait("document.body.classList.contains('live')")
    p.tap("#s-explore [data-act=back]")
    assert stops() == 2 and p.eval("document.body.classList.contains('live')") is False
    p.wait("document.getElementById('s-room').dataset.state==='default'")

    p.tap("[data-act=explore-room]")  # leaving for another screen through the bottom bar
    p.wait("document.body.classList.contains('live')")
    p.eval("window.__ui.go('settings')")
    assert stops() == 3 and p.eval("document.getElementById('v').srcObject") is None

    p.tap("#nav [data-nav=scan]")  # cancelling before the scan starts
    p.tap(f"#wzrooms [data-id='{rid}']")
    p.tap("#wzgo")
    p.wait("document.body.classList.contains('live')")
    assert p.eval("document.getElementById('s-scan').dataset.state") == "ready" and p.eval("getComputedStyle(document.getElementById('checklist')).display") != "none"
    p.tap("#scancancel")
    assert p.screen() == "home" and stops() == 4
    assert len(p.gum()) == 4 and p.errors == []


def test_hiding_the_tab_stops_the_camera_and_showing_it_does_not_restart_it(site, clean):
    rid = site.room("Living Room", active=True, label="mug")
    p = booted(site)
    p.tap(f"#hrooms [data-id='{rid}']")
    p.wait("document.getElementById('s-room').dataset.state==='default'")
    p.tap("[data-act=explore-room]")
    p.wait("document.body.classList.contains('live')")
    p.eval("Object.defineProperty(document,'hidden',{get:()=>true,configurable:true});document.dispatchEvent(new Event('visibilitychange'))")
    assert p.eval("window.__stops") == 1 and p.eval("document.getElementById('v').srcObject") is None
    assert p.screen() == "home" and "hidden" in p.text("#toast")
    p.eval("Object.defineProperty(document,'hidden',{get:()=>false,configurable:true});document.dispatchEvent(new Event('visibilitychange'))")
    p.settle(0.8)
    assert len(p.gum()) == 1, "coming back does not turn the camera on by itself"
    assert p.eval("document.body.classList.contains('live')") is False


def test_a_track_that_ends_by_itself_turns_everything_off(site, clean):
    rid = site.room("Living Room", active=True, label="mug")
    p = booted(site)
    p.tap(f"#hrooms [data-id='{rid}']")
    p.wait("document.getElementById('s-room').dataset.state==='default'")
    p.tap("[data-act=explore-room]")
    p.wait("document.body.classList.contains('live')")
    p.eval("(()=>{const t=document.getElementById('v').srcObject.getVideoTracks()[0];t.stop();t.dispatchEvent(new Event('ended'))})()")
    p.wait("document.body.dataset.screen==='home'")
    assert p.eval("document.body.classList.contains('live')") is False and "wakeLock.release" in p.media()


# ------------------------------------------------------------------------------------------------ rooms in the app

def test_offline_server_is_shown_and_the_page_reconnects_by_itself(site, clean):
    site.room("Living Room", active=True, label="mug")
    p = booted(site)
    p.wait("document.getElementById('stat').textContent.startsWith('Connected')")
    p.eval("window.__offline=true")
    p.wait("document.getElementById('stat').textContent==='Server offline'", timeout=20, what="the offline state")
    p.eval("window.__offline=false")
    p.wait("document.getElementById('stat').textContent.startsWith('Connected')", timeout=15, what="the page to reconnect without a reload")
    assert p.eval("document.querySelectorAll('#hrooms .rcard').length") == 1


def test_find_in_a_room_that_was_not_scanned_says_so_and_never_shows_another_rooms_objects(site, clean):
    site.room("Living Room", active=True, label="mug")
    desk = site.room("Desk")
    p = booted(site)
    p.tap("#s-home .roomsw")
    p.tap(f"#swlist [data-id='{desk}']")
    p.wait("document.querySelector('#s-home .roomsw .rn').textContent==='Desk'")
    assert site.svc_.wm.active_room()["name"] == "Desk"  # the choice is stored on the server
    p.tap("#nav [data-nav=find]")
    assert p.eval("document.getElementById('s-find').dataset.state") == "room-empty" and "Desk has not been scanned yet" in p.text("#fempty")
    p.eval("document.getElementById('q').value='mug'")
    assert p.eval("getComputedStyle(document.getElementById('q')).display") == "none" or p.eval("document.querySelector('#s-find .row').hidden") is True  # nothing to search in an unscanned room


def test_find_with_no_active_room_asks_to_choose_one(site, clean):
    site.room("Living Room", label="mug")  # a room, but none is active
    p = booted(site)
    p.tap("#nav [data-nav=find]")
    assert p.eval("document.getElementById('s-find').dataset.state") == "no-room" and p.text("#s-find .roomsw .rn") == "No room"
    p.tap("#nav [data-nav=changes]")
    assert p.eval("document.getElementById('s-changes').dataset.state") == "no-room"


def test_find_in_all_rooms_groups_results_and_opening_one_asks_to_switch(site, clean):
    a = site.room("Kitchen", active=True)
    site.room("Desk", label="mug", yaw=40)
    p = booted(site)
    p.tap("#nav [data-nav=find]")
    p.tap("#fscope [data-v=all]")
    p.eval("document.getElementById('q').value='mug'")
    p.tap("#findbtn")
    p.wait("document.getElementById('s-find').dataset.state==='all-results'", what="the grouped results")
    groups = p.eval("[...document.querySelectorAll('#fgroups .fgroup')].map(g=>g.textContent)")
    assert any("Desk" in g and "mug" in g for g in groups) and any("Kitchen" in g and "Not scanned yet" in g for g in groups)
    assert site.svc_.wm.active_room()["id"] == a, "searching all rooms switches nothing"
    p.tap("#fgroups .fopen")
    assert p.eval("document.getElementById('dlg').classList.contains('on')") and "Switch to Desk" in p.text("#dlgtitle")
    p.tap("#dlgno")
    assert p.screen() == "find" and site.svc_.wm.active_room()["id"] == a
    p.tap("#fgroups .fopen")
    p.tap("#dlgyes")
    p.wait("document.body.dataset.screen==='result'", what="the result after switching")
    assert site.svc_.wm.active_room()["name"] == "Desk" and p.text("#resroom") == "Desk"


def test_a_scan_is_reviewed_then_saved_or_discarded(site, clean):
    rid = site.room("Living Room", active=True, label="mug", ts=100)
    draft = site.scan_in(rid, "keys", yaw=90, ts=200, saved=False)
    p = booted(site)
    p.tap(f"#hrooms [data-id='{rid}']")
    p.wait("document.getElementById('s-room').dataset.state==='default'")
    assert "unsaved scan" in p.text("#hrooms") or p.eval("document.querySelectorAll('#rhist .hrow.draft').length") == 1
    assert p.eval("document.querySelectorAll('#rhist .hrow').length") == 2
    assert p.eval("[...document.querySelectorAll('#rhist .hrow .badge')].map(b=>b.textContent).includes('UNSAVED')")
    assert site.svc_.wm.latest_scan_id(rid) != draft  # the draft is not the room's memory
    p.tap("#rhist .hrow.draft [data-act=save-scan]")
    p.wait("document.querySelectorAll('#rhist .hrow.draft').length===0", what="the scan to be saved")
    assert site.svc_.wm.latest_scan_id(rid) == draft
    assert [s["status"] for s in site.svc_.wm.room_scans(rid)] == ["saved", "saved"]

    d2 = site.scan_in(rid, "cup", yaw=10, ts=300, saved=False)
    p.tap("#nav [data-nav=home]")
    p.tap(f"#hrooms [data-id='{rid}']")
    p.wait("document.querySelectorAll('#rhist .hrow.draft').length===1")
    p.tap("#rhist .hrow.draft [data-act=discard-scan]")
    p.tap("#dlgyes")
    p.wait("document.querySelectorAll('#rhist .hrow.draft').length===0")
    assert site.svc_.wm.get_scan(d2) is None and site.svc_.wm.latest_scan_id(rid) == draft


def test_the_review_screen_saves_a_draft_scan_over_the_api(site, clean):
    rid = site.room("Living Room", active=True, label="mug", ts=100)
    draft = site.scan_in(rid, "keys", yaw=90, ts=200, saved=False)
    p = booted(site)
    p.eval("window.__ui.RENDER.summary('review',{summary:{scan_id:%d,room_id:%d,room_name:'Living Room',speech:'I found keys.',coverage:{covered_deg:200},frames:{stored:1},seconds:5,cost_usd:0,"
           "objects:[{id:1,label:'keys',yaw:90,pitch:0,replay:null},{id:2,label:'keys',yaw:95,pitch:0,replay:null}]}})" % (draft, rid))
    assert p.screen() == "summary" and p.eval("document.getElementById('s-summary').dataset.state") == "review"
    assert p.eval("document.querySelectorAll('#sumtiles .gtile').length") == 1 and "keys" in p.text("#sumtiles")
    p.tap("#sumtiles .gtile")
    assert p.eval("document.getElementById('sumdetail').hidden") is False and p.eval("document.querySelectorAll('#sumdetail .thumb').length") == 2
    p.tap("[data-act=save-review]")
    p.wait("document.body.dataset.screen==='room'", what="the room page after saving")
    assert site.svc_.wm.latest_scan_id(rid) == draft

    d2 = site.scan_in(rid, "cup", yaw=10, ts=300, saved=False)
    p.eval("window.__ui.RENDER.summary('review',{summary:{scan_id:%d,room_id:%d,room_name:'Living Room',speech:'I found a cup.',coverage:{covered_deg:50},frames:{stored:1},seconds:5,cost_usd:0,objects:[{id:3,label:'cup',yaw:10,pitch:0,replay:null}]}})" % (d2, rid))
    p.tap("[data-act=discard-review]")
    p.tap("#dlgyes")
    p.wait("document.body.dataset.screen==='home'")
    assert site.svc_.wm.get_scan(d2) is None


def test_the_summary_groups_many_kinds_with_a_tappable_others_tile(site, clean):
    p = booted(site)
    objs = ",".join("{id:%d,label:'kind%d',yaw:%d,pitch:0,replay:null}" % (i, i % 8, i * 10) for i in range(1, 17))
    p.eval("window.__ui.RENDER.summary('review',{summary:{scan_id:1,room_id:1,room_name:'R',speech:'x',coverage:{covered_deg:300},frames:{stored:1},seconds:1,cost_usd:0,objects:[%s]}})" % objs)
    assert p.eval("document.querySelectorAll('#sumtiles .gtile').length") == 6 and p.eval("document.querySelector('#sumtiles .gtile.others')?.textContent").startswith("Others")
    p.tap("#sumtiles .gtile.others")
    assert p.eval("document.getElementById('sumdetail').hidden") is False and p.eval("document.querySelectorAll('#sumdetail .thumb').length") == 6  # the 3 smallest kinds, 2 each


def test_changes_compare_two_scans_of_one_room_with_an_angle_and_confirm(site, clean):
    rid = site.room("Living Room", active=True, label="mug", yaw=0, ts=100)
    site.scan_in(rid, "mug", yaw=60, ts=200)
    first, second = sorted(s["id"] for s in site.svc_.wm.room_scans(rid))
    site.svc_.wm.set_alignment(first, second, GOOD)                 # alignment from pictures has its own tests; this one is about the screen
    other = site.room("Desk", label="mug", yaw=0, ts=150)
    p = booted(site)
    p.tap("#nav [data-nav=changes]")
    p.wait("document.getElementById('s-changes').dataset.state==='default'", what="the comparison")
    assert p.eval("document.querySelectorAll('#chbefore option').length") == 2, "only this room's scans are offered"
    card = p.text("#chg .chgcard")
    assert "moved" in card and "to the right" in card and " m " not in card and "meter" not in card
    p.tap("#chg .chgcard [data-state=confirmed]")
    p.wait("!!document.querySelector('#chg .chgcard.confirmed')", what="the confirmation")
    r = site.svc_.wm.reviews(rid, *site.svc_.wm.default_compare(rid))
    assert list(r.values()) == ["confirmed"]
    p.tap("#chg .chgcard [data-state=dismissed]")
    p.wait("!!document.querySelector('#chg .chgcard.dismissed')")
    assert list(site.svc_.wm.reviews(rid, *site.svc_.wm.default_compare(rid)).values()) == ["dismissed"]
    p.eval("window.__ui.go('home')")
    p.tap(f"#hrooms [data-id='{other}']")  # the other room has one scan only
    p.wait("document.getElementById('s-room').dataset.state==='default'")
    p.tap("[data-act=changes-room]")
    p.wait("document.getElementById('s-changes').dataset.state==='need-two'")
    assert "two saved scans of Desk" in p.text("#chgtext")


def test_room_settings_rename_conflict_and_delete_with_warning(site, clean):
    a = site.room("Living Room", active=True, label="mug")
    b = site.room("Desk", label="cup", yaw=30)
    files = [Path(f["path"]) for s in site.svc_.wm.room_scans(b) for f in site.svc_.wm.list_frames(s["id"])]
    assert files and all(f.exists() for f in files)
    p = booted(site)
    p.tap("#nav [data-nav=settings]")
    p.wait("document.querySelectorAll('#setrooms .srow').length===2")
    p.tap(f"#setrooms [data-id='{b}']")
    assert p.screen() == "roomsettings" and p.eval("document.getElementById('rsname').value") == "Desk"
    p.eval("document.getElementById('rsname').value='Living Room'")
    p.tap("[data-act=rename-room]")
    p.wait("document.getElementById('s-roomsettings').dataset.state==='error'")
    assert "already a room called" in p.text("#rserr") and site.svc_.wm.get_room(b)["name"] == "Desk"
    p.eval("document.getElementById('rsname').value='Study'")
    p.tap("[data-act=rename-room]")
    p.wait("window.__ui.store.get().rooms.some(r=>r.name==='Study')")
    assert site.svc_.wm.get_room(b)["name"] == "Study"
    assert "Erases 1 scan" in p.text("#rsdeltext") and "1 remembered object" in p.text("#rsdeltext")
    p.tap("[data-act=delete-room]")
    assert "cannot be undone" in p.text("#dlgbody") and "Other rooms are not touched" in p.text("#dlgbody")
    assert p.eval("[...document.querySelectorAll('#dlgitems li')].map(l=>l.textContent)") == ["1 scan", "1 object", "every photo in Study"]
    p.tap("#dlgno")
    assert site.svc_.wm.get_room(b) is not None
    p.tap("[data-act=delete-room]")
    p.tap("#dlgyes")
    p.wait("document.body.dataset.screen==='home'")
    assert site.svc_.wm.get_room(b) is None and not any(f.exists() for f in files)  # its photos are gone
    assert site.svc_.wm.get_room(a) is not None and site.svc_.wm.latest_scan_id(a)  # the other room is untouched


def test_wizard_new_room_name_conflict_and_rescan_preselects_the_active_room(site, clean):
    a = site.room("Living Room", active=True, label="mug")
    p = booted(site)
    p.tap("#nav [data-nav=scan]")
    assert p.eval("document.getElementById('s-wizard').dataset.state") == "rescan"
    assert p.eval("document.querySelector('#wzrooms .chip.on')?.textContent") == "Living Room"  # rescan: the active room is chosen
    p.eval("document.getElementById('wzname').value='living room';document.getElementById('wzname').dispatchEvent(new Event('input'))")
    p.tap("#wzgo")
    p.wait("document.getElementById('s-wizard').dataset.state==='error'")
    assert "already a room called" in p.text("#wzerr") and p.gum() == []
    p.eval("document.getElementById('wzname').value='';document.getElementById('wzname').dispatchEvent(new Event('input'))")
    p.tap(f"#wzrooms [data-id='{a}']")
    assert p.eval("document.querySelector('#wzrooms .chip.on')?.textContent") == "Living Room"


def test_delete_all_data_from_settings(site, clean):
    site.room("A", active=True, label="mug")
    site.room("B", label="cup")
    p = booted(site)
    p.tap("#nav [data-nav=settings]")
    p.tap("[data-act=delete-all]")
    assert "All 2 rooms" in p.text("#dlgbody")
    assert p.eval("[...document.querySelectorAll('#dlgitems li')].map(l=>l.textContent).slice(0,3)") == ["2 rooms", "2 scans", "2 objects"]
    p.tap("#dlgno")
    assert len(site.svc_.wm.list_rooms()) == 2
    p.tap("[data-act=delete-all]")
    p.tap("#dlgyes")
    p.wait("document.body.dataset.screen==='home'")
    assert site.svc_.wm.list_rooms() == [] and site.svc_.wm.list_scans() == []
    p.wait("document.getElementById('s-home').dataset.state==='empty'")


def test_search_filters_by_room(site, clean):
    a = site.room("Kitchen", active=True, label="mug")
    b = site.room("Desk", label="mug", yaw=50)
    site.room("Wardrobe", label="shirt", yaw=80)
    p = booted(site)
    p.eval("document.getElementById('hsearch').value='mug';document.getElementById('hsearch').dispatchEvent(new Event('input'))")
    p.wait("document.querySelectorAll('#hresults .thumb').length===2", what="results from both rooms")
    assert p.eval("document.getElementById('hmain').hidden") is True
    p.tap(f"#hchips [data-id='{b}']")
    p.wait("document.querySelectorAll('#hresults .thumb').length===1")
    assert "Desk" in p.text("#hresults")
    p.eval("document.getElementById('hsearch').value='shirt';document.getElementById('hsearch').dispatchEvent(new Event('input'))")
    p.wait("document.getElementById('hresults').textContent.includes('No object called')")
    assert p.screen() == "home"


def test_settings_persist_and_replaying_the_intro_changes_nothing_stored(site, clean):
    p = booted(site, READY, "/?debug")
    assert p.eval("document.body.classList.contains('dbg')") is True
    p.tap("#nav [data-nav=settings]")
    assert p.eval("!document.getElementById('dbgrow').hidden")
    p.tap("#kfseg button:nth-child(3)")
    assert p.eval("document.querySelector('#detailseg button:nth-child(1)').getAttribute('aria-pressed')") == "true" and p.text("#detailseg button:nth-child(1)") == "Calm"  # Calm is the default
    p.tap("#detailseg button:nth-child(2)")
    p.eval("(()=>{const s=document.getElementById('langsel');s.value='hi-IN';s.dispatchEvent(new Event('change'))})()")
    p.tap("#dbgseg [data-v='0']")
    assert p.eval("localStorage.getItem('ros.kfps')") == "3" and p.eval("localStorage.getItem('ros.lang')") == "hi-IN" and p.eval("localStorage.getItem('ros.detail')") == "normal"
    assert p.eval("document.body.classList.contains('dbg')") is False
    p.tap("[data-act=replay-intro]")
    assert p.screen() == "welcome"
    p.tap("[data-act=intro-skip]")
    assert p.screen() == "settings" and p.eval("localStorage.getItem('ros.onboarded')") == "1"
    p.call("Page.reload")
    p.wait("!!window.__ui")
    p.tap("#nav [data-nav=settings]")
    assert p.eval("document.querySelector('#kfseg button:nth-child(3)').getAttribute('aria-pressed')") == "true"
    assert p.eval("document.querySelector('#detailseg button:nth-child(2)').getAttribute('aria-pressed')") == "true"
    assert p.eval("document.getElementById('langsel').value") == "hi-IN"


def test_debug_hud_and_its_setting_are_hidden_without_debug_in_the_url(site, clean):
    p = booted(site, {**READY, "ros.debug": "1"})
    assert p.eval("getComputedStyle(document.getElementById('dev')).display") == "none"
    p.tap("#nav [data-nav=settings]")
    assert p.eval("document.getElementById('dbgrow').hidden") is True


def test_a_spoken_reply_is_also_shown_as_text(site, clean):
    p = booted(site, READY, "/?demo")
    p.eval("window.__ui.go('find');window.__ui.say('The charger is 60 degrees to your left.')")
    assert p.eval("getComputedStyle(document.getElementById('cap')).display") == "block"
    assert p.text("#captext") == "The charger is 60 degrees to your left."
    assert p.eval("window.__spoken") == ["The charger is 60 degrees to your left."]  # it was "spoken" into the recorder, not out loud


# ------------------------------------------------------------------------------------------------ demo mode and the registry

def test_registry_matches_the_markup_and_every_state_has_a_demo(site):
    """Every screen in screens.js has a section, every state a fixture, and the markup uses no state the registry does not list."""
    reg = registry()
    html = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    import re
    for sid, states in reg.items():
        assert f'id="s-{sid}"' in html, f"no section for {sid}"
        sect = html[html.index(f'id="s-{sid}"'):]
        sect = sect[:sect.index("</section>")]
        used = {t for m in re.findall(r'data-show="([^"]+)"', sect) for t in m.split()}
        assert used <= set(states), f"{sid}: markup uses states the registry does not list: {used - set(states)}"
    assert len(re.findall(r'<section class="scr[^"]*" id="s-', html)) == len(reg)


@pytest.mark.parametrize("sid", sorted(registry()))
def test_every_screen_opens_in_every_demo_state_without_errors_or_media(site, sid):
    for st in registry()[sid]:
        p = site.page().open(f"/?demo&screen={sid}&state={st}")
        p.settle(0.5)
        assert p.eval(f"document.getElementById('s-{sid}').dataset.state") == st, (sid, st)
        if sid not in ("switcher", "quick", "note", "ask", "history"):  # sheets open over the screen below
            assert p.screen() == sid, (sid, st)
        assert p.eval(f"document.getElementById('s-{sid}').classList.contains('on')") is True, (sid, st)
        assert p.media() == [] and p.errors == [], (sid, st, p.errors)
        p.close()


def test_demo_home_and_flows_without_a_server(site):
    p = site.page().open("/?demo")
    assert p.screen() == "home" and "Living Room" in p.text("#hrooms") and p.text("#stat") == "Connected · API key valid"
    p.tap("#nav [data-nav=scan]")
    assert p.screen() == "wizard"
    p.tap("#wzgo")  # the active room is preselected: no primer, no camera in demo
    assert p.screen() == "scan" and p.eval("document.getElementById('s-scan').dataset.state") == "ready"
    p.tap("#startbtn")
    p.wait("document.getElementById('s-scan').dataset.state==='live'")
    p.tap("#scancancel")
    p.tap("#dlgyes")
    assert p.screen() == "home"
    p.tap("#nav [data-nav=find]")
    p.eval("document.getElementById('q').value='where is my keys'")
    p.tap("#findbtn")
    assert p.screen() == "result" and p.media() == [] and p.errors == []
    p.eval("window.__ui.go('find')")
    p.eval("document.getElementById('q').value='where is my passport'")
    p.tap("#findbtn")
    assert p.screen() == "result" and p.eval("document.getElementById('s-result').dataset.state") == "notfound"
    assert "87% of the room" in p.text("#nftext") and "not checked" in p.text("#nftext")


# ------------------------------------------------------------------------------------------------ touch targets

@pytest.mark.parametrize("sid", sorted(registry()))
def test_every_visible_button_is_a_large_tap_target(site, sid):
    p = site.page().open(f"/?demo&screen={sid}")
    p.settle(0.6)
    small = p.eval("""[...document.querySelectorAll('button,select,input[type=text],input[type=search]')].filter(e=>e.offsetParent!==null&&!e.closest('#dev')&&!e.hidden&&!e.closest('[hidden]')).map(e=>{const b=e.getBoundingClientRect();
      return [e.id||e.dataset.act||e.className||e.textContent.slice(0,20),Math.round(b.width),Math.round(b.height)]}).filter(([,w,h])=>w<44||h<44)""")
    assert small == [], f"tap targets under 44px on {sid}: {small}"


# ------------------------------------------------------------------------------------------------ calm overlay

def overlay_stats(p, settle=1.2):
    time.sleep(settle)
    return p.eval("window.__ar.stats()")


def test_calm_is_the_default_overlay_few_labels_and_no_compass_outside_guide(site, clean):
    p = site.page(READY).open("/?demo&screen=explore&objects=60&fov=100&yaw=40")
    s = overlay_stats(p)
    assert p.eval("window.__ui.prefs.get('detail')") == "calm"
    assert s["hud"] is False and 1 <= s["labels"] <= 5 and s["objects"] == 60
    assert s["hiddenBy"]["overflow"] + s["hiddenBy"]["collision"] > 0, "most of the visible objects stayed quiet"
    assert p.errors == []


def test_normal_and_debug_draw_everything_with_the_compass(site, clean):
    for mode in ("normal", "debug"):
        p = site.page({**READY, "ros.detail": mode}).open("/?demo&screen=explore&objects=60&fov=100&yaw=40")
        s = overlay_stats(p)
        assert s["hud"] is True and s["drawn"] > 5 and "labels" not in s, (mode, s)
        assert p.errors == []


def test_the_compass_tape_shows_in_guide_even_in_calm(site, clean):
    p = site.page(READY).open("/?demo&screen=guide&yaw=40")
    assert overlay_stats(p)["hud"] is True
    q = site.page(READY).open("/?demo&screen=scan&state=live&yaw=40")
    assert overlay_stats(q)["hud"] is False


def test_changing_the_detail_setting_changes_the_overlay_at_once(site, clean):
    p = site.page(READY).open("/?demo&screen=explore&objects=60&fov=100&yaw=40")
    assert overlay_stats(p)["hud"] is False
    p.eval("window.__ui.prefs.set('detail','normal')")
    assert overlay_stats(p, 0.8)["hud"] is True
    p.eval("window.__ui.prefs.set('detail','calm')")
    assert overlay_stats(p, 0.8)["hud"] is False


# ------------------------------------------------------------------------------------------------ objects review and quick ask

def bottles_scene(site):
    """A room with a saved scan: bottle A (two sightings), bottle B 15 degrees away, a cup."""
    wm = site.svc_.wm
    rid = wm.create_room("Study", active=True)["id"]
    sid = wm.start_scan(room_id=rid, ts=1.0)
    for yaw, ts, label, conf in ((0, 1.0, "bottle", 0.9), (2, 2.0, "bottle", 0.7), (15, 3.0, "bottle", 0.8), (90, 4.0, "cup", 0.6)):
        fid = wm.add_frame(sid, ts, yaw, 0.0, 480, 640, roll=0.0, screen_angle=0.0, jpeg=b"jpg")
        wm.observe(fid, [{"label": label, "conf": conf, "box": [220, 300, 260, 340], "polygon": [[220, 300], [260, 300], [260, 340]]}])
    wm.end_scan(sid, 5.0)
    return rid, sid


def test_phone_objects_review_from_labels_to_sightings_with_recorded_corrections(site, clean):
    rid, sid = bottles_scene(site)
    wm = site.svc_.wm
    bottles = lambda: [o for o in wm.list_objects(sid) if o["label"] == "bottle"]  # noqa: E731
    p = booted(site)
    p.tap(f"#hrooms [data-id='{rid}']")
    p.wait("document.getElementById('s-room').dataset.state==='default'")
    p.tap("#rhist [data-act=inspect-scan]")
    p.wait("document.getElementById('s-labels').dataset.state==='default'")
    rows = p.eval("[...document.querySelectorAll('#lbrows .lrow')].map(r=>r.textContent)")
    assert "bottle" in rows[0] and "2 ×" in rows[0] and "3 sightings" in rows[0] and "80%" in rows[0] and "cup" in rows[1]
    assert "1 possible duplicate" in p.text("#lbdups")
    p.tap("#lbrows .lrow")
    p.wait("document.getElementById('s-instances').dataset.state==='default'")
    assert p.eval("document.querySelectorAll('#inrows .irow').length") == 2 and p.eval("document.getElementById('mergebtn').disabled") is True
    p.tap("#inrows .irow:nth-child(1) .pick")  # the list is redrawn after each tap, so tap them one by one
    p.tap("#inrows .irow:nth-child(2) .pick")
    p.wait("!document.getElementById('mergebtn').disabled")
    assert "Merge selected (2)" in p.text("#mergebtn")
    p.tap("#mergebtn")
    p.wait("document.querySelectorAll('#inrows .irow').length===1")
    assert len(bottles()) == 1 and [(c["kind"], c["source"]) for c in wm.list_corrections(scan_id=sid)] == [("merge", "phone")]

    p.tap("#inrows .irow-open")
    p.wait("document.getElementById('s-sightings').dataset.state==='default'")
    assert p.eval("document.querySelectorAll('#sirows .srow3').length") == 3 and "SAM confidence" in p.text("#sirows .srow3")
    p.tap("#sirows [data-act=detach-sighting]")
    p.wait("document.querySelectorAll('#sirows .srow3').length===2")
    assert sorted(o["n_obs"] for o in bottles()) == [1, 2]

    p.tap("[data-act=delete-object]")
    p.tap("#dlgyes")
    p.wait("document.body.dataset.screen==='instances'")
    assert len(bottles()) == 1 and "delete" in [c["kind"] for c in wm.list_corrections(scan_id=sid)]

    p.tap("#notobjbtn")
    p.tap("#dlgyes")
    p.wait("document.body.dataset.screen==='labels'")
    p.wait("document.getElementById('lbhidden').textContent.includes('Not an object in this room')")
    assert wm.not_object_labels(rid) == ["bottle"] and bottles() == []
    p.tap("#lbhidden [data-act=unmark-object]")
    p.wait("document.querySelectorAll('#lbrows .lrow').length===2")
    assert wm.not_object_labels(rid) == [] and len(bottles()) == 1
    assert {c["source"] for c in wm.list_corrections(scan_id=sid)} | {c["source"] for c in wm.list_corrections(room_id=rid)} == {"phone"}
    assert p.errors == []


def test_the_review_screen_opens_the_objects_review(site, clean):
    rid, sid = bottles_scene(site)
    p = booted(site)
    p.eval("window.__ui.RENDER.summary('review',{summary:{scan_id:%d,room_id:%d,room_name:'Study',speech:'x',coverage:{covered_deg:200},frames:{stored:1},seconds:5,cost_usd:0,objects:[{id:1,label:'cup',yaw:0,pitch:0,replay:null}]}})" % (sid, rid))
    p.tap("[data-act=inspect-scan]")
    p.wait("document.getElementById('s-labels').dataset.state==='default'")
    assert "bottle" in p.text("#lbrows")


def test_quick_ask_is_a_bottom_sheet_not_a_row_of_chips(site, clean):
    site.room("Study", active=True, label="mug")
    p = booted(site)
    p.tap("#nav [data-nav=find]")
    p.wait("document.querySelectorAll('#quicklist .chip').length>=1", what="the room's names")
    assert p.eval("document.querySelectorAll('#s-find .body .chip').length") == 0, "no chips on the screen itself"
    assert p.eval("document.getElementById('s-quick').classList.contains('on')") is False
    p.tap("#quickbtn")
    assert p.eval("document.getElementById('s-quick').classList.contains('on')") is True and "mug" in p.text("#quicklist")
    p.tap("#quicklist .chip")
    p.wait("document.body.dataset.screen==='result'", what="the result of the quick ask")
    assert p.eval("document.getElementById('s-quick').classList.contains('on')") is False


# ------------------------------------------------------------------------------------------------ notes, ask the room, rewind, caregiver sharing

def explore_room(site, name="Study", label="mug"):
    rid = site.room(name, active=True, label=label)
    p = booted(site)
    p.tap(f"#hrooms [data-id='{rid}']")
    p.wait("document.getElementById('s-room').dataset.state==='default'")
    p.tap("[data-act=explore-room]")
    p.wait("document.body.classList.contains('live')")
    return rid, p


def test_a_note_is_left_listed_seen_from_another_page_and_marked_done(site, clean):
    rid, p = explore_room(site)
    p.tap("#s-explore [data-act=open-note]")
    p.wait("document.getElementById('s-note').classList.contains('on')")
    assert p.eval("document.getElementById('s-note').dataset.state") == "empty" and "direction you are facing" in p.text("#noteat") or "mug" in p.text("#noteat")
    p.eval("document.getElementById('notetext').value='check the stove'")
    p.tap("#notesave")
    p.wait("document.querySelectorAll('#notelist .nrow').length===1", what="the note in the list")
    note = site.svc_.wm.list_notes(rid)[0]
    assert note["text"] == "check the stove" and note["author"] == "phone"
    q = site.page(READY).open("/")                                   # a second phone, or the same one later
    q.wait(HOME_LOADED)
    q.tap(f"#hrooms [data-id='{rid}']")
    q.wait("document.getElementById('s-room').dataset.state==='default'")
    q.tap("[data-act=explore-room]")
    q.wait("window.__ui.world.notes.some(n=>n.text==='check the stove')", what="the note on the other page")
    q.tap("#s-explore [data-act=open-note]")                          # the other page lists it too, and marks it done
    q.wait("document.querySelectorAll('#notelist .nrow').length===1")
    q.tap("#notelist [data-act=note-done]")
    q.wait("document.querySelectorAll('#notelist .nrow').length===0")
    assert site.svc_.wm.list_notes(rid) == [] and q.errors == []


def test_ask_the_room_draws_only_the_objects_the_answer_names(site, clean):
    from test_room_features import Asker, Broken
    rid, p = explore_room(site, label="mug")
    oid = site.svc_.wm.list_objects(site.svc_.wm.latest_scan_id(rid))[0]["id"]
    site.svc_.reasoner = Asker({"answer": "The mug is the only thing here.", "matches": [{"id": oid, "reason": "it is the only object"}]})
    p.tap("#s-explore [data-act=open-ask]")
    p.eval("document.getElementById('askq').value='what is here?'")
    p.tap("#askgo")
    p.wait("document.getElementById('askbar').classList.contains('on')", what="the answer bar")
    assert "only thing here" in p.text("#askbartext") and p.eval("window.__ui.world.asked.size") == 1
    assert p.eval("document.getElementById('s-ask').classList.contains('on')") is False
    p.tap("#askbar [data-act=clear-ask]")
    assert p.eval("window.__ui.world.asked.size") == 0 and p.eval("document.getElementById('askbar').classList.contains('on')") is False
    site.svc_.reasoner = Broken()
    p.tap("#s-explore [data-act=open-ask]")
    p.tap("#s-ask [data-act=ask-example]")
    p.wait("document.getElementById('s-ask').dataset.state==='error'", what="the error state")
    assert p.text("#askrmsg") and p.eval("document.getElementById('s-ask').classList.contains('on')") is True and p.errors == []


def test_rewind_shows_what_an_earlier_scan_had_as_ghosts_and_clears(site, clean):
    rid = site.room("Study", active=True, label="mug", ts=100.0)
    site.scan_in(rid, "lamp", 0.0, 200.0)                              # the mug is gone, a lamp is there now
    first, second = sorted(s["id"] for s in site.svc_.wm.room_scans(rid))
    site.svc_.wm.set_alignment(first, second, GOOD)
    p = booted(site)
    p.tap(f"#hrooms [data-id='{rid}']")
    p.wait("document.getElementById('s-room').dataset.state==='default'")
    p.tap("[data-act=explore-room]")
    p.wait("document.body.classList.contains('live')")
    p.tap("#s-explore [data-act=open-history]")
    p.wait("document.getElementById('s-history').dataset.state==='default'")
    assert p.eval("document.querySelectorAll('#histlist [data-act=pick-then]').length") == 1
    p.tap("#histlist [data-act=pick-then]")
    p.wait("window.__ui.world.ghosts.length===1", what="the ghost")
    assert p.eval("window.__ui.world.ghosts[0].label") == "mug" and p.eval("document.getElementById('thenbar').classList.contains('on')") is True
    p.tap("#thenbar [data-act=clear-then]")
    assert p.eval("window.__ui.world.ghosts.length") == 0 and p.errors == []


def test_rewind_with_a_single_saved_scan_says_there_is_nothing_earlier(site, clean):
    rid, p = explore_room(site)
    p.tap("#s-explore [data-act=open-history]")
    p.wait("document.getElementById('s-history').dataset.state==='empty'")


def test_caregiver_sharing_is_off_until_the_person_turns_it_on_in_settings(site, clean):
    from app import care
    p = booted(site)
    p.tap("#nav [data-nav=settings]")
    p.wait("document.getElementById('s-settings').dataset.state==='default'")
    assert p.eval("document.querySelector('#careseg [data-v=\"0\"]').getAttribute('aria-pressed')") == "true" and care.consent(site.svc_)["on"] is False
    p.tap("#careseg [data-v='1']")
    p.wait("document.querySelector('#careseg [data-v=\"1\"]').getAttribute('aria-pressed')==='true'")
    assert care.consent(site.svc_)["on"] is True
    p.tap("#careseg [data-v='0']")
    p.wait("document.querySelector('#careseg [data-v=\"0\"]').getAttribute('aria-pressed')==='true'")
    assert care.consent(site.svc_)["on"] is False


# ------------------------------------------------------------------------------------------------ lining up a rescan, and the alignment on Changes

def test_a_rescan_shows_the_first_scans_outline_and_start_is_never_blocked(site, clean):
    from synthetic_world import add_scan, make_panorama
    rid = site.svc_.wm.create_room("Study", active=True)["id"]
    add_scan(site.svc_.wm, rid, make_panorama(), [0, 40, 80], ts=1.0)
    p = booted(site)
    p.tap("#nav [data-nav=scan]")
    p.tap("#wzgo")
    p.wait("document.body.dataset.screen==='scan'")
    p.wait("!document.getElementById('ghost').hidden", what="the first scan's outline")
    assert f"/api/rooms/{rid}/reference.png" in p.eval("document.getElementById('ghost').getAttribute('src')")
    assert p.eval("getComputedStyle(document.getElementById('ghost')).opacity") == "0.3"
    assert p.eval("document.getElementById('chkalign').hidden") is False
    p.eval("window.__ui.align({ok:true,within:false,inliers:80,words:'Turn left 8 degrees, move slightly right.',turn_deg:-8,tilt_deg:1,roll_deg:0,move:'right',parallax_deg:2.4})")
    assert "Turn left 8 degrees" in p.text("#aligntext") and p.text("#startbtn") == "Start anyway" and p.eval("document.getElementById('startbtn').disabled") is False
    p.eval("window.__ui.align({ok:true,within:true,inliers:120,words:'Lined up. Start scanning.',turn_deg:1,tilt_deg:0,roll_deg:0,move:null,parallax_deg:0.4})")
    assert p.text("#startbtn") == "Start scanning" and p.eval("document.getElementById('startbtn').classList.contains('ghost')") is False and p.errors == []


def test_the_first_scan_of_a_room_has_nothing_to_line_up_with(site, clean):
    site.svc_.wm.create_room("Fresh", active=True)
    p = booted(site)
    p.tap("#nav [data-nav=scan]")
    p.tap("#wzgo")
    p.wait("document.body.dataset.screen==='scan'")
    p.settle(1.0)
    assert p.eval("document.getElementById('ghost').hidden") is True and p.eval("document.getElementById('chkalign').hidden") is True
    assert p.text("#startbtn") == "Start scanning"


def test_changes_shows_the_alignment_and_keeps_possibly_moved_and_cant_compare_apart(site, clean):
    p = site.page(READY).open("/?demo&screen=changes&state=default")
    p.wait("document.getElementById('s-changes').dataset.state==='default'")
    p.settle(0.5)
    assert p.eval("document.querySelector('#chg .align.good h2')?.textContent") == "✓Alignment: good" and "214 matching points" in p.text("#chg .align")
    heads = p.eval("[...document.querySelectorAll('#chg h2')].map(h=>h.textContent)")
    assert any(h.startswith("↔Moved") for h in heads) and any(h.startswith("≈Possibly moved") for h in heads) and any(h.startswith("?Can’t compare") for h in heads)
    assert p.eval("document.querySelectorAll('#chg .chgcard[data-kind=possibly_moved]').length") == 1
    card = p.text("#chg .chgcard[data-kind=possibly_moved]")
    assert "may have moved" in card and " m " not in card and "meter" not in card


def test_a_weak_alignment_is_explained_and_no_move_is_shown(site, clean):
    p = site.page(READY).open("/?demo&screen=changes&state=default")
    p.wait("document.getElementById('s-changes').dataset.state==='default'")
    p.eval("(()=>{const c=window.__ui.RENDER;const d=JSON.parse(JSON.stringify(document.getElementById('chg')._data));"
           "d.alignment={quality:'weak',inliers:30,pairs:1,spread_deg:4};d.moved=[];d.possibly_moved=[];d.removed=[];d.added=[];d.summary='The two scans could not be lined up reliably, so I cannot say what moved.';"
           "c.changes('default',{room:{id:1,name:'Living Room'},data:d})})()")
    assert p.eval("!!document.querySelector('#chg .align.weak')") and "nothing is called moved" in p.text("#chg .align")
    assert p.eval("[...document.querySelectorAll('#chg h2')].some(h=>/Moved|Removed|New/.test(h.textContent))") is False
