import { useCallback, useEffect, useRef, useState } from "react"
import { Crosshair, Maximize2, Minus, Plus, RotateCcw } from "lucide-react"
import type { MapPoint, MissionApi, StoredMap } from "../api/types"
import { pixelToMap } from "../api/client"
import { cn } from "../lib/cn"

type View = { zoom: number; x: number; y: number }
type StageSize = { width: number; height: number }

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

export function MapCanvas({ api, map, onPoint, points = [], interactive = true, className, fit = "contain" }: { api: MissionApi; map: StoredMap; onPoint?(point: MapPoint): void; points?: MapPoint[]; interactive?: boolean; className?: string; fit?: "contain" | "width" }) {
  const [url, setUrl] = useState("")
  const [stageSize, setStageSize] = useState<StageSize | null>(null)
  const [view, setView] = useState<View>({ zoom: 1, x: 0, y: 0 })
  const [panning, setPanning] = useState(false)
  const canvasRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<{ pointerId: number; startX: number; startY: number; startView: View; moved: boolean } | null>(null)

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

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!interactive || event.button !== 0) return
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

  const stageStyle = stageSize ? {
    width: `${stageSize.width}px`,
    height: `${stageSize.height}px`,
    transform: `translate3d(${view.x}px, ${view.y}px, 0) scale(${view.zoom})`,
  } : undefined

  return <div ref={canvasRef} className={cn("map-canvas", interactive && "interactive", panning && "is-panning", fit === "width" && "fit-width", className)} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={finishPointer} onPointerCancel={finishPointer}>
    <div className="map-stage" style={stageStyle}>
      {url ? <img src={url} alt={`${map.logical_name} v${map.version}`} draggable={false} onDragStart={(event) => event.preventDefault()} /> : <div className="map-loading">正在读取地图</div>}
      {points.map((point, index) => <MapMarker key={`${point.x}-${point.y}-${index}`} map={map} point={point} index={index} zoom={view.zoom} />)}
    </div>
    {interactive && <><div className="map-hint"><Crosshair size={14} />滚轮缩放 · 拖拽移动 · 单击选点</div><div className="map-zoom-controls" onPointerDown={(event) => event.stopPropagation()}><button type="button" aria-label="放大地图" onClick={() => zoomAt(view.zoom * 1.25)}><Plus size={16} /></button><button type="button" aria-label="缩小地图" onClick={() => zoomAt(view.zoom / 1.25)}><Minus size={16} /></button><button type="button" aria-label="重置地图视图" onClick={resetView}><RotateCcw size={15} /></button><span><Maximize2 size={13} />{Math.round(view.zoom * 100)}%</span></div></>}
  </div>
}

function MapMarker({ map, point, index, zoom }: { map: StoredMap; point: MapPoint; index: number; zoom: number }) {
  const left = (point.pixelX / map.width) * 100
  const top = (point.pixelY / map.height) * 100
  return <span className="map-marker" style={{ left: `${left}%`, top: `${top}%`, transform: `translate(-50%, -50%) scale(${1 / zoom})` }}>{index + 1}</span>
}
