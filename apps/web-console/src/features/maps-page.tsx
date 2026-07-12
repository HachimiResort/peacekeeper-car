import { useEffect, useRef, useState, type FormEvent } from "react"
import { CloudDownload, Eye, Map, RadioTower, Send, Upload } from "lucide-react"
import { Link } from "react-router-dom"
import { useSession } from "../app/session"
import type { Robot, StoredMap, VehicleSavedMap } from "../api/types"
import { MapCanvas } from "../components/map-canvas"
import { Badge, Button, Card, EmptyState, LoadingBlock, Modal, PageHeader, fieldClass } from "../components/ui"

export function MapsPage() {
  const { api } = useSession()
  const [maps, setMaps] = useState<StoredMap[] | null>(null)
  const [robots, setRobots] = useState<Robot[]>([])
  const [dialog, setDialog] = useState<"upload" | "vehicle" | "dispatch" | null>(null)
  const [selected, setSelected] = useState<StoredMap | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [logicalName, setLogicalName] = useState("")
  const [robotId, setRobotId] = useState("")
  const [vehicleMaps, setVehicleMaps] = useState<VehicleSavedMap[]>([])
  const [vehicleLoading, setVehicleLoading] = useState(false)
  const [vehiclePreviewUrl, setVehiclePreviewUrl] = useState("")
  const [vehiclePreviewName, setVehiclePreviewName] = useState("")
  const [importingMap, setImportingMap] = useState("")
  const [targets, setTargets] = useState<string[]>([])
  const previewUrlRef = useRef("")
  const load = () => api && Promise.all([api.maps(), api.robots()]).then(([nextMaps, nextRobots]) => { setMaps(nextMaps); setRobots(nextRobots) })
  useEffect(() => { void load() }, [api])
  const openDispatch = (map: StoredMap) => { setSelected(map); setTargets([]); setDialog("dispatch") }
  const clearVehiclePreview = () => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
    previewUrlRef.current = ""
    setVehiclePreviewUrl("")
    setVehiclePreviewName("")
  }
  useEffect(() => () => clearVehiclePreview(), [])
  const openVehicleMaps = () => { clearVehiclePreview(); setRobotId(""); setVehicleMaps([]); setDialog("vehicle") }
  const loadVehicleMaps = async () => {
    if (!api || !robotId) return
    setVehicleLoading(true); clearVehiclePreview()
    try { setVehicleMaps(await api.vehicleMaps(robotId)) }
    catch (error) { window.alert(error instanceof Error ? error.message : "无法读取车辆地图") }
    finally { setVehicleLoading(false) }
  }
  const showVehicleMap = async (map: VehicleSavedMap) => {
    if (!api || !robotId) return
    try {
      const blob = await api.vehicleMapPreview(robotId, map.name)
      const nextUrl = URL.createObjectURL(blob)
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
      previewUrlRef.current = nextUrl
      setVehiclePreviewUrl(nextUrl); setVehiclePreviewName(map.name)
    } catch (error) { window.alert(error instanceof Error ? error.message : "无法读取地图预览") }
  }
  const importVehicleMap = async (map: VehicleSavedMap) => {
    if (!api || !robotId || importingMap) return
    setImportingMap(map.name)
    try {
      await api.importMap({ robot_id: robotId, map_name: map.name, logical_name: logicalName || undefined })
      clearVehiclePreview(); setDialog(null); await load()
    } catch (error) { window.alert(error instanceof Error ? error.message : "地图导入失败") }
    finally { setImportingMap("") }
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (!api) return
    try {
      if (dialog === "upload" && file) await api.uploadMap(file, logicalName || undefined)
      if (dialog === "dispatch" && selected) await api.dispatchMap(selected.id, targets)
      setDialog(null); await load()
    } catch (error) { window.alert(error instanceof Error ? error.message : "地图操作失败") }
  }

  return <div className="page-enter"><PageHeader eyebrow="MAP ASSETS" title="地图资产" description="中心保存不可变地图版本，分发只安装文件，不会自动启动 Nav2。" actions={<><Button onClick={openVehicleMaps}><RadioTower size={17} />查看车辆地图</Button><Button variant="primary" onClick={() => setDialog("upload")}><Upload size={17} />上传地图包</Button></>} />
    {!maps ? <LoadingBlock /> : maps.length === 0 ? <EmptyState title="中心还没有地图" detail="从车辆拉取已保存地图，或上传标准 ZIP bundle" /> : <section className="map-grid">{maps.map((map) => <Card className="map-card" key={map.id}><MapCanvas api={api!} map={map} interactive={false} /><div className="map-card-body"><div><Badge tone="success">v{map.version}</Badge><span>{map.resolution} m/px</span></div><h2>{map.logical_name}</h2><p>{map.width} × {map.height} · 来源 {map.source_robot_id || "手工上传"}</p><div className="map-card-actions"><Link to={`/maps/${map.id}`}><Button><Map size={16} />查看详情</Button></Link><Button variant="primary" onClick={() => openDispatch(map)}><Send size={16} />分发</Button></div></div></Card>)}</section>}
    <Modal open={dialog === "upload"} title="上传地图 Bundle" onClose={() => setDialog(null)} footer={<><Button onClick={() => setDialog(null)}>取消</Button><Button variant="primary" type="submit" form="map-operation" disabled={!file}>上传</Button></>}><form id="map-operation" onSubmit={submit} className="form-stack"><label><span>ZIP 文件</span><input className={fieldClass} type="file" accept=".zip,application/zip" onChange={(event) => setFile(event.target.files?.[0] || null)} required /></label><label><span>逻辑名称（可选覆盖）</span><input className={fieldClass} value={logicalName} onChange={(event) => setLogicalName(event.target.value)} /></label></form></Modal>
    <Modal open={dialog === "vehicle"} title="车辆已保存地图" onClose={() => { clearVehiclePreview(); setDialog(null) }} footer={<Button onClick={() => { clearVehiclePreview(); setDialog(null) }}>关闭</Button>}><div className="form-stack"><label><span>来源车辆</span><select className={fieldClass} value={robotId} onChange={(event) => { setRobotId(event.target.value); setVehicleMaps([]); clearVehiclePreview() }}><option value="">选择车辆</option>{robots.filter((item) => item.enabled).map((robot) => <option value={robot.id} key={robot.id}>{robot.name} · {robot.id} · {robot.online ? "在线" : "离线"}</option>)}</select></label><div className="stack-actions"><Button disabled={!robotId || vehicleLoading} onClick={loadVehicleMaps}><RadioTower size={16} />{vehicleLoading ? "正在读取" : "读取保存记录"}</Button></div><label><span>导入到中心后的逻辑名称（可选）</span><input className={fieldClass} value={logicalName} onChange={(event) => setLogicalName(event.target.value)} placeholder="默认保留车端地图名" /></label>{vehicleMaps.length > 0 && <div className="vehicle-map-list">{vehicleMaps.map((map) => <article className="vehicle-map-item" key={map.name}><div><strong>{map.name}</strong><span>{formatVehicleMapDate(map.updated_at)} · {formatBytes(map.size_bytes)}</span></div><div><Button size="sm" onClick={() => showVehicleMap(map)}><Eye size={15} />查看</Button><Button size="sm" variant="primary" disabled={Boolean(importingMap)} onClick={() => importVehicleMap(map)}><CloudDownload size={15} />{importingMap === map.name ? "导入中" : "导入中心"}</Button></div></article>)}</div>}{robotId && !vehicleLoading && vehicleMaps.length === 0 && <EmptyState title="尚未读取到已保存地图" detail="点击“读取保存记录”；若仍为空，说明该车 data/maps 中没有完整 yaml/pgm 地图对。" />}{vehiclePreviewUrl && <section className="vehicle-map-preview"><div><strong>{vehiclePreviewName}</strong><span>车端原始地图预览</span></div><img src={vehiclePreviewUrl} alt={`${vehiclePreviewName} 预览`} /></section>}</div></Modal>
    <Modal open={dialog === "dispatch"} title={`分发 ${selected?.logical_name || "地图"}`} onClose={() => setDialog(null)} footer={<><Button onClick={() => setDialog(null)}>取消</Button><Button variant="primary" type="submit" form="map-dispatch" disabled={!targets.length}><CloudDownload size={16} />安装到 {targets.length} 辆车</Button></>}><form id="map-dispatch" onSubmit={submit} className="target-list">{robots.filter((item) => item.enabled).map((robot) => <label key={robot.id}><input type="checkbox" checked={targets.includes(robot.id)} onChange={(event) => setTargets((items) => event.target.checked ? [...items, robot.id] : items.filter((id) => id !== robot.id))} /><span><strong>{robot.name}</strong><small>{robot.id} · {robot.online ? "在线" : "离线"}</small></span></label>)}</form></Modal>
  </div>
}

function formatVehicleMapDate(value: number) { return new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" }).format(new Date(value * 1000)) }
function formatBytes(value: number) { return value < 1024 * 1024 ? `${Math.max(1, Math.round(value / 1024))} KB` : `${(value / 1024 / 1024).toFixed(1)} MB` }
