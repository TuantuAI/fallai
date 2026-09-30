from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def init(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS contacts (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            posture TEXT NOT NULL,
            event_label TEXT NOT NULL,
            score REAL NOT NULL,
            visible INTEGER NOT NULL,
            observation TEXT NOT NULL,
            ai_ms REAL NOT NULL,
            image TEXT,
            note TEXT
        );
        CREATE TABLE IF NOT EXISTS sms_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
            phone TEXT NOT NULL,
            status TEXT NOT NULL,
            queued_at TEXT NOT NULL,
            finished_at TEXT,
            modem_ms REAL,
            error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_events_user_time ON events(user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_sms_event ON sms_logs(event_id);
        """
        with self._conn() as conn:
            conn.executescript(schema)

    def create_user(self, name: str, email: str, password_hash: str) -> dict[str, Any]:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO users(name,email,password_hash,created_at) VALUES(?,?,?,?)",
                (name.strip(), email.strip().lower(), password_hash, utc_now()),
            )
            row = conn.execute(
                "SELECT id,name,email,created_at FROM users WHERE id=?", (cur.lastrowid,)
            ).fetchone()
            return dict(row)

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id,name,email,created_at FROM users WHERE id=?", (user_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_user_auth(self, email: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email=?", (email.strip().lower(),)
            ).fetchone()
            return dict(row) if row else None

    def create_session(self, token_hash: str, user_id: int, days: int) -> None:
        expires = datetime.now(timezone.utc) + timedelta(days=days)
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
                (token_hash, user_id, expires.isoformat(), utc_now()),
            )

    def user_from_session(self, token_hash: str) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT u.id,u.name,u.email,u.created_at
                FROM sessions s JOIN users u ON u.id=s.user_id
                WHERE s.token_hash=? AND s.expires_at>?
                """,
                (token_hash, now),
            ).fetchone()
            return dict(row) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def get_contact(self, user_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT name,phone,enabled,updated_at FROM contacts WHERE user_id=?",
                (user_id,),
            ).fetchone()
            return dict(row) if row else None

    def upsert_contact(self, user_id: int, name: str, phone: str, enabled: bool) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO contacts(user_id,name,phone,enabled,updated_at) VALUES(?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    name=excluded.name, phone=excluded.phone,
                    enabled=excluded.enabled, updated_at=excluded.updated_at
                """,
                (user_id, name.strip(), phone, int(enabled), utc_now()),
            )

    def add_event(
        self,
        user_id: int,
        posture: str,
        event_label: str,
        score: float,
        visible: int,
        observation: str,
        ai_ms: float,
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO events(
                    user_id,created_at,posture,event_label,score,visible,observation,ai_ms
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    user_id,
                    utc_now(),
                    posture,
                    event_label,
                    float(score),
                    int(visible),
                    observation,
                    float(ai_ms),
                ),
            )
            return int(cur.lastrowid)

    def set_event_image(self, event_id: int, image: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE events SET image=? WHERE id=?", (image, event_id))

    def list_events(self, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT e.*,
                    (SELECT status FROM sms_logs s WHERE s.event_id=e.id ORDER BY s.id DESC LIMIT 1)
                    AS sms_status,
                    (SELECT modem_ms FROM sms_logs s WHERE s.event_id=e.id ORDER BY s.id DESC LIMIT 1)
                    AS sms_ms
                FROM events e WHERE user_id=? ORDER BY id DESC LIMIT ?
                """,
                (user_id, max(1, min(limit, 200))),
            ).fetchall()
            return [dict(row) for row in rows]

    def add_sms(self, event_id: int | None, phone: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO sms_logs(event_id,phone,status,queued_at) VALUES(?,?,?,?)",
                (event_id, phone, "queued", utc_now()),
            )
            return int(cur.lastrowid)

    def finish_sms(
        self, sms_id: int, status: str, modem_ms: float | None, error: str | None
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE sms_logs SET status=?,finished_at=?,modem_ms=?,error=? WHERE id=?",
                (status, utc_now(), modem_ms, error, sms_id),
            )

    def stats(self, user_id: int) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       COALESCE(AVG(ai_ms),0) AS ai_ms_avg,
                       COALESCE(MAX(ai_ms),0) AS ai_ms_max
                FROM events WHERE user_id=?
                """,
                (user_id,),
            ).fetchone()
            return dict(row) if row else {"total": 0, "ai_ms_avg": 0, "ai_ms_max": 0}
