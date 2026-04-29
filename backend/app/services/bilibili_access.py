from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal
from urllib.parse import urlparse

from sqlmodel import Session

from app.core.config import settings
from app.models import AppSecret, BilibiliAccessStatusPublic
from app.services.audit import record_audit_event

logger = logging.getLogger(__name__)

_LEGACY_COOKIE_HEADER_SECRET_KEY = "bilibili.cookie_header"
_NETSCAPE_COOKIES_SECRET_KEY = "bilibili.cookies_netscape"
_DOWNLOAD_USER_AGENT_SECRET_KEY = "bilibili.download_user_agent"
_COOKIE_FILE_HEADERS = {"# HTTP Cookie File", "# Netscape HTTP Cookie File"}
_METADATA_COOKIE_URLS = (
    "https://www.bilibili.com/",
    "https://api.bilibili.com/",
)
_SECRET_FORMAT_VERSION = 1
_SECRET_NONCE_BYTES = 16
_SECRET_MAC_BYTES = 32


class SecretStoreError(ValueError):
    pass


@dataclass(frozen=True)
class NetscapeCookie:
    domain: str
    include_subdomains: bool
    path: str
    secure: bool
    expires_at: int
    name: str
    value: str


@dataclass(frozen=True)
class BilibiliAccessRuntime:
    cookie_header: str | None
    download_cookies_text: str | None
    download_user_agent: str | None
    has_database_override: bool
    effective_cookie_source: Literal["database", "environment", "none"]
    cookie_header_summary: str | None
    netscape_cookie_summary: str | None
    download_user_agent_summary: str | None
    yt_dlp_cookies_file_configured: bool
    yt_dlp_cookies_from_browser_configured: bool
    yt_dlp_impersonate_configured: bool
    database_cookie_updated_by: str | None
    database_cookie_updated_at: datetime | None
    warnings: tuple[str, ...]

    @property
    def metadata_cookie_configured(self) -> bool:
        return self.cookie_header is not None

    @property
    def download_auth_configured(self) -> bool:
        return (
            self.download_cookies_text is not None
            or self.yt_dlp_cookies_file_configured
            or self.yt_dlp_cookies_from_browser_configured
        )

    @property
    def download_user_agent_configured(self) -> bool:
        return self.download_user_agent is not None

    def to_public(self) -> BilibiliAccessStatusPublic:
        return BilibiliAccessStatusPublic(
            metadata_cookie_configured=self.metadata_cookie_configured,
            download_auth_configured=self.download_auth_configured,
            has_database_override=self.has_database_override,
            effective_cookie_source=self.effective_cookie_source,
            cookie_header_summary=self.cookie_header_summary,
            netscape_cookie_summary=self.netscape_cookie_summary,
            download_user_agent_summary=self.download_user_agent_summary,
            download_user_agent_configured=self.download_user_agent_configured,
            yt_dlp_cookies_file_configured=self.yt_dlp_cookies_file_configured,
            yt_dlp_cookies_from_browser_configured=(
                self.yt_dlp_cookies_from_browser_configured
            ),
            yt_dlp_impersonate_configured=self.yt_dlp_impersonate_configured,
            database_cookie_updated_by=self.database_cookie_updated_by,
            database_cookie_updated_at=self.database_cookie_updated_at,
            warnings=list(self.warnings),
        )


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _derive_secret_keys() -> tuple[bytes, bytes]:
    material = hashlib.sha512(f"bilibili-access::{settings.SECRET_KEY}".encode()).digest()
    return material[:32], material[32:]


def _build_keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    blocks = bytearray()
    counter = 0
    while len(blocks) < length:
        block = hmac.new(
            key,
            nonce + counter.to_bytes(4, "big"),
            hashlib.sha256,
        ).digest()
        blocks.extend(block)
        counter += 1
    return bytes(blocks[:length])


def _xor_bytes(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right, strict=True))


def encrypt_secret_value(value: str) -> str:
    plaintext = value.encode("utf-8")
    if not plaintext:
        raise SecretStoreError("Secret value cannot be empty")

    nonce = secrets.token_bytes(_SECRET_NONCE_BYTES)
    encryption_key, mac_key = _derive_secret_keys()
    ciphertext = _xor_bytes(
        plaintext,
        _build_keystream(encryption_key, nonce, len(plaintext)),
    )
    body = bytes([_SECRET_FORMAT_VERSION]) + nonce + ciphertext
    mac = hmac.new(mac_key, body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(body + mac).decode("ascii")


def decrypt_secret_value(token: str) -> str:
    try:
        payload = base64.urlsafe_b64decode(token.encode("ascii"))
    except Exception as exc:  # pragma: no cover - base64 raises multiple error types
        raise SecretStoreError("Stored secret is not valid base64") from exc

    if len(payload) <= 1 + _SECRET_NONCE_BYTES + _SECRET_MAC_BYTES:
        raise SecretStoreError("Stored secret payload is truncated")

    version = payload[0]
    if version != _SECRET_FORMAT_VERSION:
        raise SecretStoreError(f"Unsupported stored secret version: {version}")

    mac_offset = len(payload) - _SECRET_MAC_BYTES
    body = payload[:mac_offset]
    mac = payload[mac_offset:]
    encryption_key, mac_key = _derive_secret_keys()
    expected_mac = hmac.new(mac_key, body, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected_mac):
        raise SecretStoreError("Stored secret integrity check failed")

    nonce = body[1 : 1 + _SECRET_NONCE_BYTES]
    ciphertext = body[1 + _SECRET_NONCE_BYTES :]
    plaintext = _xor_bytes(
        ciphertext,
        _build_keystream(encryption_key, nonce, len(ciphertext)),
    )
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecretStoreError("Stored secret is not valid UTF-8") from exc


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _normalize_cookie_header(value: str | None) -> str | None:
    return _normalize_text(value)


def _normalize_user_agent(value: str | None) -> str | None:
    return _normalize_text(value)


def _normalize_netscape_cookies(value: str | None) -> str | None:
    normalized = _normalize_text(value)
    if normalized is None:
        return None
    return normalized.replace("\r\n", "\n").replace("\r", "\n")


def summarize_cookie_header(value: str | None) -> str | None:
    normalized = _normalize_cookie_header(value)
    if normalized is None:
        return None

    segments: list[str] = []
    for raw_segment in normalized.split(";"):
        segment = raw_segment.strip()
        if not segment:
            continue
        name, _, _ = segment.partition("=")
        if not name.strip():
            continue
        segments.append(f"{name.strip()}=***")

    if not segments:
        return "Configured"

    preview = "; ".join(segments[:5])
    if len(segments) > 5:
        preview = f"{preview}; ..."
    return preview


def _parse_bool_field(value: str, *, field_name: str) -> bool:
    normalized = value.strip().upper()
    if normalized == "TRUE":
        return True
    if normalized == "FALSE":
        return False
    raise SecretStoreError(
        f"Cookie file field '{field_name}' must be TRUE or FALSE"
    )


def parse_netscape_cookies(value: str) -> list[NetscapeCookie]:
    normalized = _normalize_netscape_cookies(value)
    if normalized is None:
        raise SecretStoreError("Netscape cookies cannot be empty")

    lines = normalized.split("\n")
    first_nonempty_line = next((line.strip() for line in lines if line.strip()), None)
    if first_nonempty_line not in _COOKIE_FILE_HEADERS:
        raise SecretStoreError(
            "Cookie file must start with '# Netscape HTTP Cookie File' or '# HTTP Cookie File'"
        )

    cookies: list[NetscapeCookie] = []
    for raw_line in lines:
        if not raw_line.strip():
            continue

        line = raw_line
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
        elif line.startswith("#"):
            continue

        columns = line.split("\t")
        if len(columns) != 7:
            raise SecretStoreError(
                "Cookie file rows must use the Netscape/Mozilla 7-column tab-separated format"
            )

        domain, include_subdomains, path, secure, expires_at, name, cookie_value = (
            column.strip() for column in columns
        )
        if not domain:
            raise SecretStoreError("Cookie rows must include a domain")
        if not path:
            raise SecretStoreError("Cookie rows must include a path")
        if not name:
            raise SecretStoreError("Cookie rows must include a cookie name")
        try:
            expires_at_int = int(expires_at)
        except ValueError as exc:
            raise SecretStoreError(
                "Cookie rows must include a numeric expires column"
            ) from exc

        cookies.append(
            NetscapeCookie(
                domain=domain,
                include_subdomains=_parse_bool_field(
                    include_subdomains,
                    field_name="include_subdomains",
                ),
                path=path,
                secure=_parse_bool_field(secure, field_name="secure"),
                expires_at=expires_at_int,
                name=name,
                value=cookie_value,
            )
        )

    if not cookies:
        raise SecretStoreError("Cookie file does not contain any cookie rows")

    return cookies


def summarize_netscape_cookies(value: str | None) -> str | None:
    normalized = _normalize_netscape_cookies(value)
    if normalized is None:
        return None

    cookies = parse_netscape_cookies(normalized)
    names: list[str] = []
    seen_names: set[str] = set()
    for cookie in cookies:
        if cookie.name in seen_names:
            continue
        seen_names.add(cookie.name)
        names.append(cookie.name)

    preview = ", ".join(names[:5])
    if len(names) > 5:
        preview = f"{preview}, ..."
    return f"{len(cookies)} cookies ({preview})"


def _cookie_domain_matches(cookie: NetscapeCookie, host: str) -> bool:
    normalized_host = host.lower()
    normalized_domain = cookie.domain.lstrip(".").lower()
    if cookie.include_subdomains:
        return normalized_host == normalized_domain or normalized_host.endswith(
            f".{normalized_domain}"
        )
    return normalized_host == normalized_domain


def _cookie_path_matches(cookie: NetscapeCookie, request_path: str) -> bool:
    cookie_path = cookie.path or "/"
    return request_path.startswith(cookie_path)


def _cookie_is_expired(cookie: NetscapeCookie, *, reference_time: float) -> bool:
    return cookie.expires_at > 0 and cookie.expires_at <= int(reference_time)


def _cookie_matches_url(
    cookie: NetscapeCookie,
    *,
    url: str,
    reference_time: float,
) -> bool:
    parsed_url = urlparse(url)
    if cookie.secure and parsed_url.scheme != "https":
        return False
    if _cookie_is_expired(cookie, reference_time=reference_time):
        return False

    host = parsed_url.hostname or ""
    request_path = parsed_url.path or "/"
    return _cookie_domain_matches(cookie, host) and _cookie_path_matches(
        cookie,
        request_path,
    )


def build_cookie_header_from_netscape(
    value: str | None,
    *,
    urls: tuple[str, ...] = _METADATA_COOKIE_URLS,
) -> str | None:
    normalized = _normalize_netscape_cookies(value)
    if normalized is None:
        return None

    cookies = parse_netscape_cookies(normalized)
    reference_time = time.time()
    selected_segments: list[str] = []
    seen_names: set[str] = set()
    for cookie in cookies:
        if cookie.name in seen_names:
            continue
        if any(
            _cookie_matches_url(
                cookie,
                url=url,
                reference_time=reference_time,
            )
            for url in urls
        ):
            seen_names.add(cookie.name)
            selected_segments.append(f"{cookie.name}={cookie.value}")

    return "; ".join(selected_segments) or None


def _load_secret_value(
    session: Session,
    key: str,
    *,
    normalizer: Callable[[str | None], str | None],
) -> tuple[str | None, AppSecret | None]:
    stored_secret = session.get(AppSecret, key)
    if stored_secret is None:
        return None, None

    try:
        return normalizer(decrypt_secret_value(stored_secret.encrypted_value)), stored_secret
    except SecretStoreError:
        raise


def _secret_metadata(
    *secrets_to_check: AppSecret | None,
) -> tuple[str | None, datetime | None]:
    for stored_secret in secrets_to_check:
        if stored_secret is not None:
            return stored_secret.updated_by, stored_secret.updated_at
    return None, None


def build_bilibili_access_runtime(session: Session) -> BilibiliAccessRuntime:
    warnings: list[str] = []
    database_netscape_cookies: str | None = None
    database_cookie_header: str | None = None
    database_download_user_agent: str | None = None
    netscape_secret: AppSecret | None = None
    legacy_cookie_secret: AppSecret | None = None
    user_agent_secret: AppSecret | None = None

    try:
        database_netscape_cookies, netscape_secret = _load_secret_value(
            session,
            _NETSCAPE_COOKIES_SECRET_KEY,
            normalizer=_normalize_netscape_cookies,
        )
        if database_netscape_cookies is not None:
            # Validate before using the cookie file or deriving a raw header.
            parse_netscape_cookies(database_netscape_cookies)
            database_cookie_header = build_cookie_header_from_netscape(
                database_netscape_cookies
            )
    except SecretStoreError as exc:
        logger.warning("Could not parse stored Bilibili Netscape cookies: %s", exc)
        warnings.append(
            "The database Netscape cookie override is unreadable. Save a new cookies.txt export to repair it."
        )

    if database_cookie_header is None:
        try:
            database_cookie_header, legacy_cookie_secret = _load_secret_value(
                session,
                _LEGACY_COOKIE_HEADER_SECRET_KEY,
                normalizer=_normalize_cookie_header,
            )
        except SecretStoreError as exc:
            logger.warning("Could not decrypt stored Bilibili cookie header: %s", exc)
            warnings.append(
                "The legacy database cookie header override is unreadable. Save a new cookies.txt export to repair it."
            )

    try:
        database_download_user_agent, user_agent_secret = _load_secret_value(
            session,
            _DOWNLOAD_USER_AGENT_SECRET_KEY,
            normalizer=_normalize_user_agent,
        )
    except SecretStoreError as exc:
        logger.warning("Could not decrypt stored Bilibili download user-agent: %s", exc)
        warnings.append(
            "The database download user-agent override is unreadable. Save a new user-agent string to repair it."
        )

    environment_cookie_header = _normalize_cookie_header(settings.BILIBILI_COOKIE_HEADER)
    effective_cookie_header = database_cookie_header or environment_cookie_header

    effective_cookie_source: Literal["database", "environment", "none"]
    if database_cookie_header is not None:
        effective_cookie_source = "database"
    elif environment_cookie_header is not None:
        effective_cookie_source = "environment"
    else:
        effective_cookie_source = "none"

    yt_dlp_cookies_file_configured = bool(_normalize_text(settings.YT_DLP_COOKIES_FILE))
    yt_dlp_cookies_from_browser_configured = bool(
        _normalize_text(settings.YT_DLP_COOKIES_FROM_BROWSER)
    )
    yt_dlp_impersonate_configured = bool(_normalize_text(settings.YT_DLP_IMPERSONATE))
    effective_download_user_agent = (
        database_download_user_agent or _normalize_user_agent(settings.YT_DLP_USER_AGENT)
    )

    if effective_cookie_header is None:
        warnings.append(
            "Metadata, comments, danmaku, and subtitle fetches only have public-session access until Netscape cookies or a raw cookie header is configured."
        )

    if not (
        database_netscape_cookies
        or yt_dlp_cookies_file_configured
        or yt_dlp_cookies_from_browser_configured
    ):
        warnings.append(
            "Source downloads may fail until Netscape cookies, `YT_DLP_COOKIES_FILE`, or `YT_DLP_COOKIES_FROM_BROWSER` is configured."
        )
    else:
        if effective_download_user_agent is None:
            warnings.append(
                "yt-dlp download user-agent is not configured. Some Bilibili media URLs may still return 403."
            )
        if not yt_dlp_impersonate_configured:
            warnings.append(
                "yt-dlp impersonation is not configured. Some Bilibili media URLs may still return 403."
            )

    database_cookie_updated_by, database_cookie_updated_at = _secret_metadata(
        netscape_secret,
        legacy_cookie_secret,
        user_agent_secret,
    )

    return BilibiliAccessRuntime(
        cookie_header=effective_cookie_header,
        download_cookies_text=database_netscape_cookies,
        download_user_agent=effective_download_user_agent,
        has_database_override=bool(
            netscape_secret or legacy_cookie_secret or user_agent_secret
        ),
        effective_cookie_source=effective_cookie_source,
        cookie_header_summary=summarize_cookie_header(effective_cookie_header),
        netscape_cookie_summary=summarize_netscape_cookies(database_netscape_cookies),
        download_user_agent_summary=effective_download_user_agent,
        yt_dlp_cookies_file_configured=yt_dlp_cookies_file_configured,
        yt_dlp_cookies_from_browser_configured=yt_dlp_cookies_from_browser_configured,
        yt_dlp_impersonate_configured=yt_dlp_impersonate_configured,
        database_cookie_updated_by=database_cookie_updated_by,
        database_cookie_updated_at=database_cookie_updated_at,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def get_bilibili_access_status(session: Session) -> BilibiliAccessStatusPublic:
    return build_bilibili_access_runtime(session).to_public()


def set_database_bilibili_access(
    session: Session,
    *,
    actor: str | None,
    netscape_cookies: str,
    download_user_agent: str | None,
) -> BilibiliAccessStatusPublic:
    normalized_netscape_cookies = _normalize_netscape_cookies(netscape_cookies)
    if normalized_netscape_cookies is None:
        raise SecretStoreError("Netscape cookies cannot be empty")
    parse_netscape_cookies(normalized_netscape_cookies)

    normalized_download_user_agent = _normalize_user_agent(download_user_agent)

    cookies_secret = session.get(AppSecret, _NETSCAPE_COOKIES_SECRET_KEY)
    if cookies_secret is None:
        cookies_secret = AppSecret(
            key=_NETSCAPE_COOKIES_SECRET_KEY,
            encrypted_value="",
        )

    cookies_secret.encrypted_value = encrypt_secret_value(normalized_netscape_cookies)
    cookies_secret.updated_by = actor
    cookies_secret.updated_at = _now_utc()
    session.add(cookies_secret)

    user_agent_secret = session.get(AppSecret, _DOWNLOAD_USER_AGENT_SECRET_KEY)
    if normalized_download_user_agent is not None:
        if user_agent_secret is None:
            user_agent_secret = AppSecret(
                key=_DOWNLOAD_USER_AGENT_SECRET_KEY,
                encrypted_value="",
            )
        user_agent_secret.encrypted_value = encrypt_secret_value(
            normalized_download_user_agent
        )
        user_agent_secret.updated_by = actor
        user_agent_secret.updated_at = _now_utc()
        session.add(user_agent_secret)
    elif user_agent_secret is not None:
        session.delete(user_agent_secret)

    legacy_cookie_secret = session.get(AppSecret, _LEGACY_COOKIE_HEADER_SECRET_KEY)
    if legacy_cookie_secret is not None:
        session.delete(legacy_cookie_secret)

    record_audit_event(
        session=session,
        actor=actor,
        action="system_secret.updated",
        resource_type="app_secret",
        resource_id=_NETSCAPE_COOKIES_SECRET_KEY,
        message="Updated Bilibili Netscape cookies override",
        payload={
            "cookie_summary": summarize_netscape_cookies(normalized_netscape_cookies),
            "download_user_agent_configured": normalized_download_user_agent is not None,
        },
    )
    session.commit()
    session.refresh(cookies_secret)
    return get_bilibili_access_status(session)


def clear_database_bilibili_access(
    session: Session,
    *,
    actor: str | None,
) -> BilibiliAccessStatusPublic:
    stored_secrets = [
        session.get(AppSecret, _NETSCAPE_COOKIES_SECRET_KEY),
        session.get(AppSecret, _DOWNLOAD_USER_AGENT_SECRET_KEY),
        session.get(AppSecret, _LEGACY_COOKIE_HEADER_SECRET_KEY),
    ]
    deleted = False
    for stored_secret in stored_secrets:
        if stored_secret is None:
            continue
        session.delete(stored_secret)
        deleted = True

    if deleted:
        record_audit_event(
            session=session,
            actor=actor,
            action="system_secret.cleared",
            resource_type="app_secret",
            resource_id=_NETSCAPE_COOKIES_SECRET_KEY,
            message="Cleared Bilibili database access override",
            payload={},
        )
        session.commit()

    return get_bilibili_access_status(session)
