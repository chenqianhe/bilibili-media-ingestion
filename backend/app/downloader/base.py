from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class DownloaderError(Exception):
    error_code = "source_download_failed"


class DownloaderInfoError(DownloaderError):
    error_code = "download_info_failed"


class DownloaderExecutionError(DownloaderError):
    error_code = "source_download_failed"


class DownloaderToolUnavailableError(DownloaderError):
    error_code = "downloader_unavailable"


class DownloaderResultError(DownloaderError):
    error_code = "download_result_invalid"


class DownloadPlan(BaseModel):
    bvid: str
    cid: int | None = None
    title: str | None = None
    webpage_url: str
    selected_format_id: str | None = None
    format_selector: str | None = None
    max_height: int | None = None
    expected_ext: str | None = None
    raw_info: dict[str, Any] = Field(default_factory=dict)


class DownloadResult(BaseModel):
    bvid: str
    cid: int | None = None
    local_files: list[str] = Field(default_factory=list)
    info_json_path: str | None = None
    title: str | None = None


class DownloaderAdapter(ABC):
    @abstractmethod
    def extract_info(self, input_url: str) -> DownloadPlan:
        raise NotImplementedError

    @abstractmethod
    def download(self, plan: DownloadPlan, output_dir: str) -> DownloadResult:
        raise NotImplementedError
