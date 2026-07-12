import { useEffect, useState } from "react"
import { Download, Send } from "lucide-react"
import { Link, useParams } from "react-router-dom"
import { useSession } from "../app/session"
import { useFeedback } from "../app/feedback"
import { downloadBlob } from "../api/client"
import type { MapDeployment, StoredMap } from "../api/types"
import { MapCanvas } from "../components/map-canvas"
import { Badge, Button, Card, EmptyState, LoadingBlock, PageHeader } from "../components/ui"

export function MapDetailPage() {
  const { mapId = "" } = useParams()
  const { api } = useSession()
  const feedback = useFeedback()
  const [map, setMap] = useState<StoredMap | null>(null)
  const [deployments, setDeployments] = useState<MapDeployment[]>([])
  const [error, setError] = useState("")
  const [downloading, setDownloading] = useState(false)
  const load = async () => { if (!api) return; try { const [value, list] = await Promise.all([api.map(mapId), api.deployments({ map_id: mapId, limit: "200" })]); setMap(value); setDeployments(list.items); setError("") } catch (cause) { setError(cause instanceof Error ? cause.message : "地图详情读取失败") } }
  useEffect(() => { void load() }, [api, mapId])
  if (!map || !api) return error ? <EmptyState title="地图详情读取失败" detail={error} action={<Button onClick={() => void load()}>重新加载</Button>} /> : <LoadingBlock />
  const download = async () => {
    if (downloading) return
    setDownloading(true)
    try { downloadBlob(await api.mapDownload(map.id), `${map.logical_name}__v${map.version}.zip`); feedback.notify({ title: "地图 Bundle 已开始下载", description: `${map.logical_name} v${map.version}`, tone: "success" }) }
    catch (cause) { feedback.notify({ title: "地图下载失败", description: cause instanceof Error ? cause.message : "请稍后重试", tone: "error" }) }
    finally { setDownloading(false) }
  }
  return <div className="page-enter"><PageHeader eyebrow="MAP VERSION" title={`${map.logical_name} v${map.version}`} description="该版本内容不可修改；变化后的地图会生成新版本。" actions={<><Link to="/maps"><Button>返回地图</Button></Link><Button variant="primary" onClick={download} loading={downloading} loadingText="正在准备"><Download size={17} />下载 Bundle</Button></>} />
    <section className="map-detail-grid"><Card className="panel map-detail-preview"><MapCanvas api={api} map={map} interactive={false} /></Card><Card className="panel metadata-panel"><h2>地图元数据</h2><dl><div><dt>分辨率</dt><dd>{map.resolution} m/px</dd></div><div><dt>画布</dt><dd>{map.width} × {map.height}</dd></div><div><dt>原点</dt><dd>{map.origin.map((value) => value.toFixed(2)).join(", ")}</dd></div><div><dt>来源车辆</dt><dd>{map.source_robot_id || "手工上传"}</dd></div><div><dt>Bundle SHA-256</dt><dd><code>{map.bundle_sha256}</code></dd></div></dl></Card></section>
    <Card className="table-card"><div className="table-summary"><div><Send /><strong>安装记录</strong></div><span>{deployments.length} 次分发</span></div>{deployments.length ? <div className="table-scroll"><table><thead><tr><th>目标车辆</th><th>状态</th><th>安装名称</th><th>开始时间</th><th>错误</th></tr></thead><tbody>{deployments.map((item) => <tr key={item.id}><td><Link className="text-link" to={`/robots/${item.robot_id}`}>{item.robot.name}</Link><small className="block-muted">{item.robot_id}</small></td><td><Badge tone={item.state === "installed" ? "success" : item.state === "failed" ? "danger" : "warning"}>{item.state}</Badge></td><td><code>{item.installed_name || "-"}</code></td><td>{formatDate(item.started_at)}</td><td>{item.error || "-"}</td></tr>)}</tbody></table></div> : <EmptyState title="尚未分发" detail="从地图资产页选择目标车辆进行安装" />}</Card>
  </div>
}
function formatDate(value: string | null) { return value ? new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" }).format(new Date(value)) : "-" }
