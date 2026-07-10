import { useState, type FormEvent } from "react"
import { ArrowRight, Command, KeyRound, Radio, ShieldCheck, UserRound } from "lucide-react"
import { Navigate, useNavigate } from "react-router-dom"
import { useSession } from "../app/session"
import { Button, Card, fieldClass } from "../components/ui"

export function ConnectPage() {
  const { connected, connect, demoMode } = useSession()
  const [token, setToken] = useState("")
  const [operator, setOperator] = useState(localStorage.getItem("peacekeeper.operator-name") || "值守员")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  if (connected) return <Navigate to="/" replace />

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!demoMode && !token.trim()) { setError("请输入共享 Token"); return }
    if (!operator.trim()) { setError("请输入操作员名称"); return }
    setLoading(true); setError("")
    try { await connect(token.trim(), operator.trim()); navigate("/") }
    catch (reason) { setError(reason instanceof Error ? reason.message : "连接失败") }
    finally { setLoading(false) }
  }

  return (
    <main className="connect-page">
      <section className="connect-story">
        <div className="connect-brand"><Command size={22} /> PEACEKEEPER</div>
        <div className="connect-copy">
          <div className="eyebrow light">FOREST OPERATIONS NETWORK</div>
          <h1>把每一辆巡护车，<br />放进同一个指挥视野。</h1>
          <p>车辆、地图、任务和告警通过 Mission API 汇聚。这里不绕过安全仲裁，也不直接接触 ROS 与串口。</p>
        </div>
        <div className="connect-network" aria-hidden="true"><span /><span /><span /><i /></div>
        <div className="connect-principles"><div><ShieldCheck />车端安全兜底</div><div><Radio />实时状态推送</div><div><KeyRound />会话级凭据</div></div>
      </section>
      <section className="connect-panel">
        <Card className="connect-card">
          <div className="connect-card-head"><span className="status-dot is-online" /><div><strong>{demoMode ? "演示环境" : "连接中心控制面"}</strong><span>{demoMode ? "将使用显式样例数据" : "默认地址由同源代理提供"}</span></div></div>
          {demoMode && <div className="demo-notice">DEMO 模式已启用，所有操作仅修改浏览器内存。</div>}
          <form onSubmit={submit}>
            {!demoMode && <label><span>共享 Token</span><div className="input-with-icon"><KeyRound size={17} /><input className={fieldClass} type="password" value={token} onChange={(event) => setToken(event.target.value)} autoFocus autoComplete="off" placeholder="X-Peacekeeper-Token" /></div></label>}
            <label><span>操作员名称</span><div className="input-with-icon"><UserRound size={17} /><input className={fieldClass} value={operator} onChange={(event) => setOperator(event.target.value)} placeholder="例如：北区值守员" /></div></label>
            {error && <div className="form-error">{error}</div>}
            <Button variant="primary" size="lg" className="w-full" disabled={loading}>{loading ? "正在验证" : demoMode ? "进入演示指挥台" : "验证并连接"}<ArrowRight size={18} /></Button>
          </form>
          <p className="credential-note">Token 仅保存在当前标签页的 sessionStorage，关闭标签页后自动清除。</p>
        </Card>
      </section>
    </main>
  )
}
