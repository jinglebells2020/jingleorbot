#!/usr/bin/env python3
"""facewatch store — SQLite persistence + presence-session state machine.

Camera-free on purpose: the engine feeds it observations (identity, score,
embedding), it decides whether that refreshes an open session or opens a new
one, and the sweeper closes sessions that have gone quiet. Times are unix
floats throughout.
"""

import sqlite3
import threading
import time
from collections import deque

import numpy as np

ROLLING_EMBS = 5  # embeddings kept per open unknown session for chain matching

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id          INTEGER PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS embeddings (
    id          INTEGER PRIMARY KEY,
    person_id   INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    vec         BLOB NOT NULL,
    source      TEXT,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY,
    person_id    INTEGER REFERENCES people(id) ON DELETE SET NULL,
    label        TEXT NOT NULL,
    started_at   REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    ended_at     REAL,
    best_score   REAL,
    frame_path   TEXT,
    crop_path    TEXT,
    emb          BLOB
);
CREATE INDEX IF NOT EXISTS idx_sessions_open ON sessions(ended_at) WHERE ended_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,      -- 'weapon' | 'commotion'
    label       TEXT NOT NULL,      -- e.g. 'knife 0.62'
    score       REAL,
    ts          REAL NOT NULL,
    frame_path  TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);
"""


def vec_to_blob(vec):
    return np.asarray(vec, dtype=np.float32).tobytes()


def blob_to_vec(blob):
    return np.frombuffer(blob, dtype=np.float32)


def cosine(a, b):
    denom = float(np.linalg.norm(a)) * float(np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b)) / denom


class Observation:
    """One recognized (or unrecognized) face in a frame."""

    def __init__(self, person_id, name, score, emb):
        self.person_id = person_id  # None => unknown
        self.name = name            # None => unknown
        self.score = score          # cosine vs best enrolled emb (0.0 for unknown)
        self.emb = emb              # np.float32[128]


class SessionUpdate:
    """What the engine should do about one observation."""

    def __init__(self, session_id, label, is_new, wants_snap):
        self.session_id = session_id
        self.label = label
        self.is_new = is_new        # session just opened
        self.wants_snap = wants_snap  # save/refresh snapshot files for it


class Store:
    def __init__(self, db_path, unknown_match_threshold=0.363, absence_close_s=60.0):
        self.lock = threading.RLock()
        self.unknown_match_threshold = unknown_match_threshold
        self.absence_close_s = absence_close_s
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        # In-memory mirror of open sessions:
        # id -> {person_id, label, last_seen, best_score, emb (unknowns only)}
        self._open = {}
        self._close_stale_from_previous_run()

    # ---------------------------------------------------------------- people

    def add_person(self, name, now=None):
        now = time.time() if now is None else now
        with self.lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO people(name, created_at) VALUES (?, ?)",
                (name, now),
            )
            self.conn.commit()
            row = self.conn.execute(
                "SELECT id FROM people WHERE name = ?", (name,)
            ).fetchone()
            return row["id"]

    def add_embedding(self, person_id, vec, source, now=None):
        now = time.time() if now is None else now
        with self.lock:
            self.conn.execute(
                "INSERT INTO embeddings(person_id, vec, source, created_at) "
                "VALUES (?, ?, ?, ?)",
                (person_id, vec_to_blob(vec), source, now),
            )
            self.conn.commit()

    def load_embeddings(self):
        """[(person_id, name, np.float32[128]), ...] for the matcher."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT e.person_id, p.name, e.vec FROM embeddings e "
                "JOIN people p ON p.id = e.person_id"
            ).fetchall()
        return [(r["person_id"], r["name"], blob_to_vec(r["vec"])) for r in rows]

    def forget(self, name):
        """Remove a person and their embeddings. Their sessions keep the label."""
        with self.lock:
            row = self.conn.execute(
                "SELECT id FROM people WHERE name = ?", (name,)
            ).fetchone()
            if row is None:
                return False
            self.conn.execute("DELETE FROM people WHERE id = ?", (row["id"],))
            self.conn.commit()
            for sid, s in self._open.items():
                if s["person_id"] == row["id"]:
                    s["person_id"] = None
            return True

    def people_summary(self):
        with self.lock:
            rows = self.conn.execute(
                "SELECT p.id, p.name, p.created_at, "
                "  (SELECT COUNT(*) FROM embeddings e WHERE e.person_id = p.id) AS shots, "
                "  (SELECT MAX(last_seen_at) FROM sessions s WHERE s.person_id = p.id) AS last_seen, "
                "  (SELECT COUNT(*) FROM sessions s WHERE s.person_id = p.id) AS session_count "
                "FROM people p ORDER BY p.name"
            ).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------- sessions

    def _close_stale_from_previous_run(self):
        with self.lock:
            self.conn.execute(
                "UPDATE sessions SET ended_at = last_seen_at WHERE ended_at IS NULL"
            )
            self.conn.commit()

    def observe(self, obs, now=None):
        """Feed one face observation; returns a SessionUpdate."""
        now = time.time() if now is None else now
        with self.lock:
            if obs.person_id is not None:
                return self._observe_known(obs, now)
            return self._observe_unknown(obs, now)

    def _observe_known(self, obs, now):
        for sid, s in self._open.items():
            if s["person_id"] == obs.person_id:
                s["last_seen"] = now
                better = obs.score > (s["best_score"] or 0.0)
                if better:
                    s["best_score"] = obs.score
                self.conn.execute(
                    "UPDATE sessions SET last_seen_at = ?, best_score = ? WHERE id = ?",
                    (now, s["best_score"], sid),
                )
                self.conn.commit()
                return SessionUpdate(sid, s["label"], False, better)
        cur = self.conn.execute(
            "INSERT INTO sessions(person_id, label, started_at, last_seen_at, best_score) "
            "VALUES (?, ?, ?, ?, ?)",
            (obs.person_id, obs.name, now, now, obs.score),
        )
        sid = cur.lastrowid
        self.conn.commit()
        self._open[sid] = {
            "person_id": obs.person_id, "label": obs.name,
            "last_seen": now, "best_score": obs.score, "embs": None,
        }
        return SessionUpdate(sid, obs.name, True, True)

    def _observe_unknown(self, obs, now):
        # Presence is a chain: this frame resembles the previous frames of a
        # lingering stranger far more than their first glimpse. Match against
        # a rolling set of each open unknown session's recent embeddings.
        best_sid, best_cos = None, self.unknown_match_threshold
        for sid, s in self._open.items():
            if s["person_id"] is not None or not s["embs"]:
                continue
            c = max(cosine(obs.emb, e) for e in s["embs"])
            if c >= best_cos:
                best_sid, best_cos = sid, c
        if best_sid is not None:
            s = self._open[best_sid]
            s["last_seen"] = now
            s["embs"].append(obs.emb)
            self.conn.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE id = ?", (now, best_sid)
            )
            self.conn.commit()
            return SessionUpdate(best_sid, s["label"], False, False)
        cur = self.conn.execute(
            "INSERT INTO sessions(person_id, label, started_at, last_seen_at, "
            "best_score, emb) VALUES (NULL, '', ?, ?, 0.0, ?)",
            (now, now, vec_to_blob(obs.emb)),
        )
        sid = cur.lastrowid
        label = "unknown-%d" % sid
        self.conn.execute("UPDATE sessions SET label = ? WHERE id = ?", (label, sid))
        self.conn.commit()
        self._open[sid] = {
            "person_id": None, "label": label, "last_seen": now,
            "best_score": 0.0, "embs": deque([obs.emb], maxlen=ROLLING_EMBS),
        }
        return SessionUpdate(sid, label, True, True)

    def set_snaps(self, session_id, frame_path, crop_path):
        with self.lock:
            self.conn.execute(
                "UPDATE sessions SET frame_path = ?, crop_path = ? WHERE id = ?",
                (frame_path, crop_path, session_id),
            )
            self.conn.commit()

    def sweep(self, now=None):
        """Close open sessions not seen for absence_close_s. Returns closed ids."""
        now = time.time() if now is None else now
        closed = []
        with self.lock:
            for sid in list(self._open):
                s = self._open[sid]
                if now - s["last_seen"] > self.absence_close_s:
                    self.conn.execute(
                        "UPDATE sessions SET ended_at = ? WHERE id = ?",
                        (s["last_seen"], sid),
                    )
                    del self._open[sid]
                    closed.append(sid)
            if closed:
                self.conn.commit()
        return closed

    def promote(self, session_id, name, now=None):
        """Turn an unknown session into (or into more evidence for) a person."""
        now = time.time() if now is None else now
        with self.lock:
            row = self.conn.execute(
                "SELECT person_id, emb FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise ValueError("no such session: %s" % session_id)
            if row["person_id"] is not None:
                raise ValueError("session %s is not unknown" % session_id)
            if row["emb"] is None:
                raise ValueError("session %s has no stored embedding" % session_id)
            pid = self.add_person(name, now)
            # An open session carries recent embeddings too — enroll them all.
            live = self._open.get(session_id)
            embs = list(live["embs"]) if live and live["embs"] else \
                [blob_to_vec(row["emb"])]
            for i, e in enumerate(embs):
                self.add_embedding(pid, e, "promote:s%d:%d" % (session_id, i), now)
            self.conn.execute(
                "UPDATE sessions SET person_id = ?, label = ? WHERE id = ?",
                (pid, name, session_id),
            )
            self.conn.commit()
            if session_id in self._open:
                self._open[session_id]["person_id"] = pid
                self._open[session_id]["label"] = name
            return pid

    def open_sessions(self):
        with self.lock:
            if not self._open:
                return []
            ids = ",".join(str(i) for i in self._open)
            rows = self.conn.execute(
                "SELECT id, person_id, label, started_at, last_seen_at, best_score, "
                "frame_path, crop_path FROM sessions WHERE id IN (%s) "
                "ORDER BY started_at DESC" % ids
            ).fetchall()
        return [dict(r) for r in rows]

    def recent_sessions(self, limit=50):
        with self.lock:
            rows = self.conn.execute(
                "SELECT id, person_id, label, started_at, last_seen_at, ended_at, "
                "best_score, frame_path, crop_path FROM sessions "
                "ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- alerts

    def add_alert(self, kind, label, score, frame_path, now=None):
        now = time.time() if now is None else now
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO alerts(kind, label, score, ts, frame_path) "
                "VALUES (?, ?, ?, ?, ?)", (kind, label, score, now, frame_path)
            )
            self.conn.commit()
            return cur.lastrowid

    def last_alert_ts(self, kind):
        with self.lock:
            row = self.conn.execute(
                "SELECT MAX(ts) AS t FROM alerts WHERE kind = ?", (kind,)
            ).fetchone()
        return row["t"] or 0.0

    def recent_alerts(self, limit=20):
        with self.lock:
            rows = self.conn.execute(
                "SELECT id, kind, label, score, ts, frame_path FROM alerts "
                "ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def prune_snaps(self, retention_days, snaps_dir, now=None):
        """Delete snapshot files for sessions older than retention_days."""
        now = time.time() if now is None else now
        cutoff = now - retention_days * 86400
        removed = 0
        with self.lock:
            rows = self.conn.execute(
                "SELECT id, frame_path, crop_path FROM sessions "
                "WHERE ended_at IS NOT NULL AND ended_at < ? "
                "AND (frame_path IS NOT NULL OR crop_path IS NOT NULL)",
                (cutoff,),
            ).fetchall()
            for r in rows:
                for p in (r["frame_path"], r["crop_path"]):
                    if p:
                        try:
                            (snaps_dir / p).unlink(missing_ok=True)
                            removed += 1
                        except OSError:
                            pass
                self.conn.execute(
                    "UPDATE sessions SET frame_path = NULL, crop_path = NULL "
                    "WHERE id = ?", (r["id"],)
                )
            arows = self.conn.execute(
                "SELECT id, frame_path FROM alerts "
                "WHERE ts < ? AND frame_path IS NOT NULL", (cutoff,)
            ).fetchall()
            for r in arows:
                try:
                    (snaps_dir / r["frame_path"]).unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    pass
                self.conn.execute(
                    "UPDATE alerts SET frame_path = NULL WHERE id = ?", (r["id"],)
                )
            if rows or arows:
                self.conn.commit()
        return removed
