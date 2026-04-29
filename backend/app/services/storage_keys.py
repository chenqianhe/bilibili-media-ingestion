import uuid
from pathlib import Path, PurePosixPath


ASSET_PREFIXES = {
    "source_archive": "media/source",
    "source_video_stream": "media/source",
    "source_audio_stream": "media/source",
    "normalized_mp4": "media/normalized",
    "proxy_mp4": "media/proxy",
    "hls_master": "media/hls",
    "hls_segment": "media/hls",
    "dash_manifest": "media/dash",
    "dash_segment": "media/dash",
    "subtitle": "media/subtitles",
    "danmaku_raw": "media/danmaku",
    "cover": "images/covers",
    "avatar": "images/avatars",
    "comment_image": "images/comments",
    "thumbnail": "thumbnails",
}

DEFAULT_FILENAMES = {
    "source_archive": "source.mp4",
    "source_video_stream": "source-video.bin",
    "source_audio_stream": "source-audio.bin",
    "normalized_mp4": "normalized.mp4",
    "proxy_mp4": "proxy.mp4",
    "hls_master": "master.m3u8",
    "dash_manifest": "manifest.mpd",
    "subtitle": "subtitle.json",
    "danmaku_raw": "danmaku.xml",
    "cover": "cover.jpg",
    "avatar": "avatar.jpg",
    "comment_image": "comment-image.bin",
    "thumbnail": "thumbnail.jpg",
}


def _cid_segment(cid: int | None) -> str:
    if cid is None:
        return "cid=unknown"
    return f"cid={cid}"


def _normalize_filename(filename: str | None, asset_type: str) -> str:
    if filename:
        candidate = Path(filename).name.strip()
        if candidate:
            return candidate
    return DEFAULT_FILENAMES.get(asset_type, "asset.bin")


def build_asset_storage_key(
    *,
    asset_type: str,
    bvid: str,
    cid: int | None,
    asset_id: uuid.UUID,
    filename: str | None = None,
) -> str:
    prefix = ASSET_PREFIXES.get(asset_type)
    if prefix is None:
        raise ValueError(f"Unsupported asset type: {asset_type}")
    normalized_filename = _normalize_filename(filename, asset_type)
    return str(
        PurePosixPath(prefix)
        / f"bvid={bvid}"
        / _cid_segment(cid)
        / f"asset_id={asset_id}"
        / normalized_filename
    )


def build_cover_storage_key(*, bvid: str, filename: str | None = None) -> str:
    normalized_filename = _normalize_filename(filename, "cover")
    return str(PurePosixPath("images/covers") / f"bvid={bvid}" / normalized_filename)


def build_avatar_storage_key(*, owner_mid: int, filename: str | None = None) -> str:
    normalized_filename = filename or "avatar.jpg"
    return str(
        PurePosixPath("images/avatars")
        / f"mid={owner_mid}"
        / Path(normalized_filename).name
    )


def build_thumbnail_storage_key(
    *, bvid: str, cid: int | None, filename: str | None = None
) -> str:
    normalized_filename = _normalize_filename(filename, "thumbnail")
    return str(
        PurePosixPath("thumbnails")
        / f"bvid={bvid}"
        / _cid_segment(cid)
        / normalized_filename
    )
