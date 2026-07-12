import type { JsonObject, MapPoint } from "../api/types"

export const FLEET_LAB_STORAGE_KEY = "peacekeeper.fleet-lab.v1"
export const FLEET_LAB_COLORS = ["#e85d5d", "#3b82f6", "#e3a526", "#8b5cf6", "#14a98b", "#ec4899"]

export type FleetLabPhase = "draft" | "preparing" | "ready" | "running" | "blocked" | "completed" | "aborted" | "recovery_required"
export type CellState = "idle" | "sending" | "active" | "arrived" | "failed" | "skipped"

export interface FleetWaypoint extends MapPoint {
  yawOverride?: number
}

export interface FleetLabInitialPose extends MapPoint {
  yaw: number
  sent: boolean
  confirmed: boolean
}

export interface FleetLabDraft {
  version: 1
  mapId: string
  robotIds: string[]
  rows: Array<Record<string, FleetWaypoint | null>>
  initialPoses: Record<string, FleetLabInitialPose | null>
  timeoutS: number
  staleAfterS: number
  collisionDistance: number
  phase: FleetLabPhase
}

export interface RobotSliceState {
  goalId: string | null
  state: CellState
  distanceRemaining: number | null
  error: string | null
}

export interface BarrierRun {
  phase: FleetLabPhase
  rowIndex: number
  rowStartedAt: number | null
  robots: Record<string, RobotSliceState>
  error: string | null
}

export interface NavigationSnapshot {
  ready: boolean
  currentMap: string | null
  actionState: string
  activeGoalId: string | null
  resultGoalId: string | null
  resultState: string | null
  resultError: string | null
  feedbackGoalId: string | null
  distanceRemaining: number | null
}

export function createFleetLabDraft(rowCount = 5): FleetLabDraft {
  return {
    version: 1,
    mapId: "",
    robotIds: [],
    rows: Array.from({ length: rowCount }, () => ({})),
    initialPoses: {},
    timeoutS: 180,
    staleAfterS: 10,
    collisionDistance: 0.8,
    phase: "draft",
  }
}

export function loadFleetLabDraft(storage: Storage = sessionStorage): FleetLabDraft {
  const fallback = createFleetLabDraft()
  try {
    const value = JSON.parse(storage.getItem(FLEET_LAB_STORAGE_KEY) || "null") as Partial<FleetLabDraft> | null
    if (!value || value.version !== 1 || !Array.isArray(value.rows) || !Array.isArray(value.robotIds)) return fallback
    const phase = ["preparing", "ready", "running", "blocked"].includes(String(value.phase)) ? "recovery_required" : "draft"
    return {
      ...fallback,
      ...value,
      phase,
      timeoutS: clamp(Number(value.timeoutS), 10, 1800, 180),
      staleAfterS: clamp(Number(value.staleAfterS), 5, 60, 10),
      collisionDistance: clamp(Number(value.collisionDistance), 0.1, 10, 0.8),
    }
  } catch {
    return fallback
  }
}

export function saveFleetLabDraft(draft: FleetLabDraft, storage: Storage = sessionStorage) {
  storage.setItem(FLEET_LAB_STORAGE_KEY, JSON.stringify(draft))
}

export function resizeRows(draft: FleetLabDraft, count: number): FleetLabDraft {
  const size = Math.max(1, Math.min(50, Math.floor(count)))
  const rows = draft.rows.slice(0, size)
  while (rows.length < size) rows.push({})
  return { ...draft, rows }
}

export function waypointYaw(draft: FleetLabDraft, robotId: string, rowIndex: number): number {
  const point = draft.rows[rowIndex]?.[robotId]
  if (!point) return 0
  if (Number.isFinite(point.yawOverride)) return normalizeDegrees(Number(point.yawOverride))
  const next = draft.rows.slice(rowIndex + 1).map((row) => row[robotId]).find(Boolean)
  const previous = draft.rows.slice(0, rowIndex).reverse().map((row) => row[robotId]).find(Boolean)
  const target = next || previous
  if (!target) return draft.initialPoses[robotId]?.yaw ?? 0
  const dx = next ? target.x - point.x : point.x - target.x
  const dy = next ? target.y - point.y : point.y - target.y
  return normalizeDegrees(Math.atan2(dy, dx) * 180 / Math.PI)
}

export function collisionWarnings(draft: FleetLabDraft): Array<{ rowIndex: number; robotA: string; robotB: string; distance: number }> {
  const warnings: Array<{ rowIndex: number; robotA: string; robotB: string; distance: number }> = []
  draft.rows.forEach((row, rowIndex) => {
    draft.robotIds.forEach((robotA, index) => {
      const pointA = row[robotA]
      if (!pointA) return
      draft.robotIds.slice(index + 1).forEach((robotB) => {
        const pointB = row[robotB]
        if (!pointB) return
        const distance = Math.hypot(pointA.x - pointB.x, pointA.y - pointB.y)
        if (distance < draft.collisionDistance) warnings.push({ rowIndex, robotA, robotB, distance })
      })
    })
  })
  return warnings
}

export function createBarrierRun(robotIds: string[]): BarrierRun {
  return {
    phase: "ready",
    rowIndex: 0,
    rowStartedAt: null,
    robots: Object.fromEntries(robotIds.map((robotId) => [robotId, emptyRobotSlice()])),
    error: null,
  }
}

export function beginBarrierRow(run: BarrierRun, robotIds: string[], now = Date.now(), preserveArrived = false): BarrierRun {
  return {
    ...run,
    phase: "running",
    rowStartedAt: now,
    error: null,
    robots: Object.fromEntries(robotIds.map((robotId) => {
      const previous = run.robots[robotId]
      return [robotId, preserveArrived && previous?.state === "arrived" ? previous : { ...emptyRobotSlice(), state: "sending" }]
    })),
  }
}

export function applyGoalId(run: BarrierRun, robotId: string, goalId: string): BarrierRun {
  return {
    ...run,
    robots: { ...run.robots, [robotId]: { ...run.robots[robotId], goalId, state: "active", error: null } },
  }
}

export function markGoalSendFailed(run: BarrierRun, robotId: string, error: string): BarrierRun {
  return {
    ...run,
    phase: "blocked",
    error,
    robots: { ...run.robots, [robotId]: { ...run.robots[robotId], state: "failed", error } },
  }
}

export function evaluateBarrier(
  run: BarrierRun,
  robotIds: string[],
  statuses: Record<string, JsonObject>,
  lastUpdatedAt: Record<string, number>,
  timeoutS: number,
  now = Date.now(),
  staleAfterS = 10,
): { run: BarrierRun; outcome: "waiting" | "advance" | "blocked" } {
  if (run.phase !== "running") return { run, outcome: run.phase === "blocked" ? "blocked" : "waiting" }
  let blockedError = ""
  const robots = { ...run.robots }
  for (const robotId of robotIds) {
    const current = robots[robotId]
    if (!current || current.state === "arrived") continue
    const live = statuses[robotId]
    if (!live?.online) blockedError ||= `${robotId} 已离线`
    else if (!lastUpdatedAt[robotId] || now - lastUpdatedAt[robotId] > staleAfterS * 1000) blockedError ||= `${robotId} 状态超过 ${staleAfterS} 秒未更新`
    const navigation = navigationSnapshot(live)
    if (navigation.feedbackGoalId === current.goalId) {
      robots[robotId] = { ...current, state: "active", distanceRemaining: navigation.distanceRemaining }
    }
    if (navigation.resultGoalId !== current.goalId || !current.goalId) continue
    if (navigation.resultState === "succeeded") {
      robots[robotId] = { ...current, state: "arrived", distanceRemaining: 0, error: null }
    } else if (navigation.resultState && ["failed", "rejected", "canceled", "aborted", "stopped"].includes(navigation.resultState)) {
      const error = navigation.resultError || `${robotId} 导航结束：${navigation.resultState}`
      robots[robotId] = { ...current, state: "failed", error }
      blockedError ||= error
    }
  }
  if (!blockedError && run.rowStartedAt && now - run.rowStartedAt > timeoutS * 1000) blockedError = `时间片 ${run.rowIndex + 1} 超过 ${timeoutS} 秒`
  if (blockedError) return { run: { ...run, phase: "blocked", robots, error: blockedError }, outcome: "blocked" }
  const arrived = robotIds.every((robotId) => robots[robotId]?.state === "arrived")
  return { run: { ...run, robots }, outcome: arrived ? "advance" : "waiting" }
}

export function navigationSnapshot(live?: JsonObject): NavigationSnapshot {
  const status = objectValue(live?.status)
  const navigation = objectValue(status.navigation)
  const result = objectValue(navigation.last_result)
  const feedback = objectValue(navigation.feedback)
  return {
    ready: Boolean(navigation.ready),
    currentMap: stringValue(navigation.current_map),
    actionState: stringValue(navigation.action_state) || "idle",
    activeGoalId: stringValue(navigation.active_goal_id),
    resultGoalId: stringValue(result.goal_id),
    resultState: stringValue(result.state),
    resultError: stringValue(result.error),
    feedbackGoalId: stringValue(feedback.goal_id),
    distanceRemaining: numberValue(feedback.distance_remaining),
  }
}

export function goalIdFromResponse(value: JsonObject): string | null {
  const result = objectValue(value.result)
  const resultNavigation = objectValue(result.navigation)
  const navigation = objectValue(value.navigation)
  return stringValue(resultNavigation.goal_id)
    || stringValue(result.goal_id)
    || stringValue(navigation.goal_id)
    || stringValue(value.goal_id)
}

export function degreesToRadiansNormalized(value: number) {
  return normalizeDegrees(value) * Math.PI / 180
}

function emptyRobotSlice(): RobotSliceState {
  return { goalId: null, state: "idle", distanceRemaining: null, error: null }
}

function normalizeDegrees(value: number) {
  return ((value % 360) + 360) % 360
}

function objectValue(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {}
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value ? value : null
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function clamp(value: number, minimum: number, maximum: number, fallback: number) {
  return Number.isFinite(value) ? Math.min(maximum, Math.max(minimum, value)) : fallback
}
