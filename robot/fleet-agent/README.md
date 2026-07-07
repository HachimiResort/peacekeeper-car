# Fleet Agent v1

`fleet-agent` is the vehicle-side execution service. In v1, it provides:

- First-person web control demo.
- HTTP and WebSocket status APIs.
- TTL-limited `/cmd_vel` publishing for manual driving.
- ROS2 process management for chassis, lidar, and SLAM.
- Click-to-sample camera frames and saved snapshots.
- Map saving through `map_saver_cli`.

The web UI does not auto-stream camera video. It requests a single JPEG only
when `Sample` is clicked, reducing Wi-Fi load during ROS control and mapping.
The video service uses `cv2` when it is already available in the car image. It
falls back to a static JPEG if OpenCV is missing.

## Control Ownership

The direct backend is the target vehicle-side control path. In that mode,
fleet-agent owns `/dev/myserial`, keeps a ROS2 `/cmd_vel` subscriber running, and
passes every chassis command through its local arbiter before calling
`Rosmaster_Lib`.

```text
ros2_ws /cmd_vel -> fleet-agent subscriber -> command arbiter -> Rosmaster_Lib -> /dev/myserial -> car
Web manual cmd  -> fleet-agent HTTP      -> command arbiter -> Rosmaster_Lib -> /dev/myserial -> car
```

The legacy ROS publishing path is still available for inspection with older car
images:

```text
Web -> fleet-agent -> /cmd_vel -> external chassis driver -> serial -> car
```

Use `configs/fleet-agent.direct.yaml` for the intended direct-mode bring-up. Do
not start another chassis process that also writes `/dev/myserial`.

Laser tracking is available as a small experiment in both paths:

```text
Direct:  laser_Tracker_a1_X3 -> /cmd_vel -> fleet-agent subscriber -> Rosmaster_Lib
Bringup: laser_Tracker_a1_X3 -> /cmd_vel -> rosmaster_chassis -> Rosmaster_Lib
```

When using the direct config, start the agent from an environment that already
sourced ROS2 and the Yahboom workspace so the in-process `/cmd_vel` subscriber
can import `rclpy` and `geometry_msgs`. The subscriber starts with the agent, but
ROS `/cmd_vel` is executed only while the arbiter is in an allowed ROS-owned
mode such as `LASER_TRACKING` or `NAV_PATROL`.

If direct laser tracking starts but the car does not move, check
`GET /api/status`. In `ros.direct_cmd_vel_subscriber`, `ready` should be true,
`spin_thread_alive` should be true, `enabled` should be true, `message_count`
should increase while the tracker is running, and `ros_import_error` should be
null. In `ros.arbiter`, rejected commands show up as `last_reject_reason`.

## Run

Inside the car container:

```bash
cd /root/peacekeeper-car/robot/fleet-agent
python3 -m pip install -r requirements.txt
python3 agent.py --config ../../configs/fleet-agent.example.yaml
```

For the Peacekeeper ROS launch wrappers, build the workspace once and run the
bringup config:

```bash
cd /root/peacekeeper-car/robot/ros2_ws
source /opt/ros/foxy/setup.bash
colcon build --symlink-install

cd /root/peacekeeper-car/robot/fleet-agent
python3 agent.py --config ../../configs/fleet-agent.bringup.yaml
```

Or from the host:

```bash
cd robot/fleet-agent
CONFIG=../../configs/fleet-agent.bringup.yaml ./run_in_docker.sh
```

Open:

```text
http://<car-ip>:8001
```

## Main API

```text
GET  /
GET  /api/status
GET  /api/video/sample.jpg
GET  /video.mjpg
WS   /ws

POST /api/control/cmd_vel
POST /api/control/manual_cmd
POST /api/control/ros_cmd_vel/start
POST /api/control/ros_cmd_vel/stop
POST /api/control/stop
POST /api/control/estop

POST /api/process/start
POST /api/process/stop
POST /api/process/stop_all

POST /api/mapping/start
POST /api/mapping/save
POST /api/mapping/stop

POST /api/tracking/laser/start
POST /api/tracking/laser/stop

POST /api/video/snapshot
```

Manual command:

```json
{
  "linear_x": 0.2,
  "linear_y": 0.0,
  "angular_z": 0.0,
  "ttl_ms": 500,
  "source": "web"
}
```

## v1 Acceptance Loop

1. Start the agent.
2. Open the web page and click `Sample` only when a camera frame is needed.
3. Click `Start Mapping`.
4. Drive manually with `W/A/S/D/Q/E`.
5. Click `Save Map`.
6. Save a snapshot.
7. Press `Emergency Stop` and verify the car stops.

## Verification Without Hardware

See `VERIFY_WITHOUT_CAR.md`.

```bash
python3 -m unittest discover robot/fleet-agent/tests
python3 -m compileall robot/fleet-agent robot/ros2_ws/src/peacekeeper_bringup
```
