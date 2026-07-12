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
    commander.release()
    expect(stop).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
  })

  it("stops the command loop and reports only the first send failure", async () => {
    vi.useFakeTimers()
    const error = new Error("connection lost")
    const send = vi.fn(async () => { throw error })
    const stop = vi.fn(async () => undefined)
    const onError = vi.fn()
    const commander = new MotionCommander(send, stop, 150, onError)

    commander.start({ linear_x: 0.2, linear_y: 0, angular_z: 0, ttl_ms: 500, source: "test" })
    await vi.advanceTimersByTimeAsync(600)

    expect(send).toHaveBeenCalledTimes(1)
    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError).toHaveBeenCalledWith(error)
    commander.release()
    vi.useRealTimers()
  })

  it("does not send a stop request when an idle commander is released or disposed", () => {
    const send = vi.fn(async () => undefined)
    const stop = vi.fn(async () => undefined)
    const commander = new MotionCommander(send, stop)

    commander.release()
    commander.dispose()

    expect(stop).not.toHaveBeenCalled()
  })

  it("keeps explicit force stop available even without manual motion", () => {
    const send = vi.fn(async () => undefined)
    const stop = vi.fn(async () => undefined)
    const commander = new MotionCommander(send, stop)

    commander.forceStop()

    expect(stop).toHaveBeenCalledTimes(1)
  })
})
