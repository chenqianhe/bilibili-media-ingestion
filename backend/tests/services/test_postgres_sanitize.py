from app.crawler.bilibili_auxiliary import BilibiliDanmakuMetadata
from app.services.auxiliary_ingest import _incoming_danmaku_fallback_key
from app.services.image_asset_ingest import strip_url_fields
from app.services.postgres_sanitize import (
    sanitize_postgres_json,
    sanitize_postgres_text,
)


def test_sanitize_postgres_text_strips_nul_bytes() -> None:
    assert sanitize_postgres_text("ab\x00cd") == "abcd"
    assert sanitize_postgres_text(None) is None


def test_sanitize_postgres_json_strips_nested_nul_bytes() -> None:
    payload = {
        "plain": "ab\x00cd",
        "nu\x00l_key": ["x\x00y", {"nested": "z\x00"}],
    }

    assert sanitize_postgres_json(payload) == {
        "plain": "abcd",
        "nul_key": ["xy", {"nested": "z"}],
    }


def test_strip_url_fields_also_strips_nul_bytes_from_retained_payload() -> None:
    payload = {
        "img_src": "https://example.com/image.jpg",
        "message": "hello\x00world",
        "nu\x00l_key": "kept\x00value",
    }

    assert strip_url_fields(payload) == {
        "message": "helloworld",
        "nul_key": "keptvalue",
    }


def test_danmaku_fallback_key_strips_nul_bytes_from_content() -> None:
    entry = BilibiliDanmakuMetadata(
        cid=101,
        time_offset_seconds=1.5,
        content="hel\x00lo",
    )

    assert _incoming_danmaku_fallback_key(entry)[-1] == "hello"
