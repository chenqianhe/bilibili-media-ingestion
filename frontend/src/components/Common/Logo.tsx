import { Link } from "@tanstack/react-router"

import { cn } from "@/lib/utils"

interface LogoProps {
  variant?: "full" | "icon" | "responsive"
  className?: string
  asLink?: boolean
}

export function Logo({
  variant = "full",
  className,
  asLink = true,
}: LogoProps) {
  const content =
    variant === "responsive" ? (
      <>
        <div
          aria-label="Bilibili Media Ingestion Service"
          role="img"
          className={cn("group-data-[collapsible=icon]:hidden", className)}
        >
          <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-primary/80">
            Bilibili
          </div>
          <div className="text-sm font-semibold tracking-[0.14em]">
            Media Ingestion
          </div>
        </div>
        <div
          aria-label="Bilibili Media Ingestion Service"
          role="img"
          className={cn(
            "hidden size-8 items-center justify-center rounded-lg border border-border/70 bg-gradient-to-br from-emerald-500/15 via-sky-500/15 to-cyan-500/15 text-xs font-semibold tracking-[0.18em] group-data-[collapsible=icon]:flex",
            className,
          )}
        >
          BI
        </div>
      </>
    ) : (
      <div
        aria-label="Bilibili Media Ingestion Service"
        role="img"
        className={cn(
          variant === "full"
            ? "flex flex-col"
            : "flex size-8 items-center justify-center rounded-lg border border-border/70 bg-gradient-to-br from-emerald-500/15 via-sky-500/15 to-cyan-500/15 text-xs font-semibold tracking-[0.18em]",
          className,
        )}
      >
        {variant === "full" ? (
          <>
            <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-primary/80">
              Bilibili
            </div>
            <div className="text-sm font-semibold tracking-[0.14em]">
              Media Ingestion
            </div>
          </>
        ) : (
          "BI"
        )}
      </div>
    )

  if (!asLink) {
    return content
  }

  return <Link to="/">{content}</Link>
}
