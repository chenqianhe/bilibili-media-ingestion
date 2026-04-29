import httpx
import pytest

from app.core.config import settings
from app.crawler.bilibili_metadata import BilibiliMetadataNotFoundError
from app.crawler.bilibili_metadata_http import BilibiliHttpMetadataProvider

_WBI_IMG_KEY = "0123456789abcdef0123456789abcdef"
_WBI_SUB_KEY = "fedcba9876543210fedcba9876543210"
_WBI_IMG_KEY_ALT = "00112233445566778899aabbccddeeff"
_WBI_SUB_KEY_ALT = "ffeeddccbbaa99887766554433221100"


@pytest.fixture(autouse=True)
def _disable_bilibili_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "BILIBILI_REQUEST_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(settings, "BILIBILI_REQUEST_JITTER_SECONDS", 0.0)


def test_fetch_video_metadata_normalizes_bilibili_api_payload() -> None:
    bvid = "BV1Q541167Qg"
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
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
        if request.url.path == "/x/web-interface/wbi/view":
            assert request.url.params["w_rid"]
            assert request.url.params["wts"]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "bvid": bvid,
                        "aid": 987654321,
                        "title": "Example title",
                        "desc": "Example description",
                        "duration": 330,
                        "pubdate": 1735776000,
                        "owner": {
                            "mid": 42,
                            "name": "Uploader 42",
                            "face": "https://example.com/avatar.jpg",
                        },
                        "pic": "https://example.com/cover.jpg",
                        "tname": "tech",
                        "stat": {
                            "view": 1200,
                            "like": 45,
                            "coin": 10,
                            "favorite": 8,
                            "reply": 12,
                            "share": 3,
                            "danmaku": 5,
                        },
                        "pages": [
                            {
                                "cid": 101,
                                "page": 1,
                                "part": "Part 1",
                                "duration": 120,
                            },
                            {
                                "cid": 202,
                                "page": 2,
                                "part": "Part 2",
                                "duration": 210,
                            },
                        ],
                    },
                },
            )
        if request.url.path == "/x/tag/archive/tags":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": [
                        {"tag_name": "tech"},
                        {"tag_name": "archive"},
                        {"tag_name": "tech"},
                    ],
                },
            )
        return httpx.Response(404, json={"code": -404, "message": "not found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.bilibili.com",
    )
    provider = BilibiliHttpMetadataProvider(
        client=client,
        retry_attempts=1,
        cookie_header="SESSDATA=metadata-normalize",
    )

    metadata = provider.fetch_video_metadata(bvid=bvid)

    assert metadata.bvid == bvid
    assert metadata.aid == 987654321
    assert metadata.title == "Example title"
    assert metadata.owner is not None
    assert metadata.owner.mid == 42
    assert metadata.tags == ["tech", "archive"]
    assert [page.cid for page in metadata.pages] == [101, 202]
    assert metadata.stat.view_count == 1200
    assert len(requests) == 3


def test_fetch_video_metadata_falls_back_to_legacy_view_when_wbi_view_fails() -> None:
    bvid = "BV1Q541167Qg"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/web-interface/nav":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "wbi_img": {
                            "img_url": f"https://i0.hdslb.com/bfs/wbi/{_WBI_IMG_KEY_ALT}.png",
                            "sub_url": f"https://i0.hdslb.com/bfs/wbi/{_WBI_SUB_KEY_ALT}.png",
                        }
                    },
                },
            )
        if request.url.path == "/x/web-interface/wbi/view":
            return httpx.Response(404, json={"code": -404, "message": "missing"})
        if request.url.path == "/x/web-interface/view":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "bvid": bvid,
                        "aid": 987654321,
                        "title": "Example title",
                        "pages": [],
                    },
                },
            )
        if request.url.path == "/x/tag/archive/tags":
            return httpx.Response(500, json={"code": -500, "message": "upstream"})
        return httpx.Response(404, json={"code": -404, "message": "not found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.bilibili.com",
    )
    provider = BilibiliHttpMetadataProvider(
        client=client,
        retry_attempts=1,
        cookie_header="SESSDATA=metadata-fallback",
    )

    metadata = provider.fetch_video_metadata(bvid=bvid)

    assert metadata.tags == []
    assert metadata.title == "Example title"


def test_fetch_video_metadata_raises_not_found_for_missing_video() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
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
        return httpx.Response(404, json={"code": -404, "message": "not found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.bilibili.com",
    )
    provider = BilibiliHttpMetadataProvider(
        client=client,
        retry_attempts=1,
        cookie_header="SESSDATA=metadata-missing",
    )

    with pytest.raises(BilibiliMetadataNotFoundError):
        provider.fetch_video_metadata(bvid="BV1Q541167Qg")


def test_fetch_video_metadata_adds_cookie_header() -> None:
    bvid = "BV1Q541167Qg"
    seen_cookies: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_cookies.append(request.headers.get("Cookie"))
        if request.url.path == "/x/web-interface/nav":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "wbi_img": {
                            "img_url": f"https://i0.hdslb.com/bfs/wbi/{_WBI_IMG_KEY_ALT}.png",
                            "sub_url": f"https://i0.hdslb.com/bfs/wbi/{_WBI_SUB_KEY_ALT}.png",
                        }
                    },
                },
            )
        if request.url.path == "/x/web-interface/wbi/view":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "bvid": bvid,
                        "aid": 987654321,
                        "title": "Example title",
                        "pages": [],
                    },
                },
            )
        if request.url.path == "/x/tag/archive/tags":
            return httpx.Response(200, json={"code": 0, "data": []})
        return httpx.Response(404, json={"code": -404, "message": "not found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.bilibili.com",
    )
    provider = BilibiliHttpMetadataProvider(
        client=client,
        retry_attempts=1,
        cookie_header="SESSDATA=session-cookie",
    )

    metadata = provider.fetch_video_metadata(bvid=bvid)

    assert metadata.bvid == bvid
    assert seen_cookies == [
        "SESSDATA=session-cookie",
        "SESSDATA=session-cookie",
        "SESSDATA=session-cookie",
    ]
