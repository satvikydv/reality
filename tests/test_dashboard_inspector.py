"""The objects inspector on /dashboard, in a real browser (see test_entry_flow.py for the harness)."""
import pytest

from test_entry_flow import BROWSER, clean, site  # noqa: F401  (fixtures)
from test_scan_service import jpeg

pytestmark = [pytest.mark.skipif(BROWSER is None, reason="no Chrome or Edge found (set CHROME_BIN)"),
              pytest.mark.filterwarnings("ignore:connect.. must be used as a context manager")]

W, H = 480, 640
CENTRE = [220.0, 300.0, 260.0, 340.0]
POLY = [[220, 300], [260, 300], [260, 340], [220, 340]]


def scene(site):
    """A scan with bottle A (two sightings), bottle B 15 degrees away (never in a frame with A), and a cup."""
    wm = site.svc_.wm
    rid = wm.create_room("Study", active=True)["id"]
    sid = wm.start_scan(room_id=rid, ts=1.0)
    img = jpeg()
    for yaw, ts, label, conf in ((0, 1.0, "bottle", 0.9), (2, 2.0, "bottle", 0.7), (15, 3.0, "bottle", 0.8), (90, 4.0, "cup", 0.6)):
        fid = wm.add_frame(sid, ts, yaw, 0.0, W, H, roll=0.0, screen_angle=0.0, jpeg=img)
        wm.observe(fid, [{"label": label, "conf": conf, "box": CENTRE, "polygon": POLY}])
    wm.end_scan(sid, 5.0)
    return rid, sid


def inspector(site):
    p = site.page({})
    p.call("Page.navigate", url=site.base + "/dashboard")
    p.call("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)
    p.wait("!!window.__inspector", what="the dashboard")
    p.eval("window.confirm=()=>true")  # the destructive actions ask first; the test says yes
    p.tap("#objbtn")
    p.wait("document.querySelectorAll('#ilabels .row').length>=2", what="the labels")
    return p


def objs(wm, sid, label):
    return [o for o in wm.list_objects(sid) if o["label"] == label]


def test_the_inspector_groups_by_label_and_shows_large_sightings_and_decisions(site, clean):
    rid, sid = scene(site)
    p = inspector(site)
    rows = p.eval("[...document.querySelectorAll('#ilabels .row')].map(r=>r.textContent)")
    assert "bottle" in rows[0] and "2 ×" in rows[0] and "3 sightings" in rows[0] and "80%" in rows[0] and "cup" in rows[1]
    assert p.eval("document.querySelectorAll('#iinst .row').length") == 2
    p.wait("document.querySelectorAll('#idetail .sight canvas').length>=2", what="the sightings of the first instance")
    sizes = p.eval("[...document.querySelectorAll('#idetail .sight canvas')].map(c=>[Math.round(c.getBoundingClientRect().width),Math.round(c.getBoundingClientRect().height)])")
    assert all(w >= 380 and h >= 380 for w, h in sizes), f"sighting images must be large: {sizes}"
    text = p.text("#idetail")
    assert "SAM confidence" in text and "90%" in text and "mask 4 pts" in text and "Decision log" in text
    assert "Started a new object" in text and "Joined an object by" in text
    assert p.errors == []


def test_merge_two_instances_is_recorded_and_survives(site, clean):
    rid, sid = scene(site)
    wm = site.svc_.wm
    p = inspector(site)
    boxes = p.eval("document.querySelectorAll('#iinst .row input').length")
    assert boxes == 2 and p.eval("document.querySelector('#iinst .go').disabled") is True
    p.eval("document.querySelectorAll('#iinst .row input').forEach(b=>b.click())")
    p.wait("!document.querySelector('#iinst .go').disabled", what="the merge button")
    assert "Merge selected (2)" in p.text("#iinst .go")
    p.tap("#iinst .go")
    p.wait("document.getElementById('imsg').textContent.startsWith('Merged into')", what="the merge")
    assert len(objs(wm, sid, "bottle")) == 1 and objs(wm, sid, "bottle")[0]["n_obs"] == 3
    assert [(c["kind"], c["source"]) for c in wm.list_corrections(scan_id=sid)] == [("merge", "dashboard")]
    p.wait("document.querySelectorAll('#iinst .row').length===1")
    p.tap("#ilog")
    p.wait("document.querySelectorAll('#idetail .corr').length===1")
    assert "merge" in p.text("#idetail .corr") and "by dashboard" in p.text("#idetail .corr")


def test_detach_delete_and_not_an_object(site, clean):
    rid, sid = scene(site)
    wm = site.svc_.wm
    wm.merge_objects([o["id"] for o in objs(wm, sid, "bottle")])
    p = inspector(site)
    p.wait("document.querySelectorAll('#idetail .sight').length===3")
    p.eval("document.querySelectorAll('#idetail .sight button')[2].click()")  # detach the third sighting
    p.wait("document.getElementById('imsg').textContent.startsWith('Detached')")
    assert sorted(o["n_obs"] for o in objs(wm, sid, "bottle")) == [1, 2]

    p.tap("#idetail .danger")  # delete the selected object
    p.wait("document.getElementById('imsg').textContent.startsWith('Deleted') || document.getElementById('imsg').textContent.includes('objects,')")
    assert len(objs(wm, sid, "bottle")) == 1
    assert "delete" in [c["kind"] for c in wm.list_corrections(scan_id=sid)]

    p.eval("[...document.querySelectorAll('#ilabels .row')].find(r=>r.textContent.startsWith('cup')).click()")
    p.wait("document.querySelector('#iinst h2').textContent.startsWith('cup')")
    p.tap("#iinst .ih .danger")
    p.wait("document.getElementById('imsg').textContent.startsWith('Hidden')")
    assert wm.not_object_labels(rid) == ["cup"] and objs(wm, sid, "cup") == []
    p.wait("!!document.querySelector('#ilabels .row small')")
    assert "Not an object here" in p.text("#ilabels")
    p.eval("[...document.querySelectorAll('#ilabels button')].find(b=>b.textContent==='Restore').click()")
    p.wait("document.getElementById('imsg').textContent.startsWith('Restored')")
    assert wm.not_object_labels(rid) == [] and len(objs(wm, sid, "cup")) == 1
    assert p.errors == []


def test_possible_duplicates_list_with_a_merge_button(site, clean):
    rid, sid = scene(site)
    wm = site.svc_.wm
    p = inspector(site)
    assert "(1)" in p.text("#idups")
    p.tap("#idups")
    p.wait("document.querySelectorAll('#idetail .dup').length===1")
    assert "bottle" in p.text("#idetail .dup") and "apart" in p.text("#idetail .dup")
    assert p.eval("document.querySelectorAll('#idetail .dup canvas').length") == 2
    p.tap("#idetail .dup .go")
    p.wait("document.getElementById('imsg').textContent.startsWith('Merged') || document.querySelectorAll('#idetail .dup').length===0")
    assert len(objs(wm, sid, "bottle")) == 1
    p.tap("#idups")
    p.wait("!!document.querySelector('#idetail .empty')")
    assert "None found" in p.text("#idetail")


def test_the_inspector_works_on_a_draft_scan_and_switches_scans(site, clean):
    rid, sid = scene(site)
    wm = site.svc_.wm
    draft = wm.start_scan(room_id=rid, ts=100.0, status="draft")
    fid = wm.add_frame(draft, 100.0, 0, 0.0, W, H, roll=0.0, screen_angle=0.0, jpeg=jpeg())
    wm.observe(fid, [{"label": "keys", "conf": 0.5, "box": CENTRE}])
    p = inspector(site)
    options = p.eval("[...document.querySelectorAll('#iscans option')].map(o=>o.textContent)")
    assert len(options) == 2 and any("draft" in o for o in options) and any("memory" in o for o in options)
    p.eval("(()=>{const s=document.getElementById('iscans');s.value='%d';s.dispatchEvent(new Event('change'))})()" % draft)
    p.wait("document.querySelector('#ilabels .row b')?.textContent==='keys'")
    assert p.text("#idetail h3") == "keys #" + str(objs(wm, draft, "keys")[0]["id"])
