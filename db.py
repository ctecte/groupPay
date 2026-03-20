"""SQLite helper for GroupPay sessions."""

import sqlite3
import os
import uuid
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH", "grouppay.db")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = _connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            event_name TEXT NOT NULL,
            bill_amount TEXT NOT NULL,
            payee TEXT NOT NULL,
            payee_phone TEXT,
            payee_amount TEXT,
            even_split INTEGER NOT NULL DEFAULT 1,
            chat_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            name TEXT NOT NULL,
            telegram_id TEXT,
            amount TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            whisper_read INTEGER NOT NULL DEFAULT 0,
            screenshot_path TEXT,
            UNIQUE(session_id, name)
        );
    """)
    conn.commit()
    conn.close()


def create_session(event_name: str, bill_amount: str, payee: str,
                   even_split: bool, participants: list[dict],
                   chat_id: str | None = None,
                   payee_phone: str | None = None,
                   payee_amount: str | None = None) -> dict:
    session_id = uuid.uuid4().hex[:12]
    now = datetime.utcnow().isoformat()
    conn = _connect()
    conn.execute(
        "INSERT INTO sessions (id, event_name, bill_amount, payee, payee_phone, payee_amount, even_split, chat_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (session_id, event_name, bill_amount, payee, payee_phone, payee_amount, int(even_split), chat_id, now),
    )
    for p in participants:
        conn.execute(
            "INSERT INTO participants (session_id, name, telegram_id, amount) VALUES (?,?,?,?)",
            (session_id, p["name"], p.get("telegram_id"), p["amount"]),
        )
    conn.commit()
    conn.close()
    return get_session(session_id)


def get_session(session_id: str) -> dict | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        conn.close()
        return None
    session = dict(row)
    session["even_split"] = bool(session["even_split"])
    parts = conn.execute(
        "SELECT name, telegram_id, amount, status, whisper_read, screenshot_path FROM participants WHERE session_id=?",
        (session_id,),
    ).fetchall()
    session["participants"] = [dict(p) for p in parts]
    conn.close()
    return session


def update_participant_status(session_id: str, name: str, status: str) -> bool:
    conn = _connect()
    cur = conn.execute(
        "UPDATE participants SET status=? WHERE session_id=? AND name=?",
        (status, session_id, name),
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def mark_whisper_read(session_id: str, name: str) -> bool:
    conn = _connect()
    cur = conn.execute(
        "UPDATE participants SET whisper_read=1 WHERE session_id=? AND name=?",
        (session_id, name),
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def save_screenshot_path(session_id: str, name: str, path: str) -> bool:
    conn = _connect()
    cur = conn.execute(
        "UPDATE participants SET screenshot_path=? WHERE session_id=? AND name=?",
        (path, session_id, name),
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok
