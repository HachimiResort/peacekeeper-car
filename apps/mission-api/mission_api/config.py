from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PEACEKEEPER_", extra="ignore")

    database_url: str = "postgresql+asyncpg://peacekeeper:peacekeeper@postgres:5432/peacekeeper"
    shared_token: str = Field(min_length=8)
    cars_file: Path = Path("/app/config/cars.yaml")
    map_storage_dir: Path = Path("/var/lib/peacekeeper/maps")
    evidence_storage_dir: Path = Path("/var/lib/peacekeeper/evidence")
    status_poll_s: float = Field(default=1.0, gt=0.1, le=60.0)
    status_timeout_s: float = Field(default=2.0, gt=0.1, le=30.0)
    control_timeout_s: float = Field(default=5.0, gt=0.1, le=60.0)
    map_timeout_s: float = Field(default=120.0, gt=1.0, le=600.0)
    offline_failures: int = Field(default=3, ge=1, le=20)
    last_seen_flush_s: float = Field(default=30.0, ge=5.0, le=300.0)
    max_map_bytes: int = Field(default=64 * 1024 * 1024, ge=1024 * 1024)
    max_audio_bytes: int = Field(default=32 * 1024 * 1024, ge=1024 * 1024)
    max_evidence_bytes: int = Field(default=16 * 1024 * 1024, ge=1024)
    max_poll_concurrency: int = Field(default=20, ge=1, le=200)

    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
