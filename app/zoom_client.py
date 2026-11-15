from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("webinar_attendance.zoom")

ZOOM_OAUTH_URL = "https://zoom.us/oauth/token"
ZOOM_API_BASE = "https://api.zoom.us/v2"


class ZoomClientError(RuntimeError):
    pass


class ZoomClient:
    """Minimal Zoom Server-to-Server OAuth client for meeting/webinar participants."""

    def __init__(self) -> None:
        self._token: str | None = None

    @property
    def configured(self) -> bool:
        return settings.zoom_configured

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        if self._token:
            return self._token
        if not self.configured:
            raise ZoomClientError(
                "Zoom credentials missing. Set ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET."
            )

        response = await client.post(
            ZOOM_OAUTH_URL,
            params={
                "grant_type": "account_credentials",
                "account_id": settings.zoom_account_id,
            },
            auth=(settings.zoom_client_id, settings.zoom_client_secret),
            timeout=30.0,
        )
        if response.status_code >= 400:
            raise ZoomClientError(f"Zoom OAuth failed ({response.status_code}): {response.text[:300]}")
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise ZoomClientError("Zoom OAuth response missing access_token")
        self._token = token
        return token

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=ZOOM_API_BASE) as client:
            token = await self._get_token(client)
            response = await client.get(
                path,
                params=params or {},
                headers={"Authorization": f"Bearer {token}"},
                timeout=45.0,
            )
            if response.status_code == 401:
                self._token = None
                token = await self._get_token(client)
                response = await client.get(
                    path,
                    params=params or {},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=45.0,
                )
            if response.status_code >= 400:
                raise ZoomClientError(
                    f"Zoom API {path} failed ({response.status_code}): {response.text[:400]}"
                )
            return response.json()

    async def _paginate_participants(self, path: str, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        participants: list[dict[str, Any]] = []
        next_token = ""
        for _ in range(50):
            params: dict[str, Any] = {"page_size": 300, **(extra or {})}
            if next_token:
                params["next_page_token"] = next_token
            payload = await self._get_json(path, params)
            batch = payload.get("participants") or []
            participants.extend(batch)
            next_token = (payload.get("next_page_token") or "").strip()
            if not next_token:
                break
        return participants

    async def list_live_participants(self, meeting_id: str) -> list[dict[str, Any]]:
        """Live Dashboard API participants (Business+ Dashboard access required)."""
        mid = _normalize_meeting_id(meeting_id)
        return await self._paginate_participants(
            f"/metrics/meetings/{mid}/participants",
            {"type": "live"},
        )

    async def list_past_meeting_participants(self, meeting_id: str) -> list[dict[str, Any]]:
        mid = _normalize_meeting_id(meeting_id)
        return await self._paginate_participants(f"/report/meetings/{mid}/participants")

    async def list_past_webinar_participants(self, webinar_id: str) -> list[dict[str, Any]]:
        wid = _normalize_meeting_id(webinar_id)
        return await self._paginate_participants(f"/report/webinars/{wid}/participants")

    async def fetch_participants(self, meeting_id: str, mode: str = "auto") -> tuple[str, list[dict[str, Any]]]:
        """
        mode: live | past_meeting | past_webinar | auto
        auto tries live → past meeting → past webinar.
        """
        mode = (mode or "auto").strip().lower()
        errors: list[str] = []

        order: list[str]
        if mode == "auto":
            order = ["live", "past_meeting", "past_webinar"]
        else:
            order = [mode]

        for attempt in order:
            try:
                if attempt == "live":
                    rows = await self.list_live_participants(meeting_id)
                elif attempt == "past_meeting":
                    rows = await self.list_past_meeting_participants(meeting_id)
                elif attempt == "past_webinar":
                    rows = await self.list_past_webinar_participants(meeting_id)
                else:
                    raise ZoomClientError(f"Unknown mode: {attempt}")
                return attempt, rows
            except ZoomClientError as exc:
                logger.info("Zoom fetch mode=%s failed: %s", attempt, exc)
                errors.append(f"{attempt}: {exc}")

        raise ZoomClientError("All Zoom participant fetches failed. " + " | ".join(errors))


def _normalize_meeting_id(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def map_raw_participant(meeting_id: str, raw: dict[str, Any], source_mode: str) -> dict[str, Any]:
    duration = raw.get("duration")
    if duration is None and raw.get("duration_seconds") is not None:
        duration = raw.get("duration_seconds")
    try:
        duration_seconds = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_seconds = None

    status = (raw.get("status") or "").strip().lower() or None
    name = (raw.get("name") or raw.get("user_name") or "").strip()
    email = (raw.get("user_email") or raw.get("email") or None) or None
    if isinstance(email, str):
        email = email.strip() or None

    participant_id = (
        raw.get("id")
        or raw.get("participant_id")
        or raw.get("user_id")
        or raw.get("registrant_id")
    )
    if participant_id is not None:
        participant_id = str(participant_id)

    return {
        "meeting_id": _normalize_meeting_id(meeting_id),
        "participant_id": participant_id,
        "user_id": str(raw["user_id"]) if raw.get("user_id") is not None else None,
        "name": name,
        "email": email,
        "status": status,
        "join_time": raw.get("join_time"),
        "leave_time": raw.get("leave_time"),
        "duration_seconds": duration_seconds,
        "source": f"zoom_{source_mode}",
        "raw": raw,
    }
