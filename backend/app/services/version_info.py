from __future__ import annotations

import os
import platform
import subprocess
from importlib import metadata
from pathlib import Path

from app.core.config import settings
from app.models import (
    PackageVersionPublic,
    SystemVersionPublic,
    VersionControlPublic,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PACKAGES = (
    ("app", "backend"),
    ("fastapi", "FastAPI"),
    ("pydantic", "Pydantic"),
    ("sqlmodel", "SQLModel"),
    ("sqlalchemy", "SQLAlchemy"),
    ("psycopg", "psycopg"),
    ("alembic", "Alembic"),
    ("httpx", "HTTPX"),
    ("yt-dlp", "yt-dlp"),
)


def _package_version(distribution_name: str) -> str | None:
    try:
        return metadata.version(distribution_name)
    except metadata.PackageNotFoundError:
        return None


def _git_value(*args: str, empty_is_none: bool = True) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=_PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = completed.stdout.strip()
    if output:
        return output
    return None if empty_is_none else ""


def _git_dirty() -> bool | None:
    status = _git_value("status", "--porcelain", empty_is_none=False)
    if status is None:
        return None
    return bool(status)


def get_system_version() -> SystemVersionPublic:
    app_version = os.environ.get("APP_VERSION") or _package_version("app") or "unknown"
    commit = os.environ.get("APP_BUILD_COMMIT") or _git_value("rev-parse", "HEAD")
    short_commit = (
        os.environ.get("APP_BUILD_SHORT_COMMIT")
        or (commit[:12] if commit else None)
        or _git_value("rev-parse", "--short=12", "HEAD")
    )
    branch = os.environ.get("APP_BUILD_BRANCH") or _git_value(
        "rev-parse",
        "--abbrev-ref",
        "HEAD",
    )

    return SystemVersionPublic(
        service="backend",
        project_name=settings.PROJECT_NAME,
        environment=settings.ENVIRONMENT,
        app_version=app_version,
        python_version=platform.python_version(),
        build_time=os.environ.get("APP_BUILD_TIME"),
        git=VersionControlPublic(
            commit=commit,
            short_commit=short_commit,
            branch=branch,
            dirty=_git_dirty(),
        ),
        packages=[
            PackageVersionPublic(name=display_name, version=_package_version(name))
            for name, display_name in _PACKAGES
        ],
    )
