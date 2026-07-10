import { describe, expect, it } from "vitest"
import { degreesToRadians, HttpMissionApi, pixelToMap, reconnectDelay } from "./client"
import type { StoredMap } from "./types"

const map: StoredMap = { id: "map", logical_name: "lab", version: 1, yaml_sha256: "", image_sha256: "", bundle_sha256: "", resolution: 0.05, origin: [-10, -20, 0], width: 2, height: 2, source_robot_id: null, created_at: null }

describe("map coordinates", () => {
  it("converts image pixels to ROS map coordinates", () => {
    expect(pixelToMap(map, 0, 0)).toMatchObject({ x: -9.975, y: -19.925 })
    expect(degreesToRadians(90)).toBeCloseTo(Math.PI / 2)
  })
})

describe("Mission API client", () => {
  it("attaches the shared token", async () => {
    const api = new HttpMissionApi("valid-token")
    expect(await api.health()).toBe(true)
    expect(await api.robots()).toEqual([])
  })

  it("uses bounded reconnect backoff", () => {
    expect(reconnectDelay(0)).toBe(1000)
    expect(reconnectDelay(4)).toBe(15000)
    expect(reconnectDelay(20)).toBe(15000)
  })
})
