from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.downloader.base import DownloadPlan
from app.downloader.yt_dlp_adapter import YtDlpDownloaderAdapter


class RecordingYtDlpAdapter(YtDlpDownloaderAdapter):
    def __init__(
        self,
        *,
        cookie_header: str | None = None,
        cookies_text: str | None = None,
        cookies_file: str | None = None,
        cookies_from_browser: str | None = None,
        user_agent: str | None = None,
        impersonate: str | None = None,
        info_payload: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            binary="yt-dlp",
            cookie_header=cookie_header,
            cookies_text=cookies_text,
            cookies_file=cookies_file,
            cookies_from_browser=cookies_from_browser,
            user_agent=user_agent,
            impersonate=impersonate,
        )
        self.commands: list[list[str]] = []
        self.inline_cookie_payloads: list[str] = []
        self.info_payload = info_payload or {
            "id": "BV1Q541167Qg",
            "webpage_url": "https://www.bilibili.com/video/BV1Q541167Qg",
            "title": "Example title",
            "cid": 123456,
            "format_id": "video+audio",
            "ext": "mp4",
        }

    def _ensure_binary_available(self) -> None:
        return None

    def _run(
        self,
        command: list[str],
        *,
        error_cls: type[Exception],
    ) -> subprocess.CompletedProcess[str]:
        del error_cls
        self.commands.append(command)
        if "--cookies" in command:
            cookies_path = Path(command[command.index("--cookies") + 1])
            if cookies_path.exists():
                self.inline_cookie_payloads.append(
                    cookies_path.read_text(encoding="utf-8")
                )
        if "--skip-download" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(self.info_payload),
                stderr="",
            )

        output_dir = Path(command[command.index("--paths") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "source.mp4").write_bytes(b"video")
        (output_dir / "source.info.json").write_text(
            '{"extractor":"bilibili"}',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def test_extract_info_prefers_inline_cookies_and_sets_user_agent() -> None:
    cookies_text = """# Netscape HTTP Cookie File
.bilibili.com\tTRUE\t/\tTRUE\t2147483647\tSESSDATA\tsession-cookie
"""
    user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/146.0.0.0"
    adapter = RecordingYtDlpAdapter(
        cookie_header="SESSDATA=legacy-cookie",
        cookies_text=cookies_text,
        cookies_from_browser="chrome",
        user_agent=user_agent,
    )

    metadata = adapter.extract_info("https://www.bilibili.com/video/BV1Q541167Qg")

    command = adapter.commands[0]
    assert command[:4] == [
        "yt-dlp",
        "--skip-download",
        "--dump-single-json",
        "--no-warnings",
    ]
    assert command[command.index("--cookies") + 1].endswith(".txt")
    assert command[command.index("--user-agent") + 1] == user_agent
    assert "--add-header" not in command
    assert "--cookies-from-browser" not in command
    assert adapter.inline_cookie_payloads == [cookies_text]
    assert metadata.bvid == "BV1Q541167Qg"
    assert metadata.cid == 123456


def test_download_prefers_cookie_file_over_browser_sync(tmp_path: Path) -> None:
    adapter = RecordingYtDlpAdapter(
        cookies_file="/app/backend/.secrets/bilibili-cookies.txt",
        cookies_from_browser="chrome",
        impersonate="chrome",
    )
    plan = DownloadPlan(
        bvid="BV1Q541167Qg",
        webpage_url="https://www.bilibili.com/video/BV1Q541167Qg",
        title="Example title",
    )

    result = adapter.download(plan, str(tmp_path))

    command = adapter.commands[0]
    assert command[command.index("--cookies") + 1] == (
        "/app/backend/.secrets/bilibili-cookies.txt"
    )
    assert "--cookies-from-browser" not in command
    assert command[command.index("--impersonate") + 1] == "chrome"
    assert len(result.local_files) == 1
    assert result.info_json_path == str(tmp_path / "source.info.json")


def test_extract_info_uses_cookie_header_as_last_resort() -> None:
    cookie_header = "SESSDATA=session-cookie; bili_jct=csrf-token"
    adapter = RecordingYtDlpAdapter(cookie_header=cookie_header)

    adapter.extract_info("https://www.bilibili.com/video/BV1Q541167Qg")

    command = adapter.commands[0]
    assert command[command.index("--add-header") + 1] == f"Cookie: {cookie_header}"
