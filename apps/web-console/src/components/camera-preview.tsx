import { useEffect, useMemo, useState } from "react"
import { Camera, Crosshair, Radio, Ruler } from "lucide-react"
import type { DepthMeasurement, MissionApi } from "../api/types"

export function CameraPreview({
  api,
  robotId,
  active,
  online,
  device,
  streaming,
  depth,
}: {
  api: MissionApi
  robotId: string
  active: boolean
  online: boolean
  device?: string
  streaming?: boolean
  depth?: { has_depth?: boolean; encoding?: string; topic?: string; last_error?: string | null }
}) {
  const [loadedAt, setLoadedAt] = useState<Date | null>(null)
  const [streamError, setStreamError] = useState("")
  const [measurement, setMeasurement] = useState<DepthMeasurement | null>(null)
  const [measureError, setMeasureError] = useState("")
  const [measuring, setMeasuring] = useState(false)
  const streamUrl = useMemo(() => active && online ? api.videoStreamUrl(robotId) : "", [active, api, online, robotId])

  useEffect(() => {
    if (active && online) return
    setLoadedAt(null)
    setStreamError("")
    setMeasurement(null)
    setMeasureError("")
    setMeasuring(false)
  }, [active, online])

  const handleMeasure = async (event: React.MouseEvent<HTMLImageElement>) => {
    if (!active || !online || !depth?.has_depth || measuring) return
    const rect = event.currentTarget.getBoundingClientRect()
    if (!rect.width || !rect.height) return
    const xRatio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width))
    const yRatio = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))
    setMeasuring(true)
    setMeasureError("")
    try {
      const result = await api.depthMeasure(robotId, { x_ratio: xRatio, y_ratio: yRatio, window_radius_px: 6 })
      setMeasurement(result)
    } catch (cause) {
      setMeasureError(cause instanceof Error ? cause.message : "目标距离测量失败")
    } finally {
      setMeasuring(false)
    }
  }

  return <section className={`camera-preview ${active && online ? "is-active" : ""}`}>
    <div className="camera-preview-head">
      <div><strong><Camera />车端实时预览</strong><span>{active ? "已切换为连续视频流，点击画面可测目标距离" : "点击“打开摄像头”后开始连续取流"}</span></div>
      <span className={active && online ? "live-map-state active" : "live-map-state"}>{active && online ? <><Radio />LIVE</> : "待命"}</span>
    </div>
    <div className="camera-preview-frame">
      {streamUrl
        ? <div className="camera-preview-stage">
            <img
              src={streamUrl}
              alt="小车摄像头实时画面"
              onLoad={() => { setLoadedAt(new Date()); setStreamError("") }}
              onError={() => setStreamError("视频流连接失败，请检查中心代理和车端摄像头服务")}
              onClick={(event) => void handleMeasure(event)}
            />
            {measurement && <div className="camera-measure-marker" style={{ left: `${measurement.x_ratio * 100}%`, top: `${measurement.y_ratio * 100}%` }}><Crosshair /></div>}
            <div className="camera-preview-hint"><Crosshair />点击目标中心点测距</div>
          </div>
        : <div className="camera-preview-empty"><Camera /><strong>{online ? "摄像头已关闭" : "车辆离线"}</strong><span>{online ? "打开后将在这里显示车端第一视角，并支持点击测距。" : "待车辆恢复在线后即可打开预览。"}</span></div>}
    </div>
    <div className="camera-preview-footer">
      <span>{device ? `视频设备 ${device}` : "视频设备信息待同步"}</span>
      <span>{loadedAt ? `视频已连接 ${loadedAt.toLocaleTimeString()}` : "尚未建立视频流"}</span>
    </div>
    <div className="camera-measure-panel">
      <div><strong><Ruler />深度测距</strong><span>{depth?.has_depth ? `深度话题 ${depth.topic || "-"}${depth.encoding ? ` · ${depth.encoding}` : ""}` : "当前尚未收到深度帧"}</span></div>
      <div className="camera-measure-result">
        {measurement
          ? <strong>{measurement.distance_m.toFixed(3)} m</strong>
          : <strong>{measuring ? "测量中..." : "--"}</strong>}
        <span>{measurement ? `像素 (${measurement.pixel_x}, ${measurement.pixel_y}) · 有效样本 ${measurement.sample_count}` : depth?.has_depth ? "点击画面中的目标中心点开始测量" : "请先确认 Astra 深度相机驱动已启动"}</span>
      </div>
    </div>
    {active && online && streaming === false && <div className="camera-preview-warning">车端当前未检测到可用视频设备，展示的可能是回退占位图像。</div>}
    {!depth?.has_depth && <div className="camera-preview-warning">深度相机未就绪。若现场使用 Astra Pro Plus，请确认车端已启动 `ros2 launch astra_camera astra.launch.xml`。</div>}
    {depth?.last_error && <div className="camera-preview-warning">{depth.last_error}</div>}
    {measureError && <div className="live-map-error">{measureError}</div>}
    {streamError && <div className="live-map-error">{streamError}</div>}
  </section>
}
