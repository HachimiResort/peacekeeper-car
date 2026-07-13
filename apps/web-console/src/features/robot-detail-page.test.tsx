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
})
