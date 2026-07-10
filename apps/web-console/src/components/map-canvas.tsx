import { useEffect, useRef, useState } from "react"
import { Crosshair } from "lucide-react"
import type { MapPoint, MissionApi, StoredMap } from "../api/types"
import { pixelToMap } from "../api/client"
import { cn } from "../lib/cn"

export function MapCanvas({ api, map, onPoint, points = [], interactive = true, className }: { api: MissionApi; map: StoredMap; onPoint?(point: MapPoint): void; points?: MapPoint[]; interactive?: boolean; className?: string }) {
  const [url, setUrl] = useState("")
  const imageRef = useRef<HTMLImageElement>(null)
  useEffect(() => {
    let active = true
    let objectUrl = ""
    api.mapPreview(map.id).then((blob) => { if (!active) return; objectUrl = URL.createObjectURL(blob); setUrl(objectUrl) })
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [api, map.id])

  const click = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!interactive || !imageRef.current || !onPoint) return
    const frame = event.currentTarget.getBoundingClientRect()
    const localX = event.clientX - frame.left
    const localY = event.clientY - frame.top
    onPoint(pixelToMap(map, localX / frame.width * map.width, localY / frame.height * map.height))
  }

  return <div className={cn("map-canvas", interactive && "interactive", className)}>
    <div className="map-stage" style={{ aspectRatio: `${map.width} / ${map.height}` }} onClick={click}>
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
