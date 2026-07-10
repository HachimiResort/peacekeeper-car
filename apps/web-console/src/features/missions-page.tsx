import { useEffect, useState } from "react"
import { Search } from "lucide-react"
import { useSession } from "../app/session"
import type { Mission, Robot } from "../api/types"
import { Badge, Button, Card, EmptyState, JsonPanel, LoadingBlock, Modal, PageHeader, fieldClass } from "../components/ui"

export function MissionsPage() {
  const { api } = useSession()
  const [missions, setMissions] = useState<Mission[] | null>(null)
  const [robots, setRobots] = useState<Robot[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [filters, setFilters] = useState({ robot_id: "", state: "", mission_type: "" })
  const [selected, setSelected] = useState<Mission | null>(null)
  const load = () => api && Promise.all([api.missions({ ...filters, limit: "20", offset: String(page * 20) }), api.robots()]).then(([result, fleet]) => { setMissions(result.items); setTotal(result.total); setRobots(fleet) })
  useEffect(() => { void load() }, [api, page, filters.robot_id, filters.state, filters.mission_type])
  return <div className="page-enter"><PageHeader eyebrow="MISSION LEDGER" title="任务记录" description="导航、巡护、建图和地图分发均形成可追溯任务记录。" />
    <Card className="filter-bar"><Search size={18} /><select className={fieldClass} value={filters.robot_id} onChange={(event) => { setPage(0); setFilters({ ...filters, robot_id: event.target.value }) }}><option value="">全部车辆</option>{robots.map((robot) => <option value={robot.id} key={robot.id}>{robot.name}</option>)}</select><select className={fieldClass} value={filters.state} onChange={(event) => { setPage(0); setFilters({ ...filters, state: event.target.value }) }}><option value="">全部状态</option><option value="running">running</option><option value="completed">completed</option><option value="failed">failed</option></select><input className={fieldClass} placeholder="任务类型" value={filters.mission_type} onChange={(event) => { setPage(0); setFilters({ ...filters, mission_type: event.target.value }) }} /><span>共 {total} 条</span></Card>
    {!missions ? <LoadingBlock /> : missions.length === 0 ? <EmptyState title="没有匹配任务" detail="调整筛选条件后重试" /> : <Card className="table-card"><div className="table-scroll"><table><thead><tr><th>任务类型</th><th>车辆</th><th>状态</th><th>创建时间</th><th>耗时</th><th>错误</th></tr></thead><tbody>{missions.map((mission) => <tr key={mission.id} className="clickable-row" onClick={() => setSelected(mission)}><td><strong>{mission.mission_type}</strong><small className="block-muted">{mission.id.slice(0, 8)}</small></td><td>{mission.robot_id || "多车/中心"}</td><td><Badge tone={mission.state === "failed" ? "danger" : mission.state === "running" ? "info" : "success"}>{mission.state}</Badge></td><td>{formatDate(mission.created_at)}</td><td>{duration(mission.started_at, mission.finished_at)}</td><td className="error-cell">{mission.error || "-"}</td></tr>)}</tbody></table></div><div className="pagination"><Button disabled={page === 0} onClick={() => setPage((value) => value - 1)}>上一页</Button><span>第 {page + 1} 页</span><Button disabled={(page + 1) * 20 >= total} onClick={() => setPage((value) => value + 1)}>下一页</Button></div></Card>}
    <Modal open={Boolean(selected)} title={selected?.mission_type || "任务详情"} onClose={() => setSelected(null)}><div className="detail-stack"><div className="detail-row"><span>任务 ID</span><code>{selected?.id}</code></div><div className="detail-row"><span>状态</span><strong>{selected?.state}</strong></div><h3>请求</h3><JsonPanel value={selected?.request} /><h3>结果</h3><JsonPanel value={selected?.result || { error: selected?.error }} /></div></Modal>
  </div>
}
function formatDate(value: string | null) { return value ? new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "medium" }).format(new Date(value)) : "-" }
function duration(start: string | null, end: string | null) { if (!start) return "-"; const seconds = Math.max(0, (new Date(end || Date.now()).getTime() - new Date(start).getTime()) / 1000); return `${seconds.toFixed(1)}s` }
