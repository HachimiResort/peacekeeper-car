import { createContext, useContext, useEffect, useState, type ReactNode } from "react"
import { useSession } from "./session"
import type { JsonObject, RuntimeStatusMessage } from "../api/types"

interface LiveStatusValue {
  connected: boolean
  statuses: Record<string, JsonObject>
  lastUpdatedAt: Record<string, number>
  lastError: string | null
}

const LiveStatusContext = createContext<LiveStatusValue>({ connected: false, statuses: {}, lastUpdatedAt: {}, lastError: null })

export function LiveStatusProvider({ children }: { children: ReactNode }) {
  const { api } = useSession()
  const [connected, setConnected] = useState(false)
  const [statuses, setStatuses] = useState<Record<string, JsonObject>>({})
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Record<string, number>>({})
  const [lastError, setLastError] = useState<string | null>(null)
  useEffect(() => {
    if (!api) return
    setConnected(false)
    return api.subscribeStatus((message: RuntimeStatusMessage) => {
      const now = Date.now()
      if (message.type === "snapshot" && message.robots) {
        setStatuses(message.robots)
        setLastUpdatedAt(Object.fromEntries(Object.keys(message.robots).map((robotId) => [robotId, now])))
      }
      if (message.type === "robot_status" && message.robot_id && message.data) {
        setStatuses((current) => ({ ...current, [message.robot_id!]: message.data! }))
        setLastUpdatedAt((current) => ({ ...current, [message.robot_id!]: now }))
      }
    }, {
      onOpen: () => { setConnected(true); setLastError(null) },
      onClose: () => setConnected(false),
      onError: (error) => setLastError(error instanceof Error ? error.message : "实时状态连接异常"),
    })
  }, [api])
  return <LiveStatusContext.Provider value={{ connected, statuses, lastUpdatedAt, lastError }}>{children}</LiveStatusContext.Provider>
}

export function useLiveStatus() { return useContext(LiveStatusContext) }
