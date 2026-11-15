from __future__ import annotations

import csv
import io
import logging

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import ROOT, settings
from app.models import HealthResponse
from app.pipeline import AttendancePipeline
from app.store import AttendanceStore
from app.zoom_client import ZoomClientError, _normalize_meeting_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="Webinar Attendance Lab", version="0.1.0")
store = AttendanceStore()
pipeline = AttendancePipeline(store=store)

STATIC_DIR = ROOT / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class SyncRequest(BaseModel):
    meeting_id: str = Field(..., min_length=1)
    mode: str = "auto"


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        ok=True,
        zoom_configured=settings.zoom_configured,
        database=str(settings.db_path),
        default_meeting_id=settings.zoom_default_meeting_id,
    )


@app.get("/api/stats")
async def stats(meeting_id: str | None = None):
    mid = _normalize_meeting_id(meeting_id) if meeting_id else None
    return store.stats(mid)


@app.get("/api/participants")
async def participants(
    meeting_id: str | None = None,
    label: str | None = None,
    q: str | None = None,
    limit: int = Query(500, ge=1, le=2000),
):
    mid = _normalize_meeting_id(meeting_id) if meeting_id else None
    return store.list_participants(meeting_id=mid, label=label, q=q, limit=limit)


@app.post("/api/sync")
async def sync(body: SyncRequest):
    try:
        return await pipeline.sync_meeting(body.meeting_id, mode=body.mode)
    except ZoomClientError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/registrants/import")
async def import_registrants(
    meeting_id: str = Query(...),
    file: UploadFile = File(...),
):
    mid = _normalize_meeting_id(meeting_id)
    if not mid:
        raise HTTPException(status_code=400, detail="meeting_id required")
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    try:
        count = pipeline.import_registrants_csv(mid, raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"meeting_id": mid, "imported": count}


@app.get("/api/export.csv")
async def export_csv(meeting_id: str = Query(...)):
    mid = _normalize_meeting_id(meeting_id)
    if not mid:
        raise HTTPException(status_code=400, detail="meeting_id required")
    rows = store.export_rows(mid)
    buf = io.StringIO()
    fields = [
        "meeting_id",
        "name",
        "email",
        "status",
        "label",
        "join_time",
        "leave_time",
        "duration_seconds",
        "source",
        "first_seen_at",
        "last_seen_at",
    ]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="attendance-{mid}.csv"'},
    )


def run() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
