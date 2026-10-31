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

