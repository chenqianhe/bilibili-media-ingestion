import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CircleAlert, KeyRound, ShieldCheck } from "lucide-react"
import { useState } from "react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import { Textarea } from "@/components/ui/textarea"
import useCustomToast from "@/hooks/useCustomToast"
import { IngestionApi } from "@/lib/ingestionApi"
import { handleError } from "@/utils"

function formatDateTime(value?: string | null) {
  if (!value) {
    return "Not saved in the database"
  }
  return new Date(value).toLocaleString()
}

function sourceLabel(value: string) {
  switch (value) {
    case "database":
      return "Database override"
    case "environment":
      return "Environment fallback"
    default:
      return "Missing"
  }
}

function statusBadgeVariant(configured: boolean): "default" | "destructive" {
  return configured ? "default" : "destructive"
}

export default function BilibiliAccessSettings() {
  const queryClient = useQueryClient()
  const { showErrorToast, showSuccessToast } = useCustomToast()
  const [netscapeCookiesDraft, setNetscapeCookiesDraft] = useState("")
  const [downloadUserAgentDraft, setDownloadUserAgentDraft] = useState("")

  const statusQuery = useQuery({
    queryKey: ["system", "bilibili-access"],
    queryFn: () => IngestionApi.readBilibiliAccessStatus(),
  })

  const saveMutation = useMutation({
    mutationFn: () =>
      IngestionApi.updateBilibiliAccessStatus({
        netscape_cookies: netscapeCookiesDraft.trim(),
        download_user_agent: downloadUserAgentDraft.trim() || null,
      }),
    onSuccess: () => {
      setNetscapeCookiesDraft("")
      setDownloadUserAgentDraft("")
      showSuccessToast("Bilibili Netscape cookies updated.")
      queryClient.invalidateQueries({ queryKey: ["system", "bilibili-access"] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const clearMutation = useMutation({
    mutationFn: () => IngestionApi.clearBilibiliAccessStatus(),
    onSuccess: () => {
      showSuccessToast("Bilibili database access override cleared.")
      queryClient.invalidateQueries({ queryKey: ["system", "bilibili-access"] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const status = statusQuery.data

  return (
    <div className="grid gap-6 xl:grid-cols-[0.95fr_1.05fr]">
      <Card className="border-border/70 bg-card/90">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="size-4 text-primary" />
            Access Status
          </CardTitle>
          <CardDescription>
            Review whether metadata crawlers and source downloads currently have
            authenticated Bilibili access.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {statusQuery.isLoading ? (
            <div className="text-sm text-muted-foreground">
              Loading Bilibili access status…
            </div>
          ) : status ? (
            <>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-2xl border border-border/70 bg-muted/20 p-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">
                    Metadata
                  </div>
                  <div className="mt-3 flex items-center gap-2">
                    <Badge
                      variant={statusBadgeVariant(
                        status.metadata_cookie_configured,
                      )}
                    >
                      {status.metadata_cookie_configured
                        ? "Authenticated"
                        : "Public only"}
                    </Badge>
                  </div>
                  <p className="mt-3 text-sm text-muted-foreground">
                    Comments, danmaku, subtitles, and metadata crawls use a raw
                    `Cookie` header derived from the stored Netscape cookies or
                    the env fallback header.
                  </p>
                </div>
                <div className="rounded-2xl border border-border/70 bg-muted/20 p-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">
                    Source Download
                  </div>
                  <div className="mt-3 flex items-center gap-2">
                    <Badge
                      variant={statusBadgeVariant(
                        status.download_auth_configured,
                      )}
                    >
                      {status.download_auth_configured
                        ? "Authenticated"
                        : "Public only"}
                    </Badge>
                  </div>
                  <p className="mt-3 text-sm text-muted-foreground">
                    `yt-dlp` uses Netscape cookies together with an optional
                    download user-agent, browser sync, cookie file, and
                    impersonation settings.
                  </p>
                </div>
              </div>

              <div className="rounded-2xl border border-border/70 bg-muted/10 p-4 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline">
                    {sourceLabel(status.effective_cookie_source)}
                  </Badge>
                  {status.has_database_override ? (
                    <Badge variant="secondary">Database override saved</Badge>
                  ) : null}
                  {status.yt_dlp_cookies_file_configured ? (
                    <Badge variant="secondary">`YT_DLP_COOKIES_FILE` set</Badge>
                  ) : null}
                  {status.yt_dlp_cookies_from_browser_configured ? (
                    <Badge variant="secondary">
                      `YT_DLP_COOKIES_FROM_BROWSER` set
                    </Badge>
                  ) : null}
                  {status.download_user_agent_configured ? (
                    <Badge variant="secondary">Download UA ready</Badge>
                  ) : null}
                  {status.yt_dlp_impersonate_configured ? (
                    <Badge variant="secondary">`YT_DLP_IMPERSONATE` set</Badge>
                  ) : null}
                </div>
                <div className="mt-4 space-y-2 text-muted-foreground">
                  <div>
                    Derived metadata cookie summary:{" "}
                    <span className="text-foreground">
                      {status.cookie_header_summary ?? "Not configured"}
                    </span>
                  </div>
                  <div>
                    Stored Netscape cookie summary:{" "}
                    <span className="text-foreground">
                      {status.netscape_cookie_summary ?? "Not configured"}
                    </span>
                  </div>
                  <div>
                    Download user-agent:{" "}
                    <span className="break-all text-foreground">
                      {status.download_user_agent_summary ?? "Not configured"}
                    </span>
                  </div>
                  <div>
                    Database override updated by:{" "}
                    <span className="text-foreground">
                      {status.database_cookie_updated_by ?? "Not saved"}
                    </span>
                  </div>
                  <div>
                    Database override updated at:{" "}
                    <span className="text-foreground">
                      {formatDateTime(status.database_cookie_updated_at)}
                    </span>
                  </div>
                </div>
              </div>

              {status.warnings.length > 0 ? (
                <Alert variant="destructive">
                  <CircleAlert />
                  <AlertTitle>Attention required</AlertTitle>
                  <AlertDescription>
                    {status.warnings.map((warning) => (
                      <p key={warning}>{warning}</p>
                    ))}
                  </AlertDescription>
                </Alert>
              ) : null}
            </>
          ) : null}
        </CardContent>
      </Card>

      <Card className="border-border/70 bg-card/90">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <KeyRound className="size-4 text-primary" />
            Manage Download Access
          </CardTitle>
          <CardDescription>
            Save a Bilibili Netscape `cookies.txt` export and an optional
            download user-agent in the database. Metadata crawlers derive a raw
            `Cookie` header from this export, while `yt-dlp` reads the cookie
            file directly.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <div className="text-sm font-medium">Netscape cookies.txt</div>
            <Textarea
              placeholder={
                "# Netscape HTTP Cookie File\n.bilibili.com\tTRUE\t/\tTRUE\t1767225600\tSESSDATA\t..."
              }
              value={netscapeCookiesDraft}
              onChange={(event) => setNetscapeCookiesDraft(event.target.value)}
              className="min-h-48 font-mono text-xs"
            />
            <p className="text-sm text-muted-foreground">
              Paste the Bilibili-only Netscape/Mozilla `cookies.txt` export
              here. This database value takes precedence over the legacy raw
              `BILIBILI_COOKIE_HEADER` fallback.
            </p>
          </div>

          <div className="space-y-2">
            <div className="text-sm font-medium">Download user-agent</div>
            <Input
              placeholder="Mozilla/5.0 (...) Chrome/147.0.0.0 Safari/537.36"
              value={downloadUserAgentDraft}
              onChange={(event) => setDownloadUserAgentDraft(event.target.value)}
            />
            <p className="text-sm text-muted-foreground">
              Optional but recommended for `yt-dlp`. If left blank, the worker
              falls back to `YT_DLP_USER_AGENT`.
            </p>
          </div>

          <div className="flex flex-wrap gap-3">
            <LoadingButton
              loading={saveMutation.isPending}
              disabled={!netscapeCookiesDraft.trim()}
              onClick={() => saveMutation.mutate()}
            >
              Save Netscape Cookies
            </LoadingButton>
            <LoadingButton
              variant="outline"
              loading={clearMutation.isPending}
              disabled={!status?.has_database_override}
              onClick={() => clearMutation.mutate()}
            >
              Clear Database Override
            </LoadingButton>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
