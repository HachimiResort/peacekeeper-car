import { Dialog } from "@base-ui/react/dialog"
import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode, RefObject } from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { CircleAlert, CircleCheck, Info, LoaderCircle, X } from "lucide-react"
import { cn } from "../lib/cn"

const buttonVariants = cva("inline-flex items-center justify-center gap-2 rounded-lg border text-sm font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-45", {
  variants: {
    variant: {
      primary: "border-transparent bg-primary px-4 py-2 text-white shadow-sm hover:bg-primary-strong",
      secondary: "border-border bg-card px-4 py-2 text-foreground hover:border-primary/40 hover:bg-primary-soft",
      ghost: "border-transparent px-3 py-2 text-muted hover:bg-ink/5 hover:text-foreground",
      danger: "border-transparent bg-danger px-4 py-2 text-white shadow-sm hover:bg-danger-strong",
      warning: "border-transparent bg-warning px-4 py-2 text-ink hover:bg-warning-strong",
    },
    size: { sm: "h-8", md: "h-10", lg: "h-12 text-base" },
  },
  defaultVariants: { variant: "secondary", size: "md" },
})

export function Button({ className, variant, size, loading = false, loadingText, disabled, children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants> & { loading?: boolean; loadingText?: string }) {
  return <button className={cn(buttonVariants({ variant, size }), className)} disabled={disabled || loading} aria-busy={loading || undefined} {...props}>{loading && <LoaderCircle className="button-spinner" />}{loading ? loadingText || children : children}</button>
}

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-xl border border-border bg-card shadow-card", className)} {...props} />
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "success" | "warning" | "danger" | "info" }) {
  return <span className={cn("inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold", `badge-${tone}`)}>{children}</span>
}

export function StatusDot({ online }: { online: boolean }) {
  return <span className={cn("status-dot", online ? "is-online" : "is-offline")} aria-label={online ? "在线" : "离线"} />
}

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description?: string; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  )
}

export function Modal({ open, title, children, onClose, footer, busy = false, dismissible = true, initialFocus }: { open: boolean; title: string; children: ReactNode; onClose(): void; footer?: ReactNode; busy?: boolean; dismissible?: boolean; initialFocus?: boolean | RefObject<HTMLElement | null> }) {
  return <Dialog.Root open={open} disablePointerDismissal={!dismissible || busy} onOpenChange={(nextOpen) => { if (!nextOpen && dismissible && !busy) onClose() }}>
    <Dialog.Portal>
      <Dialog.Backdrop className="modal-backdrop" />
      <Dialog.Popup className="modal" initialFocus={initialFocus}>
        <header><Dialog.Title>{title}</Dialog.Title><Button variant="ghost" aria-label="关闭" disabled={!dismissible || busy} onClick={onClose}><X size={18} /></Button></header>
        <div className="modal-body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </Dialog.Popup>
    </Dialog.Portal>
  </Dialog.Root>
}

export function InlineActionStatus({ tone = "info", title, detail, onRetry }: { tone?: "info" | "success" | "error" | "warning"; title: string; detail?: string; onRetry?: () => void }) {
  const Icon = tone === "success" ? CircleCheck : tone === "error" ? CircleAlert : Info
  return <div className={cn("inline-action-status", `is-${tone}`)} role={tone === "error" ? "alert" : "status"}><Icon /><div><strong>{title}</strong>{detail && <span>{detail}</span>}</div>{onRetry && <Button size="sm" onClick={onRetry}>重试</Button>}</div>
}

export function EmptyState({ title, detail, action }: { title: string; detail: string; action?: ReactNode }) {
  return <div className="empty-state"><strong>{title}</strong><span>{detail}</span>{action}</div>
}

export function LoadingBlock({ label = "正在同步数据" }: { label?: string }) {
  return <div className="loading-block"><span className="spinner" />{label}</div>
}

export function JsonPanel({ value }: { value: unknown }) {
  return <pre className="json-panel">{JSON.stringify(value, null, 2)}</pre>
}

export const fieldClass = "field-control"
