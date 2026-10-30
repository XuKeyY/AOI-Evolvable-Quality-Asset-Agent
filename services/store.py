import json
from contextlib import contextmanager
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

def identifier(prefix):
    return f"{prefix}_{uuid4().hex}"

def now():
    return datetime.now(timezone.utc).isoformat()

def json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value

class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "aoi.sqlite3"
        with self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, payload TEXT, PRIMARY KEY(kind,id))")
            conn.execute("CREATE TABLE IF NOT EXISTS sequence (id INTEGER PRIMARY KEY AUTOINCREMENT)")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=30)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def put(self, kind, record):
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)", (kind, record['id'], json.dumps(json_safe(record), ensure_ascii=False, allow_nan=False)))
        return record

    def get(self, kind, record_id):
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM records WHERE kind=? AND id=?", (kind, record_id)).fetchone()
        if row is None:
            raise KeyError(f"Unknown {kind}: {record_id}")
        return json.loads(row[0])

    def all(self, kind):
        with self.connect() as conn:
            rows = conn.execute("SELECT payload FROM records WHERE kind=? ORDER BY rowid DESC", (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def next_sequence(self):
        with self.connect() as conn:
            cursor = conn.execute("INSERT INTO sequence DEFAULT VALUES")
            return cursor.lastrowid

    def put_many(self, entries):
        rows = [(kind, record['id'], json.dumps(json_safe(record), ensure_ascii=False, allow_nan=False)) for kind, record in entries]
        with self.connect() as conn:
            conn.executemany("INSERT OR REPLACE INTO records VALUES (?,?,?)", rows)

    def delete_many(self, kind, record_ids):
        ids = list(dict.fromkeys(record_ids))
        if not ids:
            return 0
        with self.connect() as conn:
            cursor = conn.executemany(
                "DELETE FROM records WHERE kind=? AND id=?",
                [(kind, record_id) for record_id in ids],
            )
        return cursor.rowcount
