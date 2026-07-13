import { useCallback, useEffect, useRef, useState } from "react"
import { AlertOctagon, ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Ban, ChevronDown, ChevronUp, Crosshair, MapPinned, Pause, Play, RotateCcw, Save, Square, Trash2, Waypoints, X } from "lucide-react"
import { Link, useParams } from "react-router-dom"
import { useFeedback } from "../app/feedback"
import { useLiveStatus } from "../app/live-status"
import { useSession } from "../app/session"
import { useAsyncAction } from "../app/use-async-action"
import { degreesToRadians } from "../api/client"
import { MotionCommander } from "../api/motion"
import type { MapDeployment, MapPoint, Robot, StoredMap } from "../api/types"
import { CameraPreview } from "../components/camera-preview"
import { LiveMapPreview } from "../components/live-map-preview"
import { HazardPanel } from "../components/hazard-panel"
import { MapCanvas } from "../components/map-canvas"
import { VisionDetectionCard } from "../components/vision-detection-card"
import { Badge, Button, Card, EmptyState, InlineActionStatus, JsonPanel, LoadingBlock, PageHeader, StatusDot, fieldClass } from "../components/ui"

type Direction = "forward" | "backward" | "left" | "right"
type PanelName = "control" | "mapping" | "navigation" | "patrol"
type PanelState = { tone: "info" | "success" | "error" | "warning"; title: string; detail?: string }

const motion: Record<Direction, { linear_x: number; angular_z: number }> = {
  forward: { linear_x: 1, angular_z: 0 },
  backward: { linear_x: -1, angular_z: 0 },
  left: { linear_x: 0, angular_z: 3 },
  right: { linear_x: 0, angular_z: -3 },
}

const labels: Record<string, { pending: string; success: string; panel: PanelName }> = {
  "control/estop": { pending: "正在急停", success: "急停指令已下发", panel: "control" },
  "control/clear-estop": { pending: "正在解除", success: "解除急停指令已下发", panel: "control" },
  "mapping/start": { pending: "正在启动", success: "建图指令已下发", panel: "mapping" },
  "mapping/stop": { pending: "正在停止", success: "停止 SLAM 指令已下发", panel: "mapping" },
  "mapping/save": { pending: "正在保存", success: "地图已保存并导入中心", panel: "mapping" },
  "navigation/start": { pending: "正在启动", success: "Nav2 启动指令已下发", panel: "navigation" },
  "navigation/initial-pose": { pending: "正在设置", success: "初始位姿已下发", panel: "navigation" },
  "navigation/goal": { pending: "正在下发", success: "导航目标已下发", panel: "navigation" },
  "navigation/cancel": { pending: "正在取消", success: "取消目标指令已下发", panel: "navigation" },
  "navigation/stop": { pending: "正在停止", success: "停止 Nav2 指令已下发", panel: "navigation" },
  "patrol/start": { pending: "正在启动", success: "巡护指令已下发", panel: "patrol" },
  "patrol/pause": { pending: "正在暂停", success: "暂停巡护指令已下发", panel: "patrol" },
  "patrol/resume": { pending: "正在恢复", success: "恢复巡护指令已下发", panel: "patrol" },
  "patrol/cancel": { pending: "正在取消", success: "取消巡护指令已下发", panel: "patrol" },
}

export function RobotDetailPage() {
  const { robotId = "" } = useParams()
  const { api } = useSession()
  const { statuses } = useLiveStatus()
  const feedback = useFeedback()
  const notifyRef = useRef(feedback.notify)
  notifyRef.current = feedback.notify
  const actions = useAsyncAction()
  const [robot, setRobot] = useState<Robot | null>(null)
  const [loadError, setLoadError] = useState("")
  const [maps, setMaps] = useState<StoredMap[]>([])
  const [deployments, setDeployments] = useState<MapDeployment[]>([])
  const [selectedDeployment, setSelectedDeployment] = useState("")
  const [selectedPoint, setSelectedPoint] = useState<MapPoint | null>(null)
  const [patrolPoints, setPatrolPoints] = useState<MapPoint[]>([])
  const [yaw, setYaw] = useState(0)
  const [dwell, setDwell] = useState(1)
  const [loop, setLoop] = useState(false)
  const [speed, setSpeed] = useState(0.2)
  const [mapName, setMapName] = useState("forest_map")
  const [activeDirection, setActiveDirection] = useState<Direction | null>(null)
  const [cameraActive, setCameraActive] = useState(false)
  const [panelState, setPanelState] = useState<Partial<Record<PanelName, PanelState>>>({})
  const commander = useRef<MotionCommander | null>(null)
  const canDriveRef = useRef(false)

  const load = useCallback(async () => {
    if (!api) return
    try {
      const [nextRobot, nextMaps, nextDeployments] = await Promise.all([api.robot(robotId), api.maps(), api.deployments({ robot_id: robotId, state: "installed", limit: "200" })])
      setRobot(nextRobot); setMaps(nextMaps); setDeployments(nextDeployments.items); setLoadError("")
      setSelectedDeployment((current) => current || nextDeployments.items[0]?.id || "")
    } catch (cause) {
      setLoadError(cause instanceof Error ? cause.message : "车辆状态读取失败")
    }
  }, [api, robotId])

  useEffect(() => {
    void load()
    const interval = window.setInterval(() => api?.robot(robotId).then(setRobot).catch(() => undefined), 2500)
    return () => window.clearInterval(interval)
  }, [api, robotId, load])

  const setPanel = (panel: PanelName, state: PanelState) => setPanelState((current) => ({ ...current, [panel]: state }))

  const perform = async (path: string, payload: Record<string, unknown> = {}, task?: () => Promise<unknown>) => {
    if (!api) return undefined
    const meta = labels[path]
    setPanel(meta.panel, { tone: "info", title: meta.pending, detail: "请稍候，不要重复操作" })
    const result = await actions.run(path, task || (() => api.robotAction(robotId, path, payload)), { success: meta.success, error: `${meta.success.replace("已下发", "失败")}` })
    if (result !== undefined) {
      setPanel(meta.panel, { tone: "success", title: meta.success, detail: "正在等待车辆实时状态同步" })
      await load()
    } else {
      setPanel(meta.panel, { tone: "error", title: "操作未完成", detail: actions.errors[path] || "请检查车辆连接后重试" })
    }
    return result
  }

  useEffect(() => {
    if (!api) return
    const value = new MotionCommander(
      (payload) => api.robotAction(robotId, "control/manual", payload as unknown as Record<string, unknown>),
      () => api.robotAction(robotId, "control/stop"),
      150,
      (cause) => {
        setActiveDirection(null)
        const message = cause instanceof Error ? cause.message : "底盘指令发送失败"
        setPanel("control", { tone: "error", title: "底盘控制中断", detail: message })
        notifyRef.current({ id: "motion-error", title: "底盘控制中断", description: message, tone: "error" })
      },
    )
    commander.current = value
    return () => { value.dispose(); commander.current = null }
  }, [api, robotId])

  const startMotion = useCallback((direction: Direction) => {
    if (!canDriveRef.current) return
    const unit = motion[direction]
    setActiveDirection(direction)
    setPanel("control", { tone: "info", title: `正在${directionName(direction)}`, detail: `速度 ${speed.toFixed(2)} m/s，释放后立即停车` })
    commander.current?.start({ linear_x: unit.linear_x * speed, linear_y: 0, angular_z: unit.angular_z * speed, ttl_ms: 500, source: "web-console" })
  }, [speed])
  const releaseMotion = useCallback(() => {
    setActiveDirection(null)
    commander.current?.release()
  }, [])
  const forceStopMotion = useCallback(() => {
    setActiveDirection(null)
    commander.current?.forceStop()
    setPanel("control", { tone: "success", title: "停车指令已发送", detail: "底盘控制已释放" })
  }, [])

  useEffect(() => {
    const keyDirection: Record<string, Direction> = { w: "forward", s: "backward", a: "left", d: "right" }
    const down = (event: KeyboardEvent) => {
      if (event.repeat || event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement) return
      const direction = keyDirection[event.key.toLowerCase()]
      if (direction) { event.preventDefault(); startMotion(direction) }
    }
    const up = (event: KeyboardEvent) => { if (keyDirection[event.key.toLowerCase()]) releaseMotion() }
    const hidden = () => { if (document.hidden) releaseMotion() }
    window.addEventListener("keydown", down); window.addEventListener("keyup", up); window.addEventListener("blur", releaseMotion); document.addEventListener("visibilitychange", hidden)
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); window.removeEventListener("blur", releaseMotion); document.removeEventListener("visibilitychange", hidden) }
  }, [startMotion, releaseMotion])

  if (!robot && !loadError) return <LoadingBlock label="正在读取车辆状态" />
  if (!robot) return <EmptyState title="车辆状态读取失败" detail={loadError} action={<Button onClick={() => void load()}>重新加载</Button>} />

  const live = statuses[robot.id]
  const currentRobot = live ? { ...robot, online: Boolean(live.online), runtime_status: (live.status as Record<string, unknown>) || robot.runtime_status, last_seen: String(live.last_seen || robot.last_seen || "") || null } : robot
  const runtime = currentRobot.runtime_status as { mode?: string; processes?: Record<string, string>; navigation?: Record<string, unknown>; patrol?: Record<string, unknown>; video?: { streaming?: boolean; device?: string }; last_error?: string } | null
  const navigation = runtime?.navigation || {}
  const patrol = runtime?.patrol || {}
  const mode = runtime?.mode || "UNKNOWN"
  const deployment = deployments.find((item) => item.id === selectedDeployment)
  const selectedMap = maps.find((item) => item.id === deployment?.map_id)
  const installedName = deployment?.installed_name || ""
  const navigationPayload = selectedPoint && installedName ? { map_name: installedName, x: selectedPoint.x, y: selectedPoint.y, yaw: degreesToRadians(yaw) } : null
  const controllable = currentRobot.enabled && currentRobot.online && mode !== "EMERGENCY_STOP"
  canDriveRef.current = controllable
  const mappingActive = mode === "MAPPING" || mode === "SAVING_MAP" || runtime?.processes?.slam === "running"
  const navReady = Boolean(navigation.ready) || runtime?.processes?.nav2 === "running"
  const goalActive = Boolean(navigation.active_goal_id) || navigation.action_state === "active"
  const patrolState = String(patrol.state || "idle")
  const mappingBusy = ["mapping/start", "mapping/save"].some(actions.isPending)
  const navigationBusy = ["navigation/start", "navigation/initial-pose", "navigation/goal"].some(actions.isPending)
  const patrolBusy = ["patrol/start", "patrol/pause", "patrol/resume"].some(actions.isPending)
  const unavailableReason = !currentRobot.enabled ? "车辆已停用" : !currentRobot.online ? "车辆离线" : mode === "EMERGENCY_STOP" ? "车辆处于急停状态" : ""
  const cameraAvailable = currentRobot.enabled && currentRobot.online

  const confirmThen = async (input: { title: string; description: string; confirmLabel: string; tone?: "primary" | "danger" | "warning" }, callback: () => unknown | Promise<unknown>) => {
    if (await feedback.confirm(input)) await callback()
  }
  const saveMap = () => perform("mapping/save", {}, async () => {
    await api!.robotAction(robotId, "mapping/save", { name: mapName.trim() })
    await api!.importMap({ robot_id: robotId, map_name: mapName.trim() })
    return { ok: true }
  })
  const addPoint = () => { if (selectedPoint) setPatrolPoints((items) => [...items, selectedPoint]) }
  const moveMapPoint = (index: number, point: MapPoint) => {
    if (index < patrolPoints.length) {
      setPatrolPoints((items) => items.map((item, itemIndex) => itemIndex === index ? point : item))
    } else {
      setSelectedPoint(point)
    }
  }
  const movePoint = (index: number, delta: number) => setPatrolPoints((items) => { const next = [...items]; const target = index + delta; if (target < 0 || target >= next.length) return items; [next[index], next[target]] = [next[target], next[index]]; return next })
  const removePoint = (index: number) => setPatrolPoints((items) => items.filter((_, itemIndex) => itemIndex !== index))
  const startPatrol = () => confirmThen(
    { title: "启动巡护？", description: `车辆将按顺序访问 ${patrolPoints.length} 个点位${loop ? "并循环执行" : ""}。`, confirmLabel: "确认启动" },
    () => perform("patrol/start", {
      map_name: installedName,
      loop,
      points: patrolPoints.map((point, index) => ({ name: `point_${index + 1}`, x: point.x, y: point.y, yaw: degreesToRadians(yaw), dwell_s: dwell })),
    }),
  )

  return <div className="page-enter">
    <PageHeader eyebrow="ROBOT OPERATIONS" title={currentRobot.name} description={`${currentRobot.id} · ${currentRobot.base_url}`} actions={<><Link to="/robots"><Button>返回车队</Button></Link><Badge tone={currentRobot.online ? "success" : "danger"}><StatusDot online={currentRobot.online} />{currentRobot.online ? "在线" : "离线"}</Badge></>} />
    {unavailableReason && <div className="warning-banner">{unavailableReason}，运动和任务启动操作暂不可用，停车类动作仍可执行。</div>}
    {loadError && <InlineActionStatus tone="warning" title="部分状态刷新失败" detail={loadError} onRetry={() => void load()} />}
    <section className="robot-summary"><Card><span>运行模式</span><strong>{mode}</strong></Card><Card><span>雷达</span><strong>{runtime?.processes?.lidar || "unknown"}</strong></Card><Card><span>导航</span><strong>{String(navigation.action_state || "idle")}</strong></Card><Card><span>巡护</span><strong>{patrolState}</strong></Card></section>
    <section className="ops-grid">
      <Card className="panel control-panel">
        <div className="panel-head"><div><h2>实时底盘控制</h2><p>按住按钮或使用 W/A/S/D，释放立即停车</p></div><code>TTL 500ms</code></div>
        <label className="range-field"><span>速度 {speed.toFixed(2)} m/s</span><input type="range" min="0.05" max="0.4" step="0.01" value={speed} disabled={!controllable} onChange={(event) => setSpeed(Number(event.target.value))} /></label>
        <div className="drive-pad"><span /><DriveButton label="W" icon={<ArrowUp />} active={activeDirection === "forward"} disabled={!controllable} onStart={() => startMotion("forward")} onStop={releaseMotion} /><span /><DriveButton label="A" icon={<ArrowLeft />} active={activeDirection === "left"} disabled={!controllable} onStart={() => startMotion("left")} onStop={releaseMotion} /><Button variant="warning" className="drive-stop" onClick={forceStopMotion}><Square />停止</Button><DriveButton label="D" icon={<ArrowRight />} active={activeDirection === "right"} disabled={!controllable} onStart={() => startMotion("right")} onStop={releaseMotion} /><span /><DriveButton label="S" icon={<ArrowDown />} active={activeDirection === "backward"} disabled={!controllable} onStart={() => startMotion("backward")} onStop={releaseMotion} /><span /></div>
        <div className="safety-actions"><Button variant="danger" loading={actions.isPending("control/estop")} loadingText="正在急停" onClick={() => void perform("control/estop")}><AlertOctagon />急停</Button><Button loading={actions.isPending("control/clear-estop")} loadingText="正在解除" disabled={!currentRobot.online || mode !== "EMERGENCY_STOP"} onClick={() => void confirmThen({ title: "解除急停？", description: "解除后车辆将重新允许运动指令，请确认现场环境安全。", confirmLabel: "确认解除", tone: "warning" }, () => perform("control/clear-estop"))}><RotateCcw />解除急停</Button></div>
        {panelState.control ? <InlineActionStatus {...panelState.control} /> : unavailableReason && <InlineActionStatus tone="warning" title="底盘控制不可用" detail={unavailableReason} />}
      </Card>

      <Card className="panel mapping-panel">
        <div className="panel-head"><div><h2>建图工作流</h2><p>实时查看 SLAM 当前发布的 OccupancyGrid</p></div><MapPinned /></div>
        <label><span>地图名称</span><input className={fieldClass} value={mapName} aria-invalid={!mapName.trim()} onChange={(event) => setMapName(event.target.value)} /></label>
        <div className="stack-actions"><Button variant="primary" loading={actions.isPending("mapping/start")} loadingText="正在启动" disabled={!controllable || mappingActive || mappingBusy} onClick={() => void perform("mapping/start")}><Play />开始建图</Button><Button loading={actions.isPending("mapping/stop")} loadingText="正在停止" disabled={!currentRobot.online || !mappingActive} onClick={() => void perform("mapping/stop")}><Square />停止 SLAM</Button><Button variant="warning" loading={actions.isPending("mapping/save")} loadingText="正在保存" disabled={!controllable || !mappingActive || !mapName.trim() || mappingBusy} onClick={() => void saveMap()}><Save />保存并导入中心</Button></div>
        {panelState.mapping ? <InlineActionStatus {...panelState.mapping} /> : <InlineActionStatus tone={mappingActive ? "success" : "info"} title={mappingActive ? "SLAM 正在运行" : "等待开始建图"} detail={!mapName.trim() ? "请先填写地图名称" : unavailableReason || undefined} />}
        <LiveMapPreview api={api!} robotId={robotId} active={mappingActive} />
      </Card>

      <Card className="panel span-2 camera-panel">
        <div className="panel-head">
          <div><h2>车载摄像头</h2><p>打开后按固定频率抓取单帧，适合通过中心端远程查看</p></div>
          <Button variant={cameraActive ? "secondary" : "primary"} disabled={!cameraAvailable} onClick={() => setCameraActive((current) => !current)}>{cameraActive ? "关闭摄像头" : "打开摄像头"}</Button>
        </div>
        {!cameraAvailable && <InlineActionStatus tone="warning" title="摄像头暂不可用" detail={!currentRobot.enabled ? "车辆已停用，不能建立视频预览。" : "车辆离线，待重新上线后可打开摄像头。"} />}
        <CameraPreview api={api!} robotId={robotId} active={cameraActive} online={currentRobot.online} device={runtime?.video?.device ? String(runtime.video.device) : undefined} streaming={runtime?.video?.streaming} />
      </Card>

      <VisionDetectionCard api={api!} robotId={robotId} available={cameraAvailable} />

      <HazardPanel api={api!} robotId={robotId} available={cameraAvailable} map={selectedMap} />

      <Card className="panel span-2">
        <div className="panel-head"><div><h2>地图定位与导航</h2><p>只显示已安装到本车的不可变地图版本</p></div><Crosshair /></div>
        <div className="nav-toolbar"><select className={fieldClass} value={selectedDeployment} onChange={(event) => { setSelectedDeployment(event.target.value); setSelectedPoint(null); setPatrolPoints([]) }}><option value="">选择已安装地图</option>{deployments.map((item) => <option value={item.id} key={item.id}>{item.map.logical_name} v{item.map.version} · {item.installed_name}</option>)}</select><label>Yaw °<input className={fieldClass} type="number" min="0" max="360" value={yaw} onChange={(event) => setYaw(Number(event.target.value))} /></label><label>停留 s<input className={fieldClass} type="number" min="0" value={dwell} onChange={(event) => setDwell(Math.max(0, Number(event.target.value)))} /></label></div>
        {selectedMap ? <div className="nav-workspace"><MapCanvas className="navigation-map-canvas" fit="width" api={api!} map={selectedMap} onPoint={setSelectedPoint} onPointMove={moveMapPoint} onYawChange={setYaw} points={[...patrolPoints, ...(selectedPoint ? [selectedPoint] : [])]} /><div className="nav-side">
          <div className="selected-coordinate"><small>当前选点</small><strong>{selectedPoint ? `x ${selectedPoint.x.toFixed(2)} / y ${selectedPoint.y.toFixed(2)}` : "点击地图选择"}</strong><span>{installedName}</span></div>
          <div className="stack-actions"><Button variant="primary" loading={actions.isPending("navigation/start")} loadingText="正在启动" disabled={!controllable || !installedName || navReady || navigationBusy} onClick={() => void perform("navigation/start", { map_name: installedName })}><Play />启动 Nav2</Button><Button loading={actions.isPending("navigation/initial-pose")} loadingText="正在设置" disabled={!controllable || !navigationPayload || navigationBusy} onClick={() => navigationPayload && void perform("navigation/initial-pose", navigationPayload)}><Crosshair />设置初始位姿</Button><Button variant="primary" loading={actions.isPending("navigation/goal")} loadingText="正在下发" disabled={!controllable || !navigationPayload || !navReady || navigationBusy} onClick={() => navigationPayload && void confirmThen({ title: "发送导航目标？", description: `车辆将前往 x ${selectedPoint?.x.toFixed(2)} / y ${selectedPoint?.y.toFixed(2)}，请确认路径安全。`, confirmLabel: "发送并开始移动" }, () => perform("navigation/goal", navigationPayload))}><MapPinned />发送目标</Button><Button loading={actions.isPending("navigation/cancel")} loadingText="正在取消" disabled={!currentRobot.online || !goalActive} onClick={() => void perform("navigation/cancel")}><Ban />取消目标</Button><Button loading={actions.isPending("navigation/stop")} loadingText="正在停止" disabled={!currentRobot.online || !navReady} onClick={() => void perform("navigation/stop")}><Square />停止 Nav2</Button></div>
          {panelState.navigation ? <InlineActionStatus {...panelState.navigation} /> : <InlineActionStatus tone={navReady ? "success" : "info"} title={navReady ? "Nav2 已就绪" : "Nav2 尚未启动"} detail={!installedName ? "请先选择已安装地图" : !selectedPoint ? "启动 Nav2 不要求选点；设置位姿和目标前需要在地图选点" : unavailableReason || undefined} />}
          <hr />
          <div className="patrol-builder"><div><strong>临时巡护路线</strong><span>{patrolPoints.length} 个点</span></div><Button disabled={!selectedPoint} onClick={addPoint}><Waypoints />加入点位</Button><label className="switch-field"><input type="checkbox" checked={loop} onChange={(event) => setLoop(event.target.checked)} /><span>循环路线</span></label>
            {patrolPoints.length > 0 && <div className="patrol-point-list">{patrolPoints.map((point, index) => <div className="patrol-point" key={`${point.x}-${point.y}-${index}`}><b>{index + 1}</b><span>x {point.x.toFixed(2)} / y {point.y.toFixed(2)}</span><Button variant="ghost" size="sm" aria-label={`点位 ${index + 1} 上移`} disabled={index === 0} onClick={() => movePoint(index, -1)}><ChevronUp /></Button><Button variant="ghost" size="sm" aria-label={`点位 ${index + 1} 下移`} disabled={index === patrolPoints.length - 1} onClick={() => movePoint(index, 1)}><ChevronDown /></Button><Button variant="ghost" size="sm" aria-label={`删除点位 ${index + 1}`} onClick={() => removePoint(index)}><X /></Button></div>)}</div>}
            {patrolPoints.length > 0 && <Button variant="ghost" size="sm" onClick={() => setPatrolPoints([])}><Trash2 />清空路线</Button>}
            <div className="stack-actions"><Button variant="primary" loading={actions.isPending("patrol/start")} loadingText="正在启动" disabled={!controllable || !patrolPoints.length || !navReady || patrolBusy} onClick={() => void startPatrol()}><Play />启动巡护</Button><Button loading={actions.isPending("patrol/pause")} loadingText="正在暂停" disabled={!currentRobot.online || patrolState !== "running"} onClick={() => void perform("patrol/pause")}><Pause />暂停</Button><Button loading={actions.isPending("patrol/resume")} loadingText="正在恢复" disabled={!controllable || patrolState !== "paused"} onClick={() => void confirmThen({ title: "恢复巡护？", description: "车辆将恢复当前路线并可能立即开始移动。", confirmLabel: "确认恢复" }, () => perform("patrol/resume"))}><Play />恢复</Button><Button variant="warning" loading={actions.isPending("patrol/cancel")} loadingText="正在取消" disabled={!currentRobot.online || !["running", "paused"].includes(patrolState)} onClick={() => void perform("patrol/cancel")}><Ban />取消巡护</Button></div>
            {panelState.patrol ? <InlineActionStatus {...panelState.patrol} /> : <InlineActionStatus tone={patrolState === "running" ? "success" : "info"} title={`巡护状态：${patrolState}`} detail={!patrolPoints.length ? "从地图选点并加入路线后可启动巡护" : unavailableReason || undefined} />}
          </div>
        </div></div> : <EmptyState title="没有可用地图" detail="先从地图资产页向本车分发地图" />}
      </Card>
      <Card className="panel span-2 diagnostic-panel"><details><summary><div><strong>高级诊断</strong><span>进程、ROS、导航与最后错误的原始状态</span></div><ChevronDown /></summary><JsonPanel value={currentRobot.runtime_status || { error: currentRobot.runtime_error }} /></details></Card>
    </section>
  </div>
}

function directionName(direction: Direction) { return direction === "forward" ? "前进" : direction === "backward" ? "后退" : direction === "left" ? "左转" : "右转" }

function DriveButton({ label, icon, active, disabled, onStart, onStop }: { label: string; icon: React.ReactNode; active: boolean; disabled: boolean; onStart(): void; onStop(): void }) {
  return <button className={`drive-button${active ? " is-active" : ""}`} disabled={disabled} aria-pressed={active} onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); onStart() }} onPointerUp={onStop} onPointerCancel={onStop} onLostPointerCapture={onStop}>{icon}<b>{label}</b></button>
}
