import uuid

from app.services.storage_keys import (
    build_asset_storage_key,
    build_avatar_storage_key,
    build_cover_storage_key,
)


def test_build_asset_storage_key_uses_stable_ids() -> None:
    asset_id = uuid.uuid4()
    key = build_asset_storage_key(
        asset_type="source_archive",
        bvid="BV1Q541167Qg",
        cid=123456,
        asset_id=asset_id,
        filename="My Great Title.mp4",
    )
    assert key == (
        f"media/source/bvid=BV1Q541167Qg/cid=123456/asset_id={asset_id}/"
        "My Great Title.mp4"
    )


def test_build_cover_and_avatar_keys() -> None:
    assert build_cover_storage_key(bvid="BV1Q541167Qg") == "images/covers/bvid=BV1Q541167Qg/cover.jpg"
    assert build_avatar_storage_key(owner_mid=42) == "images/avatars/mid=42/avatar.jpg"


def test_build_comment_image_asset_storage_key() -> None:
    asset_id = uuid.uuid4()
    key = build_asset_storage_key(
        asset_type="comment_image",
        bvid="BV1Q541167Qg",
        cid=None,
        asset_id=asset_id,
        filename="reply-image.png",
    )
    assert key == (
        f"images/comments/bvid=BV1Q541167Qg/cid=unknown/asset_id={asset_id}/"
        "reply-image.png"
    )
