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
