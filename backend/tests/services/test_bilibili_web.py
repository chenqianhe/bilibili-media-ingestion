import httpx
import pytest

from app.crawler.bilibili_web import BilibiliWebClient, BilibiliWebResponseError


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def epoch(self) -> float:
        return 1_735_776_000.0

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


_WBI_IMG_KEY = "0123456789abcdef0123456789abcdef"
_WBI_SUB_KEY = "fedcba9876543210fedcba9876543210"
_WBI_IMG_KEY_ALT = "00112233445566778899aabbccddeeff"
_WBI_SUB_KEY_ALT = "ffeeddccbbaa99887766554433221100"


def test_request_json_signs_wbi_requests_and_reuses_cached_keys() -> None:
    nav_requests = 0
    signed_queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal nav_requests
        if request.url.path == "/x/web-interface/nav":
            nav_requests += 1
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
            signed_queries.append(dict(request.url.params.multi_items()))
            return httpx.Response(
                200,
                json={"code": 0, "data": {"replies": []}},
            )
        return httpx.Response(404, json={"code": -404, "message": "not found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://sign.example",
    )
    web_client = BilibiliWebClient(
        client=client,
        cookie_header="SESSDATA=wbi-sign-cache",
        min_interval_seconds=0.0,
        request_jitter_seconds=0.0,
        epoch_time=lambda: 1_735_776_000.0,
    )

    first = web_client.request_json(
        "/x/v2/reply/wbi/main",
        params={"oid": 123, "type": 1},
        context="signed request one",
        use_wbi=True,
    )
    second = web_client.request_json(
        "/x/v2/reply/wbi/main",
        params={"oid": 123, "type": 1},
        context="signed request two",
        use_wbi=True,
    )

    assert first == {"replies": []}
    assert second == {"replies": []}
    assert nav_requests == 1
    assert len(signed_queries) == 2
    assert signed_queries[0]["wts"] == "1735776000"
    assert signed_queries[0]["w_rid"]
    assert signed_queries[0]["w_rid"] == signed_queries[1]["w_rid"]


def test_request_json_refreshes_wbi_cache_after_invalid_signature() -> None:
    nav_requests = 0
    main_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal nav_requests, main_requests
        if request.url.path == "/x/web-interface/nav":
            nav_requests += 1
            suffix = "first" if nav_requests == 1 else "second"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "wbi_img": {
                            "img_url": (
                                f"https://i0.hdslb.com/bfs/wbi/"
                                f"{_WBI_IMG_KEY if suffix == 'first' else _WBI_IMG_KEY_ALT}.png"
                            ),
                            "sub_url": (
                                f"https://i0.hdslb.com/bfs/wbi/"
                                f"{_WBI_SUB_KEY if suffix == 'first' else _WBI_SUB_KEY_ALT}.png"
                            ),
                        }
                    },
                },
            )
        if request.url.path == "/x/v2/reply/wbi/main":
            main_requests += 1
            if main_requests == 1:
                return httpx.Response(
                    200,
                    json={"code": -352, "message": "invalid wbi"},
                )
            return httpx.Response(
                200,
                json={"code": 0, "data": {"replies": [{"rpid": 1}]}},
            )
        return httpx.Response(404, json={"code": -404, "message": "not found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://refresh-invalid.example",
    )
    web_client = BilibiliWebClient(
        client=client,
        cookie_header="SESSDATA=wbi-refresh-invalid",
        min_interval_seconds=0.0,
        request_jitter_seconds=0.0,
        epoch_time=lambda: 1_735_776_100.0,
    )

    payload = web_client.request_json(
        "/x/v2/reply/wbi/main",
        params={"oid": 456, "type": 1},
        context="refresh request",
        use_wbi=True,
    )

    assert payload == {"replies": [{"rpid": 1}]}
    assert nav_requests == 2
    assert main_requests == 2


def test_request_json_refreshes_wbi_cache_after_comment_wbi_forbidden_response() -> None:
    nav_requests = 0
    main_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal nav_requests, main_requests
        if request.url.path == "/x/web-interface/nav":
            nav_requests += 1
            suffix = "first" if nav_requests == 1 else "second"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "wbi_img": {
                            "img_url": (
                                f"https://i0.hdslb.com/bfs/wbi/"
                                f"{_WBI_IMG_KEY if suffix == 'first' else _WBI_IMG_KEY_ALT}.png"
                            ),
                            "sub_url": (
                                f"https://i0.hdslb.com/bfs/wbi/"
                                f"{_WBI_SUB_KEY if suffix == 'first' else _WBI_SUB_KEY_ALT}.png"
                            ),
                        }
                    },
                },
            )
        if request.url.path == "/x/v2/reply/wbi/main":
            main_requests += 1
            if main_requests == 1:
                return httpx.Response(
                    200,
                    json={"code": -403, "message": "invalid wbi"},
                )
            return httpx.Response(
                200,
                json={"code": 0, "data": {"replies": [{"rpid": 1}]}},
            )
        return httpx.Response(404, json={"code": -404, "message": "not found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://reply-forbidden.example",
    )
    web_client = BilibiliWebClient(
        client=client,
        cookie_header="SESSDATA=wbi-refresh-reply-forbidden",
        min_interval_seconds=0.0,
        request_jitter_seconds=0.0,
        epoch_time=lambda: 1_735_776_200.0,
    )

    payload = web_client.request_json(
        "/x/v2/reply/wbi/main",
        params={"oid": 789, "type": 1},
        context="comment refresh request",
        use_wbi=True,
    )

    assert payload == {"replies": [{"rpid": 1}]}
    assert nav_requests == 2
    assert main_requests == 2


def test_request_json_does_not_refresh_wbi_cache_after_non_comment_wbi_forbidden_response() -> None:
    nav_requests = 0
    view_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal nav_requests, view_requests
        if request.url.path == "/x/web-interface/nav":
            nav_requests += 1
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
            view_requests += 1
            return httpx.Response(
                200,
                json={"code": -403, "message": "forbidden"},
            )
        return httpx.Response(404, json={"code": -404, "message": "not found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://view-forbidden.example",
    )
    web_client = BilibiliWebClient(
        client=client,
        cookie_header="SESSDATA=wbi-refresh-view-forbidden",
        min_interval_seconds=0.0,
        request_jitter_seconds=0.0,
        epoch_time=lambda: 1_735_776_300.0,
    )

    with pytest.raises(BilibiliWebResponseError):
        web_client.request_json(
            "/x/web-interface/wbi/view",
            params={"bvid": "BV1Q541167Qg"},
            context="metadata request",
            use_wbi=True,
        )

    assert nav_requests == 1
    assert view_requests == 1


def test_request_text_omits_managed_cookie_for_non_bilibili_hosts() -> None:
    seen_cookies: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_cookies.append(request.headers.get("Cookie"))
        return httpx.Response(200, text="ok")

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.bilibili.com",
    )
    web_client = BilibiliWebClient(
        client=client,
        cookie_header="SESSDATA=scoped-cookie",
        min_interval_seconds=0.0,
        request_jitter_seconds=0.0,
    )

    first = web_client.request_text("/x/web-interface/nav", context="api request")
    second = web_client.request_text(
        "https://i0.hdslb.com/bfs/subtitle/track.json",
        context="cdn request",
    )

    assert first == "ok"
    assert second == "ok"
    assert seen_cookies == [
        "SESSDATA=scoped-cookie",
        None,
    ]


def test_request_text_applies_minimum_interval_between_requests() -> None:
    clock = _FakeClock()
    request_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, text=f"ok-{request_count}")

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://pace.example",
    )
    web_client = BilibiliWebClient(
        client=client,
        min_interval_seconds=1.5,
        request_jitter_seconds=0.0,
        monotonic=clock.monotonic,
        epoch_time=clock.epoch,
        sleep=clock.sleep,
    )

    first = web_client.request_text("/one", context="first text request")
    second = web_client.request_text("/two", context="second text request")

    assert first == "ok-1"
    assert second == "ok-2"
    assert request_count == 2
    assert clock.sleeps == [1.5]
