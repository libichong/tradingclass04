import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .models import CustomStrategy

DEFAULT_DB = Path(__file__).resolve().parents[1] / ".local" / "research.sqlite3"


class ResearchStore:
    def __init__(self, path: Path = DEFAULT_DB):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS strategies (id TEXT PRIMARY KEY, document TEXT NOT NULL, updated_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS universes (id TEXT NOT NULL, version TEXT NOT NULL, document TEXT NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY (id, version))")
            db.execute("CREATE TABLE IF NOT EXISTS harness_runs (id TEXT PRIMARY KEY, document TEXT NOT NULL, updated_at TEXT NOT NULL)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=15)

    def list_strategies(self):
        with self.connect() as db:
            rows = db.execute("SELECT id, document, updated_at FROM strategies ORDER BY updated_at DESC").fetchall()
        return [{"id": row[0], "definition": CustomStrategy.model_validate_json(row[1]).model_dump(), "updated_at": row[2]} for row in rows]

    def save_strategy(self, definition: CustomStrategy, identifier: str | None = None):
        stamp = datetime.now(timezone.utc).isoformat()
        identifier = identifier or str(uuid4())
        with self.connect() as db:
            db.execute("INSERT INTO strategies VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET document=excluded.document, updated_at=excluded.updated_at",
                       (identifier, definition.model_dump_json(), stamp))
        return {"id": identifier, "definition": definition.model_dump(), "updated_at": stamp}

    def delete_strategy(self, identifier: str):
        with self.connect() as db:
            return db.execute("DELETE FROM strategies WHERE id = ?", (identifier,)).rowcount > 0

    def load_universe(self, identifier: str, version: str | None = None):
        with self.connect() as db:
            if version:
                row = db.execute("SELECT document FROM universes WHERE id=? AND version=?", (identifier, version)).fetchone()
            else:
                row = db.execute("SELECT document FROM universes WHERE id=? ORDER BY fetched_at DESC LIMIT 1", (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_universe(self, snapshot: dict):
        with self.connect() as db:
            db.execute("INSERT INTO universes VALUES (?, ?, ?, ?) ON CONFLICT(id,version) DO UPDATE SET fetched_at=excluded.fetched_at, document=excluded.document",
                       (snapshot["id"], snapshot["version"], json.dumps(snapshot, ensure_ascii=False), snapshot["fetched_at"]))

    def save_harness(self, document: dict):
        stamp = datetime.now(timezone.utc).isoformat()
        document["updated_at"] = stamp
        encoded = json.dumps(document, ensure_ascii=False, allow_nan=False)
        with self.connect() as db:
            db.execute("INSERT INTO harness_runs VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET document=excluded.document, updated_at=excluded.updated_at",
                       (document["id"], encoded, stamp))

    def load_harness(self, identifier: str):
        with self.connect() as db:
            row = db.execute("SELECT document FROM harness_runs WHERE id=?", (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_harness(self):
        with self.connect() as db:
            rows = db.execute("SELECT document FROM harness_runs ORDER BY updated_at DESC LIMIT 50").fetchall()
        return [{"id": item["id"], "status": item["status"], "created_at": item["created_at"],
                 "updated_at": item["updated_at"], "baseline": item["request"]["baseline"],
                 "mode": item["request"]["mode"], "experiments": len(item["experiments"]),
                 "champion_id": item["champion_id"]} for item in (json.loads(row[0]) for row in rows)]
