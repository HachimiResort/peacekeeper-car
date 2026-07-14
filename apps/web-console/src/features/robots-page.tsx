import { useEffect, useState, type FormEvent } from "react"
import { Bot, Edit3, Plus, Power } from "lucide-react"
import { Link } from "react-router-dom"
import { useFeedback } from "../app/feedback"
import { useLiveStatus } from "../app/live-status"
import { useSession } from "../app/session"
import type { Robot } from "../api/types"
import { Badge, Button, Card, EmptyState, InlineActionStatus, LoadingBlock, Modal, PageHeader, StatusDot, fieldClass } from "../components/ui"

const emptyForm = { id: "", name: "", base_url: "http://", role: "robot", enabled: true }

export function RobotsPage() {
  const { api } = useSession()
  const { statuses } = useLiveStatus()
  const feedback = useFeedback()
  const [robots, setRobots] = useState<Robot[] | null>(null)
  const [editing, setEditing] = useState<Robot | null | "new">(null)
  const [form, setForm] = useState(emptyForm)
  const [error, setError] = useState("")
  const [formError, setFormError] = useState("")
  const [saving, setSaving] = useState(false)
  const [updatingRobotId, setUpdatingRobotId] = useState("")

  const load = async () => {
    if (!api) return
    try { setRobots(await api.robots()); setError("") }
    catch (cause) { setError(cause instanceof Error ? cause.message : "车辆列表加载失败") }
  }
  useEffect(() => { void load() }, [api])

  const open = (robot?: Robot) => {
    setEditing(robot || "new"); setFormError("")
    setForm(robot ? { id: robot.id, name: robot.name, base_url: robot.base_url, role: robot.role, enabled: robot.enabled } : emptyForm)
  }
  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (!api || saving) return
    setSaving(true); setFormError("")
    try {
      if (editing === "new") await api.createRobot({ ...form, capabilities: { mapping: true, navigation: true, patrol: true } })
      else if (editing) await api.updateRobot(editing.id, { name: form.name, base_url: form.base_url, role: form.role, enabled: form.enabled })
      feedback.notify({ title: editing === "new" ? "车辆已注册" : "车辆信息已保存", description: form.name, tone: "success" })
      setEditing(null); await load()
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "保存失败"
      setFormError(message)
      feedback.notify({ title: "车辆保存失败", description: message, tone: "error" })
    } finally { setSaving(false) }
  }
  const setEnabled = async (robot: Robot, enabled: boolean) => {
    if (!api || updatingRobotId) return
    setUpdatingRobotId(robot.id)
    try {
      const updated = await api.updateRobot(robot.id, { enabled })
      setRobots((current) => current?.map((item) => item.id === updated.id ? updated : item) || current)
      feedback.notify({ title: enabled ? "车辆已启用" : "车辆已停用", description: updated.name, tone: "success" })
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "更新车辆启用状态失败"
      feedback.notify({ title: "车辆状态更新失败", description: message, tone: "error" })
    } finally { setUpdatingRobotId("") }
  }

  return <div className="page-enter">
    <PageHeader eyebrow="FLEET REGISTRY" title="车辆编组" description="数据库是正式车辆清单，停用车辆不会参与轮询与全局动作。" actions={<Button variant="primary" onClick={() => open()}><Plus size={17} />注册车辆</Button>} />
    {error && !robots ? <EmptyState title="车辆列表加载失败" detail={error} action={<Button onClick={() => void load()}>重新加载</Button>} /> : !robots ? <LoadingBlock /> : <>
      {error && <InlineActionStatus tone="warning" title="车辆列表刷新失败" detail={error} onRetry={() => void load()} />}
      <Card className="table-card"><div className="table-summary"><div><Bot /><strong>{robots.length} 辆登记车辆</strong></div><span>{robots.filter((item) => Boolean(statuses[item.id]?.online ?? item.online)).length} 在线</span></div><div className="table-scroll"><table><thead><tr><th>车辆</th><th>连接状态</th><th>角色</th><th>当前模式</th><th>最后在线</th><th>启用</th><th /></tr></thead><tbody>{robots.map((stored) => { const live = statuses[stored.id]; const robot = live ? { ...stored, online: Boolean(live.online), runtime_status: (live.status as Record<string, unknown>) || stored.runtime_status, last_seen: String(live.last_seen || stored.last_seen || "") || null } : stored; const status = robot.runtime_status as { mode?: string } | null; return <tr key={robot.id}><td><Link className="entity-link" to={`/robots/${robot.id}`}><span className="entity-icon"><Bot size={18} /></span><span><strong>{robot.name}</strong><small>{robot.id}</small></span></Link></td><td><div className="status-label"><StatusDot online={robot.online} />{robot.online ? "在线" : "离线"}</div></td><td><Badge>{robot.role}</Badge></td><td><code>{status?.mode || "UNKNOWN"}</code></td><td>{formatDate(robot.last_seen)}</td><td><Badge tone={robot.enabled ? "success" : "neutral"}>{robot.enabled ? "启用" : "停用"}</Badge></td><td><div className="table-actions">{!robot.enabled && <Button size="sm" variant="primary" loading={updatingRobotId === robot.id} loadingText="启用中" disabled={Boolean(updatingRobotId)} onClick={() => void setEnabled(robot, true)}><Power size={15} />启用</Button>}<Button variant="ghost" onClick={() => open(robot)}><Edit3 size={16} />编辑</Button></div></td></tr> })}</tbody></table></div></Card>
    </>}
    <Modal open={Boolean(editing)} busy={saving} title={editing === "new" ? "注册新车辆" : "编辑车辆"} onClose={() => setEditing(null)} footer={<><Button onClick={() => setEditing(null)} disabled={saving}>取消</Button><Button variant="primary" type="submit" form="robot-form" loading={saving} loadingText="正在保存">保存</Button></>}>
      <form id="robot-form" className="form-grid" onSubmit={save}><label><span>车辆 ID</span><input className={fieldClass} disabled={editing !== "new"} value={form.id} onChange={(event) => setForm({ ...form, id: event.target.value })} required pattern="[A-Za-z0-9_-]+" /></label><label><span>显示名称</span><input className={fieldClass} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} required /></label><label className="span-2"><span>Fleet Agent Base URL</span><input className={fieldClass} type="url" value={form.base_url} onChange={(event) => setForm({ ...form, base_url: event.target.value })} required /></label><label><span>角色</span><input className={fieldClass} value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value })} required /></label><label className="switch-field"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /><span>启用状态轮询与控制</span></label>{formError && <div className="form-error span-2" role="alert">{formError}</div>}</form>
    </Modal>
  </div>
}

function formatDate(value: string | null) { return value ? new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : "从未" }
