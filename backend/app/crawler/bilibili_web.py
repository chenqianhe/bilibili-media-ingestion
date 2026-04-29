from __future__ import annotations

import hashlib
import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_WBI_MIXIN_KEY_ENC_TAB = [
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
]
_WBI_FILTER_CHARS = str.maketrans("", "", "!'()*")
_BILIBILI_ORIGIN = "https://www.bilibili.com"
_HOST_PACERS_LOCK = threading.Lock()
_HOST_PACERS: dict[str, _HostPacer] = {}
_WBI_CACHE_LOCK = threading.Lock()
_WBI_CACHE: dict[tuple[str, str | None], _CachedWbiKeys] = {}


class BilibiliWebError(Exception):
    pass


class BilibiliWebNotFoundError(BilibiliWebError):
    pass


class BilibiliWebRateLimitedError(BilibiliWebError):
    pass


class BilibiliWebTransportError(BilibiliWebError):
    pass


class BilibiliWebResponseError(BilibiliWebError):
    pass


class BilibiliWbiError(BilibiliWebResponseError):
    pass


@dataclass(slots=True)
class _CachedWbiKeys:
    img_key: str
    sub_key: str
    expires_at_monotonic: float


@dataclass(slots=True)
class _HostPacer:
    lock: threading.Lock = field(default_factory=threading.Lock)
    next_ready_monotonic: float = 0.0


def _coerce_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def _stringify_param(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _default_port_for_scheme(scheme: str) -> int | None:
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    return None


def _site_for_host(host: str | None) -> str | None:
    if not host:
        return None
    if host == "localhost" or host.replace(".", "").isdigit():
        return host

    labels = [label for label in host.split(".") if label]
    if len(labels) < 2:
        return host
    return ".".join(labels[-2:])


def _resolve_sec_fetch_site(
    *,
    request_url: httpx.URL,
    referer: str | None,
) -> str | None:
    if not referer:
        return None

    try:
        referer_url = httpx.URL(referer)
    except Exception:
        return None

    request_port = request_url.port or _default_port_for_scheme(request_url.scheme)
    referer_port = referer_url.port or _default_port_for_scheme(referer_url.scheme)
    if (
        request_url.scheme == referer_url.scheme
        and request_url.host == referer_url.host
        and request_port == referer_port
    ):
        return "same-origin"

    request_site = _site_for_host(request_url.host)
    referer_site = _site_for_host(referer_url.host)
    if request_site is not None and request_site == referer_site:
        return "same-site"

    return "cross-site"


def _should_send_bilibili_cookie(*, request_url: httpx.URL) -> bool:
    host = (request_url.host or "").lower()
    return host == "bilibili.com" or host.endswith(".bilibili.com")


def _should_refresh_wbi_signature(*, code: object, url: str) -> bool:
    if code == -352:
        return True
    if code != -403:
        return False
    return "/x/v2/reply/wbi/" in url


class BilibiliWebClient:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        cookie_header: str | None = None,
        min_interval_seconds: float | None = None,
        request_jitter_seconds: float | None = None,
        wbi_key_cache_ttl_seconds: float | None = None,
        random_generator: random.Random | None = None,
        monotonic: Callable[[], float] | None = None,
        epoch_time: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._owns_client = client is None
        self._base_url = base_url or settings.BILIBILI_API_BASE_URL
        self._cookie_header = cookie_header or settings.BILIBILI_COOKIE_HEADER
        self._client = client or httpx.Client(
            base_url=self._base_url,
            timeout=timeout or settings.BILIBILI_METADATA_TIMEOUT_SECONDS,
            headers=self._default_headers(),
        )
        self._min_interval_seconds = max(
            0.0,
            (
                min_interval_seconds
                if min_interval_seconds is not None
                else settings.BILIBILI_REQUEST_MIN_INTERVAL_SECONDS
            ),
        )
        self._request_jitter_seconds = max(
            0.0,
            (
                request_jitter_seconds
                if request_jitter_seconds is not None
                else settings.BILIBILI_REQUEST_JITTER_SECONDS
            ),
        )
        self._wbi_key_cache_ttl_seconds = max(
            0.0,
            (
                wbi_key_cache_ttl_seconds
                if wbi_key_cache_ttl_seconds is not None
                else settings.BILIBILI_WBI_KEY_CACHE_TTL_SECONDS
            ),
        )
        self._random = random_generator or random.Random()
        self._monotonic = monotonic or time.monotonic
        self._epoch_time = epoch_time or time.time
        self._sleep = sleep or time.sleep
        self._apply_default_headers()

    @property
    def base_url(self) -> str:
        return self._base_url

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def request_json(
        self,
        url: str,
        *,
        params: dict[str, object] | None,
        context: str,
        use_wbi: bool = False,
        referer: str | None = None,
        unwrap_bilibili_data: bool = True,
    ) -> Any:
        attempts_remaining = 2 if use_wbi else 1
        while attempts_remaining > 0:
            response = self._request_response(
                url,
                params=params,
                context=context,
                use_wbi=use_wbi,
                referer=referer,
            )

            try:
                payload = response.json()
            except ValueError as exc:
                raise BilibiliWebResponseError(
                    f"Bilibili returned invalid JSON for {context}"
                ) from exc

            if not isinstance(payload, dict):
                raise BilibiliWebResponseError(
                    f"Bilibili returned a non-object response for {context}"
                )

            if not unwrap_bilibili_data:
                return payload

            code = payload.get("code", 0)
            message = _coerce_str(payload.get("message")) or _coerce_str(
                payload.get("msg")
            )
            if code in (0, None):
                return payload.get("data")
            if code in (-404, 62002):
                raise BilibiliWebNotFoundError(
                    f"Bilibili does not have {context}: {message or code}"
                )
            if code == -412:
                raise BilibiliWebRateLimitedError(
                    f"Bilibili rate limited {context}: {message or code}"
                )
            if (
                use_wbi
                and attempts_remaining > 1
                and _should_refresh_wbi_signature(code=code, url=url)
            ):
                logger.warning(
                    "Refreshing WBI keys after invalid signature response for %s",
                    context,
                )
                self.invalidate_wbi_cache()
                attempts_remaining -= 1
                continue

            raise BilibiliWebResponseError(
                f"Bilibili rejected {context} with code {code}: {message or 'unknown error'}"
            )

        raise AssertionError("WBI refresh loop exited unexpectedly")

    def request_text(
        self,
        url: str,
        *,
        context: str,
        params: dict[str, object] | None = None,
        referer: str | None = None,
    ) -> str:
        response = self._request_response(
            url,
            params=params,
            context=context,
            use_wbi=False,
            referer=referer,
        )
        return response.text

    def request_bytes(
        self,
        url: str,
        *,
        context: str,
        params: dict[str, object] | None = None,
        referer: str | None = None,
    ) -> tuple[bytes, str | None]:
        response = self._request_response(
            url,
            params=params,
            context=context,
            use_wbi=False,
            referer=referer,
        )
        return response.content, _coerce_str(response.headers.get("Content-Type"))

    def invalidate_wbi_cache(self) -> None:
        cache_key = (self._base_url, self._cookie_header)
        with _WBI_CACHE_LOCK:
            _WBI_CACHE.pop(cache_key, None)

    def _default_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": settings.BILIBILI_ACCEPT_LANGUAGE,
            "Origin": _BILIBILI_ORIGIN,
            "Referer": f"{_BILIBILI_ORIGIN}/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "User-Agent": settings.BILIBILI_METADATA_USER_AGENT,
        }
        return headers

    def _apply_default_headers(self) -> None:
        for key, value in self._default_headers().items():
            if key not in self._client.headers:
                self._client.headers[key] = value

    def _request_response(
        self,
        url: str,
        *,
        params: dict[str, object] | None,
        context: str,
        use_wbi: bool,
        referer: str | None,
    ) -> httpx.Response:
        resolved_params = dict(params or {})
        if use_wbi:
            resolved_params = self._sign_wbi_params(resolved_params)

        headers = {"Referer": referer} if referer else None
        request = self._client.build_request(
            "GET",
            url,
            params=resolved_params,
            headers=headers,
        )
        if self._cookie_header is not None:
            if _should_send_bilibili_cookie(request_url=request.url):
                request.headers["Cookie"] = self._cookie_header
            else:
                request.headers.pop("Cookie", None)
        sec_fetch_site = _resolve_sec_fetch_site(
            request_url=request.url,
            referer=referer,
        )
        if sec_fetch_site is not None:
            request.headers["Sec-Fetch-Site"] = sec_fetch_site
        self._pace_request(host=request.url.host)

        try:
            response = self._client.send(request)
        except httpx.TransportError as exc:
            raise BilibiliWebTransportError(
                f"Could not fetch {context} from Bilibili"
            ) from exc

        if response.status_code in (412, 429):
            raise BilibiliWebRateLimitedError(
                f"Bilibili temporarily refused {context} with HTTP {response.status_code}"
            )
        if response.status_code == 404:
            raise BilibiliWebNotFoundError(f"Bilibili does not have {context}")
        if response.status_code >= 500:
            raise BilibiliWebTransportError(
                f"Bilibili returned HTTP {response.status_code} for {context}"
            )
        if response.status_code >= 400:
            raise BilibiliWebResponseError(
                f"Bilibili returned HTTP {response.status_code} for {context}"
            )

        return response

    def _pace_request(self, *, host: str | None) -> None:
        if self._min_interval_seconds <= 0 and self._request_jitter_seconds <= 0:
            return

        pacer = self._get_host_pacer(host or self._base_url)
        with pacer.lock:
            now = self._monotonic()
            wait_seconds = pacer.next_ready_monotonic - now
            if wait_seconds > 0:
                logger.debug(
                    "Sleeping %.3fs before Bilibili request to %s",
                    wait_seconds,
                    host,
                )
                self._sleep(wait_seconds)
                now = self._monotonic()

            delay_seconds = self._min_interval_seconds
            if self._request_jitter_seconds > 0:
                delay_seconds += self._random.uniform(0.0, self._request_jitter_seconds)
            pacer.next_ready_monotonic = now + delay_seconds

    def _get_host_pacer(self, host: str) -> _HostPacer:
        with _HOST_PACERS_LOCK:
            pacer = _HOST_PACERS.get(host)
            if pacer is None:
                pacer = _HostPacer()
                _HOST_PACERS[host] = pacer
            return pacer

    def _sign_wbi_params(self, params: dict[str, object]) -> dict[str, object]:
        img_key, sub_key = self._get_wbi_keys()
        mixin_key = self._get_mixin_key(img_key + sub_key)

        filtered_params = {
            key: _stringify_param(value).translate(_WBI_FILTER_CHARS)
            for key, value in params.items()
            if value is not None
        }
        filtered_params["wts"] = str(int(self._epoch_time()))

        query_string = urlencode(sorted(filtered_params.items()))
        w_rid = hashlib.md5(f"{query_string}{mixin_key}".encode()).hexdigest()

        signed_params: dict[str, object] = dict(filtered_params)
        signed_params["w_rid"] = w_rid
        return signed_params

    def _get_wbi_keys(self) -> tuple[str, str]:
        cache_key = (self._base_url, self._cookie_header)
        now = self._monotonic()
        with _WBI_CACHE_LOCK:
            cached = _WBI_CACHE.get(cache_key)
            if cached is not None and cached.expires_at_monotonic > now:
                return cached.img_key, cached.sub_key

        payload = self.request_json(
            "/x/web-interface/nav",
            params={},
            context="WBI navigation metadata",
            unwrap_bilibili_data=True,
            use_wbi=False,
            referer=f"{_BILIBILI_ORIGIN}/",
        )
        if not isinstance(payload, dict):
            raise BilibiliWbiError("Bilibili WBI navigation payload was invalid")

        wbi_img = payload.get("wbi_img")
        if not isinstance(wbi_img, dict):
            raise BilibiliWbiError("Bilibili WBI navigation payload was missing wbi_img")

        img_key = self._extract_wbi_key(_coerce_str(wbi_img.get("img_url")))
        sub_key = self._extract_wbi_key(_coerce_str(wbi_img.get("sub_url")))

        cached = _CachedWbiKeys(
            img_key=img_key,
            sub_key=sub_key,
            expires_at_monotonic=now + self._wbi_key_cache_ttl_seconds,
        )
        with _WBI_CACHE_LOCK:
            _WBI_CACHE[cache_key] = cached
        return img_key, sub_key

    def _extract_wbi_key(self, value: str | None) -> str:
        if value is None:
            raise BilibiliWbiError("Bilibili WBI image key URL was missing")
        stem = PurePosixPath(httpx.URL(value).path).stem
        if not stem:
            raise BilibiliWbiError("Bilibili WBI image key URL was invalid")
        return stem

    def _get_mixin_key(self, original: str) -> str:
        mixed = "".join(
            original[index] for index in _WBI_MIXIN_KEY_ENC_TAB if index < len(original)
        )
        if len(mixed) < 32:
            raise BilibiliWbiError(
                f"Bilibili WBI mixin key was too short: {len(mixed)}"
            )
        return mixed[:32]
