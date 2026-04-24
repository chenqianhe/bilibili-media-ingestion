import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

# Host-based pytest runs should default to the repository test DB config.
os.environ.setdefault("APP_ENV", "test")

from app.core.config import settings
from app.core.db import engine, init_db
from app.ingest_models import (
    AuditEvent,
    IngestJob,
    MediaAsset,
    Uploader,
    Video,
    VideoComment,
    VideoCommentImage,
    VideoDanmaku,
    VideoPage,
    VideoStatSnapshot,
    VideoSubtitle,
)
from app.main import app
from app.models import AppSecret, User
from tests.utils.user import authentication_token_from_email
from tests.utils.utils import get_superuser_token_headers


def clear_test_data(session: Session) -> None:
    session.rollback()
    for model in (
        AuditEvent,
        VideoDanmaku,
        VideoCommentImage,
        VideoComment,
        VideoSubtitle,
        VideoStatSnapshot,
        MediaAsset,
        VideoPage,
        IngestJob,
        Video,
        Uploader,
        AppSecret,
        User,
    ):
        statement = delete(model)
        session.execute(statement)
    session.commit()


@pytest.fixture(scope="session")
def db() -> Generator[Session, None, None]:
    AppSecret.__table__.create(bind=engine, checkfirst=True)
    with Session(engine) as session:
        clear_test_data(session)
        init_db(session)
        yield session
        clear_test_data(session)


@pytest.fixture(scope="module")
def client(db: Session) -> Generator[TestClient, None, None]:
    del db
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def superuser_token_headers(client: TestClient) -> dict[str, str]:
    return get_superuser_token_headers(client)


@pytest.fixture(scope="module")
def normal_user_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    return authentication_token_from_email(
        client=client, email=settings.EMAIL_TEST_USER, db=db
    )
