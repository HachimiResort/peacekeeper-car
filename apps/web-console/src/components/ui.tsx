import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { X } from "lucide-react"
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

export function Button({ className, variant, size, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants>) {
  return <button className={cn(buttonVariants({ variant, size }), className)} {...props} />
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

export function Modal({ open, title, children, onClose, footer }: { open: boolean; title: string; children: ReactNode; onClose(): void; footer?: ReactNode }) {
  if (!open) return null
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <header><h2>{title}</h2><Button variant="ghost" aria-label="关闭" onClick={onClose}><X size={18} /></Button></header>
        <div className="modal-body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </section>
    </div>
  )
}

export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return <div className="empty-state"><strong>{title}</strong><span>{detail}</span></div>
}

export function LoadingBlock({ label = "正在同步数据" }: { label?: string }) {
  return <div className="loading-block"><span className="spinner" />{label}</div>
}

export function JsonPanel({ value }: { value: unknown }) {
  return <pre className="json-panel">{JSON.stringify(value, null, 2)}</pre>
}

export const fieldClass = "field-control"
