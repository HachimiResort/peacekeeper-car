# Peacekeeper Web Console

React operator console for Mission API. The browser never calls fleet-agent,
ROS, or the serial port directly.

## Run With Docker

From the repository root:

```bash
cp .env.example .env
docker compose up -d --build
```

Open `http://127.0.0.1:28081`. Use the same `PEACEKEEPER_SHARED_TOKEN` configured
for Mission API. The Token is stored in `sessionStorage`; the operator name is
stored in `localStorage`.

Demo mode is explicit and never acts as a fallback:

```bash
WEB_CONSOLE_DEMO_MODE=true docker compose up -d --force-recreate web-console
```

The gold `DEMO` ribbon remains visible for the whole demo session.

## Local Development

```bash
npm install
npm run dev
```

Vite proxies `/api`, `/health`, and `/ws` to `http://127.0.0.1:28080`.
Use `npm run dev:demo` when Mission API is not required.

## Contract And Checks

Mission API exports `apps/mission-api/openapi.json`. Regenerate the checked-in
TypeScript contract after API changes:

```bash
npm run generate:api
npm run lint
npm run typecheck
npm run test
npm run build
```

The production image uses Nginx for SPA fallback and same-origin Mission API /
WebSocket proxying. Runtime demo mode is written to `runtime-config.js` when the
container starts, so the same image can be used in real and demo deployments.
