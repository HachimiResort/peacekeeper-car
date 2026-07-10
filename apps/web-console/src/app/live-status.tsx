import { createContext, useContext, useEffect, useState, type ReactNode } from "react"
import { useSession } from "./session"
import type { JsonObject, RuntimeStatusMessage } from "../api/types"

interface LiveStatusValue {
  connected: boolean
  statuses: Record<string, JsonObject>
}

const LiveStatusContext = createContext<LiveStatusValue>({ connected: false, statuses: {} })

export function LiveStatusProvider({ children }: { children: ReactNode }) {
  const { api } = useSession()
  const [connected, setConnected] = useState(false)
  const [statuses, setStatuses] = useState<Record<string, JsonObject>>({})
  useEffect(() => {
    if (!api) return
    setConnected(false)
    return api.subscribeStatus((message: RuntimeStatusMessage) => {
      setConnected(true)
      if (message.type === "snapshot" && message.robots) setStatuses(message.robots)
      if (message.type === "robot_status" && message.robot_id && message.data) {
        setStatuses((current) => ({ ...current, [message.robot_id!]: message.data! }))
      }
    })
  }, [api])
  return <LiveStatusContext.Provider value={{ connected, statuses }}>{children}</LiveStatusContext.Provider>
}

export function useLiveStatus() { return useContext(LiveStatusContext) }
