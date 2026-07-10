import { useEffect, useRef, useState } from "react"
import { AlertOctagon, ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Ban, Crosshair, MapPinned, Pause, Play, RotateCcw, Save, Square, Waypoints } from "lucide-react"
import { Link, useParams } from "react-router-dom"
import { useSession } from "../app/session"
import { useLiveStatus } from "../app/live-status"
import { degreesToRadians } from "../api/client"
import { MotionCommander } from "../api/motion"
import type { MapDeployment, MapPoint, Robot, StoredMap } from "../api/types"
import { MapCanvas } from "../components/map-canvas"
import { Badge, Button, Card, EmptyState, JsonPanel, LoadingBlock, PageHeader, StatusDot, fieldClass } from "../components/ui"

type Direction = "forward" | "backward" | "left" | "right"
const motion: Record<Direction, { linear_x: number; linear_y: number; angular_z: number }> = {
  forward: { linear_x: 1, linear_y: 0, angular_z: 0 }, backward: { linear_x: -1, linear_y: 0, angular_z: 0 },
  left: { linear_x: 0, linear_y: 0, angular_z: 3 }, right: { linear_x: 0, linear_y: 0, angular_z: -3 },
}

export function RobotDetailPage() {
  const { robotId = "" } = useParams()
  const { api } = useSession()
  const { statuses } = useLiveStatus()
  const [robot, setRobot] = useState<Robot | null>(null)
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
  const [busy, setBusy] = useState("")
  const commander = useRef<MotionCommander | null>(null)

  const load = async () => {
    if (!api) return
    const [nextRobot, nextMaps, nextDeployments] = await Promise.all([api.robot(robotId), api.maps(), api.deployments({ robot_id: robotId, state: "installed", limit: "200" })])
    setRobot(nextRobot); setMaps(nextMaps); setDeployments(nextDeployments.items)
    if (!selectedDeployment && nextDeployments.items[0]) setSelectedDeployment(nextDeployments.items[0].id)
  }
  useEffect(() => { load().catch(() => setRobot(null)); const interval = window.setInterval(() => api?.robot(robotId).then(setRobot).catch(() => undefined), 2500); return () => window.clearInterval(interval) }, [api, robotId])

  const action = async (path: string, payload: Record<string, unknown> = {}) => {
    if (!api || busy) return
    setBusy(path)
    try { const result = await api.robotAction(robotId, path, payload); await load(); return result }
    catch (error) { window.alert(error instanceof Error ? error.message : "操作失败") }
    finally { setBusy("") }
  }

  useEffect(() => {
    if (!api) return
    const value = new MotionCommander(
      (payload) => api.robotAction(robotId, "control/manual", payload as unknown as Record<string, unknown>),
      () => api.robotAction(robotId, "control/stop"),
    )
    commander.current = value
    return () => { value.stop(); commander.current = null }
  }, [api, robotId])

  const startMotion = (direction: Direction) => {
    const unit = motion[direction]
    commander.current?.start({ linear_x: unit.linear_x * speed, linear_y: 0, angular_z: unit.angular_z * speed, ttl_ms: 500, source: "web-console" })
  }
  const stopMotion = () => commander.current?.stop()
  useEffect(() => {
    const keyDirection: Record<string, Direction> = { w: "forward", s: "backward", a: "left", d: "right" }
    const down = (event: KeyboardEvent) => { if (event.repeat || event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return; const direction = keyDirection[event.key.toLowerCase()]; if (direction) { event.preventDefault(); startMotion(direction) } }
    const up = (event: KeyboardEvent) => { if (keyDirection[event.key.toLowerCase()]) stopMotion() }
    const hidden = () => { if (document.hidden) stopMotion() }
    window.addEventListener("keydown", down); window.addEventListener("keyup", up); window.addEventListener("blur", stopMotion); document.addEventListener("visibilitychange", hidden)
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); window.removeEventListener("blur", stopMotion); document.removeEventListener("visibilitychange", hidden); stopMotion() }
  }, [api, robotId, speed])

  if (!robot) return <LoadingBlock label="正在读取车辆状态" />
  const live = statuses[robot.id]
  const currentRobot = live ? { ...robot, online: Boolean(live.online), runtime_status: (live.status as Record<string, unknown>) || robot.runtime_status, last_seen: String(live.last_seen || robot.last_seen || "") || null } : robot
  const runtime = currentRobot.runtime_status as { mode?: string; processes?: Record<string, string>; navigation?: Record<string, unknown>; patrol?: Record<string, unknown>; ros?: Record<string, unknown>; last_error?: string } | null
  const deployment = deployments.find((item) => item.id === selectedDeployment)
  const selectedMap = maps.find((item) => item.id === deployment?.map_id)
  const installedName = deployment?.installed_name || ""
  const navigationPayload = selectedPoint && installedName ? { map_name: installedName, x: selectedPoint.x, y: selectedPoint.y, yaw: degreesToRadians(yaw) } : null

  return <div className="page-enter">
    <PageHeader eyebrow="ROBOT OPERATIONS" title={currentRobot.name} description={`${currentRobot.id} · ${currentRobot.base_url}`} actions={<><Link to="/robots"><Button>返回车队</Button></Link><Badge tone={currentRobot.online ? "success" : "danger"}><StatusDot online={currentRobot.online} />{currentRobot.online ? "在线" : "离线"}</Badge></>} />
    {!currentRobot.enabled && <div className="warning-banner">该车辆已停用，中心不会向其下发控制命令。</div>}
    <section className="robot-summary"><Card><span>运行模式</span><strong>{runtime?.mode || "UNKNOWN"}</strong></Card><Card><span>雷达</span><strong>{runtime?.processes?.lidar || "unknown"}</strong></Card><Card><span>导航</span><strong>{String(runtime?.navigation?.action_state || "idle")}</strong></Card><Card><span>巡护</span><strong>{String(runtime?.patrol?.state || "idle")}</strong></Card></section>
    <section className="ops-grid">
      <Card className="panel control-panel"><div className="panel-head"><div><h2>实时底盘控制</h2><p>按住按钮或使用 W/A/S/D，释放立即停车</p></div><code>TTL 500ms</code></div><label className="range-field"><span>速度 {speed.toFixed(2)} m/s</span><input type="range" min="0.05" max="0.4" step="0.01" value={speed} onChange={(event) => setSpeed(Number(event.target.value))} /></label><div className="drive-pad"><span /><DriveButton label="W" icon={<ArrowUp />} onStart={() => startMotion("forward")} onStop={stopMotion} /><span /><DriveButton label="A" icon={<ArrowLeft />} onStart={() => startMotion("left")} onStop={stopMotion} /><Button variant="warning" className="drive-stop" onClick={stopMotion}><Square />停止</Button><DriveButton label="D" icon={<ArrowRight />} onStart={() => startMotion("right")} onStop={stopMotion} /><span /><DriveButton label="S" icon={<ArrowDown />} onStart={() => startMotion("backward")} onStop={stopMotion} /><span /></div><div className="safety-actions"><Button variant="danger" onClick={() => action("control/estop")}><AlertOctagon />急停</Button><Button onClick={() => action("control/clear-estop")}><RotateCcw />解除急停</Button></div></Card>
      <Card className="panel"><div className="panel-head"><div><h2>建图工作流</h2><p>保存后可立即拉入中心地图资产</p></div><MapPinned /></div><label><span>地图名称</span><input className={fieldClass} value={mapName} onChange={(event) => setMapName(event.target.value)} /></label><div className="stack-actions"><Button variant="primary" onClick={() => action("mapping/start")}><Play />开始建图</Button><Button onClick={() => action("mapping/stop")}><Square />停止 SLAM</Button><Button variant="warning" onClick={async () => { const saved = await action("mapping/save", { name: mapName }); if (saved && api) { await api.importMap({ robot_id: robotId, map_name: mapName }); await load() } }}><Save />保存并导入中心</Button></div></Card>
      <Card className="panel span-2"><div className="panel-head"><div><h2>地图定位与导航</h2><p>只显示已安装到本车的不可变地图版本</p></div><Crosshair /></div><div className="nav-toolbar"><select className={fieldClass} value={selectedDeployment} onChange={(event) => { setSelectedDeployment(event.target.value); setSelectedPoint(null); setPatrolPoints([]) }}><option value="">选择已安装地图</option>{deployments.map((item) => <option value={item.id} key={item.id}>{item.map.logical_name} v{item.map.version} · {item.installed_name}</option>)}</select><label>Yaw °<input className={fieldClass} type="number" value={yaw} onChange={(event) => setYaw(Number(event.target.value))} /></label><label>停留 s<input className={fieldClass} type="number" min="0" value={dwell} onChange={(event) => setDwell(Number(event.target.value))} /></label></div>{selectedMap ? <div className="nav-workspace"><MapCanvas api={api!} map={selectedMap} onPoint={setSelectedPoint} points={[...patrolPoints, ...(selectedPoint ? [selectedPoint] : [])]} /><div className="nav-side"><div className="selected-coordinate"><small>当前选点</small><strong>{selectedPoint ? `x ${selectedPoint.x.toFixed(2)} / y ${selectedPoint.y.toFixed(2)}` : "点击地图选择"}</strong><span>{installedName}</span></div><div className="stack-actions"><Button variant="primary" disabled={!navigationPayload} onClick={() => action("navigation/start", { map_name: installedName })}><Play />启动 Nav2</Button><Button disabled={!navigationPayload} onClick={() => navigationPayload && action("navigation/initial-pose", navigationPayload)}><Crosshair />设置初始位姿</Button><Button variant="primary" disabled={!navigationPayload} onClick={() => navigationPayload && action("navigation/goal", navigationPayload)}><MapPinned />发送目标</Button><Button onClick={() => action("navigation/cancel")}><Ban />取消目标</Button><Button onClick={() => action("navigation/stop")}><Square />停止 Nav2</Button></div><hr /><div className="patrol-builder"><div><strong>临时巡护路线</strong><span>{patrolPoints.length} 个点</span></div><Button disabled={!selectedPoint} onClick={() => selectedPoint && setPatrolPoints((items) => [...items, selectedPoint])}><Waypoints />加入点位</Button><label className="switch-field"><input type="checkbox" checked={loop} onChange={(event) => setLoop(event.target.checked)} /><span>循环路线</span></label><div className="stack-actions"><Button variant="primary" disabled={!patrolPoints.length} onClick={() => action("patrol/start", { map_name: installedName, loop, points: patrolPoints.map((point, index) => ({ name: `point_${index + 1}`, x: point.x, y: point.y, yaw: degreesToRadians(yaw), dwell_s: dwell })) })}><Play />启动巡护</Button><Button onClick={() => action("patrol/pause")}><Pause />暂停</Button><Button onClick={() => action("patrol/resume")}><Play />恢复</Button><Button variant="warning" onClick={() => action("patrol/cancel")}><Ban />取消巡护</Button></div></div></div></div> : <EmptyState title="没有可用地图" detail="先从地图资产页向本车分发地图" />}</Card>
      <Card className="panel span-2"><div className="panel-head"><div><h2>诊断快照</h2><p>进程、ROS、导航与最后错误的原始状态</p></div></div><JsonPanel value={currentRobot.runtime_status || { error: currentRobot.runtime_error }} /></Card>
    </section>
  </div>
}

function DriveButton({ label, icon, onStart, onStop }: { label: string; icon: React.ReactNode; onStart(): void; onStop(): void }) {
  return <button className="drive-button" onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); onStart() }} onPointerUp={onStop} onPointerCancel={onStop} onLostPointerCapture={onStop}>{icon}<b>{label}</b></button>
}
