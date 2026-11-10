"""Central configuration. Every secret comes from environment variables."""
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]  # .../backend


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), extra="ignore")

    APP_NAME: str = "WORKCODE"
    ENV: str = "development"          # development | production
    API_PREFIX: str = "/api"

    # --- security -------------------------------------------------
    SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_MIN: int = 30
    REFRESH_TOKEN_TTL_DAYS: int = 7
    PASSWORD_MIN_LENGTH: int = 8

    @model_validator(mode="after")
    def validate_production_secret(self) -> "Settings":
        if not self.SECRET_KEY:
            raise ValueError("SECRET_KEY must be configured.")
        if self.ENV.lower() == "production" and len(self.SECRET_KEY) < 64:
            raise ValueError("Production requires SECRET_KEY with at least 64 characters.")
        return self


    # --- database -------------------------------------------------
    # Production: postgresql+psycopg2://workcode:pass@db:5432/workcode
    DATABASE_URL: str

    # --- storage --------------------------------------------------
    STORAGE_ROOT: str = str(BASE_DIR / "storage")
    MAX_UPLOAD_MB: int = 50

    # --- code execution sandbox ----------------------------------
    RUNNER_MODE: str = "docker"       # untrusted execution requires Docker isolation
    RUN_TIMEOUT_SECONDS: int = 10
    RUN_MEMORY_MB: int = 256
    RUN_CPU_SECONDS: int = 5
    RUN_MAX_PROCESSES: int = 32
    DOCKER_IMAGE_PREFIX: str = "workcode-runner"
    DOCKER_TERMINAL_IMAGE: str = "workcode-terminal:latest"
    # Optional named volume shared with Docker when the API itself runs in Compose.
    DOCKER_STORAGE_VOLUME: str = ""

    # --- monitoring ----------------------------------------------
    AUTO_PAUSE_MINUTES: int = 10      # inactivity grace period
    HEARTBEAT_SECONDS: int = 30
    RECORDING_RETENTION_DAYS: int = 30
    # --- email / SMTP --------------------------------------------
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""
    SMTP_FROM_NAME: str = "WORKCODE"
    SMTP_USE_TLS: bool = True

    # --- AI assistance (OpenAI-compatible Chat Completions API) ---
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"

    # --- google meet ---------------------------------------------
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/meetings/google/callback"
    GOOGLE_SCOPES: str = "https://www.googleapis.com/auth/calendar.events"

    # --- http -----------------------------------------------------
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000"
    RATE_LIMIT_PER_MINUTE: int = 240
    LOGIN_RATE_LIMIT: int = 20
    FRONTEND_DIST: str = str(BASE_DIR.parent / "frontend" / "dist")

    # --- audit visibility ----------------------------------------
    # "per_admin": each admin sees only their own audit entries by default
    # "all_admins": every admin sees every admin's entries
    AUDIT_VISIBILITY: str = "per_admin"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_postgres(self) -> bool:
        return self.DATABASE_URL.startswith("postgres")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
