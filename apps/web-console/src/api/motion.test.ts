import { describe, expect, it, vi } from "vitest"
import { MotionCommander } from "./motion"

describe("MotionCommander", () => {
  it("does not pile up commands and always stops on release", async () => {
    vi.useFakeTimers()
    let release: (() => void) | undefined
    const send = vi.fn(() => new Promise<void>((resolve) => { release = resolve }))
    const stop = vi.fn(async () => undefined)
    const commander = new MotionCommander(send, stop, 150)
    commander.start({ linear_x: 0.2, linear_y: 0, angular_z: 0, ttl_ms: 500, source: "test" })
    await vi.advanceTimersByTimeAsync(600)
    expect(send).toHaveBeenCalledTimes(1)
    release?.()
    await Promise.resolve()
    await vi.advanceTimersByTimeAsync(150)
    expect(send).toHaveBeenCalledTimes(2)
    commander.stop()
    expect(stop).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
  })
})
