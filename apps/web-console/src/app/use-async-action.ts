import { useCallback, useRef, useState } from "react"
import { useFeedback } from "./feedback"

export function useAsyncAction() {
  const feedback = useFeedback()
  const [pending, setPending] = useState<Set<string>>(() => new Set())
  const pendingRef = useRef(new Set<string>())
  const [errors, setErrors] = useState<Record<string, string>>({})

  const run = useCallback(async <T,>(key: string, task: () => Promise<T>, options: { success?: string; error?: string; allowConcurrent?: boolean } = {}) => {
    if (!options.allowConcurrent && pendingRef.current.has(key)) return undefined
    pendingRef.current.add(key)
    setPending((current) => new Set(current).add(key))
    setErrors((current) => ({ ...current, [key]: "" }))
    try {
      const result = await task()
      if (options.success) feedback.notify({ id: `action-${key}`, title: options.success, tone: "success" })
      return result
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : options.error || "操作失败"
      setErrors((current) => ({ ...current, [key]: message }))
      feedback.notify({ id: `action-${key}`, title: options.error || "操作未完成", description: message, tone: "error" })
      return undefined
    } finally {
      pendingRef.current.delete(key)
      setPending((current) => { const next = new Set(current); next.delete(key); return next })
    }
  }, [feedback])

  const clearError = useCallback((key: string) => setErrors((current) => ({ ...current, [key]: "" })), [])
  return { run, isPending: (key: string) => pending.has(key), errors, clearError }
}
