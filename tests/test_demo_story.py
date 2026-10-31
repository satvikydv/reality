"""The caregiver demo, end to end over HTTP on the synthetic world, with a stand-in for the model (no network):

a person scans their kitchen twice (the pills moved in between), turns on caregiver sharing, and the caregiver sees where the pills were last
seen, rewinds the kitchen to see where they were before, asks "what is unsafe in here", and leaves a note by the stove that the overlay serves.
"""
import time

from app import ask
from synthetic_world import add_scan, make_panorama
from test_room_features import Asker
from test_scan_service import client  # noqa: F401  (client is a fixture)

PANO = make_panorama()
YAWS = list(range(0, 360, 40))


def kitchen(wm):
    rid = wm.create_room("Kitchen", active=True)["id"]
    bed = wm.create_room("Bedroom")["id"]
    now = time.time()
    before = add_scan(wm, rid, PANO, YAWS, ts=now - 2 * 86400, objects=[("pills", 80.0, 0.0), ("stove", 200.0, 0.0), ("mug", 120.0, 0.0), ("knife", 260.0, 0.0)])
    after = add_scan(wm, rid, PANO, YAWS, ts=now - 3600, true_jitter=(9.0, 1.0),
                     objects=[("pills", 150.0, 0.0), ("stove", 200.0, 0.0), ("mug", 120.0, 0.0), ("knife", 260.0, 0.0)])
    return rid, bed, before, after


def test_the_caregiver_story_works_end_to_end_and_claims_nothing_it_cannot_know(client):
    wm, svc = client.svc.wm, client.svc
    rid, bed, before, after = kitchen(wm)

    # Caregiver view: nothing shows until the person turns sharing on; then where the pills were last seen, and that it is not where they live
    assert client.post("/api/care/watch", json={"label": "pills", "usual_room_id": bed}).status_code == 201
    off = client.get("/api/care").json()
    assert off["status"] == "no_consent" and "seen" not in off["items"][0]
    client.post("/api/care/consent", json={"on": True})
    care = client.get("/api/care").json()
    pills = care["items"][0]
    assert care["status"] == "ok" and pills["seen"][0]["room"]["name"] == "Kitchen" and pills["seen"][0]["replay"]
    assert [a["code"] for a in pills["alerts"]] == ["unusual_place"] and care["as_of"] == "the last scan of each room"

    # Changes: the pills moved, nothing else is claimed
    ch = client.get(f"/api/rooms/{rid}/changes").json()
    assert ch["alignment"]["quality"] == "good" and [m["label"] for m in ch["moved"]] == ["pills"]
    assert ch["removed"] == [] and ch["added"] == [] and ch["possibly_moved"] == [] and ch["moved_low_confidence"] == []
    assert "local" not in ch["alignment"] and "frame_poses" not in ch["alignment"]                # internals stay out of the API answer

    # Rewind: a ring where the pills were two days ago, in today's directions, sure (the scans lined up well)
    rw = client.get(f"/api/rooms/{rid}/rewind?scan={before}").json()
    ghost = [g for g in rw["ghosts"] if g["label"] == "pills"]
    assert rw["status"] == "ok" and len(ghost) == 1 and ghost[0]["kind"] == "moved" and ghost[0]["unsure"] is False and abs(ghost[0]["yaw"] - 80.0) < 4

    # Ask "what is unsafe in here": the model sees only text in a delimited block, may only point at real objects, and the answer carries the limit
    objs = {o["label"]: o["id"] for o in wm.list_objects(after)}
    svc.reasoner = Asker({"answer": "The stove and the knife might need a look.",
                          "matches": [{"id": objs["stove"], "reason": "heat"}, {"id": objs["knife"], "reason": "a blade"}, {"id": 99999, "reason": "made up"}]})
    a = client.post(f"/api/rooms/{rid}/ask", json={"question": "what is unsafe in here"}).json()
    sent = svc.reasoner.seen[0]
    assert a["status"] == "ok" and sorted(m["label"] for m in a["matches"]) == ["knife", "stove"]          # the invented id is dropped
    assert a["answer"].endswith(ask.SAFETY_NOTE) and "never say that" in sent[0]["content"]
    assert sent[1]["content"].startswith("<room>") and "</room>" in sent[1]["content"] and "moved: pills" in sent[1]["content"]
    assert all(isinstance(m["content"], str) for m in sent)                                               # text only: no photo is ever sent

    # A note by the stove: stored in the room and served with the overlay, where any phone in the room draws it
    stove = next(o for o in wm.list_objects(after) if o["label"] == "stove")
    r = client.post(f"/api/rooms/{rid}/notes", json={"text": "Turn the stove off after cooking", "yaw": stove["yaw"], "pitch": stove["pitch"], "label": "stove", "author": "Sam"})
    assert r.status_code == 201
    ov = client.get(f"/api/overlay?room_id={rid}").json()
    assert [n["text"] for n in ov["notes"]] == ["Turn the stove off after cooking"] and ov["notes"][0]["label"] == "stove"


def test_a_question_that_is_not_about_safety_gets_no_safety_note(client):
    wm, svc = client.svc.wm, client.svc
    rid, _, _, after = kitchen(wm)
    mug = next(o for o in wm.list_objects(after) if o["label"] == "mug")
    svc.reasoner = Asker({"answer": "The mug.", "matches": [{"id": mug["id"], "reason": "you drink from it"}]})
    a = client.post(f"/api/rooms/{rid}/ask", json={"question": "what do I drink from"}).json()
    assert a["answer"] == "The mug." and ask.SAFETY_NOTE not in a["answer"]
