import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { MissionApi, StoredMap } from "../api/types"
import { MapCanvas } from "./map-canvas"

const map: StoredMap = { id: "map", logical_name: "lab", version: 1, yaml_sha256: "", image_sha256: "", bundle_sha256: "", resolution: 0.05, origin: [-10, -20, 0], width: 400, height: 400, source_robot_id: null, created_at: null }
const point = { x: 0, y: 0, pixelX: 112, pixelY: 112 }

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    return this.classList.contains("map-marker-hit")
      ? new DOMRect(100, 100, 24, 24)
      : new DOMRect(0, 0, 400, 400)
  })
  Object.defineProperty(HTMLElement.prototype, "setPointerCapture", { configurable: true, value: vi.fn() })
  Object.defineProperty(HTMLElement.prototype, "releasePointerCapture", { configurable: true, value: vi.fn() })
  Object.defineProperty(HTMLElement.prototype, "hasPointerCapture", { configurable: true, value: () => true })
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:map") })
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() })
  vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1))
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function renderCanvas(onPoint = vi.fn(), onYawChange = vi.fn(), onPointMove = vi.fn()) {
  const api = { mapPreview: vi.fn().mockResolvedValue(new Blob(["map"])) } as unknown as MissionApi
  const result = render(<MapCanvas api={api} map={map} points={[point]} onPoint={onPoint} onPointMove={onPointMove} onYawChange={onYawChange} />)
  return { ...result, onPoint, onYawChange, onPointMove }
}

describe("MapCanvas marker direction controls", () => {
  it("uses a blank map click only to dismiss a focused marker", () => {
    const { container, onPoint } = renderCanvas()
    const marker = screen.getByRole("button", { name: "点位 1" })
    fireEvent.click(marker)
    expect(screen.getByRole("button", { name: "选择方向" })).toBeInTheDocument()

    const canvas = container.querySelector(".map-canvas")!
    fireEvent.pointerDown(canvas, { button: 0, pointerId: 1, clientX: 200, clientY: 200 })
    fireEvent.pointerUp(canvas, { button: 0, pointerId: 1, clientX: 200, clientY: 200 })

    expect(screen.queryByRole("button", { name: "选择方向" })).not.toBeInTheDocument()
    expect(onPoint).not.toHaveBeenCalled()

    fireEvent.click(marker)
    expect(screen.getByRole("button", { name: "选择方向" })).toBeInTheDocument()
    fireEvent.pointerDown(document.body, { button: 0, pointerId: 3 })
    expect(screen.queryByRole("button", { name: "选择方向" })).not.toBeInTheDocument()
  })

  it.each([
    [60, 112, 180, "rotate(-180deg)"],
    [112, 60, 90, "rotate(-90deg)"],
    [164, 112, 0, "rotate(0deg)"],
    [112, 164, 270, "rotate(90deg)"],
  ])("points the arrow toward the cursor and writes the expected yaw", (clientX, clientY, expectedYaw, expectedRotation) => {
    const { container, onYawChange } = renderCanvas()
    fireEvent.click(screen.getByRole("button", { name: "点位 1" }))
    fireEvent.click(screen.getByRole("button", { name: "选择方向" }))
    const marker = screen.getByRole("button", { name: "点位 1，正在选择方向" })

    fireEvent.pointerDown(marker, { button: 0, pointerId: 2, clientX: 112, clientY: 112 })
    fireEvent.pointerMove(marker, { pointerId: 2, clientX, clientY })
    expect(container.querySelector(".map-marker-arrow")).toHaveStyle({ transform: `translateY(-50%) ${expectedRotation}` })
    fireEvent.pointerUp(marker, { pointerId: 2, clientX, clientY })
    fireEvent.click(marker)

    expect(onYawChange).toHaveBeenCalledWith(expectedYaw)
    expect(container.querySelector(".map-marker-arrow")).toHaveStyle({ transform: `translateY(-50%) ${expectedRotation}` })
    expect(marker.closest(".map-marker")).not.toHaveClass("is-focused")
    expect(screen.queryByRole("button", { name: "选择方向" })).not.toBeInTheDocument()
  })

  it("moves a marker continuously and clears its focus on release", () => {
    const { onPointMove } = renderCanvas()
    fireEvent.click(screen.getByRole("button", { name: "点位 1" }))
    fireEvent.click(screen.getByRole("button", { name: "移动位置" }))
    const marker = screen.getByRole("button", { name: "点位 1，正在移动位置" })
    expect(marker.closest(".map-marker")).toHaveClass("is-focused")

    fireEvent.pointerDown(marker, { button: 0, pointerId: 4, clientX: 112, clientY: 112 })
    fireEvent.pointerMove(marker, { pointerId: 4, clientX: 240, clientY: 200 })
    expect(onPointMove).toHaveBeenLastCalledWith(0, expect.objectContaining({ pixelX: 240, pixelY: 200 }))
    fireEvent.pointerUp(marker, { pointerId: 4, clientX: 240, clientY: 200 })

    expect(marker.closest(".map-marker")).not.toHaveClass("is-focused")
    expect(screen.queryByRole("button", { name: "移动位置" })).not.toBeInTheDocument()
  })
})
