import re
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import settings

BVID_RE = re.compile(r"(?i)(BV[0-9A-Za-z]{10})")
URL_RE = re.compile(r"https?://[^\s<>\]\)）】》\"']+", re.IGNORECASE)
BILIBILI_SHORT_LINK_HOSTS = {"b23.tv", "bili2233.cn"}
BILIBILI_HOST = "bilibili.com"
TRAILING_URL_PUNCTUATION = ".,;:!?，。；：！？)]}）】》\"'"
SHORT_LINK_REDIRECT_LIMIT = 5


class BilibiliShortLinkResolveError(Exception):
    pass


def extract_bvid(input_text: str) -> str | None:
    match = BVID_RE.search(input_text.strip())
    if not match:
        return None
    bvid = match.group(1)
    return f"BV{bvid[2:]}"


def extract_bilibili_short_urls(input_text: str) -> list[str]:
    short_urls: list[str] = []
    for match in URL_RE.finditer(input_text.strip()):
        candidate = match.group(0).rstrip(TRAILING_URL_PUNCTUATION)
        if is_bilibili_short_url(candidate):
            short_urls.append(candidate)
    return short_urls


def is_bilibili_short_url(value: str) -> bool:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and hostname in BILIBILI_SHORT_LINK_HOSTS


def is_bilibili_url(value: str) -> bool:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and (
        hostname == BILIBILI_HOST or hostname.endswith(f".{BILIBILI_HOST}")
    )


def resolve_bvid(
    input_text: str,
    *,
    client: httpx.Client | None = None,
) -> str | None:
    bvid = extract_bvid(input_text)
    if bvid is not None:
        return bvid

    for short_url in extract_bilibili_short_urls(input_text):
        bvid = resolve_bilibili_short_url(short_url, client=client)
        if bvid is not None:
            return bvid

    return None


def resolve_bilibili_short_url(
    short_url: str,
    *,
    client: httpx.Client | None = None,
) -> str | None:
    current_url = short_url.strip()
    if not is_bilibili_short_url(current_url):
        return None

    owns_client = client is None
    resolved_client = client or _build_short_link_client()
    try:
        for _ in range(SHORT_LINK_REDIRECT_LIMIT):
            if not is_bilibili_short_url(current_url):
                if is_bilibili_url(current_url):
                    return extract_bvid(current_url)
                return None

            try:
                response = resolved_client.get(current_url, follow_redirects=False)
            except httpx.HTTPError as exc:
                raise BilibiliShortLinkResolveError(
                    "Could not resolve Bilibili short link"
                ) from exc

            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise BilibiliShortLinkResolveError(
                        "Bilibili short link redirect did not include a location"
                    )
                current_url = urljoin(str(response.url), location)
                continue

            if response.status_code == 404:
                return None
            if response.status_code >= 400:
                raise BilibiliShortLinkResolveError(
                    f"Bilibili short link returned HTTP {response.status_code}"
                )
            return None
    finally:
        if owns_client:
            resolved_client.close()

    raise BilibiliShortLinkResolveError("Bilibili short link redirected too many times")


def _build_short_link_client() -> httpx.Client:
    return httpx.Client(
        timeout=settings.BILIBILI_METADATA_TIMEOUT_SECONDS,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": settings.BILIBILI_ACCEPT_LANGUAGE,
            "User-Agent": settings.BILIBILI_METADATA_USER_AGENT,
        },
    )
