# ROS2 Workspace

`robot/ros2_ws` is the local ROS2 capability layer for each car. In v1 it owns
the local lidar and SLAM processes plus a few helper tools around `/cmd_vel`.
`robot/fleet-agent` remains the only external HTTP/Web entrypoint and the only
writer to `/dev/myserial`.

The v1 assumptions are:

- The lidar driver publishes `/scan`.
- The SLAM node consumes `/scan` and publishes `/map`.
- ROS-native tools may publish `/cmd_vel`, but execution still flows through
  `fleet-agent`'s direct-mode subscriber and arbiter.
- The fleet-agent starts and stops these processes and saves maps.

## Packages

```text
src/peacekeeper_bringup/
  launch/lidar.launch.py
  launch/slam.launch.py
  peacekeeper_bringup/teleop_cmd_vel.py
  peacekeeper_bringup/topic_watchdog.py
```

The v1 launch files:

- `lidar.launch.py` includes `sllidar_ros2/sllidar_launch.py`.
- `slam.launch.py` starts `yahboomcar_nav/slam_gmapping_X3`.
- `teleop_cmd_vel` publishes interactive keyboard `Twist` commands for testing
  the ROS control path without the web UI.
- `topic_watchdog.py` reports whether `/cmd_vel`, `/scan`, and `/map` are active.

## Build

On the car:

```bash
cd /root/peacekeeper-car/robot/ros2_ws
colcon build --symlink-install
source install/setup.bash
```

Then start `fleet-agent` with `configs/fleet-agent.direct.yaml` so `/cmd_vel`
remains mediated by the vehicle-side arbiter.

## Control Test Without Web

Terminal A:

```bash
cd /root/peacekeeper-car/robot/fleet-agent
python3 agent.py --config ../../configs/fleet-agent.direct.yaml
```

Terminal B:

```bash
source /opt/ros/foxy/setup.bash
source /root/peacekeeper-car/robot/ros2_ws/install/setup.bash
ros2 run peacekeeper_bringup teleop_cmd_vel --ros-args -p rate_hz:=5.0
```

If this is smooth but the web UI is not, the issue is in the HTTP/Web control
layer. If this also stutters, the issue is in the direct `/cmd_vel` subscriber,
the arbiter, or the Rosmaster command path.
