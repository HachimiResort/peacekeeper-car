# Peacekeeper Mission API

Mission API is the control-plane entry point for Web clients, operator tools,
and the future fleet-commander. It never imports ROS or writes a vehicle
serial port. Vehicle operations are proxied to each authenticated
`fleet-agent`.

## Run

From the repository root:

```bash
cp .env.example .env
# Set a strong POSTGRES_PASSWORD and PEACEKEEPER_SHARED_TOKEN in .env.
docker compose up -d --build
docker compose ps
```

Host ports are intentionally high:

```text
mission-api  http://127.0.0.1:28080
PostgreSQL   127.0.0.1:55433
```

Health endpoints do not require authentication. Every `/api/*` and
`/internal/*` request uses the shared token:

```bash
curl http://127.0.0.1:28080/health/ready
curl -H "X-Peacekeeper-Token: $PEACEKEEPER_SHARED_TOKEN" \
  http://127.0.0.1:28080/api/robots
```

WebSocket clients connect to:

```text
ws://127.0.0.1:28080/ws/status?token=<shared-token>
```

## Doubao Voice Gateway

Set all provider values in the process environment (the root `.env.example`
lists them). If any credential is absent, Mission API still starts normally
and rejects only voice connections with a clear `voice_not_configured` event.
The default Ark model is `doubao-seed-2-0-lite-260215`, with deep thinking and
response storage disabled.

Each car opens one authenticated connection:

```text
ws://<mission-api>/ws/voice/<robot-id>?token=<shared-token>
```

The car sends `hello`, `turn.start`, 16 kHz mono signed-16-bit PCM binary
frames, and `turn.end`. Mission API returns state, final transcript, tool audit,
reply text, and 24 kHz PCM binary frames between `reply.audio.start` and
`reply.audio.end`. A car can have only one active voice socket and one active
turn. Disconnecting cancels the turn and triggers a best-effort stop.

The model can only call status, lights, 50–1000 ms beep, stop, camera observe,
100–1000 ms movement, and an explicit 15–180 degree in-place turn. All arguments are validated again by
Pydantic; movement is fixed at 0.22 m/s or 0.9 rad/s, carries a vehicle TTL,
and sends a second stop in `finally`. Navigation and patrol are not exposed.

Conversation sessions store transcript, reply, validated tool audit, latency,
and errors in PostgreSQL. Raw microphone and synthesized audio are never
persisted. Run `alembic upgrade head` after deploying this version.

## Vehicle Registry

PostgreSQL is authoritative. `configs/fleet/cars.yaml` is only a startup seed:
IDs absent from the database are inserted; existing rows are never updated or
deleted from YAML.

```yaml
robots:
  - id: car_1
    name: Peacekeeper Car 1
    base_url: http://10.60.162.192:8001
    role: leader
    enabled: true
    capabilities:
      mapping: true
      navigation: true
      patrol: true
```

The same `PEACEKEEPER_SHARED_TOKEN` must be configured on mission-api and all
fleet-agents. The center polls enabled vehicles once per second; three
consecutive failures mark a vehicle offline.

## Map Lifecycle

1. Save a map on a vehicle.
2. Call `POST /api/maps/import-from-robot` with `robot_id` and `map_name`.
3. Mission API validates the ZIP, PGM, YAML, and hashes, then stores immutable
   metadata in PostgreSQL and files in `mission_map_data`.
4. Call `POST /api/maps/{map_id}/dispatch` with target `robot_ids`.
5. Each target installs `logical_name__vN.yaml/.pgm` atomically.
6. Start navigation explicitly with the installed name. Dispatch never starts
   Nav2 or publishes an initial pose.

Map bundles contain exactly `manifest.json`, `map.yaml`, and `map.pgm` and are
limited to 64 MiB compressed and decompressed. SLAM, Nav2, patrol, and map
saving block installation on the vehicle.

## Persistence And Tests

`mission_pg_data` stores PostgreSQL data, `mission_map_data` stores map files,
and `mission_evidence_data` stores hazard screenshots and metadata. Do not use
`docker compose down -v` unless all three should be deleted.

```bash
python3 -m compileall apps/mission-api/mission_api apps/mission-api/migrations
docker compose build mission-api
docker compose up -d
```

The Alembic migrations create robots, missions, idempotent events, alerts,
event evidence, maps, and map deployment records. The service reconciles leftover pending/running
missions to `failed/service_restart` on startup.

This first deployment intentionally has no user accounts, TLS termination,
Redis, Celery, or object storage. Put it on a trusted network; add a reverse
proxy and user authorization before exposing it outside that network.
