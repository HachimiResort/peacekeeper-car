import { useEffect, useState } from "react"
import { AlertTriangle, Bot, Map, Route, ShieldAlert } from "lucide-react"
import { Link } from "react-router-dom"
import { useSession } from "../app/session"
import { useLiveStatus } from "../app/live-status"
import type { AlertRecord, Mission, Overview, Robot, RobotEvent } from "../api/types"
import { Badge, Card, EmptyState, LoadingBlock, PageHeader, StatusDot } from "../components/ui"

export function DashboardPage() {
  const { api } = useSession()
  const { statuses } = useLiveStatus()
  const [data, setData] = useState<{ overview: Overview; robots: Robot[]; missions: Mission[]; events: RobotEvent[]; alerts: AlertRecord[] } | null>(null)
  const [error, setError] = useState("")
  useEffect(() => { if (!api) return; Promise.all([api.overview(), api.robots(), api.missions({ limit: "6" }), api.events({ limit: "6" }), api.alerts({ state: "pending", limit: "4" })]).then(([overview, robots, missions, events, alerts]) => setData({ overview, robots, missions: missions.items, events: events.items, alerts: alerts.items })).catch((reason) => setError(reason.message)) }, [api])

  if (error) return <EmptyState title="态势数据加载失败" detail={error} />
  if (!data) return <LoadingBlock label="正在汇聚车队态势" />
  const fleet = data.robots.map((robot) => mergeLive(robot, statuses[robot.id]))
  const onlineCount = fleet.filter((robot) => robot.online).length
  const cards = [
    { label: "在线车辆", value: `${onlineCount}/${data.overview.robots.enabled}`, detail: `${Math.max(data.overview.robots.enabled - onlineCount, 0)} 辆离线`, icon: Bot, tone: "green" },
    { label: "运行中任务", value: data.overview.missions.running, detail: `累计 ${data.overview.missions.total}`, icon: Route, tone: "blue" },
    { label: "待确认告警", value: data.overview.alerts.pending, detail: "需要人工确认", icon: ShieldAlert, tone: "amber" },
    { label: "中心地图", value: data.overview.maps.total, detail: "不可变版本资产", icon: Map, tone: "slate" },
  ]
  return (
    <div className="page-enter">
      <PageHeader eyebrow="COMMAND OVERVIEW" title="态势总览" description="车辆执行状态、任务流和风险事件的统一视图。" />
      <section className="metric-grid">{cards.map(({ label, value, detail, icon: Icon, tone }) => <Card className={`metric-card tone-${tone}`} key={label}><div className="metric-icon"><Icon /></div><div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div></Card>)}</section>
      <section className="dashboard-grid">
        <Card className="panel span-2"><div className="panel-head"><div><h2>车队矩阵</h2><p>WebSocket 状态</p></div><Link to="/robots">查看全部</Link></div><div className="fleet-matrix">{fleet.map((robot) => { const status = robot.runtime_status as { mode?: string } | null; return <Link to={`/robots/${robot.id}`} className="fleet-card" key={robot.id}><div className="fleet-card-top"><StatusDot online={robot.online} /><Badge tone={robot.online ? "success" : "danger"}>{robot.online ? "在线" : "离线"}</Badge></div><strong>{robot.name}</strong><span>{robot.id} · {robot.role}</span><div className="mode-line"><small>当前模式</small><b>{status?.mode || "UNKNOWN"}</b></div></Link> })}</div></Card>
        <Card className="panel"><div className="panel-head"><div><h2>待确认告警</h2><p>处理安全风险</p></div><Link to="/events">处置中心</Link></div>{data.alerts.length ? <div className="activity-list">{data.alerts.map((alert) => <div className="activity-item danger-line" key={alert.id}><AlertTriangle size={18} /><div><strong>{alert.event.event_type}</strong><span>{alert.event.robot_id} · {alert.event.severity}</span></div><time>{formatTime(alert.event.received_at)}</time></div>)}</div> : <EmptyState title="当前没有待确认告警" detail="车队风险队列为空" />}</Card>
        <Card className="panel"><div className="panel-head"><div><h2>最近任务</h2><p>中心业务动作记录</p></div><Link to="/missions">任务记录</Link></div><div className="activity-list">{data.missions.map((mission) => <div className="activity-item" key={mission.id}><Badge tone={mission.state === "failed" ? "danger" : mission.state === "running" ? "info" : "success"}>{mission.state}</Badge><div><strong>{mission.mission_type}</strong><span>{mission.robot_id || "多车任务"}</span></div><time>{formatTime(mission.created_at)}</time></div>)}</div></Card>
        <Card className="panel"><div className="panel-head"><div><h2>事件流</h2><p>来自车端的上报</p></div><Link to="/events">全部事件</Link></div><div className="activity-list">{data.events.map((event) => <div className="activity-item" key={event.id}><Badge tone={event.severity === "warning" ? "warning" : "neutral"}>{event.severity}</Badge><div><strong>{event.event_type}</strong><span>{event.robot_id}</span></div><time>{formatTime(event.received_at)}</time></div>)}</div></Card>
      </section>
    </div>
  )
}

function formatTime(value: string | null) { return value ? new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : "-" }
function mergeLive(robot: Robot, live?: Record<string, unknown>): Robot { return live ? { ...robot, online: Boolean(live.online), last_seen: String(live.last_seen || robot.last_seen || "") || null, runtime_status: (live.status as Record<string, unknown>) || robot.runtime_status, runtime_error: String(live.error || "") || null } : robot }
