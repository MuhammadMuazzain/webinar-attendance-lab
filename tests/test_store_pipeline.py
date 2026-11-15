from __future__ import annotations

from pathlib import Path

from app.pipeline import AttendancePipeline
from app.store import AttendanceStore
from app.zoom_client import map_raw_participant


def test_upsert_and_labels(tmp_path: Path) -> None:
    store = AttendanceStore(db_path=tmp_path / "t.db")
    meeting_id = "1234567890"
    rows = [
        map_raw_participant(
            meeting_id,
            {
                "id": "p1",
                "name": "Alex Buyer",
                "user_email": "alex@example.com",
                "status": "in_meeting",
                "join_time": "2026-11-15T16:00:00Z",
                "duration": 120,
            },
            "live",
        ),
        map_raw_participant(
            meeting_id,
            {
                "id": "p2",
                "name": "Sam Short",
                "user_email": "sam@example.com",
                "join_time": "2026-11-15T16:01:00Z",
                "leave_time": "2026-11-15T16:01:20Z",
                "duration": 20,
            },
            "past_meeting",
        ),
    ]
    assert store.upsert_participants(meeting_id, rows) == 2
    stats = store.stats(meeting_id)
    assert stats.total_logged == 2
    assert stats.in_meeting == 1
    assert stats.partial == 1


def test_registrant_no_show(tmp_path: Path) -> None:
    store = AttendanceStore(db_path=tmp_path / "t.db")
    pipe = AttendancePipeline(store=store)
    meeting_id = "999"
    store.upsert_participants(
        meeting_id,
        [
            map_raw_participant(
                meeting_id,
                {
                    "name": "Present Lead",
                    "user_email": "present@example.com",
                    "duration": 300,
                    "join_time": "2026-11-15T17:00:00Z",
                },
                "past_webinar",
            )
        ],
    )
    csv_text = "email,name\npresent@example.com,Present Lead\nmissing@example.com,Missing Lead\n"
    assert pipe.import_registrants_csv(meeting_id, csv_text) == 2
    labels = {p.email: p.label.value for p in store.list_participants(meeting_id)}
    assert labels["present@example.com"] == "attended"
    assert labels["missing@example.com"] == "registered_only"
