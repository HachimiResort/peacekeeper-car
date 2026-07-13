import { useCallback, useEffect, useMemo, useState } from "react"
import { ImageIcon, ScanSearch } from "lucide-react"
import type { MissionApi, VisionCapture, VisionStatus } from "../api/types"
import { Badge, Button, Card, InlineActionStatus } from "./ui"

export function VisionDetectionCard({ api, robotId, available }: { api: MissionApi; robotId: string; available: boolean }) {
  const [status, setStatus] = useState<VisionStatus | null>(null)
  const [result, setResult] = useState<VisionCapture | null>(null)
  const [loading, setLoading] = useState(true)
  const [active, setActive] = useState(false)
  const [streamFailed, setStreamFailed] = useState(false)
  const [error, setError] = useState("")
  const modelReady = Boolean(status?.enabled && status.model_loaded)
  const streamUrl = useMemo(
    () => (active && available && modelReady ? api.visionStreamUrl(robotId) : ""),
    [active, api, available, modelReady, robotId],
  )

  const refresh = useCallback(async (includeLatest: boolean) => {
    try {
      const nextStatus = await api.visionStatus(robotId)
      setStatus(nextStatus)
      setError(nextStatus.last_error || "")
      if (includeLatest && nextStatus.latest_available) setResult(await api.visionLatest(robotId))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "视觉状态读取失败")
    }
  }, [api, robotId])

  useEffect(() => {
    let cancelled = false
    const loadInitial = async () => {
      setLoading(true)
      try {
        const nextStatus = await api.visionStatus(robotId)
        if (cancelled) return
        setStatus(nextStatus)
        setError(nextStatus.last_error || "")
        if (nextStatus.latest_available) {
          const nextResult = await api.visionLatest(robotId)
          if (cancelled) return
          setResult(nextResult)
        }
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "视觉状态读取失败")
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void loadInitial()
    return () => { cancelled = true }
  }, [api, robotId])

  useEffect(() => {
    setStreamFailed(false)
  }, [streamUrl])

  useEffect(() => {
    if (!streamUrl) return
    let cancelled = false
    const syncLatest = async () => {
      try {
        const nextStatus = await api.visionStatus(robotId)
        if (cancelled) return
        setStatus(nextStatus)
        setError(nextStatus.last_error || "")
        if (nextStatus.latest_available) {
          const nextResult = await api.visionLatest(robotId)
          if (cancelled) return
          setResult(nextResult)
        }
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "视觉状态读取失败")
      }
    }
    void syncLatest()
    const timer = window.setInterval(() => { void syncLatest() }, 1000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [api, robotId, streamUrl])

  const detections = result?.detections || []
  const targets = Object.entries(result?.triggers || {})
  const rangeSummary = result?.range_measurements

  return <Card className="panel span-2 vision-panel">
    <div className="panel-head">
      <div><h2>YOLO 实时识别</h2><p>直接显示带框视频流，持续更新最近一帧的检测结果</p></div>
      <Button variant={active ? "secondary" : "primary"} disabled={!available || !modelReady || loading} onClick={() => setActive((current) => !current)}><ScanSearch />{active ? "关闭实时识别" : "开启实时识别"}</Button>
    </div>
    {!available && <InlineActionStatus tone="warning" title="视觉识别暂不可用" detail="车辆离线或已停用，恢复在线后可执行识别。" />}
    {status && !status.enabled && <InlineActionStatus tone="warning" title="YOLO 未启用" detail="请先在 fleet-agent 配置中启用 vision。" />}
    {status?.enabled && !status.model_loaded && <InlineActionStatus tone="warning" title="模型未就绪" detail="YOLO worker 应在启动阶段加载模型，请检查工作进程和 TensorRT 引擎日志。" />}
    <div className="vision-workspace">
      <div className="vision-frame">
        {streamUrl && !streamFailed
          ? <img
              key={streamUrl}
              src={streamUrl}
              alt="YOLO 实时识别结果"
              onError={() => {
                setStreamFailed(true)
                setError("实时识别流加载失败。当前车端 fleet-agent 很可能还没有部署 /api/vision/stream.mjpg 接口。")
              }}
            />
          : <div className="vision-empty"><ImageIcon /><strong>{loading ? "正在读取视觉状态" : active ? (streamFailed ? "实时识别流不可用" : "正在等待识别首帧") : "实时识别已关闭"}</strong><span>{loading ? "请稍候" : active ? (streamFailed ? "车端当前只返回单帧识别结果，但没有提供实时识别视频流接口。" : "若长时间无画面，请检查车端摄像头、模型加载状态和视觉流接口。") : "开启后将在这里显示实时带框画面。"}</span></div>}
      </div>
      <aside className="vision-summary">
        <div className="vision-status-line"><Badge tone={modelReady ? "success" : "warning"}>{modelReady ? (active ? "实时识别中" : "模型已就绪") : "模型未就绪"}</Badge><span>{status?.target_labels?.length ? `关注 ${status.target_labels.join("、")}` : "未配置关注类别"}</span></div>
        {result ? <>
          <dl className="vision-metrics">
            <div><dt>模型</dt><dd>{result.model}</dd></div>
            <div><dt>推理耗时</dt><dd>{result.inference_ms.toFixed(1)} ms</dd></div>
            <div><dt>检测数量</dt><dd>{detections.length}</dd></div>
            <div><dt>识别时间</dt><dd>{new Date(result.captured_at * 1000).toLocaleTimeString()}</dd></div>
          </dl>
          {rangeSummary && <div className="vision-targets">
            <Badge tone={rangeSummary.measured_count > 0 ? "success" : "warning"}>测得距离 {rangeSummary.measured_count}/{rangeSummary.total_count}</Badge>
            <Badge tone="neutral">来源 {rangeSummary.sources.length ? rangeSummary.sources.map((item) => item === "depth_camera" ? "深度相机" : item === "lidar" ? "雷达" : item).join("、") : "无"}</Badge>
          </div>}
          <div className="vision-targets">{targets.map(([label, trigger]) => <Badge key={label} tone={trigger.detected ? "success" : "neutral"}>{label}：{trigger.detected ? `${trigger.count} 个` : "未发现"}</Badge>)}</div>
          <div className="vision-detections">
            <strong>检测明细</strong>
            {detections.length ? detections.map((item, index) => <div key={`${item.label}-${index}`}><span>{item.label}{item.range_m != null ? ` · ${item.range_m.toFixed(2)} m` : " · 未测得"}{item.range_source ? ` · ${item.range_source === "depth_camera" ? "深度相机" : item.range_source === "lidar" ? "雷达" : item.range_source}` : ""}</span><b>{Math.round(item.confidence * 100)}%</b></div>) : <span>当前画面没有达到置信度阈值的目标。</span>}
          </div>
        </> : <div className="vision-summary-empty">开启实时识别后，这里会持续显示最新一帧的类别、置信度和推理耗时。</div>}
      </aside>
    </div>
    {error && <InlineActionStatus tone="error" title="视觉识别异常" detail={error} onRetry={() => void refresh(true)} />}
  </Card>
}
