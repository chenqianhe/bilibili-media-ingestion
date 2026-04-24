from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.ingest_models import IngestJob, MediaAsset, Video
from tests.utils.utils import random_bvid


def test_create_ingest_job_allows_download_without_rights(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    bvid = random_bvid()
    response = client.post(
        f"{settings.API_V1_STR}/ingest/videos",
        headers=normal_user_token_headers,
        json={
            "input": f"https://www.bilibili.com/video/{bvid}",
            "options": {"download_video": True},
        },
    )
    assert response.status_code == 202
    content = response.json()
    assert content["bvid"] == bvid
    assert content["status"] == "pending"
    assert content["phase"] == "queued for metadata ingestion"


def test_create_ingest_job_is_idempotent(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    bvid = random_bvid()
    payload = {
        "input": bvid,
        "options": {"download_video": False},
    }

    first = client.post(
        f"{settings.API_V1_STR}/ingest/videos",
        headers=normal_user_token_headers,
        json=payload,
    )
    second = client.post(
        f"{settings.API_V1_STR}/ingest/videos",
        headers=normal_user_token_headers,
        json=payload,
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]


def test_create_ingest_job_defaults_auxiliary_fetches_to_expected_defaults(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
) -> None:
    bvid = random_bvid()
    create_response = client.post(
        f"{settings.API_V1_STR}/ingest/videos",
        headers=normal_user_token_headers,
        json={"input": bvid},
    )

    assert create_response.status_code == 202
    job_id = create_response.json()["job_id"]

    detail_response = client.get(
        f"{settings.API_V1_STR}/ingest/jobs/{job_id}",
        headers=normal_user_token_headers,
    )
    assert detail_response.status_code == 200

    options = detail_response.json()["options"]
    assert options["max_height"] is None
    assert options["fetch_comments"] is False
    assert options["fetch_danmaku"] is False
    assert options["fetch_subtitles"] is True
    assert options["transcribe_subtitles"] is True


@pytest.mark.parametrize(
    "option_payload",
    [
        {"fetch_comments": True},
        {"fetch_danmaku": True},
        {"fetch_subtitles": True},
        {"transcribe_subtitles": True},
        {
            "fetch_comments": True,
            "fetch_danmaku": True,
            "fetch_subtitles": True,
            "transcribe_subtitles": True,
        },
    ],
)
def test_create_ingest_job_accepts_auxiliary_fetch_flags(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    option_payload: dict[str, bool],
) -> None:
    bvid = random_bvid()

    response = client.post(
        f"{settings.API_V1_STR}/ingest/videos",
        headers=normal_user_token_headers,
        json={
            "input": bvid,
            "options": option_payload,
        },
    )

    assert response.status_code == 202
    job_id = response.json()["job_id"]

    detail_response = client.get(
        f"{settings.API_V1_STR}/ingest/jobs/{job_id}",
        headers=normal_user_token_headers,
    )

    assert detail_response.status_code == 200
    options = detail_response.json()["options"]
    assert options["fetch_comments"] is option_payload.get("fetch_comments", False)
    assert options["fetch_danmaku"] is option_payload.get("fetch_danmaku", False)
    assert options["fetch_subtitles"] is option_payload.get("fetch_subtitles", True)
    assert options["transcribe_subtitles"] is option_payload.get(
        "transcribe_subtitles",
        True,
    )


def test_read_ingest_jobs_scopes_results_to_current_user(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    normal_user_token_headers: dict[str, str],
) -> None:
    user_bvid = random_bvid()
    admin_bvid = random_bvid()

    user_response = client.post(
        f"{settings.API_V1_STR}/ingest/videos",
        headers=normal_user_token_headers,
        json={"input": user_bvid},
    )
    assert user_response.status_code == 202

    admin_response = client.post(
        f"{settings.API_V1_STR}/ingest/videos",
        headers=superuser_token_headers,
        json={"input": admin_bvid},
    )
    assert admin_response.status_code == 202

    user_jobs_response = client.get(
        f"{settings.API_V1_STR}/ingest/jobs",
        headers=normal_user_token_headers,
    )
    assert user_jobs_response.status_code == 200
    user_payload = user_jobs_response.json()
    assert user_payload["count"] >= 1
    assert all(
        item["bvid"] != admin_bvid
        for item in user_payload["data"]
    )
    assert any(item["bvid"] == user_bvid for item in user_payload["data"])

    admin_jobs_response = client.get(
        f"{settings.API_V1_STR}/ingest/jobs",
        headers=superuser_token_headers,
        params={"requested_by": settings.FIRST_SUPERUSER},
    )
    assert admin_jobs_response.status_code == 200
    admin_payload = admin_jobs_response.json()
    assert any(item["bvid"] == admin_bvid for item in admin_payload["data"])


def test_read_video_assets_and_signed_url(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    bvid = random_bvid()
    video = Video(
        bvid=bvid,
        title=bvid,
    )
    db.add(video)
    db.commit()

    asset = MediaAsset(
        bvid=bvid,
        cid=123456,
        asset_type="source_archive",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=f"media/source/bvid={bvid}/cid=123456/asset_id=test/source.mp4",
        filename="source.mp4",
        content_type="video/mp4",
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    assets_response = client.get(
        f"{settings.API_V1_STR}/videos/{bvid}/assets",
        headers=superuser_token_headers,
    )
    assert assets_response.status_code == 200
    assets_content = assets_response.json()
    assert assets_content["bvid"] == bvid
    assert len(assets_content["assets"]) == 1
    assert assets_content["assets"][0]["asset_id"] == str(asset.id)

    signed_url_response = client.post(
        f"{settings.API_V1_STR}/media/assets/{asset.id}/signed-url",
        headers=superuser_token_headers,
        json={"expires_in": 900},
    )
    assert signed_url_response.status_code == 200
    signed_url = signed_url_response.json()["url"]

    parsed = urlparse(signed_url)
    download_response = client.get(f"{parsed.path}?{parsed.query}")
    assert download_response.status_code == 200
    descriptor = download_response.json()
    assert descriptor["asset_id"] == str(asset.id)
    assert descriptor["s3_bucket"] == "bili-media-dev"
    assert descriptor["s3_key"] == asset.s3_key
