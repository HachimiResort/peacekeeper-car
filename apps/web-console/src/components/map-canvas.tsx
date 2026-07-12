import { useCallback, useEffect, useRef, useState } from "react"
import { Crosshair, Maximize2, Minus, Plus, RotateCcw } from "lucide-react"
import type { MapPoint, MissionApi, StoredMap } from "../api/types"
import { pixelToMap } from "../api/client"
import { cn } from "../lib/cn"

type View = { zoom: number; x: number; y: number }
type StageSize = { width: number; height: number }

export interface MapOverlay {
  id: string
  point: MapPoint
  label: string
  color: string
  trackId?: string
  order?: number
  yaw?: number
  state?: "idle" | "sending" | "active" | "arrived" | "failed" | "skipped" | "initial"
}

const MIN_ZOOM = 0.5
const MAX_ZOOM = 4

function constrainView(view: View, frame: DOMRect, stage: StageSize): View {
  const scaledWidth = stage.width * view.zoom
  const scaledHeight = stage.height * view.zoom
  const x = scaledWidth <= frame.width
    ? (frame.width - scaledWidth) / 2
    : Math.min(0, Math.max(frame.width - scaledWidth, view.x))
  const y = scaledHeight <= frame.height
    ? (frame.height - scaledHeight) / 2
    : Math.min(0, Math.max(frame.height - scaledHeight, view.y))
  return { ...view, x, y }
}

export function MapCanvas({ api, map, onPoint, onPointMove, onOverlayMove, onOverlaySelect, onYawChange, points = [], overlays, interactive = true, className, fit = "contain" }: { api: MissionApi; map: StoredMap; onPoint?(point: MapPoint): void; onPointMove?(index: number, point: MapPoint): void; onOverlayMove?(id: string, point: MapPoint): void; onOverlaySelect?(id: string): void; onYawChange?(yaw: number): void; points?: MapPoint[]; overlays?: MapOverlay[]; interactive?: boolean; className?: string; fit?: "contain" | "width" }) {
  const [url, setUrl] = useState("")
  const [stageSize, setStageSize] = useState<StageSize | null>(null)
  const [view, setView] = useState<View>({ zoom: 1, x: 0, y: 0 })
  const [panning, setPanning] = useState(false)
  const [focusedMarker, setFocusedMarker] = useState<number | null>(null)
  const [directionMarker, setDirectionMarker] = useState<number | null>(null)
  const [movingMarker, setMovingMarker] = useState<number | null>(null)
  const [directionYaw, setDirectionYaw] = useState<number | null>(null)
  const [markerYaws, setMarkerYaws] = useState<Record<number, number>>({})
  const canvasRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<{ pointerId: number; startX: number; startY: number; startView: View; moved: boolean } | null>(null)
  const directionDragRef = useRef<{ pointerId: number; index: number; startX: number; startY: number; moved: boolean } | null>(null)
  const moveDragRef = useRef<{ pointerId: number; index: number } | null>(null)
  const directionYawRef = useRef<number | null>(null)
  const dismissCanvasPointerRef = useRef<number | null>(null)
  const suppressMarkerClickRef = useRef<number | null>(null)

  useEffect(() => {
    let active = true
    let objectUrl = ""
    api.mapPreview(map.id).then((blob) => {
      if (!active) return
      objectUrl = URL.createObjectURL(blob)
      setUrl(objectUrl)
    })
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [api, map.id])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const measure = () => {
      const frame = canvas.getBoundingClientRect()
      if (frame.width <= 0 || frame.height <= 0) return
      const mapRatio = map.width / map.height
      const size = fit === "width"
        ? { width: frame.width, height: frame.width / mapRatio }
        : frame.width / frame.height >= mapRatio
          ? { width: frame.height * mapRatio, height: frame.height }
          : { width: frame.width, height: frame.width / mapRatio }
      setStageSize(size)
      setView({ zoom: 1, x: (frame.width - size.width) / 2, y: (frame.height - size.height) / 2 })
    }
    measure()
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure)
    observer?.observe(canvas)
    window.addEventListener("resize", measure)
    return () => { observer?.disconnect(); window.removeEventListener("resize", measure) }
  }, [fit, map.height, map.width])

  const resetView = useCallback(() => {
    const frame = canvasRef.current?.getBoundingClientRect()
    if (!frame || !stageSize) return
    setView(constrainView({ zoom: 1, x: (frame.width - stageSize.width) / 2, y: (frame.height - stageSize.height) / 2 }, frame, stageSize))
  }, [stageSize])

  const zoomAt = useCallback((nextZoom: number, clientX?: number, clientY?: number) => {
    const frame = canvasRef.current?.getBoundingClientRect()
    if (!frame || !stageSize) return
    const zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, nextZoom))
    const focusX = clientX === undefined ? frame.width / 2 : clientX - frame.left
    const focusY = clientY === undefined ? frame.height / 2 : clientY - frame.top
    setView((current) => {
      const mapX = (focusX - current.x) / current.zoom
      const mapY = (focusY - current.y) / current.zoom
      return constrainView({ zoom, x: focusX - mapX * zoom, y: focusY - mapY * zoom }, frame, stageSize)
    })
  }, [stageSize])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !interactive) return
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      event.stopPropagation()
      zoomAt(view.zoom * Math.exp(-event.deltaY * 0.0015), event.clientX, event.clientY)
    }
    canvas.addEventListener("wheel", onWheel, { passive: false })
    return () => canvas.removeEventListener("wheel", onWheel)
  }, [interactive, view.zoom, zoomAt])

  const markers: MapOverlay[] = overlays || points.map((point, index) => ({ id: String(index), point, label: String(index + 1), color: "var(--danger)", order: index }))

  useEffect(() => {
    if (focusedMarker !== null && focusedMarker >= markers.length) {
      setFocusedMarker(null)
      setDirectionMarker(null)
      setMovingMarker(null)
      setDirectionYaw(null)
      setMarkerYaws((current) => Object.fromEntries(Object.entries(current).filter(([index]) => Number(index) < points.length)))
    }
  }, [focusedMarker, markers.length])

  useEffect(() => {
    const dismissOutsideCanvas = (event: PointerEvent) => {
      const canvas = canvasRef.current
      if (focusedMarker === null || !canvas || !(event.target instanceof Node) || canvas.contains(event.target)) return
      setFocusedMarker(null)
      setDirectionMarker(null)
      setMovingMarker(null)
      setDirectionYaw(null)
      directionYawRef.current = null
    }
    document.addEventListener("pointerdown", dismissOutsideCanvas, true)
    return () => document.removeEventListener("pointerdown", dismissOutsideCanvas, true)
  }, [focusedMarker])

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!interactive || event.button !== 0) return
    if (focusedMarker !== null) {
      event.currentTarget.setPointerCapture(event.pointerId)
      dismissCanvasPointerRef.current = event.pointerId
      setFocusedMarker(null)
      setDirectionMarker(null)
      setMovingMarker(null)
      setDirectionYaw(null)
      directionYawRef.current = null
      return
    }
    event.currentTarget.setPointerCapture(event.pointerId)
    dragRef.current = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, startView: view, moved: false }
    setPanning(true)
  }

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId || !stageSize) return
    const dx = event.clientX - drag.startX
    const dy = event.clientY - drag.startY
    if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true
    const frame = event.currentTarget.getBoundingClientRect()
    setView(constrainView({ ...drag.startView, x: drag.startView.x + dx, y: drag.startView.y + dy }, frame, stageSize))
  }

  const finishPointer = (event: React.PointerEvent<HTMLDivElement>) => {
    if (dismissCanvasPointerRef.current === event.pointerId) {
      dismissCanvasPointerRef.current = null
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
      return
    }
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    dragRef.current = null
    setPanning(false)
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    if (drag.moved || !onPoint || !stageSize) return
    const frame = event.currentTarget.getBoundingClientRect()
    const pixelX = (event.clientX - frame.left - view.x) / view.zoom / stageSize.width * map.width
    const pixelY = (event.clientY - frame.top - view.y) / view.zoom / stageSize.height * map.height
    if (pixelX < 0 || pixelY < 0 || pixelX > map.width || pixelY > map.height) return
    onPoint(pixelToMap(map, pixelX, pixelY))
  }

  const focusMarker = (index: number) => {
    if (!interactive || (!onYawChange && !onPointMove && !onOverlayMove)) return
    setFocusedMarker(index)
    setDirectionMarker(null)
    setMovingMarker(null)
    setDirectionYaw(null)
  }

  const onMarkerClick = (index: number) => {
    if (suppressMarkerClickRef.current === index) {
      suppressMarkerClickRef.current = null
      return
    }
    onOverlaySelect?.(markers[index].id)
    focusMarker(index)
  }

  const startDirection = (index: number) => {
    if (!onYawChange) return
    setFocusedMarker(index)
    setDirectionMarker(index)
    setMovingMarker(null)
    setDirectionYaw(markerYaws[index] ?? null)
    directionYawRef.current = markerYaws[index] ?? null
  }

  const startDirectionDrag = (event: React.PointerEvent<HTMLButtonElement>, index: number) => {
    if (directionMarker !== index || event.button !== 0) return
    event.stopPropagation()
    event.currentTarget.setPointerCapture(event.pointerId)
    directionDragRef.current = { pointerId: event.pointerId, index, startX: event.clientX, startY: event.clientY, moved: false }
  }

  const updateDirection = (event: React.PointerEvent<HTMLButtonElement>, index: number) => {
    const drag = directionDragRef.current
    if (!drag || drag.pointerId !== event.pointerId || drag.index !== index) return
    const dxFromStart = event.clientX - drag.startX
    const dyFromStart = event.clientY - drag.startY
    if (Math.abs(dxFromStart) + Math.abs(dyFromStart) > 3) drag.moved = true
    const rect = event.currentTarget.getBoundingClientRect()
    const dx = event.clientX - (rect.left + rect.width / 2)
    const dy = event.clientY - (rect.top + rect.height / 2)
    if (dx === 0 && dy === 0) return
    const yaw = (Math.atan2(-dy, dx) * 180 / Math.PI + 360) % 360
    directionYawRef.current = yaw
    setDirectionYaw(yaw)
  }

  const finishDirection = (event: React.PointerEvent<HTMLButtonElement>, index: number) => {
    const drag = directionDragRef.current
    if (!drag || drag.pointerId !== event.pointerId || drag.index !== index) return
    directionDragRef.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    const nextYaw = directionYawRef.current
    if (drag.moved && nextYaw !== null) {
      const committedYaw = Math.round(nextYaw) % 360
      suppressMarkerClickRef.current = index
      window.requestAnimationFrame(() => {
        if (suppressMarkerClickRef.current === index) suppressMarkerClickRef.current = null
      })
      setMarkerYaws((current) => ({ ...current, [index]: committedYaw }))
      onYawChange?.(committedYaw)
      setFocusedMarker(null)
      setDirectionMarker(null)
    }
    setDirectionYaw(null)
    directionYawRef.current = null
  }

  const cancelDirection = (event: React.PointerEvent<HTMLButtonElement>, index: number) => {
    const drag = directionDragRef.current
    if (!drag || drag.pointerId !== event.pointerId || drag.index !== index) return
    directionDragRef.current = null
    setDirectionYaw(null)
    directionYawRef.current = null
  }

  const startMove = (index: number) => {
    if (!onPointMove && !onOverlayMove) return
    setFocusedMarker(index)
    setDirectionMarker(null)
    setMovingMarker(index)
    setDirectionYaw(null)
    directionYawRef.current = null
  }

  const pointAt = (clientX: number, clientY: number) => {
    const frame = canvasRef.current?.getBoundingClientRect()
    if (!frame || !stageSize) return null
    const pixelX = Math.min(map.width, Math.max(0, (clientX - frame.left - view.x) / view.zoom / stageSize.width * map.width))
    const pixelY = Math.min(map.height, Math.max(0, (clientY - frame.top - view.y) / view.zoom / stageSize.height * map.height))
    return pixelToMap(map, pixelX, pixelY)
  }

  const startPointMove = (event: React.PointerEvent<HTMLButtonElement>, index: number) => {
    if (movingMarker !== index || event.button !== 0) return
    event.stopPropagation()
    event.currentTarget.setPointerCapture(event.pointerId)
    moveDragRef.current = { pointerId: event.pointerId, index }
  }

  const updatePointMove = (event: React.PointerEvent<HTMLButtonElement>, index: number) => {
    const drag = moveDragRef.current
    if (!drag || drag.pointerId !== event.pointerId || drag.index !== index) return
    const point = pointAt(event.clientX, event.clientY)
    if (point) {
      onPointMove?.(index, point)
      onOverlayMove?.(markers[index].id, point)
    }
  }

  const finishPointMove = (event: React.PointerEvent<HTMLButtonElement>, index: number) => {
    const drag = moveDragRef.current
    if (!drag || drag.pointerId !== event.pointerId || drag.index !== index) return
    moveDragRef.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    suppressMarkerClickRef.current = index
    window.requestAnimationFrame(() => {
      if (suppressMarkerClickRef.current === index) suppressMarkerClickRef.current = null
    })
    setFocusedMarker(null)
    setMovingMarker(null)
  }

  const cancelPointMove = (event: React.PointerEvent<HTMLButtonElement>, index: number) => {
    const drag = moveDragRef.current
    if (!drag || drag.pointerId !== event.pointerId || drag.index !== index) return
    moveDragRef.current = null
    setFocusedMarker(null)
    setMovingMarker(null)
  }

  const stageStyle = stageSize ? {
    width: `${stageSize.width}px`,
    height: `${stageSize.height}px`,
    transform: `translate3d(${view.x}px, ${view.y}px, 0) scale(${view.zoom})`,
  } : undefined

  return <div ref={canvasRef} className={cn("map-canvas", interactive && "interactive", panning && "is-panning", fit === "width" && "fit-width", className)} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={finishPointer} onPointerCancel={finishPointer}>
    <div className="map-stage" style={stageStyle}>
      {url ? <img src={url} alt={`${map.logical_name} v${map.version}`} draggable={false} onDragStart={(event) => event.preventDefault()} /> : <div className="map-loading">正在读取地图</div>}
      {overlays && <TrackLines map={map} overlays={overlays} />}
      {markers.map((marker, index) => <MapMarker key={marker.id} map={map} point={marker.point} index={index} label={marker.label} color={marker.color} state={marker.state} zoom={view.zoom} interactive={Boolean(interactive && (onYawChange || onPointMove || onOverlayMove))} focused={focusedMarker === index} choosingDirection={directionMarker === index} moving={movingMarker === index} directionYaw={directionMarker === index ? directionYaw : markerYaws[index] ?? marker.yaw ?? null} canChooseDirection={Boolean(onYawChange)} canMove={Boolean(onPointMove || onOverlayMove)} onMarkerClick={onMarkerClick} onChooseDirection={startDirection} onChooseMove={startMove} onDirectionStart={startDirectionDrag} onDirectionMove={updateDirection} onDirectionEnd={finishDirection} onDirectionCancel={cancelDirection} onMoveStart={startPointMove} onMoveMove={updatePointMove} onMoveEnd={finishPointMove} onMoveCancel={cancelPointMove} />)}
    </div>
    {interactive && <><div className="map-hint"><Crosshair size={14} />滚轮缩放 · 拖拽移动 · 单击选点或标点</div><div className="map-zoom-controls" onPointerDown={(event) => event.stopPropagation()}><button type="button" aria-label="放大地图" onClick={() => zoomAt(view.zoom * 1.25)}><Plus size={16} /></button><button type="button" aria-label="缩小地图" onClick={() => zoomAt(view.zoom / 1.25)}><Minus size={16} /></button><button type="button" aria-label="重置地图视图" onClick={resetView}><RotateCcw size={15} /></button><span><Maximize2 size={13} />{Math.round(view.zoom * 100)}%</span></div></>}
  </div>
}

function MapMarker({ map, point, index, label, color, state, zoom, interactive, focused, choosingDirection, moving, directionYaw, canChooseDirection, canMove, onMarkerClick, onChooseDirection, onChooseMove, onDirectionStart, onDirectionMove, onDirectionEnd, onDirectionCancel, onMoveStart, onMoveMove, onMoveEnd, onMoveCancel }: { map: StoredMap; point: MapPoint; index: number; label: string; color: string; state?: MapOverlay["state"]; zoom: number; interactive: boolean; focused: boolean; choosingDirection: boolean; moving: boolean; directionYaw: number | null; canChooseDirection: boolean; canMove: boolean; onMarkerClick(index: number): void; onChooseDirection(index: number): void; onChooseMove(index: number): void; onDirectionStart(event: React.PointerEvent<HTMLButtonElement>, index: number): void; onDirectionMove(event: React.PointerEvent<HTMLButtonElement>, index: number): void; onDirectionEnd(event: React.PointerEvent<HTMLButtonElement>, index: number): void; onDirectionCancel(event: React.PointerEvent<HTMLButtonElement>, index: number): void; onMoveStart(event: React.PointerEvent<HTMLButtonElement>, index: number): void; onMoveMove(event: React.PointerEvent<HTMLButtonElement>, index: number): void; onMoveEnd(event: React.PointerEvent<HTMLButtonElement>, index: number): void; onMoveCancel(event: React.PointerEvent<HTMLButtonElement>, index: number): void }) {
  const left = (point.pixelX / map.width) * 100
  const top = (point.pixelY / map.height) * 100
  const arrowAngle = directionYaw === null ? 0 : ((180 - directionYaw) % 360 + 360) % 360 - 180
  return <span className={cn("map-marker", state && `state-${state}`, !interactive && "is-static", focused && "is-focused", choosingDirection && "is-direction-mode", moving && "is-moving", left > 72 && "is-right-edge")} style={{ left: `${left}%`, top: `${top}%`, transform: `translate(-50%, -50%) scale(${1 / zoom})` }}>
    <button type="button" className="map-marker-hit" style={{ backgroundColor: color }} aria-label={`点位 ${label}${choosingDirection ? "，正在选择方向" : moving ? "，正在移动位置" : ""}`} disabled={!interactive} onPointerDown={(event) => { event.stopPropagation(); if (moving) onMoveStart(event, index); else onDirectionStart(event, index) }} onPointerMove={(event) => { if (moving) onMoveMove(event, index); else onDirectionMove(event, index) }} onPointerUp={(event) => { if (moving) onMoveEnd(event, index); else onDirectionEnd(event, index) }} onPointerCancel={(event) => { if (moving) onMoveCancel(event, index); else onDirectionCancel(event, index) }} onClick={(event) => { event.stopPropagation(); if (!choosingDirection && !moving) onMarkerClick(index) }}>{label}</button>
    {directionYaw !== null && <span className="map-marker-arrow" style={{ transform: `translateY(-50%) rotate(${arrowAngle}deg)` }} />}
    {focused && !choosingDirection && !moving && <span className="map-marker-menu" onPointerDown={(event) => event.stopPropagation()}><button type="button" disabled={!canChooseDirection} onClick={(event) => { event.stopPropagation(); onChooseDirection(index) }}>选择方向</button><button type="button" disabled={!canMove} onClick={(event) => { event.stopPropagation(); onChooseMove(index) }}>移动位置</button></span>}
  </span>
}

function TrackLines({ map, overlays }: { map: StoredMap; overlays: MapOverlay[] }) {
  const tracks = new Map<string, MapOverlay[]>()
  overlays.filter((item) => item.trackId && item.state !== "initial").forEach((item) => {
    const values = tracks.get(item.trackId!) || []
    values.push(item)
    tracks.set(item.trackId!, values)
  })
  return <svg className="map-track-lines" viewBox={`0 0 ${map.width} ${map.height}`} preserveAspectRatio="none" aria-hidden="true">
    {[...tracks.entries()].map(([trackId, values]) => {
      const sorted = values.sort((a, b) => (a.order || 0) - (b.order || 0))
      return <polyline key={trackId} points={sorted.map((item) => `${item.point.pixelX},${item.point.pixelY}`).join(" ")} fill="none" stroke={sorted[0]?.color} strokeWidth="3" vectorEffect="non-scaling-stroke" />
    })}
  </svg>
}
