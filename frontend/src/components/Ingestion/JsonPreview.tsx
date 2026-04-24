export function JsonPreview({
  value,
  emptyLabel = "No data",
}: {
  value: unknown
  emptyLabel?: string
}) {
  if (
    value === null ||
    value === undefined ||
    (typeof value === "object" &&
      !Array.isArray(value) &&
      Object.keys(value as Record<string, unknown>).length === 0)
  ) {
    return (
      <div className="rounded-xl border border-dashed border-border/70 bg-muted/20 px-4 py-6 text-sm text-muted-foreground">
        {emptyLabel}
      </div>
    )
  }

  return (
    <pre className="max-h-80 overflow-auto rounded-xl border border-border/70 bg-slate-950 px-4 py-4 text-xs leading-6 text-slate-100">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}
