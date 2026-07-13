import { useEffect, useState } from "react"
import { Music2, Play, Square, Volume2 } from "lucide-react"
import type { AudioAsset, AudioStatus, MissionApi, Robot } from "../api/types"
import { Button, Card, fieldClass } from "../components/ui"

export function FleetAudioDock({ api, robots, onError, onSuccess }: {
  api: MissionApi
  robots: Robot[]
  onError(message: string): void
  onSuccess(message: string): void
}) {
  const [robotId, setRobotId] = useState(robots[0]?.id || "")
  const [assets, setAssets] = useState<AudioAsset[]>([])
  const [asset, setAsset] = useState("")
  const [volume, setVolume] = useState(80)
  const [loop, setLoop] = useState(false)
  const [statuses, setStatuses] = useState<Record<string, AudioStatus>>({})
  const [busy, setBusy] = useState("")

  useEffect(() => {
    if (!robots.some((robot) => robot.id === robotId)) setRobotId(robots[0]?.id || "")
  }, [robotId, robots])

  useEffect(() => {
    if (!robots.length) { setStatuses({}); return }
    let active = true
    const refresh = async () => {
      const results = await Promise.all(robots.map(async (robot) => [robot.id, await api.audioStatus(robot.id)] as const))
      if (active) setStatuses(Object.fromEntries(results))
    }
    void refresh().catch(() => undefined)
    const timer = window.setInterval(() => { void refresh().catch(() => undefined) }, 2500)
    return () => { active = false; window.clearInterval(timer) }
  }, [api, robots])

  useEffect(() => {
    if (!robotId) { setAssets([]); setAsset(""); return }
    let active = true
    api.audioAssets(robotId)
      .then((nextAssets) => {
        if (!active) return
        setAssets(nextAssets)
        setAsset((current) => nextAssets.some((item) => item.name === current) ? current : nextAssets[0]?.name || "")
      })
      .catch((cause) => { if (active) onError(messageOf(cause, "读取车辆音频资产失败")) })
    return () => { active = false }
  }, [api, onError, robotId])

  const refreshOne = async (id: string) => {
    const next = await api.audioStatus(id)
    setStatuses((current) => ({ ...current, [id]: next }))
  }
  const play = async () => {
    if (!robotId || !asset) return
    setBusy("play")
    try {
      await api.robotAction(robotId, "audio/play", { asset, volume, loop })
      await refreshOne(robotId)
      onSuccess(`已请求 ${robots.find((robot) => robot.id === robotId)?.name || robotId} 播放 ${asset}`)
    } catch (cause) { onError(messageOf(cause, "音频播放指令失败")) } finally { setBusy("") }
  }
  const stop = async () => {
    if (!robotId) return
    setBusy("stop")
    try {
      await api.robotAction(robotId, "audio/stop")
      await refreshOne(robotId)
    } catch (cause) { onError(messageOf(cause, "停止音频失败")) } finally { setBusy("") }
  }
  const stopAll = async () => {
    setBusy("stop-all")
    const results = await Promise.allSettled(robots.map((robot) => api.robotAction(robot.id, "audio/stop")))
    await Promise.allSettled(robots.map((robot) => refreshOne(robot.id)))
    const failed = results.filter((item) => item.status === "rejected").length
    if (failed) onError(`${failed} 辆参与车辆停止音频失败`)
    else onSuccess("已向全部参与车辆发送停止音频指令")
    setBusy("")
  }

  const playing = robots.filter((robot) => statuses[robot.id]?.playing)
  return <Card className="fleet-audio-dock">
    <div className="fleet-audio-head"><div><span>演出音频</span><strong><Music2 size={15} />手动控制</strong></div><small>不随时间片自动切换</small></div>
    <label>控制车辆<select className={fieldClass} value={robotId} disabled={!robots.length || Boolean(busy)} onChange={(event) => setRobotId(event.target.value)}>{robots.map((robot) => <option key={robot.id} value={robot.id}>{robot.name} · {robot.id}</option>)}</select></label>
    <label>已安装资产<select className={fieldClass} value={asset} disabled={!assets.length || Boolean(busy)} onChange={(event) => setAsset(event.target.value)}><option value="">{assets.length ? "选择音频" : "该车辆暂无音频资产"}</option>{assets.map((item) => <option key={item.name} value={item.name}>{item.name} · {formatBytes(item.bytes)}</option>)}</select></label>
    {!assets.length && <p className="fleet-audio-hint">请到车辆详情页上传音频资产后再播放。</p>}
    <div className="fleet-audio-options"><label><span><Volume2 size={13} />音量 {volume}</span><input type="range" min="0" max="100" value={volume} disabled={Boolean(busy)} onChange={(event) => setVolume(Number(event.target.value))} /></label><label className="switch-field"><input type="checkbox" checked={loop} disabled={Boolean(busy)} onChange={(event) => setLoop(event.target.checked)} /><span>循环播放</span></label></div>
    <div className="fleet-audio-actions"><Button size="sm" variant="primary" loading={busy === "play"} loadingText="播放中" disabled={!robotId || !asset || Boolean(busy)} onClick={() => void play()}><Play />播放</Button><Button size="sm" loading={busy === "stop"} loadingText="停止中" disabled={!robotId || Boolean(busy)} onClick={() => void stop()}><Square />停止本车</Button></div>
    <div className="fleet-audio-playing">{playing.length ? <>正在播放：{playing.map((robot) => `${robot.name} · ${statuses[robot.id].asset || "未知"}`).join("；")}</> : "参与车辆当前没有播放音频"}</div>
    <Button size="sm" variant="danger" loading={busy === "stop-all"} loadingText="停止中" disabled={!robots.length || Boolean(busy)} onClick={() => void stopAll()}><Square />停止全部参与车辆音频</Button>
  </Card>
}

function formatBytes(value: number) {
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function messageOf(cause: unknown, fallback: string) {
  return cause instanceof Error ? cause.message : fallback
}
