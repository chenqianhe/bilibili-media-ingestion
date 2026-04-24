import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.models import AppSecret

_NETSCAPE_SECRET_KEY = "bilibili.cookies_netscape"
_USER_AGENT_SECRET_KEY = "bilibili.download_user_agent"
_LEGACY_COOKIE_SECRET_KEY = "bilibili.cookie_header"
_NETSCAPE_COOKIES = """# Netscape HTTP Cookie File
.bilibili.com\tTRUE\t/\tTRUE\t2147483647\tSESSDATA\tdb-cookie
.bilibili.com\tTRUE\t/\tTRUE\t2147483647\tbili_jct\tcsrf-token
"""
_DOWNLOAD_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


def _clear_bilibili_cookie_override(db: Session) -> None:
    deleted = False
    for key in (
        _NETSCAPE_SECRET_KEY,
        _USER_AGENT_SECRET_KEY,
        _LEGACY_COOKIE_SECRET_KEY,
    ):
        existing = db.get(AppSecret, key)
        if existing is None:
            continue
        db.delete(existing)
        deleted = True
    if deleted:
        db.commit()


def test_read_bilibili_access_status_requires_superuser(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
) -> None:
    response = client.get(
        f"{settings.API_V1_STR}/system/bilibili-access",
        headers=normal_user_token_headers,
    )

    assert response.status_code == 403


def test_read_bilibili_access_status_reports_missing_configuration(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_bilibili_cookie_override(db)
    monkeypatch.setattr(settings, "BILIBILI_COOKIE_HEADER", None)
    monkeypatch.setattr(settings, "YT_DLP_COOKIES_FILE", None)
    monkeypatch.setattr(settings, "YT_DLP_COOKIES_FROM_BROWSER", None)
    monkeypatch.setattr(settings, "YT_DLP_USER_AGENT", None)
    monkeypatch.setattr(settings, "YT_DLP_IMPERSONATE", None)

    response = client.get(
        f"{settings.API_V1_STR}/system/bilibili-access",
        headers=superuser_token_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata_cookie_configured"] is False
    assert payload["download_auth_configured"] is False
    assert payload["has_database_override"] is False
    assert payload["effective_cookie_source"] == "none"
    assert len(payload["warnings"]) == 2


def test_update_bilibili_access_status_stores_database_override(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_bilibili_cookie_override(db)
    monkeypatch.setattr(settings, "BILIBILI_COOKIE_HEADER", "SESSDATA=env-cookie")
    monkeypatch.setattr(settings, "YT_DLP_COOKIES_FILE", None)
    monkeypatch.setattr(settings, "YT_DLP_COOKIES_FROM_BROWSER", None)
    monkeypatch.setattr(settings, "YT_DLP_USER_AGENT", None)
    monkeypatch.setattr(settings, "YT_DLP_IMPERSONATE", None)

    response = client.put(
        f"{settings.API_V1_STR}/system/bilibili-access",
        headers=superuser_token_headers,
        json={
            "netscape_cookies": _NETSCAPE_COOKIES,
            "download_user_agent": _DOWNLOAD_USER_AGENT,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata_cookie_configured"] is True
    assert payload["download_auth_configured"] is True
    assert payload["has_database_override"] is True
    assert payload["effective_cookie_source"] == "database"
    assert payload["cookie_header_summary"] == "SESSDATA=***; bili_jct=***"
    assert payload["netscape_cookie_summary"] == "2 cookies (SESSDATA, bili_jct)"
    assert payload["download_user_agent_summary"] == _DOWNLOAD_USER_AGENT
    assert payload["download_user_agent_configured"] is True
    assert payload["database_cookie_updated_by"] == settings.FIRST_SUPERUSER
    assert len(payload["warnings"]) == 1
    assert "impersonation" in payload["warnings"][0]

    stored_secret = db.get(AppSecret, _NETSCAPE_SECRET_KEY)
    assert stored_secret is not None
    assert stored_secret.encrypted_value != _NETSCAPE_COOKIES
    assert db.get(AppSecret, _USER_AGENT_SECRET_KEY) is not None
    assert db.get(AppSecret, _LEGACY_COOKIE_SECRET_KEY) is None


def test_delete_bilibili_access_status_falls_back_to_environment_cookie(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_bilibili_cookie_override(db)
    monkeypatch.setattr(settings, "BILIBILI_COOKIE_HEADER", "SESSDATA=env-cookie")
    monkeypatch.setattr(settings, "YT_DLP_COOKIES_FILE", "/tmp/env-bilibili.cookies.txt")
    monkeypatch.setattr(settings, "YT_DLP_COOKIES_FROM_BROWSER", None)
    monkeypatch.setattr(settings, "YT_DLP_USER_AGENT", _DOWNLOAD_USER_AGENT)
    monkeypatch.setattr(settings, "YT_DLP_IMPERSONATE", "chrome")

    create_response = client.put(
        f"{settings.API_V1_STR}/system/bilibili-access",
        headers=superuser_token_headers,
        json={
            "netscape_cookies": _NETSCAPE_COOKIES,
            "download_user_agent": _DOWNLOAD_USER_AGENT,
        },
    )
    assert create_response.status_code == 200

    delete_response = client.delete(
        f"{settings.API_V1_STR}/system/bilibili-access",
        headers=superuser_token_headers,
    )

    assert delete_response.status_code == 200
    payload = delete_response.json()
    assert payload["metadata_cookie_configured"] is True
    assert payload["download_auth_configured"] is True
    assert payload["has_database_override"] is False
    assert payload["effective_cookie_source"] == "environment"
    assert payload["cookie_header_summary"] == "SESSDATA=***"
    assert payload["download_user_agent_configured"] is True
    assert payload["yt_dlp_cookies_file_configured"] is True
    assert payload["yt_dlp_impersonate_configured"] is True
    assert db.get(AppSecret, _NETSCAPE_SECRET_KEY) is None
    assert db.get(AppSecret, _USER_AGENT_SECRET_KEY) is None
    assert db.get(AppSecret, _LEGACY_COOKIE_SECRET_KEY) is None


def test_update_bilibili_access_status_rejects_invalid_cookie_file(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    _clear_bilibili_cookie_override(db)

    response = client.put(
        f"{settings.API_V1_STR}/system/bilibili-access",
        headers=superuser_token_headers,
        json={"netscape_cookies": "SESSDATA=db-cookie"},
    )

    assert response.status_code == 400
    assert "Cookie file must start with" in response.json()["detail"]
