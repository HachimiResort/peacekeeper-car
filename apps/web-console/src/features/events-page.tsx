import { Tabs } from "@base-ui/react/tabs"
import { AlertTriangle, Bell, Radio } from "lucide-react"
import { useEffect, useState, type FormEvent } from "react"
import type { AlertRecord, Robot, RobotEvent } from "../api/types"
import { useSession } from "../app/session"
import { Badge, Button, Card, EmptyState, JsonPanel, LoadingBlock, Modal, PageHeader, fieldClass } from "../components/ui"

export function EventsPage() {
  const { api, operatorName } = useSession()
  const [tab, setTab] = useState<"events" | "alerts">("events")
  const [events, setEvents] = useState<RobotEvent[] | null>(null)
  const [alerts, setAlerts] = useState<AlertRecord[] | null>(null)
  const [robots, setRobots] = useState<Robot[]>([])
  const [robotId, setRobotId] = useState("")
  const [severity, setSeverity] = useState("")
  const [alertState, setAlertState] = useState("")
  const [selectedEvent, setSelectedEvent] = useState<RobotEvent | null>(null)
  const [selectedAlert, setSelectedAlert] = useState<AlertRecord | null>(null)
  const [resolution, setResolution] = useState("")

  const load = () => {
    if (!api) return
    Promise.all([
      api.events({ robot_id: robotId, severity, limit: "100" }),
      api.alerts({ robot_id: robotId, state: alertState, limit: "100" }),
      api.robots(),
    ]).then(([eventPage, alertPage, fleet]) => {
      setEvents(eventPage.items)
      setAlerts(alertPage.items)
      setRobots(fleet)
    })
  }

  useEffect(() => { load() }, [api, robotId, severity, alertState])

  const confirm = async (event: FormEvent) => {
    event.preventDefault()
    if (!api || !selectedAlert) return
    await api.confirmAlert(selectedAlert.id, operatorName, resolution)
    setSelectedAlert(null)
    setResolution("")
    load()
  }

  return <div className="page-enter">
    <PageHeader eyebrow="EVENT OPERATIONS" title="事件与告警" description="车端事件在中心留档，告警必须由操作员明确确认。" />
    <Tabs.Root value={tab} onValueChange={(value) => setTab(value as "events" | "alerts")}>
      <Tabs.List className="tab-bar" aria-label="事件视图">
        <Tabs.Tab value="events"><Radio />事件流 <span>{events?.length || 0}</span></Tabs.Tab>
        <Tabs.Tab value="alerts"><Bell />告警队列 <span>{alerts?.filter((item) => item.state === "pending").length || 0}</span></Tabs.Tab>
      </Tabs.List>
      <Card className="filter-bar">
        <select className={fieldClass} value={robotId} onChange={(event) => setRobotId(event.target.value)}>
          <option value="">全部车辆</option>
          {robots.map((robot) => <option value={robot.id} key={robot.id}>{robot.name}</option>)}
        </select>
        {tab === "events" ? <select className={fieldClass} value={severity} onChange={(event) => setSeverity(event.target.value)}>
          <option value="">全部级别</option><option value="info">info</option><option value="warning">warning</option><option value="error">error</option><option value="critical">critical</option>
        </select> : <select className={fieldClass} value={alertState} onChange={(event) => setAlertState(event.target.value)}>
          <option value="">全部状态</option><option value="pending">pending</option><option value="confirmed">confirmed</option><option value="resolved">resolved</option>
        </select>}
      </Card>
      <Tabs.Panel value="events">
        {!events ? <LoadingBlock /> : events.length === 0 ? <EmptyState title="没有事件" detail="当前筛选条件下没有车端上报" /> : <Card className="table-card"><div className="table-scroll"><table><thead><tr><th>事件</th><th>车辆</th><th>级别</th><th>发生时间</th><th>任务</th></tr></thead><tbody>{events.map((event) => <tr className="clickable-row" key={event.id} onClick={() => setSelectedEvent(event)}><td><strong>{event.event_type}</strong></td><td>{event.robot_id}</td><td><Badge tone={tone(event.severity)}>{event.severity}</Badge></td><td>{formatDate(event.occurred_at)}</td><td>{event.mission_id?.slice(0, 8) || "-"}</td></tr>)}</tbody></table></div></Card>}
      </Tabs.Panel>
      <Tabs.Panel value="alerts">
        {!alerts ? <LoadingBlock /> : alerts.length === 0 ? <EmptyState title="告警队列为空" detail="没有匹配的待处理风险" /> : <section className="alert-grid">{alerts.map((alert) => <Card className="alert-card" key={alert.id}><div className="alert-card-icon"><AlertTriangle /></div><div><Badge tone={alert.state === "pending" ? "warning" : "success"}>{alert.state}</Badge><h2>{alert.event.event_type}</h2><p>{alert.event.robot_id} · {alert.event.severity}</p><time>{formatDate(alert.event.occurred_at)}</time></div>{alert.state === "pending" ? <Button variant="warning" onClick={() => setSelectedAlert(alert)}>确认处置</Button> : <div className="resolved-by">{alert.confirmed_by}<small>{alert.resolution || "已确认"}</small></div>}</Card>)}</section>}
      </Tabs.Panel>
    </Tabs.Root>
    <Modal open={Boolean(selectedEvent)} title={selectedEvent?.event_type || "事件详情"} onClose={() => setSelectedEvent(null)}><JsonPanel value={selectedEvent} /></Modal>
    <Modal open={Boolean(selectedAlert)} title="确认告警处置" onClose={() => setSelectedAlert(null)} footer={<><Button onClick={() => setSelectedAlert(null)}>取消</Button><Button variant="warning" type="submit" form="confirm-alert">确认并留档</Button></>}>
      <form id="confirm-alert" className="form-stack" onSubmit={confirm}><div className="operator-line">确认人：<strong>{operatorName}</strong></div><label><span>处置结果</span><textarea className={fieldClass} rows={4} value={resolution} onChange={(event) => setResolution(event.target.value)} placeholder="例如：现场复核无明火，已恢复巡护" /></label></form>
    </Modal>
  </div>
}

function tone(value: string): "neutral" | "warning" | "danger" {
  return value === "warning" ? "warning" : ["error", "critical"].includes(value) ? "danger" : "neutral"
}

function formatDate(value: string | null) {
  return value ? new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "medium" }).format(new Date(value)) : "-"
}
