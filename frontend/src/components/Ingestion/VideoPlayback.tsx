import { useQuery } from "@tanstack/react-query"
import {
  Captions,
  ExternalLink,
  MessageCircleMore,
  Play,
  Waves,
} from "lucide-react"
import { type CSSProperties, useEffect, useMemo, useRef, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  IngestionApi,
  type VideoDanmakuEntry,
  type VideoSubtitle,
} from "@/lib/ingestionApi"

const DANMAKU_DISPLAY_SECONDS = 5.5
const DANMAKU_LANE_COUNT = 6
const DANMAKU_RESET_JUMP_SECONDS = 1.5
const DANMAKU_LEAD_SECONDS = 0.08
const PREVIEW_DANMAKU_LIMIT = 800
const PREVIEW_SUBTITLE_LIMIT = 100
const EMPTY_CAPTIONS_TRACK =
  "data:text/vtt;charset=utf-8,WEBVTT%0A%0A00:00:00.000%20-->%2000:00:00.001%0A"

type SubtitleCue = {
  startSeconds: number
  endSeconds: number
  text: string
}

type ActiveDanmakuItem = {
  key: string
  lane: number
  text: string
  colorHex?: string
  expiresAtSeconds: number
}

function formatPlaybackTime(totalSeconds: number) {
  const safeSeconds = Math.max(0, Math.floor(totalSeconds))
  const hours = Math.floor(safeSeconds / 3600)
  const minutes = Math.floor((safeSeconds % 3600) / 60)
  const seconds = safeSeconds % 60

  if (hours > 0) {
    return `${hours}:${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`
  }

  return `${minutes}:${seconds.toString().padStart(2, "0")}`
}

function parseCueTimestamp(value: string) {
  const match = value.trim().match(/^(\d+):(\d+):(\d+)(?:[.,](\d{1,3}))?$/)
  if (!match) {
    return undefined
  }

  const hours = Number(match[1])
  const minutes = Number(match[2])
  const seconds = Number(match[3])
  const milliseconds = Number((match[4] ?? "0").padEnd(3, "0"))

  if (
    !Number.isFinite(hours) ||
    !Number.isFinite(minutes) ||
    !Number.isFinite(seconds) ||
    !Number.isFinite(milliseconds)
  ) {
    return undefined
  }

  return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000
}

function formatVttTimestamp(totalSeconds: number) {
  const safeSeconds = Math.max(0, totalSeconds)
  const hours = Math.floor(safeSeconds / 3600)
  const minutes = Math.floor((safeSeconds % 3600) / 60)
  const seconds = Math.floor(safeSeconds % 60)
  const milliseconds = Math.round(
    (safeSeconds - Math.floor(safeSeconds)) * 1000,
  )

  return `${hours.toString().padStart(2, "0")}:${minutes
    .toString()
    .padStart(2, "0")}:${seconds.toString().padStart(2, "0")}.${milliseconds
    .toString()
    .padStart(3, "0")}`
}

function buildWebVttTrack(cues: SubtitleCue[]) {
  if (!cues.length) {
    return "WEBVTT\n"
  }

  const blocks = cues.map((cue, index) =>
    [
      `${index + 1}`,
      `${formatVttTimestamp(cue.startSeconds)} --> ${formatVttTimestamp(cue.endSeconds)}`,
      cue.text,
    ].join("\n"),
  )

  return `WEBVTT\n\n${blocks.join("\n\n")}\n`
}

function parseSubtitleCues(content?: string | null): SubtitleCue[] {
  if (!content?.trim()) {
    return []
  }

  const trimmed = content.trim()
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      const parsed = JSON.parse(trimmed) as {
        body?: Array<{ from?: number; to?: number; content?: string }>
        segments?: Array<{
          start?: number
          end?: number
          text?: string
          content?: string
        }>
      }

      if (Array.isArray(parsed.body)) {
        return parsed.body.flatMap((line) => {
          const startSeconds = line.from
          const endSeconds = line.to
          const text = line.content?.trim()
          if (
            typeof startSeconds !== "number" ||
            typeof endSeconds !== "number" ||
            !text
          ) {
            return []
          }
          return [{ startSeconds, endSeconds, text }]
        })
      }

      if (Array.isArray(parsed.segments)) {
        return parsed.segments.flatMap((line) => {
          const startSeconds = line.start
          const endSeconds = line.end
          const text = (line.text ?? line.content)?.trim()
          if (
            typeof startSeconds !== "number" ||
            typeof endSeconds !== "number" ||
            !text
          ) {
            return []
          }
          return [{ startSeconds, endSeconds, text }]
        })
      }
    } catch {
      return []
    }
  }

  const normalized = content.replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim()
  const blocks = normalized.split(/\n{2,}/)
  const cues: SubtitleCue[] = []

  for (const block of blocks) {
    const lines = block
      .split("\n")
      .map((line: string) => line.trimEnd())
      .filter(Boolean)

    if (!lines.length) {
      continue
    }

    let timingLineIndex = 0
    if (/^\d+$/.test(lines[0] ?? "") && lines[1]?.includes("-->")) {
      timingLineIndex = 1
    }

    const timingLine = lines[timingLineIndex]
    if (!timingLine?.includes("-->")) {
      continue
    }

    const [rawStart, rawEnd] = timingLine.split("-->")
    const startSeconds = parseCueTimestamp((rawStart ?? "").trim())
    const endSeconds = parseCueTimestamp(
      ((rawEnd ?? "").trim().split(/\s+/)[0] ?? "").trim(),
    )
    const text = lines
      .slice(timingLineIndex + 1)
      .join("\n")
      .trim()

    if (
      typeof startSeconds !== "number" ||
      typeof endSeconds !== "number" ||
      !text
    ) {
      continue
    }

    cues.push({
      startSeconds,
      endSeconds,
      text,
    })
  }

  return cues
}

function rankSubtitleTrack(track: VideoSubtitle, preferredCid?: number | null) {
  let score = 0

  if (preferredCid !== null && preferredCid !== undefined) {
    if (track.cid === preferredCid) {
      score += 100
    } else if (track.cid === null || track.cid === undefined) {
      score += 60
    }
  } else if (track.cid === null || track.cid === undefined) {
    score += 40
  } else {
    score += 20
  }

  if (track.source === "openai_stt") {
    score += 15
  }

  if ((track.lang ?? "").toLowerCase().startsWith("zh")) {
    score += 5
  }

  return score
}

function pickPreferredSubtitle(
  subtitles: VideoSubtitle[],
  preferredCid?: number | null,
) {
  if (!subtitles.length) {
    return null
  }

  return [...subtitles].sort((left, right) => {
    const scoreDifference =
      rankSubtitleTrack(right, preferredCid) -
      rankSubtitleTrack(left, preferredCid)
    if (scoreDifference !== 0) {
      return scoreDifference
    }

    return Date.parse(right.crawled_at) - Date.parse(left.crawled_at)
  })[0]
}

function chooseDanmakuCid(
  entries: VideoDanmakuEntry[],
  preferredCid?: number | null,
  fallbackCid?: number | null,
) {
  if (preferredCid !== null && preferredCid !== undefined) {
    return preferredCid
  }

  if (fallbackCid !== null && fallbackCid !== undefined) {
    return fallbackCid
  }

  const countsByCid = new Map<number, number>()
  for (const entry of entries) {
    countsByCid.set(entry.cid, (countsByCid.get(entry.cid) ?? 0) + 1)
  }

  let bestCid: number | undefined
  let bestCount = -1
  for (const [cid, count] of countsByCid.entries()) {
    if (count > bestCount) {
      bestCid = cid
      bestCount = count
    }
  }

  return bestCid
}

function prepareDanmakuEntries(
  entries: VideoDanmakuEntry[],
  activeCid?: number | null,
) {
  return entries
    .filter((entry) => {
      if (
        typeof entry.time_offset_seconds !== "number" ||
        !Number.isFinite(entry.time_offset_seconds)
      ) {
        return false
      }

      if (!entry.content?.trim()) {
        return false
      }

      return activeCid === null || activeCid === undefined
        ? true
        : entry.cid === activeCid
    })
    .sort((left, right) => {
      if (
        typeof left.time_offset_seconds === "number" &&
        typeof right.time_offset_seconds === "number" &&
        left.time_offset_seconds !== right.time_offset_seconds
      ) {
        return left.time_offset_seconds - right.time_offset_seconds
      }
      return (
        (Date.parse(left.sent_at ?? "") || 0) -
        (Date.parse(right.sent_at ?? "") || 0)
      )
    })
}

function findActiveSubtitleCue(
  cues: SubtitleCue[],
  currentTimeSeconds: number,
) {
  return (
    cues.find(
      (cue) =>
        currentTimeSeconds >= cue.startSeconds &&
        currentTimeSeconds <= cue.endSeconds + 0.05,
    ) ?? null
  )
}

function findNextDanmakuIndex(
  entries: VideoDanmakuEntry[],
  currentTimeSeconds: number,
) {
  let low = 0
  let high = entries.length

  while (low < high) {
    const middle = Math.floor((low + high) / 2)
    const candidate = entries[middle]?.time_offset_seconds ?? 0
    if (candidate < currentTimeSeconds) {
      low = middle + 1
    } else {
      high = middle
    }
  }

  return low
}

function pickDanmakuLane(
  activeItems: ActiveDanmakuItem[],
  currentTimeSeconds: number,
) {
  for (let lane = 0; lane < DANMAKU_LANE_COUNT; lane += 1) {
    const laneBlocked = activeItems.some(
      (item) =>
        item.lane === lane && item.expiresAtSeconds > currentTimeSeconds + 0.8,
    )
    if (!laneBlocked) {
      return lane
    }
  }

  let nextLane = 0
  let earliestExpiry = Number.POSITIVE_INFINITY
  for (let lane = 0; lane < DANMAKU_LANE_COUNT; lane += 1) {
    const laneExpiry = activeItems.reduce((latestExpiry, item) => {
      if (item.lane !== lane) {
        return latestExpiry
      }
      return Math.max(latestExpiry, item.expiresAtSeconds)
    }, currentTimeSeconds)

    if (laneExpiry < earliestExpiry) {
      earliestExpiry = laneExpiry
      nextLane = lane
    }
  }

  return nextLane
}

function formatDanmakuColor(color?: number | null) {
  if (
    typeof color !== "number" ||
    !Number.isFinite(color) ||
    color < 0 ||
    color > 0xffffff
  ) {
    return undefined
  }

  return `#${color.toString(16).padStart(6, "0")}`
}

function describeSubtitleTrack(track: VideoSubtitle | null) {
  if (!track) {
    return "No synced subtitle track"
  }

  return [
    track.lang || "Unknown language",
    track.source || "unknown source",
    track.cid !== null && track.cid !== undefined
      ? `cid ${track.cid}`
      : "no cid",
  ].join(" · ")
}

export function VideoPlayback({
  bvid,
  hlsAssetId,
  fallbackAssetId,
  posterUrl,
  preferredCid,
}: {
  bvid: string
  hlsAssetId?: string
  fallbackAssetId?: string
  posterUrl?: string | null
  preferredCid?: number | null
}) {
  const [canPlayNativeHls, setCanPlayNativeHls] = useState(false)
  const [showSubtitles, setShowSubtitles] = useState(true)
  const [showDanmaku, setShowDanmaku] = useState(true)
  const [currentTimeSeconds, setCurrentTimeSeconds] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)
  const [activeDanmaku, setActiveDanmaku] = useState<ActiveDanmakuItem[]>([])
  const [captionTrackUrl, setCaptionTrackUrl] = useState<string>()
  const currentTimeRef = useRef(0)
  const lastDanmakuTimeRef = useRef(0)
  const nextDanmakuIndexRef = useRef(0)

  useEffect(() => {
    const probe = document.createElement("video")
    setCanPlayNativeHls(
      Boolean(probe.canPlayType("application/vnd.apple.mpegurl")),
    )
  }, [])

  const hlsUrlQuery = useQuery({
    queryKey: ["media-playback-url", "hls", hlsAssetId],
    queryFn: () => IngestionApi.createPlaybackUrl(hlsAssetId ?? ""),
    enabled: Boolean(hlsAssetId),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  const fallbackUrlQuery = useQuery({
    queryKey: ["media-playback-url", "fallback", fallbackAssetId],
    queryFn: () => IngestionApi.createPlaybackUrl(fallbackAssetId ?? ""),
    enabled: Boolean(fallbackAssetId),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })
  const subtitlesQuery = useQuery({
    queryKey: ["media-preview-subtitles", bvid],
    queryFn: () =>
      IngestionApi.readVideoSubtitles(bvid, {
        limit: PREVIEW_SUBTITLE_LIMIT,
        offset: 0,
      }),
    enabled: Boolean(bvid),
    staleTime: 60 * 1000,
    refetchOnWindowFocus: false,
  })
  const danmakuQuery = useQuery({
    queryKey: ["media-preview-danmaku", bvid, preferredCid ?? null],
    queryFn: () =>
      IngestionApi.readVideoDanmaku(bvid, {
        cid: preferredCid ?? undefined,
        limit: PREVIEW_DANMAKU_LIMIT,
        offset: 0,
      }),
    enabled: Boolean(bvid),
    staleTime: 60 * 1000,
    refetchOnWindowFocus: false,
  })

  const preferredMode =
    canPlayNativeHls && hlsUrlQuery.data?.url ? "hls" : "proxy"
  const playbackUrl =
    preferredMode === "hls" ? hlsUrlQuery.data?.url : fallbackUrlQuery.data?.url
  const playbackLabel = preferredMode === "hls" ? "Native HLS" : "Proxy MP4"
  const isLoading =
    hlsUrlQuery.isLoading ||
    (preferredMode === "proxy" && fallbackUrlQuery.isLoading)
  const subtitleTracks = subtitlesQuery.data?.subtitles ?? []
  const selectedSubtitle = useMemo(
    () => pickPreferredSubtitle(subtitleTracks, preferredCid),
    [preferredCid, subtitleTracks],
  )
  const subtitleCues = useMemo(
    () => parseSubtitleCues(selectedSubtitle?.content),
    [selectedSubtitle?.content],
  )
  const resolvedDanmakuCid = useMemo(
    () =>
      chooseDanmakuCid(
        danmakuQuery.data?.danmaku ?? [],
        preferredCid,
        selectedSubtitle?.cid,
      ),
    [danmakuQuery.data?.danmaku, preferredCid, selectedSubtitle?.cid],
  )
  const sortedDanmakuEntries = useMemo(
    () =>
      prepareDanmakuEntries(
        danmakuQuery.data?.danmaku ?? [],
        resolvedDanmakuCid,
      ),
    [danmakuQuery.data?.danmaku, resolvedDanmakuCid],
  )
  const activeSubtitleCue = useMemo(
    () => findActiveSubtitleCue(subtitleCues, currentTimeSeconds),
    [currentTimeSeconds, subtitleCues],
  )
  const danmakuIsTruncated =
    (danmakuQuery.data?.count ?? 0) > sortedDanmakuEntries.length

  useEffect(() => {
    if (!playbackUrl) {
      return
    }

    setCurrentTimeSeconds(0)
    currentTimeRef.current = 0
    setIsPlaying(false)
    setActiveDanmaku([])
    lastDanmakuTimeRef.current = 0
    nextDanmakuIndexRef.current = 0
  }, [playbackUrl])

  useEffect(() => {
    if (!subtitleCues.length) {
      setCaptionTrackUrl(undefined)
      return
    }

    const nextTrackUrl = URL.createObjectURL(
      new Blob([buildWebVttTrack(subtitleCues)], {
        type: "text/vtt;charset=utf-8",
      }),
    )
    setCaptionTrackUrl(nextTrackUrl)

    return () => {
      URL.revokeObjectURL(nextTrackUrl)
    }
  }, [subtitleCues])

  useEffect(() => {
    const snapshotTimeSeconds = currentTimeRef.current
    setActiveDanmaku((current) => (current.length ? [] : current))
    lastDanmakuTimeRef.current = snapshotTimeSeconds
    nextDanmakuIndexRef.current = findNextDanmakuIndex(
      sortedDanmakuEntries,
      snapshotTimeSeconds,
    )
  }, [sortedDanmakuEntries.length, sortedDanmakuEntries])

  useEffect(() => {
    if (!showDanmaku || !sortedDanmakuEntries.length) {
      setActiveDanmaku((current) => (current.length ? [] : current))
      lastDanmakuTimeRef.current = currentTimeSeconds
      nextDanmakuIndexRef.current = findNextDanmakuIndex(
        sortedDanmakuEntries,
        currentTimeSeconds,
      )
      return
    }

    const previousTime = lastDanmakuTimeRef.current
    const jumpedBackward = currentTimeSeconds + 0.05 < previousTime
    const jumpedForward =
      currentTimeSeconds - previousTime > DANMAKU_RESET_JUMP_SECONDS

    if (jumpedBackward || jumpedForward) {
      setActiveDanmaku((current) => (current.length ? [] : current))
      nextDanmakuIndexRef.current = findNextDanmakuIndex(
        sortedDanmakuEntries,
        currentTimeSeconds,
      )
      lastDanmakuTimeRef.current = currentTimeSeconds
      return
    }

    const spawnedEntries: Array<{ entry: VideoDanmakuEntry; index: number }> =
      []
    let nextIndex = nextDanmakuIndexRef.current

    while (nextIndex < sortedDanmakuEntries.length) {
      const nextEntry = sortedDanmakuEntries[nextIndex]
      const nextOffset = nextEntry?.time_offset_seconds
      if (typeof nextOffset !== "number") {
        nextIndex += 1
        continue
      }

      if (nextOffset > currentTimeSeconds + DANMAKU_LEAD_SECONDS) {
        break
      }

      if (nextOffset >= previousTime - 0.05) {
        spawnedEntries.push({ entry: nextEntry, index: nextIndex })
      }

      nextIndex += 1
    }

    nextDanmakuIndexRef.current = nextIndex
    setActiveDanmaku((current) => {
      let nextActive = current.filter(
        (item) => item.expiresAtSeconds > currentTimeSeconds,
      )

      if (!spawnedEntries.length) {
        return nextActive.length === current.length ? current : nextActive
      }

      for (const { entry, index } of spawnedEntries) {
        const offsetSeconds = entry.time_offset_seconds
        const content = entry.content?.trim()
        if (typeof offsetSeconds !== "number" || !content) {
          continue
        }

        const lane = pickDanmakuLane(nextActive, currentTimeSeconds)
        nextActive = [
          ...nextActive.slice(-11),
          {
            key: `${entry.cid}-${entry.danmaku_id ?? "no-id"}-${offsetSeconds}-${entry.sent_at ?? "no-sent-at"}-${index}`,
            lane,
            text: content,
            colorHex: formatDanmakuColor(entry.color),
            expiresAtSeconds: offsetSeconds + DANMAKU_DISPLAY_SECONDS,
          },
        ]
      }

      return nextActive
    })
    lastDanmakuTimeRef.current = currentTimeSeconds
  }, [currentTimeSeconds, showDanmaku, sortedDanmakuEntries])

  const handleTimelineSync = (nextTimeSeconds: number) => {
    currentTimeRef.current = nextTimeSeconds
    setCurrentTimeSeconds(nextTimeSeconds)
  }

  const previewStatusBadges = (
    <>
      {subtitleCues.length ? (
        <Badge variant="secondary">
          {showSubtitles ? "Subtitles On" : "Subtitles Off"}
        </Badge>
      ) : null}
      {sortedDanmakuEntries.length ? (
        <Badge variant="secondary">
          {showDanmaku ? "Danmaku On" : "Danmaku Off"}
        </Badge>
      ) : null}
      {resolvedDanmakuCid !== null && resolvedDanmakuCid !== undefined ? (
        <Badge variant="outline">cid {resolvedDanmakuCid}</Badge>
      ) : null}
      <Badge variant="outline">{formatPlaybackTime(currentTimeSeconds)}</Badge>
    </>
  )

  if (!hlsAssetId && !fallbackAssetId) {
    return (
      <div className="flex min-h-72 flex-col items-center justify-center rounded-lg border border-dashed border-border/70 bg-muted/20 px-6 text-center">
        <Play className="mb-4 size-10 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">
          This video does not have a playable derivative yet.
        </p>
      </div>
    )
  }

  if (isLoading && !playbackUrl) {
    return (
      <div className="flex min-h-72 flex-col items-center justify-center rounded-lg border border-border/70 bg-muted/20 px-6 text-center">
        <Waves className="mb-4 size-10 animate-pulse text-primary" />
        <p className="text-sm text-muted-foreground">
          Preparing a signed playback URL.
        </p>
      </div>
    )
  }

  if (!playbackUrl) {
    return (
      <div className="flex min-h-72 flex-col items-center justify-center rounded-lg border border-dashed border-border/70 bg-muted/20 px-6 text-center">
        <Play className="mb-4 size-10 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">
          Playback is available in the backend, but no browser-compatible source
          was resolved for this client.
        </p>
      </div>
    )
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border/70 bg-card shadow-sm">
      <div className="flex flex-col gap-3 border-b border-border/70 bg-muted/20 px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="text-sm font-semibold">Playback</div>
          <div className="text-xs text-muted-foreground">{playbackLabel}</div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {previewStatusBadges}
          <Button
            size="sm"
            variant={showSubtitles ? "default" : "outline"}
            disabled={!subtitleCues.length}
            onClick={() => setShowSubtitles((current) => !current)}
          >
            <Captions className="size-4" />
            Subtitles
          </Button>
          <Button
            size="sm"
            variant={showDanmaku ? "default" : "outline"}
            disabled={!sortedDanmakuEntries.length}
            onClick={() => setShowDanmaku((current) => !current)}
          >
            <MessageCircleMore className="size-4" />
            Danmaku
          </Button>
          <Button asChild size="sm" variant="outline">
            <a href={playbackUrl} rel="noreferrer" target="_blank">
              <ExternalLink className="size-4" />
              Open Source
            </a>
          </Button>
        </div>
      </div>
      <div className="relative bg-black">
        <video
          className="aspect-video w-full bg-black"
          controls
          playsInline
          poster={posterUrl ?? undefined}
          preload="metadata"
          src={playbackUrl}
          onLoadedMetadata={(event) => {
            handleTimelineSync(event.currentTarget.currentTime)
            nextDanmakuIndexRef.current = findNextDanmakuIndex(
              sortedDanmakuEntries,
              event.currentTarget.currentTime,
            )
          }}
          onPause={(event) => {
            setIsPlaying(false)
            handleTimelineSync(event.currentTarget.currentTime)
          }}
          onPlay={(event) => {
            setIsPlaying(true)
            handleTimelineSync(event.currentTarget.currentTime)
          }}
          onSeeked={(event) => {
            const nextTimeSeconds = event.currentTarget.currentTime
            setActiveDanmaku([])
            lastDanmakuTimeRef.current = nextTimeSeconds
            nextDanmakuIndexRef.current = findNextDanmakuIndex(
              sortedDanmakuEntries,
              nextTimeSeconds,
            )
            handleTimelineSync(nextTimeSeconds)
          }}
          onTimeUpdate={(event) =>
            handleTimelineSync(event.currentTarget.currentTime)
          }
        >
          <track
            kind="captions"
            label={
              selectedSubtitle
                ? describeSubtitleTrack(selectedSubtitle)
                : "Captions unavailable"
            }
            src={captionTrackUrl ?? EMPTY_CAPTIONS_TRACK}
            srcLang={selectedSubtitle?.lang ?? "und"}
          />
        </video>

        {showDanmaku && activeDanmaku.length ? (
          <div className="pointer-events-none absolute inset-x-0 top-0 h-[68%] overflow-hidden px-3 py-3">
            {activeDanmaku.map((item) => {
              const style: CSSProperties = {
                top: `${0.75 + item.lane * 2.35}rem`,
                color: item.colorHex,
                textShadow: "0 1px 2px rgba(0, 0, 0, 0.9)",
                animation: `media-preview-danmaku ${DANMAKU_DISPLAY_SECONDS}s linear forwards`,
                animationPlayState: isPlaying ? "running" : "paused",
              }

              return (
                <div
                  key={item.key}
                  className="absolute right-[-18rem] max-w-[min(72%,28rem)] overflow-hidden text-ellipsis whitespace-nowrap rounded-md border border-white/15 bg-black/50 px-3 py-1 text-sm font-medium text-white shadow-lg backdrop-blur-sm"
                  style={style}
                >
                  {item.text}
                </div>
              )
            })}
          </div>
        ) : null}

        {showSubtitles && activeSubtitleCue ? (
          <div className="pointer-events-none absolute inset-x-0 bottom-14 flex justify-center px-4">
            <div className="max-w-3xl rounded-lg border border-white/10 bg-black/70 px-4 py-2 text-center text-sm font-medium leading-6 text-white shadow-lg backdrop-blur-sm">
              {activeSubtitleCue.text.split("\n").map((line, index) => (
                <div key={`${activeSubtitleCue.startSeconds}-${index}`}>
                  {line}
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      <div className="grid gap-3 border-t border-border/70 bg-muted/10 px-4 py-3 text-xs text-muted-foreground md:grid-cols-[minmax(0,1fr)_auto]">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={selectedSubtitle ? "secondary" : "outline"}>
              {selectedSubtitle ? "Subtitle Track" : "No Subtitle Track"}
            </Badge>
            {selectedSubtitle ? (
              <span>{describeSubtitleTrack(selectedSubtitle)}</span>
            ) : subtitlesQuery.isLoading ? (
              <span>Loading subtitle track metadata…</span>
            ) : null}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Badge
              variant={sortedDanmakuEntries.length ? "secondary" : "outline"}
            >
              {sortedDanmakuEntries.length ? "Danmaku Feed" : "No Danmaku"}
            </Badge>
            {sortedDanmakuEntries.length ? (
              <span>
                {sortedDanmakuEntries.length.toLocaleString()} rows synced
                {danmakuIsTruncated
                  ? ` from ${danmakuQuery.data?.count?.toLocaleString() ?? "more"} stored rows`
                  : ""}
              </span>
            ) : danmakuQuery.isLoading ? (
              <span>Loading danmaku rows…</span>
            ) : null}
          </div>

          {selectedSubtitle && !subtitleCues.length ? (
            <div>
              This subtitle track is stored, but its content could not be parsed
              into timed preview cues yet.
            </div>
          ) : null}

          {danmakuIsTruncated ? (
            <div>
              Preview mode only loads the first{" "}
              {PREVIEW_DANMAKU_LIMIT.toLocaleString()} danmaku rows for the
              active feed.
            </div>
          ) : null}

          {subtitlesQuery.isError ? (
            <div>
              Subtitle preview rows could not be loaded for this client.
            </div>
          ) : null}

          {danmakuQuery.isError ? (
            <div>Danmaku preview rows could not be loaded for this client.</div>
          ) : null}
        </div>
      </div>
    </div>
  )
}
