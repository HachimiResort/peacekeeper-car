import { describe, expect, it } from "vitest"
import type { JsonObject } from "../api/types"
import {
  applyGoalId,
  beginBarrierRow,
  collisionWarnings,
  createBarrierRun,
  createFleetLabDraft,
  createFleetWaypoint,
  evaluateBarrier,
  goalIdFromResponse,
  loadFleetLabDraft,
  resizeRows,
  saveFleetLabDraft,
  waypointYaw,
} from "./fleet-lab-model"

const point = (x: number, y: number) => createFleetWaypoint({ x, y, pixelX: x, pixelY: y })
const live = (goalId: string, state: string): JsonObject => ({ online: true, status: { navigation: { last_result: { goal_id: goalId, state } } } })

describe("fleet lab route model", () => {
  it("resizes rows and computes route tangent yaw with an override", () => {
    let draft = createFleetLabDraft(2)
    draft = { ...draft, robotIds: ["a"], rows: [{ a: point(0, 0) }, { a: point(0, 2) }] }
    expect(waypointYaw(draft, "a", 0)).toBe(90)
    expect(waypointYaw(draft, "a", 1)).toBe(90)
    draft.rows[1].a = { ...point(0, 2), yawOverride: 225 }
    expect(waypointYaw(draft, "a", 1)).toBe(225)
    expect(resizeRows(draft, 4).rows).toHaveLength(4)
  })

  it("reports same-slice proximity without blocking the draft", () => {
    const draft = createFleetLabDraft(1)
    draft.robotIds = ["a", "b"]
    draft.rows = [{ a: point(0, 0), b: point(0.3, 0.4) }]
    expect(collisionWarnings(draft)).toEqual([{ rowIndex: 0, robotA: "a", robotB: "b", distance: 0.5 }])
  })

  it("restores a running draft as recovery required", () => {
    const storage = window.sessionStorage
    storage.clear()
    const draft = { ...createFleetLabDraft(), mapId: "map-1", phase: "running" as const }
    saveFleetLabDraft(draft, storage)
    expect(loadFleetLabDraft(storage)).toMatchObject({ mapId: "map-1", phase: "recovery_required" })
  })

  it("migrates v1 waypoints with safe default light effects", () => {
    const storage = window.sessionStorage
    storage.clear()
    storage.setItem("peacekeeper.fleet-lab.v1", JSON.stringify({
      version: 1,
      mapId: "map-1",
      robotIds: ["a"],
      rows: [{ a: { x: 1, y: 2, pixelX: 3, pixelY: 4 } }],
      initialPoses: {},
      phase: "draft",
    }))
    expect(loadFleetLabDraft(storage)).toMatchObject({ version: 2, rows: [{ a: { travelLight: "ignore", waitingLight: "ignore" } }] })
  })
})

describe("fleet lab barrier", () => {
  it("reads goal ids from the real fleet-agent response wrapped by Mission API", () => {
    expect(goalIdFromResponse({ mission_id: "mission-1", result: { ok: true, navigation: { goal_id: "nav-7", action_state: "sending" } } })).toBe("nav-7")
    expect(goalIdFromResponse({ result: { goal_id: "demo-nav-1" } })).toBe("demo-nav-1")
  })

  it("waits for both matching goal results and ignores stale goals", () => {
    let run = beginBarrierRow(createBarrierRun(["a", "b"]), ["a", "b"], 1000)
    run = applyGoalId(applyGoalId(run, "a", "goal-a"), "b", "goal-b")
    let result = evaluateBarrier(run, ["a", "b"], { a: live("old", "succeeded"), b: live("goal-b", "succeeded") }, { a: 1900, b: 1900 }, 180, 2000)
    expect(result.outcome).toBe("waiting")
    expect(result.run.robots.b.state).toBe("arrived")
    result = evaluateBarrier(result.run, ["a", "b"], { a: live("goal-a", "succeeded"), b: live("goal-b", "succeeded") }, { a: 2100, b: 2100 }, 180, 2200)
    expect(result.outcome).toBe("advance")
  })

  it("blocks on failure, stale status and timeout", () => {
    let run = beginBarrierRow(createBarrierRun(["a"]), ["a"], 1000)
    run = applyGoalId(run, "a", "goal-a")
    expect(evaluateBarrier(run, ["a"], { a: live("goal-a", "failed") }, { a: 1500 }, 180, 2000).outcome).toBe("blocked")
    expect(evaluateBarrier(run, ["a"], { a: { online: true } }, { a: 1000 }, 180, 12_000).outcome).toBe("blocked")
    expect(evaluateBarrier(run, ["a"], { a: { online: true } }, { a: 181500 }, 180, 182000).outcome).toBe("blocked")
  })

  it("preserves arrived vehicles while retrying the blocked row", () => {
    let run = beginBarrierRow(createBarrierRun(["a", "b"]), ["a", "b"], 1000)
    run = applyGoalId(applyGoalId(run, "a", "goal-a"), "b", "goal-b")
    run = evaluateBarrier(run, ["a", "b"], { a: live("goal-a", "succeeded"), b: live("goal-b", "failed") }, { a: 1500, b: 1500 }, 180, 2000).run
    const retry = beginBarrierRow(run, ["a", "b"], 3000, true)
    expect(retry.robots.a.state).toBe("arrived")
    expect(retry.robots.b.state).toBe("sending")
  })
})
