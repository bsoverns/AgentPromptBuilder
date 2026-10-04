"""
SQLite storage for projects, requests and saved prompt versions.

    projects  one row per project profile (repo, language, commands, rules)
    requests  one row per piece of work (title, task type, status)
    versions  every saved prompt for a request: the full form state plus the
              exact prompt text, so any version can be recalled or reused

The UI only talks to Store, so another backend (for example the local board
system) can replace it later by implementing the same methods.
"""

import json
import os
import sqlite3
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE COLLATE NOCASE,
    data    TEXT NOT NULL DEFAULT '{}',
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    template_id TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'Draft',
    created     TEXT NOT NULL,
    updated     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
    id         INTEGER PRIMARY KEY,
    request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    created    TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    state      TEXT NOT NULL,
    prompt     TEXT NOT NULL,
    edited     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_requests_project ON requests(project_id);
CREATE INDEX IF NOT EXISTS ix_versions_request ON versions(request_id);
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Store:
    def __init__(self, path: str | os.PathLike) -> None:
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # -- projects -----------------------------------------------------------

    @staticmethod
    def _project(row: sqlite3.Row) -> dict:
        p = json.loads(row["data"] or "{}")
        p.update(id=row["id"], name=row["name"])
        return p

    def list_projects(self) -> list[dict]:
        rows = self.db.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE")
        return [self._project(r) for r in rows]

    def get_project(self, pid: int) -> dict | None:
        row = self.db.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
        return self._project(row) if row else None

    def find_project(self, name: str) -> dict | None:
        row = self.db.execute("SELECT * FROM projects WHERE name = ?", (name.strip(),)).fetchone()
        return self._project(row) if row else None

    def save_project(self, values: dict, pid: int | None = None) -> int:
        name = values["name"].strip()
        data = json.dumps({k: v for k, v in values.items() if k not in ("id", "name")})
        with self.db:
            if pid:
                self.db.execute(
                    "UPDATE projects SET name = ?, data = ?, updated = ? WHERE id = ?",
                    (name, data, _now(), pid),
                )
                return pid
            cur = self.db.execute(
                "INSERT INTO projects (name, data, created, updated) VALUES (?, ?, ?, ?)",
                (name, data, _now(), _now()),
            )
            return cur.lastrowid

    def delete_project(self, pid: int) -> None:
        with self.db:
            self.db.execute("DELETE FROM projects WHERE id = ?", (pid,))

    def request_count(self, project_id: int) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM requests WHERE project_id = ?", (project_id,)
        ).fetchone()[0]

    # -- requests -----------------------------------------------------------

    def list_requests(self, project_id: int | None = None) -> list[dict]:
        """Requests, newest first, with project name, version count and the latest prompt."""
        sql = """
            SELECT r.*, p.name AS project_name,
                   (SELECT COUNT(*) FROM versions v WHERE v.request_id = r.id) AS version_count,
                   (SELECT v.prompt FROM versions v WHERE v.request_id = r.id
                     ORDER BY v.id DESC LIMIT 1) AS latest_prompt
            FROM requests r LEFT JOIN projects p ON p.id = r.project_id"""
        args = ()
        if project_id:
            sql += " WHERE r.project_id = ?"
            args = (project_id,)
        sql += " ORDER BY r.updated DESC, r.id DESC"
        return [dict(r) for r in self.db.execute(sql, args)]

    def get_request(self, rid: int) -> dict | None:
        row = self.db.execute("SELECT * FROM requests WHERE id = ?", (rid,)).fetchone()
        return dict(row) if row else None

    def create_request(self, project_id: int, title: str, template_id: str, status: str = "Draft") -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO requests (project_id, title, template_id, status, created, updated) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, title, template_id, status, _now(), _now()),
            )
        return cur.lastrowid

    def update_request(self, rid: int, **fields: object) -> None:
        allowed = {k: v for k, v in fields.items() if k in ("project_id", "title", "template_id", "status")}
        if not allowed:
            return
        sets = ", ".join(f"{k} = ?" for k in allowed)
        with self.db:
            self.db.execute(
                f"UPDATE requests SET {sets}, updated = ? WHERE id = ?", (*allowed.values(), _now(), rid)
            )

    def delete_request(self, rid: int) -> None:
        with self.db:
            self.db.execute("DELETE FROM requests WHERE id = ?", (rid,))

    # -- versions -----------------------------------------------------------

    @staticmethod
    def _version(row: sqlite3.Row) -> dict:
        v = dict(row)
        v["state"] = json.loads(v["state"])
        v["edited"] = bool(v["edited"])
        return v

    def add_version(self, rid: int, state: dict, prompt: str, edited: bool = False, note: str = "") -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO versions (request_id, created, note, state, prompt, edited) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (rid, _now(), note, json.dumps(state), prompt, int(edited)),
            )
            self.db.execute("UPDATE requests SET updated = ? WHERE id = ?", (_now(), rid))
        return cur.lastrowid

    def list_versions(self, rid: int) -> list[dict]:
        """Oldest first, so list position + 1 is the version number."""
        rows = self.db.execute("SELECT * FROM versions WHERE request_id = ? ORDER BY id", (rid,))
        return [self._version(r) for r in rows]

    def latest_version(self, rid: int) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM versions WHERE request_id = ? ORDER BY id DESC LIMIT 1", (rid,)
        ).fetchone()
        return self._version(row) if row else None
