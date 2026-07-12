import { useEffect, useRef, useState } from "react"
import { Crosshair } from "lucide-react"
import type { MapPoint, MissionApi, StoredMap } from "../api/types"
import { pixelToMap } from "../api/client"
import { cn } from "../lib/cn"

export function MapCanvas({ api, map, onPoint, points = [], interactive = true, className, fit = "contain" }: { api: MissionApi; map: StoredMap; onPoint?(point: MapPoint): void; points?: MapPoint[]; interactive?: boolean; className?: string; fit?: "contain" | "width" }) {
  const [url, setUrl] = useState("")
  const [stageSize, setStageSize] = useState<{ width: number; height: number } | null>(null)
  const canvasRef = useRef<HTMLDivElement>(null)
  const imageRef = useRef<HTMLImageElement>(null)
  useEffect(() => {
    let active = true
    let objectUrl = ""
    api.mapPreview(map.id).then((blob) => { if (!active) return; objectUrl = URL.createObjectURL(blob); setUrl(objectUrl) })
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [api, map.id])

  useEffect(() => {
    if (fit === "width") {
      setStageSize(null)
      return
    }
    const canvas = canvasRef.current
    if (!canvas) return
    const measure = () => {
      const frame = canvas.getBoundingClientRect()
      if (frame.width <= 0 || frame.height <= 0) return
      const mapRatio = map.width / map.height
      const frameRatio = frame.width / frame.height
      const size = mapRatio >= frameRatio
        ? { width: frame.width, height: frame.width / mapRatio }
        : { width: frame.height * mapRatio, height: frame.height }
      setStageSize(size)
    }
    measure()
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure)
    observer?.observe(canvas)
    window.addEventListener("resize", measure)
    return () => { observer?.disconnect(); window.removeEventListener("resize", measure) }
  }, [fit, map.width, map.height])

  const click = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!interactive || !imageRef.current || !onPoint) return
    const frame = event.currentTarget.getBoundingClientRect()
    const localX = event.clientX - frame.left
    const localY = event.clientY - frame.top
    onPoint(pixelToMap(map, localX / frame.width * map.width, localY / frame.height * map.height))
  }

  const stageStyle = fit === "width" ? { width: "100%" } : (stageSize || { width: "100%" })

  return <div ref={canvasRef} className={cn("map-canvas", interactive && "interactive", fit === "width" && "fit-width", className)}>
    <div className="map-stage" style={{ aspectRatio: `${map.width} / ${map.height}`, ...stageStyle }} onClick={click}>
      {url ? <img ref={imageRef} src={url} alt={`${map.logical_name} v${map.version}`} /> : <div className="map-loading">正在读取地图</div>}
      {points.map((point, index) => <MapMarker key={`${point.x}-${point.y}-${index}`} map={map} point={point} index={index} />)}
    </div>
    {interactive && <div className="map-hint"><Crosshair size={14} />点击地图选点</div>}
  </div>
}

function MapMarker({ map, point, index }: { map: StoredMap; point: MapPoint; index: number }) {
  const left = (point.pixelX / map.width) * 100
  const top = (point.pixelY / map.height) * 100
  return <span className="map-marker" style={{ left: `${left}%`, top: `${top}%` }}>{index + 1}</span>
}
