import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { FeedbackProvider } from "../app/feedback"
import { SessionProvider } from "../app/session"
import { DemoMissionApi } from "../mocks/demo-client"
import { RobotDetailPage } from "./robot-detail-page"

describe("RobotDetailPage control safety", () => {
  beforeEach(() => {
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

  it("shows an annotated YOLO image and supports a new single-frame capture", async () => {
    const captureSpy = vi.spyOn(DemoMissionApi.prototype, "visionCapture")
    render(
      <FeedbackProvider>
        <MemoryRouter initialEntries={["/robots/car_1"]}>
          <SessionProvider>
            <Routes><Route path="/robots/:robotId" element={<RobotDetailPage />} /></Routes>
          </SessionProvider>
        </MemoryRouter>
      </FeedbackProvider>,
    )

    expect(await screen.findByRole("heading", { name: "YOLO 单帧识别" })).toBeInTheDocument()
    expect(await screen.findByAltText("YOLO 带框识别结果")).toHaveAttribute("src", "blob:test")
    await userEvent.click(screen.getByRole("button", { name: "识别当前画面" }))
    await waitFor(() => expect(captureSpy).toHaveBeenCalledWith("car_1"))
    expect(await screen.findByText("45.4 ms")).toBeInTheDocument()
  })
})
