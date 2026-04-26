import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import {
  ArrowRight,
  CircleAlert,
  Clock3,
  DatabaseZap,
  Film,
} from "lucide-react"
import { useEffect, useId, useState } from "react"

import { JsonPreview } from "@/components/Ingestion/JsonPreview"
import { StatusBadge } from "@/components/Ingestion/StatusBadge"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import {
  defaultIngestOptions,
  IngestionApi,
  type IngestJobSummary,
  type IngestVideoOptions,
} from "@/lib/ingestionApi"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/")({
  component: Dashboard,
  head: () => ({
    meta: [
      {
        title: "Ingestion Console",
      },
    ],
  }),
})

const numberFormatter = new Intl.NumberFormat("en-US")
const downloadQualityOptions = [
  { value: "best", label: "Best available" },
  { value: "2160", label: "2160p (4K)" },
  { value: "1440", label: "1440p" },
  { value: "1080", label: "1080p" },
  { value: "720", label: "720p" },
  { value: "480", label: "480p" },
  { value: "360", label: "360p" },
] as const

function formatDateTime(value?: string | null) {
  if (!value) {
    return "Not yet"
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

function MetricCard({
  label,
  value,
  hint,
  icon: Icon,
}: {
  label: string
  value: string
  hint: string
  icon: typeof Film
}) {
  return (
    <Card className="border-border/70 bg-card/80 backdrop-blur">
      <CardContent className="flex items-start justify-between gap-4 pt-6">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            {label}
          </div>
          <div className="mt-3 text-3xl font-semibold">{value}</div>
          <p className="mt-2 text-sm text-muted-foreground">{hint}</p>
        </div>
        <div className="rounded-lg border border-border/70 bg-muted/40 p-3">
          <Icon className="size-5 text-primary" />
        </div>
      </CardContent>
    </Card>
  )
}

function OptionToggle({
  checked,
  description,
  label,
  onCheckedChange,
}: {
  checked: boolean
  label: string
  description: string
  onCheckedChange: (checked: boolean) => void
}) {
  const checkboxId = useId()

  return (
    <div className="flex items-start gap-3 rounded-lg border border-border/70 bg-muted/20 p-4">
      <Checkbox
        id={checkboxId}
        checked={checked}
        className="mt-1"
        onCheckedChange={(value) => onCheckedChange(value === true)}
      />
      <label htmlFor={checkboxId}>
        <div className="text-sm font-semibold">{label}</div>
        <div className="text-sm text-muted-foreground">{description}</div>
      </label>
    </div>
  )
}

function JobList({
  jobs,
  onSelect,
  selectedJobId,
}: {
  jobs: IngestJobSummary[]
  selectedJobId: string | null
  onSelect: (jobId: string) => void
}) {
  if (jobs.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border/70 px-4 py-8 text-center text-sm text-muted-foreground">
        No ingest jobs yet.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {jobs.map((job) => (
        <button
          type="button"
          key={job.job_id}
          className={`w-full rounded-lg border px-4 py-4 text-left transition ${
            selectedJobId === job.job_id
              ? "border-primary/50 bg-primary/5"
              : "border-border/70 bg-card hover:bg-muted/20"
          }`}
          onClick={() => onSelect(job.job_id)}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate font-semibold">
                {job.bvid ?? job.job_id}
              </div>
              <div className="mt-1 text-sm text-muted-foreground">
                {job.phase}
              </div>
            </div>
            <StatusBadge status={job.status} />
          </div>
          <div className="mt-3 text-xs text-muted-foreground">
            Created {formatDateTime(job.created_at)}
          </div>
        </button>
      ))}
    </div>
  )
}

function Dashboard() {
  const { user: currentUser } = useAuth()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const [inputValue, setInputValue] = useState("")
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const [options, setOptions] =
    useState<IngestVideoOptions>(defaultIngestOptions)
  const downloadQualityValue =
    options.max_height == null ? "best" : String(options.max_height)

  const jobsQuery = useQuery({
    queryKey: ["ingest-jobs", { limit: 8 }],
    queryFn: () => IngestionApi.readIngestJobs({ limit: 8, offset: 0 }),
  })
  const videosQuery = useQuery({
    queryKey: ["videos", { limit: 8 }],
    queryFn: () => IngestionApi.readVideos({ limit: 8, offset: 0 }),
  })
  const selectedJobQuery = useQuery({
    queryKey: ["ingest-job", selectedJobId],
    queryFn: () => IngestionApi.readIngestJob(selectedJobId ?? ""),
    enabled: Boolean(selectedJobId),
  })
  const bilibiliAccessQuery = useQuery({
    queryKey: ["system", "bilibili-access"],
    queryFn: () => IngestionApi.readBilibiliAccessStatus(),
    enabled: currentUser?.is_superuser === true,
  })

  useEffect(() => {
    if (!selectedJobId && jobsQuery.data?.data.length) {
      setSelectedJobId(jobsQuery.data.data[0].job_id)
    }
  }, [jobsQuery.data, selectedJobId])

  const createJobMutation = useMutation({
    mutationFn: () =>
      IngestionApi.createIngestJob({
        input: inputValue.trim(),
        options,
      }),
    onSuccess: (job) => {
      showSuccessToast(`Queued ${job.bvid ?? job.job_id}.`)
      setInputValue("")
      setSelectedJobId(job.job_id)
      queryClient.invalidateQueries({ queryKey: ["ingest-jobs"] })
      queryClient.invalidateQueries({ queryKey: ["videos"] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const recentJobs = jobsQuery.data?.data ?? []
  const recentVideos = videosQuery.data?.data ?? []
  const activeJobCount = recentJobs.filter(
    (job) => !["completed", "failed"].includes(job.status),
  ).length
  const completedCount = recentJobs.filter(
    (job) => job.status === "completed",
  ).length

  return (
    <div className="space-y-6">
      <section className="flex flex-col gap-4 border-b pb-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">
            Bilibili Media Ingestion
          </div>
          <h1 className="mt-3 max-w-4xl text-3xl font-semibold tracking-tight">
            Operate the ingest pipeline, inspect auxiliary data, and jump into
            playable outputs from one console.
          </h1>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-muted-foreground">
            Submit BVIDs, monitor recent jobs, review indexed videos, and open
            playable derivatives without leaving the dashboard.
          </p>
        </div>
        <div className="w-full rounded-lg border border-border/70 bg-card p-4 lg:max-w-sm">
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            Current Operator
          </div>
          <div className="mt-2 truncate text-lg font-semibold">
            {currentUser?.full_name || currentUser?.email}
          </div>
          <div className="mt-1 text-sm text-muted-foreground">
            {currentUser?.is_superuser
              ? "Superuser access enabled"
              : "Standard viewer/operator access"}
          </div>
          <Link
            className="mt-3 inline-flex items-center gap-2 text-sm font-medium text-primary hover:underline"
            to="/videos"
          >
            Open video browser
            <ArrowRight className="size-4" />
          </Link>
        </div>
      </section>

      {currentUser?.is_superuser &&
      bilibiliAccessQuery.data &&
      !bilibiliAccessQuery.data.metadata_cookie_configured ? (
        <Alert variant="destructive">
          <CircleAlert />
          <AlertTitle>Bilibili cookie header is not configured</AlertTitle>
          <AlertDescription>
            <p>
              Metadata, comments, danmaku, and subtitle crawls only have
              public-session access right now.
            </p>
            <p>
              Open{" "}
              <Link className="font-medium underline" to="/admin">
                Admin
              </Link>{" "}
              to save a raw `Cookie` header or confirm the environment fallback.
            </p>
          </AlertDescription>
        </Alert>
      ) : null}

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          icon={DatabaseZap}
          label="Recent Jobs"
          value={numberFormatter.format(jobsQuery.data?.count ?? 0)}
          hint="Latest ingest submissions visible to this account."
        />
        <MetricCard
          icon={CircleAlert}
          label="Active Jobs"
          value={numberFormatter.format(activeJobCount)}
          hint="Recent jobs that are still moving through the pipeline."
        />
        <MetricCard
          icon={Clock3}
          label="Completed"
          value={numberFormatter.format(completedCount)}
          hint="Recent jobs that reached the completed stage."
        />
        <MetricCard
          icon={Film}
          label="Videos"
          value={numberFormatter.format(videosQuery.data?.count ?? 0)}
          hint="Videos already materialized in the catalog."
        />
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <Card className="border-border/70 bg-card/90">
          <CardHeader>
            <CardTitle>Submit Ingest Job</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="space-y-2">
              <div className="text-sm font-medium">BVID or video URL</div>
              <Input
                placeholder="BV1xx411c7mD or https://www.bilibili.com/video/..."
                value={inputValue}
                onChange={(event) => setInputValue(event.target.value)}
              />
            </div>
            <div className="rounded-lg border border-border/70 bg-muted/20 p-4">
              <div className="text-sm font-semibold">Download Quality</div>
              <div className="mt-1 text-sm text-muted-foreground">
                Default uses the best available source stream. Set a cap only
                when you want smaller downloads.
              </div>
              <Select
                disabled={!options.download_video}
                value={downloadQualityValue}
                onValueChange={(value) =>
                  setOptions((current) => ({
                    ...current,
                    max_height: value === "best" ? null : Number(value),
                  }))
                }
              >
                <SelectTrigger className="mt-4 w-full sm:w-72">
                  <SelectValue placeholder="Select quality" />
                </SelectTrigger>
                <SelectContent>
                  {downloadQualityOptions.map((quality) => (
                    <SelectItem key={quality.value} value={quality.value}>
                      {quality.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <OptionToggle
                checked={options.download_video}
                description="Stage the source media, upload it, and process playable derivatives."
                label="Download Video"
                onCheckedChange={(checked) =>
                  setOptions((current) => ({
                    ...current,
                    download_video: checked,
                  }))
                }
              />
              <OptionToggle
                checked={options.create_hls}
                description="Generate proxy MP4 plus HLS master/segment outputs during processing."
                label="Create HLS"
                onCheckedChange={(checked) =>
                  setOptions((current) => ({
                    ...current,
                    create_hls: checked,
                  }))
                }
              />
              <OptionToggle
                checked={options.fetch_comments}
                description="Fetch threaded replies and comment-image associations."
                label="Fetch Comments"
                onCheckedChange={(checked) =>
                  setOptions((current) => ({
                    ...current,
                    fetch_comments: checked,
                  }))
                }
              />
              <OptionToggle
                checked={options.fetch_danmaku}
                description="Fetch snapshot plus history coverage and completeness metadata."
                label="Fetch Danmaku"
                onCheckedChange={(checked) =>
                  setOptions((current) => ({
                    ...current,
                    fetch_danmaku: checked,
                  }))
                }
              />
              <OptionToggle
                checked={options.fetch_subtitles}
                description="Persist subtitle tracks exposed by player metadata."
                label="Fetch Subtitles"
                onCheckedChange={(checked) =>
                  setOptions((current) => ({
                    ...current,
                    fetch_subtitles: checked,
                  }))
                }
              />
              <OptionToggle
                checked={options.transcribe_subtitles}
                description="Queue an independent OpenAI STT worker to generate timed subtitle tracks."
                label="Transcribe Subtitles"
                onCheckedChange={(checked) =>
                  setOptions((current) => ({
                    ...current,
                    transcribe_subtitles: checked,
                  }))
                }
              />
              <OptionToggle
                checked={options.force_refresh}
                description="Bypass idempotency and queue a fresh ingest job even for the same request."
                label="Force Refresh"
                onCheckedChange={(checked) =>
                  setOptions((current) => ({
                    ...current,
                    force_refresh: checked,
                  }))
                }
              />
            </div>
            <LoadingButton
              className="w-full"
              disabled={!inputValue.trim()}
              loading={createJobMutation.isPending}
              onClick={() => createJobMutation.mutate()}
            >
              Queue Ingest Job
            </LoadingButton>
          </CardContent>
        </Card>

        <Card className="border-border/70 bg-card/90">
          <CardHeader>
            <CardTitle>Recent Jobs</CardTitle>
          </CardHeader>
          <CardContent>
            {jobsQuery.isLoading ? (
              <div className="text-sm text-muted-foreground">
                Loading recent jobs…
              </div>
            ) : (
              <JobList
                jobs={recentJobs}
                onSelect={setSelectedJobId}
                selectedJobId={selectedJobId}
              />
            )}
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-6 xl:grid-cols-[0.95fr_1.05fr]">
        <Card className="border-border/70 bg-card/90">
          <CardHeader>
            <CardTitle>Recent Videos</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {videosQuery.isLoading ? (
              <div className="text-sm text-muted-foreground">
                Loading videos…
              </div>
            ) : recentVideos.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border/70 px-4 py-8 text-center text-sm text-muted-foreground">
                No videos indexed yet.
              </div>
            ) : (
              recentVideos.map((video) => (
                <Link
                  key={video.bvid}
                  className="block rounded-lg border border-border/70 bg-muted/15 px-4 py-4 transition hover:bg-muted/30"
                  search={{ bvid: video.bvid }}
                  to="/videos"
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
                  <div className="mt-3 text-sm text-muted-foreground">
                    {video.owner_name || "Unknown uploader"} ·{" "}
                    {formatDuration(video.duration_seconds)}
                  </div>
                </Link>
              ))
            )}
          </CardContent>
        </Card>

        <Card className="border-border/70 bg-card/90">
          <CardHeader>
            <CardTitle>Selected Job Detail</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            {selectedJobQuery.isLoading ? (
              <div className="text-sm text-muted-foreground">
                Loading job detail…
              </div>
            ) : selectedJobQuery.data ? (
              <>
                <div className="rounded-lg border border-border/70 bg-muted/15 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-sm text-muted-foreground">
                        {selectedJobQuery.data.bvid ??
                          selectedJobQuery.data.job_id}
                      </div>
                      <div className="mt-2 text-xl font-semibold">
                        {selectedJobQuery.data.phase}
                      </div>
                    </div>
                    <StatusBadge status={selectedJobQuery.data.status} />
                  </div>
                  <div className="mt-4 grid gap-3 text-sm md:grid-cols-3">
                    <div>
                      <div className="text-muted-foreground">Created</div>
                      <div className="mt-1">
                        {formatDateTime(selectedJobQuery.data.created_at)}
                      </div>
                    </div>
                    <div>
                      <div className="text-muted-foreground">Started</div>
                      <div className="mt-1">
                        {formatDateTime(selectedJobQuery.data.started_at)}
                      </div>
                    </div>
                    <div>
                      <div className="text-muted-foreground">Finished</div>
                      <div className="mt-1">
                        {formatDateTime(selectedJobQuery.data.finished_at)}
                      </div>
                    </div>
                  </div>
                </div>
                <div className="grid gap-4 lg:grid-cols-2">
                  <div className="space-y-2">
                    <div className="text-sm font-semibold">Options</div>
                    <JsonPreview value={selectedJobQuery.data.options} />
                  </div>
                  <div className="space-y-2">
                    <div className="text-sm font-semibold">Progress</div>
                    <JsonPreview value={selectedJobQuery.data.progress} />
                  </div>
                </div>
                {selectedJobQuery.data.error ? (
                  <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-700 dark:text-rose-300">
                    <div className="font-semibold">Job Error</div>
                    <div className="mt-2">
                      {selectedJobQuery.data.error.code}:{" "}
                      {selectedJobQuery.data.error.message}
                    </div>
                  </div>
                ) : null}
              </>
            ) : (
              <div className="rounded-lg border border-dashed border-border/70 px-4 py-8 text-center text-sm text-muted-foreground">
                Select a job to inspect its progress payload.
              </div>
            )}
          </CardContent>
        </Card>
      </section>
    </div>
  )
}
