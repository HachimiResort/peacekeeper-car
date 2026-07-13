import { useCallback, useEffect, useRef, useState } from "react"
import { ImageIcon, ScanSearch } from "lucide-react"
import type { MissionApi, VisionCapture, VisionStatus } from "../api/types"
import { Badge, Button, Card, InlineActionStatus } from "./ui"

export function VisionDetectionCard({ api, robotId, available }: { api: MissionApi; robotId: string; available: boolean }) {
  const [status, setStatus] = useState<VisionStatus | null>(null)
  const [result, setResult] = useState<VisionCapture | null>(null)
  const [imageUrl, setImageUrl] = useState("")
  const [loading, setLoading] = useState(true)
  const [capturing, setCapturing] = useState(false)
  const [error, setError] = useState("")
  const imageUrlRef = useRef("")

  const replaceImage = useCallback((blob: Blob) => {
    const nextUrl = URL.createObjectURL(blob)
    if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current)
    imageUrlRef.current = nextUrl
    setImageUrl(nextUrl)
  }, [])

  useEffect(() => {
    let cancelled = false
    const loadLatest = async () => {
      setLoading(true)
      try {
        const nextStatus = await api.visionStatus(robotId)
        if (cancelled) return
        setStatus(nextStatus)
        if (nextStatus.latest_available) {
          const [nextResult, blob] = await Promise.all([api.visionLatest(robotId), api.visionLatestImage(robotId)])
          if (cancelled) return
          setResult(nextResult)
          replaceImage(blob)
        }
        setError(nextStatus.last_error || "")
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "视觉状态读取失败")
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void loadLatest()
    return () => {
      cancelled = true
      if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current)
      imageUrlRef.current = ""
    }
  }, [api, replaceImage, robotId])

  const capture = async () => {
    setCapturing(true)
    setError("")
    try {
      const nextResult = await api.visionCapture(robotId)
      const blob = await api.visionLatestImage(robotId)
      setResult(nextResult)
      replaceImage(blob)
      setStatus((current) => current ? { ...current, latest_available: true, last_result: nextResult, last_error: null } : current)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "YOLO 识别失败")
    } finally {
      setCapturing(false)
    }
  }

  const detections = result?.detections || []
  const targets = Object.entries(result?.triggers || {})
  const modelReady = Boolean(status?.enabled && status.model_loaded)

  return <Card className="panel span-2 vision-panel">
    <div className="panel-head">
      <div><h2>YOLO 单帧识别</h2><p>按需抓取一帧并显示检测框，不持续占用 GPU 推理</p></div>
      <Button variant="primary" loading={capturing} loadingText="正在识别" disabled={!available || !modelReady || loading} onClick={() => void capture()}><ScanSearch />识别当前画面</Button>
    </div>
    {!available && <InlineActionStatus tone="warning" title="视觉识别暂不可用" detail="车辆离线或已停用，恢复在线后可执行识别。" />}
    {status && !status.enabled && <InlineActionStatus tone="warning" title="YOLO 未启用" detail="请先在 fleet-agent 配置中启用 vision。" />}
    {status?.enabled && !status.model_loaded && <InlineActionStatus tone="warning" title="模型未就绪" detail="YOLO worker 应在启动阶段加载模型，请检查工作进程和 TensorRT 引擎日志。" />}
    <div className="vision-workspace">
      <div className="vision-frame">
        {imageUrl
          ? <img src={imageUrl} alt="YOLO 带框识别结果" />
          : <div className="vision-empty"><ImageIcon /><strong>{loading ? "正在读取最近结果" : "还没有识别图片"}</strong><span>{loading ? "请稍候" : "点击“识别当前画面”生成一张带检测框的图片。"}</span></div>}
      </div>
      <aside className="vision-summary">
        <div className="vision-status-line"><Badge tone={modelReady ? "success" : "warning"}>{modelReady ? "模型已就绪" : "模型未就绪"}</Badge><span>{status?.target_labels?.length ? `关注 ${status.target_labels.join("、")}` : "未配置关注类别"}</span></div>
        {result ? <>
          <dl className="vision-metrics">
            <div><dt>模型</dt><dd>{result.model}</dd></div>
            <div><dt>推理耗时</dt><dd>{result.inference_ms.toFixed(1)} ms</dd></div>
            <div><dt>检测数量</dt><dd>{detections.length}</dd></div>
            <div><dt>识别时间</dt><dd>{new Date(result.captured_at * 1000).toLocaleTimeString()}</dd></div>
          </dl>
          <div className="vision-targets">{targets.map(([label, trigger]) => <Badge key={label} tone={trigger.detected ? "success" : "neutral"}>{label}：{trigger.detected ? `${trigger.count} 个` : "未发现"}</Badge>)}</div>
          <div className="vision-detections">
            <strong>检测明细</strong>
            {detections.length ? detections.map((item, index) => <div key={`${item.label}-${index}`}><span>{item.label}</span><b>{Math.round(item.confidence * 100)}%</b></div>) : <span>当前画面没有达到置信度阈值的目标。</span>}
          </div>
        </> : <div className="vision-summary-empty">执行一次识别后，这里会显示类别、置信度和推理耗时。</div>}
      </aside>
    </div>
    {error && <InlineActionStatus tone="error" title="视觉识别异常" detail={error} onRetry={() => void capture()} />}
  </Card>
}
