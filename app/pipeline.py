from __future__ import annotations

import csv
import io
import logging

from app.models import SyncResult
from app.store import AttendanceStore
from app.zoom_client import ZoomClient, ZoomClientError, map_raw_participant, _normalize_meeting_id

logger = logging.getLogger("webinar_attendance.pipeline")


class AttendancePipeline:
    def __init__(self, store: AttendanceStore | None = None, zoom: ZoomClient | None = None) -> None:
        self.store = store or AttendanceStore()
        self.zoom = zoom or ZoomClient()

    async def sync_meeting(self, meeting_id: str, mode: str = "auto") -> SyncResult:
        mid = _normalize_meeting_id(meeting_id)
        if not mid:
            raise ZoomClientError("Meeting / webinar ID is required")

        used_mode, raw_rows = await self.zoom.fetch_participants(mid, mode=mode)
        mapped = [map_raw_participant(mid, row, used_mode) for row in raw_rows]
        upserted = self.store.upsert_participants(mid, mapped)
        self.store.mark_no_shows(mid)

        stats = self.store.stats(mid)
        message = f"Synced via {used_mode}"
        self.store.log_sync(mid, used_mode, len(mapped), upserted, message)
        logger.info("%s meeting=%s fetched=%s upserted=%s", message, mid, len(mapped), upserted)

        return SyncResult(
            meeting_id=mid,
            mode=used_mode,
            fetched=len(mapped),
            upserted=upserted,
            in_meeting=stats.in_meeting,
            attendees=stats.attended,
            partial=stats.partial,
            message=message,
        )

    def import_registrants_csv(self, meeting_id: str, csv_text: str) -> int:
        mid = _normalize_meeting_id(meeting_id)
        reader = csv.DictReader(io.StringIO(csv_text))
        if not reader.fieldnames:
            raise ValueError("CSV must include a header row with email (and optional name)")

        fields = {f.lower().strip(): f for f in reader.fieldnames}
        email_key = fields.get("email") or fields.get("user_email") or fields.get("mail")
        name_key = fields.get("name") or fields.get("full_name") or fields.get("first_name")
        if not email_key:
            raise ValueError("CSV needs an email column")

        registrants: list[dict[str, str]] = []
        for row in reader:
            email = (row.get(email_key) or "").strip()
            name = (row.get(name_key) or "").strip() if name_key else ""
            if email:
                registrants.append({"email": email, "name": name})

        return self.store.replace_registrants(mid, registrants)
