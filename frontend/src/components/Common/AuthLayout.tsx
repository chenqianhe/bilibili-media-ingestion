import { Appearance } from "@/components/Common/Appearance"
import { Logo } from "@/components/Common/Logo"
import { Footer } from "./Footer"

interface AuthLayoutProps {
  children: React.ReactNode
}

export function AuthLayout({ children }: AuthLayoutProps) {
  return (
    <div className="grid min-h-svh bg-background lg:grid-cols-[0.9fr_1.1fr]">
      <div className="relative hidden border-r bg-muted/25 lg:flex lg:items-center lg:justify-center">
        <div className="space-y-4 text-center">
          <img
            alt=""
            className="mx-auto size-16 rounded-lg"
            src="/assets/images/bili-ingest-mark.svg"
          />
          <Logo variant="full" asLink={false} />
        </div>
      </div>
      <div className="flex min-w-0 flex-col gap-4 p-4 md:p-8">
        <div className="flex justify-end">
          <Appearance />
        </div>
        <div className="flex flex-1 items-center justify-center">
          <div className="w-full max-w-sm rounded-lg border border-border/70 bg-card p-6 shadow-xs">
            {children}
          </div>
        </div>
        <Footer />
      </div>
    </div>
  )
}
