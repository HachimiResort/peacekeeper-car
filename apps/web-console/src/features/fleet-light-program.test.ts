import { afterEach, describe, expect, it, vi } from "vitest"
import { FleetLightProgram, lightEffectState } from "./fleet-light-program"

afterEach(() => vi.useRealTimers())

describe("fleet light program", () => {
  it("maps all presets to their visible on state", () => {
    expect(lightEffectState("left_steady")).toEqual({ left: true, right: false })
    expect(lightEffectState("right_steady")).toEqual({ left: false, right: true })
    expect(lightEffectState("both_steady")).toEqual({ left: true, right: true })
    expect(lightEffectState("left_blink", false)).toEqual({ left: false, right: false })
    expect(lightEffectState("right_blink", false)).toEqual({ left: false, right: false })
    expect(lightEffectState("both_blink", false)).toEqual({ left: false, right: false })
    expect(lightEffectState("alternate", true)).toEqual({ left: true, right: false })
    expect(lightEffectState("alternate", false)).toEqual({ left: false, right: true })
  })

  it("starts blink lit, alternates every 600ms, and stops its old timer on effect change", async () => {
    vi.useFakeTimers()
    const send = vi.fn(async () => undefined)
    const program = new FleetLightProgram(send)
    program.set("car_1", "both_blink")
    await vi.runAllTicks()
    expect(send).toHaveBeenLastCalledWith("car_1", { left: true, right: true })
    await vi.advanceTimersByTimeAsync(600)
    expect(send).toHaveBeenLastCalledWith("car_1", { left: false, right: false })
    program.set("car_1", "alternate")
    await vi.runAllTicks()
    expect(send).toHaveBeenLastCalledWith("car_1", { left: true, right: false })
    await vi.advanceTimersByTimeAsync(600)
    expect(send).toHaveBeenLastCalledWith("car_1", { left: false, right: true })
  })

  it("ignore clears timers without changing the last requested lamp state", async () => {
    vi.useFakeTimers()
    const send = vi.fn(async () => undefined)
    const program = new FleetLightProgram(send)
    program.set("car_1", "left_blink")
    await vi.runAllTicks()
    program.set("car_1", "ignore")
    await vi.advanceTimersByTimeAsync(1800)
    expect(send).toHaveBeenCalledTimes(1)
    expect(send).toHaveBeenLastCalledWith("car_1", { left: true, right: false })
  })

  it("turns every selected vehicle off when stopped", async () => {
    const send = vi.fn(async () => undefined)
    const program = new FleetLightProgram(send)
    program.set("a", "both_steady")
    program.set("b", "right_steady")
    await Promise.resolve()
    await program.stopAll(["a", "b"])
    expect(send).toHaveBeenLastCalledWith("b", { left: false, right: false })
    expect(send).toHaveBeenCalledWith("a", { left: false, right: false })
  })
})
