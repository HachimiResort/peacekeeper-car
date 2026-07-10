import { Navigate, Route, Routes, useLocation } from "react-router-dom"
import { useSession } from "./session"
import { AppShell } from "../components/app-shell"
import { ConnectPage } from "../features/connect-page"
import { DashboardPage } from "../features/dashboard-page"
import { RobotsPage } from "../features/robots-page"
import { RobotDetailPage } from "../features/robot-detail-page"
import { MapsPage } from "../features/maps-page"
import { MapDetailPage } from "../features/map-detail-page"
import { MissionsPage } from "../features/missions-page"
import { EventsPage } from "../features/events-page"

function RequireSession() {
  const { connected } = useSession()
  const location = useLocation()
  return connected ? <AppShell /> : <Navigate to="/connect" replace state={{ from: location.pathname }} />
}

export function App() {
  return (
    <Routes>
      <Route path="/connect" element={<ConnectPage />} />
      <Route element={<RequireSession />}>
        <Route index element={<DashboardPage />} />
        <Route path="robots" element={<RobotsPage />} />
        <Route path="robots/:robotId" element={<RobotDetailPage />} />
        <Route path="maps" element={<MapsPage />} />
        <Route path="maps/:mapId" element={<MapDetailPage />} />
        <Route path="missions" element={<MissionsPage />} />
        <Route path="events" element={<EventsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
