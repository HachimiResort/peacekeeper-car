import type {
  AlertRecord,
  JsonObject,
  MapDeployment,
  Mission,
  MissionApi,
  Overview,
  Robot,
  RobotEvent,
  RuntimeStatusMessage,
  StoredMap,
} from "./types"

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly details?: unknown,
  ) {
    super(message)
  }
}

function query(filters: Record<string, string> = {}) {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== "") params.set(key, value)
  })
  const value = params.toString()
  return value ? `?${value}` : ""
}

export class HttpMissionApi implements MissionApi {
  constructor(private readonly token: string) {}

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers)
    headers.set("X-Peacekeeper-Token", this.token)
    if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json")
    const response = await fetch(path, { ...init, headers })
    if (response.status === 401) {
      window.dispatchEvent(new CustomEvent("peacekeeper:unauthorized"))
    }
    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as
        | { error?: { code?: string; message?: string; details?: unknown }; message?: string }
        | null
      const code = payload?.error?.code || "request_failed"
      const fallback = response.status >= 500 ? "中心服务暂时不可用" : "操作未完成"
      throw new ApiError(payload?.error?.message || payload?.message || fallback, response.status, code, payload?.error?.details)
    }
    if (response.status === 204) return undefined as T
    return response.json() as Promise<T>
  }

  private async blob(path: string): Promise<Blob> {
    const response = await fetch(path, { headers: { "X-Peacekeeper-Token": this.token } })
    if (!response.ok) throw new ApiError("文件读取失败", response.status, "blob_failed")
    return response.blob()
  }

  async health() {
    const response = await fetch("/health/ready")
    return response.ok && Boolean((await response.json()).ok)
  }

  async overview() { return this.request<Overview>("/api/overview") }
  async robots() { return (await this.request<{ robots: Robot[] }>("/api/robots")).robots }
  async robot(id: string) { return this.request<Robot>(`/api/robots/${encodeURIComponent(id)}`) }
  async createRobot(payload: Partial<Robot>) { return this.request<Robot>("/api/robots", { method: "POST", body: JSON.stringify(payload) }) }
  async updateRobot(id: string, payload: Partial<Robot>) { return this.request<Robot>(`/api/robots/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(payload) }) }
  async robotAction(id: string, path: string, payload: JsonObject = {}) {
    return this.request<JsonObject>(`/api/robots/${encodeURIComponent(id)}/${path}`, { method: "POST", body: JSON.stringify(payload) })
  }
  async fleetStop() { return this.request<JsonObject>("/api/fleet/stop", { method: "POST", body: "{}" }) }
  async maps() { return (await this.request<{ maps: StoredMap[] }>("/api/maps")).maps }
  async map(id: string) { return this.request<StoredMap>(`/api/maps/${id}`) }
  async mapPreview(id: string) { return this.blob(`/api/maps/${id}/preview.png`) }
  async mapDownload(id: string) { return this.blob(`/api/maps/${id}/download`) }
  async uploadMap(file: File, logicalName?: string) {
    const form = new FormData()
    form.set("bundle", file)
    return this.request<JsonObject>(`/api/maps/upload${query(logicalName ? { logical_name: logicalName } : {})}`, { method: "POST", body: form })
  }
  async importMap(payload: { robot_id: string; map_name: string; logical_name?: string }) {
    return this.request<JsonObject>("/api/maps/import-from-robot", { method: "POST", body: JSON.stringify(payload) })
  }
  async dispatchMap(id: string, robotIds: string[]) {
    return this.request<JsonObject>(`/api/maps/${id}/dispatch`, { method: "POST", body: JSON.stringify({ robot_ids: robotIds }) })
  }
  async deployments(filters: Record<string, string> = {}) {
    const data = await this.request<{ deployments: MapDeployment[]; total: number; limit: number; offset: number }>(`/api/map-deployments${query(filters)}`)
    return { items: data.deployments, total: data.total, limit: data.limit, offset: data.offset }
  }
  async missions(filters: Record<string, string> = {}) {
    const data = await this.request<{ missions: Mission[]; total: number; limit: number; offset: number }>(`/api/missions${query(filters)}`)
    return { items: data.missions, total: data.total, limit: data.limit, offset: data.offset }
  }
  async events(filters: Record<string, string> = {}) {
    const data = await this.request<{ events: RobotEvent[]; total: number; limit: number; offset: number }>(`/api/events${query(filters)}`)
    return { items: data.events, total: data.total, limit: data.limit, offset: data.offset }
  }
  async alerts(filters: Record<string, string> = {}) {
    const data = await this.request<{ alerts: AlertRecord[]; total: number; limit: number; offset: number }>(`/api/alerts${query(filters)}`)
    return { items: data.alerts, total: data.total, limit: data.limit, offset: data.offset }
  }
  async confirmAlert(id: string, confirmedBy: string, resolution?: string) {
    return this.request<JsonObject>(`/api/alerts/${id}/confirm`, { method: "POST", body: JSON.stringify({ confirmed_by: confirmedBy, resolution: resolution || null }) })
  }

  subscribeStatus(listener: (message: RuntimeStatusMessage) => void) {
    let socket: WebSocket | null = null
    let stopped = false
    let attempt = 0
    let timer: number | undefined
    const connect = () => {
      if (stopped) return
      const protocol = location.protocol === "https:" ? "wss:" : "ws:"
      socket = new WebSocket(`${protocol}//${location.host}/ws/status?token=${encodeURIComponent(this.token)}`)
      socket.onopen = () => { attempt = 0 }
      socket.onmessage = (event) => listener(JSON.parse(event.data) as RuntimeStatusMessage)
      socket.onclose = () => {
        if (stopped) return
        const delay = reconnectDelay(attempt++)
        timer = window.setTimeout(connect, delay)
      }
    }
    connect()
    return () => {
      stopped = true
      if (timer) window.clearTimeout(timer)
      socket?.close()
    }
  }
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  anchor.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

export function reconnectDelay(attempt: number) {
  return Math.min(1000 * 2 ** attempt, 15_000)
}

export function pixelToMap(map: StoredMap, pixelX: number, pixelY: number) {
  return {
    x: map.origin[0] + (pixelX + 0.5) * map.resolution,
    y: map.origin[1] + (map.height - pixelY - 0.5) * map.resolution,
    pixelX,
    pixelY,
  }
}

export function degreesToRadians(value: number) {
  return value * Math.PI / 180
}
