export interface MotionPayload {
  linear_x: number
  linear_y: number
  angular_z: number
  ttl_ms: number
  source: string
}

export class MotionCommander {
  private timer: number | undefined
  private inFlight = false
  private failed = false
  private active = false

  constructor(
    private readonly send: (payload: MotionPayload) => Promise<unknown>,
    private readonly sendStop: () => Promise<unknown>,
    private readonly periodMs = 150,
    private readonly onError: (error: unknown) => void = () => undefined,
  ) {}

  start(payload: MotionPayload) {
    this.clearTimer()
    this.failed = false
    this.active = true
    void this.tick(payload)
    this.timer = window.setInterval(() => void this.tick(payload), this.periodMs)
  }

  release() {
    this.clearTimer()
    if (!this.active) return
    this.active = false
    void this.sendStop().catch((error) => this.reportError(error))
  }

  forceStop() {
    this.clearTimer()
    this.active = false
    this.failed = false
    void this.sendStop().catch((error) => this.reportError(error))
  }

  dispose() {
    this.release()
  }

  private clearTimer() {
    if (this.timer) window.clearInterval(this.timer)
    this.timer = undefined
  }

  private async tick(payload: MotionPayload) {
    if (this.inFlight || this.failed) return
    this.inFlight = true
    try { await this.send(payload) }
    catch (error) {
      this.failed = true
      this.active = false
      this.clearTimer()
      this.onError(error)
    }
    finally { this.inFlight = false }
  }

  private reportError(error: unknown) {
    if (this.failed) return
    this.failed = true
    this.onError(error)
  }
}
