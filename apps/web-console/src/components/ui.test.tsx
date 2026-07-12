import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useEffect } from "react"
import { describe, expect, it, vi } from "vitest"
import { FeedbackProvider, useFeedback } from "../app/feedback"
import { Button, InlineActionStatus, Modal } from "./ui"

describe("interaction primitives", () => {
  it("exposes an immediate loading state and blocks duplicate clicks", async () => {
    const onClick = vi.fn()
    render(<Button loading loadingText="正在保存" onClick={onClick}>保存</Button>)
    const button = screen.getByRole("button", { name: "正在保存" })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute("aria-busy", "true")
    await userEvent.click(button)
    expect(onClick).not.toHaveBeenCalled()
  })

  it("renders retryable inline errors", async () => {
    const onRetry = vi.fn()
    render(<InlineActionStatus tone="error" title="保存失败" detail="车辆离线" onRetry={onRetry} />)
    expect(screen.getByRole("alert")).toHaveTextContent("车辆离线")
    await userEvent.click(screen.getByRole("button", { name: "重试" }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it("prevents a busy modal from being dismissed", async () => {
    const onClose = vi.fn()
    render(<Modal open title="编辑车辆" busy onClose={onClose}>内容</Modal>)
    await userEvent.click(screen.getByRole("button", { name: "关闭" }))
    expect(onClose).not.toHaveBeenCalled()
  })

  it("shows toast feedback and resolves confirmations", async () => {
    function Probe() {
      const feedback = useFeedback()
      return <><button onClick={() => feedback.notify({ title: "操作成功", tone: "success" })}>通知</button><button onClick={async () => { if (await feedback.confirm({ title: "恢复巡护？", description: "车辆将开始移动" })) feedback.notify({ title: "已确认", tone: "success" }) }}>打开确认</button></>
    }
    render(<FeedbackProvider><Probe /></FeedbackProvider>)
    await userEvent.click(screen.getByRole("button", { name: "通知" }))
    expect(screen.getByText("操作成功")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "打开确认" }))
    expect(screen.getByRole("alertdialog")).toHaveTextContent("车辆将开始移动")
    await userEvent.click(screen.getByRole("button", { name: "确认" }))
    expect(await screen.findByText("已确认")).toBeInTheDocument()
  })

  it("keeps the feedback API stable when the toast list changes", async () => {
    const cleanup = vi.fn()
    function StabilityProbe() {
      const feedback = useFeedback()
      useEffect(() => () => cleanup(), [feedback])
      return <button onClick={() => feedback.notify({ title: "导航目标已下发", tone: "success" })}>显示提示</button>
    }

    const view = render(<FeedbackProvider><StabilityProbe /></FeedbackProvider>)
    await userEvent.click(screen.getByRole("button", { name: "显示提示" }))

    expect(screen.getByText("导航目标已下发")).toBeInTheDocument()
    expect(cleanup).not.toHaveBeenCalled()
    view.unmount()
    expect(cleanup).toHaveBeenCalledTimes(1)
  })
})
