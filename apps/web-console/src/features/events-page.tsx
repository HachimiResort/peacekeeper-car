import { Tabs } from "@base-ui/react/tabs"
import { AlertTriangle, Bell, Radio } from "lucide-react"
import { useEffect, useState, type FormEvent } from "react"
import type { AlertRecord, Robot, RobotEvent } from "../api/types"
import { useSession } from "../app/session"
import { useFeedback } from "../app/feedback"
import { Badge, Button, Card, EmptyState, InlineActionStatus, JsonPanel, LoadingBlock, Modal, PageHeader, fieldClass } from "../components/ui"

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
  const [resolutionAction, setResolutionAction] = useState<"acknowledge" | "takeover" | "false_positive" | "resolved">("acknowledge")
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState("")
  const [confirming, setConfirming] = useState(false)
  const [confirmError, setConfirmError] = useState("")
  const feedback = useFeedback()

  const load = () => {
    if (!api) return
    setLoading(true)
    Promise.all([
      api.events({ robot_id: robotId, severity, limit: "100" }),
      api.alerts({ robot_id: robotId, state: alertState, limit: "100" }),
      api.robots(),
    ]).then(([eventPage, alertPage, fleet]) => {
      setEvents(eventPage.items)
      setAlerts(alertPage.items)
      setRobots(fleet)
      setLoadError("")
    }).catch((cause) => setLoadError(cause instanceof Error ? cause.message : "事件与告警读取失败")).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [api, robotId, severity, alertState])
  useEffect(() => { const refresh = () => load(); window.addEventListener("peacekeeper:hazard-event", refresh); return () => window.removeEventListener("peacekeeper:hazard-event", refresh) }, [api, robotId, severity, alertState])

  const confirm = async (event: FormEvent) => {
    event.preventDefault()
    if (!api || !selectedAlert || confirming) return
    setConfirming(true); setConfirmError("")
    try {
      if (resolutionAction === "takeover") await api.hazardAction(selectedAlert.event.robot_id, selectedAlert.event.event_key, "takeover")
      if (resolutionAction === "false_positive" || resolutionAction === "resolved") {
        const hazard = await api.hazardStatus(selectedAlert.event.robot_id)
        if (hazard.current_event_key === selectedAlert.event.event_key) {
          await api.hazardAction(selectedAlert.event.robot_id, selectedAlert.event.event_key, "resume")
        }
      }
      await api.confirmAlert(selectedAlert.id, operatorName, resolution, resolutionAction)
      feedback.notify({ title: "告警处置已确认并留档", description: selectedAlert.event.event_type, tone: "success" })
      setSelectedAlert(null); setResolution(""); setResolutionAction("acknowledge"); load()
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "告警确认失败"
      setConfirmError(message); feedback.notify({ title: "告警确认失败", description: message, tone: "error" })
    } finally { setConfirming(false) }
  }

  return <div className="page-enter">
    <PageHeader eyebrow="EVENT OPERATIONS" title="事件与告警" description="车端事件在中心留档，告警必须由操作员明确确认。" />
    {loading && (events || alerts) && <InlineActionStatus tone="info" title="正在刷新事件与告警" />}
    {loadError && <InlineActionStatus tone="error" title="事件与告警刷新失败" detail={loadError} onRetry={load} />}
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
        {!alerts ? <LoadingBlock /> : alerts.length === 0 ? <EmptyState title="告警队列为空" detail="没有匹配的待处理风险" /> : <section className="alert-grid">{alerts.map((alert) => <Card className="alert-card" key={alert.id}><div className="alert-card-icon"><AlertTriangle /></div><div><Badge tone={alert.state === "pending" ? "warning" : "success"}>{alert.state}</Badge><h2>{alert.event.event_type}</h2><p>{alert.event.robot_id} · {alert.event.severity}</p><time>{formatDate(alert.event.occurred_at)}</time></div>{alert.state === "pending" ? <Button variant="warning" onClick={() => { setConfirmError(""); setSelectedAlert(alert) }}>确认处置</Button> : <div className="resolved-by">{alert.confirmed_by}<small>{alert.resolution || "已确认"}</small></div>}</Card>)}</section>}
      </Tabs.Panel>
    </Tabs.Root>
    <Modal open={Boolean(selectedEvent)} title={selectedEvent?.event_type || "事件详情"} onClose={() => setSelectedEvent(null)}>{selectedEvent?.event_type === "hazard_detected" && api && <HazardEvidence api={api} event={selectedEvent} />}<JsonPanel value={selectedEvent} /></Modal>
    <Modal open={Boolean(selectedAlert)} busy={confirming} title="确认告警处置" onClose={() => setSelectedAlert(null)} footer={<><Button onClick={() => setSelectedAlert(null)} disabled={confirming}>取消</Button><Button variant="warning" type="submit" form="confirm-alert" loading={confirming} loadingText="正在留档">确认并留档</Button></>}>
      {selectedAlert?.event.event_type === "hazard_detected" && api && <HazardEvidence api={api} event={selectedAlert.event} />}
      <form id="confirm-alert" className="form-stack" onSubmit={confirm}><div className="operator-line">确认人：<strong>{operatorName}</strong></div><label><span>处置动作</span><select className={fieldClass} value={resolutionAction} onChange={(event) => setResolutionAction(event.target.value as typeof resolutionAction)}><option value="acknowledge">确认告警，保持当前状态</option><option value="takeover">接管车辆</option><option value="false_positive">标记误报并尝试恢复</option><option value="resolved">处置完成并尝试恢复</option></select></label><label><span>处置结果</span><textarea className={fieldClass} rows={4} value={resolution} onChange={(event) => setResolution(event.target.value)} placeholder="例如：现场复核后已清除隐患" /></label>{confirmError && <div className="form-error" role="alert">{confirmError}</div>}</form>
    </Modal>
  </div>
}

function HazardEvidence({ api, event }: { api: NonNullable<ReturnType<typeof useSession>["api"]>; event: RobotEvent }) {
  const observation = (event.payload.observation || {}) as Record<string, unknown>
  const detection = (event.payload.trigger_detection || {}) as Record<string, unknown>
  const target = (detection.target_pose_map || {}) as Record<string, unknown>
  return <section className="event-evidence"><img src={api.eventEvidenceUrl(event.id, "annotated")} alt="隐患识别证据" /><div><strong>{String(detection.label || "隐患")}</strong><span>置信度 {Math.round(Number(detection.confidence || 0) * 100)}%</span><span>距离 {detection.range_m == null ? "未知" : `${Number(detection.range_m).toFixed(2)} m`}</span><span>{detection.localization_valid ? `地图 x ${Number(target.x).toFixed(2)} / y ${Number(target.y).toFixed(2)}` : "目标位置未确认"}</span><small>{String(observation.model || "")}</small></div></section>
}

function tone(value: string): "neutral" | "warning" | "danger" {
  return value === "warning" ? "warning" : ["error", "critical"].includes(value) ? "danger" : "neutral"
}

function formatDate(value: string | null) {
  return value ? new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "medium" }).format(new Date(value)) : "-"
}
