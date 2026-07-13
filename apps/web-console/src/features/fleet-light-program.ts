export const LIGHT_EFFECTS = [
  "ignore",
  "left_steady",
  "right_steady",
  "both_steady",
  "left_blink",
  "right_blink",
  "both_blink",
  "alternate",
] as const

export type LightEffect = typeof LIGHT_EFFECTS[number]

export interface LightState {
  left: boolean
  right: boolean
}

export const LIGHT_EFFECT_OPTIONS: Array<{ value: LightEffect; label: string }> = [
  { value: "ignore", label: "不管" },
  { value: "left_steady", label: "左灯常亮" },
  { value: "right_steady", label: "右灯常亮" },
  { value: "both_steady", label: "双灯常亮" },
  { value: "left_blink", label: "左灯频闪" },
  { value: "right_blink", label: "右灯频闪" },
  { value: "both_blink", label: "双灯频闪" },
  { value: "alternate", label: "左右交替闪" },
]

const OFF: LightState = { left: false, right: false }

interface LightSlot {
  effect: LightEffect
  phaseOn: boolean
  timer: number | null
  desired: LightState | null
  sent: LightState | null
  inFlight: boolean
  force: boolean
  waiters: Array<() => void>
}

export class FleetLightProgram {
  private readonly slots = new Map<string, LightSlot>()

  constructor(
    private readonly send: (robotId: string, state: LightState) => Promise<unknown>,
    private readonly onError?: (robotId: string, error: unknown) => void,
  ) {}

  set(robotId: string, effect: LightEffect) {
    const slot = this.slot(robotId)
    this.clearTimer(slot)
    slot.effect = effect
    slot.phaseOn = true
    if (effect === "ignore") {
      slot.desired = null
      this.resolveWaiters(slot)
      return
    }
    this.request(robotId, slot, lightEffectState(effect, true))
    if (isAnimated(effect)) {
      slot.timer = window.setInterval(() => {
        slot.phaseOn = !slot.phaseOn
        this.request(robotId, slot, lightEffectState(effect, slot.phaseOn))
      }, 600)
    }
  }

  stop(robotId: string, turnOff = true) {
    const slot = this.slot(robotId)
    this.clearTimer(slot)
    slot.effect = "ignore"
    slot.phaseOn = false
    if (turnOff) this.request(robotId, slot, OFF, true)
    else {
      slot.desired = null
      this.resolveWaiters(slot)
    }
  }

  async stopAll(robotIds: string[]) {
    const unique = [...new Set(robotIds)]
    unique.forEach((robotId) => this.stop(robotId, true))
    await Promise.all(unique.map((robotId) => this.whenIdle(robotId)))
  }

  dispose(robotIds: string[] = []) {
    robotIds.forEach((robotId) => this.stop(robotId, true))
    this.slots.forEach((slot) => this.clearTimer(slot))
  }

  private slot(robotId: string): LightSlot {
    let slot = this.slots.get(robotId)
    if (!slot) {
      slot = { effect: "ignore", phaseOn: false, timer: null, desired: null, sent: null, inFlight: false, force: false, waiters: [] }
      this.slots.set(robotId, slot)
    }
    return slot
  }

  private request(robotId: string, slot: LightSlot, state: LightState, force = false) {
    slot.desired = state
    slot.force ||= force
    this.flush(robotId, slot)
  }

  private flush(robotId: string, slot: LightSlot) {
    if (slot.inFlight || !slot.desired) return
    const state = slot.desired
    const shouldSend = slot.force || !sameState(slot.sent, state)
    slot.desired = null
    slot.force = false
    if (!shouldSend) {
      this.resolveWaiters(slot)
      return
    }
    slot.inFlight = true
    void this.send(robotId, state)
      .then(() => { slot.sent = state })
      .catch((error) => this.onError?.(robotId, error))
      .finally(() => {
        slot.inFlight = false
        if (slot.desired) this.flush(robotId, slot)
        else this.resolveWaiters(slot)
      })
  }

  private whenIdle(robotId: string) {
    const slot = this.slot(robotId)
    if (!slot.inFlight && !slot.desired) return Promise.resolve()
    return new Promise<void>((resolve) => slot.waiters.push(resolve))
  }

  private resolveWaiters(slot: LightSlot) {
    if (slot.inFlight || slot.desired) return
    slot.waiters.splice(0).forEach((resolve) => resolve())
  }

  private clearTimer(slot: LightSlot) {
    if (slot.timer !== null) window.clearInterval(slot.timer)
    slot.timer = null
  }
}

export function lightEffectLabel(effect: LightEffect) {
  return LIGHT_EFFECT_OPTIONS.find((item) => item.value === effect)?.label || "不管"
}

export function lightEffectState(effect: LightEffect, phaseOn = true): LightState {
  if (!phaseOn && effect !== "alternate") return OFF
  if (effect === "left_steady" || effect === "left_blink") return { left: true, right: false }
  if (effect === "right_steady" || effect === "right_blink") return { left: false, right: true }
  if (effect === "both_steady" || effect === "both_blink") return { left: true, right: true }
  if (effect === "alternate") return phaseOn ? { left: true, right: false } : { left: false, right: true }
  return OFF
}

function isAnimated(effect: LightEffect) {
  return effect === "left_blink" || effect === "right_blink" || effect === "both_blink" || effect === "alternate"
}

function sameState(left: LightState | null, right: LightState) {
  return left?.left === right.left && left?.right === right.right
}
