import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, useNavigate } from "@tanstack/react-router"
import {
  Activity,
  Captions,
  Clock3,
  Database,
  FileJson,
  Film,
  ListFilter,
  type LucideIcon,
  MessageCircleMore,
  Package,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
} from "lucide-react"
import {
  type ReactNode,
  startTransition,
  useDeferredValue,
  useEffect,
  useState,
} from "react"
import { z } from "zod"

import { JsonPreview } from "@/components/Ingestion/JsonPreview"
import { StatusBadge } from "@/components/Ingestion/StatusBadge"
import { VideoPlayback } from "@/components/Ingestion/VideoPlayback"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import {
  buildCommentDanmakuRefreshRequest,
  IngestionApi,
  type VideoComment,
  type VideoCommentImage,
  type VideosResponse,
} from "@/lib/ingestionApi"
import { handleError } from "@/utils"

const videoSearchSchema = z.object({
  bvid: z.string().optional(),
})

const commentLimitOptions = ["30", "50", "100", "200"]
const danmakuLimitOptions = ["40", "100", "250", "500"]
const subtitleLimitOptions = ["12", "25", "50", "100"]
const compactStatsGridClass =
  "grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(10rem,1fr))]"
const filterGridClass =
  "grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(13rem,1fr))]"
const metricGridClass =
  "grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(17rem,1fr))]"
const imageGridClass =
  "grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(12rem,1fr))]"
const subtitleSourceAssetTypes = new Set([
  "source_archive",
  "source_video_stream",
  "source_audio_stream",
])

export const Route = createFileRoute("/_layout/videos")({
  validateSearch: (search) => videoSearchSchema.parse(search),
  component: VideosPage,
  head: () => ({
    meta: [
      {
        title: "Videos Browser",
      },
    ],
  }),
})

function formatDateTime(value?: string | null) {
  if (!value) {
    return "Unknown"
  }
  return new Date(value).toLocaleString()
}

function formatDuration(totalSeconds?: number | null) {
  if (!totalSeconds || totalSeconds <= 0) {
    return "Unknown"
  }
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = Math.round(totalSeconds % 60)
  return `${minutes}m ${seconds.toString().padStart(2, "0")}s`
}

function formatBytes(bytes?: number | null) {
  if (!bytes) {
    return "Unknown"
  }
  if (bytes < 1024) {
    return `${bytes} B`
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`
  }
  if (bytes < 1024 * 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function formatCount(value?: number | null) {
  if (value === null || value === undefined) {
    return "Unknown"
  }
  return value.toLocaleString()
}

function formatOffset(seconds?: number | null) {
  if (seconds === null || seconds === undefined) {
    return "Unknown"
  }
  const safeSeconds = Math.max(0, seconds)
  const minutes = Math.floor(safeSeconds / 60)
  const remainder = Math.floor(safeSeconds % 60)
  return `${minutes}:${remainder.toString().padStart(2, "0")}`
}

function parseSubtitleBody(content?: string | null) {
  if (!content) {
    return []
  }
  try {
    const parsed = JSON.parse(content) as {
      body?: Array<{ from?: number; to?: number; content?: string }>
    }
    return parsed.body ?? []
  } catch {
    return []
  }
}

function parseIntegerInput(value: string) {
  if (!value.trim()) {
    return undefined
  }
  const parsed = Number.parseInt(value, 10)
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return undefined
  }
  return parsed
}

function getCoverageState(
  completeness?: { partial?: boolean | null } | null,
): "complete" | "partial" | "available" | "unknown" {
  if (!completeness) {
    return "unknown"
  }
  if (typeof completeness.partial === "boolean") {
    return completeness.partial ? "partial" : "complete"
  }
  return "available"
}

function CoverageBadge({
  completeness,
}: {
  completeness?: { partial?: boolean | null } | null
}) {
  const state = getCoverageState(completeness)
  const classNameByState: Record<string, string> = {
    complete:
      "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
    partial:
      "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
    available: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300",
    unknown:
      "border-slate-500/30 bg-slate-500/10 text-slate-700 dark:text-slate-300",
  }

  return (
    <Badge variant="outline" className={classNameByState[state]}>
      {state}
    </Badge>
  )
}

function MetricCard({
  icon: Icon,
  label,
  value,
  hint,
  badge,
}: {
  icon: LucideIcon
  label: string
  value: string
  hint: string
  badge?: ReactNode
}) {
  return (
    <Card className="border-border/70 bg-card/90">
      <CardHeader className="space-y-3 pb-0">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex min-w-0 items-center gap-2 text-sm font-medium text-muted-foreground">
            <Icon className="size-4 text-primary" />
            <span className="truncate">{label}</span>
          </div>
          {badge ? <div className="ml-auto flex shrink-0">{badge}</div> : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-2 pt-0">
        <div className="text-3xl font-semibold tracking-tight">{value}</div>
        <div className="text-sm leading-6 text-muted-foreground">{hint}</div>
      </CardContent>
    </Card>
  )
}

function OverviewField({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-xs uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </div>
      <div className="mt-2 break-words text-sm font-medium">{value}</div>
    </div>
  )
}

type CommentTreeNode = {
  comment: VideoComment
  replies: CommentTreeNode[]
  isOrphan: boolean
}

function buildCommentTree(comments: VideoComment[]) {
  const nodesByRpid = new Map<number, CommentTreeNode>()
  for (const comment of comments) {
    nodesByRpid.set(comment.rpid, {
      comment,
      replies: [],
      isOrphan: false,
    })
  }

  const roots: CommentTreeNode[] = []
  for (const comment of comments) {
    const node = nodesByRpid.get(comment.rpid)
    if (!node) {
      continue
    }

    const parentRpid = comment.parent
    if (parentRpid && parentRpid !== comment.rpid) {
      const parentNode = nodesByRpid.get(parentRpid)
      if (parentNode) {
        parentNode.replies.push(node)
        continue
      }
      node.isOrphan = true
    }

    roots.push(node)
  }

  return roots
}

function buildPaginationItems(currentPage: number, totalPages: number) {
  if (totalPages <= 1) {
    return [1]
  }

  const items: Array<number | string> = [1]
  const windowStart = Math.max(2, currentPage - 1)
  const windowEnd = Math.min(totalPages - 1, currentPage + 1)

  if (windowStart > 2) {
    items.push("ellipsis-start")
  }
  for (let page = windowStart; page <= windowEnd; page += 1) {
    items.push(page)
  }
  if (windowEnd < totalPages - 1) {
    items.push("ellipsis-end")
  }
  if (totalPages > 1) {
    items.push(totalPages)
  }

  return items
}

function CommentImageGrid({
  images,
  commentRpid,
}: {
  images: VideoCommentImage[]
  commentRpid: number
}) {
  if (!images.length) {
    return null
  }

  return (
    <div className="space-y-3">
      <div className="text-xs uppercase tracking-[0.14em] text-muted-foreground">
        Comment Images
      </div>
      <div className={imageGridClass}>
        {images.map((image, imageIndex) => (
          <div
            key={image.asset_id ?? `${commentRpid}-${imageIndex}`}
            className="overflow-hidden rounded-lg border border-border/70 bg-muted/20"
          >
            {image.source_url ? (
              <img
                alt={`Comment attachment ${imageIndex + 1}`}
                className="aspect-video w-full object-cover"
                src={image.source_url}
              />
            ) : (
              <div className="flex aspect-video items-center justify-center text-sm text-muted-foreground">
                No stored image preview
              </div>
            )}
            <div className="space-y-3 px-3 py-3 text-xs text-muted-foreground">
              <div className="flex items-center justify-between gap-2">
                <span>
                  {image.width ?? "?"} × {image.height ?? "?"}
                </span>
                <StatusBadge status={image.storage_status} />
              </div>
              <div className="flex flex-wrap gap-2">
                {image.asset_id ? (
                  <Badge variant="outline">
                    asset {image.asset_id.slice(0, 8)}
                  </Badge>
                ) : null}
                {image.asset?.size_bytes ? (
                  <Badge variant="outline">
                    {formatBytes(image.asset.size_bytes)}
                  </Badge>
                ) : null}
              </div>
              {image.error_message ? (
                <div className="rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-sm text-rose-700 dark:text-rose-300">
                  {image.error_message}
                </div>
              ) : image.storage_status === "skipped" ? (
                <div className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
                  This image was recorded in PostgreSQL but was not uploaded as
                  an object-storage asset.
                </div>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function CommentThreadCard({
  node,
  depth = 0,
}: {
  node: CommentTreeNode
  depth?: number
}) {
  const { comment, replies, isOrphan } = node
  const isReply = depth > 0

  return (
    <div className="space-y-3">
      <div
        className={`rounded-lg border border-border/70 ${
          isReply ? "bg-muted/20" : "bg-card/90"
        }`}
      >
        <div className="space-y-4 px-5 py-5">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <div className="font-semibold">
                  {comment.uname || comment.mid || "Unknown user"}
                </div>
                {isReply ? <Badge variant="secondary">reply</Badge> : null}
                {isOrphan ? (
                  <Badge variant="outline">parent missing</Badge>
                ) : null}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                {formatDateTime(comment.ctime)} · likes{" "}
                {comment.like_count ?? 0} · replies {comment.reply_count ?? 0}
              </div>
            </div>
            <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
              <Badge variant="outline">rpid {comment.rpid}</Badge>
              <Badge variant="outline">
                root {comment.root ?? comment.rpid}
              </Badge>
              <Badge variant="outline">parent {comment.parent ?? "none"}</Badge>
            </div>
          </div>

          {isOrphan ? (
            <div className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
              This reply&apos;s parent is not included in the current filtered
              result set.
            </div>
          ) : null}

          <p className="whitespace-pre-wrap break-words text-sm leading-7">
            {comment.message || "No text content"}
          </p>

          <CommentImageGrid
            images={comment.images}
            commentRpid={comment.rpid}
          />
        </div>
      </div>

      {replies.length ? (
        <div className="space-y-3 border-l border-border/70 pl-4 sm:pl-6">
          <div className="text-xs uppercase tracking-[0.14em] text-muted-foreground">
            {replies.length === 1
              ? "1 threaded reply"
              : `${replies.length} threaded replies`}
          </div>
          {replies.map((reply) => (
            <CommentThreadCard
              key={reply.comment.rpid}
              node={reply}
              depth={depth + 1}
            />
          ))}
        </div>
      ) : null}
    </div>
  )
}

function DeleteVideoDialog({
  bvid,
  title,
  isPending,
  onConfirm,
}: {
  bvid: string
  title: string
  isPending: boolean
  onConfirm: () => Promise<void>
}) {
  const [isOpen, setIsOpen] = useState(false)

  const handleConfirm = async () => {
    await onConfirm()
    setIsOpen(false)
  }

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(nextOpen) => {
        if (!isPending) {
          setIsOpen(nextOpen)
        }
      }}
    >
      <Button
        variant="destructive"
        className="w-full sm:w-auto"
        disabled={isPending}
        onClick={() => setIsOpen(true)}
      >
        <Trash2 className="size-4" />
        Delete Video
      </Button>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete Video</DialogTitle>
          <DialogDescription>
            Delete <strong>{bvid}</strong> from the local catalog? Metadata,
            media assets, comments, danmaku, subtitles, and stored files tied to
            this video will be permanently removed.
          </DialogDescription>
        </DialogHeader>
        <div className="rounded-lg border border-border/70 bg-muted/20 px-4 py-3">
          <div className="text-sm font-semibold">{title}</div>
          <div className="mt-1 text-xs text-muted-foreground">{bvid}</div>
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" disabled={isPending}>
              Cancel
            </Button>
          </DialogClose>
          <LoadingButton
            variant="destructive"
            type="button"
            loading={isPending}
            onClick={() => void handleConfirm()}
          >
            Delete
          </LoadingButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function VideosPage() {
  const navigate = useNavigate({ from: Route.fullPath })
  const queryClient = useQueryClient()
  const { showErrorToast, showSuccessToast } = useCustomToast()
  const { user: currentUser } = useAuth()
  const search = Route.useSearch()
  const [queryText, setQueryText] = useState("")
  const [commentRoot, setCommentRoot] = useState("")
  const [commentParent, setCommentParent] = useState("")
  const [commentLimit, setCommentLimit] = useState("30")
  const [commentPage, setCommentPage] = useState(1)
  const [danmakuCid, setDanmakuCid] = useState("")
  const [danmakuSource, setDanmakuSource] = useState("all")
  const [danmakuHistoryDate, setDanmakuHistoryDate] = useState("")
  const [danmakuLimit, setDanmakuLimit] = useState("40")
  const [subtitleCid, setSubtitleCid] = useState("")
  const [subtitleLang, setSubtitleLang] = useState("")
  const [subtitleLimit, setSubtitleLimit] = useState("12")
  const deferredQuery = useDeferredValue(queryText.trim())

  const videosQuery = useQuery({
    queryKey: ["videos", { q: deferredQuery, limit: 30 }],
    queryFn: () =>
      IngestionApi.readVideos({
        q: deferredQuery || undefined,
        limit: 30,
        offset: 0,
      }),
  })

  const selectedBvid = search.bvid

  useEffect(() => {
    if (!selectedBvid && videosQuery.data?.data.length) {
      setCommentPage(1)
      startTransition(() => {
        navigate({
          search: { bvid: videosQuery.data?.data[0]?.bvid },
        })
      })
    }
  }, [navigate, selectedBvid, videosQuery.data])

  useEffect(() => {
    setCommentRoot("")
    setCommentParent("")
    setCommentLimit("30")
    setCommentPage(1)
    setDanmakuCid("")
    setDanmakuSource("all")
    setDanmakuHistoryDate("")
    setDanmakuLimit("40")
    setSubtitleCid("")
    setSubtitleLang("")
    setSubtitleLimit("12")
  }, [])

  const commentRootValue = parseIntegerInput(commentRoot)
  const commentParentValue = parseIntegerInput(commentParent)
  const commentLimitValue = parseIntegerInput(commentLimit) ?? 30
  const commentOffsetValue = (commentPage - 1) * commentLimitValue
  const danmakuCidValue = parseIntegerInput(danmakuCid)
  const danmakuLimitValue = parseIntegerInput(danmakuLimit) ?? 40
  const subtitleCidValue = parseIntegerInput(subtitleCid)
  const subtitleLimitValue = parseIntegerInput(subtitleLimit) ?? 12

  const videoQuery = useQuery({
    queryKey: ["video", selectedBvid],
    queryFn: () => IngestionApi.readVideo(selectedBvid ?? ""),
    enabled: Boolean(selectedBvid),
  })
  const assetsQuery = useQuery({
    queryKey: ["video-assets", selectedBvid],
    queryFn: () => IngestionApi.readVideoAssets(selectedBvid ?? ""),
    enabled: Boolean(selectedBvid),
  })
  const commentsQuery = useQuery({
    queryKey: [
      "video-comments",
      selectedBvid,
      commentRootValue ?? null,
      commentParentValue ?? null,
      commentLimitValue,
      commentOffsetValue,
    ],
    queryFn: () =>
      IngestionApi.readVideoComments(selectedBvid ?? "", {
        root: commentRootValue,
        parent: commentParentValue,
        limit: commentLimitValue,
        offset: commentOffsetValue,
      }),
    enabled: Boolean(selectedBvid),
  })
  const danmakuQuery = useQuery({
    queryKey: [
      "video-danmaku",
      selectedBvid,
      danmakuCidValue ?? null,
      danmakuSource,
      danmakuHistoryDate || null,
      danmakuLimitValue,
    ],
    queryFn: () =>
      IngestionApi.readVideoDanmaku(selectedBvid ?? "", {
        cid: danmakuCidValue,
        source: danmakuSource === "all" ? undefined : danmakuSource,
        history_date: danmakuHistoryDate || undefined,
        limit: danmakuLimitValue,
        offset: 0,
      }),
    enabled: Boolean(selectedBvid),
  })
  const subtitlesQuery = useQuery({
    queryKey: [
      "video-subtitles",
      selectedBvid,
      subtitleCidValue ?? null,
      subtitleLang.trim() || null,
      subtitleLimitValue,
    ],
    queryFn: () =>
      IngestionApi.readVideoSubtitles(selectedBvid ?? "", {
        cid: subtitleCidValue,
        lang: subtitleLang.trim() || undefined,
        limit: subtitleLimitValue,
        offset: 0,
      }),
    enabled: Boolean(selectedBvid),
  })
  const jobsQuery = useQuery({
    queryKey: ["ingest-jobs", selectedBvid],
    queryFn: () =>
      IngestionApi.readIngestJobs({
        bvid: selectedBvid,
        limit: 10,
        offset: 0,
      }),
    enabled: Boolean(selectedBvid),
  })

  const latestJobId = jobsQuery.data?.data[0]?.job_id
  const latestJobQuery = useQuery({
    queryKey: ["ingest-job-detail", latestJobId],
    queryFn: () => IngestionApi.readIngestJob(latestJobId ?? ""),
    enabled: Boolean(latestJobId),
  })
  const refreshAuxiliaryMutation = useMutation({
    mutationFn: () =>
      IngestionApi.createIngestJob(
        buildCommentDanmakuRefreshRequest(selectedBvid ?? ""),
      ),
    onSuccess: (job) => {
      showSuccessToast(
        `Queued a merge refresh for comments and danmaku on ${job.bvid ?? selectedBvid}.`,
      )
      queryClient.invalidateQueries({ queryKey: ["ingest-jobs", selectedBvid] })
      queryClient.invalidateQueries({ queryKey: ["ingest-job-detail"] })
    },
    onError: handleError.bind(showErrorToast),
  })
  const generateSubtitlesMutation = useMutation({
    mutationFn: () =>
      IngestionApi.createSubtitleTranscriptionTasks(selectedBvid ?? "", {}),
    onSuccess: (response) => {
      const queuedCount = response.assets.length
      if (queuedCount > 0) {
        showSuccessToast(
          `Queued ${formatCount(queuedCount)} subtitle task${queuedCount === 1 ? "" : "s"} for ${response.bvid}.`,
        )
      } else {
        showSuccessToast(
          `No new subtitle tasks were queued for ${response.bvid}.`,
        )
      }
      queryClient.invalidateQueries({
        queryKey: ["video-assets", response.bvid],
      })
      queryClient.invalidateQueries({
        queryKey: ["video-subtitles", response.bvid],
      })
      queryClient.invalidateQueries({
        queryKey: ["ingest-jobs", response.bvid],
      })
      queryClient.invalidateQueries({ queryKey: ["ingest-job-detail"] })
    },
    onError: handleError.bind(showErrorToast),
  })
  const deleteVideoMutation = useMutation({
    mutationFn: (bvid: string) => IngestionApi.deleteVideo(bvid),
    onError: handleError.bind(showErrorToast),
  })

  const assets = assetsQuery.data?.assets ?? []
  const selectedVideo = videoQuery.data
  const canDeleteVideo = currentUser?.is_superuser === true
  const commentCompleteness = commentsQuery.data?.completeness
  const danmakuCompleteness = danmakuQuery.data?.completeness
  const subtitleCompleteness = subtitlesQuery.data?.completeness
  const latestJobSummary = jobsQuery.data?.data[0] ?? null

  const hlsAsset = assets.find((asset) => asset.asset_type === "hls_master")
  const playbackAsset =
    assets.find((asset) => asset.asset_type === "normalized_mp4") ??
    assets.find((asset) => asset.asset_type === "proxy_mp4")
  const hasSubtitleSourceAssets = assets.some(
    (asset) =>
      subtitleSourceAssetTypes.has(asset.asset_type) &&
      ["uploaded", "ready"].includes(asset.status),
  )

  const commentFiltersActive =
    commentRootValue !== undefined || commentParentValue !== undefined
  const danmakuFiltersActive =
    danmakuCidValue !== undefined ||
    danmakuSource !== "all" ||
    Boolean(danmakuHistoryDate)
  const subtitleFiltersActive =
    subtitleCidValue !== undefined || Boolean(subtitleLang.trim())

  const commentStoredCount =
    commentCompleteness?.stored_count ?? commentsQuery.data?.count ?? 0
  const danmakuStoredCount =
    danmakuCompleteness?.stored_count ?? danmakuQuery.data?.count ?? 0
  const subtitleStoredCount =
    subtitleCompleteness?.stored_count ?? subtitlesQuery.data?.count ?? 0
  const threadedComments = buildCommentTree(commentsQuery.data?.comments ?? [])
  const commentThreadCount = commentsQuery.data?.thread_count ?? 0
  const commentReturnedRowCount = commentsQuery.data?.comments.length ?? 0
  const commentPaginationByThread = commentParentValue === undefined
  const commentPaginationTotal = commentPaginationByThread
    ? commentThreadCount
    : (commentsQuery.data?.count ?? 0)
  const commentTotalPages = Math.max(
    1,
    Math.ceil(Math.max(commentPaginationTotal, 1) / commentLimitValue),
  )
  const commentPageItems = buildPaginationItems(commentPage, commentTotalPages)
  const commentMetricHint = commentFiltersActive
    ? commentPaginationByThread
      ? `${formatCount(commentsQuery.data?.count ?? 0)} rows across ${formatCount(commentThreadCount)} threads match current filters`
      : `${formatCount(commentsQuery.data?.count ?? 0)} replies across ${formatCount(commentThreadCount)} threads match current filters`
    : `${formatCount(commentsQuery.data?.count ?? 0)} stored rows across ${formatCount(commentThreadCount)} threads`
  const commentQuerySummary = commentsQuery.isFetching
    ? commentPaginationByThread
      ? "Refreshing comment threads…"
      : "Refreshing filtered replies…"
    : commentPaginationByThread
      ? `${formatCount(commentsQuery.data?.count ?? 0)} rows across ${formatCount(commentThreadCount)} threads · showing ${formatCount(threadedComments.length)} threads / ${formatCount(commentReturnedRowCount)} rows on this page`
      : `${formatCount(commentsQuery.data?.count ?? 0)} replies across ${formatCount(commentThreadCount)} threads · showing ${formatCount(commentReturnedRowCount)} replies on this page`

  const assetTypeCount = new Set(assets.map((asset) => asset.asset_type)).size
  const danmakuSourceOptions = Array.from(
    new Set((danmakuQuery.data?.danmaku ?? []).map((entry) => entry.source)),
  ).filter(Boolean)
  if (
    danmakuSource !== "all" &&
    danmakuSource &&
    !danmakuSourceOptions.includes(danmakuSource)
  ) {
    danmakuSourceOptions.unshift(danmakuSource)
  }

  useEffect(() => {
    if (commentPage > commentTotalPages) {
      setCommentPage(commentTotalPages)
    }
  }, [commentPage, commentTotalPages])

  const handleDeleteVideo = async () => {
    if (!selectedBvid) {
      return
    }

    const deletedBvid = selectedBvid
    const nextSelectedBvid = videosQuery.data?.data.find(
      (video) => video.bvid !== deletedBvid,
    )?.bvid

    const result = await deleteVideoMutation.mutateAsync(deletedBvid)

    queryClient.setQueriesData<VideosResponse>(
      { queryKey: ["videos"] },
      (current) => {
        if (!current) {
          return current
        }
        const nextVideos = current.data.filter(
          (video) => video.bvid !== deletedBvid,
        )
        const removedCount = current.data.length - nextVideos.length
        if (removedCount === 0) {
          return current
        }
        return {
          ...current,
          data: nextVideos,
          count: Math.max(0, current.count - removedCount),
        }
      },
    )

    for (const queryKey of [
      ["video", deletedBvid],
      ["video-assets", deletedBvid],
      ["video-comments", deletedBvid],
      ["video-danmaku", deletedBvid],
      ["video-subtitles", deletedBvid],
      ["ingest-jobs", deletedBvid],
      ["ingest-job-detail"],
    ]) {
      queryClient.removeQueries({ queryKey })
    }

    setCommentPage(1)
    startTransition(() => {
      navigate({
        search: nextSelectedBvid ? { bvid: nextSelectedBvid } : {},
      })
    })

    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["videos"] }),
      queryClient.invalidateQueries({ queryKey: ["ingest-jobs"] }),
    ])

    showSuccessToast(result.message)
  }

  return (
    <div className="space-y-6">
      <section className="flex flex-col gap-4 border-b pb-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">
            Data Workspace
          </div>
          <h1 className="mt-3 text-3xl font-semibold tracking-tight">
            Inspect crawl coverage, stored rows, and ingest health.
          </h1>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-muted-foreground">
            Jump between BVIDs, confirm what landed in PostgreSQL, inspect
            completeness, and spot broken or partial crawls quickly.
          </p>
        </div>
        <div className="w-full max-w-sm">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-9"
              placeholder="Search by BVID or title"
              value={queryText}
              onChange={(event) => setQueryText(event.target.value)}
            />
          </div>
        </div>
      </section>

      <div className="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)]">
        <Card className="h-fit border-border/70 bg-card/90">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Film className="size-4 text-primary" />
              Video Catalog
            </CardTitle>
            <CardDescription>
              Select a BVID to inspect metadata, auxiliary coverage, and recent
              ingest jobs.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {videosQuery.isLoading ? (
              <div className="text-sm text-muted-foreground">
                Loading videos…
              </div>
            ) : videosQuery.data?.data.length ? (
              videosQuery.data.data.map((video) => (
                <button
                  type="button"
                  key={video.bvid}
                  className={`w-full rounded-lg border px-4 py-4 text-left transition ${
                    selectedBvid === video.bvid
                      ? "border-primary/50 bg-primary/5"
                      : "border-border/70 bg-card hover:bg-muted/20"
                  }`}
                  onClick={() => {
                    setCommentPage(1)
                    startTransition(() => {
                      navigate({
                        search: { bvid: video.bvid },
                      })
                    })
                  }}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate font-semibold">
                        {video.title}
                      </div>
                      <div className="mt-1 text-sm text-muted-foreground">
                        {video.bvid}
                      </div>
                    </div>
                  </div>
                  <div className="mt-3 text-xs text-muted-foreground">
                    {video.owner_name || "Unknown uploader"} ·{" "}
                    {formatDuration(video.duration_seconds)}
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    crawled {formatDateTime(video.last_crawled_at)}
                  </div>
                </button>
              ))
            ) : (
              <div className="rounded-lg border border-dashed border-border/70 px-4 py-8 text-center text-sm text-muted-foreground">
                No matching videos found.
              </div>
            )}
          </CardContent>
        </Card>

        <div className="space-y-6">
          {videoQuery.isLoading && selectedBvid ? (
            <Card className="border-border/70 bg-card/90">
              <CardContent className="flex min-h-80 items-center justify-center text-sm text-muted-foreground">
                Loading video detail…
              </CardContent>
            </Card>
          ) : selectedVideo ? (
            <>
              <div className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
                <Card className="border-border/70 bg-card/90">
                  <CardHeader>
                    <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                      <div>
                        <CardTitle className="flex items-center gap-2 text-base">
                          <Database className="size-4 text-primary" />
                          Video Overview
                        </CardTitle>
                        <CardDescription>
                          Canonical metadata and storage-facing identifiers for
                          this BVID.
                        </CardDescription>
                      </div>
                      {canDeleteVideo ? (
                        <DeleteVideoDialog
                          bvid={selectedVideo.bvid}
                          title={selectedVideo.title}
                          isPending={deleteVideoMutation.isPending}
                          onConfirm={handleDeleteVideo}
                        />
                      ) : null}
                    </div>
                  </CardHeader>
                  <CardContent>
                    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_220px]">
                      <div className="space-y-4">
                        <div>
                          <div className="text-sm text-muted-foreground">
                            {selectedVideo.bvid}
                            {selectedVideo.aid
                              ? ` · aid ${selectedVideo.aid}`
                              : ""}
                          </div>
                          <h2 className="mt-2 break-words text-2xl font-semibold">
                            {selectedVideo.title}
                          </h2>
                          {selectedVideo.description ? (
                            <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-7 text-muted-foreground">
                              {selectedVideo.description}
                            </p>
                          ) : null}
                        </div>

                        <div className="grid gap-4 text-sm md:grid-cols-2">
                          <OverviewField
                            label="Uploader"
                            value={selectedVideo.owner_name || "Unknown"}
                          />
                          <OverviewField
                            label="Duration"
                            value={formatDuration(
                              selectedVideo.duration_seconds,
                            )}
                          />
                          <OverviewField
                            label="Published"
                            value={formatDateTime(selectedVideo.pubdate)}
                          />
                          <OverviewField
                            label="Last Crawled"
                            value={formatDateTime(
                              selectedVideo.last_crawled_at,
                            )}
                          />
                          <OverviewField
                            label="Takedown"
                            value={
                              <StatusBadge
                                status={selectedVideo.takedown_status}
                              />
                            }
                          />
                        </div>

                        <div className="flex flex-wrap gap-2">
                          {selectedVideo.category ? (
                            <Badge variant="outline">
                              {selectedVideo.category}
                            </Badge>
                          ) : null}
                          {selectedVideo.tags.map((tag) => (
                            <Badge key={tag} variant="secondary">
                              {tag}
                            </Badge>
                          ))}
                        </div>
                      </div>

                      <div className="space-y-3">
                        {selectedVideo.cover_url ? (
                          <img
                            alt={`${selectedVideo.title} cover`}
                            className="aspect-video w-full rounded-lg border border-border/70 object-cover"
                            src={selectedVideo.cover_url}
                          />
                        ) : (
                          <div className="flex aspect-video items-center justify-center rounded-lg border border-dashed border-border/70 bg-muted/20 text-sm text-muted-foreground">
                            No cover image
                          </div>
                        )}
                        <div className="rounded-lg border border-border/70 bg-muted/20 p-4 text-sm">
                          <div className="font-medium">Storage Snapshot</div>
                          <div className="mt-2 text-muted-foreground">
                            {formatCount(assets.length)} assets across{" "}
                            {formatCount(assetTypeCount)} asset types.
                          </div>
                        </div>
                      </div>
                    </div>
                  </CardContent>
                </Card>

                <Card className="border-border/70 bg-card/90">
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base">
                      <ShieldCheck className="size-4 text-primary" />
                      Ingest Health
                    </CardTitle>
                    <CardDescription>
                      Latest ingest status plus the current auxiliary coverage
                      view for this video.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    {latestJobSummary ? (
                      <div className="rounded-lg border border-border/70 bg-muted/20 p-4">
                        <div className="flex flex-wrap items-center gap-2">
                          <StatusBadge status={latestJobSummary.status} />
                          {latestJobSummary.phase ? (
                            <div className="text-sm font-medium">
                              {latestJobSummary.phase}
                            </div>
                          ) : null}
                        </div>
                        <div
                          className={`${compactStatsGridClass} mt-3 text-sm`}
                        >
                          <OverviewField
                            label="Created"
                            value={formatDateTime(latestJobSummary.created_at)}
                          />
                          <OverviewField
                            label="Finished"
                            value={formatDateTime(latestJobSummary.finished_at)}
                          />
                        </div>
                        {latestJobSummary.error?.message ? (
                          <div className="mt-4 rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-sm text-rose-700 dark:text-rose-300">
                            {latestJobSummary.error.message}
                          </div>
                        ) : null}
                      </div>
                    ) : (
                      <div className="rounded-lg border border-dashed border-border/70 px-4 py-6 text-sm text-muted-foreground">
                        No ingest jobs are visible for this BVID yet.
                      </div>
                    )}

                    <div className={compactStatsGridClass}>
                      <div className="min-w-0 rounded-lg border border-border/70 bg-muted/20 p-4">
                        <div className="text-xs uppercase tracking-[0.14em] text-muted-foreground">
                          Comments
                        </div>
                        <div className="mt-3 flex flex-wrap items-center gap-2">
                          <CoverageBadge completeness={commentCompleteness} />
                          <div className="text-sm text-muted-foreground">
                            {formatCount(commentStoredCount)} stored
                          </div>
                        </div>
                      </div>
                      <div className="min-w-0 rounded-lg border border-border/70 bg-muted/20 p-4">
                        <div className="text-xs uppercase tracking-[0.14em] text-muted-foreground">
                          Danmaku
                        </div>
                        <div className="mt-3 flex flex-wrap items-center gap-2">
                          <CoverageBadge completeness={danmakuCompleteness} />
                          <div className="text-sm text-muted-foreground">
                            {formatCount(danmakuStoredCount)} stored
                          </div>
                        </div>
                      </div>
                      <div className="min-w-0 rounded-lg border border-border/70 bg-muted/20 p-4">
                        <div className="text-xs uppercase tracking-[0.14em] text-muted-foreground">
                          Subtitles
                        </div>
                        <div className="mt-3 flex flex-wrap items-center gap-2">
                          <CoverageBadge completeness={subtitleCompleteness} />
                          <div className="text-sm text-muted-foreground">
                            {formatCount(subtitleStoredCount)} stored
                          </div>
                        </div>
                      </div>
                    </div>

                    <div className="rounded-lg border border-primary/15 bg-primary/5 p-4">
                      <div className="flex flex-col gap-4">
                        <div className="min-w-0 space-y-2">
                          <div className="flex min-w-0 items-start gap-2 text-sm font-medium">
                            <RefreshCw className="mt-0.5 size-4 shrink-0 text-primary" />
                            Only Refresh Comments + Danmaku
                          </div>
                          <div className="text-sm leading-6 text-muted-foreground">
                            Queue a merge refresh for auxiliary rows only.
                            Existing comments or danmaku that later disappear
                            upstream stay stored locally.
                          </div>
                        </div>
                        <LoadingButton
                          className="h-auto min-h-9 w-full whitespace-normal py-2 text-center"
                          disabled={!selectedBvid}
                          loading={refreshAuxiliaryMutation.isPending}
                          onClick={() => refreshAuxiliaryMutation.mutate()}
                        >
                          Refresh Comments + Danmaku
                        </LoadingButton>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </div>

              <div className={metricGridClass}>
                <MetricCard
                  icon={Package}
                  label="Assets"
                  value={formatCount(assets.length)}
                  hint={`${formatCount(assetTypeCount)} asset types currently indexed`}
                  badge={
                    <Badge variant="secondary">
                      {formatCount(assetTypeCount)} types
                    </Badge>
                  }
                />
                <MetricCard
                  icon={MessageCircleMore}
                  label="Comments"
                  value={formatCount(commentStoredCount)}
                  hint={commentMetricHint}
                  badge={<CoverageBadge completeness={commentCompleteness} />}
                />
                <MetricCard
                  icon={Activity}
                  label="Danmaku"
                  value={formatCount(danmakuStoredCount)}
                  hint={
                    danmakuFiltersActive
                      ? `${formatCount(danmakuQuery.data?.count ?? 0)} rows match current filters`
                      : `${formatCount(danmakuQuery.data?.count ?? 0)} rows in current view`
                  }
                  badge={<CoverageBadge completeness={danmakuCompleteness} />}
                />
                <MetricCard
                  icon={Captions}
                  label="Subtitles"
                  value={formatCount(subtitleStoredCount)}
                  hint={
                    subtitleFiltersActive
                      ? `${formatCount(subtitlesQuery.data?.count ?? 0)} rows match current filters`
                      : `${formatCount(subtitlesQuery.data?.count ?? 0)} rows in current view`
                  }
                  badge={<CoverageBadge completeness={subtitleCompleteness} />}
                />
              </div>

              <Tabs className="space-y-4" defaultValue="comments">
                <TabsList className="w-full justify-start overflow-auto">
                  <TabsTrigger value="comments">Comments</TabsTrigger>
                  <TabsTrigger value="danmaku">Danmaku</TabsTrigger>
                  <TabsTrigger value="subtitles">Subtitles</TabsTrigger>
                  <TabsTrigger value="assets">Assets</TabsTrigger>
                  <TabsTrigger value="jobs">Jobs</TabsTrigger>
                  <TabsTrigger value="raw">Raw</TabsTrigger>
                </TabsList>

                <TabsContent value="comments" className="space-y-4">
                  <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
                    <Card className="border-border/70 bg-card/90">
                      <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base">
                          <MessageCircleMore className="size-4 text-primary" />
                          Comments Coverage
                        </CardTitle>
                        <CardDescription>
                          Whole-video completeness, independent of the active
                          route filters.
                        </CardDescription>
                      </CardHeader>
                      <CardContent>
                        {commentCompleteness ? (
                          <div className="space-y-4">
                            <div className="flex flex-wrap items-center gap-2">
                              <CoverageBadge
                                completeness={commentCompleteness}
                              />
                              <StatusBadge
                                status={commentCompleteness.source_job.status}
                              />
                              <div className="text-sm text-muted-foreground">
                                crawled{" "}
                                {formatDateTime(
                                  commentCompleteness.source_job.crawled_at,
                                )}
                              </div>
                            </div>
                            <div className={compactStatsGridClass}>
                              <OverviewField
                                label="Expected"
                                value={formatCount(
                                  commentCompleteness.expected_count,
                                )}
                              />
                              <OverviewField
                                label="Fetched"
                                value={formatCount(
                                  commentCompleteness.fetched_count,
                                )}
                              />
                              <OverviewField
                                label="Stored"
                                value={formatCount(
                                  commentCompleteness.stored_count,
                                )}
                              />
                              <OverviewField
                                label="Images"
                                value={formatCount(
                                  commentCompleteness.image_count,
                                )}
                              />
                            </div>
                            <div className="flex flex-wrap gap-2 text-sm text-muted-foreground">
                              <Badge variant="outline">
                                fallback{" "}
                                {commentCompleteness.fallback_used
                                  ? "used"
                                  : "not used"}
                              </Badge>
                              <Badge variant="outline">
                                ready images{" "}
                                {formatCount(
                                  commentCompleteness.stored_image_count,
                                )}
                              </Badge>
                              <Badge variant="outline">
                                failed images{" "}
                                {formatCount(
                                  commentCompleteness.failed_image_count,
                                )}
                              </Badge>
                              <Badge variant="outline">
                                skipped images{" "}
                                {formatCount(
                                  commentCompleteness.skipped_image_count,
                                )}
                              </Badge>
                            </div>
                          </div>
                        ) : (
                          <div className="rounded-lg border border-dashed border-border/70 px-4 py-6 text-sm text-muted-foreground">
                            No comments completeness summary is attached to the
                            visible crawl history for this video.
                          </div>
                        )}
                      </CardContent>
                    </Card>

                    <Card className="border-border/70 bg-card/90">
                      <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base">
                          <ListFilter className="size-4 text-primary" />
                          Comment Filters
                        </CardTitle>
                        <CardDescription>
                          Narrow the current view without changing the stored
                          completeness summary. Replies render as threaded
                          conversations when parent rows are available. Page
                          size counts top-level threads unless a parent filter
                          is active.
                        </CardDescription>
                      </CardHeader>
                      <CardContent className="space-y-4">
                        <div className={filterGridClass}>
                          <div>
                            <div className="mb-2 text-xs uppercase tracking-[0.14em] text-muted-foreground">
                              Root RPId
                            </div>
                            <Input
                              placeholder="e.g. 123456"
                              value={commentRoot}
                              onChange={(event) => {
                                setCommentPage(1)
                                setCommentRoot(event.target.value)
                              }}
                            />
                          </div>
                          <div>
                            <div className="mb-2 text-xs uppercase tracking-[0.14em] text-muted-foreground">
                              Parent RPId
                            </div>
                            <Input
                              placeholder="e.g. 123456"
                              value={commentParent}
                              onChange={(event) => {
                                setCommentPage(1)
                                setCommentParent(event.target.value)
                              }}
                            />
                          </div>
                          <div>
                            <div className="mb-2 text-xs uppercase tracking-[0.14em] text-muted-foreground">
                              Page Size
                            </div>
                            <Select
                              value={commentLimit}
                              onValueChange={(value) => {
                                setCommentPage(1)
                                setCommentLimit(value)
                              }}
                            >
                              <SelectTrigger className="w-full">
                                <SelectValue placeholder="Select limit" />
                              </SelectTrigger>
                              <SelectContent>
                                {commentLimitOptions.map((option) => (
                                  <SelectItem key={option} value={option}>
                                    {option}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                        </div>

                        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                          <div className="text-sm leading-6 text-muted-foreground">
                            {commentQuerySummary}
                          </div>
                          <Button
                            className="w-full sm:w-auto"
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              setCommentRoot("")
                              setCommentParent("")
                              setCommentLimit("30")
                              setCommentPage(1)
                            }}
                          >
                            Reset filters
                          </Button>
                        </div>
                      </CardContent>
                    </Card>
                  </div>

                  <div className="space-y-4">
                    {commentsQuery.isLoading ? (
                      <Card className="border-border/70 bg-card/90">
                        <CardContent className="pt-6 text-sm text-muted-foreground">
                          Loading comments…
                        </CardContent>
                      </Card>
                    ) : commentsQuery.data?.comments.length ? (
                      threadedComments.map((thread) => (
                        <CommentThreadCard
                          key={thread.comment.rpid}
                          node={thread}
                        />
                      ))
                    ) : (
                      <Card className="border-border/70 bg-card/90">
                        <CardContent className="pt-6 text-sm text-muted-foreground">
                          No comments matched the current filters.
                        </CardContent>
                      </Card>
                    )}
                  </div>

                  {commentPaginationTotal > commentLimitValue ? (
                    <Card className="border-border/70 bg-card/90">
                      <CardContent className="flex flex-col gap-3 pt-6 lg:flex-row lg:items-center lg:justify-between">
                        <div className="text-sm text-muted-foreground">
                          Page {commentPage.toLocaleString()} of{" "}
                          {commentTotalPages.toLocaleString()} by{" "}
                          {commentPaginationByThread
                            ? "top-level threads"
                            : "reply rows"}
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={commentPage <= 1}
                            onClick={() =>
                              setCommentPage((currentPage) =>
                                Math.max(1, currentPage - 1),
                              )
                            }
                          >
                            Previous
                          </Button>
                          {commentPageItems.map((item) =>
                            typeof item === "number" ? (
                              <Button
                                key={item}
                                size="sm"
                                variant={
                                  item === commentPage ? "default" : "outline"
                                }
                                onClick={() => setCommentPage(item)}
                              >
                                {item}
                              </Button>
                            ) : (
                              <span
                                key={item}
                                className="px-1 text-sm text-muted-foreground"
                              >
                                …
                              </span>
                            ),
                          )}
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={commentPage >= commentTotalPages}
                            onClick={() =>
                              setCommentPage((currentPage) =>
                                Math.min(commentTotalPages, currentPage + 1),
                              )
                            }
                          >
                            Next
                          </Button>
                        </div>
                      </CardContent>
                    </Card>
                  ) : null}
                </TabsContent>

                <TabsContent value="danmaku" className="space-y-4">
                  <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
                    <Card className="border-border/70 bg-card/90">
                      <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base">
                          <Activity className="size-4 text-primary" />
                          Danmaku Coverage
                        </CardTitle>
                        <CardDescription>
                          Coverage is calculated from history index coverage
                          plus snapshot merges, not from the current filtered
                          rows.
                        </CardDescription>
                      </CardHeader>
                      <CardContent>
                        {danmakuCompleteness ? (
                          <div className="space-y-4">
                            <div className="flex flex-wrap items-center gap-2">
                              <CoverageBadge
                                completeness={danmakuCompleteness}
                              />
                              <StatusBadge
                                status={danmakuCompleteness.source_job.status}
                              />
                              <Badge variant="outline">
                                source{" "}
                                {danmakuCompleteness.crawl_source || "unknown"}
                              </Badge>
                              <div className="text-sm text-muted-foreground">
                                crawled{" "}
                                {formatDateTime(
                                  danmakuCompleteness.source_job.crawled_at,
                                )}
                              </div>
                            </div>
                            <div className={compactStatsGridClass}>
                              <OverviewField
                                label="Stored"
                                value={formatCount(
                                  danmakuCompleteness.stored_count,
                                )}
                              />
                              <OverviewField
                                label="CIDs"
                                value={`${formatCount(
                                  danmakuCompleteness.filled_cid_count,
                                )} / ${formatCount(danmakuCompleteness.cid_count)}`}
                              />
                              <OverviewField
                                label="Days"
                                value={`${formatCount(
                                  danmakuCompleteness.fetched_days_count,
                                )} / ${formatCount(
                                  danmakuCompleteness.expected_days_count,
                                )}`}
                              />
                              <OverviewField
                                label="Indexed Months"
                                value={formatCount(
                                  danmakuCompleteness.indexed_month_count,
                                )}
                              />
                            </div>
                            <div className="flex flex-wrap gap-2 text-sm text-muted-foreground">
                              <Badge variant="outline">
                                history{" "}
                                {danmakuCompleteness.history_used
                                  ? "used"
                                  : "not used"}
                              </Badge>
                              <Badge variant="outline">
                                snapshot{" "}
                                {danmakuCompleteness.snapshot_used
                                  ? "used"
                                  : "not used"}
                              </Badge>
                              <Badge variant="outline">
                                duplicates{" "}
                                {formatCount(
                                  danmakuCompleteness.duplicate_count,
                                )}
                              </Badge>
                            </div>

                            {danmakuCompleteness.pages.length ? (
                              <div className="overflow-x-auto rounded-lg border border-border/70">
                                <Table>
                                  <TableHeader>
                                    <TableRow>
                                      <TableHead>CID</TableHead>
                                      <TableHead>Rows</TableHead>
                                      <TableHead>Days</TableHead>
                                      <TableHead>State</TableHead>
                                    </TableRow>
                                  </TableHeader>
                                  <TableBody>
                                    {danmakuCompleteness.pages.map((page) => (
                                      <TableRow key={page.cid}>
                                        <TableCell className="font-medium">
                                          {page.cid}
                                        </TableCell>
                                        <TableCell>
                                          {formatCount(page.count)}
                                        </TableCell>
                                        <TableCell>
                                          {formatCount(page.fetched_days_count)}{" "}
                                          /{" "}
                                          {formatCount(
                                            page.expected_days_count,
                                          )}
                                        </TableCell>
                                        <TableCell>
                                          <CoverageBadge completeness={page} />
                                        </TableCell>
                                      </TableRow>
                                    ))}
                                  </TableBody>
                                </Table>
                              </div>
                            ) : null}
                          </div>
                        ) : (
                          <div className="rounded-lg border border-dashed border-border/70 px-4 py-6 text-sm text-muted-foreground">
                            No danmaku completeness summary is attached to the
                            visible crawl history for this video.
                          </div>
                        )}
                      </CardContent>
                    </Card>

                    <Card className="border-border/70 bg-card/90">
                      <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base">
                          <ListFilter className="size-4 text-primary" />
                          Danmaku Filters
                        </CardTitle>
                        <CardDescription>
                          Filter by page, crawl source, or a specific history
                          day.
                        </CardDescription>
                      </CardHeader>
                      <CardContent className="space-y-4">
                        <div className={filterGridClass}>
                          <div>
                            <div className="mb-2 text-xs uppercase tracking-[0.14em] text-muted-foreground">
                              CID
                            </div>
                            <Input
                              placeholder="e.g. 101"
                              value={danmakuCid}
                              onChange={(event) =>
                                setDanmakuCid(event.target.value)
                              }
                            />
                          </div>
                          <div>
                            <div className="mb-2 text-xs uppercase tracking-[0.14em] text-muted-foreground">
                              Source
                            </div>
                            <Select
                              value={danmakuSource}
                              onValueChange={setDanmakuSource}
                            >
                              <SelectTrigger className="w-full">
                                <SelectValue placeholder="All sources" />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="all">All sources</SelectItem>
                                {danmakuSourceOptions.map((option) => (
                                  <SelectItem key={option} value={option}>
                                    {option}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                          <div>
                            <div className="mb-2 text-xs uppercase tracking-[0.14em] text-muted-foreground">
                              History Date
                            </div>
                            <Input
                              type="date"
                              value={danmakuHistoryDate}
                              onChange={(event) =>
                                setDanmakuHistoryDate(event.target.value)
                              }
                            />
                          </div>
                          <div>
                            <div className="mb-2 text-xs uppercase tracking-[0.14em] text-muted-foreground">
                              Limit
                            </div>
                            <Select
                              value={danmakuLimit}
                              onValueChange={setDanmakuLimit}
                            >
                              <SelectTrigger className="w-full">
                                <SelectValue placeholder="Select limit" />
                              </SelectTrigger>
                              <SelectContent>
                                {danmakuLimitOptions.map((option) => (
                                  <SelectItem key={option} value={option}>
                                    {option}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                        </div>

                        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                          <div className="text-sm leading-6 text-muted-foreground">
                            {danmakuQuery.isFetching
                              ? "Refreshing danmaku rows…"
                              : `${formatCount(danmakuQuery.data?.count ?? 0)} rows in the current result set`}
                          </div>
                          <Button
                            className="w-full sm:w-auto"
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              setDanmakuCid("")
                              setDanmakuSource("all")
                              setDanmakuHistoryDate("")
                              setDanmakuLimit("40")
                            }}
                          >
                            Reset filters
                          </Button>
                        </div>
                      </CardContent>
                    </Card>
                  </div>

                  <Card className="border-border/70 bg-card/90">
                    <CardContent className="pt-6">
                      <div className="overflow-x-auto rounded-lg border border-border/70">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>CID</TableHead>
                              <TableHead>Offset</TableHead>
                              <TableHead>Source</TableHead>
                              <TableHead>History Date</TableHead>
                              <TableHead>Sent At</TableHead>
                              <TableHead>Content</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {danmakuQuery.isLoading ? (
                              <TableRow>
                                <TableCell
                                  className="text-muted-foreground"
                                  colSpan={6}
                                >
                                  Loading danmaku…
                                </TableCell>
                              </TableRow>
                            ) : danmakuQuery.data?.danmaku.length ? (
                              danmakuQuery.data.danmaku.map((entry) => (
                                <TableRow
                                  key={`${entry.cid}-${entry.danmaku_id}-${entry.sent_at}`}
                                >
                                  <TableCell>{entry.cid}</TableCell>
                                  <TableCell>
                                    {formatOffset(entry.time_offset_seconds)}
                                  </TableCell>
                                  <TableCell>{entry.source}</TableCell>
                                  <TableCell>
                                    {entry.history_date || "Snapshot"}
                                  </TableCell>
                                  <TableCell>
                                    {formatDateTime(entry.sent_at)}
                                  </TableCell>
                                  <TableCell className="max-w-xl whitespace-normal">
                                    {entry.content || "No content"}
                                  </TableCell>
                                </TableRow>
                              ))
                            ) : (
                              <TableRow>
                                <TableCell
                                  className="text-muted-foreground"
                                  colSpan={6}
                                >
                                  No danmaku matched the current filters.
                                </TableCell>
                              </TableRow>
                            )}
                          </TableBody>
                        </Table>
                      </div>
                    </CardContent>
                  </Card>
                </TabsContent>

                <TabsContent value="subtitles" className="space-y-4">
                  <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
                    <Card className="border-border/70 bg-card/90">
                      <CardHeader>
                        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                          <div>
                            <CardTitle className="flex items-center gap-2 text-base">
                              <Captions className="size-4 text-primary" />
                              Subtitle Coverage
                            </CardTitle>
                            <CardDescription>
                              Subtitle rows are stored per track. This summary
                              shows track count and language coverage from the
                              latest crawl that reported subtitle progress.
                            </CardDescription>
                          </div>
                          <LoadingButton
                            className="w-full sm:w-auto"
                            disabled={!selectedBvid || !hasSubtitleSourceAssets}
                            loading={generateSubtitlesMutation.isPending}
                            size="sm"
                            onClick={() => generateSubtitlesMutation.mutate()}
                          >
                            <Captions className="size-4" />
                            Generate subtitles
                          </LoadingButton>
                        </div>
                      </CardHeader>
                      <CardContent>
                        {subtitleCompleteness ? (
                          <div className="space-y-4">
                            <div className="flex flex-wrap items-center gap-2">
                              <CoverageBadge
                                completeness={subtitleCompleteness}
                              />
                              <StatusBadge
                                status={subtitleCompleteness.source_job.status}
                              />
                              <div className="text-sm text-muted-foreground">
                                crawled{" "}
                                {formatDateTime(
                                  subtitleCompleteness.source_job.crawled_at,
                                )}
                              </div>
                            </div>
                            <div className={compactStatsGridClass}>
                              <OverviewField
                                label="Stored"
                                value={formatCount(
                                  subtitleCompleteness.stored_count,
                                )}
                              />
                              <OverviewField
                                label="CIDs"
                                value={formatCount(
                                  subtitleCompleteness.cid_count,
                                )}
                              />
                              <OverviewField
                                label="Languages"
                                value={formatCount(
                                  subtitleCompleteness.languages.length,
                                )}
                              />
                            </div>
                            <div className="flex flex-wrap gap-2">
                              {subtitleCompleteness.languages.length ? (
                                subtitleCompleteness.languages.map(
                                  (language) => (
                                    <Badge key={language} variant="secondary">
                                      {language}
                                    </Badge>
                                  ),
                                )
                              ) : (
                                <Badge variant="outline">
                                  No language labels
                                </Badge>
                              )}
                            </div>
                          </div>
                        ) : (
                          <div className="rounded-lg border border-dashed border-border/70 px-4 py-6 text-sm text-muted-foreground">
                            No subtitle completeness summary is attached to the
                            visible crawl history for this video.
                          </div>
                        )}
                      </CardContent>
                    </Card>

                    <Card className="border-border/70 bg-card/90">
                      <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base">
                          <ListFilter className="size-4 text-primary" />
                          Subtitle Filters
                        </CardTitle>
                        <CardDescription>
                          Narrow tracks by page or language.
                        </CardDescription>
                      </CardHeader>
                      <CardContent className="space-y-4">
                        <div className={filterGridClass}>
                          <div>
                            <div className="mb-2 text-xs uppercase tracking-[0.14em] text-muted-foreground">
                              CID
                            </div>
                            <Input
                              placeholder="e.g. 101"
                              value={subtitleCid}
                              onChange={(event) =>
                                setSubtitleCid(event.target.value)
                              }
                            />
                          </div>
                          <div>
                            <div className="mb-2 text-xs uppercase tracking-[0.14em] text-muted-foreground">
                              Language
                            </div>
                            <Input
                              placeholder="e.g. zh-CN"
                              value={subtitleLang}
                              onChange={(event) =>
                                setSubtitleLang(event.target.value)
                              }
                            />
                          </div>
                          <div>
                            <div className="mb-2 text-xs uppercase tracking-[0.14em] text-muted-foreground">
                              Limit
                            </div>
                            <Select
                              value={subtitleLimit}
                              onValueChange={setSubtitleLimit}
                            >
                              <SelectTrigger className="w-full">
                                <SelectValue placeholder="Select limit" />
                              </SelectTrigger>
                              <SelectContent>
                                {subtitleLimitOptions.map((option) => (
                                  <SelectItem key={option} value={option}>
                                    {option}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                        </div>

                        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                          <div className="text-sm leading-6 text-muted-foreground">
                            {subtitlesQuery.isFetching
                              ? "Refreshing subtitle tracks…"
                              : `${formatCount(subtitlesQuery.data?.count ?? 0)} tracks in the current result set`}
                          </div>
                          <Button
                            className="w-full sm:w-auto"
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              setSubtitleCid("")
                              setSubtitleLang("")
                              setSubtitleLimit("12")
                            }}
                          >
                            Reset filters
                          </Button>
                        </div>
                      </CardContent>
                    </Card>
                  </div>

                  <div className="space-y-4">
                    {subtitlesQuery.isLoading ? (
                      <Card className="border-border/70 bg-card/90">
                        <CardContent className="pt-6 text-sm text-muted-foreground">
                          Loading subtitle tracks…
                        </CardContent>
                      </Card>
                    ) : subtitlesQuery.data?.subtitles.length ? (
                      subtitlesQuery.data.subtitles.map((subtitle) => {
                        const body = parseSubtitleBody(subtitle.content)
                        return (
                          <Card
                            key={subtitle.subtitle_id}
                            className="border-border/70 bg-card/90"
                          >
                            <CardHeader>
                              <CardTitle className="flex items-center gap-2 text-base">
                                <Captions className="size-4 text-primary" />
                                {subtitle.lang || "Unknown language"} · cid{" "}
                                {subtitle.cid ?? "unknown"}
                              </CardTitle>
                              <CardDescription>
                                {subtitle.source || "Unknown source"} · crawled{" "}
                                {formatDateTime(subtitle.crawled_at)}
                              </CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-3">
                              {body.length ? (
                                <div className="space-y-2 rounded-lg border border-border/70 bg-muted/20 p-4">
                                  {body.slice(0, 24).map((line, index) => (
                                    <div
                                      key={`${subtitle.subtitle_id}-${index}`}
                                      className="grid gap-2 text-sm md:grid-cols-[112px_minmax(0,1fr)]"
                                    >
                                      <div className="font-mono text-xs text-muted-foreground">
                                        {formatOffset(line.from)} →{" "}
                                        {formatOffset(line.to)}
                                      </div>
                                      <div>{line.content || ""}</div>
                                    </div>
                                  ))}
                                </div>
                              ) : (
                                <JsonPreview
                                  value={subtitle.content}
                                  emptyLabel="No subtitle body"
                                />
                              )}
                            </CardContent>
                          </Card>
                        )
                      })
                    ) : (
                      <Card className="border-border/70 bg-card/90">
                        <CardContent className="pt-6 text-sm text-muted-foreground">
                          No subtitle tracks matched the current filters.
                        </CardContent>
                      </Card>
                    )}
                  </div>
                </TabsContent>

                <TabsContent value="assets" className="space-y-4">
                  {hlsAsset || playbackAsset ? (
                    <Card className="border-border/70 bg-card/90">
                      <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base">
                          <Film className="size-4 text-primary" />
                          Media Preview
                        </CardTitle>
                        <CardDescription>
                          Optional validation preview only. This is not intended
                          to replicate a consumer playback page.
                        </CardDescription>
                      </CardHeader>
                      <CardContent>
                        <div className="max-w-4xl">
                          <VideoPlayback
                            bvid={selectedVideo.bvid}
                            fallbackAssetId={playbackAsset?.asset_id}
                            hlsAssetId={hlsAsset?.asset_id}
                            posterUrl={selectedVideo.cover_url}
                            preferredCid={hlsAsset?.cid ?? playbackAsset?.cid}
                          />
                        </div>
                      </CardContent>
                    </Card>
                  ) : null}

                  <Card className="border-border/70 bg-card/90">
                    <CardHeader>
                      <CardTitle className="flex items-center gap-2 text-base">
                        <Package className="size-4 text-primary" />
                        Asset Inventory
                      </CardTitle>
                      <CardDescription>
                        Stored media and sidecar assets indexed for this BVID.
                      </CardDescription>
                    </CardHeader>
                    <CardContent>
                      <div className="overflow-x-auto rounded-lg border border-border/70">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>Type</TableHead>
                              <TableHead>Variant</TableHead>
                              <TableHead>CID</TableHead>
                              <TableHead>Status</TableHead>
                              <TableHead>Format</TableHead>
                              <TableHead>Size</TableHead>
                              <TableHead>Ready</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {assets.length ? (
                              assets.map((asset) => (
                                <TableRow key={asset.asset_id}>
                                  <TableCell className="font-medium">
                                    {asset.asset_type}
                                  </TableCell>
                                  <TableCell>
                                    {asset.variant || "default"}
                                  </TableCell>
                                  <TableCell>{asset.cid ?? "—"}</TableCell>
                                  <TableCell>
                                    <StatusBadge status={asset.status} />
                                  </TableCell>
                                  <TableCell>
                                    {asset.content_type ||
                                      asset.container_format ||
                                      "Unknown"}
                                  </TableCell>
                                  <TableCell>
                                    {formatBytes(asset.size_bytes)}
                                  </TableCell>
                                  <TableCell>
                                    {formatDateTime(
                                      asset.ready_at || asset.created_at,
                                    )}
                                  </TableCell>
                                </TableRow>
                              ))
                            ) : (
                              <TableRow>
                                <TableCell
                                  className="text-muted-foreground"
                                  colSpan={7}
                                >
                                  No assets are indexed for this video yet.
                                </TableCell>
                              </TableRow>
                            )}
                          </TableBody>
                        </Table>
                      </div>
                    </CardContent>
                  </Card>
                </TabsContent>

                <TabsContent value="jobs" className="space-y-4">
                  <Card className="border-border/70 bg-card/90">
                    <CardHeader>
                      <CardTitle className="flex items-center gap-2 text-base">
                        <Clock3 className="size-4 text-primary" />
                        Latest Job Detail
                      </CardTitle>
                      <CardDescription>
                        Most recent visible ingest job plus its current progress
                        payload.
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      {latestJobSummary ? (
                        <>
                          <div className={compactStatsGridClass}>
                            <OverviewField
                              label="Status"
                              value={
                                <StatusBadge status={latestJobSummary.status} />
                              }
                            />
                            <OverviewField
                              label="Phase"
                              value={latestJobSummary.phase || "Unknown"}
                            />
                            <OverviewField
                              label="Created"
                              value={formatDateTime(
                                latestJobSummary.created_at,
                              )}
                            />
                            <OverviewField
                              label="Finished"
                              value={formatDateTime(
                                latestJobSummary.finished_at,
                              )}
                            />
                          </div>
                          {latestJobSummary.error?.message ? (
                            <div className="rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-sm text-rose-700 dark:text-rose-300">
                              {latestJobSummary.error.message}
                            </div>
                          ) : null}
                          <JsonPreview
                            value={latestJobQuery.data?.progress}
                            emptyLabel={
                              latestJobQuery.isLoading
                                ? "Loading latest job progress"
                                : "No progress payload"
                            }
                          />
                        </>
                      ) : (
                        <div className="rounded-lg border border-dashed border-border/70 px-4 py-6 text-sm text-muted-foreground">
                          No ingest jobs are visible for this BVID.
                        </div>
                      )}
                    </CardContent>
                  </Card>

                  <Card className="border-border/70 bg-card/90">
                    <CardHeader>
                      <CardTitle>Recent Jobs</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <div className="overflow-x-auto rounded-lg border border-border/70">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>Status</TableHead>
                              <TableHead>Phase</TableHead>
                              <TableHead>Created</TableHead>
                              <TableHead>Finished</TableHead>
                              <TableHead>Error</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {jobsQuery.data?.data.length ? (
                              jobsQuery.data.data.map((job) => (
                                <TableRow key={job.job_id}>
                                  <TableCell>
                                    <StatusBadge status={job.status} />
                                  </TableCell>
                                  <TableCell className="max-w-md whitespace-normal">
                                    {job.phase || "Unknown"}
                                  </TableCell>
                                  <TableCell>
                                    {formatDateTime(job.created_at)}
                                  </TableCell>
                                  <TableCell>
                                    {formatDateTime(job.finished_at)}
                                  </TableCell>
                                  <TableCell className="max-w-sm whitespace-normal text-sm text-muted-foreground">
                                    {job.error?.message || "—"}
                                  </TableCell>
                                </TableRow>
                              ))
                            ) : (
                              <TableRow>
                                <TableCell
                                  className="text-muted-foreground"
                                  colSpan={5}
                                >
                                  No ingest jobs recorded for this BVID.
                                </TableCell>
                              </TableRow>
                            )}
                          </TableBody>
                        </Table>
                      </div>
                    </CardContent>
                  </Card>
                </TabsContent>

                <TabsContent value="raw" className="space-y-4">
                  <div className="grid gap-4 xl:grid-cols-2">
                    <Card className="border-border/70 bg-card/90">
                      <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base">
                          <FileJson className="size-4 text-primary" />
                          Video Stat Payload
                        </CardTitle>
                      </CardHeader>
                      <CardContent>
                        <JsonPreview value={selectedVideo.stat} />
                      </CardContent>
                    </Card>

                    <Card className="border-border/70 bg-card/90">
                      <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base">
                          <FileJson className="size-4 text-primary" />
                          Latest Job Progress
                        </CardTitle>
                      </CardHeader>
                      <CardContent>
                        <JsonPreview
                          value={latestJobQuery.data?.progress}
                          emptyLabel="No visible job progress"
                        />
                      </CardContent>
                    </Card>

                    <Card className="border-border/70 bg-card/90">
                      <CardHeader>
                        <CardTitle>Comments Completeness JSON</CardTitle>
                      </CardHeader>
                      <CardContent>
                        <JsonPreview
                          value={commentCompleteness}
                          emptyLabel="No comments completeness summary"
                        />
                      </CardContent>
                    </Card>

                    <Card className="border-border/70 bg-card/90">
                      <CardHeader>
                        <CardTitle>Danmaku Completeness JSON</CardTitle>
                      </CardHeader>
                      <CardContent>
                        <JsonPreview
                          value={danmakuCompleteness}
                          emptyLabel="No danmaku completeness summary"
                        />
                      </CardContent>
                    </Card>

                    <Card className="border-border/70 bg-card/90 xl:col-span-2">
                      <CardHeader>
                        <CardTitle>Subtitles Completeness JSON</CardTitle>
                      </CardHeader>
                      <CardContent>
                        <JsonPreview
                          value={subtitleCompleteness}
                          emptyLabel="No subtitles completeness summary"
                        />
                      </CardContent>
                    </Card>
                  </div>
                </TabsContent>
              </Tabs>
            </>
          ) : (
            <Card className="border-border/70 bg-card/90">
              <CardContent className="flex min-h-80 flex-col items-center justify-center text-center">
                <MessageCircleMore className="mb-4 size-10 text-muted-foreground" />
                <div className="text-lg font-semibold">Select a video</div>
                <p className="mt-2 max-w-md text-sm text-muted-foreground">
                  Choose a BVID from the catalog to inspect stored rows,
                  completeness summaries, and recent ingest jobs.
                </p>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}
