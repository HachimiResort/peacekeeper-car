import { useCallback, useEffect, useMemo, useState } from "react"
import { AlertTriangle, Hand, Pause, Play, ShieldCheck } from "lucide-react"
import type { HazardStatus, MapPoint, MissionApi, StoredMap } from "../api/types"
import { MapCanvas, type MapOverlay } from "./map-canvas"
import { Badge, Button, Card, InlineActionStatus } from "./ui"

export function HazardPanel({ api, robotId, available, map }: { api: MissionApi; robotId: string; available: boolean; map?: StoredMap }) {
  const [status, setStatus] = useState<HazardStatus | null>(null)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState("")
  const [imageUrl, setImageUrl] = useState("")
  const load = useCallback(async () => {
    if (!available) return
    try { setStatus(await api.hazardStatus(robotId)); setError("") }
    catch (cause) { setError(cause instanceof Error ? cause.message : "隐患状态读取失败") }
  }, [api, available, robotId])

  useEffect(() => { void load(); const timer = window.setInterval(() => void load(), 1000); return () => window.clearInterval(timer) }, [load])
  useEffect(() => {
    if (!status?.enabled) { setImageUrl(""); return }
    let active = true
    let objectUrl = ""
    const refresh = async () => {
      try {
        const blob = await api.visionLatestImage(robotId)
        if (!active) return
        const next = URL.createObjectURL(blob)
        if (objectUrl) URL.revokeObjectURL(objectUrl)
        objectUrl = next
        setImageUrl(next)
      } catch { /* The first inference may not have completed yet. */ }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 2000)
    return () => { active = false; window.clearInterval(timer); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [api, robotId, status?.enabled])

  const run = async (name: string, task: () => Promise<unknown>) => {
    setBusy(name); setError("")
    try { await task(); await load() }
    catch (cause) { setError(cause instanceof Error ? cause.message : "隐患操作失败") }
    finally { setBusy("") }
  }
  const detection = status?.current_detection
  const targetPoint = useMemo(() => map && detection?.target_pose_map ? mapPoint(map, detection.target_pose_map.x, detection.target_pose_map.y) : null, [detection?.target_pose_map, map])
  const overlays: MapOverlay[] | undefined = targetPoint ? [{ id: "hazard", point: targetPoint, label: "!", color: "var(--danger)", state: "failed" }] : undefined
  const holding = status?.state === "HOLDING"
  const alarmAudio = status?.alarm_audio
  const alarmTitle = alarmAudio?.playing ? "报警音乐正在播放" : alarmAudio?.ready ? "报警音乐已就绪" : alarmAudio?.configured ? "报警音乐不可用" : "报警音乐未配置"
  const alarmDetail = alarmAudio?.last_error || (alarmAudio?.asset ? `${alarmAudio.asset} · ${alarmAudio.loop ? "循环播放" : "单次播放"} · 音量 ${alarmAudio.volume}%` : "请上传音频并在车端 hazards.alarm_audio_asset 中配置文件名。")

  return <Card className="panel span-2 hazard-panel">
    <div className="panel-head"><div><h2>隐患巡逻闭环</h2><p>后台独立监控，关闭网页后仍会继续运行</p></div><Button variant={status?.enabled ? "secondary" : "primary"} disabled={!available || Boolean(busy) || holding} loading={busy === "monitor"} onClick={() => void run("monitor", () => api.setHazardMonitor(robotId, !status?.enabled))}>{status?.enabled ? <Pause /> : <Play />}{status?.enabled ? "关闭隐患监控" : "开启隐患监控"}</Button></div>
    {!available && <InlineActionStatus tone="warning" title="车辆离线，无法控制隐患监控" />}
    {status && <div className="hazard-status-row"><Badge tone={holding ? "danger" : status.enabled ? "success" : "neutral"}>{status.state}</Badge><span>连续确认 {status.confirmation_hits}/{status.confirmation_window}</span><span>待上传证据 {status.outbox?.pending || 0}</span>{status.auto_resume_remaining_s != null && <strong>{status.auto_resume_remaining_s.toFixed(1)} 秒后尝试恢复</strong>}</div>}
    <div className="hazard-workspace"><div className="vision-frame">{imageUrl ? <img src={imageUrl} alt="当前隐患识别画面" /> : <div className="vision-empty"><ShieldCheck /><strong>{status?.enabled ? "等待后台识别结果" : "隐患监控未开启"}</strong></div>}</div><div className="hazard-details">
      {detection ? <><h3><AlertTriangle />{detection.label}</h3><dl className="vision-metrics"><div><dt>置信度</dt><dd>{Math.round(detection.confidence * 100)}%</dd></div><div><dt>距离</dt><dd>{detection.range_m?.toFixed(2) ?? "-"} m</dd></div><div><dt>来源</dt><dd>{detection.range_source || "-"}</dd></div><div><dt>质量</dt><dd>{detection.range_quality || "-"}</dd></div></dl><p>{detection.localization_valid && detection.target_pose_map ? `地图位置 x ${detection.target_pose_map.x.toFixed(2)} / y ${detection.target_pose_map.y.toFixed(2)}` : "目标位置未确认"}</p></> : <p>当前没有确认中的隐患。</p>}
      {alarmAudio && <InlineActionStatus tone={alarmAudio.playing ? "success" : alarmAudio.ready ? "info" : "warning"} title={alarmTitle} detail={alarmDetail} />}
      {holding && status?.current_event_key && <div className="stack-actions"><Button variant="warning" loading={busy === "hold"} onClick={() => void run("hold", () => api.hazardAction(robotId, status.current_event_key!, "hold"))}><Pause />保持停车</Button><Button loading={busy === "resume"} onClick={() => void run("resume", () => api.hazardAction(robotId, status.current_event_key!, "resume"))}><Play />立即恢复</Button><Button variant="danger" loading={busy === "takeover"} onClick={() => void run("takeover", () => api.hazardAction(robotId, status.current_event_key!, "takeover"))}><Hand />接管车辆</Button></div>}
    </div></div>
    {map && overlays && <div className="hazard-map"><MapCanvas api={api} map={map} overlays={overlays} interactive={false} /></div>}
    {error && <InlineActionStatus tone="error" title="隐患闭环异常" detail={error} onRetry={() => void load()} />}
  </Card>
}

function mapPoint(map: StoredMap, x: number, y: number): MapPoint {
  return { x, y, pixelX: (x - map.origin[0]) / map.resolution - 0.5, pixelY: map.height - (y - map.origin[1]) / map.resolution - 0.5 }
}
