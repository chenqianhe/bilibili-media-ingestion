export function Footer() {
  const currentYear = new Date().getFullYear()

  return (
    <footer className="border-t py-4 px-6">
      <div className="flex flex-col items-center justify-between gap-2 sm:flex-row">
        <p className="text-muted-foreground text-sm">
          Bilibili Media Ingestion Service
        </p>
        <p className="text-muted-foreground text-xs">{currentYear}</p>
      </div>
    </footer>
  )
}
