import { describe, expect, it } from "vitest"
import { mapImportedShow, parseShowImport } from "./show-import"

describe("show file import", () => {
  it("parses logical vehicles and maps them to real robots", () => {
    const imported = parseShowImport({ name: "双车", score: { bpm: 120, robot_ids: ["car_1", "car_2"], tracks: { car_1: { motion: [{ at_tick: 0, duration_ticks: 2, linear_x: .3 }] }, car_2: { lights: [{ at_tick: 0, duration_ticks: 2, effect: "both" }] } } } })
    const result = mapImportedShow(imported, { car_1: "robot-a", car_2: "robot-b" })
    expect(result.score.robot_ids).toEqual(["robot-a", "robot-b"])
    expect(result.score.tracks["robot-a"].motion[0].linear_x).toBe(.3)
    expect(result.score.tracks["robot-b"].lights[0].effect).toBe("both")
  })

  it("rejects duplicate real-robot mapping", () => {
    const imported = parseShowImport({ bpm: 120, robot_ids: ["car_1", "car_2"], tracks: {} })
    expect(() => mapImportedShow(imported, { car_1: "robot-a", car_2: "robot-a" })).toThrow("不能对应多个")
  })
})
