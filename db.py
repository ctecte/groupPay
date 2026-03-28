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
            payee_telegram_id TEXT,
            payee_phone TEXT,
            payee_amount TEXT,
            even_split INTEGER NOT NULL DEFAULT 1,
            chat_id TEXT,
            thread_id TEXT,
            remind_after_hours REAL,
            remind_at TEXT,
            last_reminded_at TEXT,
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
            payment_ref TEXT,
            whisper_msg_id TEXT,
            UNIQUE(session_id, name)
        );
    """)
    conn.commit()
    # Migrate: add columns if missing (for existing DBs)
    for col, coltype in [("payee_telegram_id", "TEXT"), ("remind_after_hours", "REAL"), ("remind_at", "TEXT"), ("last_reminded_at", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {coltype}")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    for col, coltype in [("payment_ref", "TEXT"), ("whisper_msg_id", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE participants ADD COLUMN {col} {coltype}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.close()


def create_session(event_name: str, bill_amount: str, payee: str,
                   even_split: bool, participants: list[dict],
                   chat_id: str | None = None,
                   thread_id: str | None = None,
                   payee_phone: str | None = None,
                   payee_amount: str | None = None,
                   payee_telegram_id: str | None = None) -> dict:
    session_id = uuid.uuid4().hex[:12]
    now = datetime.utcnow().isoformat()
    conn = _connect()
    conn.execute(
        "INSERT INTO sessions (id, event_name, bill_amount, payee, payee_telegram_id, payee_phone, payee_amount, even_split, chat_id, thread_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (session_id, event_name, bill_amount, payee, payee_telegram_id, payee_phone, payee_amount, int(even_split), chat_id, thread_id, now),
    )
    for p in participants:
        payment_ref = f"GP-{uuid.uuid4().hex[:4].upper()}"
        conn.execute(
            "INSERT INTO participants (session_id, name, telegram_id, amount, payment_ref) VALUES (?,?,?,?,?)",
            (session_id, p["name"], p.get("telegram_id"), p["amount"], payment_ref),
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
        "SELECT name, telegram_id, amount, status, whisper_read, screenshot_path, payment_ref, whisper_msg_id FROM participants WHERE session_id=?",
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


def save_whisper_msg_id(session_id: str, name: str, msg_id: str) -> bool:
    conn = _connect()
    cur = conn.execute(
        "UPDATE participants SET whisper_msg_id=? WHERE session_id=? AND name=?",
        (msg_id, session_id, name),
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def set_auto_remind(session_id: str, hours: float) -> bool:
    """Set auto-remind for a session. Computes remind_at from now + hours."""
    remind_at = (datetime.utcnow() + __import__('datetime').timedelta(hours=hours)).isoformat()
    conn = _connect()
    cur = conn.execute(
        "UPDATE sessions SET remind_after_hours=?, remind_at=? WHERE id=?",
        (hours, remind_at, session_id),
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def cancel_auto_remind(session_id: str) -> bool:
    conn = _connect()
    cur = conn.execute(
        "UPDATE sessions SET remind_after_hours=NULL, remind_at=NULL WHERE id=?",
        (session_id,),
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def get_due_reminders() -> list[dict]:
    """Get sessions with auto-remind due (remind_at <= now and has unpaid participants)."""
    now = datetime.utcnow().isoformat()
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM sessions WHERE remind_at IS NOT NULL AND remind_at <= ?",
        (now,),
    ).fetchall()
    results = []
    for row in rows:
        session = dict(row)
        session["even_split"] = bool(session["even_split"])
        parts = conn.execute(
            "SELECT name, telegram_id, amount, status, whisper_read, screenshot_path, payment_ref, whisper_msg_id FROM participants WHERE session_id=?",
            (session["id"],),
        ).fetchall()
        session["participants"] = [dict(p) for p in parts]
        unpaid = [p for p in session["participants"] if p["status"] != "paid"]
        if unpaid:
            results.append(session)
        else:
            # All paid — cancel the reminder
            conn.execute("UPDATE sessions SET remind_at=NULL, remind_after_hours=NULL WHERE id=?", (session["id"],))
            conn.commit()
    conn.close()
    return results


def reschedule_reminder(session_id: str):
    """Reschedule the reminder for the next interval."""
    conn = _connect()
    row = conn.execute("SELECT remind_after_hours FROM sessions WHERE id=?", (session_id,)).fetchone()
    if row and row["remind_after_hours"]:
        hours = row["remind_after_hours"]
        next_at = (datetime.utcnow() + __import__('datetime').timedelta(hours=hours)).isoformat()
        conn.execute(
            "UPDATE sessions SET remind_at=?, last_reminded_at=? WHERE id=?",
            (next_at, datetime.utcnow().isoformat(), session_id),
        )
        conn.commit()
    conn.close()


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
