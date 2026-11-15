from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    zoom_account_id: str = ""
    zoom_client_id: str = ""
    zoom_client_secret: str = ""
    zoom_default_meeting_id: str = ""

    app_host: str = "127.0.0.1"
    app_port: int = 5080
    database_path: str = "data/attendance.db"

    @property
    def db_path(self) -> Path:
        path = Path(self.database_path)
        if not path.is_absolute():
            path = ROOT / path
        return path

    @property
    def zoom_configured(self) -> bool:
        return bool(
            self.zoom_account_id.strip()
            and self.zoom_client_id.strip()
            and self.zoom_client_secret.strip()
        )


settings = Settings()