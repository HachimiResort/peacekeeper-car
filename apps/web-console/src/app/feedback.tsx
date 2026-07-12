import { Toast } from "@base-ui/react/toast"
import { Dialog } from "@base-ui/react/dialog"
import { CheckCircle2, CircleAlert, Info, X } from "lucide-react"
import { createContext, useContext, useMemo, useState, type ReactNode } from "react"

export type FeedbackTone = "success" | "error" | "info" | "warning"

interface FeedbackInput {
  id?: string
  title: string
  description?: string
  tone?: FeedbackTone
}

interface ConfirmInput {
  title: string
  description: string
  confirmLabel?: string
  tone?: "primary" | "danger" | "warning"
}

interface FeedbackValue {
  notify(input: FeedbackInput): void
  confirm(input: ConfirmInput): Promise<boolean>
}

interface PendingConfirm extends ConfirmInput {
  resolve(value: boolean): void
}

const FeedbackContext = createContext<FeedbackValue | null>(null)

export function FeedbackProvider({ children }: { children: ReactNode }) {
  return <Toast.Provider limit={4} timeout={3000}><FeedbackBridge>{children}</FeedbackBridge></Toast.Provider>
}

function FeedbackBridge({ children }: { children: ReactNode }) {
  const { add } = Toast.useToastManager<{ tone: FeedbackTone }>()
  const [pendingConfirm, setPendingConfirm] = useState<PendingConfirm | null>(null)
  const value = useMemo<FeedbackValue>(() => ({
    notify(input) {
      const tone = input.tone || "info"
      add({
        id: input.id,
        title: input.title,
        description: input.description,
        type: tone,
        data: { tone },
        priority: tone === "error" ? "high" : "low",
        timeout: tone === "error" ? 6000 : 3000,
      })
    },
    confirm(input) {
      return new Promise<boolean>((resolve) => setPendingConfirm({ ...input, resolve }))
    },
  }), [add])

  const finishConfirm = (accepted: boolean) => {
    pendingConfirm?.resolve(accepted)
    setPendingConfirm(null)
  }

  return <FeedbackContext.Provider value={value}>
    {children}
    <Toast.Portal><Toast.Viewport className="toast-viewport"><ToastList /></Toast.Viewport></Toast.Portal>
    {pendingConfirm && <ConfirmOverlay value={pendingConfirm} onFinish={finishConfirm} />}
  </FeedbackContext.Provider>
}

function ToastList() {
  const { toasts } = Toast.useToastManager<{ tone: FeedbackTone }>()
  return toasts.map((toast) => {
    const tone = toast.data?.tone || "info"
    const Icon = tone === "success" ? CheckCircle2 : tone === "error" ? CircleAlert : Info
    return <Toast.Root key={toast.id} toast={toast} className={`app-toast toast-${tone}`}>
      <Toast.Content className="toast-content">
        <Icon className="toast-icon" />
        <div className="toast-copy"><Toast.Title className="toast-title" /><Toast.Description className="toast-description" /></div>
        <Toast.Close className="toast-close" aria-label="关闭通知"><X size={16} /></Toast.Close>
      </Toast.Content>
    </Toast.Root>
  })
}

function ConfirmOverlay({ value, onFinish }: { value: PendingConfirm; onFinish(value: boolean): void }) {
  return <Dialog.Root open onOpenChange={(open) => { if (!open) onFinish(false) }}>
    <Dialog.Portal>
      <Dialog.Backdrop className="modal-backdrop confirm-backdrop" />
      <Dialog.Popup className="modal confirm-modal" role="alertdialog">
        <header><Dialog.Title>{value.title}</Dialog.Title></header>
        <div className="modal-body"><Dialog.Description>{value.description}</Dialog.Description></div>
        <footer><button className="confirm-cancel" autoFocus onClick={() => onFinish(false)}>取消</button><button className={`confirm-submit is-${value.tone || "primary"}`} onClick={() => onFinish(true)}>{value.confirmLabel || "确认"}</button></footer>
      </Dialog.Popup>
    </Dialog.Portal>
  </Dialog.Root>
}

export function useFeedback() {
  const value = useContext(FeedbackContext)
  if (!value) throw new Error("FeedbackProvider is missing")
  return value
}
