import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"

export const server = setupServer(
  http.get("*/health/ready", () => HttpResponse.json({ ok: true })),
  http.get("*/api/robots", ({ request }) => {
    if (request.headers.get("X-Peacekeeper-Token") !== "valid-token") {
      return HttpResponse.json({ error: { code: "unauthorized", message: "invalid", request_id: "test" } }, { status: 401 })
    }
    return HttpResponse.json({ robots: [] })
  }),
)
