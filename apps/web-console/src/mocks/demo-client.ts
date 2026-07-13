import type { AlertRecord, JsonObject, LiveMapStatus, MapDeployment, Mission, MissionApi, NavigationPose, Overview, Page, Robot, RobotEvent, RuntimeStatusMessage, StatusSubscriptionObserver, StoredMap, VehicleSavedMap, VisionCapture, VisionStatus } from "../api/types"

const now = new Date()
const iso = (minutes = 0) => new Date(now.getTime() - minutes * 60_000).toISOString()

const robots: Robot[] = [
  {
    id: "car_1", name: "苍松一号", base_url: "http://10.60.162.192:8001", role: "leader", enabled: true,
    capabilities: { mapping: true, navigation: true, patrol: true }, last_seen: iso(), online: true,
    runtime_status: { mode: "IDLE", processes: { lidar: "running", slam: "stopped", nav2: "running" }, navigation: { ready: true, action_state: "running", current_map: "forest_lab__v3", active_goal_id: null }, patrol: { state: "idle", current_index: 0, total_points: 0 }, ros: { last_speed: 25, publisher_ready: true } },
  },
  {
    id: "car_2", name: "云杉二号", base_url: "http://10.60.162.193:8001", role: "wing", enabled: true,
    capabilities: { mapping: true, navigation: true, patrol: true }, last_seen: iso(2), online: true,
    runtime_status: { mode: "IDLE", processes: { lidar: "running", slam: "stopped", nav2: "running" }, navigation: { ready: true, action_state: "running", current_map: "forest_lab__v3", active_goal_id: null }, patrol: { state: "idle" }, ros: { last_speed: 20, publisher_ready: true } },
  },
  {
    id: "car_3", name: "冷杉三号", base_url: "http://10.60.162.194:8001", role: "reserve", enabled: true,
    capabilities: { mapping: true, navigation: true }, last_seen: iso(48), online: false, runtime_status: null, runtime_error: "连续三次状态轮询失败",
  },
]

const maps: StoredMap[] = [
  { id: "map-forest-v3", logical_name: "forest_lab", version: 3, yaml_sha256: "a".repeat(64), image_sha256: "b".repeat(64), bundle_sha256: "c".repeat(64), resolution: 0.05, origin: [-12.4, -8.2, 0], width: 608, height: 384, source_robot_id: "car_1", created_at: iso(25) },
  { id: "map-corridor-v1", logical_name: "north_corridor", version: 1, yaml_sha256: "d".repeat(64), image_sha256: "e".repeat(64), bundle_sha256: "f".repeat(64), resolution: 0.05, origin: [-4.1, -15.8, 0], width: 420, height: 720, source_robot_id: "car_2", created_at: iso(1440) },
]

const missions: Mission[] = [
  { id: "mission-1", mission_type: "patrol", robot_id: "car_1", state: "running", request: { route: "north_loop" }, result: null, error: null, created_at: iso(18), started_at: iso(18), finished_at: null },
  { id: "mission-2", mission_type: "map_dispatch", robot_id: null, state: "completed", request: { map_id: "map-forest-v3", robot_ids: ["car_1", "car_2"] }, result: { installed: 2 }, error: null, created_at: iso(90), started_at: iso(90), finished_at: iso(88) },
  { id: "mission-3", mission_type: "navigation_goal", robot_id: "car_3", state: "failed", request: { x: 2.4, y: 1.1 }, result: null, error: "Robot car_3 is unreachable", created_at: iso(220), started_at: iso(220), finished_at: iso(218) },
]

const events: RobotEvent[] = [
  { id: "event-1", robot_id: "car_1", mission_id: "mission-1", event_type: "smoke_detected", severity: "warning", payload: { confidence: 0.82, zone: "北侧样区" }, occurred_at: iso(4), received_at: iso(4) },
  { id: "event-2", robot_id: "car_2", mission_id: null, event_type: "patrol_checkpoint", severity: "info", payload: { checkpoint: 3 }, occurred_at: iso(33), received_at: iso(33) },
]

const alerts: AlertRecord[] = [
  { id: "alert-1", event_id: "event-1", state: "pending", confirmed_by: null, confirmed_at: null, resolution: null, event: events[0] },
]

const deployments: MapDeployment[] = [
  { id: "dep-1", map_id: "map-forest-v3", robot_id: "car_1", state: "installed", installed_name: "forest_lab__v3", error: null, created_at: iso(80), started_at: iso(80), finished_at: iso(79), map: { id: "map-forest-v3", logical_name: "forest_lab", version: 3 }, robot: { id: "car_1", name: "苍松一号" } },
  { id: "dep-2", map_id: "map-forest-v3", robot_id: "car_2", state: "installed", installed_name: "forest_lab__v3", error: null, created_at: iso(80), started_at: iso(80), finished_at: iso(78), map: { id: "map-forest-v3", logical_name: "forest_lab", version: 3 }, robot: { id: "car_2", name: "云杉二号" } },
]

const visionCapture: VisionCapture = {
  ok: true,
  model: "yolov8n.engine",
  image_width: 960,
  image_height: 540,
  inference_ms: 45.4,
  detections: [{ label: "cat", confidence: 0.91, bbox: { x: 180, y: 120, width: 260, height: 210 } }],
  label_counts: { cat: 1 },
  triggers: { cat: { detected: true, count: 1 } },
  captured_at: Date.now() / 1000,
  annotated_image_available: true,
}

function page<T>(items: T[], filters: Record<string, string> = {}): Page<T> {
  const limit = Number(filters.limit || 50)
  const offset = Number(filters.offset || 0)
  return { items: items.slice(offset, offset + limit), total: items.length, limit, offset }
}

export class DemoMissionApi implements MissionApi {
  private listeners = new Set<(message: RuntimeStatusMessage) => void>()
  private goalSequence = 0

  private emit(robot: Robot) {
    const message: RuntimeStatusMessage = { type: "robot_status", robot_id: robot.id, data: { online: robot.online, last_seen: new Date().toISOString(), status: robot.runtime_status } }
    this.listeners.forEach((listener) => listener(message))
  }
  async health() { return true }
  async overview(): Promise<Overview> {
    return {
      robots: { total: robots.length, enabled: robots.filter((item) => item.enabled).length, online: robots.filter((item) => item.online).length, offline: robots.filter((item) => item.enabled && !item.online).length },
      missions: { total: missions.length, running: missions.filter((item) => item.state === "running").length, failed: missions.filter((item) => item.state === "failed").length },
      alerts: { total: alerts.length, pending: alerts.filter((item) => item.state === "pending").length },
      maps: { total: maps.length, latest_created_at: maps[0].created_at },
    }
  }
  async robots() { return structuredClone(robots) }
  async robot(id: string) { const value = robots.find((item) => item.id === id); if (!value) throw new Error("车辆不存在"); return structuredClone(value) }
  async createRobot(payload: Partial<Robot>) {
    const value: Robot = { id: String(payload.id), name: String(payload.name), base_url: String(payload.base_url), role: payload.role || "robot", enabled: payload.enabled ?? true, capabilities: payload.capabilities || {}, last_seen: null, online: false, runtime_status: null }
    robots.push(value); return structuredClone(value)
  }
  async updateRobot(id: string, payload: Partial<Robot>) { const value = robots.find((item) => item.id === id); if (!value) throw new Error("车辆不存在"); Object.assign(value, payload); return structuredClone(value) }
  async robotAction(id: string, path: string, payload: JsonObject = {}) {
    const robot = robots.find((item) => item.id === id); if (!robot) throw new Error("车辆不存在")
    const status = (robot.runtime_status ||= {}) as JsonObject
    if (path === "control/estop") status.mode = "EMERGENCY_STOP"
    if (path === "control/clear-estop") status.mode = "IDLE"
    if (path === "control/stop") status.mode = "IDLE"
    if (path === "mapping/start") status.mode = "MAPPING"
    if (path === "mapping/stop") status.mode = "IDLE"
    if (path === "navigation/start") status.navigation = { ready: true, current_map: payload.map_name, action_state: "running", active_goal_id: null }
    if (path === "navigation/initial-pose") status.navigation = { ...(status.navigation as JsonObject), initial_pose: payload }
    if (path === "navigation/cancel") status.navigation = { ...(status.navigation as JsonObject), action_state: "canceled", active_goal_id: null }
    if (path === "navigation/stop") status.navigation = { ready: false, current_map: null, action_state: "idle", active_goal_id: null }
    this.emit(robot)
    return { ok: true, demo: true, path, payload }
  }
  async navigationStart(id: string, mapName: string) { return this.robotAction(id, "navigation/start", { map_name: mapName }) }
  async navigationInitialPose(id: string, pose: NavigationPose) { return this.robotAction(id, "navigation/initial-pose", { ...pose }) }
  async navigationGoal(id: string, pose: NavigationPose) {
    const robot = robots.find((item) => item.id === id); if (!robot) throw new Error("车辆不存在")
    const status = (robot.runtime_status ||= {}) as JsonObject
    const goalId = `demo-nav-${++this.goalSequence}`
    status.mode = "NAV_PATROL"
    status.navigation = { ...(status.navigation as JsonObject), ready: true, current_map: pose.map_name, action_state: "active", active_goal_id: goalId, current_goal: { id: goalId, ...pose }, feedback: { goal_id: goalId, distance_remaining: 2.4 } }
    this.emit(robot)
    window.setTimeout(() => {
      const navigation = status.navigation as JsonObject
      if (navigation.active_goal_id !== goalId) return
      navigation.feedback = { goal_id: goalId, distance_remaining: 0.7 }
      this.emit(robot)
    }, id === "car_1" ? 450 : 750)
    window.setTimeout(() => {
      const navigation = status.navigation as JsonObject
      if (navigation.active_goal_id !== goalId) return
      navigation.action_state = "succeeded"
      navigation.active_goal_id = null
      navigation.feedback = { goal_id: goalId, distance_remaining: 0 }
      navigation.last_result = { goal_id: goalId, state: "succeeded", error: null }
      this.emit(robot)
    }, id === "car_1" ? 900 : 1400)
    return { mission_id: `demo-mission-${goalId}`, result: { ok: true, goal_id: goalId } }
  }
  async navigationCancel(id: string) { return this.robotAction(id, "navigation/cancel") }
  async navigationStop(id: string) { return this.robotAction(id, "navigation/stop") }
  async fleetStop() { robots.forEach((robot) => { if (robot.runtime_status) robot.runtime_status.mode = "IDLE" }); return { ok: true, demo: true } }
  async maps() { return structuredClone(maps) }
  async map(id: string) { const value = maps.find((item) => item.id === id); if (!value) throw new Error("地图不存在"); return structuredClone(value) }
  async mapPreview(id: string) {
    const label = maps.find((item) => item.id === id)?.logical_name || "map"
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="608" height="384"><rect width="100%" height="100%" fill="#d8d8d2"/><path d="M55 310H210V248H310V188H550M210 248V75M310 188V320M430 188V62" fill="none" stroke="#183c31" stroke-width="18"/><path d="M55 310H210V248H310V188H550M210 248V75M310 188V320M430 188V62" fill="none" stroke="#f9f8f2" stroke-width="13"/><text x="24" y="36" font-family="sans-serif" font-size="18" fill="#244c40">${label}</text></svg>`
    return new Blob([svg], { type: "image/svg+xml" })
  }
  async vehicleMaps(_robotId: string): Promise<VehicleSavedMap[]> {
    return maps.map((item, index) => ({
      name: `${item.logical_name}__v${item.version}`,
      yaml: `/root/peacekeeper-car/data/maps/${item.logical_name}__v${item.version}.yaml`,
      pgm: `/root/peacekeeper-car/data/maps/${item.logical_name}__v${item.version}.pgm`,
      updated_at: Date.now() / 1000 - index * 3600,
      size_bytes: item.width * item.height,
    }))
  }
  async vehicleMapPreview(_robotId: string, mapName: string) {
    const map = maps.find((item) => `${item.logical_name}__v${item.version}` === mapName) || maps[0]
    return this.mapPreview(map.id)
  }
  async liveMapStatus(robotId: string): Promise<LiveMapStatus> {
    const mapping = robots.find((item) => item.id === robotId)?.runtime_status?.mode === "MAPPING"
    return {
      available: true, ready: true, has_map: Boolean(mapping), topic: "/map", message_count: mapping ? 36 : 0,
      last_received_at: mapping ? Date.now() / 1000 : null, age_s: mapping ? 0.4 : null,
      width: 608, height: 384, resolution: 0.05, origin: [-12.4, -8.2, 0], frame_id: "map",
      last_error: null, spin_thread_alive: true,
    }
  }
  async liveMapPreview(_robotId: string) { return this.mapPreview("map-forest-v3") }
  async videoSample(_robotId: string) {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540"><rect width="100%" height="100%" fill="#0f1b17"/><rect x="70" y="70" width="820" height="400" rx="28" fill="#1d3a31" stroke="#78c29b" stroke-width="6"/><circle cx="225" cy="270" r="84" fill="#2f7659"/><circle cx="225" cy="270" r="45" fill="#9fe0bc"/><path d="M410 215h300M410 270h210M410 325h265" stroke="#d8efe3" stroke-width="18" stroke-linecap="round"/><text x="72" y="40" font-family="sans-serif" font-size="28" fill="#9fe0bc">camera demo</text></svg>`
    return new Blob([svg], { type: "image/svg+xml" })
  }
  videoStreamUrl(_robotId: string) {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540"><rect width="100%" height="100%" fill="#0f1b17"/><rect x="70" y="70" width="820" height="400" rx="28" fill="#1d3a31" stroke="#78c29b" stroke-width="6"/><circle cx="225" cy="270" r="84" fill="#2f7659"/><circle cx="225" cy="270" r="45" fill="#9fe0bc"/><path d="M410 215h300M410 270h210M410 325h265" stroke="#d8efe3" stroke-width="18" stroke-linecap="round"/><text x="72" y="40" font-family="sans-serif" font-size="28" fill="#9fe0bc">camera demo</text></svg>`
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
  }
  async visionStatus(_robotId: string): Promise<VisionStatus> {
    return { enabled: true, engine_path: null, model_loaded: true, target_labels: ["cat"], latest_available: true, last_result: structuredClone(visionCapture), last_error: null }
  }
  async visionCapture(_robotId: string) { return { ...structuredClone(visionCapture), captured_at: Date.now() / 1000 } }
  async visionLatest(_robotId: string) { return structuredClone(visionCapture) }
  async visionLatestImage(robotId: string) { return this.videoSample(robotId) }
  visionStreamUrl(robotId: string) { return this.videoStreamUrl(robotId) }
  async mapDownload(id: string) { return new Blob([`demo bundle ${id}`], { type: "application/zip" }) }
  async uploadMap(file: File, logicalName?: string) { return { ok: true, demo: true, filename: file.name, logical_name: logicalName } }
  async importMap(payload: { robot_id: string; map_name: string; logical_name?: string }) { return { ok: true, demo: true, ...payload } }
  async dispatchMap(id: string, robotIds: string[]) { return { ok: true, demo: true, map_id: id, robot_ids: robotIds } }
  async deployments(filters: Record<string, string> = {}) { return page(deployments.filter((item) => (!filters.robot_id || item.robot_id === filters.robot_id) && (!filters.map_id || item.map_id === filters.map_id)), filters) }
  async missions(filters: Record<string, string> = {}) { return page(missions.filter((item) => (!filters.robot_id || item.robot_id === filters.robot_id) && (!filters.state || item.state === filters.state) && (!filters.mission_type || item.mission_type === filters.mission_type)), filters) }
  async events(filters: Record<string, string> = {}) { return page(events.filter((item) => (!filters.robot_id || item.robot_id === filters.robot_id) && (!filters.severity || item.severity === filters.severity) && (!filters.event_type || item.event_type === filters.event_type)), filters) }
  async alerts(filters: Record<string, string> = {}) { return page(alerts.filter((item) => (!filters.robot_id || item.event.robot_id === filters.robot_id) && (!filters.state || item.state === filters.state)), filters) }
  async confirmAlert(id: string, confirmedBy: string, resolution?: string) { const value = alerts.find((item) => item.id === id); if (value) Object.assign(value, { state: "confirmed", confirmed_by: confirmedBy, confirmed_at: new Date().toISOString(), resolution: resolution || null }); return { ok: true, demo: true } }
  subscribeStatus(listener: (message: RuntimeStatusMessage) => void, observer: StatusSubscriptionObserver = {}) {
    this.listeners.add(listener)
    observer.onOpen?.()
    listener({ type: "snapshot", robots: Object.fromEntries(robots.map((robot) => [robot.id, { online: robot.online, status: robot.runtime_status }])) })
    const timer = window.setInterval(() => listener({ type: "robot_status", robot_id: "car_1", data: { online: true, status: robots[0].runtime_status } }), 5000)
    return () => { window.clearInterval(timer); this.listeners.delete(listener); observer.onClose?.() }
  }
}
