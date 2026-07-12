import { useEffect, useState } from "react"
import { Activity, Bell, Bot, ChevronRight, Command, Database, FlaskConical, LogOut, Map, Menu, Moon, Octagon, ScrollText, Sun, X } from "lucide-react"
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom"
import { useSession } from "../app/session"
import { useFeedback } from "../app/feedback"
import { LiveStatusProvider, useLiveStatus } from "../app/live-status"
import { Button, InlineActionStatus } from "./ui"
import { cn } from "../lib/cn"

const navigation = [
  { to: "/", label: "态势总览", icon: Activity },
  { to: "/robots", label: "车辆编组", icon: Bot },
  { to: "/maps", label: "地图资产", icon: Map },
  { to: "/missions", label: "任务记录", icon: ScrollText },
  { to: "/events", label: "事件与告警", icon: Bell },
  { to: "/fleet-lab", label: "联动实验", icon: FlaskConical },
]

export function AppShell() {
  return <LiveStatusProvider><AppShellContent /></LiveStatusProvider>
}

function AppShellContent() {
  const { api, demoMode, operatorName, disconnect } = useSession()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [dark, setDark] = useState(() => localStorage.getItem("peacekeeper.theme") === "dark")
  const { connected: live } = useLiveStatus()
  const [stopping, setStopping] = useState(false)
  const [fleetState, setFleetState] = useState<{ tone: "success" | "error" | "info"; title: string; detail?: string } | null>(null)
  const feedback = useFeedback()
  const location = useLocation()
  const navigate = useNavigate()

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark)
    localStorage.setItem("peacekeeper.theme", dark ? "dark" : "light")
  }, [dark])

  const fleetStop = async () => {
    if (!api || stopping) return
    setStopping(true)
    setFleetState({ tone: "info", title: "正在向全部启用车辆下发停车指令" })
    try {
      await api.fleetStop()
      setFleetState({ tone: "success", title: "全局停车指令已下发", detail: "车辆状态将通过实时链路继续更新" })
      feedback.notify({ id: "fleet-stop", title: "全局停车指令已下发", tone: "success" })
    } catch (error) {
      const message = error instanceof Error ? error.message : "全局停车失败"
      setFleetState({ tone: "error", title: "全局停车未完成", detail: message })
      feedback.notify({ id: "fleet-stop", title: "全局停车未完成", description: message, tone: "error" })
    } finally {
      setStopping(false)
    }
  }

  const leave = () => { disconnect(); navigate("/connect") }
  const title = navigation.find((item) => item.to === location.pathname)?.label || "运营指挥"

  return (
    <div className="app-shell">
      <aside className={cn("sidebar", mobileOpen && "is-open")}>
        <div className="brand"><div className="brand-mark"><Command size={21} /></div><div><strong>PEACEKEEPER</strong><span>巡护指挥中心</span></div></div>
        {demoMode && <div className="demo-ribbon">DEMO 演示数据</div>}
        <nav>
          {navigation.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === "/"} onClick={() => setMobileOpen(false)} className={({ isActive }) => cn("nav-item", isActive && "active")}>
              <Icon size={18} /><span>{label}</span><ChevronRight className="nav-chevron" size={15} />
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-meta">
          <div className="connection-state"><span className={cn("status-dot", live ? "is-online" : "is-warning")} /><div><strong>{live ? "实时链路正常" : "等待状态推送"}</strong><span>{operatorName}</span></div></div>
          <Button variant="ghost" onClick={leave}><LogOut size={16} />断开连接</Button>
        </div>
      </aside>
      {mobileOpen && <button className="sidebar-overlay" onClick={() => setMobileOpen(false)} aria-label="关闭导航" />}
      <div className="shell-main">
        <header className="topbar">
          <Button variant="ghost" className="mobile-menu" onClick={() => setMobileOpen((value) => !value)}>{mobileOpen ? <X /> : <Menu />}</Button>
          <div className="breadcrumb"><Database size={16} /><span>中心控制面</span><ChevronRight size={14} /><strong>{title}</strong></div>
          <div className="topbar-actions">
            <Button variant="ghost" aria-label="切换主题" onClick={() => setDark((value) => !value)}>{dark ? <Sun size={18} /> : <Moon size={18} />}</Button>
            <Button variant="danger" onClick={fleetStop} loading={stopping} loadingText="下发中"><Octagon size={17} />全局停车</Button>
          </div>
        </header>
        <main className="page-stage">{fleetState && <div className="shell-action-status"><InlineActionStatus {...fleetState} /></div>}<Outlet /></main>
      </div>
    </div>
  )
}
