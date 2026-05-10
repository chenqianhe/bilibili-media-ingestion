import httpx

from app.services.bilibili import (
    BilibiliShortLinkResolveError,
    extract_bilibili_short_urls,
    extract_bvid,
    resolve_bvid,
)
from tests.utils.utils import random_bvid


def test_extract_bvid_from_link() -> None:
    bvid = random_bvid()
    extracted = extract_bvid(f"https://www.bilibili.com/video/{bvid}/?spm_id_from=333")
    assert extracted == bvid


def test_extract_bvid_from_plain_text() -> None:
    bvid = random_bvid()
    extracted = extract_bvid(f"请处理这个视频 {bvid}")
    assert extracted == bvid


def test_extract_bvid_returns_none_for_invalid_input() -> None:
    assert extract_bvid("not-a-bvid") is None


def test_extract_bilibili_short_urls_from_shared_text() -> None:
    assert extract_bilibili_short_urls(
        "【你好，我是李白老师的梦女-哔哩哔哩】 https://b23.tv/wDgpwx5。"
    ) == ["https://b23.tv/wDgpwx5"]


def test_resolve_bvid_prefers_direct_bvid_without_short_link_request() -> None:
    bvid = random_bvid()

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("direct BVID input should not perform HTTP requests")

    client = httpx.Client(transport=httpx.MockTransport(handler))

    assert resolve_bvid(f"https://www.bilibili.com/video/{bvid}", client=client) == bvid


def test_resolve_bvid_from_b23_short_link_redirect() -> None:
    bvid = random_bvid()
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            302,
            headers={
                "Location": f"https://www.bilibili.com/video/{bvid}/?spm_id_from=333"
            },
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    assert (
        resolve_bvid(
            "【你好，我是李白老师的梦女-哔哩哔哩】 https://b23.tv/wDgpwx5",
            client=client,
        )
        == bvid
    )
    assert requested_urls == ["https://b23.tv/wDgpwx5"]


def test_resolve_bvid_does_not_fetch_non_bilibili_short_urls() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("non-bilibili short URL should not be fetched")

    client = httpx.Client(transport=httpx.MockTransport(handler))

    assert resolve_bvid("https://example.com/BiliShortCode", client=client) is None


def test_resolve_bvid_rejects_short_link_redirect_to_non_bilibili_host() -> None:
    bvid = random_bvid()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": f"https://example.com/video/{bvid}"},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    assert resolve_bvid("https://b23.tv/wDgpwx5", client=client) is None


def test_resolve_bvid_raises_for_short_link_resolution_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    try:
        resolve_bvid("https://b23.tv/wDgpwx5", client=client)
    except BilibiliShortLinkResolveError:
        return

    raise AssertionError("expected short link resolution failure")
