import os
import secrets
import warnings
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    AnyUrl,
    BeforeValidator,
    EmailStr,
    HttpUrl,
    PostgresDsn,
    computed_field,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Self


def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",") if i.strip()]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)


def build_env_files() -> tuple[str, ...]:
    project_root = Path(__file__).resolve().parents[3]
    env_files = [
        project_root / ".env",
        project_root / ".env.local",
    ]
    if os.environ.get("APP_ENV") == "test":
        env_files.extend(
            [
                project_root / ".env.test",
                project_root / ".env.test.local",
            ]
        )
    return tuple(str(path) for path in env_files)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use repository-level env files, with optional test-specific overrides
        # enabled by setting APP_ENV=test before importing settings.
        env_file=build_env_files(),
        env_ignore_empty=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = secrets.token_urlsafe(32)
    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    FRONTEND_HOST: str = "http://localhost:5173"
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"
    BACKEND_PUBLIC_URL: str = "http://localhost:8000"
    S3_ENDPOINT_URL: str | None = None
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_KEY: str | None = None
    S3_BUCKET: str | None = None
    S3_REGION: str | None = None
    BILIBILI_API_BASE_URL: str = "https://api.bilibili.com"
    BILIBILI_METADATA_TIMEOUT_SECONDS: float = 10.0
    BILIBILI_METADATA_RETRY_ATTEMPTS: int = 3
    BILIBILI_METADATA_USER_AGENT: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )
    BILIBILI_ACCEPT_LANGUAGE: str = "zh-CN,zh;q=0.9,en;q=0.8"
    BILIBILI_COOKIE_HEADER: str | None = None
    BILIBILI_REQUEST_MIN_INTERVAL_SECONDS: float = 0.8
    BILIBILI_REQUEST_JITTER_SECONDS: float = 0.4
    BILIBILI_WBI_KEY_CACHE_TTL_SECONDS: float = 10 * 60
    BILIBILI_COMMENT_EMPTY_PAGE_RETRY_ATTEMPTS: int = 3
    METADATA_WORKER_POLL_INTERVAL_SECONDS: float = 5.0
    METADATA_WORKER_INTER_JOB_DELAY_SECONDS: float = 5 * 60
    DOWNLOAD_WORKER_POLL_INTERVAL_SECONDS: float = 5.0
    UPLOAD_WORKER_POLL_INTERVAL_SECONDS: float = 5.0
    PROCESSING_WORKER_POLL_INTERVAL_SECONDS: float = 5.0
    SUBTITLE_WORKER_POLL_INTERVAL_SECONDS: float = 5.0
    METADATA_WORKER_STALE_AFTER_SECONDS: float = 15 * 60
    DOWNLOAD_WORKER_STALE_AFTER_SECONDS: float = 6 * 60 * 60
    UPLOAD_WORKER_STALE_AFTER_SECONDS: float = 4 * 60 * 60
    PROCESSING_WORKER_STALE_AFTER_SECONDS: float = 4 * 60 * 60
    SUBTITLE_WORKER_STALE_AFTER_SECONDS: float = 4 * 60 * 60
    YT_DLP_BINARY: str = "yt-dlp"
    YT_DLP_COOKIES_FILE: str | None = None
    YT_DLP_COOKIES_FROM_BROWSER: str | None = None
    YT_DLP_USER_AGENT: str | None = None
    YT_DLP_IMPERSONATE: str | None = None
    FFMPEG_BINARY: str = "ffmpeg"
    FFPROBE_BINARY: str = "ffprobe"
    OPENAI_API_KEY: str | None = None
    OPENAI_API_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_TRANSCRIPTION_MODEL: str = "gpt-4o-transcribe-diarize"
    OPENAI_TRANSCRIPTION_LANGUAGE: str | None = None
    OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS: float = 300.0
    OPENAI_TRANSCRIPTION_TEMPERATURE: float = 0.0
    SUBTITLE_TRANSCRIPTION_AUDIO_FORMAT: Literal["m4a", "flac"] = "m4a"
    SUBTITLE_TRANSCRIPTION_AUDIO_BITRATE: str = "48k"
    SUBTITLE_TRANSCRIPTION_AUDIO_COMPRESSION_LEVEL: int = 5
    SUBTITLE_TRANSCRIPTION_AUDIO_SAMPLE_RATE: int = 16000
    SUBTITLE_TRANSCRIPTION_MAX_UPLOAD_BYTES: int = 25 * 1024 * 1024
    S3_FORCE_PATH_STYLE: bool = True
    S3_MULTIPART_CHUNK_SIZE_BYTES: int = 8 * 1024 * 1024
    MEDIA_SIGNING_SECRET: str | None = None
    SIGNED_URL_EXPIRE_SECONDS: int = 900
    INGEST_TMP_DIR: str = "/tmp/bili-ingest"
    RUN_LIVE_S3_SMOKE: bool = False

    BACKEND_CORS_ORIGINS: Annotated[
        list[AnyUrl] | str, BeforeValidator(parse_cors)
    ] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_cors_origins(self) -> list[str]:
        return [str(origin).rstrip("/") for origin in self.BACKEND_CORS_ORIGINS] + [
            self.FRONTEND_HOST
        ]

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None
    POSTGRES_SERVER: str
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> PostgresDsn:
        return PostgresDsn.build(
            scheme="postgresql+psycopg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_SERVER,
            port=self.POSTGRES_PORT,
            path=self.POSTGRES_DB,
        )

    SMTP_TLS: bool = True
    SMTP_SSL: bool = False
    SMTP_PORT: int = 587
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: str | None = None

    @model_validator(mode="after")
    def _set_default_emails_from(self) -> Self:
        if not self.EMAILS_FROM_NAME:
            self.EMAILS_FROM_NAME = self.PROJECT_NAME
        return self

    EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.EMAILS_FROM_EMAIL)

    EMAIL_TEST_USER: EmailStr = "test@example.com"
    FIRST_SUPERUSER: EmailStr
    FIRST_SUPERUSER_PASSWORD: str

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value == "changethis":
            message = (
                f'The value of {var_name} is "changethis", '
                "for security, please change it, at least for deployments."
            )
            if self.ENVIRONMENT == "local":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        self._check_default_secret("POSTGRES_PASSWORD", self.POSTGRES_PASSWORD)
        self._check_default_secret(
            "FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD
        )

        return self


settings = Settings()  # type: ignore
