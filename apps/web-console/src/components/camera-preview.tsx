import { useEffect, useMemo, useState } from "react"
import { Camera, RefreshCw, Radio } from "lucide-react"
import type { MissionApi } from "../api/types"

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
  const [error, setError] = useState("")
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const streamUrl = useMemo(
    () => (active && online ? api.videoStreamUrl(robotId) : ""),
    [active, api, online, robotId],
  )

  useEffect(() => {
    setError("")
    setUpdatedAt(null)
  }, [streamUrl])

  return <section className={`camera-preview ${active && online ? "is-active" : ""}`}>
    <div className="camera-preview-head">
      <div>
        <strong><Camera />车端实时预览</strong>
        <span>{active ? "当前直接显示车端 MJPEG 实时视频流" : "点击“打开摄像头”后开始显示车端画面"}</span>
      </div>
      <span className={active && online ? "live-map-state active" : "live-map-state"}>{active && online ? <><Radio />LIVE</> : "待命"}</span>
    </div>
    <div className="camera-preview-frame">
      {streamUrl
        ? <img
            key={streamUrl}
            src={streamUrl}
            alt="小车摄像头实时画面"
            onLoad={() => {
              setUpdatedAt(new Date())
              setError("")
            }}
            onError={() => setError("摄像头视频流读取失败")}
          />
        : <div className="camera-preview-empty"><RefreshCw className={active && online ? "spin-soft" : ""} /><strong>{online ? (active ? "正在等待画面首帧" : "摄像头已关闭") : "车辆离线"}</strong><span>{online ? (active ? "若长时间无画面，请检查车端摄像头设备和流代理接口。" : "打开后将在这里显示车端第一视角。") : "待车辆恢复在线后即可打开预览。"}</span></div>}
    </div>
    <div className="camera-preview-footer">
      <span>{device ? `设备 ${device}` : "设备信息待同步"}</span>
      <span>{updatedAt ? `已连接 ${updatedAt.toLocaleTimeString()}` : "尚未收到图像"}</span>
    </div>
    {active && online && streaming === false && <div className="camera-preview-warning">车端当前未检测到可用摄像头，展示的可能是回退占位图像。</div>}
    {error && <div className="live-map-error">{error}</div>}
  </section>
}
