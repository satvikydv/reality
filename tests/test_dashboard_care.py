"""The caregiver view on /dashboard, in a real browser (see test_entry_flow.py for the harness)."""
import time

import pytest

from app import care
from test_entry_flow import BROWSER, clean, site  # noqa: F401  (fixtures)

pytestmark = [pytest.mark.skipif(BROWSER is None, reason="no Chrome or Edge found (set CHROME_BIN)"),
              pytest.mark.filterwarnings("ignore:connect.. must be used as a context manager")]


def dashboard(site):
    p = site.page({})
    p.call("Page.navigate", url=site.base + "/dashboard")
    p.call("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)
    p.wait("!!window.__care", what="the dashboard")
    p.tap("#carebtn")
    p.wait("document.getElementById('care').classList.contains('on')")
    return p


def test_without_consent_the_view_says_so_and_shows_no_places(site, clean):
    site.room("Bedroom", active=True, label="pills", ts=time.time())
    site.svc_.wm.add_watch("pills")
    p = dashboard(site)
    p.wait("document.querySelectorAll('#careitems .citem').length===1")
    assert "OFF" in p.text("#careconsent") and "Settings on their phone" in p.text("#careconsent")
    assert "hidden until sharing is on" in p.text("#careitems") and p.eval("document.querySelectorAll('#careitems canvas').length") == 0
    assert p.eval("document.getElementById('careask').disabled") is True


def test_with_consent_it_shows_the_room_the_photo_the_age_and_alerts_and_answers_questions(site, clean):
    from test_room_features import Asker
    now = time.time()
    bed = site.room("Bedroom", active=True, label="mug", ts=now - 3600)
    site.room("Kitchen", label="pills", ts=now - 7200)
    wm = site.svc_.wm
    wm.add_watch("pills", bed)                                   # usually in the bedroom, seen in the kitchen
    wm.add_watch("wallet")                                       # not seen anywhere
    care.set_consent(site.svc_, True)
    site.svc_.reasoner = Asker(concept="pills")
    p = dashboard(site)
    p.wait("document.querySelectorAll('#careitems .citem').length===2")
    assert "ON since" in p.text("#careconsent")
    items = p.text("#careitems")
    assert "Kitchen" in items and "usually lives in Bedroom" in items and "Not seen in the last scan of any room" in items
    assert p.eval("document.querySelectorAll('#careitems .citem canvas').length") == 1
    assert p.eval("document.querySelectorAll('#carealerts .calert').length") == 2
    p.eval("document.getElementById('carelabel').value='glasses'")
    p.tap("#careadd")
    p.wait("document.querySelectorAll('#careitems .citem').length===3", what="the new item")
    p.eval("document.getElementById('careq').value='where are the tablets?'")
    p.tap("#careask")
    p.wait("document.getElementById('careans').textContent.includes('Kitchen')", what="the answer")
    assert "not live" in p.text("#careans") and p.eval("document.querySelectorAll('#careans canvas').length") == 1
    assert p.errors == []


def test_the_dashboard_demo_shows_the_caregiver_view_without_a_server(site, clean):
    p = site.page({})
    p.call("Page.navigate", url=site.base + "/dashboard?demo")
    p.call("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)
    p.wait("!!window.__care", what="the dashboard")
    p.tap("#carebtn")
    p.wait("document.querySelectorAll('#careitems .citem').length===3", what="the demo items")
    assert "ON" in p.text("#careconsent") and "usually lives in Bedroom" in p.text("#careitems") and "Not seen" in p.text("#careitems")
    p.eval("document.getElementById('careq').value='where are the pills?'")
    p.tap("#careask")
    p.wait("!!document.querySelector('#careans .ans')", what="the answer")
    assert "not live" in p.text("#careans") and p.errors == []
    assert p.eval("document.querySelectorAll('#care canvas').length") == 0      # a sighting with no photo draws no empty box



def test_sharing_off_never_says_all_clear(site, clean):
    site.room("Bedroom", active=True, label="pills", ts=time.time())
    site.svc_.wm.add_watch("pills")
    p = dashboard(site)
    p.wait("document.querySelectorAll('#careitems .citem').length===1")
    assert "sharing is off" in p.text("#caresum") and "All clear" not in p.text("#caresum") + p.text("#carealerts")
    assert "nothing can be checked" in p.text("#carealerts")


def test_all_clear_is_said_only_when_everything_is_where_it_belongs(site, clean):
    now = time.time()
    bed = site.room("Bedroom", active=True, label="pills", ts=now - 3600)
    site.svc_.wm.add_watch("pills", bed)
    care.set_consent(site.svc_, True)
    p = dashboard(site)
    p.wait("document.querySelectorAll('#careitems .citem').length===1")
    assert "All clear" in p.text("#caresum") and "All clear" in p.text("#carealerts")
    site.svc_.wm.add_watch("wallet")                                           # not seen anywhere: no longer all clear
    p.wait("document.querySelectorAll('#careitems .citem').length===2", timeout=15)
    assert "1 needs attention" in p.text("#caresum") and "All clear" not in p.text("#carealerts")


def test_quick_add_suggestions_add_a_watch_in_one_click_and_hide_what_is_already_watched(site, clean):
    site.svc_.wm.add_watch("keys")
    p = dashboard(site)
    p.wait("document.querySelectorAll('#caresug button').length>0")
    assert "keys" not in p.text("#caresug") and "+ pills" in p.text("#caresug")
    p.tap("#caresug button")
    p.wait("document.querySelectorAll('#careitems .citem').length===2")
    assert "pills" in [w["label"] for w in site.svc_.wm.list_watches()]


def test_the_idle_dashboard_shows_the_active_rooms_last_scan_and_how_to_connect_an_agent(site, clean):
    site.room("Study", active=True, label="mug", ts=time.time() - 600)
    p = site.page({})
    p.call("Page.navigate", url=site.base + "/dashboard")
    p.call("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)
    p.wait("!!window.__care", what="the dashboard")
    p.wait("document.getElementById('co').textContent==='1'", what="the room's objects on the map")
    assert "Study" in p.text("#phase") and "last scan" in p.text("#phase") and p.text("#cs") == "–" and p.text("#cu") == "–"
    assert "claude mcp add" in p.text("#prev") and "Scan a room twice" in p.text("#chsum") and p.errors == []


def test_notes_need_sharing_then_go_on_the_object_picked_and_can_be_edited_done_and_deleted(site, clean):
    rid = site.room("Kitchen", active=True, label="mug", yaw=40.0, ts=time.time())
    wm = site.svc_.wm
    p = dashboard(site)
    p.wait("!document.getElementById('cnoff').hidden", what="the sharing-off message")
    assert p.eval("document.getElementById('cnon').hidden") is True        # nothing about their rooms before they say yes
    care.set_consent(site.svc_, True)
    p.wait("!document.getElementById('cnon').hidden && document.querySelectorAll('#cnobjs .cnobj').length===1", timeout=15, what="the room's things")
    assert p.text("#cnobjs .cnobj") == "mug"
    p.tap("#cnobjs .cnobj")
    assert "On the mug in Kitchen" in p.text("#cnat")
    p.eval("document.getElementById('cntext').value='Take the evening pills after dinner';document.getElementById('cnfrom').value='Maya'")
    p.tap("#cnadd")
    p.wait("document.querySelectorAll('#cnlist .cnote').length===1", what="the note in the list")
    n = wm.list_notes(rid)[0]
    obj = wm.list_objects(wm.latest_scan_id(rid))[0]
    assert (n["text"], n["author"], n["label"]) == ("Take the evening pills after dinner", "Maya", "mug")
    assert abs(((n["yaw"] - obj["yaw"] + 180) % 360) - 180) < 0.5 and abs(n["pitch"] - obj["pitch"]) < 0.5  # it hangs on the mug
    p.tap("#cnlist .cnote .acts button")                                    # Edit
    p.eval("document.querySelector('#cnlist .cnote textarea').value='Take the pills at 8'")
    p.tap("#cnlist .cnote .acts .go")                                       # Save
    p.wait("document.querySelector('#cnlist .cnote p')?.textContent==='Take the pills at 8'", what="the edited note")
    assert wm.get_note(n["id"])["text"] == "Take the pills at 8"
    p.eval("[...document.querySelectorAll('#cnlist .cnote .acts button')].find(b=>b.textContent==='Done').click()")
    p.wait("!document.querySelector('#cnlist .cnote')", what="the done note gone from the list")
    assert wm.get_note(n["id"])["done_at"] is not None
    wm.set_note_done(n["id"], False)
    p.eval("document.getElementById('cnroom').dispatchEvent(new Event('change'))")
    p.wait("document.querySelectorAll('#cnlist .cnote').length===1")
    p.eval("window.confirm=()=>true;[...document.querySelectorAll('#cnlist .cnote .acts button')].find(b=>b.textContent==='Delete').click()")
    p.wait("!document.querySelector('#cnlist .cnote')", what="the deleted note gone")
    assert wm.get_note(n["id"]) is None and p.errors == []
