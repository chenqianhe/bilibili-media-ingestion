from datetime import date

import httpx
import pytest

from app.core.config import settings
from app.crawler.bilibili_auxiliary_http import BilibiliHttpAuxiliaryProvider

_WBI_IMG_KEY = "0123456789abcdef0123456789abcdef"
_WBI_SUB_KEY = "fedcba9876543210fedcba9876543210"


def _encode_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("Negative protobuf varints are not supported in this test helper")

    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _encode_varint_field(field_number: int, value: int) -> bytes:
    return _encode_varint((field_number << 3) | 0) + _encode_varint(value)


def _encode_string_field(field_number: int, value: str) -> bytes:
    encoded_value = value.encode("utf-8")
    return (
        _encode_varint((field_number << 3) | 2)
        + _encode_varint(len(encoded_value))
        + encoded_value
    )


def _encode_message_field(field_number: int, payload: bytes) -> bytes:
    return (
        _encode_varint((field_number << 3) | 2)
        + _encode_varint(len(payload))
        + payload
    )


def _build_danmaku_elem(
    *,
    entry_id: int,
    progress: int,
    mode: int,
    font_size: int,
    color: int,
    content: str,
    ctime: int,
) -> bytes:
    return b"".join(
        [
            _encode_varint_field(1, entry_id),
            _encode_varint_field(2, progress),
            _encode_varint_field(3, mode),
            _encode_varint_field(4, font_size),
            _encode_varint_field(5, color),
            _encode_string_field(7, content),
            _encode_varint_field(8, ctime),
        ]
    )


def _build_danmaku_history_segment(*entries: bytes) -> bytes:
    return b"".join(_encode_message_field(1, entry) for entry in entries)


@pytest.fixture(autouse=True)
def _disable_bilibili_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "BILIBILI_REQUEST_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(settings, "BILIBILI_REQUEST_JITTER_SECONDS", 0.0)


def test_fetch_video_comments_flattens_nested_replies() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/x/v2/reply/count":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"count": 4}},
            )
        if request.url.path == "/x/web-interface/nav":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "wbi_img": {
                            "img_url": f"https://i0.hdslb.com/bfs/wbi/{_WBI_IMG_KEY}.png",
                            "sub_url": f"https://i0.hdslb.com/bfs/wbi/{_WBI_SUB_KEY}.png",
                        }
                    },
                },
            )
        if request.url.path == "/x/v2/reply/wbi/main":
            assert request.url.params["pagination_str"] == "{\"offset\":\"\"}"
            assert request.url.params["w_rid"]
            assert request.url.params["wts"]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "cursor": {
                            "is_end": True,
                            "pagination_reply": {"next_offset": ""},
                            "all_count": 7,
                        },
                        "top": {
                            "admin": {
                                "rpid": 808,
                                "root": 0,
                                "parent": 0,
                                "like": 8,
                                "rcount": 0,
                                "ctime": 1735774500,
                                "member": {"mid": "10", "uname": "Admin Reply"},
                                "content": {"message": "Admin notice"},
                            },
                            "upper": {
                                "rpid": 909,
                                "root": 0,
                                "parent": 0,
                                "like": 9,
                                "rcount": 0,
                                "ctime": 1735775000,
                                "member": {"mid": "11", "uname": "Upper Reply"},
                                "content": {"message": "UP notice"},
                            },
                            "vote": None,
                        },
                        "top_replies": [
                            {
                                "rpid": 707,
                                "root": 0,
                                "parent": 0,
                                "like": 7,
                                "rcount": 0,
                                "ctime": 1735775500,
                                "member": {"mid": "12", "uname": "Top Reply"},
                                "content": {"message": "Pinned reply"},
                            }
                        ],
                        "hots": [
                            {
                                "rpid": 606,
                                "root": 0,
                                "parent": 0,
                                "like": 6,
                                "rcount": 0,
                                "ctime": 1735775600,
                                "member": {"mid": "13", "uname": "Hot Reply"},
                                "content": {"message": "Hot reply"},
                            }
                        ],
                        "replies": [
                            {
                                "rpid": 101,
                                "root": 0,
                                "parent": 0,
                                "like": 3,
                                "rcount": 2,
                                "ctime": 1735776000,
                                "member": {"mid": "42", "uname": "Uploader 42"},
                                "content": {
                                    "message": "Top level reply",
                                    "pictures": [
                                        {
                                            "img_src": "//i0.hdslb.com/bfs/reply/example-1.jpg",
                                            "img_width": 1280,
                                            "img_height": 720,
                                        }
                                    ],
                                },
                                "folder": {"has_folded": True, "is_folded": True},
                                "replies": [],
                            }
                        ],
                    },
                },
            )
        if request.url.path == "/x/v2/reply/reply":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "page": {"count": 2, "size": 10},
                        "top_replies": [
                            {
                                "rpid": 202,
                                "root": 101,
                                "parent": 101,
                                "like": 1,
                                "rcount": 0,
                                "ctime": 1735777000,
                                "member": {"mid": "99", "uname": "Pinned Child"},
                                "content": {
                                    "message": "Pinned child",
                                    "pictures": [
                                        {
                                            "img_src": "//i0.hdslb.com/bfs/reply/example-2.png",
                                            "width": 512,
                                            "height": 512,
                                        }
                                    ],
                                },
                            }
                        ],
                        "replies": [
                            {
                                "rpid": 303,
                                "root": 101,
                                "parent": 101,
                                "like": 0,
                                "rcount": 0,
                                "ctime": 1735778000,
                                "member": {"mid": "100", "uname": "Fetched Child"},
                                "content": {
                                    "message": "Fetched child",
                                    "pictures": [
                                        {
                                            "img_src": "//i0.hdslb.com/bfs/reply/example-3.webp",
                                            "img_width": 960,
                                            "img_height": 540,
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                },
            )
        return httpx.Response(404, json={"code": -404, "message": "not found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.bilibili.com",
    )
    provider = BilibiliHttpAuxiliaryProvider(
        client=client,
        retry_attempts=1,
        cookie_header="SESSDATA=comment-fetch",
    )

    result = provider.fetch_video_comments(bvid="BV1Q541167Qg", aid=987654321)
    comments = result.comments

    assert {comment.rpid for comment in comments} == {
        101,
        202,
        303,
        606,
        707,
        808,
        909,
    }
    assert {comment.message for comment in comments} == {
        "Top level reply",
        "Pinned child",
        "Fetched child",
        "Pinned reply",
        "Hot reply",
        "Admin notice",
        "UP notice",
    }
    top_level = next(comment for comment in comments if comment.rpid == 101)
    assert len(top_level.images) == 1
    assert top_level.images[0].source_url == "https://i0.hdslb.com/bfs/reply/example-1.jpg"
    assert top_level.images[0].width == 1280
    assert top_level.images[0].height == 720
    pinned_child = next(comment for comment in comments if comment.rpid == 202)
    assert len(pinned_child.images) == 1
    assert pinned_child.images[0].source_url == "https://i0.hdslb.com/bfs/reply/example-2.png"
    assert pinned_child.images[0].width == 512
    assert pinned_child.images[0].height == 512
    fetched_child = next(comment for comment in comments if comment.rpid == 303)
    assert len(fetched_child.images) == 1
    assert fetched_child.images[0].source_url == "https://i0.hdslb.com/bfs/reply/example-3.webp"
    assert fetched_child.images[0].width == 960
    assert fetched_child.images[0].height == 540
    assert result.summary.expected_count == 4
    assert result.summary.fetched_count == 7
    assert result.summary.fallback_used is False
    assert result.summary.partial is False
    assert any("/x/v2/reply/count?" in request for request in requests)
    assert any("/x/web-interface/nav" in request for request in requests)
    assert any("/x/v2/reply/wbi/main?" in request for request in requests)
    assert any("/x/v2/reply/reply?" in request for request in requests)


def test_fetch_video_comments_falls_back_to_legacy_upper_and_hots() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/x/v2/reply/count":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"count": 5}},
            )
        if request.url.path == "/x/web-interface/nav":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "wbi_img": {
                            "img_url": f"https://i0.hdslb.com/bfs/wbi/{_WBI_IMG_KEY}.png",
                            "sub_url": f"https://i0.hdslb.com/bfs/wbi/{_WBI_SUB_KEY}.png",
                        }
                    },
                },
            )
        if request.url.path == "/x/v2/reply/wbi/main":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"replies": "invalid"}},
            )
        if request.url.path == "/x/v2/reply":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "page": {"count": 3, "size": 20},
                        "upper": {
                            "top": {
                                "rpid": 401,
                                "root": 0,
                                "parent": 0,
                                "like": 4,
                                "rcount": 0,
                                "ctime": 1735778200,
                                "member": {"mid": "21", "uname": "Upper Top"},
                                "content": {"message": "Upper top"},
                            }
                        },
                        "hots": [
                            {
                                "rpid": 402,
                                "root": 0,
                                "parent": 0,
                                "like": 5,
                                "rcount": 0,
                                "ctime": 1735778300,
                                "member": {"mid": "22", "uname": "Hot Legacy"},
                                "content": {"message": "Hot legacy"},
                            }
                        ],
                        "replies": [
                            {
                                "rpid": 403,
                                "root": 0,
                                "parent": 0,
                                "like": 6,
                                "rcount": 0,
                                "ctime": 1735778400,
                                "member": {"mid": "23", "uname": "Reply Legacy"},
                                "content": {"message": "Reply legacy"},
                            }
                        ],
                    },
                },
            )
        return httpx.Response(404, json={"code": -404, "message": "not found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.bilibili.com",
    )
    provider = BilibiliHttpAuxiliaryProvider(
        client=client,
        retry_attempts=1,
        cookie_header="SESSDATA=comment-fetch",
    )

    result = provider.fetch_video_comments(bvid="BV1Q541167Qg", aid=987654321)
    comments = result.comments

    assert {comment.rpid for comment in comments} == {401, 402, 403}
    assert {comment.message for comment in comments} == {
        "Upper top",
        "Hot legacy",
        "Reply legacy",
    }
    assert result.summary.expected_count == 5
    assert result.summary.fetched_count == 3
    assert result.summary.fallback_used is True
    assert result.summary.partial is True
    assert any("/x/v2/reply/wbi/main?" in request for request in requests)
    assert any("/x/v2/reply?" in request for request in requests)


def test_fetch_video_danmaku_merges_history_and_snapshot() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/x/v2/dm/history/index":
            assert request.url.params["month"] == "2025-01"
            return httpx.Response(
                200,
                json={"code": 0, "data": ["2025-01-02"]},
            )
        if request.url.path == "/x/v2/dm/web/history/seg.so":
            assert request.url.params["date"] == "2025-01-02"
            return httpx.Response(
                200,
                content=_build_danmaku_history_segment(
                    _build_danmaku_elem(
                        entry_id=1001,
                        progress=1500,
                        mode=1,
                        font_size=25,
                        color=16777215,
                        content="Hello from history",
                        ctime=1735776000,
                    )
                ),
                headers={"Content-Type": "application/octet-stream"},
            )
        if request.url.host == "comment.bilibili.com":
            return httpx.Response(
                200,
                text=(
                    "<i>"
                    '<d p="3.0,4,18,255,1735777000,0,hash-b,1002">World</d>'
                    "</i>"
                ),
            )
        return httpx.Response(404, json={"code": -404, "message": "not found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.bilibili.com",
    )
    provider = BilibiliHttpAuxiliaryProvider(client=client, retry_attempts=1)

    result = provider.fetch_video_danmaku(
        bvid="BV1Q541167Qg",
        cid=101,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 3),
    )
    entries = result.entries

    assert [entry.id for entry in entries] == [1001, 1002]
    assert entries[0].time_offset_seconds == 1.5
    assert entries[1].mode == 4
    assert entries[1].content == "World"
    assert entries[0].source == "history_proto"
    assert entries[0].history_date == date(2025, 1, 2)
    assert entries[1].source == "snapshot_xml"
    assert entries[1].history_date is None
    assert result.summary.source == "history_and_snapshot"
    assert result.summary.history_used is True
    assert result.summary.snapshot_used is True
    assert result.summary.indexed_month_count == 1
    assert result.summary.expected_days_count == 1
    assert result.summary.fetched_days_count == 1
    assert result.summary.partial is False
    assert any("/x/v2/dm/history/index?" in request for request in requests)
    assert any("/x/v2/dm/web/history/seg.so?" in request for request in requests)
    assert any("comment.bilibili.com/101.xml" in request for request in requests)


def test_fetch_video_danmaku_falls_back_to_snapshot_when_history_is_unavailable() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/x/v2/dm/history/index":
            return httpx.Response(
                200,
                json={"code": -101, "message": "account not logged in"},
            )
        if request.url.host == "comment.bilibili.com":
            return httpx.Response(
                200,
                text=(
                    "<i>"
                    '<d p="1.5,1,25,16777215,1735776000,0,hash-a,1001">Hello</d>'
                    "</i>"
                ),
            )
        return httpx.Response(404, json={"code": -404, "message": "not found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.bilibili.com",
    )
    provider = BilibiliHttpAuxiliaryProvider(client=client, retry_attempts=1)

    result = provider.fetch_video_danmaku(
        bvid="BV1Q541167Qg",
        cid=101,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 3),
    )
    entries = result.entries

    assert [entry.id for entry in entries] == [1001]
    assert entries[0].source == "snapshot_xml"
    assert result.summary.source == "snapshot_xml"
    assert result.summary.history_used is False
    assert result.summary.snapshot_used is True
    assert result.summary.indexed_month_count == 1
    assert result.summary.expected_days_count is None
    assert result.summary.fetched_days_count == 0
    assert result.summary.partial is True
    assert any("/x/v2/dm/history/index?" in request for request in requests)
    assert any("comment.bilibili.com/101.xml" in request for request in requests)


def test_fetch_video_subtitles_uses_cross_site_headers_for_cdn_tracks() -> None:
    seen_cookies: list[str | None] = []
    seen_sec_fetch_sites: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_cookies.append(request.headers.get("Cookie"))
        seen_sec_fetch_sites.append(request.headers.get("Sec-Fetch-Site"))
        if request.url.path == "/x/player/v2":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "subtitle": {
                            "subtitles": [
                                {
                                    "id": 10,
                                    "lan": "zh-CN",
                                    "lan_doc": "简体中文",
                                    "subtitle_url": (
                                        "//i0.hdslb.com/bfs/subtitle/track.json"
                                    ),
                                }
                            ]
                        }
                    },
                },
            )
        if request.url.host == "i0.hdslb.com":
            return httpx.Response(
                200,
                json={
                    "body": [
                        {"from": 0.0, "to": 1.5, "content": "第一句"},
                        {"from": 2.0, "to": 4.0, "content": "第二句"},
                    ]
                },
            )
        return httpx.Response(404, json={"code": -404, "message": "not found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.bilibili.com",
    )
    provider = BilibiliHttpAuxiliaryProvider(
        client=client,
        retry_attempts=1,
        cookie_header="SESSDATA=session-cookie",
    )

    subtitles = provider.fetch_video_subtitles(bvid="BV1Q541167Qg", cid=101)

    assert len(subtitles) == 1
    assert subtitles[0].lang == "zh-CN"
    assert subtitles[0].source == "bilibili_player_v2"
    assert "00:00:00,000 --> 00:00:01,500" in subtitles[0].content
    assert "第一句" in subtitles[0].content
    assert seen_cookies == [
        "SESSDATA=session-cookie",
        None,
    ]
    assert seen_sec_fetch_sites == [
        "same-site",
        "cross-site",
    ]


def test_fetch_video_subtitles_skips_ai_tracks() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/x/player/v2":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "subtitle": {
                            "subtitles": [
                                {
                                    "id": 10,
                                    "lan": "zh-CN",
                                    "lan_doc": "简体中文",
                                    "subtitle_url": (
                                        "//aisubtitle.hdslb.com/"
                                        "bfs/ai_subtitle/prod/track.json"
                                    ),
                                }
                            ]
                        }
                    },
                },
            )
        if request.url.host == "aisubtitle.hdslb.com":
            raise AssertionError("AI subtitle tracks should be skipped before fetch")
        return httpx.Response(404, json={"code": -404, "message": "not found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.bilibili.com",
    )
    provider = BilibiliHttpAuxiliaryProvider(
        client=client,
        retry_attempts=1,
        cookie_header="SESSDATA=session-cookie",
    )

    subtitles = provider.fetch_video_subtitles(bvid="BV1Q541167Qg", cid=101)

    assert subtitles == []
    assert requests == [
        "https://api.bilibili.com/x/player/v2?bvid=BV1Q541167Qg&cid=101"
    ]


def test_fetch_video_subtitles_skips_tracks_without_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/player/v2":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "subtitle": {
                            "subtitles": [
                                {
                                    "id": 10,
                                    "lan": "zh-CN",
                                    "lan_doc": "简体中文",
                                }
                            ]
                        }
                    },
                },
            )
        return httpx.Response(404, json={"code": -404, "message": "not found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.bilibili.com",
    )
    provider = BilibiliHttpAuxiliaryProvider(
        client=client,
        retry_attempts=1,
        cookie_header="SESSDATA=session-cookie",
    )

    with caplog.at_level("WARNING"):
        subtitles = provider.fetch_video_subtitles(bvid="BV1Q541167Qg", cid=101)

    assert subtitles == []
    assert "Skipping subtitle track without URL" in caplog.text
