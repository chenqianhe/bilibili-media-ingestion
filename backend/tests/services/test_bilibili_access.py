import pytest

from app.services.bilibili_access import (
    SecretStoreError,
    build_cookie_header_from_netscape,
    decrypt_secret_value,
    encrypt_secret_value,
    parse_netscape_cookies,
    summarize_cookie_header,
    summarize_netscape_cookies,
)

NETSCAPE_COOKIES = """# Netscape HTTP Cookie File
.bilibili.com\tTRUE\t/\tTRUE\t2147483647\tSESSDATA\tsession-cookie
.bilibili.com\tTRUE\t/\tTRUE\t2147483647\tbili_jct\tcsrf-token
"""


def test_encrypt_secret_value_round_trip() -> None:
    raw_cookie_header = "SESSDATA=session-cookie; bili_jct=csrf-token"

    encrypted = encrypt_secret_value(raw_cookie_header)

    assert encrypted != raw_cookie_header
    assert decrypt_secret_value(encrypted) == raw_cookie_header


def test_decrypt_secret_value_rejects_tampering() -> None:
    encrypted = encrypt_secret_value("SESSDATA=session-cookie")
    tampered = encrypted[:-1] + ("A" if encrypted[-1] != "A" else "B")

    with pytest.raises(SecretStoreError):
        decrypt_secret_value(tampered)


def test_summarize_cookie_header_masks_values() -> None:
    summary = summarize_cookie_header("SESSDATA=session-cookie; bili_jct=csrf-token")

    assert summary == "SESSDATA=***; bili_jct=***"


def test_parse_netscape_cookies_requires_standard_header() -> None:
    with pytest.raises(SecretStoreError):
        parse_netscape_cookies("SESSDATA=session-cookie; bili_jct=csrf-token")


def test_build_cookie_header_from_netscape_selects_matching_cookies() -> None:
    cookie_header = build_cookie_header_from_netscape(NETSCAPE_COOKIES)

    assert cookie_header == "SESSDATA=session-cookie; bili_jct=csrf-token"


def test_summarize_netscape_cookies_lists_cookie_names() -> None:
    summary = summarize_netscape_cookies(NETSCAPE_COOKIES)

    assert summary == "2 cookies (SESSDATA, bili_jct)"
