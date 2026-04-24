import { OpenAPI } from "@/client"
import { request } from "@/client/core/request"

export type JobError = {
  code?: string | null
  message?: string | null
}

export type ApiMessage = {
  message: string
}

export type IngestVideoOptions = {
  download_video: boolean
  max_height?: number | null
  store_source_archive?: boolean
  create_normalized_mp4: boolean
  create_hls: boolean
  fetch_comments: boolean
  fetch_danmaku: boolean
  fetch_subtitles: boolean
  transcribe_subtitles: boolean
  force_refresh: boolean
}

export type IngestVideoRequest = {
  input: string
  options: IngestVideoOptions
}

export const commentDanmakuRefreshOptions: IngestVideoOptions = {
  download_video: false,
  max_height: null,
  store_source_archive: false,
  create_normalized_mp4: false,
  create_hls: false,
  fetch_comments: true,
  fetch_danmaku: true,
  fetch_subtitles: false,
  transcribe_subtitles: false,
  force_refresh: true,
}

export const buildCommentDanmakuRefreshRequest = (
  input: string,
): IngestVideoRequest => ({
  input,
  options: { ...commentDanmakuRefreshOptions },
})

export type IngestJobSummary = {
  job_id: string
  bvid?: string | null
  status: string
  phase?: string | null
  requested_by?: string | null
  error?: JobError | null
  created_at: string
  started_at?: string | null
  finished_at?: string | null
}

export type IngestJobDetail = IngestJobSummary & {
  options: Record<string, unknown>
  progress: Record<string, unknown>
}

export type IngestJobsResponse = {
  data: IngestJobSummary[]
  count: number
  limit: number
  offset: number
}

export type MediaAssetSummary = {
  asset_id: string
  asset_type: string
  variant?: string | null
  status: string
  cid?: number | null
  filename?: string | null
  content_type?: string | null
  size_bytes?: number | null
  sha256?: string | null
  container_format?: string | null
  video_codec?: string | null
  audio_codec?: string | null
  width?: number | null
  height?: number | null
  duration_seconds?: number | null
  created_at: string
  ready_at?: string | null
}

export type VideoAssetsResponse = {
  bvid: string
  assets: MediaAssetSummary[]
}

export type VideoCommentImage = {
  source_url?: string | null
  width?: number | null
  height?: number | null
  asset_id?: string | null
  storage_status: string
  error_message?: string | null
  asset?: MediaAssetSummary | null
}

export type VideoComment = {
  rpid: number
  oid?: number | null
  mid?: number | null
  uname?: string | null
  root?: number | null
  parent?: number | null
  message?: string | null
  like_count?: number | null
  reply_count?: number | null
  ctime?: string | null
  images: VideoCommentImage[]
}

export type VideoCommentContext = {
  rpid: number
  oid?: number | null
  mid?: number | null
  uname?: string | null
  root?: number | null
  parent?: number | null
  message?: string | null
  like_count?: number | null
  reply_count?: number | null
  ctime?: string | null
}

export type AuxiliarySourceJob = {
  job_id: string
  status: string
  phase?: string | null
  crawled_at?: string | null
}

export type VideoCommentsCompleteness = {
  partial?: boolean | null
  expected_count?: number | null
  fetched_count?: number | null
  stored_count?: number | null
  fallback_used?: boolean | null
  image_count?: number | null
  stored_image_count?: number | null
  failed_image_count?: number | null
  skipped_image_count?: number | null
  source_job: AuxiliarySourceJob
}

export type VideoCommentsResponse = {
  bvid: string
  count: number
  thread_count?: number | null
  limit: number
  offset: number
  completeness?: VideoCommentsCompleteness | null
  comments: VideoComment[]
}

export type VideoCommentImageEntry = VideoCommentImage & {
  image_id: string
  ordinal: number
  crawled_at: string
  comment: VideoCommentContext
}

export type VideoCommentImagesResponse = {
  bvid: string
  count: number
  limit: number
  offset: number
  completeness?: VideoCommentsCompleteness | null
  images: VideoCommentImageEntry[]
}

export type VideoDanmakuEntry = {
  danmaku_id?: number | null
  cid: number
  time_offset_seconds?: number | null
  mode?: number | null
  font_size?: number | null
  color?: number | null
  content?: string | null
  sent_at?: string | null
  source: string
  history_date?: string | null
}

export type VideoDanmakuResponse = {
  bvid: string
  count: number
  limit: number
  offset: number
  completeness?: VideoDanmakuCompleteness | null
  danmaku: VideoDanmakuEntry[]
}

export type VideoDanmakuPageCoverage = {
  cid: number
  count?: number | null
  source?: string | null
  history_used?: boolean | null
  snapshot_used?: boolean | null
  indexed_month_count?: number | null
  expected_days_count?: number | null
  fetched_days_count?: number | null
  partial?: boolean | null
}

export type VideoDanmakuCompleteness = {
  partial?: boolean | null
  stored_count?: number | null
  duplicate_count?: number | null
  cid_count?: number | null
  filled_cid_count?: number | null
  crawl_source?: string | null
  history_used?: boolean | null
  snapshot_used?: boolean | null
  indexed_month_count?: number | null
  expected_days_count?: number | null
  fetched_days_count?: number | null
  pages: VideoDanmakuPageCoverage[]
  source_job: AuxiliarySourceJob
}

export type VideoSubtitle = {
  subtitle_id: string
  cid?: number | null
  lang?: string | null
  source?: string | null
  content?: string | null
  asset_id?: string | null
  crawled_at: string
}

export type VideoSubtitlesResponse = {
  bvid: string
  count: number
  limit: number
  offset: number
  completeness?: VideoSubtitlesCompleteness | null
  subtitles: VideoSubtitle[]
}

export type VideoSubtitlesCompleteness = {
  partial?: boolean | null
  stored_count?: number | null
  cid_count?: number | null
  languages: string[]
  source_job: AuxiliarySourceJob
}

export type VideoSummary = {
  bvid: string
  aid?: number | null
  title: string
  owner_mid?: number | null
  owner_name?: string | null
  duration_seconds?: number | null
  pubdate?: string | null
  category?: string | null
  cover_url?: string | null
  tags: string[]
  takedown_status: string
  last_crawled_at?: string | null
}

export type VideoDetail = VideoSummary & {
  description?: string | null
  stat: Record<string, unknown>
}

export type VideosResponse = {
  data: VideoSummary[]
  count: number
  limit: number
  offset: number
}

export type SignedUrlResponse = {
  url: string
  expires_in: number
}

export type BilibiliAccessStatus = {
  metadata_cookie_configured: boolean
  download_auth_configured: boolean
  has_database_override: boolean
  effective_cookie_source: string
  cookie_header_summary?: string | null
  netscape_cookie_summary?: string | null
  download_user_agent_summary?: string | null
  download_user_agent_configured: boolean
  yt_dlp_cookies_file_configured: boolean
  yt_dlp_cookies_from_browser_configured: boolean
  yt_dlp_impersonate_configured: boolean
  database_cookie_updated_by?: string | null
  database_cookie_updated_at?: string | null
  warnings: string[]
}

export const IngestionApi = {
  createIngestJob: (payload: IngestVideoRequest) =>
    request<IngestJobSummary>(OpenAPI, {
      method: "POST",
      url: "/api/v1/ingest/videos",
      body: payload,
      mediaType: "application/json",
    }),
  readIngestJob: (jobId: string) =>
    request<IngestJobDetail>(OpenAPI, {
      method: "GET",
      url: "/api/v1/ingest/jobs/{job_id}",
      path: { job_id: jobId },
    }),
  readIngestJobs: (params: {
    status?: string
    bvid?: string
    requested_by?: string
    limit?: number
    offset?: number
  }) =>
    request<IngestJobsResponse>(OpenAPI, {
      method: "GET",
      url: "/api/v1/ingest/jobs",
      query: params,
    }),
  readVideos: (params: { q?: string; limit?: number; offset?: number }) =>
    request<VideosResponse>(OpenAPI, {
      method: "GET",
      url: "/api/v1/videos/",
      query: params,
    }),
  readVideo: (bvid: string) =>
    request<VideoDetail>(OpenAPI, {
      method: "GET",
      url: "/api/v1/videos/{bvid}",
      path: { bvid },
    }),
  deleteVideo: (bvid: string) =>
    request<ApiMessage>(OpenAPI, {
      method: "DELETE",
      url: "/api/v1/videos/{bvid}",
      path: { bvid },
    }),
  readVideoAssets: (bvid: string, assetType?: string) =>
    request<VideoAssetsResponse>(OpenAPI, {
      method: "GET",
      url: "/api/v1/videos/{bvid}/assets",
      path: { bvid },
      query: assetType ? { asset_type: assetType } : undefined,
    }),
  readVideoComments: (
    bvid: string,
    params: { root?: number; parent?: number; limit?: number; offset?: number },
  ) =>
    request<VideoCommentsResponse>(OpenAPI, {
      method: "GET",
      url: "/api/v1/videos/{bvid}/comments",
      path: { bvid },
      query: params,
    }),
  readVideoCommentImages: (
    bvid: string,
    params: {
      rpid?: number
      root?: number
      parent?: number
      storage_status?: string
      limit?: number
      offset?: number
    },
  ) =>
    request<VideoCommentImagesResponse>(OpenAPI, {
      method: "GET",
      url: "/api/v1/videos/{bvid}/comment-images",
      path: { bvid },
      query: params,
    }),
  readVideoDanmaku: (
    bvid: string,
    params: {
      cid?: number
      source?: string
      history_date?: string
      limit?: number
      offset?: number
    },
  ) =>
    request<VideoDanmakuResponse>(OpenAPI, {
      method: "GET",
      url: "/api/v1/videos/{bvid}/danmaku",
      path: { bvid },
      query: params,
    }),
  readVideoSubtitles: (
    bvid: string,
    params: { cid?: number; lang?: string; limit?: number; offset?: number },
  ) =>
    request<VideoSubtitlesResponse>(OpenAPI, {
      method: "GET",
      url: "/api/v1/videos/{bvid}/subtitles",
      path: { bvid },
      query: params,
    }),
  createPlaybackUrl: (assetId: string, expiresIn = 900) =>
    request<SignedUrlResponse>(OpenAPI, {
      method: "POST",
      url: "/api/v1/media/assets/{asset_id}/playback-url",
      path: { asset_id: assetId },
      body: { expires_in: expiresIn },
      mediaType: "application/json",
    }),
  readBilibiliAccessStatus: () =>
    request<BilibiliAccessStatus>(OpenAPI, {
      method: "GET",
      url: "/api/v1/system/bilibili-access",
    }),
  updateBilibiliAccessStatus: (payload: {
    netscape_cookies: string
    download_user_agent?: string | null
  }) =>
    request<BilibiliAccessStatus>(OpenAPI, {
      method: "PUT",
      url: "/api/v1/system/bilibili-access",
      body: payload,
      mediaType: "application/json",
    }),
  clearBilibiliAccessStatus: () =>
    request<BilibiliAccessStatus>(OpenAPI, {
      method: "DELETE",
      url: "/api/v1/system/bilibili-access",
    }),
}

export const defaultIngestOptions: IngestVideoOptions = {
  download_video: true,
  max_height: null,
  store_source_archive: true,
  create_normalized_mp4: true,
  create_hls: true,
  fetch_comments: true,
  fetch_danmaku: true,
  fetch_subtitles: true,
  transcribe_subtitles: true,
  force_refresh: false,
}
