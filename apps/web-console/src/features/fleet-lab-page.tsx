import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { AlertTriangle, ArrowLeft, ArrowRight, CheckCircle2, CircleStop, Crosshair, FlaskConical, MapPinned, Play, Plus, RotateCcw, ShieldCheck, SkipForward, Trash2 } from "lucide-react"
import { useFeedback } from "../app/feedback"
import { useLiveStatus } from "../app/live-status"
import { useSession } from "../app/session"
import type { JsonObject, MapDeployment, MapPoint, Robot, StoredMap } from "../api/types"
import { MapCanvas, type MapOverlay } from "../components/map-canvas"
import { Badge, Button, Card, EmptyState, InlineActionStatus, LoadingBlock, PageHeader, fieldClass } from "../components/ui"
import {
  FLEET_LAB_COLORS,
  applyGoalId,
  beginBarrierRow,
  collisionWarnings,
  createBarrierRun,
  degreesToRadiansNormalized,
  evaluateBarrier,
  goalIdFromResponse,
  loadFleetLabDraft,
  markGoalSendFailed,
  navigationSnapshot,
  resizeRows,
  saveFleetLabDraft,
  waypointYaw,
  type BarrierRun,
  type FleetLabDraft,
} from "./fleet-lab-model"

type ActiveTarget = { kind: "pose"; robotId: string } | { kind: "waypoint"; robotId: string; rowIndex: number } | null

export function FleetLabPage() {
  const { api, demoMode } = useSession()
  const { connected, statuses, lastUpdatedAt, lastError } = useLiveStatus()
  const feedback = useFeedback()
  const [draft, setDraft] = useState<FleetLabDraft>(() => loadFleetLabDraft())
  const [robots, setRobots] = useState<Robot[] | null>(null)
  const [maps, setMaps] = useState<StoredMap[] | null>(null)
  const [deployments, setDeployments] = useState<MapDeployment[]>([])
  const [activeTarget, setActiveTarget] = useState<ActiveTarget>(null)
  const [run, setRunState] = useState<BarrierRun | null>(null)
  const [busy, setBusy] = useState("")
  const [error, setError] = useState("")
  const advancingRef = useRef(false)
  const blockedHandledRef = useRef(false)
  const runRef = useRef<BarrierRun | null>(null)
  const draftRef = useRef(draft)
  const selectedMap = maps?.find((item) => item.id === draft.mapId) || null

  const setRun = useCallback((value: BarrierRun | null) => { runRef.current = value; setRunState(value) }, [])
  useEffect(() => { draftRef.current = draft; saveFleetLabDraft(draft) }, [draft])

  const load = useCallback(async () => {
    if (!api) return
    try {
      const [nextRobots, nextMaps] = await Promise.all([api.robots(), api.maps()])
      setRobots(nextRobots); setMaps(nextMaps); setError("")
    } catch (cause) { setError(messageOf(cause, "实验数据加载失败")) }
  }, [api])
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!api || !draft.mapId) { setDeployments([]); return }
    api.deployments({ map_id: draft.mapId, state: "installed", limit: "200" }).then((page) => setDeployments(page.items)).catch((cause) => setError(messageOf(cause, "地图安装记录读取失败")))
  }, [api, draft.mapId])

  const liveRobots = useMemo(() => (robots || []).map((robot) => mergeLive(robot, statuses[robot.id])), [robots, statuses])
  const deploymentByRobot = useMemo(() => Object.fromEntries(deployments.map((item) => [item.robot_id, item])), [deployments])
  const eligibleRobots = liveRobots.filter((robot) => robot.enabled && robot.online && robot.capabilities.navigation !== false && deploymentByRobot[robot.id])
  const selectedRobots = draft.robotIds.map((id) => liveRobots.find((robot) => robot.id === id)).filter(Boolean) as Robot[]
  const warnings = collisionWarnings(draft)
  const editingLocked = Boolean(run && ["running", "blocked"].includes(run.phase)) || ["preparing", "recovery_required"].includes(draft.phase)

  const updateDraft = (change: (current: FleetLabDraft) => FleetLabDraft) => setDraft((current) => change(current))
  const chooseMap = (mapId: string) => {
    setActiveTarget(null); setRun(null); setDeployments([])
    setDraft((current) => ({ ...current, mapId, robotIds: [], rows: current.rows.map(() => ({})), initialPoses: {}, phase: "draft" }))
  }
  const toggleRobot = (robotId: string) => updateDraft((current) => {
    const selected = current.robotIds.includes(robotId)
    const robotIds = selected ? current.robotIds.filter((id) => id !== robotId) : [...current.robotIds, robotId]
    const rows = current.rows.map((row) => { const next = { ...row }; if (selected) delete next[robotId]; return next })
    const initialPoses = { ...current.initialPoses }; if (selected) delete initialPoses[robotId]
    return { ...current, robotIds, rows, initialPoses, phase: "draft" }
  })
  const reorderRobot = (index: number, delta: number) => updateDraft((current) => {
    const target = index + delta
    if (target < 0 || target >= current.robotIds.length) return current
    const robotIds = [...current.robotIds]; [robotIds[index], robotIds[target]] = [robotIds[target], robotIds[index]]
    return { ...current, robotIds }
  })
  const assignPoint = (point: MapPoint) => {
    if (!activeTarget || editingLocked) return
    updateDraft((current) => {
      if (activeTarget.kind === "pose") {
        return { ...current, initialPoses: { ...current.initialPoses, [activeTarget.robotId]: { ...point, yaw: current.initialPoses[activeTarget.robotId]?.yaw || 0, sent: false, confirmed: false } }, phase: "draft" }
      }
      const rows = current.rows.map((row, index) => index === activeTarget.rowIndex ? { ...row, [activeTarget.robotId]: point } : row)
      return { ...current, rows, phase: "draft" }
    })
  }
  const moveOverlay = (id: string, point: MapPoint) => {
    const [kind, robotId, rowValue] = id.split(":")
    if (kind === "pose") setActiveTarget({ kind: "pose", robotId })
    else setActiveTarget({ kind: "waypoint", robotId, rowIndex: Number(rowValue) })
    assignOverlayPoint(kind, robotId, Number(rowValue), point)
  }
  const assignOverlayPoint = (kind: string, robotId: string, rowIndex: number, point: MapPoint) => updateDraft((current) => {
    if (kind === "pose") return { ...current, initialPoses: { ...current.initialPoses, [robotId]: { ...current.initialPoses[robotId]!, ...point, sent: false, confirmed: false } }, phase: "draft" }
    return { ...current, rows: current.rows.map((row, index) => index === rowIndex ? { ...row, [robotId]: { ...row[robotId]!, ...point } } : row), phase: "draft" }
  })

  const overlays = useMemo<MapOverlay[]>(() => {
    const values: MapOverlay[] = []
    draft.robotIds.forEach((robotId, robotIndex) => {
      const color = FLEET_LAB_COLORS[robotIndex % FLEET_LAB_COLORS.length]
      const pose = draft.initialPoses[robotId]
      if (pose) values.push({ id: `pose:${robotId}`, point: pose, label: `${letter(robotIndex)}S`, color, yaw: pose.yaw, state: "initial" })
      draft.rows.forEach((row, rowIndex) => {
        const point = row[robotId]
        if (!point) return
        values.push({ id: `waypoint:${robotId}:${rowIndex}`, point, label: `${letter(robotIndex)}${rowIndex + 1}`, color, yaw: waypointYaw(draft, robotId, rowIndex), trackId: robotId, order: rowIndex, state: run?.rowIndex === rowIndex ? run.robots[robotId]?.state || "idle" : run && rowIndex < run.rowIndex ? "arrived" : "idle" })
      })
    })
    return values
  }, [draft, run])

  const safeHold = useCallback(async (robotIds = draftRef.current.robotIds) => {
    if (!api) return
    await Promise.allSettled(robotIds.flatMap((robotId) => [api.navigationCancel(robotId), api.robotAction(robotId, "control/stop")]))
  }, [api])

  const prepare = async () => {
    if (!api || !selectedMap) return
    const problem = validateDraft(draft, selectedRobots, deploymentByRobot, statuses, false)
    if (problem) { setError(problem); return }
    setBusy("prepare"); setError(""); updateDraft((current) => ({ ...current, phase: "preparing" }))
    try {
      for (const robotId of draft.robotIds) {
        const installedName = deploymentByRobot[robotId].installed_name!
        const navigation = navigationSnapshot(statuses[robotId])
        if (navigation.ready && navigation.currentMap !== installedName) throw new Error(`${robotId} 已在其他地图 ${navigation.currentMap} 上运行，请先停止 Nav2`)
        if (!navigation.ready) await api.navigationStart(robotId, installedName)
        const pose = draft.initialPoses[robotId]!
        await api.navigationInitialPose(robotId, { map_name: installedName, x: pose.x, y: pose.y, yaw: degreesToRadiansNormalized(pose.yaw) })
        setDraft((current) => ({ ...current, initialPoses: { ...current.initialPoses, [robotId]: { ...current.initialPoses[robotId]!, sent: true, confirmed: false } } }))
      }
      updateDraft((current) => ({ ...current, phase: "draft" }))
      feedback.notify({ title: "Nav2 与初始位姿已下发", description: "请观察每辆车定位稳定后逐一确认", tone: "success" })
    } catch (cause) {
      const message = messageOf(cause, "准备失败")
      setError(message); updateDraft((current) => ({ ...current, phase: "draft" }))
      await safeHold()
    } finally { setBusy("") }
  }

  const confirmLocalization = (robotId: string, confirmed: boolean) => updateDraft((current) => {
    const initialPoses = { ...current.initialPoses, [robotId]: { ...current.initialPoses[robotId]!, confirmed } }
    const ready = current.robotIds.every((id) => initialPoses[id]?.sent && initialPoses[id]?.confirmed)
    return { ...current, initialPoses, phase: ready ? "ready" : "draft" }
  })

  const dispatchRow = useCallback(async (base: BarrierRun, rowIndex: number, targetIds?: string[]) => {
    if (!api) return
    const currentDraft = draftRef.current
    const ids = targetIds || currentDraft.robotIds
    let next = beginBarrierRow({ ...base, rowIndex }, currentDraft.robotIds, Date.now(), Boolean(targetIds))
    setRun(next); blockedHandledRef.current = false
    const results = await Promise.allSettled(ids.map(async (robotId) => {
      const point = currentDraft.rows[rowIndex][robotId]!
      const installedName = deploymentByRobot[robotId].installed_name!
      const response = await api.navigationGoal(robotId, { map_name: installedName, x: point.x, y: point.y, yaw: degreesToRadiansNormalized(waypointYaw(currentDraft, robotId, rowIndex)) })
      const goalId = goalIdFromResponse(response)
      if (!goalId) throw new Error(`${robotId} 未返回 goal_id`)
      return { robotId, goalId }
    }))
    results.forEach((result, index) => {
      const robotId = ids[index]
      if (result.status === "fulfilled") next = applyGoalId(next, robotId, result.value.goalId)
      else next = markGoalSendFailed(next, robotId, messageOf(result.reason, `${robotId} 目标发送失败`))
    })
    setRun(next)
    if (next.phase === "blocked") { blockedHandledRef.current = true; await safeHold(); feedback.notify({ title: "联动已阻断", description: next.error || undefined, tone: "error" }) }
  }, [api, deploymentByRobot, feedback, safeHold, setRun])

  const start = async () => {
    const problem = validateDraft(draft, selectedRobots, deploymentByRobot, statuses, true)
    if (problem) { setError(problem); return }
    if (warnings.length && !await feedback.confirm({ title: "存在目标距离警告", description: `${warnings.length} 组同时间片目标小于 ${draft.collisionDistance.toFixed(1)}m，仍要启动吗？`, confirmLabel: "确认启动", tone: "warning" })) return
    if (!await feedback.confirm({ title: "启动多车联动？", description: `${draft.robotIds.length} 辆车将执行 ${draft.rows.length} 个屏障时间片，请保持页面打开。`, confirmLabel: "开始执行" })) return
    setError(""); updateDraft((current) => ({ ...current, phase: "running" }))
    const initial = createBarrierRun(draft.robotIds)
    await dispatchRow(initial, 0)
  }

  useEffect(() => {
    if (!run || run.phase !== "running") return
    const timer = window.setInterval(() => {
      const current = runRef.current
      if (!current || current.phase !== "running") return
      const result = evaluateBarrier(current, draftRef.current.robotIds, statuses, lastUpdatedAt, draftRef.current.timeoutS, Date.now(), draftRef.current.staleAfterS)
      setRun(result.run)
      if (result.outcome === "blocked" && !blockedHandledRef.current) {
        blockedHandledRef.current = true
        void safeHold().then(() => feedback.notify({ title: "联动已阻断", description: result.run.error || undefined, tone: "error" }))
      }
      if (result.outcome === "advance" && !advancingRef.current) {
        advancingRef.current = true
        window.setTimeout(() => {
          const latest = runRef.current
          if (!latest || latest.phase !== "running") { advancingRef.current = false; return }
          const nextRow = latest.rowIndex + 1
          if (nextRow >= draftRef.current.rows.length) {
            const completed = { ...latest, phase: "completed" as const, rowStartedAt: null }
            setRun(completed); setDraft((value) => ({ ...value, phase: "completed" }))
            void Promise.allSettled(draftRef.current.robotIds.map((robotId) => api!.robotAction(robotId, "control/stop")))
            feedback.notify({ title: "多车联动已完成", description: "全部车辆通过所有时间片", tone: "success" })
          } else void dispatchRow({ ...latest, rowIndex: nextRow }, nextRow)
          advancingRef.current = false
        }, 500)
      }
    }, 250)
    return () => window.clearInterval(timer)
  }, [api, dispatchRow, feedback, lastUpdatedAt, run?.phase, safeHold, setRun, statuses])

  const retry = async () => {
    if (!run) return
    const ids = draft.robotIds.filter((robotId) => run.robots[robotId]?.state !== "arrived")
    if (!ids.length) return
    blockedHandledRef.current = false
    await dispatchRow(run, run.rowIndex, ids)
  }
  const skip = async () => {
    if (!run || !await feedback.confirm({ title: "跳过当前时间片？", description: "未到达车辆会从当前位置直接规划到下一行目标。", confirmLabel: "确认跳过", tone: "warning" })) return
    const skipped = { ...run, robots: Object.fromEntries(draft.robotIds.map((id) => [id, { ...run.robots[id], state: "skipped" as const }])) }
    const nextRow = run.rowIndex + 1
    if (nextRow >= draft.rows.length) { setRun({ ...skipped, phase: "completed" }); updateDraft((value) => ({ ...value, phase: "completed" })); return }
    await dispatchRow(skipped, nextRow)
  }
  const abort = async () => {
    setBusy("abort"); await safeHold(); setRun((runRef.current && { ...runRef.current, phase: "aborted" }) || null); updateDraft((value) => ({ ...value, phase: "aborted" })); setBusy("")
  }
  const recover = async () => {
    setBusy("recover"); await safeHold(); setRun(null); updateDraft((value) => ({ ...value, phase: "draft" })); setBusy("")
  }

  if (!robots || !maps) return <LoadingBlock label="正在加载联动实验台" />
  return <div className="page-enter fleet-lab-page">
    <PageHeader eyebrow="EXPERIMENTAL BARRIER CONTROL" title="多车联动实验" description="以地图点位作为屏障时间片：同一行全部到达后，才会推进下一行。" actions={<Badge tone="warning"><FlaskConical size={14} />实验性</Badge>} />
    <div className="experiment-warning"><AlertTriangle /><div><strong>前端在线协调</strong><span>请保持页面和网络连接。关闭或刷新页面不会可靠接管正在移动的车辆，异常时使用右上角全局停车。</span></div></div>
    {!connected && <InlineActionStatus tone="error" title="实时状态链路未连接" detail={lastError || "禁止启动新的联动实验"} />}
    {draft.phase === "recovery_required" && <InlineActionStatus tone="warning" title="检测到未结束的实验草稿" detail="必须先取消全部目标并停车，不能从浏览器缓存自动续跑。" onRetry={() => void recover()} />}
    {error && <InlineActionStatus tone="error" title="实验台需要处理" detail={error} onRetry={() => { setError(""); void load() }} />}

    <Card className="panel lab-setup-panel">
      <div className="panel-head"><div><h2>1. 地图与车辆编组</h2><p>只允许选择已安装同一中心地图版本的在线车辆</p></div><MapPinned /></div>
      <div className="lab-setup-grid"><label><span>中心地图版本</span><select className={fieldClass} value={draft.mapId} disabled={editingLocked} onChange={(event) => chooseMap(event.target.value)}><option value="">选择地图</option>{maps.map((map) => <option value={map.id} key={map.id}>{map.logical_name} v{map.version} · {map.width}×{map.height}</option>)}</select></label><label><span>时间片行数</span><input className={fieldClass} type="number" min="1" max="50" value={draft.rows.length} disabled={editingLocked} onChange={(event) => updateDraft((value) => resizeRows(value, Number(event.target.value)))} /></label><label><span>单行超时 s</span><input className={fieldClass} type="number" min="10" max="1800" value={draft.timeoutS} disabled={editingLocked} onChange={(event) => updateDraft((value) => ({ ...value, timeoutS: Number(event.target.value) }))} /></label><label><span>状态静默阈值 s</span><input className={fieldClass} type="number" min="5" max="60" value={draft.staleAfterS} disabled={editingLocked} onChange={(event) => updateDraft((value) => ({ ...value, staleAfterS: Number(event.target.value) }))} /></label><label><span>距离警告 m</span><input className={fieldClass} type="number" min="0.1" max="10" step="0.1" value={draft.collisionDistance} disabled={editingLocked} onChange={(event) => updateDraft((value) => ({ ...value, collisionDistance: Number(event.target.value) }))} /></label></div>
      {selectedMap && <div className="lab-robot-picker">{eligibleRobots.length ? eligibleRobots.map((robot) => { const selected = draft.robotIds.includes(robot.id); const index = draft.robotIds.indexOf(robot.id); return <button type="button" className={selected ? "selected" : ""} disabled={editingLocked} key={robot.id} onClick={() => toggleRobot(robot.id)}><span className="lab-color" style={{ background: selected ? FLEET_LAB_COLORS[index % FLEET_LAB_COLORS.length] : undefined }} /><span><strong>{robot.name}</strong><small>{robot.id} · {deploymentByRobot[robot.id]?.installed_name}</small></span>{selected && <CheckCircle2 />}</button> }) : <EmptyState title="没有符合条件的车辆" detail="车辆必须在线、启用并已安装当前地图" />}</div>}
    </Card>

    {selectedMap && draft.robotIds.length > 0 && <section className="fleet-lab-workspace">
      <Card className="panel fleet-lab-map"><div className="panel-head"><div><h2>2. 统一地图编排</h2><p>{activeTarget ? activeTarget.kind === "pose" ? "点击地图设置车辆起始位姿" : `点击地图设置时间片 ${activeTarget.rowIndex + 1}` : "先选择右侧起点或时间片单元格"}</p></div><Crosshair /></div><MapCanvas className="fleet-lab-canvas" fit="width" api={api!} map={selectedMap} overlays={overlays} onPoint={assignPoint} onOverlayMove={moveOverlay} onOverlaySelect={(id) => { const [kind, robotId, row] = id.split(":"); setActiveTarget(kind === "pose" ? { kind: "pose", robotId } : { kind: "waypoint", robotId, rowIndex: Number(row) }) }} /></Card>
      <div className="fleet-lab-side">
        <Card className="panel"><div className="panel-head"><div><h2>车辆准备</h2><p>先启动 Nav2，再下发并人工确认定位</p></div><ShieldCheck /></div><div className="lab-vehicle-list">{selectedRobots.map((robot, index) => { const pose = draft.initialPoses[robot.id]; const navigation = navigationSnapshot(statuses[robot.id]); return <article key={robot.id}><div className="lab-vehicle-head"><span className="lab-color" style={{ background: FLEET_LAB_COLORS[index % FLEET_LAB_COLORS.length] }} /><div><strong>{letter(index)} · {robot.name}</strong><small>{navigation.ready ? `Nav2 ${navigation.currentMap}` : "Nav2 未就绪"}</small></div><div className="lab-reorder"><Button size="sm" variant="ghost" disabled={editingLocked || index === 0} onClick={() => reorderRobot(index, -1)}><ArrowLeft /></Button><Button size="sm" variant="ghost" disabled={editingLocked || index === draft.robotIds.length - 1} onClick={() => reorderRobot(index, 1)}><ArrowRight /></Button></div></div><Button className="w-full" variant={activeTarget?.kind === "pose" && activeTarget.robotId === robot.id ? "primary" : "secondary"} disabled={editingLocked} onClick={() => setActiveTarget({ kind: "pose", robotId: robot.id })}><Crosshair />{pose ? `起点 x ${pose.x.toFixed(2)} / y ${pose.y.toFixed(2)}` : "在地图设置起点"}</Button>{pose && <div className="pose-row"><label>Yaw °<input className={fieldClass} type="number" value={pose.yaw} disabled={editingLocked} onChange={(event) => updateDraft((value) => ({ ...value, initialPoses: { ...value.initialPoses, [robot.id]: { ...value.initialPoses[robot.id]!, yaw: Number(event.target.value), sent: false, confirmed: false } } }))} /></label><label className="switch-field"><input type="checkbox" checked={pose.confirmed} disabled={!pose.sent || editingLocked} onChange={(event) => confirmLocalization(robot.id, event.target.checked)} /><span>定位稳定</span></label></div>}</article> })}</div><Button className="w-full" variant="primary" loading={busy === "prepare"} loadingText="正在准备所有车辆" disabled={editingLocked || draft.robotIds.length < 2 || draft.robotIds.some((id) => !draft.initialPoses[id])} onClick={() => void prepare()}><RotateCcw />启动/校验 Nav2 并下发初始位姿</Button></Card>

        <Card className="panel lab-matrix-card"><div className="panel-head"><div><h2>时间片矩阵</h2><p>{draft.robotIds.length} 列 × {draft.rows.length} 行</p></div><Plus /></div><div className="lab-matrix-scroll"><table className="lab-matrix"><thead><tr><th>片</th>{selectedRobots.map((robot, index) => <th key={robot.id}><span className="lab-color" style={{ background: FLEET_LAB_COLORS[index % FLEET_LAB_COLORS.length] }} />{letter(index)}</th>)}</tr></thead><tbody>{draft.rows.map((row, rowIndex) => <tr key={rowIndex} className={run?.rowIndex === rowIndex ? "current" : ""}><th>{rowIndex + 1}</th>{draft.robotIds.map((robotId, robotIndex) => { const point = row[robotId]; const state = run?.rowIndex === rowIndex ? run.robots[robotId]?.state : run && rowIndex < run.rowIndex ? "arrived" : "idle"; const selected = activeTarget?.kind === "waypoint" && activeTarget.robotId === robotId && activeTarget.rowIndex === rowIndex; return <td key={robotId}><button type="button" className={selected ? "selected" : ""} disabled={editingLocked} onClick={() => setActiveTarget({ kind: "waypoint", robotId, rowIndex })}><b>{letter(robotIndex)}{rowIndex + 1}</b><span>{point ? `${point.x.toFixed(2)}, ${point.y.toFixed(2)}` : "点击选点"}</span>{state !== "idle" && <small className={`cell-${state}`}>{cellLabel(state)}{run?.robots[robotId]?.distanceRemaining !== null && run?.robots[robotId]?.distanceRemaining !== undefined ? ` ${run.robots[robotId].distanceRemaining!.toFixed(1)}m` : ""}</small>}</button>{point && !editingLocked && <div className="cell-yaw"><input aria-label={`${letter(robotIndex)}${rowIndex + 1} yaw`} type="number" placeholder="自动" value={point.yawOverride ?? ""} onChange={(event) => updateDraft((value) => ({ ...value, rows: value.rows.map((item, index) => index === rowIndex ? { ...item, [robotId]: { ...item[robotId]!, yawOverride: event.target.value === "" ? undefined : Number(event.target.value) } } : item) }))} /><span>° {Math.round(waypointYaw(draft, robotId, rowIndex))}</span><Button size="sm" variant="ghost" aria-label="删除点位" onClick={() => updateDraft((value) => ({ ...value, rows: value.rows.map((item, index) => { if (index !== rowIndex) return item; const next = { ...item }; delete next[robotId]; return next }) }))}><Trash2 /></Button></div>}</td> })}</tr>)}</tbody></table></div><Button variant="ghost" disabled={editingLocked} onClick={() => updateDraft((value) => resizeRows(value, value.rows.length + 1))}><Plus />增加时间片</Button></Card>

        {warnings.length > 0 && <InlineActionStatus tone="warning" title={`${warnings.length} 组目标距离过近`} detail={`最小 ${Math.min(...warnings.map((item) => item.distance)).toFixed(2)}m，启动时仍可人工确认继续`} />}
        <Card className="panel lab-run-panel"><div className="panel-head"><div><h2>3. 屏障执行</h2><p>到齐才推进，异常全组停车</p></div><FlaskConical /></div>{run && <div className="lab-run-state"><Badge tone={run.phase === "blocked" ? "danger" : run.phase === "completed" ? "success" : "info"}>{run.phase}</Badge><strong>时间片 {Math.min(run.rowIndex + 1, draft.rows.length)} / {draft.rows.length}</strong>{run.error && <span>{run.error}</span>}</div>}<div className="stack-actions"><Button variant="primary" disabled={!connected || editingLocked || draft.robotIds.length < 2 || draft.phase !== "ready"} onClick={() => void start()}><Play />启动联动</Button>{run?.phase === "blocked" && <><Button onClick={() => void retry()}><RotateCcw />重试未到达车辆</Button><Button variant="warning" onClick={() => void skip()}><SkipForward />跳过当前行</Button></>}<Button variant="danger" loading={busy === "abort"} loadingText="正在停车" disabled={!run || ["completed", "aborted"].includes(run.phase)} onClick={() => void abort()}><CircleStop />终止并全组停车</Button></div>{draft.phase !== "ready" && <InlineActionStatus tone="info" title="尚未满足启动条件" detail="至少选择两辆车，完成全部点位，并为每辆车确认初始定位。" />}</Card>
      </div>
    </section>}
    {selectedMap && draft.robotIds.length === 0 && <EmptyState title="请选择参与车辆" detail="地图安装记录决定哪些在线车辆可加入本次实验" />}
    {!selectedMap && <EmptyState title="先选择统一地图" detail="所有参与车辆必须安装同一个中心地图版本" />}
    {demoMode && <div className="demo-floating-note">DEMO 会让车辆以不同速度到达，以验证时间片屏障。</div>}
  </div>
}

function validateDraft(draft: FleetLabDraft, robots: Robot[], deployments: Record<string, MapDeployment>, statuses: Record<string, JsonObject>, requireReady: boolean) {
  if (!draft.mapId) return "请选择中心地图"
  if (draft.robotIds.length < 2) return "至少选择两辆车辆"
  for (const robotId of draft.robotIds) {
    const robot = robots.find((item) => item.id === robotId)
    if (!robot?.enabled || !robot.online) return `${robotId} 当前不可用或离线`
    if (!deployments[robotId]?.installed_name) return `${robotId} 未安装当前地图`
    const status = (statuses[robotId]?.status || {}) as JsonObject
    const mode = String(status.mode || "UNKNOWN")
    const patrol = (status.patrol || {}) as JsonObject
    const navigation = navigationSnapshot(statuses[robotId])
    if (mode === "EMERGENCY_STOP" || mode === "MAPPING" || mode === "SAVING_MAP" || ["running", "paused", "pausing"].includes(String(patrol.state))) return `${robotId} 当前模式 ${mode} 存在冲突`
    if (navigation.activeGoalId) return `${robotId} 已有活动导航目标 ${navigation.activeGoalId}`
    if (!draft.initialPoses[robotId]) return `${robotId} 尚未设置起始位姿`
    if (requireReady) {
      if (!navigation.ready || navigation.currentMap !== deployments[robotId].installed_name) return `${robotId} Nav2 未在目标地图上就绪`
      if (!draft.initialPoses[robotId]?.sent || !draft.initialPoses[robotId]?.confirmed) return `${robotId} 初始定位尚未确认`
    }
  }
  if (requireReady && draft.rows.some((row) => draft.robotIds.some((robotId) => !row[robotId]))) return "每个时间片必须为所有车辆设置目标点"
  return ""
}

function mergeLive(robot: Robot, live?: JsonObject): Robot {
  return live ? { ...robot, online: Boolean(live.online), runtime_status: (live.status as JsonObject) || robot.runtime_status, last_seen: String(live.last_seen || robot.last_seen || "") || null } : robot
}
function messageOf(cause: unknown, fallback: string) { return cause instanceof Error ? cause.message : fallback }
function letter(index: number) { return String.fromCharCode(65 + (index % 26)) }
function cellLabel(state: string) { return state === "sending" ? "发送中" : state === "active" ? "行进中" : state === "arrived" ? "已到达" : state === "failed" ? "失败" : state === "skipped" ? "已跳过" : state }
