"""World Model: scans, frames with orientation, objects anchored to (yaw, pitch), replay (PRD F2, F10, F11).

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
import re
import sqlite3
import threading
import time
from pathlib import Path

from app import anchoring as A

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
  track_ids TEXT NOT NULL DEFAULT '[]');
CREATE INDEX IF NOT EXISTS objects_scan_label ON objects(scan_id, label);
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY, object_id INTEGER NOT NULL REFERENCES objects(id), frame_id INTEGER NOT NULL REFERENCES frames(id),
  ts REAL NOT NULL, box TEXT NOT NULL, conf REAL NOT NULL, yaw REAL NOT NULL, pitch REAL NOT NULL);
CREATE INDEX IF NOT EXISTS obs_object ON observations(object_id);
"""

_ARTICLES = {"a", "an", "the", "my", "your"}


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
                 merge_deg: float = 8.0, default_hfov_deg: float = A.DEFAULT_HFOV_DEG):
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.frames_dir = Path(frames_dir) if frames_dir else None
        self.merge_deg = merge_deg
        self.default_hfov_deg = default_hfov_deg
        self.lock = threading.RLock()

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

    def list_frames(self, scan_id: int) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM frames WHERE scan_id=? ORDER BY ts, id", (scan_id,))]

    def scan_start_pose(self, scan_id: int) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT yaw, pitch FROM frames WHERE scan_id=? ORDER BY ts, id LIMIT 1", (scan_id,)).fetchone()
            return dict(r) if r else None

    # ------------------------------------------------------------------ observing

    def observe(self, frame_id: int, detections: list[dict]) -> list[dict]:
        """Add detections {label, box:[x1,y1,x2,y2], conf?, track_id?} seen in a stored frame.
        Returns one dict per detection: object_id, new, yaw, pitch."""
        with self.lock:
            fr = self.get_frame(frame_id)
            if fr is None:
                raise KeyError(f"unknown frame {frame_id}")
            roll = A.image_roll(fr["roll"], fr["screen_angle"])
            claimed: set[int] = set()
            out = []
            for d in detections:
                label = normalize_label(d["label"])
                if not label:
                    continue
                box = [float(x) for x in d["box"]]
                conf = float(d.get("conf", 1.0))
                yaw, pitch = A.box_anchor(tuple(box), fr["width"], fr["height"], fr["yaw"], fr["pitch"], roll, fr["hfov_deg"])
                oid, new = self._match_or_create(fr, label, box, conf, yaw, pitch, d.get("track_id"), claimed)
                claimed.add(oid)
                self.db.execute("INSERT INTO observations (object_id, frame_id, ts, box, conf, yaw, pitch) VALUES (?,?,?,?,?,?,?)",
                                (oid, frame_id, fr["ts"], json.dumps(box), conf, yaw, pitch))
                out.append({"object_id": oid, "new": new, "yaw": yaw, "pitch": pitch})
            self.db.commit()
            return out

    def _match_or_create(self, fr, label, box, conf, yaw, pitch, track_id, claimed):
        best, best_sep = None, self.merge_deg
        for r in self.db.execute("SELECT * FROM objects WHERE scan_id=? AND label=?", (fr["scan_id"], label)):
            if r["id"] in claimed:
                continue
            sep = A.angular_distance(yaw, pitch, r["yaw"], r["pitch"])
            if sep < best_sep:
                best, best_sep = r, sep
        tracks = [track_id] if track_id is not None else []
        if best is None:
            cur = self.db.execute(
                "INSERT INTO objects (scan_id, label, first_seen, last_seen, yaw, pitch, weight, n_obs, spread_deg, conf_best,"
                " last_box, last_frame_id, track_ids) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fr["scan_id"], label, fr["ts"], fr["ts"], yaw, pitch, conf, 1, 0.0, conf, json.dumps(box), fr["id"], json.dumps(tracks)))
            return cur.lastrowid, True
        new_yaw, new_pitch = A.mean_direction([(best["yaw"], best["pitch"], best["weight"]), (yaw, pitch, conf)])
        known = json.loads(best["track_ids"])
        if track_id is not None and track_id not in known:
            known.append(track_id)
        self.db.execute(
            "UPDATE objects SET yaw=?, pitch=?, weight=weight+?, n_obs=n_obs+1, spread_deg=MAX(spread_deg, ?), conf_best=MAX(conf_best, ?),"
            " last_seen=MAX(last_seen, ?), last_box=?, last_frame_id=?, track_ids=? WHERE id=?",
            (new_yaw, new_pitch, conf, best_sep, conf, fr["ts"], json.dumps(box), fr["id"], json.dumps(known), best["id"]))
        return best["id"], False

    # ------------------------------------------------------------------ queries

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
