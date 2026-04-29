export function Footer() {
  const currentYear = new Date().getFullYear()

  return (
    <footer className="border-t px-4 py-4 md:px-6">
      <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-2 sm:flex-row">
        <p className="text-sm text-muted-foreground">
          Bilibili Media Ingestion Service
        </p>
        <p className="text-xs text-muted-foreground">{currentYear}</p>
      </div>
    </footer>
  )
}
