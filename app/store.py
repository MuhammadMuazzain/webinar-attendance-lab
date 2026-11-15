from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.models import AttendanceLabel, DashboardStats, ParticipantRecord


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class AttendanceStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or settings.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meeting_id TEXT NOT NULL,
                    participant_key TEXT NOT NULL,
                    participant_id TEXT,
                    user_id TEXT,
                    name TEXT NOT NULL DEFAULT '',
                    email TEXT,
                    status TEXT,
                    join_time TEXT,
                    leave_time TEXT,
                    duration_seconds INTEGER,
                    label TEXT NOT NULL DEFAULT 'unknown',
                    source TEXT NOT NULL DEFAULT 'zoom_api',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(meeting_id, participant_key)
                );

                CREATE TABLE IF NOT EXISTS sync_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meeting_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    fetched INTEGER NOT NULL,
                    upserted INTEGER NOT NULL,
                    message TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS registrants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meeting_id TEXT NOT NULL,
                    email TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    UNIQUE(meeting_id, email)
                );

                CREATE INDEX IF NOT EXISTS idx_participants_meeting
                    ON participants(meeting_id);
                CREATE INDEX IF NOT EXISTS idx_participants_label
                    ON participants(meeting_id, label);
                """
            )

    @staticmethod
    def _participant_key(row: dict[str, Any]) -> str:
        if row.get("email"):
            return f"email:{(row['email'] or '').lower()}"
        if row.get("participant_id"):
            return f"pid:{row['participant_id']}"
        if row.get("user_id"):
            return f"uid:{row['user_id']}"
        name = (row.get("name") or "unknown").strip().lower()
        join_time = row.get("join_time") or ""
        return f"name:{name}|join:{join_time}"

    @staticmethod
    def _label_for(row: dict[str, Any]) -> AttendanceLabel:
        status = (row.get("status") or "").lower()
        duration = row.get("duration_seconds")
        if status == "in_meeting":
            return AttendanceLabel.in_meeting
        if duration is None:
            if row.get("join_time"):
                return AttendanceLabel.attended
            return AttendanceLabel.unknown
        if duration >= 60:
            return AttendanceLabel.attended
        if duration > 0:
            return AttendanceLabel.partial
        return AttendanceLabel.unknown

    def upsert_participants(self, meeting_id: str, rows: list[dict[str, Any]]) -> int:
        now = _utc_now()
        upserted = 0
        with self._connect() as conn:
            for row in rows:
                key = self._participant_key(row)
                label = self._label_for(row).value
                existing = conn.execute(
                    "SELECT id, first_seen_at FROM participants WHERE meeting_id = ? AND participant_key = ?",
                    (meeting_id, key),
                ).fetchone()
                first_seen = existing["first_seen_at"] if existing else now
                conn.execute(
                    """
                    INSERT INTO participants (
                        meeting_id, participant_key, participant_id, user_id, name, email,
                        status, join_time, leave_time, duration_seconds, label, source,
                        raw_json, first_seen_at, last_seen_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(meeting_id, participant_key) DO UPDATE SET
                        participant_id = excluded.participant_id,
                        user_id = excluded.user_id,
                        name = excluded.name,
                        email = excluded.email,
                        status = excluded.status,
                        join_time = excluded.join_time,
                        leave_time = excluded.leave_time,
                        duration_seconds = excluded.duration_seconds,
                        label = excluded.label,
                        source = excluded.source,
                        raw_json = excluded.raw_json,
                        last_seen_at = excluded.last_seen_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        meeting_id,
                        key,
                        row.get("participant_id"),
                        row.get("user_id"),
                        row.get("name") or "",
                        row.get("email"),
                        row.get("status"),
                        row.get("join_time"),
                        row.get("leave_time"),
                        row.get("duration_seconds"),
                        label,
                        row.get("source") or "zoom_api",
                        json.dumps(row.get("raw") or {}, ensure_ascii=False),
                        first_seen,
                        now,
                        now,
                    ),
                )
                upserted += 1
        return upserted

    def log_sync(self, meeting_id: str, mode: str, fetched: int, upserted: int, message: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_runs (meeting_id, mode, fetched, upserted, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (meeting_id, mode, fetched, upserted, message, _utc_now()),
            )

    def replace_registrants(self, meeting_id: str, registrants: list[dict[str, str]]) -> int:
        with self._connect() as conn:
            conn.execute("DELETE FROM registrants WHERE meeting_id = ?", (meeting_id,))
            count = 0
            for item in registrants:
                email = (item.get("email") or "").strip().lower()
                if not email:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO registrants (meeting_id, email, name) VALUES (?, ?, ?)",
                    (meeting_id, email, (item.get("name") or "").strip()),
                )
                count += 1
        self.mark_no_shows(meeting_id)
        return count

    def mark_no_shows(self, meeting_id: str) -> None:
        """Registrants with no matching attended/in_meeting/partial row become registered_only."""
        now = _utc_now()
        with self._connect() as conn:
            regs = conn.execute(
                "SELECT email, name FROM registrants WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchall()
            for reg in regs:
                email = reg["email"]
                hit = conn.execute(
                    """
                    SELECT id FROM participants
                    WHERE meeting_id = ? AND lower(coalesce(email, '')) = ?
                      AND label IN ('in_meeting', 'attended', 'partial')
                    """,
                    (meeting_id, email),
                ).fetchone()
                if hit:
                    continue
                key = f"email:{email}"
                conn.execute(
                    """
                    INSERT INTO participants (
                        meeting_id, participant_key, name, email, label, source,
                        raw_json, first_seen_at, last_seen_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'roster', '{}', ?, ?, ?)
                    ON CONFLICT(meeting_id, participant_key) DO UPDATE SET
                        label = CASE
                            WHEN participants.label IN ('in_meeting', 'attended', 'partial')
                            THEN participants.label
                            ELSE 'registered_only'
                        END,
                        updated_at = excluded.updated_at
                    """,
                    (
                        meeting_id,
                        key,
                        reg["name"] or "",
                        email,
                        AttendanceLabel.registered_only.value,
                        now,
                        now,
                        now,
                    ),
                )

    def list_participants(
        self,
        meeting_id: str | None = None,
        label: str | None = None,
        q: str | None = None,
        limit: int = 500,
    ) -> list[ParticipantRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if meeting_id:
            clauses.append("meeting_id = ?")
            params.append(meeting_id)
        if label:
            clauses.append("label = ?")
            params.append(label)
        if q:
            clauses.append("(lower(name) LIKE ? OR lower(coalesce(email, '')) LIKE ?)")
            like = f"%{q.lower()}%"
            params.extend([like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 2000)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM participants
                {where}
                ORDER BY
                    CASE label
                        WHEN 'in_meeting' THEN 0
                        WHEN 'attended' THEN 1
                        WHEN 'partial' THEN 2
                        WHEN 'registered_only' THEN 3
                        ELSE 4
                    END,
                    lower(name) ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._to_record(row) for row in rows]

    def stats(self, meeting_id: str | None = None) -> DashboardStats:
        mid = meeting_id
        with self._connect() as conn:
            if not mid:
                latest = conn.execute(
                    "SELECT meeting_id FROM sync_runs ORDER BY id DESC LIMIT 1"
                ).fetchone()
                mid = latest["meeting_id"] if latest else None

            if not mid:
                return DashboardStats(
                    meeting_id=None,
                    total_logged=0,
                    in_meeting=0,
                    attended=0,
                    partial=0,
                    registered_only=0,
                    last_sync_at=None,
                )

            counts = {
                row["label"]: row["c"]
                for row in conn.execute(
                    """
                    SELECT label, COUNT(*) AS c
                    FROM participants
                    WHERE meeting_id = ?
                    GROUP BY label
                    """,
                    (mid,),
                ).fetchall()
            }
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM participants WHERE meeting_id = ?",
                (mid,),
            ).fetchone()["c"]
            last = conn.execute(
                "SELECT created_at FROM sync_runs WHERE meeting_id = ? ORDER BY id DESC LIMIT 1",
                (mid,),
            ).fetchone()

        return DashboardStats(
            meeting_id=mid,
            total_logged=total,
            in_meeting=counts.get("in_meeting", 0),
            attended=counts.get("attended", 0),
            partial=counts.get("partial", 0),
            registered_only=counts.get("registered_only", 0),
            last_sync_at=last["created_at"] if last else None,
        )

    def export_rows(self, meeting_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT meeting_id, name, email, status, label, join_time, leave_time,
                       duration_seconds, source, first_seen_at, last_seen_at
                FROM participants
                WHERE meeting_id = ?
                ORDER BY lower(name)
                """,
                (meeting_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _to_record(row: sqlite3.Row) -> ParticipantRecord:
        return ParticipantRecord(
            meeting_id=row["meeting_id"],
            participant_id=row["participant_id"],
            user_id=row["user_id"],
            name=row["name"] or "",
            email=row["email"],
            status=row["status"],
            join_time=row["join_time"],
            leave_time=row["leave_time"],
            duration_seconds=row["duration_seconds"],
            label=AttendanceLabel(row["label"] or "unknown"),
            source=row["source"] or "zoom_api",
            raw=json.loads(row["raw_json"] or "{}"),
        )
