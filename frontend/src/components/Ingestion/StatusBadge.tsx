import { Badge } from "@/components/ui/badge"

const toneByStatus: Record<string, string> = {
  completed:
    "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  complete:
    "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  pending: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  partial:
    "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  metadata_fetching:
    "border-cyan-500/30 bg-cyan-500/10 text-cyan-700 dark:text-cyan-300",
  processing_media:
    "border-cyan-500/30 bg-cyan-500/10 text-cyan-700 dark:text-cyan-300",
  metadata_ready:
    "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-300",
  source_uploaded:
    "border-indigo-500/30 bg-indigo-500/10 text-indigo-700 dark:text-indigo-300",
  uploading_source:
    "border-indigo-500/30 bg-indigo-500/10 text-indigo-700 dark:text-indigo-300",
  source_downloaded:
    "border-violet-500/30 bg-violet-500/10 text-violet-700 dark:text-violet-300",
  failed: "border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-300",
  active:
    "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  unknown:
    "border-slate-500/30 bg-slate-500/10 text-slate-700 dark:text-slate-300",
}

export function formatStatusLabel(status: string | null | undefined) {
  if (!status) {
    return "unknown"
  }
  return status.replace(/_/g, " ")
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  return (
    <Badge
      variant="outline"
      className={toneByStatus[status ?? ""] ?? toneByStatus.unknown}
    >
      {formatStatusLabel(status)}
    </Badge>
  )
}
