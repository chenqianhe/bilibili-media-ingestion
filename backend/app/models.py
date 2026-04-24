import uuid
from datetime import datetime, timezone

from pydantic import EmailStr
from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel


def get_datetime_utc() -> datetime:
    return datetime.now(timezone.utc)


# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on update, all are optional
class UserUpdate(UserBase):
    email: EmailStr | None = Field(default=None, max_length=255)  # type: ignore[assignment]
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class AppSecret(SQLModel, table=True):
    __tablename__ = "app_secrets"

    key: str = Field(primary_key=True, max_length=128)
    encrypted_value: str
    updated_by: str | None = Field(default=None, max_length=255, index=True)
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
        index=True,
    )


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class BilibiliAccessUpdate(SQLModel):
    netscape_cookies: str = Field(min_length=1, max_length=262144)
    download_user_agent: str | None = Field(default=None, max_length=2048)


class BilibiliAccessStatusPublic(SQLModel):
    metadata_cookie_configured: bool
    download_auth_configured: bool
    has_database_override: bool
    effective_cookie_source: str
    cookie_header_summary: str | None = None
    netscape_cookie_summary: str | None = None
    download_user_agent_summary: str | None = None
    download_user_agent_configured: bool = False
    yt_dlp_cookies_file_configured: bool
    yt_dlp_cookies_from_browser_configured: bool
    yt_dlp_impersonate_configured: bool = False
    database_cookie_updated_by: str | None = None
    database_cookie_updated_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)


import app.ingest_models  # noqa: F401,E402
