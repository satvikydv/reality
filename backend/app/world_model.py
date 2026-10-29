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
import math
import re
import sqlite3
import threading
import time
from pathlib import Path

from collections import deque

from app import anchoring as A
from app import memory
from app.coverage import coverage

log = logging.getLogger("memory")

SCHEMA = """
-- A room ("space"): a user-named place. Its memory is its latest saved scan; older saved scans are kept for what-changed.
CREATE TABLE IF NOT EXISTS rooms (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at REAL NOT NULL, active INTEGER NOT NULL DEFAULT 0);
-- Every manual correction of what the memory believes, in order: merge, detach, delete, mark_not_object, unmark_not_object.
-- merge / detach / delete are stored as sets of detection ids, which is what a rebuild works from, so they are applied again
-- every time the scan's objects are recomputed.
CREATE TABLE IF NOT EXISTS corrections (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, room_id INTEGER, scan_id INTEGER, kind TEXT NOT NULL, payload TEXT NOT NULL, source TEXT);
CREATE INDEX IF NOT EXISTS corr_scan ON corrections(scan_id);
-- labels the user said are not objects in this room ("wall", "ceiling light"): never made into objects there
CREATE TABLE IF NOT EXISTS not_objects (
  room_id INTEGER NOT NULL, label TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY (room_id, label));
-- confirm / dismiss marks on the changes between two scans of one room
CREATE TABLE IF NOT EXISTS change_reviews (
  room_id INTEGER NOT NULL, scan_before INTEGER NOT NULL, scan_after INTEGER NOT NULL, change_key TEXT NOT NULL, state TEXT NOT NULL,
  PRIMARY KEY (room_id, scan_before, scan_after, change_key));
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
-- A note left in a room: a message anchored to a direction (and the label of the object under the reticle, when there was one).
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL, text TEXT NOT NULL, author TEXT, yaw REAL NOT NULL, pitch REAL NOT NULL,
  label TEXT, created_at REAL NOT NULL, done_at REAL);
CREATE INDEX IF NOT EXISTS notes_room ON notes(room_id);
-- Things a caregiver keeps an eye on, by label, and the room each one usually lives in. Not tied to one room.
CREATE TABLE IF NOT EXISTS watch_items (
  id INTEGER PRIMARY KEY, label TEXT NOT NULL UNIQUE, usual_room_id INTEGER, created_at REAL NOT NULL);
-- typical real-world size (longest side, cm) of each label, from the name pass; a running geometric mean over the frames that gave one
CREATE TABLE IF NOT EXISTS label_sizes (label TEXT PRIMARY KEY, size_cm REAL NOT NULL, n INTEGER NOT NULL, updated REAL NOT NULL);
-- how scan B's reference frame differs from scan A's (app.align), kept so a comparison does not redo the image matching
CREATE TABLE IF NOT EXISTS alignments (scan_a INTEGER NOT NULL, scan_b INTEGER NOT NULL, result TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY (scan_a, scan_b));
-- small key / value settings kept with the memory (the caregiver-sharing consent)
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

NOTE_MAX = 280
WATCH_MAX = 60

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


class RoomNameError(ValueError):
    """A room name that is empty, too long or already used (`duplicate`)."""

    def __init__(self, msg: str, duplicate: bool = False):
        super().__init__(msg)
        self.duplicate = duplicate


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
        for c, decl in (("room_id", "INTEGER"), ("status", "TEXT NOT NULL DEFAULT 'saved'"), ("baseline", "INTEGER NOT NULL DEFAULT 0")):
            if c not in cols("scans"):
                self.db.execute(f"ALTER TABLE scans ADD COLUMN {c} {decl}")
        self.db.execute("CREATE INDEX IF NOT EXISTS scans_room ON scans(room_id)")
        if self.db.execute("SELECT 1 FROM scans WHERE room_id IS NULL LIMIT 1").fetchone():  # scans from before rooms existed
            self.db.execute("UPDATE scans SET room_id=? WHERE room_id IS NULL", (self._unassigned_id(),))
        if "poly" not in cols("observations"):
            self.db.execute("ALTER TABLE observations ADD COLUMN poly TEXT")
        for c in ("fov_err_deg", "uncertainty_deg"):
            if c not in cols("objects"):
                self.db.execute(f"ALTER TABLE objects ADD COLUMN {c} REAL NOT NULL DEFAULT 0")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------ scans and frames

    def start_scan(self, name: str | None = None, source: str | None = None, ts: float | None = None,
                   room_id: int | None = None, status: str = "saved") -> int:
        """A new scan in `room_id` (default: the active room, else the "Unassigned" room). The phone's live scans start as
        "draft" and only become the room's memory when the user saves them."""
        with self.lock:
            if room_id is None:
                active = self.active_room()
                room_id = active["id"] if active else self._unassigned_id()
            elif self.get_room(room_id) is None:
                raise KeyError(f"no room {room_id}")
            cur = self.db.execute("INSERT INTO scans (name, source, started_at, room_id, status) VALUES (?,?,?,?,?)",
                                  (name, source, time.time() if ts is None else ts, room_id, status))
            self.db.commit()
            return cur.lastrowid

    def end_scan(self, scan_id: int, ts: float | None = None) -> None:
        with self.lock:
            self.db.execute("UPDATE scans SET ended_at=? WHERE id=?", (time.time() if ts is None else ts, scan_id))
            self.db.commit()

    def latest_scan_id(self, room_id: int | None = None) -> int | None:
        """The newest saved scan that stored frames: a room's memory. `room_id` None looks across rooms (only for code that
        has no room, such as old scripts); everything the phone and the API do passes a room."""
        with self.lock:
            q = "SELECT id FROM scans WHERE status='saved' AND EXISTS (SELECT 1 FROM frames f WHERE f.scan_id=scans.id)"
            args: list = []
            if room_id is not None:
                q += " AND room_id=?"
                args.append(room_id)
            r = self.db.execute(q + " ORDER BY started_at DESC, id DESC LIMIT 1", args).fetchone()
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
                "SELECT s.id, s.name, s.source, s.started_at, s.ended_at, s.room_id, s.status, s.baseline, r.name AS room_name,"
                " (SELECT COUNT(*) FROM frames f WHERE f.scan_id=s.id) AS n_frames,"
                " (SELECT COUNT(*) FROM objects o WHERE o.scan_id=s.id) AS n_objects"
                " FROM scans s LEFT JOIN rooms r ON r.id=s.room_id ORDER BY s.started_at DESC, s.id DESC")]

    def get_scan(self, scan_id: int) -> dict | None:
        return next((s for s in self.list_scans() if s["id"] == scan_id), None)

    def delete_scan(self, scan_id: int) -> bool:
        """Remove a scan with its frames, detections, objects and stored images. False when there is no such scan.
        Only the image files this scan recorded, and only inside `frames_dir`, are deleted (plus the scan's own folder when
        that is left empty); nothing is removed recursively."""
        with self.lock:
            if self.db.execute("SELECT 1 FROM scans WHERE id=?", (scan_id,)).fetchone() is None:
                return False
            paths = [r["path"] for r in self.db.execute("SELECT path FROM frames WHERE scan_id=? AND path IS NOT NULL", (scan_id,))]
            self.db.execute("DELETE FROM alignments WHERE scan_a=? OR scan_b=?", (scan_id, scan_id))
            self.db.execute("DELETE FROM observations WHERE object_id IN (SELECT id FROM objects WHERE scan_id=?)", (scan_id,))
            for table in ("detections", "objects", "frames"):
                self.db.execute(f"DELETE FROM {table} WHERE scan_id=?", (scan_id,))
            self.db.execute("DELETE FROM scans WHERE id=?", (scan_id,))
            self.db.execute("DELETE FROM change_reviews WHERE scan_before=? OR scan_after=?", (scan_id, scan_id))
            self.db.execute("DELETE FROM corrections WHERE scan_id=?", (scan_id,))
            self.db.commit()
            self._logged.pop(scan_id, None)
        root = self.frames_dir.resolve() if self.frames_dir else None
        if root is not None:
            for p in map(Path, paths):
                try:
                    if p.resolve().is_relative_to(root) and p.is_file():
                        p.unlink()
                except OSError:
                    log.warning("could not remove stored frame %s", p)
            try:
                (root / str(scan_id)).rmdir()  # only succeeds when empty
            except OSError:
                pass
        return True

    # ------------------------------------------------------------------ rooms

    UNASSIGNED = "Unassigned"

    @staticmethod
    def _room(r: sqlite3.Row | None) -> dict | None:
        return None if r is None else {"id": r["id"], "name": r["name"], "created_at": r["created_at"], "active": bool(r["active"])}

    def _unassigned_id(self) -> int:
        r = self.db.execute("SELECT id FROM rooms WHERE name=?", (self.UNASSIGNED,)).fetchone()
        if r:
            return r["id"]
        cur = self.db.execute("INSERT INTO rooms (name, created_at, active) VALUES (?,?,0)", (self.UNASSIGNED, time.time()))
        self.db.commit()
        return cur.lastrowid

    @staticmethod
    def _clean_name(name: str | None) -> str:
        name = " ".join((name or "").split())
        if not name:
            raise RoomNameError("a room needs a name")
        if len(name) > 60:
            raise RoomNameError("a room name can have at most 60 characters")
        return name

    def _name_taken(self, name: str, except_id: int | None = None) -> bool:
        return any(r["name"].casefold() == name.casefold() and r["id"] != except_id for r in self.db.execute("SELECT id, name FROM rooms"))

    def create_room(self, name: str, active: bool = False) -> dict:
        with self.lock:
            name = self._clean_name(name)
            if self._name_taken(name):
                raise RoomNameError(f"there is already a room called {name}", duplicate=True)
            cur = self.db.execute("INSERT INTO rooms (name, created_at, active) VALUES (?,?,0)", (name, time.time()))
            self.db.commit()
            if active:
                self.set_active(cur.lastrowid)
            return self.get_room(cur.lastrowid)

    def get_room(self, room_id: int) -> dict | None:
        with self.lock:
            return self._room(self.db.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone())

    def resolve_room(self, ref) -> dict | None:
        """A room from an id, a numeric string or a name (case does not matter); None when there is no such room."""
        with self.lock:
            if isinstance(ref, str) and ref.strip().isdigit():
                ref = int(ref)
            if isinstance(ref, int):
                return self.get_room(ref)
            if isinstance(ref, str):
                return next((self._room(r) for r in self.db.execute("SELECT * FROM rooms") if r["name"].casefold() == ref.strip().casefold()), None)
            return None

    def active_room(self) -> dict | None:
        with self.lock:
            return self._room(self.db.execute("SELECT * FROM rooms WHERE active=1 LIMIT 1").fetchone())

    def set_active(self, room_id: int | None) -> dict | None:
        """The user's choice of room: the default for Find, Explore and the Reality API. None clears it."""
        with self.lock:
            if room_id is not None and self.get_room(room_id) is None:
                raise KeyError(f"no room {room_id}")
            self.db.execute("UPDATE rooms SET active=0")
            if room_id is not None:
                self.db.execute("UPDATE rooms SET active=1 WHERE id=?", (room_id,))
            self.db.commit()
            return self.active_room()

    def rename_room(self, room_id: int, name: str) -> dict:
        with self.lock:
            if self.get_room(room_id) is None:
                raise KeyError(f"no room {room_id}")
            name = self._clean_name(name)
            if self._name_taken(name, room_id):
                raise RoomNameError(f"there is already a room called {name}", duplicate=True)
            self.db.execute("UPDATE rooms SET name=? WHERE id=?", (name, room_id))
            self.db.commit()
            return self.get_room(room_id)

    def delete_room(self, room_id: int) -> bool:
        """Erase a room with every scan, frame, photo, detection and object in it."""
        with self.lock:
            if self.get_room(room_id) is None:
                return False
            for s in self.db.execute("SELECT id FROM scans WHERE room_id=?", (room_id,)).fetchall():
                self.delete_scan(s["id"])
            self.db.execute("DELETE FROM change_reviews WHERE room_id=?", (room_id,))
            self.db.execute("DELETE FROM corrections WHERE room_id=?", (room_id,))
            self.db.execute("DELETE FROM not_objects WHERE room_id=?", (room_id,))
            self.db.execute("DELETE FROM notes WHERE room_id=?", (room_id,))
            self.db.execute("UPDATE watch_items SET usual_room_id=NULL WHERE usual_room_id=?", (room_id,))
            self.db.execute("DELETE FROM rooms WHERE id=?", (room_id,))
            self.db.commit()
            return True

    def delete_all(self) -> dict:
        """Every room and scan, with their files."""
        with self.lock:
            rooms = [r["id"] for r in self.db.execute("SELECT id FROM rooms")]
            n_scans = sum(1 for r in rooms for _ in self.db.execute("SELECT id FROM scans WHERE room_id=?", (r,)).fetchall())
            for r in rooms:
                self.delete_room(r)
            for s in self.db.execute("SELECT id FROM scans").fetchall():  # a scan with a room that no longer exists
                self.delete_scan(s["id"])
            for table in ("notes", "watch_items", "settings", "label_sizes", "alignments"):
                self.db.execute(f"DELETE FROM {table}")
            self.db.commit()
            return {"rooms": len(rooms), "scans": n_scans}

    # ------------------------------------------------------------------ notes, watch items, settings

    @staticmethod
    def _clean_text(text: str | None, limit: int, what: str) -> str:
        t = " ".join(re.sub(r"[\x00-\x1f\x7f]+", " ", text or "").split())
        if not t:
            raise ValueError(f"{what} is empty")
        if len(t) > limit:
            raise ValueError(f"{what} is longer than {limit} characters")
        return t

    def add_note(self, room_id: int, text: str, yaw: float, pitch: float, label: str | None = None, author: str | None = None,
                 ts: float | None = None) -> dict:
        """Leave a note in a room, anchored to a direction as seen from where the scans were made."""
        text = self._clean_text(text, NOTE_MAX, "the note")
        if not (-360 <= yaw <= 720 and -90 <= pitch <= 90):
            raise ValueError("the direction is not a yaw and a pitch in degrees")
        with self.lock:
            if self.get_room(room_id) is None:
                raise KeyError(f"no room {room_id}")
            cur = self.db.execute("INSERT INTO notes (room_id, text, author, yaw, pitch, label, created_at) VALUES (?,?,?,?,?,?,?)",
                                  (room_id, text, (author or "").strip()[:40] or None, yaw % 360.0, pitch,
                                   (label or "").strip()[:60] or None, time.time() if ts is None else ts))
            self.db.commit()
            return self.get_note(cur.lastrowid)

    @staticmethod
    def _note(r: sqlite3.Row | None) -> dict | None:
        return dict(r) if r else None

    def get_note(self, note_id: int) -> dict | None:
        with self.lock:
            return self._note(self.db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone())

    def list_notes(self, room_id: int, include_done: bool = False) -> list[dict]:
        """A room's notes, newest first. Notes belong to one room and never appear in another."""
        with self.lock:
            q = "SELECT * FROM notes WHERE room_id=?" + ("" if include_done else " AND done_at IS NULL")
            return [dict(r) for r in self.db.execute(q + " ORDER BY created_at DESC, id DESC", (room_id,))]

    def set_note_done(self, note_id: int, done: bool, ts: float | None = None) -> dict | None:
        with self.lock:
            if self.get_note(note_id) is None:
                return None
            self.db.execute("UPDATE notes SET done_at=? WHERE id=?", ((time.time() if ts is None else ts) if done else None, note_id))
            self.db.commit()
            return self.get_note(note_id)

    def delete_note(self, note_id: int) -> bool:
        with self.lock:
            n = self.db.execute("DELETE FROM notes WHERE id=?", (note_id,)).rowcount
            self.db.commit()
            return n > 0

    def record_label_size(self, label: str, size_cm: float) -> None:
        """Remember a size Spark gave for a label (a running geometric mean, so one wild answer does not dominate)."""
        if not label or not size_cm or not (0.5 <= size_cm <= 400):
            return
        with self.lock:
            r = self.db.execute("SELECT size_cm, n FROM label_sizes WHERE label=?", (label,)).fetchone()
            if r is None:
                self.db.execute("INSERT INTO label_sizes (label, size_cm, n, updated) VALUES (?,?,?,?)", (label, float(size_cm), 1, time.time()))
            else:
                n = r["n"]
                mean = math.exp((n * math.log(r["size_cm"]) + math.log(size_cm)) / (n + 1))
                self.db.execute("UPDATE label_sizes SET size_cm=?, n=?, updated=? WHERE label=?", (mean, n + 1, time.time(), label))
            self.db.commit()

    def get_label_size(self, label: str) -> float | None:
        with self.lock:
            r = self.db.execute("SELECT size_cm FROM label_sizes WHERE label=?", (label,)).fetchone()
            return float(r["size_cm"]) if r else None

    def get_alignment(self, scan_a: int, scan_b: int) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT result FROM alignments WHERE scan_a=? AND scan_b=?", (scan_a, scan_b)).fetchone()
            return json.loads(r["result"]) if r else None

    def set_alignment(self, scan_a: int, scan_b: int, result: dict) -> None:
        with self.lock:
            self.db.execute("INSERT INTO alignments (scan_a, scan_b, result, ts) VALUES (?,?,?,?) ON CONFLICT(scan_a, scan_b) DO UPDATE SET result=excluded.result, ts=excluded.ts",
                            (scan_a, scan_b, json.dumps(result), time.time()))
            self.db.commit()

    def add_watch(self, label: str, usual_room_id: int | None = None) -> dict:
        label = normalize_label(self._clean_text(label, WATCH_MAX, "the name"))
        if not label:
            raise ValueError("the name is empty")
        with self.lock:
            if usual_room_id is not None and self.get_room(usual_room_id) is None:
                raise KeyError(f"no room {usual_room_id}")
            if self.db.execute("SELECT 1 FROM watch_items WHERE label=?", (label,)).fetchone():
                raise ValueError(f"“{label}” is already on the list")
            cur = self.db.execute("INSERT INTO watch_items (label, usual_room_id, created_at) VALUES (?,?,?)", (label, usual_room_id, time.time()))
            self.db.commit()
            return dict(self.db.execute("SELECT * FROM watch_items WHERE id=?", (cur.lastrowid,)).fetchone())

    def list_watches(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM watch_items ORDER BY created_at, id")]

    def delete_watch(self, watch_id: int) -> bool:
        with self.lock:
            n = self.db.execute("DELETE FROM watch_items WHERE id=?", (watch_id,)).rowcount
            self.db.commit()
            return n > 0

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.lock:
            r = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return r["value"] if r else default

    def set_setting(self, key: str, value: str | None) -> None:
        with self.lock:
            if value is None:
                self.db.execute("DELETE FROM settings WHERE key=?", (key,))
            else:
                self.db.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
            self.db.commit()

    def scan_room_id(self, scan_id: int) -> int | None:
        with self.lock:
            r = self.db.execute("SELECT room_id FROM scans WHERE id=?", (scan_id,)).fetchone()
            return r["room_id"] if r else None

    def room_stats(self, room: dict) -> dict:
        """What a room card shows: the memory's object count and coverage, and when it was last scanned."""
        with self.lock:
            sid = self.latest_scan_id(room["id"])
            n = self.db.execute("SELECT COUNT(*) AS n FROM scans WHERE room_id=? AND status='saved'", (room["id"],)).fetchone()["n"]
            drafts = self.db.execute("SELECT COUNT(*) AS n FROM scans WHERE room_id=? AND status='draft'", (room["id"],)).fetchone()["n"]
            out = {**room, "n_scans": n, "n_drafts": drafts, "scan_id": sid, "n_objects": 0, "coverage_pct": 0, "last_scanned": None}
            if sid is not None:
                s = self.get_scan(sid)
                out.update(n_objects=s["n_objects"], last_scanned=s["ended_at"] or s["started_at"],
                           coverage_pct=round(coverage(self.list_frames(sid))["fraction"] * 100))
            return out

    def list_rooms(self) -> list[dict]:
        """Every room with its stats; most recently scanned first, rooms never scanned last."""
        with self.lock:
            rooms = [self.room_stats(self._room(r)) for r in self.db.execute("SELECT * FROM rooms")]
        return sorted(rooms, key=lambda r: (r["last_scanned"] is None, -(r["last_scanned"] or 0), r["name"].casefold()))

    def room_scans(self, room_id: int) -> list[dict]:
        """A room's scan history, newest first: saved scans, an unsaved draft, and which one is the comparison baseline."""
        return [s for s in self.list_scans() if s["room_id"] == room_id and s["n_frames"] > 0]

    def save_scan(self, scan_id: int) -> bool:
        """Make a draft scan its room's memory. False if there is no such draft or it stored nothing."""
        with self.lock:
            s = self.get_scan(scan_id)
            if not s or s["status"] != "draft" or s["n_frames"] == 0:
                return False
            self.db.execute("UPDATE scans SET status='saved' WHERE id=?", (scan_id,))
            self.db.commit()
            return True

    def set_baseline(self, room_id: int, scan_id: int | None) -> None:
        """Choose which saved scan What changed compares against (None: the one before the latest)."""
        with self.lock:
            if scan_id is not None:
                s = self.get_scan(scan_id)
                if not s or s["room_id"] != room_id or s["status"] != "saved":
                    raise ValueError("the baseline must be a saved scan of this room")
            self.db.execute("UPDATE scans SET baseline=0 WHERE room_id=?", (room_id,))
            if scan_id is not None:
                self.db.execute("UPDATE scans SET baseline=1 WHERE id=?", (scan_id,))
            self.db.commit()

    def default_compare(self, room_id: int) -> tuple[int, int] | None:
        """(before, after) for What changed: the latest saved scan against the baseline, else against the one before it."""
        saved = [s for s in self.room_scans(room_id) if s["status"] == "saved"]
        if len(saved) < 2:
            return None
        after = saved[0]
        base = next((s for s in saved[1:] if s["baseline"]), saved[1])
        return base["id"], after["id"]

    def set_review(self, room_id: int, before: int, after: int, key: str, state: str | None) -> None:
        with self.lock:
            if state is None:
                self.db.execute("DELETE FROM change_reviews WHERE room_id=? AND scan_before=? AND scan_after=? AND change_key=?", (room_id, before, after, key))
            else:
                self.db.execute("INSERT OR REPLACE INTO change_reviews VALUES (?,?,?,?,?)", (room_id, before, after, key, state))
            self.db.commit()

    def reviews(self, room_id: int, before: int, after: int) -> dict[str, str]:
        with self.lock:
            return {r["change_key"]: r["state"] for r in self.db.execute(
                "SELECT change_key, state FROM change_reviews WHERE room_id=? AND scan_before=? AND scan_after=?", (room_id, before, after))}

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

    def _load_dets(self, scan_id: int):
        """({detection id: row}, [memory.Det]) for a scan, from the detections table."""
        self._ensure_detections(scan_id)
        rows = self.db.execute(
            "SELECT d.*, f.ts AS fts, f.yaw AS cam_yaw, f.pitch AS cam_pitch FROM detections d JOIN frames f ON f.id=d.frame_id"
            " WHERE d.scan_id=? ORDER BY d.id", (scan_id,)).fetchall()
        dets = [memory.Det(id=r["id"], frame_id=r["frame_id"], ts=r["fts"], cam_yaw=r["cam_yaw"], cam_pitch=r["cam_pitch"], label=r["label"],
                           box=tuple(json.loads(r["box"])), conf=r["conf"], yaw=r["yaw"], pitch=r["pitch"],
                           ext=tuple(json.loads(r["ext"])) if r["ext"] else None, fragment=bool(r["fragment"]),
                           hist=json.loads(r["hist"]) if r["hist"] else None, fov_err=r["fov_err"], track_id=r["track_id"],
                           prev_object=r["object_id"], idx=r["idx"]) for r in rows]
        return {r["id"]: r for r in rows}, dets

    def _constraints(self, scan_id: int, dets: list) -> memory.Constraints:
        """The user's corrections for a scan, replayed in the order they were made: a later correction wins over an earlier one."""
        exclude: set[int] = set()
        separate: set[int] = set()
        groups: list[set[int]] = []
        for r in self.db.execute("SELECT kind, payload FROM corrections WHERE scan_id=? AND kind IN ('merge','detach','delete') ORDER BY id", (scan_id,)):
            p = json.loads(r["payload"])
            if r["kind"] == "merge":
                g = set(p["dets"])
                separate -= g  # merging an object that had been detached puts it back
                groups.append(g)
            elif r["kind"] == "detach":
                separate.add(p["det"])
                groups = [g - {p["det"]} for g in groups]
            else:
                exclude |= set(p["dets"])
        room = self.scan_room_id(scan_id)
        hidden = {r["label"] for r in self.db.execute("SELECT label FROM not_objects WHERE room_id=?", (room,))} if room is not None else set()
        if hidden:
            exclude |= {d.id for d in dets if d.label in hidden}
        return memory.Constraints(frozenset(exclude), frozenset(separate - exclude), tuple(frozenset(g - exclude) for g in groups if len(g - exclude) > 1))

    def explain(self, scan_id: int) -> list[dict]:
        """Every decision the memory takes for this scan, recomputed from its detections and corrections (it does not depend on what
        the server happened to log since it started, and writes nothing)."""
        with self.lock:
            _, dets = self._load_dets(scan_id)
            return memory.cluster(dets, self.memory_cfg, self._constraints(scan_id, dets)).decisions

    def rebuild(self, scan_id: int, newest_frame: int | None = None) -> int:
        """Recompute a scan's objects from all its detections (see app/memory.py). The result depends only on the
        detections and their capture timestamps, not on the order they were stored in. Object ids are kept where an
        object still contains the same detections. Returns how many objects the scan has."""
        with self.lock:
            by_id, dets = self._load_dets(scan_id)
            res = memory.cluster(dets, self.memory_cfg, self._constraints(scan_id, dets))
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

    # ------------------------------------------------------------------ manual corrections

    def record_correction(self, kind: str, scan_id: int | None, payload: dict, source: str = "api") -> dict:
        """Write one correction to the log (every correction is kept, in order, with who made it)."""
        with self.lock:
            room = self.scan_room_id(scan_id) if scan_id is not None else payload.get("room_id")
            ts = time.time()
            cur = self.db.execute("INSERT INTO corrections (ts, room_id, scan_id, kind, payload, source) VALUES (?,?,?,?,?,?)",
                                  (ts, room, scan_id, kind, json.dumps(payload), source))
            self.db.commit()
            row = {"id": cur.lastrowid, "ts": ts, "room_id": room, "scan_id": scan_id, "kind": kind, "payload": payload, "source": source}
            log.info("correction %s", row)
            return row

    def list_corrections(self, scan_id: int | None = None, room_id: int | None = None, limit: int = 200) -> list[dict]:
        with self.lock:
            q, args = "SELECT * FROM corrections WHERE 1=1", []
            if scan_id is not None:
                q += " AND scan_id=?"
                args.append(scan_id)
            if room_id is not None:
                q += " AND room_id=?"
                args.append(room_id)
            return [{**dict(r), "payload": json.loads(r["payload"])} for r in self.db.execute(q + " ORDER BY id DESC LIMIT ?", args + [limit])]

    def _object_dets(self, object_id: int) -> list[int]:
        return [r["id"] for r in self.db.execute("SELECT id FROM detections WHERE object_id=? ORDER BY id", (object_id,))]

    def merge_objects(self, object_ids: list[int], source: str = "api") -> dict:
        """The user says these instances are one object. They must be in the same scan and have the same label. Returns the
        resulting object. Raises KeyError (no such object) or ValueError (cannot be merged)."""
        with self.lock:
            objs = []
            for oid in dict.fromkeys(object_ids):
                o = self.get_object(oid)
                if o is None:
                    raise KeyError(f"no object {oid}")
                objs.append(o)
            if len(objs) < 2:
                raise ValueError("merging needs at least two different objects")
            if len({o["scan_id"] for o in objs}) > 1:
                raise ValueError("those objects are from different scans; only instances in one scan can be merged")
            if len({o["label"] for o in objs}) > 1:
                raise ValueError("those objects have different labels: " + ", ".join(sorted({o["label"] for o in objs})))
            scan_id = objs[0]["scan_id"]
            dets = sorted({d for o in objs for d in self._object_dets(o["id"])})
            self.record_correction("merge", scan_id, {"objects": [o["id"] for o in objs], "label": objs[0]["label"], "dets": dets}, source)
            self.rebuild(scan_id)
            oid = self.db.execute("SELECT object_id FROM detections WHERE id=?", (dets[0],)).fetchone()["object_id"]
            return self.get_object(oid)

    def detach_sighting(self, detection_id: int, source: str = "api") -> dict:
        """Take one sighting out of its object: it becomes an object of its own. Returns that new object."""
        with self.lock:
            d = self.db.execute("SELECT id, scan_id, label, object_id FROM detections WHERE id=?", (detection_id,)).fetchone()
            if d is None:
                raise KeyError(f"no sighting {detection_id}")
            if d["object_id"] is None:
                raise ValueError("that sighting is not part of an object")
            if len(self._object_dets(d["object_id"])) < 2:
                raise ValueError("that sighting is already an object of its own")
            self.record_correction("detach", d["scan_id"], {"det": d["id"], "from_object": d["object_id"], "label": d["label"]}, source)
            self.rebuild(d["scan_id"])
            return self.get_object(self.db.execute("SELECT object_id FROM detections WHERE id=?", (detection_id,)).fetchone()["object_id"])

    def delete_object(self, object_id: int, source: str = "api") -> dict:
        """The user says this is not a real object: all its sightings are ignored from now on. Returns what was deleted."""
        with self.lock:
            o = self.get_object(object_id)
            if o is None:
                raise KeyError(f"no object {object_id}")
            dets = self._object_dets(object_id)
            self.record_correction("delete", o["scan_id"], {"object": object_id, "label": o["label"], "dets": dets}, source)
            self.rebuild(o["scan_id"])
            return {"id": object_id, "label": o["label"], "scan_id": o["scan_id"], "sightings": len(dets)}

    # -- "not an object" labels, per room

    def not_object_labels(self, room_id: int) -> list[str]:
        with self.lock:
            return [r["label"] for r in self.db.execute("SELECT label FROM not_objects WHERE room_id=? ORDER BY label", (room_id,))]

    def _rebuild_room(self, room_id: int) -> None:
        for s in self.db.execute("SELECT id FROM scans WHERE room_id=?", (room_id,)).fetchall():
            self.rebuild(s["id"])

    def mark_not_object(self, room_id: int, label: str, source: str = "api") -> dict:
        """Objects with this label are not objects in this room: they disappear from every scan of the room, now and in the future."""
        with self.lock:
            if self.get_room(room_id) is None:
                raise KeyError(f"no room {room_id}")
            label = normalize_label(label)
            if not label:
                raise ValueError("a label is needed")
            n = self.db.execute("SELECT COUNT(*) AS n FROM objects o JOIN scans s ON s.id=o.scan_id WHERE s.room_id=? AND o.label=?", (room_id, label)).fetchone()["n"]
            self.db.execute("INSERT OR REPLACE INTO not_objects VALUES (?,?,?)", (room_id, label, time.time()))
            self.db.commit()
            self.record_correction("mark_not_object", None, {"room_id": room_id, "label": label, "objects_hidden": n}, source)
            self._rebuild_room(room_id)
            return {"room_id": room_id, "label": label, "objects_hidden": n}

    def unmark_not_object(self, room_id: int, label: str, source: str = "api") -> bool:
        with self.lock:
            label = normalize_label(label)
            cur = self.db.execute("DELETE FROM not_objects WHERE room_id=? AND label=?", (room_id, label))
            self.db.commit()
            if not cur.rowcount:
                return False
            self.record_correction("unmark_not_object", None, {"room_id": room_id, "label": label}, source)
            self._rebuild_room(room_id)
            return True

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
        """Where an object is, in one scan: `scan_id`, which callers take from a room's memory (its latest saved scan).
        Older scans are not searched. None if it is not in that scan."""
        with self.lock:
            sid = scan_id if scan_id is not None else self.latest_scan_id()
            if sid is None:
                return None
            hits = self.find(label, alternates, sid)
            if not hits:
                return None
            o = hits[0]
            latest = self.latest_scan_id(self.scan_room_id(sid))
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
            speech += "."
            return {"object": o, "scan_id": sid, "is_latest_scan": sid == latest, "anchor": {"yaw": o["yaw"], "pitch": o["pitch"]},
                    "from_scan_start": offset, "neighbors": nbrs, "replay": self.replay(o["id"]), "speech": speech}
