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

  constructor(
    private readonly send: (payload: MotionPayload) => Promise<unknown>,
    private readonly sendStop: () => Promise<unknown>,
    private readonly periodMs = 150,
  ) {}

  start(payload: MotionPayload) {
    this.clearTimer()
    void this.tick(payload)
    this.timer = window.setInterval(() => void this.tick(payload), this.periodMs)
  }

  stop() {
    this.clearTimer()
    void this.sendStop()
  }

  private clearTimer() {
    if (this.timer) window.clearInterval(this.timer)
    this.timer = undefined
  }

  private async tick(payload: MotionPayload) {
    if (this.inFlight) return
    this.inFlight = true
    try { await this.send(payload) }
    finally { this.inFlight = false }
  }
}
