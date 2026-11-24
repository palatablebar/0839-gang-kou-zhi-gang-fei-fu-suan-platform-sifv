import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = '''
CREATE TABLE IF NOT EXISTS runs(
  id TEXT PRIMARY KEY,
  request_key TEXT UNIQUE NOT NULL,
  created_at TEXT NOT NULL,
  profile TEXT NOT NULL,
  status TEXT NOT NULL,
  revision INTEGER NOT NULL,
  payload TEXT NOT NULL,
  comment TEXT NOT NULL DEFAULT ''
);
'''


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=8)
        connection.row_factory = sqlite3.Row
        return connection

    def init(self):
        with self.connect() as db:
            db.executescript(SCHEMA)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(runs)")}
            additions = {
                "base_key": "TEXT NOT NULL DEFAULT ''",
                "ticket_fingerprint": "TEXT NOT NULL DEFAULT ''",
                "invalidated": "INTEGER NOT NULL DEFAULT 0",
                "invalidated_reason": "TEXT NOT NULL DEFAULT ''",
                "superseded_by": "TEXT NOT NULL DEFAULT ''",
            }
            for name, definition in additions.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE runs ADD COLUMN {name} {definition}")
            db.execute("CREATE INDEX IF NOT EXISTS runs_base ON runs(base_key)")

    def find_by_request(self, request_key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE request_key=?", (request_key,)).fetchone()
        return dict(row) if row else None

    def _request_key(self, db, request_key):
        """失效批次不能复用；同一归属回退时按顺序生成可复核的新批次键。"""
        existing = db.execute("SELECT * FROM runs WHERE request_key=?", (request_key,)).fetchone()
        if existing is None or not existing["invalidated"]:
            return request_key
        suffix = 1
        while True:
            candidate = "{}#{}".format(request_key, suffix)
            row = db.execute("SELECT * FROM runs WHERE request_key=?", (candidate,)).fetchone()
            if row is None or not row["invalidated"]:
                return candidate
            suffix += 1

    def create(self, request_key, profile, payload, base_key=None, ticket_fingerprint=None):
        base_key = base_key if base_key is not None else request_key
        fingerprint = ticket_fingerprint or ""
        run_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        superseded = []
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            request_key = self._request_key(db, request_key)
            existing = db.execute("SELECT * FROM runs WHERE request_key=?", (request_key,)).fetchone()
            if existing is not None:
                return dict(existing)
            # 晚到票据产生新的归属：同源旧批次（含此前无票据的批次）的复核意见随之失效。
            if fingerprint:
                rows = db.execute(
                    "SELECT id FROM runs WHERE base_key=? AND invalidated=0 AND ticket_fingerprint!=?",
                    (base_key, fingerprint)).fetchall()
                superseded = [row["id"] for row in rows]
                for row in rows:
                    db.execute("UPDATE runs SET invalidated=1, superseded_by=?, "
                               "invalidated_reason='TICKET_ATTRIBUTION_REVISED' WHERE id=?",
                               (run_id, row["id"]))
            db.execute("INSERT INTO runs(id,request_key,created_at,profile,status,revision,payload,"
                       "base_key,ticket_fingerprint) VALUES(?,?,?,?,?,?,?,?,?)",
                       (run_id, request_key, now, profile, "DRAFT", 0,
                        json.dumps(payload, ensure_ascii=False), base_key, fingerprint))
        row = self.get(run_id)
        row["superseded_runs"] = superseded
        return row

    def get(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def review(self, run_id, decision, comment, expected_revision):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return None, "missing"
            if row["invalidated"]:
                return None, "invalidated"
            if row["revision"] != expected_revision:
                return None, "stale"
            db.execute("UPDATE runs SET status=?, revision=revision+1, comment=? WHERE id=?",
                       (decision, comment, run_id))
        return self.get(run_id), None
