"""
Rebound Database & Idempotency Layer
Lightweight SQLite store using WAL mode for event deduplication,
crash-safe booking state, and pending SMS approvals.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DEFAULT_DB_PATH = "rebound.db"


class Database:
    """SQLite database manager enforcing WAL mode and table idempotency."""
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # Enforce Write-Ahead Logging (WAL) mode for concurrency per blueprint
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    order_id TEXT,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bookings (
                    booking_intent_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    booking_reference TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_approvals (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    traveler_phone TEXT NOT NULL,
                    hold_order_id TEXT,
                    options_json TEXT NOT NULL,
                    status TEXT NOT NULL, -- pending | approved | rejected | expired
                    expires_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    chosen_offer_id TEXT,
                    cost_delta_usd REAL DEFAULT 0.0,
                    trace_path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()

    # -------------------------------------------------------------------------
    # Event Idempotency
    # -------------------------------------------------------------------------
    def is_event_processed(self, event_id: str) -> bool:
        """Checks if an event_id was already ingested."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT event_id FROM events WHERE event_id = ?", (event_id,))
            return cursor.fetchone() is not None

    def record_event(self, event_id: str, event_type: str, order_id: Optional[str] = None, status: str = "processing") -> bool:
        """
        Atomically records an event_id. Returns True if inserted, False if duplicate.
        """
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO events (event_id, event_type, order_id, status) VALUES (?, ?, ?, ?)",
                    (event_id, event_type, order_id, status),
                )
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            return False

    def update_event_status(self, event_id: str, status: str) -> None:
        with self._get_connection() as conn:
            conn.execute("UPDATE events SET status = ? WHERE event_id = ?", (status, event_id))
            conn.commit()

    # -------------------------------------------------------------------------
    # Pending Approvals (HITL)
    # -------------------------------------------------------------------------
    def save_pending_approval(
        self,
        event_id: str,
        run_id: str,
        traveler_phone: str,
        hold_order_id: Optional[str],
        options: List[Dict[str, Any]],
        expires_at: Optional[datetime] = None,
    ) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pending_approvals
                (event_id, run_id, traveler_phone, hold_order_id, options_json, status, expires_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    event_id,
                    run_id,
                    traveler_phone,
                    hold_order_id,
                    json.dumps(options),
                    expires_at.isoformat() if expires_at else None,
                ),
            )
            conn.commit()

    def get_pending_approval_by_phone(self, traveler_phone: str) -> Optional[Dict[str, Any]]:
        """Finds the most recent pending approval for this phone number."""
        clean_phone = traveler_phone.strip()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM pending_approvals
                WHERE (traveler_phone = ? OR traveler_phone = ? OR ? LIKE '%' || traveler_phone)
                  AND status = 'pending'
                ORDER BY created_at DESC LIMIT 1
                """,
                (clean_phone, clean_phone.replace("+", ""), clean_phone),
            )
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["options"] = json.loads(d["options_json"])
                return d
            return None

    def resolve_pending_approval(self, event_id: str, status: str) -> None:
        with self._get_connection() as conn:
            conn.execute("UPDATE pending_approvals SET status = ? WHERE event_id = ?", (status, event_id))
            conn.commit()

    # -------------------------------------------------------------------------
    # Booking Records
    # -------------------------------------------------------------------------
    def record_booking(self, booking_intent_id: str, event_id: str, order_id: str, booking_reference: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bookings
                (booking_intent_id, event_id, order_id, booking_reference, status)
                VALUES (?, ?, ?, ?, 'confirmed')
                """,
                (booking_intent_id, event_id, order_id, booking_reference),
            )
            conn.commit()

    # -------------------------------------------------------------------------
    # Run Records & Dashboard Feeds
    # -------------------------------------------------------------------------
    def record_run(
        self,
        run_id: str,
        event_id: str,
        action: str,
        chosen_offer_id: Optional[str] = None,
        cost_delta_usd: float = 0.0,
        trace_path: Optional[str] = None,
    ) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs
                (run_id, event_id, action, chosen_offer_id, cost_delta_usd, trace_path)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, event_id, action, chosen_offer_id, cost_delta_usd, trace_path),
            )
            conn.commit()

    def get_recent_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))
            return [dict(r) for r in cursor.fetchall()]
