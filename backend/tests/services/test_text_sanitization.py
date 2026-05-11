from app.crawler.bilibili_auxiliary import BilibiliDanmakuMetadata
from app.services.auxiliary_ingest import _incoming_danmaku_fallback_key
from app.services.image_asset_ingest import strip_url_fields
from app.services.text_sanitization import strip_nul_bytes, strip_nul_text


def test_strip_nul_text_removes_postgres_forbidden_nul_bytes() -> None:
    assert strip_nul_text("Uploader\x00 42") == "Uploader 42"
    assert strip_nul_text(None) is None


def test_strip_nul_bytes_recursively_sanitizes_json_payloads() -> None:
    payload = {
        "bad\x00key": [
            "x\x00",
            {
                "nested": "va\x00lue",
                "items": ("a\x00", 1, None),
            },
        ],
        "count": 1,
    }

    assert strip_nul_bytes(payload) == {
        "badkey": [
            "x",
            {
                "nested": "value",
                "items": ("a", 1, None),
            },
        ],
        "count": 1,
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
