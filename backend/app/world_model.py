"""World Model: scans, frames with orientation, objects anchored to (yaw, pitch), replay (F2, F10, F11).

Storage is SQLite (in memory by default). Frames are optional JPEG files under `frames_dir` so a
find query can return a replay frame. Anchors are angular only: translation is unknown, so every
statement about where something is is relative to the scan (see app/anchoring.py).

Identity inside a scan: a detection joins an existing object with the same normalised label whose
anchor is within `merge_deg`, unless that object already matched another detection in the same frame
(two mugs in one view stay two objects). Appearance re-identification and cross-scan matching are
not here yet (what-changed, F13).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path

from collections import deque

from app import anchoring as A
from app import memory

log = logging.getLogger("memory")

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY, name TEXT, source TEXT, started_at REAL NOT NULL, ended_at REAL);
CREATE TABLE IF NOT EXISTS frames (
  id INTEGER PRIMARY KEY, scan_id INTEGER NOT NULL REFERENCES scans(id), ts REAL NOT NULL,
  yaw REAL NOT NULL, pitch REAL NOT NULL, roll REAL, screen_angle REAL, source TEXT, api TEXT,
  width INTEGER NOT NULL, height INTEGER NOT NULL, hfov_deg REAL NOT NULL, path TEXT);
CREATE TABLE IF NOT EXISTS objects (
  id INTEGER PRIMARY KEY, scan_id INTEGER NOT NULL REFERENCES scans(id), label TEXT NOT NULL,
  first_seen REAL NOT NULL, last_seen REAL NOT NULL, yaw REAL NOT NULL, pitch REAL NOT NULL,
  weight REAL NOT NULL, n_obs INTEGER NOT NULL, spread_deg REAL NOT NULL, conf_best REAL NOT NULL,
  last_box TEXT, last_frame_id INTEGER, state TEXT NOT NULL DEFAULT '{}', attrs TEXT NOT NULL DEFAULT '{}',
  track_ids TEXT NOT NULL DEFAULT '[]', fov_err_deg REAL NOT NULL DEFAULT 0, uncertainty_deg REAL NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS objects_scan_label ON objects(scan_id, label);
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY, object_id INTEGER NOT NULL REFERENCES objects(id), frame_id INTEGER NOT NULL REFERENCES frames(id),
  ts REAL NOT NULL, box TEXT NOT NULL, conf REAL NOT NULL, yaw REAL NOT NULL, pitch REAL NOT NULL, poly TEXT);
CREATE INDEX IF NOT EXISTS obs_object ON observations(object_id);
-- Every detection ever stored, with the object it currently belongs to. Objects and observations are derived from
-- this table (see `rebuild`), so they do not depend on the order frames arrived in.
CREATE TABLE IF NOT EXISTS detections (
  id INTEGER PRIMARY KEY, scan_id INTEGER NOT NULL, frame_id INTEGER NOT NULL REFERENCES frames(id), idx INTEGER NOT NULL,
  label TEXT NOT NULL, box TEXT NOT NULL, conf REAL NOT NULL, yaw REAL NOT NULL, pitch REAL NOT NULL, ext TEXT,
  fov_err REAL NOT NULL DEFAULT 0, fragment INTEGER NOT NULL DEFAULT 0, poly TEXT, hist TEXT, track_id INTEGER, object_id INTEGER);
CREATE INDEX IF NOT EXISTS det_scan ON detections(scan_id);
"""

_ARTICLES = {"a", "an", "the", "my", "your"}
SLIVER_FRACTION = 0.03  # a border-touching box smaller than this share of the frame never starts an object


def normalize_label(label: str) -> str:
    words = re.sub(r"[^a-z0-9 ]+", " ", label.lower()).split()
    while words and words[0] in _ARTICLES:
        words.pop(0)
    return " ".join(words)


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _words(label: str) -> set[str]:
    return {_stem(w) for w in normalize_label(label).split()}


def label_match_score(query: str, label: str) -> float:
    """1.0 same words; 0.7 one is a whole-word subset of the other ("remote" vs "remote control"); else 0."""
    q, l = _words(query), _words(label)
    if not q or not l:
        return 0.0
    if q == l:
        return 1.0
    if q <= l or l <= q:
        return 0.7
    return 0.0


_IRREGULAR = {"mouse": "mice", "knife": "knives", "shelf": "shelves", "box": "boxes", "glass": "glasses", "bag": "bags"}


def plural(label: str) -> str:
    """Pluralise the last word: "water bottle" -> "water bottles", "computer mouse" -> "computer mice"."""
    head, _, last = label.rpartition(" ")
    return (head + " " if head else "") + _IRREGULAR.get(last, last if last.endswith("s") and not last.endswith("ss") else last + "s")


def describe_offset(dyaw: float, dpitch: float) -> str:
    """Words for a direction relative to where the scan started."""
    a = abs(dyaw)
    if a < 8:
        horiz = "straight ahead of where the scan started"
    elif a > 150:
        horiz = "behind where the scan started"
    else:
        horiz = f"about {round(a / 5) * 5:.0f} degrees to the {'right' if dyaw > 0 else 'left'} of where the scan started"
    if abs(dpitch) < 10:
        vert = "roughly level"
    else:
        vert = f"about {round(abs(dpitch) / 5) * 5:.0f} degrees {'above' if dpitch > 0 else 'below'} level"
    return f"{horiz}, {vert}"


class WorldModel:
    def __init__(self, db_path: str | Path = ":memory:", frames_dir: str | Path | None = None,
                 merge_deg: float = 8.0, default_hfov_deg: float = A.DEFAULT_HFOV_DEG, fov_rel_error: float = A.FOV_REL_ERROR,
                 memory_cfg: memory.Config | None = None):
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()
        self.frames_dir = Path(frames_dir) if frames_dir else None
        self.merge_deg = merge_deg
        self.default_hfov_deg = default_hfov_deg
        self.fov_rel_error = fov_rel_error
        self.memory_cfg = memory_cfg or memory.Config(merge_deg=merge_deg)
        self.decision_log: deque = deque(maxlen=5000)  # every merge / veto / promotion decision, newest last
        self._logged: dict[int, set] = {}
        self.lock = threading.RLock()

    def _migrate(self) -> None:
        """Bring a database written by an older version up to the current schema."""
        def cols(t):
            return {r["name"] for r in self.db.execute(f"PRAGMA table_info({t})")}
        if "poly" not in cols("observations"):
            self.db.execute("ALTER TABLE observations ADD COLUMN poly TEXT")
        for c in ("fov_err_deg", "uncertainty_deg"):
            if c not in cols("objects"):
                self.db.execute(f"ALTER TABLE objects ADD COLUMN {c} REAL NOT NULL DEFAULT 0")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------ scans and frames

    def start_scan(self, name: str | None = None, source: str | None = None, ts: float | None = None) -> int:
        with self.lock:
            cur = self.db.execute("INSERT INTO scans (name, source, started_at) VALUES (?,?,?)",
                                  (name, source, time.time() if ts is None else ts))
            self.db.commit()
            return cur.lastrowid

    def end_scan(self, scan_id: int, ts: float | None = None) -> None:
        with self.lock:
            self.db.execute("UPDATE scans SET ended_at=? WHERE id=?", (time.time() if ts is None else ts, scan_id))
            self.db.commit()

    def latest_scan_id(self) -> int | None:
        with self.lock:
            r = self.db.execute("SELECT id FROM scans ORDER BY started_at DESC, id DESC LIMIT 1").fetchone()
            return r["id"] if r else None

    def add_frame(self, scan_id: int, ts: float, yaw: float, pitch: float, width: int, height: int,
                  roll: float | None = None, screen_angle: float | None = 0.0, source: str | None = None,
                  api: str | None = None, hfov_deg: float | None = None, jpeg: bytes | None = None) -> int:
        """Store a keyframe with the camera orientation it was taken at. Box coordinates passed to
        `observe` must be in this frame's width x height pixel space."""
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO frames (scan_id, ts, yaw, pitch, roll, screen_angle, source, api, width, height, hfov_deg)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (scan_id, ts, yaw % 360.0, pitch, roll, screen_angle, source, api, width, height,
                 hfov_deg or self.default_hfov_deg))
            fid = cur.lastrowid
            if jpeg is not None and self.frames_dir is not None:
                path = self.frames_dir / str(scan_id) / f"{fid}.jpg"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(jpeg)
                self.db.execute("UPDATE frames SET path=? WHERE id=?", (str(path), fid))
            self.db.commit()
            return fid

    def get_frame(self, frame_id: int) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM frames WHERE id=?", (frame_id,)).fetchone()
            return dict(r) if r else None

    def frame_jpeg(self, frame_id: int) -> bytes | None:
        f = self.get_frame(frame_id)
        return Path(f["path"]).read_bytes() if f and f["path"] and Path(f["path"]).exists() else None

    def list_scans(self) -> list[dict]:
        """Every scan, newest first, with how many frames and objects it holds."""
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT s.id, s.name, s.source, s.started_at, s.ended_at,"
                " (SELECT COUNT(*) FROM frames f WHERE f.scan_id=s.id) AS n_frames,"
                " (SELECT COUNT(*) FROM objects o WHERE o.scan_id=s.id) AS n_objects"
                " FROM scans s ORDER BY s.started_at DESC, s.id DESC")]

    def get_scan(self, scan_id: int) -> dict | None:
        return next((s for s in self.list_scans() if s["id"] == scan_id), None)

    def list_frames(self, scan_id: int) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM frames WHERE scan_id=? ORDER BY ts, id", (scan_id,))]

    def scan_start_pose(self, scan_id: int) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT yaw, pitch FROM frames WHERE scan_id=? ORDER BY ts, id LIMIT 1", (scan_id,)).fetchone()
            return dict(r) if r else None

    # ------------------------------------------------------------------ observing

    @staticmethod
    def _sliver(box, width: int, height: int) -> bool:
        """A small box touching the image border: part of something the neighbouring frame sees whole."""
        m = 2.0
        touches = box[0] <= m or box[1] <= m or box[2] >= width - m or box[3] >= height - m
        return touches and (box[2] - box[0]) * (box[3] - box[1]) < SLIVER_FRACTION * width * height

    def observe(self, frame_id: int, detections: list[dict]) -> list[dict]:
        """Store the detections {label, box:[x1,y1,x2,y2], conf?, polygon?, hist?, track_id?} seen in a frame, then rebuild
        the scan's objects from every detection stored so far. Returns one dict per detection: object_id (None while an
        edge-clipped fragment waits for a second sighting), new, yaw, pitch."""
        with self.lock:
            fr = self.get_frame(frame_id)
            if fr is None:
                raise KeyError(f"unknown frame {frame_id}")
            roll = A.image_roll(fr["roll"], fr["screen_angle"])
            before = {r["id"] for r in self.db.execute("SELECT id FROM objects WHERE scan_id=?", (fr["scan_id"],))}
            made = []
            for idx, d in enumerate(detections):
                label = normalize_label(d["label"])
                if not label:
                    continue
                box = [float(x) for x in d["box"]]
                yaw, pitch = A.box_anchor(tuple(box), fr["width"], fr["height"], fr["yaw"], fr["pitch"], roll, fr["hfov_deg"])
                ext = A.box_extent(tuple(box), fr["width"], fr["height"], fr["yaw"], fr["pitch"], roll, fr["hfov_deg"])
                off = A.angular_distance(yaw, pitch, fr["yaw"], fr["pitch"])
                poly, hist = d.get("polygon"), d.get("hist")
                cur = self.db.execute(
                    "INSERT INTO detections (scan_id, frame_id, idx, label, box, conf, yaw, pitch, ext, fov_err, fragment, poly, hist, track_id)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (fr["scan_id"], frame_id, idx, label, json.dumps(box), float(d.get("conf", 1.0)), yaw, pitch, json.dumps(ext),
                     memory.fov_error_deg(off, self.fov_rel_error), int(self._sliver(box, fr["width"], fr["height"])),
                     json.dumps(poly) if poly else None, json.dumps(hist) if hist else None, d.get("track_id")))
                made.append((cur.lastrowid, yaw, pitch))
            self.rebuild(fr["scan_id"], newest_frame=frame_id)
            out = []
            for did, yaw, pitch in made:
                oid = self.db.execute("SELECT object_id FROM detections WHERE id=?", (did,)).fetchone()["object_id"]
                e = {"object_id": oid, "new": oid is not None and oid not in before, "yaw": yaw, "pitch": pitch}
                if oid is None:
                    e["skipped"] = "fragment"
                out.append(e)
            return out

    def _ensure_detections(self, scan_id: int) -> None:
        """A scan stored before detections were kept: recreate them from its observations so it can be rebuilt."""
        if self.db.execute("SELECT 1 FROM detections WHERE scan_id=? LIMIT 1", (scan_id,)).fetchone():
            return
        rows = self.db.execute(
            "SELECT ob.object_id, ob.frame_id, ob.box, ob.conf, ob.poly, o.label, f.yaw AS cy, f.pitch AS cp, f.roll, f.screen_angle, f.width, f.height, f.hfov_deg"
            " FROM observations ob JOIN objects o ON o.id=ob.object_id JOIN frames f ON f.id=ob.frame_id WHERE o.scan_id=? ORDER BY ob.id", (scan_id,)).fetchall()
        for idx, r in enumerate(rows):
            box = json.loads(r["box"])
            roll = A.image_roll(r["roll"], r["screen_angle"])
            yaw, pitch = A.box_anchor(tuple(box), r["width"], r["height"], r["cy"], r["cp"], roll, r["hfov_deg"])
            ext = A.box_extent(tuple(box), r["width"], r["height"], r["cy"], r["cp"], roll, r["hfov_deg"])
            self.db.execute(
                "INSERT INTO detections (scan_id, frame_id, idx, label, box, conf, yaw, pitch, ext, fov_err, fragment, poly, object_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (scan_id, r["frame_id"], idx, r["label"], r["box"], r["conf"], yaw, pitch, json.dumps(ext),
                 memory.fov_error_deg(A.angular_distance(yaw, pitch, r["cy"], r["cp"]), self.fov_rel_error),
                 int(self._sliver(box, r["width"], r["height"])), r["poly"], r["object_id"]))

    def rebuild(self, scan_id: int, newest_frame: int | None = None) -> int:
        """Recompute a scan's objects from all its detections (see app/memory.py). The result depends only on the
        detections and their capture timestamps, not on the order they were stored in. Object ids are kept where an
        object still contains the same detections. Returns how many objects the scan has."""
        with self.lock:
            self._ensure_detections(scan_id)
            rows = self.db.execute(
                "SELECT d.*, f.ts AS fts, f.yaw AS cam_yaw, f.pitch AS cam_pitch FROM detections d JOIN frames f ON f.id=d.frame_id"
                " WHERE d.scan_id=? ORDER BY d.id", (scan_id,)).fetchall()
            by_id = {r["id"]: r for r in rows}
            dets = [memory.Det(id=r["id"], frame_id=r["frame_id"], ts=r["fts"], cam_yaw=r["cam_yaw"], cam_pitch=r["cam_pitch"], label=r["label"],
                               box=tuple(json.loads(r["box"])), conf=r["conf"], yaw=r["yaw"], pitch=r["pitch"],
                               ext=tuple(json.loads(r["ext"])) if r["ext"] else None, fragment=bool(r["fragment"]),
                               hist=json.loads(r["hist"]) if r["hist"] else None, fov_err=r["fov_err"], track_id=r["track_id"],
                               prev_object=r["object_id"], idx=r["idx"]) for r in rows]
            res = memory.cluster(dets, self.memory_cfg)
            self._record_decisions(scan_id, res.decisions, newest_frame)

            # keep ids: an object continues the old object that shares the most detections with it
            old_ids = [r["id"] for r in self.db.execute("SELECT id FROM objects WHERE scan_id=? ORDER BY id", (scan_id,))]
            votes = sorted(((sum(1 for m in c.members if m.prev_object == oid), oid, i) for i, c in enumerate(res.clusters) for oid in old_ids
                            if any(m.prev_object == oid for m in c.members)), key=lambda t: (-t[0], t[1], t[2]))
            assigned: dict[int, int] = {}
            used: set[int] = set()
            for _, oid, i in votes:
                if i not in assigned and oid not in used:
                    assigned[i] = oid
                    used.add(oid)

            self.db.execute("DELETE FROM observations WHERE object_id IN (SELECT id FROM objects WHERE scan_id=?)", (scan_id,))
            self.db.execute("UPDATE detections SET object_id=NULL WHERE scan_id=?", (scan_id,))
            for oid in old_ids:
                if oid not in used:
                    self.db.execute("DELETE FROM objects WHERE id=?", (oid,))
            for i, c in enumerate(res.clusters):
                f = memory.summarize(c)
                vals = (f["label"], f["first_seen"], f["last_seen"], f["yaw"], f["pitch"], f["weight"], f["n_obs"], f["spread_deg"], f["conf_best"],
                        json.dumps(f["last_box"]), f["last_frame_id"], json.dumps({"ext": f["ext"]}), json.dumps(f["track_ids"]),
                        f["fov_err_deg"], f["uncertainty_deg"])
                if i in assigned:
                    oid = assigned[i]
                    self.db.execute("UPDATE objects SET label=?, first_seen=?, last_seen=?, yaw=?, pitch=?, weight=?, n_obs=?, spread_deg=?, conf_best=?,"
                                    " last_box=?, last_frame_id=?, state=?, track_ids=?, fov_err_deg=?, uncertainty_deg=? WHERE id=?", vals + (oid,))
                else:
                    oid = self.db.execute("INSERT INTO objects (scan_id, label, first_seen, last_seen, yaw, pitch, weight, n_obs, spread_deg, conf_best,"
                                          " last_box, last_frame_id, state, track_ids, fov_err_deg, uncertainty_deg) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                          (scan_id,) + vals).lastrowid
                for m in sorted(c.members, key=lambda m: m.sort_key("capture")):
                    r = by_id[m.id]
                    self.db.execute("UPDATE detections SET object_id=? WHERE id=?", (oid, m.id))
                    self.db.execute("INSERT INTO observations (object_id, frame_id, ts, box, conf, yaw, pitch, poly) VALUES (?,?,?,?,?,?,?,?)",
                                    (oid, m.frame_id, m.ts, r["box"], m.conf, m.yaw, m.pitch, r["poly"]))
            self.db.commit()
            return len(res.clusters)

    def consolidate(self, scan_id: int) -> int:
        """Re-derive a scan's objects; returns how many objects that removed (0 when nothing changed)."""
        with self.lock:
            before = self.db.execute("SELECT COUNT(*) AS n FROM objects WHERE scan_id=?", (scan_id,)).fetchone()["n"]
            return max(0, before - self.rebuild(scan_id))

    def _record_decisions(self, scan_id: int, decisions: list[dict], newest_frame: int | None) -> None:
        """Log each memory decision the first time it is made (a rebuild repeats most of them)."""
        seen = self._logged.setdefault(scan_id, set())
        for d in decisions:
            sig = (d["action"], d.get("det"), d.get("label"), tuple(d.get("dets") or ()))
            if sig in seen:
                continue
            seen.add(sig)
            self.decision_log.append({"scan": scan_id, "frame": newest_frame, **d})
            log.info("scan %s %s", scan_id, " ".join(f"{k}={v}" for k, v in d.items()))

    def decisions(self, scan_id: int | None = None) -> list[dict]:
        return [d for d in self.decision_log if scan_id is None or d["scan"] == scan_id]

    # ------------------------------------------------------------------ queries

    def overlay_objects(self, scan_id: int | None = None) -> list[dict]:
        """What the phone needs to draw a scan's objects over the live camera: the anchor, how sure it is, when it was
        last seen, the footprint, and the latest sighting that kept a mask outline together with the pose it was taken at."""
        with self.lock:
            out = []
            for o in self.list_objects(scan_id):
                r = self.db.execute(
                    "SELECT ob.poly, f.yaw, f.pitch, f.roll, f.screen_angle, f.width, f.height, f.hfov_deg FROM observations ob"
                    " JOIN frames f ON f.id=ob.frame_id WHERE ob.object_id=? AND ob.poly IS NOT NULL ORDER BY ob.ts DESC, ob.id DESC LIMIT 1",
                    (o["id"],)).fetchone()
                sighting = None
                if r:
                    sighting = {"poly": json.loads(r["poly"]), "yaw": r["yaw"], "pitch": r["pitch"], "roll": A.image_roll(r["roll"], r["screen_angle"]),
                                "width": r["width"], "height": r["height"], "hfov": r["hfov_deg"]}
                out.append({"id": o["id"], "label": o["label"], "yaw": o["yaw"], "pitch": o["pitch"],
                            "uncertainty_deg": o["uncertainty_deg"] or o["spread_deg"], "spread_deg": o["spread_deg"], "fov_error_deg": o["fov_err_deg"],
                            "n_obs": o["n_obs"], "supporting_frames": o["n_obs"], "confidence": o["conf_best"], "last_seen": o["last_seen"],
                            "footprint": (o["state"] or {}).get("ext"), "mask": sighting})
            return out

    @staticmethod
    def _obj(r: sqlite3.Row) -> dict:
        d = dict(r)
        for k in ("last_box", "state", "attrs", "track_ids"):
            d[k] = json.loads(d[k]) if d[k] else None
        return d

    def get_object(self, object_id: int) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM objects WHERE id=?", (object_id,)).fetchone()
            return self._obj(r) if r else None

    def list_objects(self, scan_id: int | None = None, since: float | None = None) -> list[dict]:
        """Objects of a scan (default: the latest), optionally only those last seen at or after `since`."""
        with self.lock:
            scan_id = scan_id if scan_id is not None else self.latest_scan_id()
            if scan_id is None:
                return []
            q, args = "SELECT * FROM objects WHERE scan_id=?", [scan_id]
            if since is not None:
                q += " AND last_seen>=?"
                args.append(since)
            return [self._obj(r) for r in self.db.execute(q + " ORDER BY first_seen, id", args)]

    def find(self, label: str, alternates: tuple[str, ...] | list[str] = (), scan_id: int | None = None) -> list[dict]:
        """Objects matching `label` (then the alternates, each slightly less trusted), best first.
        Each result is an object dict plus `match_score` and `matched_on`."""
        with self.lock:
            scan_id = scan_id if scan_id is not None else self.latest_scan_id()
            if scan_id is None:
                return []
            objs = self.list_objects(scan_id)
            best: dict[int, dict] = {}
            for rank, q in enumerate([label, *alternates]):
                for o in objs:
                    s = label_match_score(q, o["label"]) * (0.9 ** rank)
                    if s > 0 and s > best.get(o["id"], {}).get("match_score", 0):
                        best[o["id"]] = {**o, "match_score": s, "matched_on": q}
            return sorted(best.values(), key=lambda o: (o["match_score"], o["conf_best"], o["last_seen"]), reverse=True)

    def neighbors(self, object_id: int, max_sep_deg: float = 30.0) -> list[dict]:
        """Other objects of the same scan within `max_sep_deg`, nearest first, with the relation of
        `object_id` to each ("left of", "right of", "above", "below", "next to"). Derived from angular
        anchors only: no "on" or "behind", which need masks or depth."""
        with self.lock:
            o = self.get_object(object_id)
            if o is None:
                return []
            out = []
            for other in self.list_objects(o["scan_id"]):
                if other["id"] == object_id:
                    continue
                sep = A.angular_distance(o["yaw"], o["pitch"], other["yaw"], other["pitch"])
                if sep > max_sep_deg:
                    continue
                dy, dp = A.wrap180(other["yaw"] - o["yaw"]), other["pitch"] - o["pitch"]
                if sep < 3:
                    rel = "next to"
                elif abs(dp) > abs(dy):
                    rel = "above" if dp < 0 else "below"
                else:
                    rel = "left of" if dy > 0 else "right of"
                out.append({"object_id": other["id"], "label": other["label"], "relation": rel, "sep_deg": sep})
            return sorted(out, key=lambda n: n["sep_deg"])

    def replay(self, object_id: int) -> dict | None:
        """Best sighting of an object (highest confidence, then newest): frame to show and where in it."""
        with self.lock:
            r = self.db.execute("SELECT o.frame_id, o.box, o.conf, o.ts, f.path, f.yaw AS cam_yaw, f.pitch AS cam_pitch,"
                                " f.width, f.height FROM observations o JOIN frames f ON f.id=o.frame_id"
                                " WHERE o.object_id=? ORDER BY o.conf DESC, o.ts DESC LIMIT 1", (object_id,)).fetchone()
            if not r:
                return None
            d = dict(r)
            d["box"] = json.loads(d["box"])
            return d

    def where_is(self, label: str, alternates: tuple[str, ...] | list[str] = (), scan_id: int | None = None) -> dict | None:
        """Where an object is, from the latest scan that contains it (or `scan_id`). None if never seen."""
        with self.lock:
            latest = self.latest_scan_id()
            if scan_id is not None:
                scans = [scan_id]
            else:
                scans = [r["id"] for r in self.db.execute("SELECT id FROM scans ORDER BY started_at DESC, id DESC")]
            for sid in scans:
                hits = self.find(label, alternates, sid)
                if hits:
                    o = hits[0]
                    break
            else:
                return None
            start = self.scan_start_pose(sid)
            offset = None
            if start:
                offset = {"dyaw": A.wrap180(o["yaw"] - start["yaw"]), "dpitch": o["pitch"]}
            nbrs = self.neighbors(o["id"])
            where = describe_offset(offset["dyaw"], offset["dpitch"]) if offset else "at an unknown direction"
            speech = f"The {o['label']} was last seen {where}"
            other = next((n for n in nbrs if label_match_score(n["label"], o["label"]) == 0), None)
            if other:
                speech += f", {other['relation']} the {other['label']}"
            elif nbrs:
                speech += f", {nbrs[0]['relation']} another {nbrs[0]['label']}"
            speech += "." if sid == latest else ", in an earlier scan; it was not in the latest scan."
            return {"object": o, "scan_id": sid, "is_latest_scan": sid == latest, "anchor": {"yaw": o["yaw"], "pitch": o["pitch"]},
                    "from_scan_start": offset, "neighbors": nbrs, "replay": self.replay(o["id"]), "speech": speech}
