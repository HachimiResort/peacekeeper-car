# Peacekeeper Car

Peacekeeper is the robot-side software for the forest patrol smart car project.

This repository now contains the vehicle execution layer and the first central
control-plane service:

- `robot/fleet-agent`: one HTTP/WebSocket/Web demo service running on each car.
- `robot/ros2_ws`: the local ROS2 capability layer managed by the agent.
- `apps/mission-api`: the authenticated business API, PostgreSQL registry,
  mission/event store, status aggregator, and map distribution service.
- `apps/web-console`: the React operator console that talks only to Mission API.

The v1 loop is intentionally narrow and demonstrable:

```text
Start fleet-agent
-> open the ROS control web demo
-> start lidar and SLAM
-> drive manually through /cmd_vel
-> build and save a map
-> click to sample camera frames or save snapshots
-> stop or emergency-stop safely
```

The vehicle demo now also supports the next direct-mode loop:

```text
load saved map -> set AMCL initial pose -> send Nav2 goal
-> build or load a multi-point patrol route -> pause/resume/cancel safely
```

The intended direct-mode control ownership rule is strict: fleet-agent is the
only process that writes `/dev/myserial`. It subscribes to ROS2 `/cmd_vel`,
arbitrates that stream against manual control and emergency stop, then calls
`Rosmaster_Lib`.

`mission-api` is the formal entry point for operator clients. The fleet-agent
homepage remains an engineering/debug console and is protected by the same
shared Token in direct deployments.

## Quick Start

On the car, run inside the `icar/ros-foxy:1.0.2` environment:

```bash
cd /root/peacekeeper-car/robot/fleet-agent
python3 -m pip install -r requirements.txt
export PEACEKEEPER_SHARED_TOKEN='<same-token-as-mission-api>'
python3 agent.py --config ../../configs/fleet-agent.direct.yaml
```

Then open:

```text
http://<car-ip>:8001
```

For Docker host launch guidance, see `robot/fleet-agent/run_in_docker.sh`.

On the control computer:

```bash
cp .env.example .env
# Edit .env and configs/fleet/cars.yaml.
docker compose up -d --build
curl http://127.0.0.1:28080/health/ready
```

Open the operator console at `http://127.0.0.1:28081`. Enter the shared Token
from `.env`; it is retained only in the current browser tab. To run with
explicit sample data, set `WEB_CONSOLE_DEMO_MODE=true` before recreating the
web-console container.

See `apps/mission-api/README.md` for registry, authentication, and map
distribution flows, `apps/web-console/README.md` for frontend development, and
`docs/cicd.md` for the Docker Compose CI/CD pipeline and production deploy
setup.

## Main Interfaces

- `GET /` - ROS control and mapping demo.
- `GET /api/status` - process, ROS, video, and mode status.
- `GET /api/video/sample.jpg` - single camera frame for click-to-sample UI.
- `POST /api/control/cmd_vel` - publish a TTL-limited velocity command.
- `POST /api/control/stop` - publish zero velocity.
- `POST /api/control/estop` - emergency stop.
- `POST /api/mapping/start` - start lidar and SLAM.
- `POST /api/mapping/save` - save the current map.
- `POST /api/video/snapshot` - save a current first-person frame.
