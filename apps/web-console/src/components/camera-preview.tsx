import { useEffect, useRef, useState } from "react"
import { Camera, RefreshCw, Radio } from "lucide-react"
import type { MissionApi } from "../api/types"

const POLL_INTERVAL_MS = 900

export function CameraPreview({
  api,
  robotId,
  active,
  online,
  device,
  streaming,
}: {
  api: MissionApi
  robotId: string
  active: boolean
  online: boolean
  device?: string
  streaming?: boolean
}) {
  const [imageUrl, setImageUrl] = useState("")
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
      setUpdatedAt(null)
    }

    if (!active || !online) {
      clearImage()
      setError("")
      return clearImage
    }

    const poll = async () => {
      try {
        const blob = await api.videoSample(robotId)
        if (cancelled) return
        const nextUrl = URL.createObjectURL(blob)
        if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current)
        imageUrlRef.current = nextUrl
        setImageUrl(nextUrl)
        setUpdatedAt(new Date())
        setError("")
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "摄像头画面读取失败")
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
  }, [active, api, online, robotId])

  return <section className={`camera-preview ${active && online ? "is-active" : ""}`}>
    <div className="camera-preview-head">
      <div><strong><Camera />车端实时预览</strong><span>{active ? "按当前会话轮询单帧，适配现有鉴权" : "点击“打开摄像头”后开始取帧"}</span></div>
      <span className={active && online ? "live-map-state active" : "live-map-state"}>{active && online ? <><Radio />LIVE</> : "待命"}</span>
    </div>
    <div className="camera-preview-frame">
      {imageUrl
        ? <img src={imageUrl} alt="小车摄像头实时画面" />
        : <div className="camera-preview-empty"><RefreshCw className={active && online ? "spin-soft" : ""} /><strong>{online ? (active ? "正在等待画面首帧" : "摄像头已关闭") : "车辆离线"}</strong><span>{online ? (active ? "若长时间无画面，请检查车端摄像头设备和代理接口。" : "打开后将在这里显示车端第一视角。") : "待车辆恢复在线后即可打开预览。"}</span></div>}
    </div>
    <div className="camera-preview-footer">
      <span>{device ? `设备 ${device}` : "设备信息待同步"}</span>
      <span>{updatedAt ? `画面更新 ${updatedAt.toLocaleTimeString()}` : "尚未收到图像"}</span>
    </div>
    {active && online && streaming === false && <div className="camera-preview-warning">车端当前未检测到可用摄像头，展示的可能是回退占位图像。</div>}
    {error && <div className="live-map-error">{error}</div>}
  </section>
}
