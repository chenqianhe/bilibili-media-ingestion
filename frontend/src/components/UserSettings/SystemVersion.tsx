import { useQuery } from "@tanstack/react-query"
import { Code2, MonitorCog, ServerCog } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { IngestionApi, type SystemVersion } from "@/lib/ingestionApi"

const frontendVersion = {
  service: "frontend",
  appVersion: __APP_VERSION__,
  gitCommit: __GIT_COMMIT__ || null,
  gitBranch: __GIT_BRANCH__ || null,
  buildTime: __BUILD_TIME__ || null,
  apiUrl: import.meta.env.VITE_API_URL || "same-origin",
}

function formatDateTime(value?: string | null) {
  if (!value) {
    return "Unknown"
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString()
}

function displayValue(value?: string | number | boolean | null) {
  if (value === null || value === undefined || value === "") {
    return "Unknown"
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No"
  }
  return String(value)
}

function InfoRow({
  label,
  value,
  mono = false,
}: {
  label: string
  value?: string | number | boolean | null
  mono?: boolean
}) {
  return (
    <div className="grid gap-1 border-b border-border/60 py-3 last:border-b-0 sm:grid-cols-[180px_minmax(0,1fr)]">
      <dt className="text-sm font-medium text-muted-foreground">{label}</dt>
      <dd
        className={
          mono
            ? "break-all font-mono text-sm text-foreground"
            : "break-words text-sm text-foreground"
        }
      >
        {displayValue(value)}
      </dd>
    </div>
  )
}

function BackendVersionPanel({ version }: { version?: SystemVersion }) {
  const git = version?.git

  return (
    <Card className="border-border/70 bg-card/90">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ServerCog className="size-4 text-primary" />
          Backend
        </CardTitle>
        <CardDescription>
          API runtime, deployed package, Git revision, and key Python
          dependencies.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <dl className="rounded-lg border border-border/70 bg-muted/10 px-4">
          <InfoRow label="Service" value={version?.service} />
          <InfoRow label="Project" value={version?.project_name} />
          <InfoRow label="Environment" value={version?.environment} />
          <InfoRow label="App version" value={version?.app_version} mono />
          <InfoRow label="Python" value={version?.python_version} mono />
          <InfoRow
            label="Build time"
            value={formatDateTime(version?.build_time)}
          />
          <InfoRow label="Git branch" value={git?.branch} mono />
          <InfoRow label="Git commit" value={git?.short_commit} mono />
          <InfoRow label="Working tree dirty" value={git?.dirty} />
        </dl>

        {version?.packages.length ? (
          <div className="flex flex-wrap gap-2">
            {version.packages.map((pkg) => (
              <Badge key={pkg.name} variant="secondary">
                {pkg.name} {pkg.version ?? "unknown"}
              </Badge>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}

export default function SystemVersionSettings() {
  const versionQuery = useQuery({
    queryKey: ["system", "version"],
    queryFn: () => IngestionApi.readSystemVersion(),
  })

  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <Card className="border-border/70 bg-card/90">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <MonitorCog className="size-4 text-primary" />
            Frontend
          </CardTitle>
          <CardDescription>
            Browser bundle version, Git revision, and API target from the Vite
            build.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="rounded-lg border border-border/70 bg-muted/10 px-4">
            <InfoRow label="Service" value={frontendVersion.service} />
            <InfoRow
              label="App version"
              value={frontendVersion.appVersion}
              mono
            />
            <InfoRow label="Git branch" value={frontendVersion.gitBranch} mono />
            <InfoRow label="Git commit" value={frontendVersion.gitCommit} mono />
            <InfoRow
              label="Build time"
              value={formatDateTime(frontendVersion.buildTime)}
            />
            <InfoRow label="API URL" value={frontendVersion.apiUrl} mono />
          </dl>
        </CardContent>
      </Card>

      {versionQuery.isLoading ? (
        <Card className="border-border/70 bg-card/90">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Code2 className="size-4 text-primary" />
              Backend
            </CardTitle>
            <CardDescription>Loading backend version information.</CardDescription>
          </CardHeader>
        </Card>
      ) : versionQuery.data ? (
        <BackendVersionPanel version={versionQuery.data} />
      ) : (
        <Card className="border-border/70 bg-card/90">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Code2 className="size-4 text-primary" />
              Backend
            </CardTitle>
            <CardDescription>
              Backend version information is unavailable.
            </CardDescription>
          </CardHeader>
        </Card>
      )}
    </div>
  )
}
