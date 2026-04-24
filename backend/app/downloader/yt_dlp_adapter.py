from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.downloader.base import (
    DownloaderAdapter,
    DownloaderExecutionError,
    DownloaderInfoError,
    DownloaderResultError,
    DownloaderToolUnavailableError,
    DownloadPlan,
    DownloadResult,
)
from app.services.bilibili import extract_bvid


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


class YtDlpDownloaderAdapter(DownloaderAdapter):
    def __init__(
        self,
        *,
        binary: str | None = None,
        cookie_header: str | None = None,
        cookies_text: str | None = None,
        cookies_file: str | None = None,
        cookies_from_browser: str | None = None,
        user_agent: str | None = None,
        impersonate: str | None = None,
    ) -> None:
        self._binary = binary or settings.YT_DLP_BINARY
        self._cookie_header = cookie_header or settings.BILIBILI_COOKIE_HEADER
        self._cookies_text = cookies_text
        self._cookies_file = cookies_file or settings.YT_DLP_COOKIES_FILE
        self._cookies_from_browser = (
            cookies_from_browser or settings.YT_DLP_COOKIES_FROM_BROWSER
        )
        self._user_agent = user_agent or settings.YT_DLP_USER_AGENT
        self._impersonate = impersonate or settings.YT_DLP_IMPERSONATE

    def extract_info(self, input_url: str) -> DownloadPlan:
        self._ensure_binary_available()

        with self._auth_args() as auth_args:
            command = [
                self._binary,
                "--skip-download",
                "--dump-single-json",
                "--no-warnings",
                *auth_args,
                input_url,
            ]
            completed = self._run(command, error_cls=DownloaderInfoError)
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise DownloaderInfoError("yt-dlp returned invalid metadata JSON") from exc
        if not isinstance(payload, dict):
            raise DownloaderInfoError("yt-dlp returned a non-object metadata payload")

        webpage_url = payload.get("webpage_url")
        if not isinstance(webpage_url, str) or not webpage_url.strip():
            webpage_url = input_url

        bvid = extract_bvid(webpage_url) or extract_bvid(str(payload.get("id") or ""))
        if bvid is None:
            raise DownloaderInfoError("Could not determine BVID from yt-dlp metadata")

        return DownloadPlan(
            bvid=bvid,
            cid=_coerce_int(payload.get("cid")),
            title=payload.get("title") if isinstance(payload.get("title"), str) else None,
            webpage_url=webpage_url,
            selected_format_id=(
                payload.get("format_id")
                if isinstance(payload.get("format_id"), str)
                else None
            ),
            expected_ext=payload.get("ext") if isinstance(payload.get("ext"), str) else None,
            raw_info=payload,
        )

    def download(self, plan: DownloadPlan, output_dir: str) -> DownloadResult:
        self._ensure_binary_available()

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        with self._auth_args() as auth_args:
            command = [
                self._binary,
                "--no-warnings",
                "--write-info-json",
                *auth_args,
                "--paths",
                str(output_path),
                "--output",
                "%(title).200B [%(id)s].%(ext)s",
            ]
            format_selector = plan.format_selector or plan.selected_format_id
            if format_selector:
                command.extend(["--format", format_selector])
            command.append(plan.webpage_url)

            self._run(command, error_cls=DownloaderExecutionError)

        info_json_path: str | None = None
        local_files: list[str] = []
        for path in sorted(output_path.rglob("*")):
            if not path.is_file():
                continue
            if path.name.endswith(".info.json"):
                info_json_path = str(path)
                continue
            local_files.append(str(path))

        if not local_files:
            raise DownloaderResultError("yt-dlp did not produce any media files")

        return DownloadResult(
            bvid=plan.bvid,
            cid=plan.cid,
            local_files=local_files,
            info_json_path=info_json_path,
            title=plan.title,
        )

    @contextmanager
    def _auth_args(self) -> Iterator[list[str]]:
        args: list[str] = []
        cookie_file_to_cleanup: Path | None = None
        try:
            if self._user_agent:
                args.extend(["--user-agent", self._user_agent])
            if self._impersonate:
                args.extend(["--impersonate", self._impersonate])

            if self._cookies_text:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    prefix="yt-dlp-cookies-",
                    suffix=".txt",
                    delete=False,
                ) as temp_cookie_file:
                    temp_cookie_file.write(self._cookies_text)
                    cookie_file_to_cleanup = Path(temp_cookie_file.name)
                args.extend(["--cookies", str(cookie_file_to_cleanup)])
            elif self._cookies_file:
                args.extend(["--cookies", self._cookies_file])
            elif self._cookies_from_browser:
                args.extend(
                    ["--cookies-from-browser", self._cookies_from_browser]
                )
            elif self._cookie_header:
                args.extend(["--add-header", f"Cookie: {self._cookie_header}"])

            yield args
        finally:
            if cookie_file_to_cleanup is not None:
                cookie_file_to_cleanup.unlink(missing_ok=True)

    def _ensure_binary_available(self) -> None:
        if shutil.which(self._binary) is None:
            raise DownloaderToolUnavailableError(
                f"Downloader binary '{self._binary}' is not available"
            )

    def _run(
        self,
        command: list[str],
        *,
        error_cls: type[DownloaderExecutionError] | type[DownloaderInfoError],
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
            raise error_cls(f"yt-dlp failed: {detail}")
        return completed
