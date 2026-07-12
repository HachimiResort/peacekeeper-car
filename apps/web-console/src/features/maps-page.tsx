import { useEffect, useRef, useState, type FormEvent } from "react"
import { CloudDownload, Eye, Map, RadioTower, Send, Upload } from "lucide-react"
import { Link } from "react-router-dom"
import { useFeedback } from "../app/feedback"
import { useSession } from "../app/session"
import type { Robot, StoredMap, VehicleSavedMap } from "../api/types"
import { MapCanvas } from "../components/map-canvas"
import { Badge, Button, Card, EmptyState, InlineActionStatus, LoadingBlock, Modal, PageHeader, fieldClass } from "../components/ui"

export function MapsPage() {
  const { api } = useSession()
  const feedback = useFeedback()
  const [maps, setMaps] = useState<StoredMap[] | null>(null)
  const [robots, setRobots] = useState<Robot[]>([])
  const [loadError, setLoadError] = useState("")
  const [dialog, setDialog] = useState<"upload" | "vehicle" | "dispatch" | null>(null)
  const [selected, setSelected] = useState<StoredMap | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [logicalName, setLogicalName] = useState("")
  const [robotId, setRobotId] = useState("")
  const [vehicleMaps, setVehicleMaps] = useState<VehicleSavedMap[]>([])
  const [vehicleLoading, setVehicleLoading] = useState(false)
  const [vehicleLoaded, setVehicleLoaded] = useState(false)
  const [vehiclePreviewUrl, setVehiclePreviewUrl] = useState("")
  const [vehiclePreviewName, setVehiclePreviewName] = useState("")
  const [previewLoading, setPreviewLoading] = useState("")
  const [importingMap, setImportingMap] = useState("")
  const [targets, setTargets] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [operationError, setOperationError] = useState("")
  const previewUrlRef = useRef("")

  const load = async () => {
    if (!api) return
    try { const [nextMaps, nextRobots] = await Promise.all([api.maps(), api.robots()]); setMaps(nextMaps); setRobots(nextRobots); setLoadError("") }
    catch (cause) { setLoadError(cause instanceof Error ? cause.message : "地图资产读取失败") }
  }
  useEffect(() => { void load() }, [api])

  const clearVehiclePreview = () => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
    previewUrlRef.current = ""; setVehiclePreviewUrl(""); setVehiclePreviewName("")
  }
  useEffect(() => () => clearVehiclePreview(), [])

  const openDialog = (next: "upload" | "vehicle" | "dispatch") => { setOperationError(""); setDialog(next) }
  const openDispatch = (map: StoredMap) => { setSelected(map); setTargets([]); openDialog("dispatch") }
  const openVehicleMaps = () => { clearVehiclePreview(); setRobotId(""); setVehicleMaps([]); setVehicleLoaded(false); openDialog("vehicle") }
  const closeDialog = () => { if (submitting || vehicleLoading || importingMap) return; clearVehiclePreview(); setDialog(null); setOperationError("") }

  const loadVehicleMaps = async () => {
    if (!api || !robotId || vehicleLoading) return
    setVehicleLoading(true); setVehicleLoaded(false); setOperationError(""); clearVehiclePreview()
    try { const values = await api.vehicleMaps(robotId); setVehicleMaps(values); setVehicleLoaded(true); feedback.notify({ title: "车辆地图读取完成", description: `${values.length} 个已保存地图`, tone: "success" }) }
    catch (cause) { const message = errorMessage(cause, "无法读取车辆地图"); setOperationError(message); feedback.notify({ title: "车辆地图读取失败", description: message, tone: "error" }) }
    finally { setVehicleLoading(false) }
  }

  const showVehicleMap = async (map: VehicleSavedMap) => {
    if (!api || !robotId || previewLoading) return
    setPreviewLoading(map.name); setOperationError("")
    try {
      const blob = await api.vehicleMapPreview(robotId, map.name)
      const nextUrl = URL.createObjectURL(blob)
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
      previewUrlRef.current = nextUrl; setVehiclePreviewUrl(nextUrl); setVehiclePreviewName(map.name)
    } catch (cause) { const message = errorMessage(cause, "无法读取地图预览"); setOperationError(message); feedback.notify({ title: "地图预览失败", description: message, tone: "error" }) }
    finally { setPreviewLoading("") }
  }

  const importVehicleMap = async (map: VehicleSavedMap) => {
    if (!api || !robotId || importingMap) return
    setImportingMap(map.name); setOperationError("")
    try {
      await api.importMap({ robot_id: robotId, map_name: map.name, logical_name: logicalName || undefined })
      feedback.notify({ title: "地图已导入中心", description: logicalName || map.name, tone: "success" })
      clearVehiclePreview(); setDialog(null); await load()
    } catch (cause) { const message = errorMessage(cause, "地图导入失败"); setOperationError(message); feedback.notify({ title: "地图导入失败", description: message, tone: "error" }) }
    finally { setImportingMap("") }
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!api || submitting) return
    setSubmitting(true); setOperationError("")
    try {
      if (dialog === "upload" && file) { await api.uploadMap(file, logicalName || undefined); feedback.notify({ title: "地图包上传完成", description: logicalName || file.name, tone: "success" }) }
      if (dialog === "dispatch" && selected) { await api.dispatchMap(selected.id, targets); feedback.notify({ title: "地图分发任务已创建", description: `${selected.logical_name} → ${targets.length} 辆车`, tone: "success" }) }
      setDialog(null); setFile(null); setLogicalName(""); await load()
    } catch (cause) { const message = errorMessage(cause, "地图操作失败"); setOperationError(message); feedback.notify({ title: "地图操作失败", description: message, tone: "error" }) }
    finally { setSubmitting(false) }
  }

  return <div className="page-enter">
    <PageHeader eyebrow="MAP ASSETS" title="地图资产" description="中心保存不可变地图版本，分发只安装文件，不会自动启动 Nav2。" actions={<><Button onClick={openVehicleMaps}><RadioTower size={17} />查看车辆地图</Button><Button variant="primary" onClick={() => openDialog("upload")}><Upload size={17} />上传地图包</Button></>} />
    {loadError && !maps ? <EmptyState title="地图资产读取失败" detail={loadError} action={<Button onClick={() => void load()}>重新加载</Button>} /> : !maps ? <LoadingBlock /> : <>{loadError && <InlineActionStatus tone="warning" title="地图列表刷新失败" detail={loadError} onRetry={() => void load()} />}{maps.length === 0 ? <EmptyState title="中心还没有地图" detail="从车辆拉取已保存地图，或上传标准 ZIP bundle" /> : <section className="map-grid">{maps.map((map) => <Card className="map-card" key={map.id}><MapCanvas api={api!} map={map} interactive={false} /><div className="map-card-body"><div><Badge tone="success">v{map.version}</Badge><span>{map.resolution} m/px</span></div><h2>{map.logical_name}</h2><p>{map.width} × {map.height} · 来源 {map.source_robot_id || "手工上传"}</p><div className="map-card-actions"><Link to={`/maps/${map.id}`}><Button><Map size={16} />查看详情</Button></Link><Button variant="primary" onClick={() => openDispatch(map)}><Send size={16} />分发</Button></div></div></Card>)}</section>}</>}

    <Modal open={dialog === "upload"} busy={submitting} title="上传地图 Bundle" onClose={closeDialog} footer={<><Button onClick={closeDialog} disabled={submitting}>取消</Button><Button variant="primary" type="submit" form="map-operation" disabled={!file} loading={submitting} loadingText="正在上传">上传</Button></>}><form id="map-operation" onSubmit={submit} className="form-stack"><label><span>ZIP 文件</span><input className={fieldClass} type="file" accept=".zip,application/zip" onChange={(event) => setFile(event.target.files?.[0] || null)} required /></label><label><span>逻辑名称（可选覆盖）</span><input className={fieldClass} value={logicalName} onChange={(event) => setLogicalName(event.target.value)} /></label>{operationError && <div className="form-error" role="alert">{operationError}</div>}</form></Modal>

    <Modal open={dialog === "vehicle"} busy={vehicleLoading || Boolean(importingMap)} title="车辆已保存地图" onClose={closeDialog} footer={<Button onClick={closeDialog} disabled={vehicleLoading || Boolean(importingMap)}>关闭</Button>}><div className="form-stack"><label><span>来源车辆</span><select className={fieldClass} value={robotId} onChange={(event) => { setRobotId(event.target.value); setVehicleMaps([]); setVehicleLoaded(false); setOperationError(""); clearVehiclePreview() }}><option value="">选择车辆</option>{robots.filter((item) => item.enabled).map((robot) => <option value={robot.id} key={robot.id}>{robot.name} · {robot.id} · {robot.online ? "在线" : "离线"}</option>)}</select></label><div className="stack-actions"><Button disabled={!robotId} loading={vehicleLoading} loadingText="正在读取" onClick={loadVehicleMaps}><RadioTower size={16} />读取保存记录</Button></div><label><span>导入到中心后的逻辑名称（可选）</span><input className={fieldClass} value={logicalName} onChange={(event) => setLogicalName(event.target.value)} placeholder="默认保留车端地图名" /></label>{operationError && <div className="form-error" role="alert">{operationError}</div>}{vehicleMaps.length > 0 && <div className="vehicle-map-list">{vehicleMaps.map((map) => <article className="vehicle-map-item" key={map.name}><div><strong>{map.name}</strong><span>{formatVehicleMapDate(map.updated_at)} · {formatBytes(map.size_bytes)}</span></div><div><Button size="sm" loading={previewLoading === map.name} loadingText="读取中" disabled={Boolean(previewLoading) || Boolean(importingMap)} onClick={() => void showVehicleMap(map)}><Eye size={15} />查看</Button><Button size="sm" variant="primary" loading={importingMap === map.name} loadingText="导入中" disabled={Boolean(importingMap)} onClick={() => void importVehicleMap(map)}><CloudDownload size={15} />导入中心</Button></div></article>)}</div>}{vehicleLoaded && vehicleMaps.length === 0 && <EmptyState title="没有已保存地图" detail="该车 data/maps 中没有完整 yaml/pgm 地图对" />}{vehiclePreviewUrl && <section className="vehicle-map-preview"><div><strong>{vehiclePreviewName}</strong><span>车端原始地图预览</span></div><img src={vehiclePreviewUrl} alt={`${vehiclePreviewName} 预览`} /></section>}</div></Modal>

    <Modal open={dialog === "dispatch"} busy={submitting} title={`分发 ${selected?.logical_name || "地图"}`} onClose={closeDialog} footer={<><Button onClick={closeDialog} disabled={submitting}>取消</Button><Button variant="primary" type="submit" form="map-dispatch" disabled={!targets.length} loading={submitting} loadingText="正在创建任务">安装到 {targets.length} 辆车</Button></>}><form id="map-dispatch" onSubmit={submit} className="target-list">{robots.filter((item) => item.enabled).map((robot) => <label key={robot.id}><input type="checkbox" checked={targets.includes(robot.id)} onChange={(event) => setTargets((items) => event.target.checked ? [...items, robot.id] : items.filter((id) => id !== robot.id))} /><span><strong>{robot.name}</strong><small>{robot.id} · {robot.online ? "在线" : "离线"}</small></span></label>)}{operationError && <div className="form-error" role="alert">{operationError}</div>}</form></Modal>
  </div>
}

function errorMessage(cause: unknown, fallback: string) { return cause instanceof Error ? cause.message : fallback }
function formatVehicleMapDate(value: number) { return new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" }).format(new Date(value * 1000)) }
function formatBytes(value: number) { return value < 1024 * 1024 ? `${Math.max(1, Math.round(value / 1024))} KB` : `${(value / 1024 / 1024).toFixed(1)} MB` }
