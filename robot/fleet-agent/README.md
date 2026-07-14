# Fleet Agent v1

`fleet-agent` is the vehicle-side execution service. In v1, it provides:

- First-person web control demo.
- HTTP and WebSocket status APIs.
- Direct Rosmaster command execution for manual driving and ROS-owned `/cmd_vel`.
- Direct ROS odometry/TF publishing for SLAM and map saving.
- ROS2 process management for lidar and SLAM.
- Click-to-sample camera frames and saved snapshots.
- Map saving through `map_saver_cli`.
- Direct Nav2 startup, AMCL initial pose, and `NavigateToPose` goals.
- Sequential multi-point patrol routes with pause, resume, cancel, dwell, and loop control.
- Authenticated map bundle export and atomic, versioned map installation.

The web UI does not auto-stream camera video. It requests a single JPEG only
when `Sample` is clicked, reducing Wi-Fi load during ROS control and mapping.
The video service uses `cv2` when it is already available in the car image. It
falls back to a static JPEG if OpenCV is missing.

## Control Ownership

fleet-agent always runs in direct mode. It owns `/dev/myserial`, keeps a ROS2
`/cmd_vel` subscriber running, and
passes every chassis command through its local arbiter before calling
`Rosmaster_Lib`.

```text
ros2_ws /cmd_vel -> fleet-agent subscriber -> command arbiter -> Rosmaster_Lib -> /dev/myserial -> car
Web manual cmd  -> fleet-agent HTTP      -> command arbiter -> Rosmaster_Lib -> /dev/myserial -> car
Rosmaster motion feedback -> direct odom bridge -> /odom + /tf -> SLAM / map_saver_cli
```

Use `configs/fleet-agent.direct.yaml` or a direct-mode equivalent. Do not start
another process that also writes `/dev/myserial`.

Laser tracking is available as a small experiment in the same direct chain:

```text
laser_Tracker_a1_X3 -> /cmd_vel -> fleet-agent subscriber -> Rosmaster_Lib
```

When using the direct config, start the agent from an environment that already
sourced ROS2 and the Yahboom workspace so the in-process `/cmd_vel` subscriber
can import `rclpy` and `geometry_msgs`. The subscriber starts with the agent, but
ROS `/cmd_vel` is executed only while the arbiter is in an allowed ROS-owned
mode such as `LASER_TRACKING` or `NAV_PATROL`.

For mapping, `Start Mapping` starts lidar plus SLAM. The app itself keeps a
direct-mode odom/TF bridge alive, and the bridge now prefers
`Rosmaster_Lib.get_motion_data()` over command integration, so the installed
`yahboomcar_nav` gmapping launch sees `/odom` and `odom -> base_link` TF from
real chassis feedback whenever the board exposes it.
`Save Map` then runs `map_saver_cli` against the live `/map` topic and writes
`<data_dir>/maps/<name>.yaml` plus `<data_dir>/maps/<name>.pgm`. The default
save command sets `save_map_timeout:=120000`, and the agent retries once when
`map_saver_cli` reports a timeout because larger Yahboom maps can save slowly
under load.

For navigation, stop SLAM first, load a saved map with `Start Nav2`, click the
robot's real position and publish its initial pose, then send a short goal.
Nav2 remains an internal capability layer: its `/cmd_vel` stream is accepted
only while the fleet-agent arbiter owns `NAV_PATROL` mode.

Patrol can run a saved route from `configs/patrol_routes.yaml` or a draft route
built by clicking points on the web map. Route files use `yaw_deg`; HTTP patrol
points use `yaw` in radians. Pausing cancels the current Nav2 goal, and resume
re-sends the same route point. Manual control, stop, emergency stop, and Nav2
shutdown all cancel the patrol locally before changing control ownership.

If direct laser tracking starts but the car does not move, check
`GET /api/status`. In `ros.direct_cmd_vel_subscriber`, `ready` should be true,
`spin_thread_alive` should be true, `enabled` should be true, `message_count`
should increase while the tracker is running, and `ros_import_error` should be
null. In `ros.direct_odom_bridge`, `ready` should be true and `pose` should
change while the car moves. In `ros.arbiter`, rejected commands show up as
`last_reject_reason`.

## Run

Inside the car container:

```bash
cd /root/peacekeeper-car/robot/fleet-agent
python3 -m pip install -r requirements.txt
export PEACEKEEPER_SHARED_TOKEN='<shared-token>'
python3 agent.py --config ../../configs/fleet-agent.direct.yaml
```

Or from the host:

```bash
cd robot/fleet-agent
CONFIG=../../configs/fleet-agent.direct.yaml ./run_in_docker.sh
```

Open:

```text
http://<car-ip>:8001
```

Direct and example configs require the Token. The debug page stores it only in
the current tab's `sessionStorage`; API clients send it as
`X-Peacekeeper-Token`, and WebSocket clients use `?token=`. Local test config
may disable the requirement.

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

GET  /api/mapping/meta
GET  /api/maps/export?name=<map-name>
POST /api/maps/install
GET  /api/navigation/status
POST /api/navigation/start
POST /api/navigation/initial_pose
POST /api/navigation/goal
POST /api/navigation/cancel
POST /api/navigation/stop

GET  /api/patrol/routes
POST /api/patrol/routes/reload
GET  /api/patrol/status
POST /api/patrol/start
POST /api/patrol/pause
POST /api/patrol/resume
POST /api/patrol/cancel

GET  /api/hazards/status
POST /api/hazards/monitor
POST /api/hazards/{event_key}/hold
POST /api/hazards/{event_key}/takeover
POST /api/hazards/{event_key}/resume

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

Inline patrol request:

```json
{
  "map_name": "lab_first_map",
  "loop": false,
  "points": [
    {"name": "point_1", "x": 1.2, "y": 0.8, "yaw": 0.0, "dwell_s": 2.0},
    {"name": "point_2", "x": 2.6, "y": 1.1, "yaw": 1.57, "dwell_s": 1.0}
  ]
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

## Hazard patrol loop

Configure `hazards.robot_id`, `hazards.mission_api_url`, and the shared token
before deployment. The Mission API URL must be reachable from the car (for
example `http://<server>:28080`). Publish a calibrated `base_link ->
camera_link` transform for target map coordinates. Missing camera TF never
blocks a confirmed stop, but the target map position is reported as unknown.

Monitoring starts only after `POST /api/hazards/monitor {"enabled": true}` and
continues without an open browser. Three confirmed measurements in five
observations inside 1.5 m pause patrol and enter `HAZARD_HOLD`. Evidence
is written under `data/evidence`, queued under `data/outbox`, and retried until
the center accepts it. Do not use the generic patrol resume endpoint while a
hazard hold is active.

To play a custom alarm after the stop command, upload a supported `.mp3`,
`.wav`, `.ogg`, or `.m4a` file from Web Console, or copy it into
`/root/peacekeeper-car/data/audio`. Configure only its file name:

```yaml
hazards:
  alarm_audio_asset: cat-alert.mp3
  alarm_audio_volume: 100
  alarm_audio_loop: true
```

The alarm loops while the vehicle remains in `HOLDING` and stops before patrol
resume or operator takeover. Playback failure is reported in
`GET /api/hazards/status` under `alarm_audio`, but never prevents the stop,
evidence capture, or event delivery. Before a vehicle test, verify
`command -v ffplay` and `/dev/snd` inside `pk-agent`, then use the existing Web
Console audio controls to confirm that the selected speaker produces sound.

## Verification Without Hardware

See `VERIFY_WITHOUT_CAR.md`.

```bash
python3 -m unittest discover robot/fleet-agent/tests
python3 -m compileall robot/fleet-agent robot/ros2_ws/src/peacekeeper_bringup
```
