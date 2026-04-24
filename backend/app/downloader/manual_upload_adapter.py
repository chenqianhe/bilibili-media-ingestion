from app.downloader.base import DownloadPlan, DownloadResult, DownloaderAdapter


class ManualUploadAdapter(DownloaderAdapter):
    def extract_info(self, input_url: str) -> DownloadPlan:
        return DownloadPlan(
            bvid="manual-upload",
            webpage_url=input_url,
            raw_info={"source": "manual_upload"},
        )

    def download(self, plan: DownloadPlan, output_dir: str) -> DownloadResult:
        return DownloadResult(
            bvid=plan.bvid,
            cid=plan.cid,
            local_files=[],
            info_json_path=None,
            title=plan.title,
        )
