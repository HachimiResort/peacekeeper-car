import { act, render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it } from "vitest"
import { SessionProvider, useSession } from "./session"

function SessionProbe() {
  const session = useSession()
  return <span>{session.connected ? "connected" : "disconnected"}</span>
}

describe("SessionProvider", () => {
  beforeEach(() => {
    window.__PEACEKEEPER_CONFIG__ = { demoMode: false }
    sessionStorage.clear()
    localStorage.clear()
  })

  it("keeps the token in session storage and clears it after a 401 signal", () => {
    sessionStorage.setItem("peacekeeper.shared-token", "temporary-token")
    render(<SessionProvider><SessionProbe /></SessionProvider>)
    expect(screen.getByText("connected")).toBeInTheDocument()

    act(() => window.dispatchEvent(new Event("peacekeeper:unauthorized")))

    expect(screen.getByText("disconnected")).toBeInTheDocument()
    expect(sessionStorage.getItem("peacekeeper.shared-token")).toBeNull()
  })
})
