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
                             "--disable-gpu", "--no-default-browser-check", "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
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

        def scan(self, yaws=(0,)):
            with websockets_sync.connect(f"ws://127.0.0.1:{port}/ws") as ws:
                ws.send(json.dumps({"type": "scan_start"}))
                assert json.loads(ws.recv())["type"] == "scan_started"
                for i, yaw in enumerate(yaws):
                    ws.send(frame_msg(i + 1, 2, meta(yaw), jpeg()))
                ws.send(json.dumps({"type": "scan_end"}))
                while json.loads(ws.recv())["type"] != "scan_summary":
                    pass

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


@pytest.fixture()
def clean(site):
    site.svc_.wm.db.execute("DELETE FROM scans")  # every test starts with no scans unless it makes one
    site.svc_.wm.db.commit()
    yield


# ------------------------------------------------------------------------------------------------ entry flow

@pytest.mark.parametrize("seed,landing,path", [({}, "welcome", "/"), (READY, "home", "/"), (READY, "home", "/?debug"), ({}, "welcome", "/?debug")])
def test_opening_the_page_does_not_touch_camera_sensors_or_microphone(site, clean, seed, landing, path):
    p = site.page(seed).open(path)
    p.wait("document.getElementById('stat').textContent.startsWith('Connected')", what="the server check to finish")
    p.settle(1.5)  # long enough for any auto-start, a status poll and the world layer to have run
    assert p.media() == [], f"the page touched media or sensor APIs on load: {p.media()}"
    assert p.screen() == landing
    assert p.eval("document.getElementById('v').srcObject") is None
    assert p.eval("document.body.classList.contains('live')") is False
    assert p.errors == []


def test_home_shows_the_connection_and_the_four_buttons(site, clean):
    p = site.page(READY).open()
    p.wait("document.getElementById('stat').textContent.startsWith('Connected')")
    assert p.text("#stat") == "Connected · API key valid"
    assert "No scan yet" in p.text("#lastcard")
    assert [p.text(s) for s in ("#scanbtn", "#askbtn", "#chgbtn", "#setbtn")] == ["New scan", "Find something", "What changed", "Settings"]
    assert p.eval("['scanbtn','askbtn','chgbtn','setbtn'].every(i=>!document.getElementById(i).disabled)")


def test_the_spy_really_sees_the_camera_so_the_empty_log_above_means_something(site, clean):
    p = site.page(READY).open()
    p.wait("!document.getElementById('scanbtn').disabled")
    assert p.media() == []
    p.tap("#askbtn")
    p.wait("document.body.classList.contains('live')", what="the camera to start")
    assert len(p.gum()) == 1 and '"audio":false' in p.gum()[0]


@pytest.mark.parametrize("button,screen", [("#scanbtn", "scan"), ("#askbtn", "ask")])
def test_the_camera_starts_from_a_tap_on_new_scan_or_find_something(site, clean, button, screen):
    p = site.page(READY).open()
    p.wait("!document.getElementById('scanbtn').disabled")
    p.settle(0.5)
    assert p.gum() == []
    p.tap(button)
    p.wait("document.body.classList.contains('live')")
    assert p.screen() == screen and len(p.gum()) == 1
    assert not any("audio\":true" in m for m in p.media()), "the microphone must never be requested"


def test_the_camera_starts_from_a_tap_on_guide_me(site, clean):
    p = site.page(READY).open()
    p.wait("!document.getElementById('scanbtn').disabled")
    p.eval("window.__ui.go('find')")
    p.settle(0.3)
    assert p.gum() == []
    p.tap("#guidebtn")
    p.wait("document.body.classList.contains('live')")
    assert p.screen() == "guide" and len(p.gum()) == 1


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
    p.tap("#introskip")
    assert p.screen() == "home" and p.media() == []
    p.call("Page.reload")
    p.wait("!!window.__ui")
    assert p.screen() == "home", "a second visit goes straight to Home"
    assert p.eval("localStorage.getItem('ros.onboarded')") == "1"


def test_a_user_who_skipped_the_intro_still_gets_the_primer_before_the_camera(site, clean):
    p = site.page({"ros.onboarded": "1"}).open()
    p.wait("!document.getElementById('scanbtn').disabled")
    p.tap("#scanbtn")
    assert p.screen() == "primer" and p.media() == []
    p.tap("#primergo")
    p.wait("document.body.dataset.screen==='scan'", what="the scan screen after the permissions")
    p.wait("document.body.classList.contains('live')")
    assert p.eval("document.getElementById('scan').dataset.ph") == "ready", "the pre-scan checklist, not a scan that already started"


def test_denied_camera_is_explained_and_can_be_retried(site, clean):
    p = site.page({"ros.onboarded": "1"}).open()
    p.wait("!document.getElementById('scanbtn').disabled")
    p.eval("window.__deny='NotAllowedError'")
    p.tap("#scanbtn")
    p.tap("#primergo")
    p.wait("!document.getElementById('primerproblem').hidden", what="the explanation")
    assert "Camera access was blocked" in p.text("#problemtitle") and "Try again" in p.text("#problembody")
    assert p.text("#primergo") == "Try again" and p.screen() == "primer"
    assert p.eval("localStorage.getItem('ros.primed')") is None, "a refusal is not remembered as done"
    p.eval("window.__deny=null")
    p.tap("#primergo")
    p.wait("document.body.dataset.screen==='scan'")  # the retry worked and carried on to the scan the user asked for
    assert p.eval("localStorage.getItem('ros.primed')") == "1"


def test_a_camera_taken_away_later_goes_back_to_the_explanation(site, clean):
    p = site.page(READY).open()
    p.wait("!document.getElementById('scanbtn').disabled")
    p.eval("window.__deny='NotAllowedError'")
    p.tap("#askbtn")
    p.wait("document.body.dataset.screen==='primer'")
    assert "Camera access was blocked" in p.text("#problemtitle")
    assert p.eval("document.body.classList.contains('live')") is False


# ------------------------------------------------------------------------------------------------ camera lifecycle

def test_every_way_out_of_a_camera_screen_stops_the_camera(site, clean):
    p = site.page(READY).open()
    p.wait("!document.getElementById('scanbtn').disabled")
    stops = lambda: p.eval("window.__stops")  # noqa: E731

    p.tap("#askbtn")
    p.wait("document.body.classList.contains('live')")
    assert p.eval("document.querySelector('#ask .camstop').textContent").startswith("Camera on") and p.text("#ask .camstop b") == "STOP"
    p.wait("window.__media.includes('wakeLock.request')", what="the wake lock")
    assert "orientation.lock portrait" in p.media()
    assert any(m.startswith("new AbsoluteOrientationSensor") for m in p.media()), "the sensors start with the camera"

    p.tap("#ask [data-stop]")  # the Stop control
    assert p.screen() == "home" and stops() == 1
    assert p.eval("document.getElementById('v').srcObject") is None and p.eval("document.body.classList.contains('live')") is False
    assert "wakeLock.release" in p.media() and "orientation.unlock" in p.media()

    p.tap("#askbtn")  # Back button
    p.wait("document.body.classList.contains('live')")
    p.tap("#askback")
    assert p.screen() == "home" and stops() == 2

    p.tap("#askbtn")  # a result: leaving for another screen
    p.wait("document.body.classList.contains('live')")
    p.eval("window.__ui.go('find')")
    assert stops() == 3 and p.eval("document.getElementById('v').srcObject") is None

    p.eval("window.__ui.go('home')")
    p.tap("#scanbtn")  # cancelling before the scan starts
    p.wait("document.body.classList.contains('live')")
    assert p.eval("document.getElementById('scan').dataset.ph") == "ready" and p.eval("getComputedStyle(document.getElementById('checklist')).display") != "none"
    p.tap("#scancancel")
    assert p.screen() == "home" and stops() == 4
    assert len(p.gum()) == 4 and p.errors == []


def test_hiding_the_tab_stops_the_camera_and_showing_it_does_not_restart_it(site, clean):
    p = site.page(READY).open()
    p.wait("!document.getElementById('scanbtn').disabled")
    p.tap("#askbtn")
    p.wait("document.body.classList.contains('live')")
    p.eval("Object.defineProperty(document,'hidden',{get:()=>true,configurable:true});document.dispatchEvent(new Event('visibilitychange'))")
    assert p.eval("window.__stops") == 1 and p.eval("document.getElementById('v').srcObject") is None
    assert p.screen() == "home" and "hidden" in p.text("#toast")
    p.eval("Object.defineProperty(document,'hidden',{get:()=>false,configurable:true});document.dispatchEvent(new Event('visibilitychange'))")
    p.settle(0.8)
    assert len(p.gum()) == 1, "coming back does not turn the camera on by itself"
    assert p.eval("document.body.classList.contains('live')") is False


def test_a_track_that_ends_by_itself_turns_everything_off(site, clean):
    p = site.page(READY).open()
    p.wait("!document.getElementById('scanbtn').disabled")
    p.tap("#askbtn")
    p.wait("document.body.classList.contains('live')")
    p.eval("(()=>{const t=document.getElementById('v').srcObject.getVideoTracks()[0];t.stop();t.dispatchEvent(new Event('ended'))})()")
    p.wait("document.body.dataset.screen==='home'")
    assert p.eval("document.body.classList.contains('live')") is False and "wakeLock.release" in p.media()


# ------------------------------------------------------------------------------------------------ states

def test_offline_server_is_shown_and_the_page_reconnects_by_itself(site, clean):
    p = site.page(READY).open()
    p.wait("document.getElementById('stat').textContent.startsWith('Connected')")
    p.eval("window.__offline=true")
    p.wait("document.getElementById('stat').textContent==='Server offline'", timeout=20, what="the offline state")
    assert p.eval("document.getElementById('scanbtn').disabled") is True
    assert "Reconnecting automatically" in p.text("#noticetext") and p.eval("!document.getElementById('homenotice').hidden")
    assert p.eval("document.getElementById('setbtn').disabled") is False, "Settings still opens"
    p.eval("window.__offline=false")
    p.wait("document.getElementById('stat').textContent.startsWith('Connected')", timeout=15, what="the page to reconnect without a reload")
    assert p.eval("document.getElementById('scanbtn').disabled") is False


def test_last_scan_summary_and_delete_from_settings(site, clean):
    site.scan(yaws=(0, 90))
    p = site.page(READY).open()
    p.wait("document.getElementById('lastcard').textContent.includes('Last scan')")
    card = p.text("#lastcard")
    assert "Objects" in card and "Covered" in card and "When" in card and ("just now" in card or "min ago" in card)
    p.tap("#setbtn")
    p.wait("document.getElementById('scanlist').textContent.includes('Scan #')")
    p.tap(".srow button")
    assert p.eval("document.getElementById('dlg').classList.contains('on')") and "cannot be undone" in p.text("#dlgbody")
    p.tap("#dlgno")
    assert "Scan #" in p.text("#scanlist"), "cancelling the dialog keeps the scan"
    p.tap(".srow button")
    p.tap("#dlgyes")
    p.wait("document.getElementById('scanlist').textContent.includes('No scans are stored')")
    assert site.svc_.wm.list_scans() == []
    p.tap("#setback")
    p.wait("document.getElementById('lastcard').textContent.includes('No scan yet')", timeout=20)


def test_settings_persist_and_replaying_the_intro_changes_nothing_stored(site, clean):
    p = site.page(READY).open("/?debug")
    p.wait("!document.getElementById('scanbtn').disabled")
    assert p.eval("document.body.classList.contains('dbg')") is True
    p.tap("#setbtn")
    assert p.eval("!document.getElementById('dbgrow').hidden")
    p.tap("#kfseg button:nth-child(3)")
    p.eval("(()=>{const s=document.getElementById('langsel');s.value='hi-IN';s.dispatchEvent(new Event('change'))})()")
    p.tap("#dbgseg button[data-v='0']")
    assert p.eval("localStorage.getItem('ros.kfps')") == "3" and p.eval("localStorage.getItem('ros.lang')") == "hi-IN"
    assert p.eval("document.body.classList.contains('dbg')") is False
    p.tap("#replayintro")
    assert p.screen() == "welcome"
    p.tap("#introskip")
    assert p.screen() == "settings" and p.eval("localStorage.getItem('ros.onboarded')") == "1"
    p.call("Page.reload")
    p.wait("!!window.__ui")
    p.tap("#setbtn")
    assert p.eval("document.querySelector('#kfseg button:nth-child(3)').getAttribute('aria-pressed')") == "true"
    assert p.eval("document.getElementById('langsel').value") == "hi-IN"


def test_debug_hud_and_its_setting_are_hidden_without_debug_in_the_url(site, clean):
    p = site.page({**READY, "ros.debug": "1"}).open()
    p.wait("!document.getElementById('scanbtn').disabled")
    assert p.eval("getComputedStyle(document.getElementById('dev')).display") == "none"
    p.tap("#setbtn")
    assert p.eval("document.getElementById('dbgrow').hidden") is True


def test_a_spoken_reply_is_also_shown_as_text(site, clean):
    p = site.page(READY).open("/?demo")
    p.eval("window.__ui.go('ask');window.__ui.say('The charger is 60 degrees to your left.')")
    assert p.eval("getComputedStyle(document.getElementById('cap')).display") == "block"
    assert p.text("#captext") == "The charger is 60 degrees to your left."


# ------------------------------------------------------------------------------------------------ demo mode

DEMO = {"summary": "summary", "find": "find", "guide": "guide", "changes": "changes", "scan": "scan", "welcome": "welcome", "primer": "primer",
        "primer-denied": "primer", "settings": "settings", "ask": "ask", "ask-busy": "ask", "notfound": "find", "scan-ready": "scan",
        "processing": "summary", "changes-empty": "changes", "home-offline": "home", "home-loading": "home", "home-empty": "home", "home-nokey": "home"}


@pytest.mark.parametrize("name,screen", DEMO.items())
def test_demo_mode_screens_still_open_without_media(site, name, screen):
    p = site.page().open(f"/?demo&screen={name}")
    p.settle(0.6)
    assert p.screen() == screen
    assert p.media() == []
    assert p.errors == [], p.errors


def test_demo_home_and_flows_without_a_server_check(site):
    p = site.page().open("/?demo")
    assert p.screen() == "home" and p.text("#stat") == "Connected · API key valid" and "Last scan" in p.text("#lastcard")
    p.tap("#scanbtn")  # demo: no primer and no camera, straight to the checklist
    assert p.screen() == "scan" and p.eval("document.getElementById('scan').dataset.ph") == "ready"
    p.tap("#startbtn")
    p.wait("document.getElementById('scan').dataset.ph==='live'")
    p.tap("#scancancel")
    p.tap("#dlgyes")
    assert p.screen() == "home"
    p.tap("#askbtn")
    p.eval("document.getElementById('q').value='where is my keys'")
    p.tap("#findbtn")
    assert p.screen() == "find" and p.media() == [] and p.errors == []
    p.eval("window.__ui.go('home')")
    p.tap("#askbtn")
    p.eval("document.getElementById('q').value='where is my passport'")
    p.tap("#findbtn")
    assert p.screen() == "find" and "Not found in your last scan" in p.text("#nftitle")
    assert "87% of the room" in p.text("#nftext") and "not checked" in p.text("#nftext")


def test_not_found_explains_what_was_not_checked(site):
    p = site.page().open("/?demo&screen=notfound")
    assert p.eval("document.getElementById('result').classList.contains('nf')") is True
    assert "87% of the room" in p.text("#nftext")
    assert p.eval("getComputedStyle(document.getElementById('guidebtn')).display") == "none"


# ------------------------------------------------------------------------------------------------ touch targets

TARGETS = ["home", "ask", "scan-ready", "scan", "find", "guide", "settings", "welcome", "primer", "notfound", "processing", "changes-empty"]


@pytest.mark.parametrize("name", TARGETS)
def test_every_visible_button_is_a_large_tap_target(site, name):
    p = site.page().open(f"/?demo&screen={name}")
    p.settle(0.6)
    small = p.eval("""[...document.querySelectorAll('button,select,input[type=text]')].filter(e=>e.offsetParent!==null&&!e.closest('#dev')).map(e=>{const b=e.getBoundingClientRect();
      return [e.id||e.className||e.textContent.slice(0,20),Math.round(b.width),Math.round(b.height)]}).filter(([,w,h])=>w<44||h<44)""")
    assert small == [], f"tap targets under 44px on {name}: {small}"
