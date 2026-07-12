import { useEffect, useRef, useState } from "react"
import { Activity, Radio, RefreshCw } from "lucide-react"
import type { LiveMapStatus, MissionApi } from "../api/types"

const POLL_INTERVAL_MS = 2_000

export function LiveMapPreview({ api, robotId, active }: { api: MissionApi; robotId: string; active: boolean }) {
  const [imageUrl, setImageUrl] = useState("")
  const [status, setStatus] = useState<LiveMapStatus | null>(null)
  const [error, setError] = useState("")
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const imageUrlRef = useRef("")

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined
    const clearImage = () => {
      if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current)
      imageUrlRef.current = ""
      setImageUrl("")
    }
    if (!active) {
      clearImage()
      setStatus(null)
      setError("")
      setUpdatedAt(null)
      return clearImage
    }

    const poll = async () => {
      try {
        const nextStatus = await api.liveMapStatus(robotId)
        if (cancelled) return
        setStatus(nextStatus)
        setError(nextStatus.last_error || "")
        if (nextStatus.has_map) {
          const blob = await api.liveMapPreview(robotId)
          if (cancelled) return
          const nextUrl = URL.createObjectURL(blob)
          if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current)
          imageUrlRef.current = nextUrl
          setImageUrl(nextUrl)
          setUpdatedAt(new Date())
        }
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "实时地图读取失败")
      } finally {
        if (!cancelled) timer = window.setTimeout(poll, POLL_INTERVAL_MS)
      }
    }
    void poll()
    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
      clearImage()
    }
  }, [active, api, robotId])

  const meta = status?.has_map
    ? `${status.width} × ${status.height} · ${(status.resolution * 100).toFixed(1)} cm/px`
    : "等待 SLAM 发布 /map"

  return <section className={`live-map-preview ${active ? "is-active" : ""}`}>
    <div className="live-map-head">
      <div><strong><Activity />实时建图预览</strong><span>{active ? "每 2 秒刷新一次，不影响 SLAM" : "开始建图后自动刷新"}</span></div>
      <span className={active ? "live-map-state active" : "live-map-state"}>{active ? <><Radio />LIVE</> : "待命"}</span>
    </div>
    <div className="live-map-frame">
      {imageUrl ? <img src={imageUrl} alt="当前 OccupancyGrid 实时建图预览" /> : <div className="live-map-empty"><RefreshCw className={active ? "spin-soft" : ""} /><strong>{active ? "正在等待地图首帧" : "尚未开始建图"}</strong><span>{active ? "确认雷达与 SLAM 正在运行；首帧通常在数秒内出现。" : "点击“开始建图”后，这里将展示当前 /map。"}</span></div>}
    </div>
    <div className="live-map-footer"><span>{meta}</span><span>{updatedAt ? `预览更新 ${updatedAt.toLocaleTimeString()}` : "尚未收到图像"}</span></div>
    {error && <div className="live-map-error">{error}</div>}
  </section>
}
