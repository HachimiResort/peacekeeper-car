import { useEffect, useState, type FormEvent } from "react"
import { CloudDownload, Map, RadioTower, Send, Upload } from "lucide-react"
import { Link } from "react-router-dom"
import { useSession } from "../app/session"
import type { Robot, StoredMap } from "../api/types"
import { MapCanvas } from "../components/map-canvas"
import { Badge, Button, Card, EmptyState, LoadingBlock, Modal, PageHeader, fieldClass } from "../components/ui"

export function MapsPage() {
  const { api } = useSession()
  const [maps, setMaps] = useState<StoredMap[] | null>(null)
  const [robots, setRobots] = useState<Robot[]>([])
  const [dialog, setDialog] = useState<"upload" | "import" | "dispatch" | null>(null)
  const [selected, setSelected] = useState<StoredMap | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [logicalName, setLogicalName] = useState("")
  const [robotId, setRobotId] = useState("")
  const [vehicleMapName, setVehicleMapName] = useState("")
  const [targets, setTargets] = useState<string[]>([])
  const load = () => api && Promise.all([api.maps(), api.robots()]).then(([nextMaps, nextRobots]) => { setMaps(nextMaps); setRobots(nextRobots) })
  useEffect(() => { void load() }, [api])
  const openDispatch = (map: StoredMap) => { setSelected(map); setTargets([]); setDialog("dispatch") }

  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (!api) return
    try {
      if (dialog === "upload" && file) await api.uploadMap(file, logicalName || undefined)
      if (dialog === "import") await api.importMap({ robot_id: robotId, map_name: vehicleMapName, logical_name: logicalName || undefined })
      if (dialog === "dispatch" && selected) await api.dispatchMap(selected.id, targets)
      setDialog(null); await load()
    } catch (error) { window.alert(error instanceof Error ? error.message : "地图操作失败") }
  }

  return <div className="page-enter"><PageHeader eyebrow="MAP ASSETS" title="地图资产" description="中心保存不可变地图版本，分发只安装文件，不会自动启动 Nav2。" actions={<><Button onClick={() => setDialog("import")}><RadioTower size={17} />从车辆拉取</Button><Button variant="primary" onClick={() => setDialog("upload")}><Upload size={17} />上传地图包</Button></>} />
    {!maps ? <LoadingBlock /> : maps.length === 0 ? <EmptyState title="中心还没有地图" detail="从车辆拉取已保存地图，或上传标准 ZIP bundle" /> : <section className="map-grid">{maps.map((map) => <Card className="map-card" key={map.id}><MapCanvas api={api!} map={map} interactive={false} /><div className="map-card-body"><div><Badge tone="success">v{map.version}</Badge><span>{map.resolution} m/px</span></div><h2>{map.logical_name}</h2><p>{map.width} × {map.height} · 来源 {map.source_robot_id || "手工上传"}</p><div className="map-card-actions"><Link to={`/maps/${map.id}`}><Button><Map size={16} />查看详情</Button></Link><Button variant="primary" onClick={() => openDispatch(map)}><Send size={16} />分发</Button></div></div></Card>)}</section>}
    <Modal open={dialog === "upload"} title="上传地图 Bundle" onClose={() => setDialog(null)} footer={<><Button onClick={() => setDialog(null)}>取消</Button><Button variant="primary" type="submit" form="map-operation" disabled={!file}>上传</Button></>}><form id="map-operation" onSubmit={submit} className="form-stack"><label><span>ZIP 文件</span><input className={fieldClass} type="file" accept=".zip,application/zip" onChange={(event) => setFile(event.target.files?.[0] || null)} required /></label><label><span>逻辑名称（可选覆盖）</span><input className={fieldClass} value={logicalName} onChange={(event) => setLogicalName(event.target.value)} /></label></form></Modal>
    <Modal open={dialog === "import"} title="从车辆拉取地图" onClose={() => setDialog(null)} footer={<><Button onClick={() => setDialog(null)}>取消</Button><Button variant="primary" type="submit" form="map-import">开始拉取</Button></>}><form id="map-import" onSubmit={submit} className="form-stack"><label><span>来源车辆</span><select className={fieldClass} value={robotId} onChange={(event) => setRobotId(event.target.value)} required><option value="">选择在线车辆</option>{robots.filter((item) => item.enabled).map((robot) => <option value={robot.id} key={robot.id}>{robot.name} · {robot.id}</option>)}</select></label><label><span>车端地图名</span><input className={fieldClass} value={vehicleMapName} onChange={(event) => setVehicleMapName(event.target.value)} required placeholder="lab_first_map" /></label><label><span>中心逻辑名称（可选）</span><input className={fieldClass} value={logicalName} onChange={(event) => setLogicalName(event.target.value)} /></label></form></Modal>
    <Modal open={dialog === "dispatch"} title={`分发 ${selected?.logical_name || "地图"}`} onClose={() => setDialog(null)} footer={<><Button onClick={() => setDialog(null)}>取消</Button><Button variant="primary" type="submit" form="map-dispatch" disabled={!targets.length}><CloudDownload size={16} />安装到 {targets.length} 辆车</Button></>}><form id="map-dispatch" onSubmit={submit} className="target-list">{robots.filter((item) => item.enabled).map((robot) => <label key={robot.id}><input type="checkbox" checked={targets.includes(robot.id)} onChange={(event) => setTargets((items) => event.target.checked ? [...items, robot.id] : items.filter((id) => id !== robot.id))} /><span><strong>{robot.name}</strong><small>{robot.id} · {robot.online ? "在线" : "离线"}</small></span></label>)}</form></Modal>
  </div>
}
