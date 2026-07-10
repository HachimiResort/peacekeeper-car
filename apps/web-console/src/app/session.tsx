import { createContext, useContext, useEffect, useState, type ReactNode } from "react"
import type { MissionApi } from "../api/types"
import { HttpMissionApi } from "../api/client"
import { DemoMissionApi } from "../mocks/demo-client"

const TOKEN_KEY = "peacekeeper.shared-token"
const OPERATOR_KEY = "peacekeeper.operator-name"

function configuredDemoMode() {
  const runtime = window.__PEACEKEEPER_CONFIG__?.demoMode
  if (typeof runtime === "boolean") return runtime
  return import.meta.env.VITE_DEMO_MODE === "true"
}

interface SessionValue {
  api: MissionApi | null
  connected: boolean
  demoMode: boolean
  operatorName: string
  connect(token: string, operatorName: string): Promise<void>
  disconnect(): void
}

const SessionContext = createContext<SessionValue | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const demoMode = configuredDemoMode()
  const [token, setToken] = useState(() => sessionStorage.getItem(TOKEN_KEY) || "")
  const [operatorName, setOperatorName] = useState(() => localStorage.getItem(OPERATOR_KEY) || "值守员")
  const [connected, setConnected] = useState(() => demoMode || Boolean(token))
  const api = connected ? (demoMode ? new DemoMissionApi() : new HttpMissionApi(token)) : null

  useEffect(() => {
    const unauthorized = () => {
      sessionStorage.removeItem(TOKEN_KEY)
      setToken("")
      setConnected(false)
    }
    window.addEventListener("peacekeeper:unauthorized", unauthorized)
    return () => window.removeEventListener("peacekeeper:unauthorized", unauthorized)
  }, [])

  const connect = async (nextToken: string, nextOperator: string) => {
    const candidate = demoMode ? new DemoMissionApi() : new HttpMissionApi(nextToken)
    if (!(await candidate.health())) throw new Error("Mission API 尚未就绪")
    await candidate.robots()
    if (!demoMode) sessionStorage.setItem(TOKEN_KEY, nextToken)
    localStorage.setItem(OPERATOR_KEY, nextOperator)
    setToken(nextToken)
    setOperatorName(nextOperator)
    setConnected(true)
  }

  const disconnect = () => {
    sessionStorage.removeItem(TOKEN_KEY)
    setToken("")
    setConnected(false)
  }

  return (
    <SessionContext.Provider value={{ api, connected, demoMode, operatorName, connect, disconnect }}>
      {children}
    </SessionContext.Provider>
  )
}

export function useSession() {
  const value = useContext(SessionContext)
  if (!value) throw new Error("SessionProvider is missing")
  return value
}
