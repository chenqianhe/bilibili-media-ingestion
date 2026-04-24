import { useQuery } from "@tanstack/react-query"
import { Play, Waves } from "lucide-react"
import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import { IngestionApi } from "@/lib/ingestionApi"

const EMPTY_CAPTIONS_TRACK =
  "data:text/vtt;charset=utf-8,WEBVTT%0A%0A00:00:00.000%20-->%2000:00:00.001%0A"

export function VideoPlayback({
  hlsAssetId,
  fallbackAssetId,
  posterUrl,
}: {
  hlsAssetId?: string
  fallbackAssetId?: string
  posterUrl?: string | null
}) {
  const [canPlayNativeHls, setCanPlayNativeHls] = useState(false)

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

  const preferredMode =
    canPlayNativeHls && hlsUrlQuery.data?.url ? "hls" : "proxy"
  const playbackUrl =
    preferredMode === "hls" ? hlsUrlQuery.data?.url : fallbackUrlQuery.data?.url
  const playbackLabel = preferredMode === "hls" ? "Native HLS" : "Proxy MP4"
  const isLoading =
    hlsUrlQuery.isLoading ||
    (preferredMode === "proxy" && fallbackUrlQuery.isLoading)

  if (!hlsAssetId && !fallbackAssetId) {
    return (
      <div className="flex min-h-72 flex-col items-center justify-center rounded-[28px] border border-dashed border-border/70 bg-muted/20 px-6 text-center">
        <Play className="mb-4 size-10 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">
          This video does not have a playable derivative yet.
        </p>
      </div>
    )
  }

  if (isLoading && !playbackUrl) {
    return (
      <div className="flex min-h-72 flex-col items-center justify-center rounded-[28px] border border-border/70 bg-muted/20 px-6 text-center">
        <Waves className="mb-4 size-10 animate-pulse text-primary" />
        <p className="text-sm text-muted-foreground">
          Preparing a signed playback URL.
        </p>
      </div>
    )
  }

  if (!playbackUrl) {
    return (
      <div className="flex min-h-72 flex-col items-center justify-center rounded-[28px] border border-dashed border-border/70 bg-muted/20 px-6 text-center">
        <Play className="mb-4 size-10 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">
          Playback is available in the backend, but no browser-compatible source
          was resolved for this client.
        </p>
      </div>
    )
  }

  return (
    <div className="overflow-hidden rounded-[28px] border border-border/70 bg-card shadow-sm">
      <div className="flex items-center justify-between border-b border-border/70 bg-gradient-to-r from-sky-500/10 via-cyan-500/10 to-emerald-500/10 px-4 py-3">
        <div>
          <div className="text-sm font-semibold">Playback</div>
          <div className="text-xs text-muted-foreground">{playbackLabel}</div>
        </div>
        <Button asChild size="sm" variant="outline">
          <a href={playbackUrl} rel="noreferrer" target="_blank">
            Open Source
          </a>
        </Button>
      </div>
      <video
        className="aspect-video w-full bg-black"
        controls
        playsInline
        poster={posterUrl ?? undefined}
        preload="metadata"
        src={playbackUrl}
      >
        <track
          default
          kind="captions"
          label="Captions unavailable"
          src={EMPTY_CAPTIONS_TRACK}
          srcLang="en"
        />
      </video>
    </div>
  )
}
