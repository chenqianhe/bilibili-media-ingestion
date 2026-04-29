import uuid
from datetime import datetime, timedelta, timezone

import jwt
from jwt.exceptions import InvalidTokenError

from app.core.config import settings


def _signing_secret() -> str:
    return settings.MEDIA_SIGNING_SECRET or settings.SECRET_KEY


def _create_media_asset_token(
    *,
    asset_id: uuid.UUID,
    expires_in: int,
    purpose: str,
) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return jwt.encode(
        {
            "exp": expires_at,
            "sub": str(asset_id),
            "purpose": purpose,
        },
        _signing_secret(),
        algorithm="HS256",
    )


def _build_media_asset_url(
    *,
    asset_id: uuid.UUID,
    expires_in: int,
    route: str,
    purpose: str,
) -> str:
    token = _create_media_asset_token(
        asset_id=asset_id,
        expires_in=expires_in,
        purpose=purpose,
    )
    return (
        f"{settings.BACKEND_PUBLIC_URL}{settings.API_V1_STR}"
        f"/media/assets/{asset_id}/{route}?token={token}"
    )


def _verify_media_asset_token(
    *,
    asset_id: uuid.UUID,
    token: str,
    purpose: str,
) -> datetime:
    try:
        payload = jwt.decode(token, _signing_secret(), algorithms=["HS256"])
    except InvalidTokenError as exc:
        raise ValueError("Invalid media asset token") from exc
    if payload.get("purpose") != purpose:
        raise ValueError("Invalid media asset token purpose")
    if payload.get("sub") != str(asset_id):
        raise ValueError("Media asset token does not match asset")
    exp = payload.get("exp")
    if not isinstance(exp, int):
        raise ValueError("Media asset token has no expiration")
    return datetime.fromtimestamp(exp, tz=timezone.utc)


def create_media_download_token(*, asset_id: uuid.UUID, expires_in: int) -> str:
    return _create_media_asset_token(
        asset_id=asset_id,
        expires_in=expires_in,
        purpose="media_asset_download",
    )


def build_media_download_url(*, asset_id: uuid.UUID, expires_in: int) -> str:
    return _build_media_asset_url(
        asset_id=asset_id,
        expires_in=expires_in,
        route="download",
        purpose="media_asset_download",
    )


def verify_media_download_token(*, asset_id: uuid.UUID, token: str) -> datetime:
    return _verify_media_asset_token(
        asset_id=asset_id,
        token=token,
        purpose="media_asset_download",
    )


def create_media_playback_token(*, asset_id: uuid.UUID, expires_in: int) -> str:
    return _create_media_asset_token(
        asset_id=asset_id,
        expires_in=expires_in,
        purpose="media_asset_playback",
    )


def build_media_playback_url(*, asset_id: uuid.UUID, expires_in: int) -> str:
    return _build_media_asset_url(
        asset_id=asset_id,
        expires_in=expires_in,
        route="playback",
        purpose="media_asset_playback",
    )


def verify_media_playback_token(*, asset_id: uuid.UUID, token: str) -> datetime:
    return _verify_media_asset_token(
        asset_id=asset_id,
        token=token,
        purpose="media_asset_playback",
    )
