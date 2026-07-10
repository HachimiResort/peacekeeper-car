# Peacekeeper Car

Peacekeeper is the robot-side software for the forest patrol smart car project.

This repository starts with the v1 vehicle execution layer:

- `robot/fleet-agent`: one HTTP/WebSocket/Web demo service running on each car.
- `robot/ros2_ws`: the local ROS2 capability layer managed by the agent.

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

## Quick Start

On the car, run inside the `icar/ros-foxy:1.0.2` environment:

```bash
cd /root/peacekeeper-car/robot/fleet-agent
python3 -m pip install -r requirements.txt
python3 agent.py --config ../../configs/fleet-agent.direct.yaml
```

Then open:

```text
http://<car-ip>:8001
```

For Docker host launch guidance, see `robot/fleet-agent/run_in_docker.sh`.

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
