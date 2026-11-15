from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class AttendanceLabel(str, Enum):
    in_meeting = "in_meeting"
    attended = "attended"
    partial = "partial"
    registered_only = "registered_only"
    unknown = "unknown"


class ParticipantRecord(BaseModel):
    meeting_id: str
    participant_id: str | None = None
    user_id: str | None = None
    name: str = ""
    email: str | None = None
    status: str | None = None
    join_time: str | None = None
    leave_time: str | None = None
    duration_seconds: int | None = None
    label: AttendanceLabel = AttendanceLabel.unknown
    source: str = "zoom_api"
    raw: dict = Field(default_factory=dict)


class SyncResult(BaseModel):
    meeting_id: str
    mode: str
    fetched: int
    upserted: int
    in_meeting: int
    attendees: int
    partial: int
    message: str = ""


class DashboardStats(BaseModel):
    meeting_id: str | None
    total_logged: int
    in_meeting: int
    attended: int
    partial: int
    registered_only: int
    last_sync_at: str | None


class HealthResponse(BaseModel):
    ok: bool
    zoom_configured: bool
    database: str
    default_meeting_id: str = ""
