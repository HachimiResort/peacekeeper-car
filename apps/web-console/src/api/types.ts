import type { components } from "./generated"

export type JsonObject = Record<string, unknown>

type Schemas = components["schemas"]

type GeneratedVisionDetection = Schemas["VisionDetection"]
type GeneratedVisionCapture = Schemas["VisionCaptureResponse"]

export type VisionDetection = GeneratedVisionDetection & {
  bearing_deg?: number | null
  range_m?: number | null
  range_source?: string | null
  range_quality?: string | null
  measurement_age_ms?: number | null
  measurement_timestamp?: number | null
  measurement_angle_deg?: number | null
  robot_pose_map?: { x: number; y: number; yaw?: number | null } | null
  target_pose_map?: { x: number; y: number; yaw?: number | null } | null
  localization_valid?: boolean
}

export interface VisionRangeSummary {
  measured_count: number
  total_count: number
  sources: string[]
}

export interface HazardStatus {
  enabled: boolean
  state: string
  current_event_key: string | null
  current_detection: VisionDetection | null
  confirmation_hits: number
  confirmation_window: number
  hold_started_at: number | null
  auto_resume_remaining_s: number | null
  takeover: boolean
  hold_requested: boolean
  last_error: string | null
  outbox?: { pending: number; mission_api_configured: boolean; last_error: string | null }
}

export type VisionCapture = Omit<GeneratedVisionCapture, "detections"> & {
  detections?: VisionDetection[]
  range_measurements?: VisionRangeSummary | null
}

export type VisionStatus = Schemas["VisionStatusResponse"]

export interface Robot extends Omit<Schemas["RobotResponse"], "last_seen" | "runtime_status" | "runtime_error"> {
  last_seen: string | null
  runtime_status: JsonObject | null
  runtime_error?: string | null
}

export interface StoredMap extends Omit<Schemas["MapResponse"], "source_robot_id" | "created_at"> {
  source_robot_id: string | null
  created_at: string | null
}

export interface Mission extends Omit<Schemas["MissionResponse"], "robot_id" | "request" | "result" | "error" | "created_at" | "started_at" | "finished_at"> {
  robot_id: string | null
  request: JsonObject
  result: JsonObject | null
  error: string | null
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

export interface RobotEvent extends Omit<Schemas["EventResponse"], "mission_id" | "payload" | "occurred_at" | "received_at"> {
  event_key: string
  mission_id: string | null
  payload: JsonObject
  occurred_at: string | null
  received_at: string | null
}

export interface AlertRecord extends Omit<Schemas["AlertResponse"], "confirmed_by" | "confirmed_at" | "resolution" | "event"> {
  confirmed_by: string | null
  confirmed_at: string | null
  resolution: string | null
  action?: string | null
  event: RobotEvent
}

export interface MapDeployment extends Omit<Schemas["DeploymentResponse"], "installed_name" | "error" | "created_at" | "started_at" | "finished_at" | "map" | "robot"> {
  installed_name: string | null
  error: string | null
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  map: { id: string; logical_name: string; version: number }
  robot: { id: string; name: string }
}

type GeneratedOverview = Schemas["OverviewResponse"]

export interface Overview extends GeneratedOverview {
  robots: { total: number; enabled: number; online: number; offline: number }
  missions: { total: number; running: number; failed: number }
  alerts: { total: number; pending: number }
  maps: { total: number; latest_created_at: string | null }
}

export interface Page<T> {
  total: number
  limit: number
  offset: number
  items: T[]
}

export interface MapPoint {
  x: number
  y: number
  pixelX: number
  pixelY: number
}

export interface RuntimeStatusMessage {
  type: "snapshot" | "robot_status" | "hazard_event"
  robots?: Record<string, JsonObject>
  robot_id?: string
  data?: JsonObject
  event?: RobotEvent
}

export interface NavigationPose {
  map_name: string
  x: number
  y: number
  yaw: number
}

export interface StatusSubscriptionObserver {
  onOpen?(): void
  onClose?(): void
  onError?(error: unknown): void
}

export interface LiveMapStatus {
  available: boolean
  ready: boolean
  has_map: boolean
  topic: string
  message_count: number
  last_received_at: number | null
  age_s: number | null
  width: number
  height: number
  resolution: number
  origin: number[]
  frame_id: string
  last_error: string | null
  spin_thread_alive: boolean
}

export interface VehicleSavedMap {
  name: string
  yaml: string
  pgm: string
  updated_at: number
  size_bytes: number
}

export interface AudioAsset {
  name: string
  bytes: number
}

export interface AudioStatus {
  enabled: boolean
  available: boolean
  playing: boolean
  asset: string | null
  pid: number | null
  loop: boolean
  volume: number
  started_at: number | null
  last_error: string | null
}

export interface MissionApi {
  health(): Promise<boolean>
  overview(): Promise<Overview>
  robots(): Promise<Robot[]>
  robot(id: string): Promise<Robot>
  createRobot(payload: Partial<Robot>): Promise<Robot>
  updateRobot(id: string, payload: Partial<Robot>): Promise<Robot>
  robotAction(id: string, path: string, payload?: JsonObject): Promise<JsonObject>
  navigationStart(id: string, mapName: string): Promise<JsonObject>
  navigationInitialPose(id: string, pose: NavigationPose): Promise<JsonObject>
  navigationGoal(id: string, pose: NavigationPose): Promise<JsonObject>
  navigationCancel(id: string): Promise<JsonObject>
  navigationStop(id: string): Promise<JsonObject>
  audioAssets(robotId: string): Promise<AudioAsset[]>
  audioStatus(robotId: string): Promise<AudioStatus>
  uploadAudio(robotId: string, file: File): Promise<JsonObject>
  fleetStop(): Promise<JsonObject>
  maps(): Promise<StoredMap[]>
  map(id: string): Promise<StoredMap>
  mapPreview(id: string): Promise<Blob>
  vehicleMaps(robotId: string): Promise<VehicleSavedMap[]>
  vehicleMapPreview(robotId: string, mapName: string): Promise<Blob>
  liveMapStatus(robotId: string): Promise<LiveMapStatus>
  liveMapPreview(robotId: string): Promise<Blob>
  videoSample(robotId: string): Promise<Blob>
  videoStreamUrl(robotId: string): string
  visionStatus(robotId: string): Promise<VisionStatus>
  visionCapture(robotId: string): Promise<VisionCapture>
  visionLatest(robotId: string): Promise<VisionCapture>
  visionLatestImage(robotId: string): Promise<Blob>
  visionStreamUrl(robotId: string): string
  hazardStatus(robotId: string): Promise<HazardStatus>
  setHazardMonitor(robotId: string, enabled: boolean): Promise<JsonObject>
  hazardAction(robotId: string, eventKey: string, action: "takeover" | "resume" | "hold", payload?: JsonObject): Promise<JsonObject>
  eventEvidenceUrl(eventId: string, kind: "raw" | "annotated" | "metadata"): string
  mapDownload(id: string): Promise<Blob>
  uploadMap(file: File, logicalName?: string): Promise<JsonObject>
  importMap(payload: { robot_id: string; map_name: string; logical_name?: string }): Promise<JsonObject>
  dispatchMap(id: string, robotIds: string[]): Promise<JsonObject>
  deployments(filters?: Record<string, string>): Promise<Page<MapDeployment>>
  missions(filters?: Record<string, string>): Promise<Page<Mission>>
  events(filters?: Record<string, string>): Promise<Page<RobotEvent>>
  alerts(filters?: Record<string, string>): Promise<Page<AlertRecord>>
  confirmAlert(id: string, confirmedBy: string, resolution?: string, action?: "acknowledge" | "takeover" | "false_positive" | "resolved"): Promise<JsonObject>
  subscribeStatus(listener: (message: RuntimeStatusMessage) => void, observer?: StatusSubscriptionObserver): () => void
}
