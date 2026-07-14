import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { FeedbackProvider } from "../app/feedback"
import { SessionProvider } from "../app/session"
import { DemoMissionApi } from "../mocks/demo-client"
import { RobotDetailPage } from "./robot-detail-page"

describe("RobotDetailPage control safety", () => {
  afterEach(() => cleanup())

  beforeEach(() => {
    vi.restoreAllMocks()
    window.__PEACEKEEPER_CONFIG__ = { demoMode: true }
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:test") })
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() })
  })

  it("does not emit control/stop when an action toast rerenders the page", async () => {
    const actionSpy = vi.spyOn(DemoMissionApi.prototype, "robotAction")
    const view = render(
      <FeedbackProvider>
        <MemoryRouter initialEntries={["/robots/car_1"]}>
          <SessionProvider>
            <Routes><Route path="/robots/:robotId" element={<RobotDetailPage />} /></Routes>
          </SessionProvider>
        </MemoryRouter>
      </FeedbackProvider>,
    )

    await userEvent.click(await screen.findByRole("button", { name: "急停" }))
    expect((await screen.findAllByText("急停指令已下发")).length).toBeGreaterThan(0)
    await waitFor(() => expect(actionSpy).toHaveBeenCalledWith("car_1", "control/estop", {}))

    expect(actionSpy.mock.calls.map((call) => call[1])).not.toContain("control/stop")
    view.unmount()
    expect(actionSpy.mock.calls.map((call) => call[1])).not.toContain("control/stop")
  })

  it("shows a live YOLO stream and latest detection metrics", async () => {
    render(
      <FeedbackProvider>
        <MemoryRouter initialEntries={["/robots/car_1"]}>
          <SessionProvider>
            <Routes><Route path="/robots/:robotId" element={<RobotDetailPage />} /></Routes>
          </SessionProvider>
        </MemoryRouter>
      </FeedbackProvider>,
    )

    expect(await screen.findByRole("heading", { name: "YOLO 实时识别" })).toBeInTheDocument()
    const toggleButton = screen.getByRole("button", { name: "开启实时识别" })
    await waitFor(() => expect(toggleButton).toBeEnabled())
    await userEvent.click(toggleButton)
    expect(await screen.findByAltText("YOLO 实时识别结果")).toHaveAttribute("src", expect.any(String))
    expect(screen.getByRole("button", { name: "关闭实时识别" })).toBeInTheDocument()
    expect(await screen.findByText("45.4 ms")).toBeInTheDocument()
  })

  it("routes light, buzzer, and audio controls through Mission API", async () => {
    const actionSpy = vi.spyOn(DemoMissionApi.prototype, "robotAction")
    render(
      <FeedbackProvider>
        <MemoryRouter initialEntries={["/robots/car_1"]}>
          <SessionProvider>
            <Routes><Route path="/robots/:robotId" element={<RobotDetailPage />} /></Routes>
          </SessionProvider>
        </MemoryRouter>
      </FeedbackProvider>,
    )

    await userEvent.click(await screen.findByRole("button", { name: "仅左灯开" }))
    await waitFor(() => expect(actionSpy).toHaveBeenCalledWith("car_1", "control/lights", { left: true, right: false, duration_ms: 0 }))

    await userEvent.click(screen.getByRole("button", { name: "仅右灯开" }))
    await waitFor(() => expect(actionSpy).toHaveBeenCalledWith("car_1", "control/lights", { left: false, right: true, duration_ms: 0 }))

    await userEvent.clear(screen.getByLabelText("蜂鸣时长 ms"))
    await userEvent.type(screen.getByLabelText("蜂鸣时长 ms"), "450")
    await userEvent.click(screen.getByRole("button", { name: "测试蜂鸣" }))
    await waitFor(() => expect(actionSpy).toHaveBeenCalledWith("car_1", "control/buzzer", { enabled: true, duration_ms: 450 }))

    await userEvent.selectOptions(screen.getByLabelText("音频资产"), "alert-tone.wav")
    await userEvent.click(screen.getByRole("button", { name: "播放" }))
    await waitFor(() => expect(actionSpy).toHaveBeenCalledWith("car_1", "audio/play", { asset: "alert-tone.wav", volume: 80, loop: false }))
  })

  it("uploads an audio asset through Mission API before it is played", async () => {
    const uploadSpy = vi.spyOn(DemoMissionApi.prototype, "uploadAudio")
    render(
      <FeedbackProvider>
        <MemoryRouter initialEntries={["/robots/car_1"]}>
          <SessionProvider>
            <Routes><Route path="/robots/:robotId" element={<RobotDetailPage />} /></Routes>
          </SessionProvider>
        </MemoryRouter>
      </FeedbackProvider>,
    )

    const file = new File(["audio-data"], "arrival.mp3", { type: "audio/mpeg" })
    await userEvent.upload(await screen.findByLabelText("上传音频文件"), file)
    await userEvent.click(screen.getByRole("button", { name: "上传" }))
    await waitFor(() => expect(uploadSpy).toHaveBeenCalledWith("car_1", file))
  })

  it("shows the configured hazard alarm audio state", async () => {
    vi.spyOn(DemoMissionApi.prototype, "hazardStatus").mockResolvedValue({
      enabled: true,
      state: "HOLDING",
      current_event_key: "hazard-1",
      current_detection: null,
      confirmation_hits: 3,
      confirmation_window: 5,
      hold_started_at: Date.now() / 1000,
      auto_resume_remaining_s: 8,
      takeover: false,
      hold_requested: false,
      last_error: null,
      alarm_audio: { configured: true, ready: true, playing: true, asset: "cat-alert.mp3", loop: true, volume: 100, last_error: null },
      outbox: { pending: 0, mission_api_configured: true, last_error: null },
    })
    render(
      <FeedbackProvider>
        <MemoryRouter initialEntries={["/robots/car_1"]}>
          <SessionProvider>
            <Routes><Route path="/robots/:robotId" element={<RobotDetailPage />} /></Routes>
          </SessionProvider>
        </MemoryRouter>
      </FeedbackProvider>,
    )

    expect(await screen.findByText("报警音乐正在播放")).toBeInTheDocument()
    expect(screen.getByText("cat-alert.mp3 · 循环播放 · 音量 100%")).toBeInTheDocument()
  })
})
